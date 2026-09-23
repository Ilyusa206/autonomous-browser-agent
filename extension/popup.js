const task=document.querySelector("#task"),err=document.querySelector("#err");
document.querySelector("#run").onclick=async()=>{
 const value=task.value.trim();if(!value){err.textContent="Введите задачу.";return;}
 try{
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  await chrome.storage.local.set({pendingTask:value,targetTabId:tab?.id||null});
  const r=await chrome.runtime.sendMessage({type:"OPEN_AGENT",windowId:tab?.windowId});
  if(!r?.ok)throw Error(r?.error||"Side panel unavailable");
  window.close();
 }catch(e){err.textContent="Ошибка: "+(e?.message||String(e));}
};