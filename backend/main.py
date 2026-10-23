import os
import re
from urllib.parse import urlparse, urljoin, urlunparse
import asyncio
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Reedtt Reader API")
app.add_middleware(CORSMiddleware, allow_origins=["https://ginginji1102.github.io"], allow_methods=["GET","POST"], allow_headers=["*"])

ALLOWED = ("truyenfull.live", "wikicv.org", "truyenmo.xyz", "docln.net", "wattpad.com", "wordpress.com")
SELECTORS = ("#chapter-content", ".chapter-content", ".reading-content", ".entry-content", ".chapter-c", "article", "[itemprop=articleBody]")
class ReadRequest(BaseModel):
    url: str

class BatchRequest(BaseModel):
    url: str
    start: int = 1
    end: int = 20
    include_images: bool = False

def allowed(host):
    return any(host == d or host.endswith("." + d) for d in ALLOWED)

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/read")
async def read(req: ReadRequest):
    url = req.url.strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or not allowed(parsed.hostname.lower()) or parsed.port not in (None,443):
        raise HTTPException(400, "Nguồn chưa được hỗ trợ hoặc URL không hợp lệ.")
    try:
        async with httpx.AsyncClient(timeout=18, follow_redirects=False, headers={"User-Agent":"ReedttReader/1.0 (+public content reader)"}) as client:
            response = await client.get(url)
            if response.status_code in (301,302,303,307,308):
                raise HTTPException(400, "Nguồn chuyển hướng; vui lòng nhập URL đích thuộc danh sách hỗ trợ.")
            response.raise_for_status()
            if "html" not in response.headers.get("content-type","").lower():
                raise HTTPException(400, "URL không trả về trang HTML.")
            if len(response.content)>3_000_000:
                raise HTTPException(413, "Trang quá lớn.")
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Không thể tải trang từ nguồn.") from exc
    soup = BeautifulSoup(response.text,"html.parser")
    title_tag=soup.select_one('meta[property="og:title"]')
    title=(title_tag.get("content") if title_tag else None) or (soup.h1.get_text(" ",strip=True) if soup.h1 else soup.title.get_text(" ",strip=True) if soup.title else "")
    author=soup.select_one('[itemprop="author"],.author')
    chapter=next((soup.select_one(s) for s in SELECTORS if soup.select_one(s)),None)
    if chapter:
        for tag in chapter.select("script,style,nav,aside,footer"): tag.decompose()
    content=chapter.get_text("\n",strip=True) if chapter else ""
    links=[]
    for a in soup.select("a[href]"):
        label=a.get_text(" ",strip=True)
        if re.search(r"(chương|chapter)\s*\d+",label,re.I):
            links.append({"title":label[:120],"url":a.get("href","")})
            if len(links)>=100:break
    return {"title":title[:200],"author":author.get_text(" ",strip=True)[:120] if author else "","content":content[:500000],"chapters":links,"count":len(links)}

# Discover public chapter links from the story landing page and pagination.
def normalized_url(base, href):
    try:
        u = urlparse(urljoin(base, href))
        if u.scheme != "https" or u.port not in (None,443) or not u.hostname or not allowed(u.hostname.lower()):
            return None
        return urlunparse((u.scheme,u.netloc,u.path,"",u.query,""))
    except ValueError:
        return None

async def get_page(client, url):
    r=await client.get(url)
    if r.status_code in (301,302,303,307,308):
        target=normalized_url(url,r.headers.get("location",""))
        if not target: raise HTTPException(400,"Chuyển hướng tới nguồn không được hỗ trợ.")
        r=await client.get(target)
    r.raise_for_status()
    if "html" not in r.headers.get("content-type","").lower() or len(r.content)>3_000_000:
        raise HTTPException(400,"Trang không phải HTML hợp lệ hoặc quá lớn.")
    return BeautifulSoup(r.text,"html.parser"),str(r.url)

def chapter_links(soup,base,story_path):
    found={}
    selectors=".list-chapter a[href],#list-chapter a[href],.chapter-list a[href],.list-chapters a[href],ul.list-chapter a[href],a[href*='chuong-'],a[href*='chapter-']"
    for a in soup.select(selectors):
        href=normalized_url(base,a.get("href",""))
        if not href or urlparse(href).path.rstrip("/") == story_path.rstrip("/"): continue
        label=a.get_text(" ",strip=True) or a.get("title","")
        if not re.search(r"(chương|chuong|chapter)\s*[-:]?\s*\d+|/(chuong|chapter)[-_]?\d+",label+" "+href,re.I):continue
        found[href]={"title":label[:180] or href.rsplit("/",1)[-1],"url":href}
    return list(found.values())

def page_links(soup,base,story_path):
    result=[]
    for a in soup.select(".pagination a[href],.pagination-container a[href],a[href*='page='],a[href*='/trang-']"):
        href=normalized_url(base,a.get("href",""))
        if href and urlparse(href).path.rstrip("/").startswith(story_path.rstrip("/")) and href not in result:result.append(href)
    return result[:50]

def chapter_body(soup):
    chapter=next((soup.select_one(sel) for sel in SELECTORS if soup.select_one(sel)),None)
    if not chapter:return "",[]
    for tag in chapter.select("script,style,nav,aside,footer,.ads,.advertisement"):tag.decompose()
    images=[]
    for img in chapter.select("img"):
        src=img.get("data-src") or img.get("data-original") or img.get("src")
        if src:images.append(src)
    return chapter.get_text("\n",strip=True)[:300000],images[:50]

@app.post("/discover")
async def discover(req:ReadRequest):
    u=urlparse(req.url.strip())
    if u.scheme!="https" or not u.hostname or not allowed(u.hostname.lower()) or u.port not in (None,443):
        raise HTTPException(400,"Nguồn chưa được hỗ trợ hoặc URL không hợp lệ.")
    try:
        async with httpx.AsyncClient(timeout=20,follow_redirects=False,headers={"User-Agent":"Mozilla/5.0 (compatible; ReedttReader/1.1)"}) as client:
            soup,base=await get_page(client,req.url)
            title=(soup.select_one("meta[property='og:title']") or {}).get("content","") or (soup.h1.get_text(" ",strip=True) if soup.h1 else "")
            author=soup.select_one("[itemprop='author'],.author")
            story_path=urlparse(base).path
            found={x["url"]:x for x in chapter_links(soup,base,story_path)}
            pages=page_links(soup,base,story_path)
            # Bounded pagination discovery to avoid unlimited crawling.
            for page in pages[:12]:
                try:
                    sub,_=await get_page(client,page)
                    for x in chapter_links(sub,page,story_path):found[x["url"]]=x
                except (httpx.HTTPError,HTTPException):pass
                await asyncio.sleep(.15)
            chapters=list(found.values())
            def order(x):
                m=re.search(r"(?:chương|chuong|chapter)[^0-9]{0,12}(\d+)",x["title"]+" "+x["url"],re.I)
                return int(m.group(1)) if m else 10**9
            chapters.sort(key=order)
            return {"title":title[:200],"author":author.get_text(" ",strip=True)[:120] if author else "","chapters":chapters[:1000],"count":len(chapters),"truncated":len(pages)>12 or len(chapters)>1000}
    except httpx.HTTPError as exc:
        raise HTTPException(502,"Nguồn không phản hồi hoặc từ chối truy cập công khai.") from exc

@app.post("/batch")
async def batch(req:BatchRequest):
    if req.start<1 or req.end<req.start or req.end-req.start>=20:
        raise HTTPException(400,"Mỗi yêu cầu kỹ thuật hỗ trợ tối đa 20 chương; website tự chia đợt.")
    discovery=await discover(ReadRequest(url=req.url))
    chapters=discovery["chapters"]
    if not chapters:raise HTTPException(404,"Chưa tìm thấy danh sách chương công khai từ trang truyện.")
    chosen=chapters[req.start-1:req.end]
    result=[]
    async with httpx.AsyncClient(timeout=20,follow_redirects=False,headers={"User-Agent":"Mozilla/5.0 (compatible; ReedttReader/1.1)"}) as client:
        for item in chosen:
            try:
                soup,base=await get_page(client,item["url"])
                content,images=chapter_body(soup)
                resolved=[normalized_url(base,x) for x in images] if req.include_images else []
                result.append({"title":item["title"],"url":item["url"],"content":content,"images":[x for x in resolved if x]})
            except (httpx.HTTPError,HTTPException):
                result.append({"title":item["title"],"url":item["url"],"content":"","images":[],"error":"Không tải được chương"})
            await asyncio.sleep(.25)
    return {"title":discovery["title"],"author":discovery["author"],"chapters":result,"count":len(result)}
