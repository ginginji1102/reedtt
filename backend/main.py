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

ALLOWED = ("truyenfull.live", "wattpad.com", "wordpress.com", "navyteams.com", "inkitt.com", "giatocvuongtai.com", "luotnovel1.site")
SELECTORS = ("#chapter-content", "#chapterContent", "#chapter-content-body", ".chapter-reading", ".chapter-body", ".chapter-text", ".reading-content", ".entry-content", ".chapter-content", ".reading-content", ".entry-content", ".chapter-c", "article", "[itemprop=articleBody]")
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
    return {"title":title[:200],"author":author_name[:120],"content":content[:500000],"chapters":links,"count":len(links)}

# Discover public chapter links from the story landing page and pagination.
def normalized_url(base, href):
    try:
        u = urlparse(urljoin(base, href))
        # Old WordPress indexes often contain http:// chapter links from 2012.
        # Upgrade those links to HTTPS before validating; never fetch plaintext HTTP.
        if u.scheme=="http" and u.hostname and u.hostname.lower().endswith(".wordpress.com") and u.port in (None,80):
            u=u._replace(scheme="https",netloc=u.hostname)
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
    for _ in range(4):
        if r.status_code not in (301,302,303,307,308):break
        target=normalized_url(str(r.url),r.headers.get("location",""))
        if not target:raise HTTPException(400,"Chuyển hướng tới nguồn không được hỗ trợ.")
        r=await client.get(target)
    if r.status_code in (401,403,429) and (urlparse(url).hostname or "").endswith("inkitt.com"):
        raise HTTPException(502,"Inkitt từ chối yêu cầu từ máy chủ (HTTP "+str(r.status_code)+"). Không thể tải chương công khai qua Render; không phải lỗi tìm mục lục.")
    r.raise_for_status()
    if "html" not in r.headers.get("content-type","").lower() or len(r.content)>3_000_000:
        raise HTTPException(400,"Trang không phải HTML hợp lệ hoặc quá lớn.")
    return BeautifulSoup(r.text,"html.parser"),str(r.url)

def chapter_links(soup,base,story_path):
    found={}
    host=(urlparse(base).hostname or "").lower()
    selectors=".list-chapter a[href],#list-chapter a[href],.chapter-list a[href],.list-chapters a[href],ul.list-chapter a[href],#chaptersList a[href],a[href*='chuong-'],a[href*='chapter-']"
    if host.endswith("wattpad.com"):
        selectors+=",a[href^='/'][href*='-'],a[href*='wattpad.com/']"
    elif host.endswith("inkitt.com"):
        selectors+=", .chapters-list a[href],a[href*='/chapters/'],a[href*='/chapter/']"
    if host.endswith("giatocvuongtai.com"):
        # Astro story pages link to /reader/?storyId=UUID&chapter=UUID.
        selectors+=", a[href*='/reader'][href*='storyId='][href*='chapter=']"
    if host.endswith("luotnovel1.site"):
        selectors += ", .chapter-list a, .list-chapter a, [class*='chapter'] a[href], a[href*='/chuong-'], a[href*='/chapter-'], a[href*='/truyen-chu/']"
    if host.endswith("wordpress.com"):
        # WebToEpub WordpressBaseParser: take ALL anchors in the article content,
        # not just links prefixed "Chapter". This includes titled extras.
        article=soup.select_one("div.entry-content,div.post-content,ul.wp-block-post-template,.wp-block-cover__inner-container,div.single-entry-content,.post-entry,[itemprop='articleBody']")
        if article:
            for a in article.select("a[href]"):
                if a.find_parent(["nav","aside","footer","form"]) or a.find_parent(class_=re.compile(r"comment|related|share|social|widget|pagination|post-meta|entry-meta",re.I)):continue
                label=a.get_text(" ",strip=True)
                if not label:continue
                href=normalized_url(base,a.get("href",""))
                if not href:continue
                target=urlparse(href)
                if target.path.rstrip("/")==story_path.rstrip("/"):continue
                if target.hostname!=(urlparse(base).hostname or "").lower():continue
                # WordPress posts generally have date-prefixed permalinks.
                if not re.match(r"^/\d{4}/\d{2}/\d{2}/[^/]+/?$",target.path):continue
                found[href]={"title":label[:180],"url":href}
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
        elif host.endswith("giatocvuongtai.com"):
            from urllib.parse import parse_qs
            params=parse_qs(target.query)
            if target.hostname!=host or target.path.rstrip("/")!="/reader" or not params.get("storyId") or not params.get("chapter"):continue
            # Use actual chapter entries and discard reading statistics.
            if "chapter-list-item" not in (a.get("class") or []):continue
            heading=a.select_one("strong,p")
            label=heading.get_text(" ",strip=True) if heading else ""
            if not re.fullmatch(r"(?:chương|chuong|chapter)\s*\d+",label,re.I):continue
        elif host.endswith("luotnovel1.site"):
            if target.hostname != host or not re.search(r"(?:chuong|chapter|chap)[-_ /]?\d+|/\d+(?:/|$)",target.path,re.I):
                if not re.search(r"^(?:chương|chuong|chapter)\s*\d+",label,re.I):continue
        elif not re.search(r"(chương|chuong|chapter)\s*[-:]?\s*\d+|/(chuong|chapter)[-_]?\d+",label+" "+href,re.I):continue
        if host.endswith("navyteams.com"):
            # Chapter list entries may append publication dates in child elements.
            for child in a.select("time,.date,.chapter-date,.chapter-time,.published,.updated,[class*='date'],[class*='time']"):
                child.decompose()
            label=a.get_text(" ",strip=True) or label
            label=re.sub(r"\s*(?:[-–—|·•]\s*)?(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}[/.-]\d{1,2}[/.-]\d{1,2})(?:\s+\d{1,2}:\d{2})?\s*$","",label).strip()
        found[href]={"title":label[:180] or href.rsplit("/",1)[-1],"url":href}
    return list(found.values())

async def wattpad_story_metadata(client,url):
    """WattpadDownloader-inspired: support either a story URL or a part URL."""
    parsed=urlparse(url)
    story_match=re.search(r'/story/(\d+)',parsed.path)
    part_match=re.match(r'^/(\d{6,})(?:-|/|$)',parsed.path)
    story_id=story_match.group(1) if story_match else None
    try:
        if not story_id and part_match:
            part_id=part_match.group(1)
            response=await client.get(
                'https://www.wattpad.com/api/v3/story_parts/'+part_id,
                params={'fields':'groupId,group(id,title,cover,user(username),parts(id,title))'},
                headers={'Accept':'application/json'})
            if response.status_code!=200:return None
            data=response.json()
            group=data.get('group') if isinstance(data,dict) else None
            story_id=str(data.get('groupId') or (group.get('id') if isinstance(group,dict) else '') or '')
        if not story_id or not re.fullmatch(r'\d+',story_id):return None
        response=await client.get(
            'https://www.wattpad.com/api/v3/stories/'+story_id,
            params={'fields':'id,title,cover,numParts,user(username),parts(id,title,url)'},
            headers={'Accept':'application/json'})
        if response.status_code!=200:return None
        data=response.json()
        if not isinstance(data,dict) or not isinstance(data.get('parts'),list):return None
        parts=[]
        for part in data['parts']:
            if not isinstance(part,dict):continue
            identifier=str(part.get('id') or '')
            if not re.fullmatch(r'\d{6,}',identifier):continue
            link=normalized_url(url,part.get('url') or ('https://www.wattpad.com/'+identifier))
            if link and re.fullmatch(r'/\d{6,}(?:-[^/]*)?/?',urlparse(link).path):
                parts.append({'title':str(part.get('title') or 'Chương '+str(len(parts)+1))[:180],'url':link})
        user=data.get('user') if isinstance(data.get('user'),dict) else {}
        return {'parts':parts,'cover':data.get('cover'),'title':data.get('title'),'author':user.get('username',''),'story_url':'https://www.wattpad.com/story/'+story_id,'numParts':data.get('numParts')}
    except (httpx.HTTPError,ValueError,TypeError,KeyError,AttributeError):return None

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
    # Images can be standalone elements outside paragraphs.
    if include_images:
        for img in root.select('img'):
            src=img.get('data-src') or img.get('data-original') or img.get('src')
            if src:
                resolved=image_url(base,src)
                if resolved and resolved not in images:images.append(resolved)
    for p in root.find_all('p'):
        if p.find_parent(['aside','nav','footer','header','script','style']):continue
        for img in p.find_all('img'):
            src=img.get('data-src') or img.get('src')
            if src and include_images:
                resolved=image_url(base,src)
                if resolved and resolved not in images:images.append(resolved)
        for bad in p.select('script,style,button,nav,aside'):bad.decompose()
        for br in p.find_all('br'):br.replace_with('\n')
        text=p.get_text('',strip=False).replace('\xa0',' ').strip()
        text=re.sub(r'[ \t]+',' ',text)
        text=re.sub(r'\n{3,}','\n\n',text)
        if text:paragraphs.append(text)
    return '\n\n'.join(paragraphs)[:300000],images[:1000]

def page_links(soup,base,story_path):
    result=[]
    for a in soup.select(".pagination a[href],.pagination-container a[href],a[href*='page='],a[href*='/trang-']"):
        href=normalized_url(base,a.get("href",""))
        if href and urlparse(href).path.rstrip("/").startswith(story_path.rstrip("/")) and href not in result:result.append(href)
    return result[:50]

def chapter_body(soup, source_url=''):
    host=(urlparse(source_url).hostname or '').lower()
    special=host.endswith(('wattpad.com','inkitt.com'))
    preferred=(('.story-page-text','#story-text-container') if host.endswith('inkitt.com') else ())+('[data-testid=story-part-content]','.story-part-content','.panel-reading','.reading-content','.chapter-text','.chapter-content')
    if host.endswith("luotnovel1.site"):
        preferred=("#chapter-content","#chapterContent",".chapter-content",".chapter-body",".reading-content",".read-content",".chapter-text",".entry-content",".content-chapter",".novel-content","article", "[class*='chapter-content']")
    if host.endswith("navyteams.com"):
        # Read only the actual visible novel body; avoid generic page containers.
        chapter=soup.select_one("#novelContent")
        if chapter is None:
            chapter=soup.select_one(".reader-container .reader-content .content-type-novel .novel-text")
        if chapter is not None:
            # Work on a copy so cleanup does not mutate the soup used elsewhere.
            from bs4 import BeautifulSoup
            chapter=BeautifulSoup(str(chapter),"html.parser")
    elif host.endswith("giatocvuongtai.com"):
        # Reader is a client-rendered Astro app; these are best-effort HTML fallbacks.
        chapter=next((soup.select_one(sel) for sel in ("[data-reader-content]",".reader-content",".chapter-content",".prose","article") if soup.select_one(sel)),None)
    else:
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


def inkitt_html_metadata(soup, story_url):
    import json
    match=re.search(r"/stories/(\d+)",urlparse(story_url).path)
    if not match:
        raise HTTPException(400,"URL Inkitt không hợp lệ.")
    story_id=match.group(1)
    base=f"https://www.inkitt.com/stories/{story_id}"
    book={}
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            item=json.loads(node.string or node.get_text())
            for obj in (item if isinstance(item,list) else [item]):
                if isinstance(obj,dict) and obj.get("@type")=="Book":
                    book=obj
                    break
        except (ValueError,TypeError):
            continue
    chapter_data=[]
    for node in soup.select('script[data-component-name="StoryChaptersList"]'):
        try:
            item=json.loads(node.string or node.get_text())
            if isinstance(item.get("chapters"),list):
                chapter_data=item["chapters"]
                break
        except (ValueError,TypeError,AttributeError):
            continue
    chapters=[]
    for ch in chapter_data[:1000]:
        if not isinstance(ch,dict):
            continue
        number=ch.get("number")
        if not str(number).isdigit() or int(number)<1:
            continue
        path=ch.get("path") or f"/stories/{story_id}/chapters/{number}"
        link=normalized_url(base,path)
        if link and re.fullmatch(r"/stories/"+story_id+r"/chapters/\d+",urlparse(link).path):
            chapters.append({"title":str(ch.get("name") or f"Chương {number}")[:180],"url":link})
    author=book.get("author") or {}
    image=book.get("image") or {}
    summary_node=soup.select_one(".story-summary")
    summary=summary_node.get_text("\n",strip=True) if summary_node else book.get("description") or ""
    cover=image.get("url","") if isinstance(image,dict) else image
    if not cover:
        image_node=soup.select_one('meta[property="og:image"]')
        cover=image_node.get("content","") if image_node else ""
    title=book.get("name") or ""
    if not title:
        title_node=soup.select_one('meta[property="og:title"]')
        title=title_node.get("content","") if title_node else ""
    genres=book.get("genre") or []
    return {"title":str(title)[:200],"author":str(author.get("name","") if isinstance(author,dict) else author)[:120],"description":str(summary)[:15000],"cover":image_url(base,cover) if isinstance(cover,str) and cover else None,"categories":genres if isinstance(genres,list) else [genres],"chapters":chapters,"count":len(chapters),"truncated":len(chapter_data)>1000}

async def inkitt_info(client, story_url):
    """Use Inkitt's public story metadata endpoint, as lightnovel-crawler does."""
    match=re.search(r"/stories/(\d+)",urlparse(story_url).path)
    if not match:
        return None
    story_id=match.group(1)
    api_url=f"https://www.inkitt.com/api/stories/{story_id}"
    try:
        response=await client.get(api_url,headers={"Accept":"application/json","Referer":story_url})
        if response.status_code in (401,403,429):
            raise HTTPException(502,f"Inkitt API từ chối truy cập (HTTP {response.status_code}).")
        response.raise_for_status()
        data=response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(502,f"Không tải được dữ liệu truyện từ Inkitt API: {type(exc).__name__}") from exc
    except ValueError as exc:
        raise HTTPException(502,"Inkitt API không trả về JSON hợp lệ.") from exc
    if not isinstance(data,dict) or not isinstance(data.get("chapters"),list):
        raise HTTPException(502,"Inkitt API không có danh sách chương công khai.")
    base=f"https://www.inkitt.com/stories/{story_id}"
    chapters=[]
    for entry in data["chapters"][:1000]:
        if not isinstance(entry,dict):
            continue
        number=entry.get("chapter_number")
        if not isinstance(number,int) and not (isinstance(number,str) and number.isdigit()):
            continue
        number=int(number)
        if number<1:
            continue
        name=str(entry.get("name") or f"Chương {number}").strip()
        chapters.append({"title":f"{number}. {name}"[:180],"url":f"{base}/chapters/{number}"})
    author=data.get("user") or {}
    cover=data.get("vertical_cover") or {}
    # Inkitt descriptions can be HTML and may be stored under several summary fields.
    summary=data.get("summary") or data.get("description") or data.get("synopsis") or ""
    if isinstance(summary,dict):
        summary=summary.get("text") or summary.get("content") or ""
    if isinstance(summary,str):
        summary_soup=BeautifulSoup(summary,"html.parser")
        for br in summary_soup.select("br"):br.replace_with("\n")
        for p in summary_soup.select("p"):p.append("\n\n")
        summary=summary_soup.get_text("",strip=False)
        summary=re.sub(r"\n{3,}","\n\n",summary).strip()
    else:
        summary=""
    raw_genres=data.get("genres") or data.get("categories") or []
    if not isinstance(raw_genres,list):raw_genres=[]
    categories=[]
    for genre in raw_genres:
        name=genre.get("name") or genre.get("title") if isinstance(genre,dict) else genre
        if isinstance(name,str) and name.strip():categories.append(name.strip())
    return {
        "title":str(data.get("title") or "Truyện Inkitt")[:200],
        "author":str(author.get("name") or "")[:120] if isinstance(author,dict) else "",
        "description":summary[:15000],
        "cover":image_url(base,cover.get("url","")) if isinstance(cover,dict) else None,
        "categories":categories[:30],
        "chapters":chapters,
        "count":len(chapters),
        "truncated":len(data["chapters"])>1000,
    }


@app.post("/discover")
async def discover(req:ReadRequest):
    u=urlparse(req.url.strip())
    if u.scheme!="https" or not u.hostname or not allowed(u.hostname.lower()) or u.port not in (None,443):
        raise HTTPException(400,"Nguồn chưa được hỗ trợ hoặc URL không hợp lệ.")
    if (u.hostname or "").lower().endswith("wattpad.com"):
        async with httpx.AsyncClient(timeout=25,follow_redirects=False,headers={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36","Accept":"application/json","Accept-Language":"en-US,en;q=0.9"}) as client:
            info=await wattpad_story_metadata(client,req.url)
            if info and info.get("parts"):
                return {"title":info.get("title") or "", "author":info.get("author") or "", "cover":info.get("cover") or "", "chapters":info["parts"],"count":len(info["parts"]),"story_url":info.get("story_url") or req.url}
        # Wattpad's API can return 403/429 while public story HTML still contains part links.
        try:
            async with httpx.AsyncClient(timeout=25,follow_redirects=False,headers={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36","Accept":"text/html,application/xhtml+xml","Accept-Language":"vi-VN,vi;q=0.9"}) as client:
                soup,base=await get_page(client,req.url)
                chapters=chapter_links(soup,base,urlparse(base).path)
                if not chapters:
                    for a in soup.select("a[href]"):
                        link=normalized_url(base,a.get("href",""))
                        if link and re.fullmatch(r"/\\d{6,}(?:-[^/]*)?/?",urlparse(link).path):
                            chapters.append({"title":a.get_text(" ",strip=True)[:180] or "Chương "+str(len(chapters)+1),"url":link})
                if chapters:
                    chapters=list({item["url"]:item for item in chapters}.values())
                    return {"title":(soup.select_one("meta[property='og:title']") or {}).get("content",""),"author":"","chapters":chapters,"count":len(chapters)}
        except (HTTPException,httpx.HTTPError,ValueError):
            pass
        raise HTTPException(502,"Wattpad không trả mục lục qua API hoặc HTML công khai từ máy chủ. Kiểm tra quyền truy cập truyện hoặc thử lại sau.")
    if (u.hostname or "").lower().endswith("luotnovel1.site"):
        return await luotnovel_discover(req.url)
    if (u.hostname or "").lower().endswith("inkitt.com") and re.search(r"/stories/\d+",u.path):
        async with httpx.AsyncClient(timeout=20,follow_redirects=False,headers={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36","Accept-Language":"en-US,en;q=0.9"}) as client:
            try:
                match=re.search(r"/stories/(\d+)",u.path)
                page_url=f"https://www.inkitt.com/stories/{match.group(1)}/chapters/1"
                soup,final_url=await get_page(client,page_url)
                parsed=inkitt_html_metadata(soup,final_url)
                if parsed["chapters"]:
                    return parsed
            except (HTTPException,httpx.HTTPError,ValueError,AttributeError):
                pass
            return await inkitt_info(client,req.url)
    try:
        async with httpx.AsyncClient(timeout=20,follow_redirects=False,headers={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36","Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8","Accept-Language":"en-US,en;q=0.9"}) as client:
            soup,base=await get_page(client,req.url)
            if (urlparse(base).hostname or "").lower().endswith("navyteams.com"):
                keywords=soup.select_one('meta[name="keywords"]')
                raw_keywords=(keywords.get("content") or "").strip() if keywords else ""
                # Chapter pages may prefix the keywords with "Chương N, ".
                title=re.sub(r"^Chương\\s*\d+\\s*[,，:-]\\s*","",raw_keywords,flags=re.I).strip()
                if not title or title.casefold()=="navy team":
                    raise HTTPException(422,"NavyTeams không có tên truyện hợp lệ trong meta keywords.")
            else:
                title=(soup.select_one("meta[property='og:title']") or {}).get("content","") or (soup.h1.get_text(" ",strip=True) if soup.h1 else "")
            author=soup.select_one("span#storyAuthor,[itemprop='author'],.author")
            author_name=author.get_text(" ",strip=True) if author else ""
            if (urlparse(base).hostname or "").lower().endswith("navyteams.com"):
                # Read the story-information panel, not a generic site author label.
                for panel in soup.select("#storyInfo,.story-info,.story-information,.novel-info,.book-info,.story-detail,.story-details,.info-content,.info-list,.book-details,[id*='storyInfo']"):
                    for item in panel.select("li,div,p,span,dd,tr"):
                        line=item.get_text(" ",strip=True)
                        match=re.match(r"^(?:Tác giả|Author)\s*[:：]\s*(.+)$",line,re.I)
                        if match and len(line)<180:
                            author_name=match.group(1).strip()
                            break
                    if author_name:break
                if not author_name:
                    for node in soup.find_all(string=re.compile(r"^\s*Tác giả\s*[:：]?\s*$",re.I)):
                        parent=node.parent
                        if not parent:continue
                        sibling=parent.find_next_sibling()
                        if sibling:
                            candidate=sibling.get_text(" ",strip=True)
                            if candidate and len(candidate)<120:author_name=candidate;break
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
            if (urlparse(base).hostname or "").lower().endswith("wordpress.com"):
                article=soup.select_one("div.entry-content,div.post-content,div.single-entry-content,.post-entry")
                if article:
                    lines=[]
                    for block in article.select("p,h1,h2,h3,h4"):
                        if block.find_parent(["nav","aside","footer"]) or block.find_parent(class_=re.compile("comment|widget|related|share",re.I)):continue
                        # Preserve explicit HTML line breaks within a single WordPress paragraph.
                        for br in block.select("br"):
                            br.replace_with("\n")
                        block_text=block.get_text(" ",strip=False)
                        block_text=re.sub(r"[ \t]*\n[ \t]*","\n",block_text)
                        block_text=re.sub(r"[ \t]+"," ",block_text).strip()
                        if re.search(r"^(?:Chương|Chapter|Chap) +[0-9]+",block_text,re.I):break
                        if block.select_one("a[href]") and re.search(r"^(?:Chương|Chapter|Chap) +[0-9]+",block_text,re.I):break
                        if block_text and not block.find_parent(["p","h1","h2","h3","h4"]):lines.append(block_text)
                    if lines:description="\n\n".join(lines)[:15000]
            # Some story pages keep the complete synopsis inside the Summary section,
            # while meta descriptions and collapsed preview nodes contain only excerpts.
            summary_candidates=[]
            for selector in ("#summary",".story-summary",".summary-content",".summary-text",".story-description",".book-summary",".novel-summary","[itemprop='description']",".description",".summary"):
                for node in soup.select(selector):
                    from bs4 import BeautifulSoup as _BS
                    copy=_BS(str(node),"html.parser")
                    for junk in copy.select("script,style,button,.read-more,.show-more,.expand,.collapse,[role=button]"):
                        junk.decompose()
                    for br in copy.select("br"):
                        br.replace_with("\n")
                    for block in reversed(list(copy.select("p,li,blockquote"))):
                        block.insert_before("\n\n")
                        block.insert_after("\n\n")
                    candidate=copy.get_text("",strip=False)
                    candidate=re.sub(r"[ \\t]+"," ",candidate)
                    candidate=re.sub(r" *\\n *","\\n",candidate)
                    candidate=re.sub(r"\\n{3,}","\\n\\n",candidate).strip()
                    if candidate and candidate not in summary_candidates:
                        summary_candidates.append(candidate)
            if summary_candidates:
                best=max(summary_candidates,key=len)
                if len(best)>len(description):
                    description=best
            description=description or meta("meta[name=description]") or meta("meta[property='og:description']")
            description=re.sub(r"\s*(?:Xem thêm|Thu gọn|Đọc thêm|Show more|Read more)\s*$","",description,flags=re.I).strip()
            host=(urlparse(base).hostname or "").lower()
            if host.endswith("navyteams.com"):
                # Verified cover URL supplied for this specific story.
                if "/truyen/khong-con-la-ban-thuo-nho/" in urlparse(base).path + "/":
                    cover="https://img.ridicdn.net/cover/2336051899/xxlarge?dpi=xxhdpi"
                else:
                    cover=None
                # The chapter page can advertise a website icon as og:image.
                # Prefer the actual book cover, never a favicon or generic site icon.
                candidates=[]
                for node in soup.select(".book-cover img,.story-cover img,.novel-cover img,.cover img,.book-info img,.novel-info img,img[src*='ridicdn.net/cover/'],img[data-src*='ridicdn.net/cover/']"):
                    raw=node.get("data-src") or node.get("data-original") or node.get("src") or ""
                    if raw:candidates.append(raw)
                candidates.extend([meta("meta[property='og:image']"),meta("meta[name='twitter:image']")])
                def is_book_cover(raw):
                    u=urlparse(urljoin(base,raw))
                    p=u.path.lower()
                    return bool(raw) and not any(x in p for x in ("favicon","/icon","logo","avatar","apple-touch")) and (
                        "ridicdn.net/cover/" in (u.netloc+p) or
                        any(x in p for x in ("/cover/","/covers/","book-cover","novel-cover"))
                    )
                if not cover:
                    cover=next((image_url(base,raw) for raw in candidates if is_book_cover(raw)),None)
            else:
                cover_tag=soup.select_one(".book-cover img,.book img,.story-cover img,.cover img,.info img,meta[property='og:image'],meta[name='twitter:image']")
                cover_url=(cover_tag.get("data-src") or cover_tag.get("data-original") or cover_tag.get("data-lazy-src") or cover_tag.get("src") or cover_tag.get("content") or "") if cover_tag else ""
                inkitt_cover=soup.select_one("div.story-horizontal-cover__front") if host.endswith("inkitt.com") else None
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
    except httpx.HTTPStatusError as exc:
        status=exc.response.status_code
        raise HTTPException(502,f"Máy chủ nguồn trả HTTP {status}. "+("Website có thể đang chặn truy cập từ Render." if status in (403,429,503) else "Không thể tải trang truyện.")) from exc
    except httpx.ConnectError as exc:
        raise HTTPException(502,"Không kết nối được tới máy chủ nguồn từ Render (lỗi DNS, TLS hoặc kết nối mạng).") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(504,"Máy chủ nguồn không phản hồi trong 20 giây.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502,f"Không thể tải nguồn: {type(exc).__name__}.") from exc

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
    async with httpx.AsyncClient(timeout=20,follow_redirects=False,limits=httpx.Limits(max_connections=6),headers={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36","Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8","Accept-Language":"en-US,en;q=0.9"}) as client:
        archive_url=req.url
        if is_wattpad and not re.search(r"/story/\\d+",urlparse(req.url).path):
            info=await wattpad_story_metadata(client,req.url)
            if info and info.get("story_url"):archive_url=info["story_url"]
        archive=await wattpad_story_archive(client,archive_url) if is_wattpad else None
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
                    if is_wattpad and not content and not images:
                        return {"title":item["title"],"url":item["url"],"content":"","images":[],"error":"Wattpad không cung cấp nội dung chương qua HTML hoặc bản ZIP công khai; chương có thể bị khóa hoặc nguồn từ chối máy chủ."}
                    if not content and (urlparse(base).hostname or "").lower().endswith("navyteams.com"):
                        return {"title":item["title"],"url":item["url"],"content":"","images":[],"error":"NavyTeams: trang chương không có nội dung HTML nhận diện được (có thể tải bằng JavaScript hoặc cấu trúc khác)."}
                    resolved=[image_url(base,x) for x in images] if req.include_images else []
                    return {"title":item["title"],"url":item["url"],"content":content,"images":[x for x in resolved if x]}
                except (httpx.HTTPError,HTTPException) as exc:
                    if isinstance(exc,httpx.HTTPStatusError):
                        reason=f"Nguồn trả HTTP {exc.response.status_code}"
                    elif isinstance(exc,HTTPException):
                        reason=f"Nguồn: {exc.detail}"
                    elif isinstance(exc,httpx.TimeoutException):
                        reason="Nguồn phản hồi quá thời gian"
                    else:
                        reason=f"Lỗi kết nối nguồn ({type(exc).__name__})"
                    return {"title":item["title"],"url":item["url"],"content":"","images":[],"error":reason}
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

# Rendered-page extraction for JavaScript-driven, public story pages.
_render_browser_lock = asyncio.Lock()
_render_browser_ready = False

async def _render_browser():
    global _render_browser_ready
    from playwright.async_api import async_playwright
    async with _render_browser_lock:
        if not _render_browser_ready:
            import subprocess, sys
            # Render's existing build command installs Python packages only.
            # Install headless Chromium once into the service's writable cache.
            result = await asyncio.to_thread(
                subprocess.run, [sys.executable, "-m", "playwright", "install", "chromium"],
                capture_output=True, text=True, timeout=180
            )
            if result.returncode:
                raise HTTPException(503, "Không thể cài Chromium trên Render: " + result.stderr[-350:])
            _render_browser_ready = True
    return async_playwright()

async def _render_page(url):
    target = normalized_url(url, url)
    if not target or not (urlparse(target).hostname or "").endswith(("giatocvuongtai.com","luotnovel1.site")):
        raise HTTPException(400, "Chỉ hỗ trợ đường dẫn HTTPS của Gia Tộc Vượng Tài.")
    try:
        async with await _render_browser() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 900}, locale="vi-VN")
                page = await context.new_page()
                await page.goto(target, wait_until="domcontentloaded", timeout=35000)
                await page.wait_for_timeout(1800)
                for _ in range(8):
                    await page.evaluate("window.scrollBy(0, Math.max(650, window.innerHeight))")
                    await page.wait_for_timeout(500)
                data = await page.evaluate("""() => {
                  const title = document.querySelector('h1')?.textContent?.trim() || document.querySelector("meta[property='og:title']")?.content || '';
                  const links = [...document.querySelectorAll('a[href]')].map(a => ({
                    title: (a.querySelector('strong,p')?.textContent || a.textContent || '').trim(),
                    url: a.href
                  })).filter(x => {try {
                    const u=new URL(x.url);
                    return (u.pathname.replace(/\\/$/,'')==='/reader' && u.searchParams.has('storyId') && u.searchParams.has('chapter')) || (u.hostname.endsWith('luotnovel1.site') && /(?:chuong|chapter|chap)[-_ /]?\\d+|\\/\\d+(?:\\/|$)/i.test(u.pathname));
                  } catch {return false;}});
                  const body=document.querySelector('.reader-demo-body,.reader-content,.chapter-content,#chapter-content,[data-chapter-content]');
                  const ps=body?[...body.querySelectorAll('.chapter-paragraph')]:[];
                  const fallback=body&&!ps.length?[...body.querySelectorAll('p.reader-demo-para,p')]:[];
                  const content=(ps.length?ps:fallback).map(p=>p.textContent.trim()).filter(Boolean).join('\\n\\n');
                  return {title,links,content};
                }""")
                return data
            finally:
                await browser.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, "Playwright không thể đọc trang: " + str(exc)[:250]) from exc

@app.post("/render-discover")
async def render_discover(req: ReadRequest):
    data = await _render_page(req.url)
    story_id = __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs(urlparse(req.url).query).get("storyId", [None])[0]
    seen = set()
    chapters = []
    for item in data.get("links", []):
        link = normalized_url(req.url, item.get("url", ""))
        if not link or link in seen:
            continue
        if story_id and __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs(urlparse(link).query).get("storyId", [None])[0] != story_id:
            continue
        seen.add(link)
        chapters.append({"title":item.get("title") or f"Chương {len(chapters)+1}", "url":link})
    if not chapters and "/reader" in urlparse(req.url).path and data.get("content"):
        chapters.append({"title":data.get("title") or "Chương đã chọn", "url":req.url})
    return {"title":data.get("title", ""), "author":"", "chapters":chapters, "count":len(chapters)}

@app.post("/render-batch")
async def render_batch(req: BatchRequest):
    if req.chapters is None or len(req.chapters)>10:
        raise HTTPException(400, "Mỗi đợt tối đa 10 chương.")
    chapters=[]
    for item in req.chapters:
        url=normalized_url(req.url, item.get("url", ""))
        if not url:
            raise HTTPException(400, "URL chương không hợp lệ.")
        try:
            data=await _render_page(url)
            content=data.get("content", "")
            chapters.append({"title":item.get("title",""),"url":url,"content":content,"images":[],
                             **({"error":"Trang không hiển thị văn bản có thể trích xuất; OCR ảnh chưa được bật."} if not content else {})})
        except HTTPException as exc:
            chapters.append({"title":item.get("title",""),"url":url,"content":"","images":[],"error":str(exc.detail)})
    return {"chapters":chapters,"count":len(chapters)}

async def luotnovel_discover(url):
    from urllib.parse import parse_qs
    found={}
    async with httpx.AsyncClient(timeout=25,follow_redirects=False,headers={"User-Agent":"Mozilla/5.0","Accept-Language":"vi-VN,vi;q=0.9"}) as client:
        soup,base=await get_page(client,url)
        title=(soup.select_one("meta[property='og:title']") or {}).get("content","") or (soup.h1.get_text(" ",strip=True) if soup.h1 else "")
        story_path=urlparse(base).path
        def collect(doc,page_url):
            for a in doc.select("a[href]"):
                link=normalized_url(page_url,a.get("href",""))
                if not link or urlparse(link).path.rstrip("/")==story_path.rstrip("/"):continue
                if (urlparse(link).hostname or "").lower()!=(urlparse(base).hostname or "").lower():continue
                label=a.get_text(" ",strip=True)
                path=urlparse(link).path
                if not (re.search(r"(?:chuong|chapter|chap)[-_ /]?\d+|/\d+(?:/|$)",path,re.I) or re.search(r"(?:chương|chuong|chapter)\s*\d+",label,re.I)):continue
                found[link]={"title":label[:180] or "Chương "+str(len(found)+1),"url":link}
        collect(soup,base)
        pages=page_links(soup,base,story_path)
        for link in pages[:30]:
            try:
                sub,_=await get_page(client,link)
                collect(sub,link)
            except (HTTPException,httpx.HTTPError):pass
    if len(found)<3:
        # LuotNovel loads its table of contents asynchronously into #nt_listchapter.
        try:
            async with await _render_browser() as playwright:
                browser=await playwright.chromium.launch(headless=True,args=["--no-sandbox","--disable-dev-shm-usage"])
                try:
                    page=await browser.new_page(locale="vi-VN")
                    await page.goto(url,wait_until="domcontentloaded",timeout=35000)
                    try:
                        await page.locator("#nt_listchapter a[href]").first.wait_for(timeout=18000)
                    except Exception:
                        pass
                    for _ in range(5):
                        await page.evaluate("window.scrollTo(0,document.body.scrollHeight)")
                        await page.wait_for_timeout(700)
                    rendered_html=await page.content()
                    rendered_soup=BeautifulSoup(rendered_html,"html.parser")
                    collect(rendered_soup,url)
                finally:
                    await browser.close()
        except Exception:
            pass
    if len(found)<3:
        try:
            rendered=await _render_page(url)
            for item in rendered.get("links",[]):
                link=normalized_url(url,item.get("url",""))
                if link:found[link]={"title":item.get("title") or "Chương "+str(len(found)+1),"url":link}
        except HTTPException:
            pass
    return {"title":title,"author":"","chapters":list(found.values()),"count":len(found)}
