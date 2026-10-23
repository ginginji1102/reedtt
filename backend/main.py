import os
import re
from urllib.parse import urlparse, urljoin, urlunparse
import asyncio
import ipaddress
import socket
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Reedtt Reader API")
app.add_middleware(CORSMiddleware, allow_origins=["https://yuu110299.github.io", "https://ginginji1102.github.io"], allow_methods=["GET","POST"], allow_headers=["*"])

ALLOWED = ("truyenfull.live", "wikicv.org", "truyenmo.xyz", "docln.net", "wattpad.com", "wordpress.com", "navyteams.com", "inkitt.com")
SELECTORS = ("#chapter-content", ".chapter-content", ".reading-content", ".entry-content", ".chapter-c", "article", "[itemprop=articleBody]")
class ReadRequest(BaseModel):
    url: str

class BatchRequest(BaseModel):
    url: str
    start: int = 1
    end: int = 20
    include_images: bool = False
    chapters: list[dict] | None = None

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

def image_url(base,href):
    try:
        u=urlparse(urljoin(base,href))
        if u.scheme!="https" or not u.hostname or u.port not in (None,443) or u.username or u.password:return None
        return urlunparse((u.scheme,u.netloc,u.path,"",u.query,""))
    except ValueError:return None

async def public_image_host(host):
    try:
        infos=await asyncio.get_running_loop().getaddrinfo(host,443,type=socket.SOCK_STREAM)
        return bool(infos) and all(ipaddress.ip_address(info[4][0]).is_global for info in infos)
    except (OSError,ValueError):return False

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
    host=(urlparse(base).hostname or "").lower()
    selectors=".list-chapter a[href],#list-chapter a[href],.chapter-list a[href],.list-chapters a[href],ul.list-chapter a[href],a[href*='chuong-'],a[href*='chapter-']"
    if host.endswith("wattpad.com"):
        selectors+=",a[href^='/'][href*='-'],a[href*='wattpad.com/']"
    elif host.endswith("inkitt.com"):
        selectors+=", .chapters-list a[href],a[href*='/chapters/'],a[href*='/chapter/']"
    if host.endswith("wordpress.com"):
        # WordPress story indexes typically list chapters as ordinary links inside post content.
        # Never scan sidebars, navigation, related posts, or the comment section.
        article=soup.select_one(".entry-content,.post-content,.entry,.post-entry,[itemprop='articleBody']")
        if article:
            for a in article.select("a[href]"):
                if a.find_parent(["nav","aside","footer"]) or a.find_parent(class_=re.compile(r"comment|related|share|social|widget",re.I)):continue
                label=a.get_text(" ",strip=True)
                if not re.search(r"^(?:chương|chuong|chapter|chap)\\s*0*\\d+(?:\\b|\\s|$)",label,re.I):
                    continue
                href=normalized_url(base,a.get("href",""))
                if not href or urlparse(href).path.rstrip("/")==story_path.rstrip("/"):continue
                found[href]={"title":label[:180],"url":href}
            # Extra chapters are grouped after a heading like 'Phiên Ngoại'.
            for heading in article.select("h1,h2,h3,h4,h5,h6,p,strong,b"):
                if not re.fullmatch(r"(?:phiên ngoại|phien ngoai|ngoại truyện|ngoai truyen)\\s*:?",heading.get_text(" ",strip=True),re.I):continue
                parent=heading.parent
                if not parent:continue
                for sibling in parent.next_siblings:
                    if getattr(sibling,"name",None) in ("h1","h2","h3","h4","h5","h6"):break
                    if not hasattr(sibling,"select"):continue
                    for a in sibling.select("a[href]"):
                        label=a.get_text(" ",strip=True)
                        href=normalized_url(base,a.get("href",""))
                        if label and href and urlparse(href).path.rstrip("/")!=story_path.rstrip("/"):
                            found[href]={"title":"Phiên ngoại: "+label[:155],"url":href}
        return list(found.values())
    for a in soup.select(selectors):
        href=normalized_url(base,a.get("href",""))
        if not href or urlparse(href).path.rstrip("/") == story_path.rstrip("/"): continue
        label=a.get_text(" ",strip=True) or a.get("title","")
        target=urlparse(href)
        if host.endswith("wattpad.com"):
            if not re.fullmatch(r"/\d{6,}(?:-[^/]*)?/?",target.path):continue
        elif host.endswith("inkitt.com"):
            if not (a.find_parent(class_="chapters-list") or re.search(r"/chapters?/[^/]+",target.path,re.I)):continue
            nr=a.select_one(".chapter-nr")
            chapter_title=a.select_one(".chapter-title")
            if nr or chapter_title:
                label=" ".join(x.get_text(" ",strip=True) for x in (nr,chapter_title) if x)
        elif not re.search(r"(chương|chuong|chapter)\s*[-:]?\s*\d+|/(chuong|chapter)[-_]?\d+",label+" "+href,re.I):continue
        found[href]={"title":label[:180] or href.rsplit("/",1)[-1],"url":href}
    return list(found.values())

async def wattpad_story_metadata(client,url):
    match=re.search(r'/story/(\d+)',urlparse(url).path)
    if not match:return None
    try:
        r=await client.get('https://www.wattpad.com/api/v3/stories/'+match.group(1),params={'fields':'id,title,cover,numParts,parts(id,title,url)'},headers={'Accept':'application/json'})
        if r.status_code!=200:return None
        data=r.json()
        if not isinstance(data,dict) or not isinstance(data.get('parts'),list):return None
        parts=[]
        for part in data['parts']:
            if not isinstance(part,dict):continue
            link=normalized_url(url,part.get('url') or ('https://www.wattpad.com/'+str(part.get('id',''))))
            if link and re.fullmatch(r'/\d{6,}(?:-[^/]*)?/?',urlparse(link).path):
                parts.append({'title':str(part.get('title') or 'Chương '+str(len(parts)+1))[:180],'url':link})
        return {'parts':parts,'cover':data.get('cover'),'numParts':data.get('numParts')}
    except (httpx.HTTPError,ValueError,TypeError):return None


async def wattpad_story_archive(client,story_url):
    """Fetch publicly available story text ZIP, as used by WattpadDownloader."""
    match=re.search(r'/story/(\d+)',urlparse(story_url).path)
    if not match:return None
    import io,zipfile
    try:
        r=await client.get('https://www.wattpad.com/apiv2/',params={'m':'storytext','group_id':match.group(1),'output':'zip'},timeout=35,headers={'Accept':'application/zip,application/octet-stream'})
        if r.status_code!=200 or len(r.content)>24_000_000 or not zipfile.is_zipfile(io.BytesIO(r.content)):return None
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            result={}
            for info in z.infolist():
                if len(result)>=2000:break
                part_id=re.fullmatch(r'(\d+)(?:\.html?)?',info.filename.rsplit('/',1)[-1])
                if not part_id or info.file_size>750000:continue
                result[part_id.group(1)]=z.read(info).decode('utf-8',errors='replace')
            return result if result else None
    except (httpx.HTTPError,ValueError,OSError,zipfile.BadZipFile):return None

def wattpad_story_text(html,base,include_images=False):
    """Only retain actual p tags from Wattpad's dedicated story-text archive."""
    from bs4 import Tag
    soup=BeautifulSoup(html,'html.parser')
    root=soup.body or soup
    paragraphs=[]
    images=[]
    for p in root.find_all('p'):
        if p.find_parent(['aside','nav','footer','header','script','style']):continue
        for img in p.find_all('img'):
            src=img.get('data-src') or img.get('src')
            if src and include_images:
                resolved=image_url(base,src)
                if resolved:images.append(resolved)
        for bad in p.select('script,style,button,nav,aside'):bad.decompose()
        for br in p.find_all('br'):br.replace_with('\n')
        text=p.get_text('',strip=False).replace('\xa0',' ').strip()
        text=re.sub(r'[ \t]+',' ',text)
        text=re.sub(r'\n{3,}','\n\n',text)
        if text:paragraphs.append(text)
    return '\n\n'.join(paragraphs)[:300000],images[:50]

def page_links(soup,base,story_path):
    result=[]
    for a in soup.select(".pagination a[href],.pagination-container a[href],a[href*='page='],a[href*='/trang-']"):
        href=normalized_url(base,a.get("href",""))
        if href and urlparse(href).path.rstrip("/").startswith(story_path.rstrip("/")) and href not in result:result.append(href)
    return result[:50]

def chapter_body(soup, source_url=''):
    host=(urlparse(source_url).hostname or '').lower()
    special=host.endswith(('wattpad.com','inkitt.com'))
    preferred=(('#story-text-container',) if host.endswith('inkitt.com') else ())+('[data-testid=story-part-content]','.story-part-content','.panel-reading','.reading-content','.chapter-text','.chapter-content')
    chapter=next((soup.select_one(sel) for sel in preferred if soup.select_one(sel)),None) if special else next((soup.select_one(sel) for sel in SELECTORS if soup.select_one(sel)),None)
    if not chapter:return "",[]
    for tag in chapter.select("script,style,nav,aside,footer,button,.ads,.advertisement,.read-more,.show-more,.more-btn,.expand,.collapse,[role=button]"):tag.decompose()
    for a in chapter.select("a"):
        if re.search(r"^(xem thêm|đọc thêm|thu gọn|read more|show more)$",a.get_text(" ",strip=True),re.I):a.decompose()
    if special:
        for tag in list(chapter.select('a,button,header,footer,nav,aside,.share,.sharing,.story-stats,.stats,.toolbar,.story-actions,.action-bar,[role=toolbar],[role=navigation]')):tag.decompose()
    images=[]
    for img in chapter.select("img"):
        src=img.get("data-src") or img.get("data-original") or img.get("src")
        if src:images.append(src)
    # Extract block paragraphs without inserting breaks between inline spans.
    from bs4 import NavigableString, Tag
    blocks=[]
    def walk(node):
        if isinstance(node,NavigableString):
            return str(node)
        if not isinstance(node,Tag):return ""
        if node.name=="br":return "\n"
        if node.name in ("img","picture","svg"):return ""
        if node.name in ("p","div","section","article","li","blockquote","h1","h2","h3","h4"):
            content="".join(walk(child) for child in node.children).strip()
            return "\n\n"+content+"\n\n" if content else ""
        return "".join(walk(child) for child in node.children)
    content="".join(walk(child) for child in chapter.children)
    content=content.replace("\xa0"," ").replace("\r","")
    content=re.sub(r"[ \t]+"," ",content)
    content=re.sub(r" *\n *","\n",content)
    content=re.sub(r"\n{3,}","\n\n",content).strip()
    if special:
        # Only accept story paragraphs. Never harvest recommendation cards or comments.
        ui=re.compile(r'^(?:[0-9]+(?:[.,][0-9]+)?[KMB]?|[0-9]+\s*(?:comments?|bình luận|votes?|lượt đọc)|bởi .{1,70}|by .{1,70}|Chia sẻ|Gửi|Post to Your Profile|Share via Email|Report Story|Share|Send|Vote|Reply|Trả lời|Like|Thích|Xem thêm|Đọc thêm|[?？]{1,4})$',re.I)
        boundary=re.compile(r'^(?:Các truyện được quảng cáo|Bạn cũng sẽ thích|Truyện đề xuất|Truyện liên quan|Đề xuất cho bạn|Promoted Stories|Promoted|You\s+May Also Like|You Might Also Like|Recommended(?: Stories)?|Recommendations|More Stories|Comments?|Bình luận|Phản hồi)(?:\s*[:：])?$',re.I)
        kept=[]
        stop=False
        for paragraph in content.split('\n\n'):
            lines=[]
            for line in paragraph.split('\n'):
                line=line.strip()
                if boundary.fullmatch(line):
                    stop=True
                    break
                if line and not ui.fullmatch(line):lines.append(line)
            if stop:break
            if lines:kept.append('\n'.join(lines))
        content='\n\n'.join(kept)
        if not content:return '',[]
    return content[:300000],images[:50]

@app.post("/discover")
async def discover(req:ReadRequest):
    u=urlparse(req.url.strip())
    if u.scheme!="https" or not u.hostname or not allowed(u.hostname.lower()) or u.port not in (None,443):
        raise HTTPException(400,"Nguồn chưa được hỗ trợ hoặc URL không hợp lệ.")
    try:
        async with httpx.AsyncClient(timeout=20,follow_redirects=False,headers={"User-Agent":"Mozilla/5.0 (compatible; ReedttReader/1.1)"}) as client:
            soup,base=await get_page(client,req.url)
            title=(soup.select_one("meta[property='og:title']") or {}).get("content","") or (soup.h1.get_text(" ",strip=True) if soup.h1 else "")
            author=soup.select_one("span#storyAuthor,[itemprop='author'],.author")
            story_path=urlparse(base).path
            found={x["url"]:x for x in chapter_links(soup,base,story_path)}
            wattpad_data=await wattpad_story_metadata(client,base) if (urlparse(base).hostname or '').lower().endswith('wattpad.com') else None
            if wattpad_data and wattpad_data['parts']:
                found={part['url']:part for part in wattpad_data['parts']}
            pages=page_links(soup,base,story_path)
            # Bounded pagination discovery to avoid unlimited crawling.
            async def listing(page):
                try:
                    sub,_=await get_page(client,page)
                    return chapter_links(sub,page,story_path)
                except (httpx.HTTPError,HTTPException):return []
            for group in await asyncio.gather(*(listing(p) for p in pages[:12])):
                for x in group:found[x["url"]]=x
            def meta(selector):
                t=soup.select_one(selector)
                return (t.get("content") or "").strip() if t else ""
            def label(selector):
                t=soup.select_one(selector)
                return t.get_text(" ",strip=True) if t else ""
            desc_node=soup.select_one("p.story-summary,.desc-text,.book-desc,.story-description,.description,.desc,.intro,.summary,[itemprop=description]")
            if desc_node:
                # Keep source paragraph boundaries, explicit BR breaks, and inline sentence flow.
                for junk in desc_node.select("button,script,style,.read-more,.show-more,.more-btn,.expand,.collapse,[role=button],a"):
                    junk.decompose()
                for br in desc_node.select("br"):br.replace_with("\n")
                for tag in reversed(list(desc_node.select("p,div,section,article,li,blockquote,h1,h2,h3,h4"))):
                    tag.insert_before("\n\n")
                    tag.insert_after("\n\n")
                description=desc_node.get_text("",strip=False)
                description=description.replace("\r","")
                description=re.sub(r"[ \t]+"," ",description)
                description=re.sub(r" *\n *","\n",description)
                description=re.sub(r"\n{3,}","\n\n",description).strip()
            else:description=""
            description=description or meta("meta[name=description]") or meta("meta[property='og:description']")
            description=re.sub(r"\s*(?:Xem thêm|Thu gọn|Đọc thêm|Show more|Read more)\s*$","",description,flags=re.I).strip()
            cover_tag=soup.select_one(".book-cover img,.book img,.story-cover img,.cover img,.info img,meta[property='og:image'],meta[name='twitter:image']")
            cover_url=(cover_tag.get("data-src") or cover_tag.get("data-original") or cover_tag.get("data-lazy-src") or cover_tag.get("src") or cover_tag.get("content") or "") if cover_tag else ""
            inkitt_cover=soup.select_one("div.story-horizontal-cover__front") if (urlparse(base).hostname or '').lower().endswith("inkitt.com") else None
            cover_css=inkitt_cover.get("style","") if inkitt_cover else ""
            cover_match=re.search(r"url\(\s*['\"]?([^'\"\)]+)",cover_css,re.I)
            cover=image_url(base,(wattpad_data or {}).get('cover') or (cover_match.group(1) if cover_match else "") or meta("meta[property='og:image']") or meta("meta[name='twitter:image']") or cover_url)
            if not cover:
                raw=meta("meta[property='og:image']") or meta("meta[name='twitter:image']")
                cover=image_url(base,raw) if raw else None
            categories=[a.get_text(" ",strip=True) for a in soup.select(".tag a,.tags a,.genres a,.genre a,.book-tags a,[itemprop=genre] a") if a.get_text(" ",strip=True)]
            if not categories:
                raw=label("[itemprop=genre],.genres,.genre,.book-tags")
                categories=[x.strip() for x in raw.split(",") if x.strip()]
            chapters=list(found.values())
            def order(x):
                m=re.search(r"(?:chương|chuong|chapter)[^0-9]{0,12}(\d+)",x["title"]+" "+x["url"],re.I)
                return int(m.group(1)) if m else 10**9
            if not (wattpad_data and wattpad_data['parts']):chapters.sort(key=order)
            return {"title":title[:200],"author":author.get_text(" ",strip=True)[:120] if author else "","description":description[:15000],"cover":cover,"categories":categories[:30],"chapters":chapters[:1000],"count":len(chapters),"truncated":len(pages)>12 or len(chapters)>1000}
    except httpx.HTTPError as exc:
        raise HTTPException(502,"Nguồn không phản hồi hoặc từ chối truy cập công khai.") from exc

@app.post("/batch")
async def batch(req:BatchRequest):
    if req.start<1 or req.end<req.start or req.end-req.start>=20:
        raise HTTPException(400,"Mỗi yêu cầu kỹ thuật hỗ trợ tối đa 20 chương; website tự chia đợt.")
    discovery=None
    if req.chapters is not None:
        chosen=req.chapters
        if len(chosen)>20:raise HTTPException(400,"Mỗi đợt tối đa 20 chương.")
        for item in chosen:
            if not normalized_url(req.url,item.get("url","")):raise HTTPException(400,"URL chương không hợp lệ.")
    else:
        discovery=await discover(ReadRequest(url=req.url))
        chosen=discovery["chapters"][req.start-1:req.end]
    if not chosen:raise HTTPException(404,"Không tìm thấy chương.")
    is_wattpad=(urlparse(req.url).hostname or '').lower().endswith('wattpad.com')
    semaphore=asyncio.Semaphore(5)
    async with httpx.AsyncClient(timeout=20,follow_redirects=False,limits=httpx.Limits(max_connections=6),headers={"User-Agent":"Mozilla/5.0 (compatible; ReedttReader/1.1)"}) as client:
        archive=await wattpad_story_archive(client,req.url) if is_wattpad else None
        async def fetch_one(item):
            async with semaphore:
                try:
                    if archive:
                        match=re.match(r'/([0-9]{6,})',urlparse(item['url']).path)
                        part=archive.get(match.group(1)) if match else None
                        if part is not None:
                            text,imgs=wattpad_story_text(part,item['url'],req.include_images)
                            return {'title':item['title'],'url':item['url'],'content':text,'images':imgs}
                    soup,base=await get_page(client,item["url"])
                    content,images=chapter_body(soup,base)
                    resolved=[image_url(base,x) for x in images] if req.include_images else []
                    return {"title":item["title"],"url":item["url"],"content":content,"images":[x for x in resolved if x]}
                except (httpx.HTTPError,HTTPException):
                    return {"title":item["title"],"url":item["url"],"content":"","images":[],"error":"Không tải được chương"}
        result=await asyncio.gather(*(fetch_one(item) for item in chosen))
    return {"title":discovery["title"] if discovery else "","author":discovery["author"] if discovery else "","chapters":result,"count":len(result)}

@app.post("/image")
async def image(req:ReadRequest):
    url=image_url(req.url,req.url)
    if not url:raise HTTPException(400,"URL ảnh HTTPS không hợp lệ.")
    u=urlparse(url)
    if not await public_image_host(u.hostname):raise HTTPException(400,"Máy chủ ảnh không an toàn hoặc không truy cập được.")
    try:
        async with httpx.AsyncClient(timeout=22,follow_redirects=False,trust_env=False) as client:
            r=await client.get(url,headers={"User-Agent":"Mozilla/5.0","Referer":f"https://{u.hostname}/","Accept":"image/avif,image/webp,image/png,image/jpeg,*/*"})
            r.raise_for_status()
            mime=r.headers.get("content-type","").split(";")[0].lower()
            if mime not in ("image/jpeg","image/png","image/webp") or len(r.content)>5_000_000:
                raise HTTPException(415,"Ảnh không hỗ trợ hoặc quá lớn.")
            return Response(r.content,media_type=mime,headers={"Cache-Control":"public, max-age=3600"})
    except httpx.HTTPError as exc:
        raise HTTPException(502,"Không tải được ảnh từ máy chủ nguồn.") from exc

# Convert the actual generated Word document to a readable, Unicode PDF.
class ConvertWordRequest(BaseModel):
    docx_base64: str

@app.post("/convert-docx-pdf")
async def convert_docx_pdf(req: ConvertWordRequest):
    import base64
    import io
    import os
    from xml.sax.saxutils import escape
    from docx import Document
    from docx.oxml.ns import qn
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, KeepTogether
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from matplotlib import font_manager
    try:
        data=base64.b64decode(req.docx_base64,validate=True)
        if len(data)>18_000_000:raise HTTPException(413,"File Word quá lớn.")
        doc=Document(io.BytesIO(data))
        regular=font_manager.findfont(font_manager.FontProperties(family="DejaVu Sans"))
        bold=font_manager.findfont(font_manager.FontProperties(family="DejaVu Sans",weight="bold"))
        if "ReedttUnicode" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("ReedttUnicode",regular))
            pdfmetrics.registerFont(TTFont("ReedttUnicodeBold",bold))
            pdfmetrics.registerFontFamily("ReedttUnicode",normal="ReedttUnicode",bold="ReedttUnicodeBold")
        out=io.BytesIO()
        pdf=SimpleDocTemplate(out,pagesize=A4,leftMargin=36,rightMargin=36,topMargin=36,bottomMargin=36,title=doc.core_properties.title or "Reedtt")
        styles={
          "Normal":ParagraphStyle("Body",fontName="ReedttUnicode",fontSize=11,leading=16.5,spaceAfter=8,wordWrap="CJK"),
          "Heading2":ParagraphStyle("Chapter",fontName="ReedttUnicodeBold",fontSize=11,leading=16.5,spaceBefore=12,spaceAfter=6,keepWithNext=True),
          "StoryTitle":ParagraphStyle("Title",fontName="ReedttUnicodeBold",fontSize=25,leading=31,textColor="#1F3864",alignment=TA_CENTER,spaceAfter=12,keepWithNext=True)
        }
        flow=[]
        from docx.oxml.ns import qn
        from PIL import Image as PILImage
        for p in doc.paragraphs:
            kind=p.style.name if p.style else "Normal"
            if kind in ("Heading 2","Heading2"):kind="Heading2"
            if kind not in styles:kind="Normal"
            txt=p.text.strip()
            if txt:
                flow.append(Paragraph(escape(txt),styles[kind]))
            for run in p.runs:
                for blip in run._element.xpath(".//a:blip"):
                    rid=blip.get(qn("r:embed"))
                    if not rid or rid not in p.part.related_parts:continue
                    raw=p.part.related_parts[rid].blob
                    try:
                        picture=PILImage.open(io.BytesIO(raw))
                        w,h=picture.size
                        ratio=min(1,450/max(w,1),540/max(h,1))
                        flow.append(Image(io.BytesIO(raw),width=w*ratio,height=h*ratio))
                        flow.append(Spacer(1,8))
                    except Exception:continue
        if not flow:raise HTTPException(400,"File Word không có nội dung.")
        pdf.build(flow)
        return Response(content=out.getvalue(),media_type="application/pdf",headers={"Content-Disposition":'attachment; filename="reedtt.pdf"'})
    except HTTPException:raise
    except Exception as exc:raise HTTPException(422,"Không thể chuyển Word thành PDF: "+str(exc)[:180])
