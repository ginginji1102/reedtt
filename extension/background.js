const allowedHost=host=>host==='giatocvuongtai.com'||host.endsWith('.giatocvuongtai.com');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function readRenderedPage(){
 const heading=document.querySelector('h1')?.textContent?.trim()||document.querySelector("meta[property='og:title']")?.content||'';
 const links=[...document.querySelectorAll('a[href]')].map(a=>({url:a.href,title:(a.querySelector('strong,p')?.textContent||a.textContent||'').trim()})).filter(a=>{try{const u=new URL(a.url);return u.pathname.replace(/\\/$/,'')==='/reader'&&u.searchParams.has('storyId')&&u.searchParams.has('chapter')}catch{return false}});
 const body=document.querySelector('.reader-demo-body');
 const paras=body?[...body.querySelectorAll('.chapter-paragraph')].map(p=>p.textContent.trim()).filter(Boolean):[];
 const fallback=body&&!paras.length?[...body.querySelectorAll('p.reader-demo-para')].map(p=>p.textContent.trim()).filter(Boolean):[];
 return {title:heading,links,content:(paras.length?paras:fallback).join('\\n\\n'),ready:!!body};
}
async function readTab(url){
 const tab=await chrome.tabs.create({url,active:false});
 try{
  let latest=null;
  for(let i=0;i<32;i++){
   await sleep(750);
   const info=await chrome.tabs.get(tab.id).catch(()=>null);
   if(!info)throw Error('Tab nguồn đã bị đóng.');
   if(info.url&&!allowedHost(new URL(info.url).hostname))throw Error('Trang đã chuyển hướng ra ngoài nguồn được hỗ trợ.');
   if(info.status!=='complete')continue;
   try{
    const results=await chrome.scripting.executeScript({target:{tabId:tab.id},func:readRenderedPage});
    latest=results[0]?.result;
    if(latest?.content||latest?.links?.length)return latest;
   }catch(e){if(i>8)throw e}
  }
  if(latest)return latest;
  throw Error('Trang nguồn chưa tải xong.');
 }finally{await chrome.tabs.remove(tab.id).catch(()=>{});}
}
chrome.runtime.onMessage.addListener((msg,sender,sendResponse)=>{
 if(msg?.type!=='readRendered'||!sender.url?.startsWith('https://yuu110299.github.io/NovelBox/'))return;
 let url;
 try{url=new URL(msg.url);if(url.protocol!=='https:'||!allowedHost(url.hostname))throw Error('Nguồn không được hỗ trợ.')}catch(e){sendResponse({ok:false,error:e.message});return}
 (async()=>{try{sendResponse({ok:true,data:await readTab(url.href)})}catch(e){sendResponse({ok:false,error:e.message||String(e)})}})();
 return true;
});