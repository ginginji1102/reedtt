const allowedHost=host=>host==='giatocvuongtai.com'||host.endsWith('.giatocvuongtai.com');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function readRenderedPage(){
 const heading=document.querySelector('h1')?.textContent?.trim()||document.querySelector("meta[property='og:title']")?.content||'';
 const links=[...document.querySelectorAll('a[href]')].map(a=>({url:a.href,title:(a.querySelector('strong,p')?.textContent||a.textContent||'').trim()})).filter(a=>{try{const u=new URL(a.url);return u.pathname.replace(/\/$/,'')==='/reader'&&u.searchParams.has('storyId')&&u.searchParams.has('chapter')}catch{return false}});
 const body=document.querySelector('.reader-demo-body,.reader-content,.chapter-content,#chapter-content,[data-chapter-content]');
 const paras=body?[...body.querySelectorAll('.chapter-paragraph')].map(p=>p.textContent.trim()).filter(Boolean):[];
 const fallback=body&&!paras.length?[...body.querySelectorAll('p.reader-demo-para,p')].map(p=>p.textContent.trim()).filter(Boolean):[];
 return {title:heading,links,content:(paras.length?paras:fallback).join('\n\n'),ready:!!body};
}
async function readTab(url){
 const tab=await chrome.tabs.create({url,active:false});
 try{
  let latest=null,stable=0,lastLength=0;
  for(let i=0;i<48;i++){
   await sleep(650);
   const info=await chrome.tabs.get(tab.id).catch(()=>null);
   if(!info)throw Error('Tab nguồn đã bị đóng.');
   if(info.url&&!allowedHost(new URL(info.url).hostname))throw Error('Trang đã chuyển hướng ra ngoài nguồn được hỗ trợ.');
   if(info.status!=='complete')continue;
   try{
    if(i>3&&i%5===0){
     await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>{
      const doc=document.scrollingElement||document.documentElement;
      doc.scrollTop=Math.min(doc.scrollHeight,doc.scrollTop+Math.max(400,innerHeight*1.5));
      window.dispatchEvent(new Event('scroll'));
     }});
    }
    const results=await chrome.scripting.executeScript({target:{tabId:tab.id},func:readRenderedPage});
    latest=results[0]?.result;
    if(latest?.content){
     const length=latest.content.length;
     stable=length===lastLength?stable+1:0;
     lastLength=length;
     if(stable>=5)return latest;
    }else if(latest?.links?.length&&i>=9)return latest;
   }catch(e){if(i>12)throw e}
  }
  if(latest?.content||latest?.links?.length)return latest;
  throw Error('Trang đã mở nhưng chưa hiển thị mục lục hoặc văn bản chương.');
 }finally{await chrome.tabs.remove(tab.id).catch(()=>{});}
}
chrome.runtime.onMessage.addListener((msg,sender,sendResponse)=>{
 if(msg?.type!=='readRendered'||!sender.url?.startsWith('https://yuu110299.github.io/NovelBox/'))return;
 let url;
 try{url=new URL(msg.url);if(url.protocol!=='https:'||!allowedHost(url.hostname))throw Error('Nguồn không được hỗ trợ.')}catch(e){sendResponse({ok:false,error:e.message});return}
 (async()=>{try{sendResponse({ok:true,data:await readTab(url.href)})}catch(e){sendResponse({ok:false,error:e.message||String(e)})}})();
 return true;
});