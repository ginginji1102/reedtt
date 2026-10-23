
(function(){
 const key='novel-box-theme';
 const day=document.getElementById('theme-day'),night=document.getElementById('theme-night');
 function setTheme(mode){
  const selected=mode==='day'?'day':'night';
  document.documentElement.setAttribute('data-theme',selected);
  day.setAttribute('aria-pressed',String(selected==='day'));
  night.setAttribute('aria-pressed',String(selected==='night'));
  try{localStorage.setItem(key,selected)}catch(e){}
 }
 let saved='night';try{saved=localStorage.getItem(key)||'night'}catch(e){}
 setTheme(saved);
 day.addEventListener('click',()=>setTheme('day'));
 night.addEventListener('click',()=>setTheme('night'));
})();



const $=id=>document.getElementById(id);
const supportedHosts=['truyenfull.live','wattpad.com','wordpress.com','navyteams.com','giatocvuongtai.com','luotnovel1.site'];
function cleanText(node){return (node?.innerText||node?.textContent||'').replace(/\n{3,}/g,'\n\n').trim()}
function safeUrl(value){const u=new URL(value);if(!['https:','http:'].includes(u.protocol))throw Error('Chỉ hỗ trợ đường dẫn HTTP hoặc HTTPS.');return u}

const API='https://novelbox-reader-api.onrender.com';
let discovered=[];
let downloaded=[];
let storyMeta={};
$('url').addEventListener('input',()=>{try{const host=new URL($('url').value).hostname.replace(/^www\./,'').toLowerCase();const match=[...$('source').options].find(o=>o.value!=='auto'&&(host===o.value||host.endsWith('.'+o.value)));$('source').value=match?match.value:'auto'}catch{$('source').value='auto'}});
async function api(path,body){
 let r;
 try{
  r=await fetch(API+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(120000)});
 }catch(e){
  if(e?.name==='TimeoutError'||e?.name==='AbortError')throw Error('Máy chủ xử lý truyện phản hồi quá lâu (quá 120 giây). Vui lòng thử lại sau.');
  throw Error('Không kết nối được máy chủ xử lý truyện (Render). Hãy kiểm tra máy chủ đang chạy và cấu hình CORS; đây chưa phải lỗi đọc chương của nguồn truyện.');
 }
 const data=await r.json().catch(()=>({}));
 if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'HTTP '+r.status);
 return data;
}
const bridgePending=new Map();
window.addEventListener('message',event=>{
 if(event.source!==window||event.origin!==location.origin||event.data?.novelboxBridge!=='response')return;
 const pending=bridgePending.get(event.data.id);if(!pending)return;
 bridgePending.delete(event.data.id);clearTimeout(pending.timer);
 event.data.ok?pending.resolve(event.data.data):pending.reject(Error(event.data.error||'Tiện ích không đọc được trang.'));
});
function renderedPage(url){
 return new Promise((resolve,reject)=>{
  const id='novelbox-'+crypto.randomUUID();
  const timer=setTimeout(()=>{bridgePending.delete(id);reject(Error('Tiện ích không phản hồi. Hãy cài hoặc tải lại tiện ích Novel Box phiên bản 1.1.0.'));},45000);
  bridgePending.set(id,{resolve,reject,timer});
  window.postMessage({novelboxBridge:'request',id,url},location.origin);
 });
}
async function browserDiscover(url){
 const page=await renderedPage(url);
 const chapters=[],seen=new Set(),storyId=new URL(url).searchParams.get('storyId');
 for(const item of page.links||[]){
  const href=new URL(item.url,url);
  if(storyId&&href.searchParams.get('storyId')!==storyId)continue;
  if(seen.has(href.href))continue;
  seen.add(href.href);
  chapters.push({title:item.title||'Chương '+(chapters.length+1),url:href.href});
 }
 if(!chapters.length){
  const direct=new URL(url);
  if(direct.pathname.replace(/\/$/,'')==='/reader'&&direct.searchParams.has('chapter')){
   chapters.push({title:page.title||'Chương đã chọn',url:direct.href});
  }else throw Error('Đã mở trang nhưng không thấy liên kết chương. Hãy kiểm tra truyện có mục lục công khai hay không.');
 }
 return {title:page.title||'',chapters,count:chapters.length};
}
async function browserChapter(url){
 const page=await renderedPage(url);
 if(!page.content?.trim())throw Error('Trang đã mở nhưng không có nội dung chương công khai trong .reader-demo-body.');
 return page.content;
}
async function loadStory(){
 const status=$('urlstatus'),btn=$('check');
 let u;try{u=safeUrl($('url').value.trim());const src=$('source').value;if(src!=='auto'&&!(u.hostname===src||u.hostname.endsWith('.'+src)))throw Error('Nguồn đã chọn không khớp với link.')}catch(e){status.textContent=e.message||'Nhập URL HTTPS hợp lệ.';return}
 btn.disabled=true;status.textContent='Đang rà mục lục và các trang danh sách chương...';
 try{
  let data;
  if(u.hostname==='giatocvuongtai.com'||u.hostname.endsWith('.giatocvuongtai.com')){
   status.textContent='Đang mở trang bằng Playwright trên Render...';
   try{data=await api('/render-discover',{url:u.href})}
   catch(e){
    if(e instanceof TypeError||/Failed to fetch|NetworkError|Load failed/i.test(e.message||''))throw Error('Trình duyệt không được phép đọc trực tiếp nguồn này (có thể do CORS). Cần tiện ích trình duyệt có quyền truy cập trang nguồn. Không thể giải quyết bằng Render.');
    throw e;
   }
  }else data=await api('/discover',{url:u.href});
  discovered=data.chapters||[];
  storyMeta={title:data.title||"",author:data.author||"",description:data.description||"",categories:data.categories||[],cover:data.cover||""};
  const meta=$("storymeta");meta.replaceChildren();
  const layout=document.createElement("div");layout.className="story-meta-layout";
  const details=document.createElement("div");details.className="story-meta-details";
  if(storyMeta.cover){const img=document.createElement("img");img.src=storyMeta.cover;img.alt="Ảnh bìa truyện";img.className="story-meta-cover";img.referrerPolicy="no-referrer";layout.append(img)}
  for(const [name,val] of [["Tên truyện",storyMeta.title],["Tác giả",storyMeta.author],["Thể loại",storyMeta.categories.join(", ")],["Mô tả",storyMeta.description]]){if(val){const p=document.createElement("p");const b=document.createElement("strong");b.textContent=name+": ";p.append(b,document.createTextNode(val));if(name==="Mô tả"&&val.length>450){p.style.maxHeight="9em";p.style.overflow="hidden";const toggle=document.createElement("button");toggle.type="button";toggle.className="alt";toggle.textContent="Xem thêm";toggle.style.marginBottom="12px";toggle.onclick=()=>{const expanded=p.style.maxHeight==="none";p.style.maxHeight=expanded?"9em":"none";toggle.textContent=expanded?"Xem thêm":"Thu gọn";toggle.setAttribute("aria-expanded",String(!expanded))};toggle.setAttribute("aria-expanded","false");details.append(p,toggle)}else details.append(p)}}
  layout.append(details);meta.append(layout);
  downloaded=[];
  if(data.title)$('title').value=data.title;
  if(data.author)$('author').value=data.author;
  $('end').value=discovered.length||1;
  $('chapterinfo').textContent='Đã tìm thấy '+discovered.length+' chương. '+(data.truncated?'Danh sách có thể chưa đầy đủ. ':'')+'Chọn phạm vi rồi nhấn “Tải nội dung các chương đã chọn”.';
  status.textContent=discovered.length?'Đã tìm được '+discovered.length+' chương. Đang tự tải nội dung...':'Chưa tìm được link chương từ mục lục. Nguồn có thể chặn máy chủ hoặc sử dụng cấu trúc khác.';
  if(discovered.length){
   try{await loadChapters()}catch(downloadError){$('status').textContent='Đã tìm '+discovered.length+' link chương, nhưng tải nội dung thất bại: '+(downloadError.message||downloadError);status.textContent='Đã tìm '+discovered.length+' link chương. Danh sách vẫn được giữ để thử tải lại.'}
  }
 }catch(e){status.textContent='Lỗi tìm danh sách chương: '+e.message}
 finally{btn.disabled=false}
}
let activeDownload=false;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function loadChapters(){
 if(activeDownload)return;
 const btn=$('fetchchapters'),status=$('status');
 const start=Number($('start').value),end=Number($('end').value);
 if(!discovered.length){status.textContent='Hãy tìm danh sách chương trước.';return}
 if(!Number.isInteger(start)||!Number.isInteger(end)||start<1||end<start||end>discovered.length){status.textContent='Phạm vi chương không hợp lệ.';return}
 activeDownload=true;btn.disabled=true;
 const url=$('url').value.trim(),include_images=true;
 const previous=new Map(downloaded.map(c=>[c.url,c]));
 downloaded=[];
 const total=end-start+1;
 let failures=0;
 try{
  const chunkSize=new URL(url).hostname.endsWith('giatocvuongtai.com')?2:10;
  for(let from=start;from<=end;from+=chunkSize){
   const to=Math.min(from+chunkSize-1,end),items=discovered.slice(from-1,to);
   const cached=items.map(item=>previous.get(item.url));
   let result=cached.every(x=>x&&x.content)?cached:null;
   if(!result){
    let lastError;
    for(let attempt=1;attempt<=(new URL(url).hostname.endsWith('giatocvuongtai.com')?2:5);attempt++){
     status.textContent='Đang thu thập chương '+from+'–'+to+' / '+end+' · '+downloaded.length+'/'+total+' chương'+(attempt>1?' · thử lại '+attempt+'/5':'');
     try{
      const data=(new URL(url).hostname.endsWith('giatocvuongtai.com'))
       ? await api('/render-batch',{url,start:from,end:to,chapters:items,include_images})
       : await api('/batch',{url,start:from,end:to,chapters:items,include_images});
      if(!Array.isArray(data.chapters)||data.chapters.length!==items.length)throw Error('Phản hồi chưa đầy đủ');
      result=data.chapters;break;
     }catch(e){
      lastError=e;
      if(attempt<(new URL(url).hostname.endsWith('giatocvuongtai.com')?2:5))await sleep(Math.min(1500*Math.pow(2,attempt-1),12000));
     }
    }
    if(!result){
     failures+=items.length;
     result=items.map(item=>({title:item.title,url:item.url,content:'',images:[],error:lastError?.message||'Không tải được'}));
    }
   }
   const blocked=result.some(x=>!x.content&&/Inkitt.*(?:403|401|429)|Inkitt.*từ chối/i.test(x.error||''));
   if(blocked){
    downloaded.push(...result);
    $('content').value=downloaded.filter(x=>x.content).map(x=>x.title+'\n\n'+x.content).join('\n\n\n');
    status.textContent='Inkitt đang chặn truy cập từ máy chủ (HTTP 403/401/429). Đã dừng tải để tránh thử lại hàng trăm chương. Không thể lấy nội dung khi nguồn từ chối kết nối.';
    return;
   }
   downloaded.push(...result);
   $('content').value=downloaded.map(x=>x.title+'\n\n'+(x.content||'[Chương tải lỗi — xem thông báo trạng thái]')).join('\n\n\n');
   status.textContent='Đã xử lý '+downloaded.length+'/'+total+' chương'+(failures?' · '+failures+' chương cần tải lại':'');
  }
  const failed=downloaded.filter(x=>!x.content);
  if(failed.length && !new URL(url).hostname.endsWith('giatocvuongtai.com') && !failed.some(x=>/Inkitt.*(?:403|401|429)|Inkitt.*từ chối/i.test(x.error||''))){
   status.textContent='Đã quét '+total+' chương; đang thử tải lại '+failed.length+' chương bị lỗi...';
   for(let i=0;i<failed.length;i+=5){
    const items=failed.slice(i,i+5);
    try{
     const data=await api('/batch',{url,chapters:items,include_images});
     for(const chapter of data.chapters||[]){const index=downloaded.findIndex(x=>x.url===chapter.url);if(index>=0&&chapter.content)downloaded[index]=chapter}
    }catch(e){/* Keep the downloaded chapters and continue. */}
   }
   $('content').value=downloaded.map(x=>x.title+'\n\n'+(x.content||'[Chương tải lỗi — xem thông báo trạng thái]')).join('\n\n\n');
  }
  const missing=downloaded.filter(x=>!x.content).length;
  const reasons=[...new Set(downloaded.filter(x=>!x.content).map(x=>x.error||'Không rõ nguyên nhân'))].slice(0,3);status.textContent='Hoàn thành '+(total-missing)+'/'+total+' chương.'+(missing?' Lỗi: '+reasons.join(' · ')+'. Bấm Tải nội dung để thử lại.':' Sẵn sàng xuất file.');
 }finally{activeDownload=false;btn.disabled=false}
}
$('fetchchapters').onclick=loadChapters;
const escapeXml=s=>s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function cleanSourceMarks(text){
 return String(text||'').replace(/(?:truyenfullvn|truyenfulllive|truyenfull|truyen full)/gi,'').replace(/(?:\s*,){2,}\s*(?:vn\b)?/gi,' ').replace(/(^|[\s,])vn(?=\s*(?:$|[.!?\n]))/gi,'$1').replace(/\s+,/g,',').replace(/,{2,}/g,',').replace(/[ \t]{2,}/g,' ').replace(/ *\n */g,'\n').trim();
}
function sourceParagraphs(text){
 const cleaned=cleanSourceMarks(text).replace(/\r\n?/g,'\n');
 return cleaned.split(/\n+/).map(x=>x.trim()).filter(Boolean);
}
function bookParagraphs(d){
 const out=[];
 if(storyMeta.description){out.push({text:'Giới Thiệu',heading:true});for(const p of sourceParagraphs(storyMeta.description))if(p.trim())out.push({text:p.trim()})}
 if(storyMeta.categories?.length)out.push({text:'Thể loại: '+storyMeta.categories.join(', ')});
 if(downloaded.length){for(const chapter of downloaded.filter(x=>x.content)){out.push({text:chapter.title,heading:true});for(const p of sourceParagraphs(chapter.content||''))if(p.trim())out.push({text:p.trim()})}}
 else for(const p of sourceParagraphs($('content').value))if(p.trim())out.push({text:p.trim()});
 return out;
}
const get=()=>({title:cleanSourceMarks($('title').value.trim()||'Truyện mẫu'),author:cleanSourceMarks($('author').value.trim()),body:[storyMeta.description?'Giới Thiệu\n'+storyMeta.description:'',storyMeta.categories?.length?'Thể loại: '+storyMeta.categories.join(', '):'',$('content').value].filter(Boolean).join('\n\n')});
async function imageBytes(url){
 if(!url)return null;
 let blob;
 try{const r=await fetch(url,{mode:'cors'});if(r.ok)blob=await r.blob()}catch{}
 if(!blob){try{const r=await fetch(API+'/image',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});if(r.ok)blob=await r.blob()}catch{}}
 if(!blob||blob.size>5000000||!/^image\/(png|jpeg|webp)$/.test(blob.type))return null;
 if(blob.type==='image/webp'){try{const bmp=await createImageBitmap(blob),canvas=document.createElement('canvas');canvas.width=bmp.width;canvas.height=bmp.height;canvas.getContext('2d').drawImage(bmp,0,0);blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));bmp.close()}catch{return null}}
 return {bytes:new Uint8Array(await blob.arrayBuffer()),mime:blob.type,ext:blob.type==='image/png'?'png':'jpg'};
}
async function coverBytes(){return await imageBytes(storyMeta.cover)}
async function chapterImages(){
 const result=[];

 for(let i=0;i<downloaded.length;i++){
  for(const url of (downloaded[i].images||[]).slice(0,30)){
   const data=await imageBytes(url);
   if(data)result.push({chapter:i,...data});
  }
 }
 return result;
}
function save(blob,name){const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),10000)}
$('check').onclick=loadStory;
$('download').onclick=async()=>{const d=get();const status=$('status');status.textContent='Đang tạo file...';try{if(!d.body.trim())throw Error('Hãy nhập nội dung trước.');const fmt=document.querySelector('input[name="format"]:checked').value;const name=(d.title.replace(/[^\p{L}\p{N} _-]/gu,'').slice(0,55)||'truyen')+'.'+fmt;const cover=await coverBytes();const inlineImages=await chapterImages();
if(fmt==='docx'){
 if(!window.docx)throw Error('Không tải được thư viện Word.');
 const para=(text,kind='normal',extra={})=>new docx.Paragraph({text,style:kind==='title'?'StoryTitle':kind==='heading'?'Heading2':'Normal',...extra});
 const paragraphs=[para(d.title,'title')];
 if(cover)paragraphs.push(new docx.Paragraph({alignment:docx.AlignmentType.CENTER,spacing:{after:120},children:[new docx.ImageRun({data:cover.bytes,transformation:{width:270,height:400},type:cover.ext})]}));
 if(d.author)paragraphs.push(new docx.Paragraph({alignment:docx.AlignmentType.CENTER,spacing:{after:80},children:[new docx.TextRun({text:'Tác giả: ',bold:true}),new docx.TextRun(d.author)]}));
 if(storyMeta.categories?.length)paragraphs.push(new docx.Paragraph({alignment:docx.AlignmentType.CENTER,spacing:{after:80},children:[new docx.TextRun({text:'Thể loại: ',bold:true}),new docx.TextRun(storyMeta.categories.join(', '))]}));
 if(downloaded.length)paragraphs.push(para('Độ dài: '+downloaded.length+' chương','normal',{alignment:docx.AlignmentType.CENTER}));
 if(storyMeta.description){
  paragraphs.push(para('Giới Thiệu','heading'));
  for(const block of sourceParagraphs(storyMeta.description)){if(block.trim())paragraphs.push(para(block.trim()))}
 }
 for(let i=0;i<downloaded.length;i++){
  const chapter=downloaded[i];
  paragraphs.push(para(chapter.title,'heading'));
  for(const block of sourceParagraphs(chapter.content||'')){if(block.trim())paragraphs.push(para(block.trim()))}
  for(const im of inlineImages.filter(x=>x.chapter===i))paragraphs.push(new docx.Paragraph({alignment:docx.AlignmentType.CENTER,children:[new docx.ImageRun({data:im.bytes,transformation:{width:300,height:300},type:im.ext})]}));
 }
 if(!downloaded.length)for(const block of sourceParagraphs($('content').value))if(block.trim())paragraphs.push(para(block.trim()));
 const doc=new docx.Document({
  styles:{default:{document:{run:{font:'Tahoma',size:22,color:'000000'},paragraph:{spacing:{after:160,line:360}}}},paragraphStyles:[
   {id:'StoryTitle',name:'Story Title',basedOn:'Normal',next:'Normal',run:{font:'Cambria',size:72,bold:true,color:'1F3864'},paragraph:{alignment:docx.AlignmentType.CENTER,spacing:{after:140}}},
   {id:'Heading1',name:'Heading 1',basedOn:'Normal',next:'Normal',run:{font:'Cambria',size:32,bold:true,color:'1F3864'},paragraph:{spacing:{before:240,after:0}}},
   {id:'Heading2',name:'Heading 2',basedOn:'Normal',next:'Normal',run:{font:'Tahoma',size:22,bold:true,color:'000000'},paragraph:{spacing:{before:40,after:0,line:360}}}
  ]},
  sections:[{properties:{page:{size:{width:11906,height:16838},margin:{top:720,right:720,bottom:720,left:720,header:360,footer:360}}},children:paragraphs}]
 });
 save(await docx.Packer.toBlob(doc),name)
}
else if(fmt==='epub'){if(!window.JSZip)throw Error('Không tải được thư viện EPUB.');const zip=new JSZip();zip.file('mimetype','application/epub+zip',{compression:'STORE'});zip.file('META-INF/container.xml','<?xml version="1.0" encoding="UTF-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>');if(cover){zip.file('OEBPS/cover.'+cover.ext,cover.bytes)}inlineImages.forEach((im,i)=>zip.file('OEBPS/image'+i+'.'+im.ext,im.bytes));const paragraphs=bookParagraphs(d);
zip.file('OEBPS/chapter.xhtml','<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>'+escapeXml(d.title)+'<\/title><\/head><body><h1>'+escapeXml(d.title)+'<\/h1>'+(cover?'<div><img src="cover.'+cover.ext+'" alt="Ảnh bìa truyện" style="max-width:85%;height:auto"/><\/div>':'')+paragraphs.map(p=>p.heading?'<h2>'+escapeXml(p.text)+'<\/h2>':'<p>'+escapeXml(p.text)+'<\/p>').join('')+inlineImages.map((im,i)=>'<p><img src="image'+i+'.'+im.ext+'" alt="Minh họa chương" style="max-width:95%;height:auto"/><\/p>').join('')+'<\/body><\/html>');
zip.file('OEBPS/nav.xhtml','<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Mục lục<\/title><\/head><body><nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc"><ol><li><a href="chapter.xhtml">Nội dung<\/a><\/li><\/ol><\/nav><\/body><\/html>');zip.file('OEBPS/content.opf','<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="bookid">urn:uuid:reedtt-demo</dc:identifier><dc:title>'+escapeXml(d.title)+'</dc:title><dc:language>vi</dc:language><dc:creator>'+escapeXml(d.author)+'</dc:creator><\/metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>' +(cover?'<item id="cover" href="cover.'+cover.ext+'" media-type="'+cover.mime+'" properties="cover-image"/>':'') +inlineImages.map((im,i)=>'<item id="img'+i+'" href="image'+i+'.'+im.ext+'" media-type="'+im.mime+'"/>').join('')+'<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/><\/manifest><spine><itemref idref="chapter"/><\/spine><\/package>');save(await zip.generateAsync({type:'blob',mimeType:'application/epub+zip'}),name)}
status.textContent='Đã tạo '+fmt.toUpperCase()+' thành công.'}catch(e){status.textContent='Lỗi: '+e.message}};
