const task=document.querySelector("#task"),err=document.querySelector("#err");
document.querySelector("#run").onclick=async()=>{
 const value=task.value.trim();
 if(!value){err.textContent="Введите задачу.";return;}
 err.textContent="Запускаю…";
 try{
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  await chrome.storage.local.set({pendingTask:value,targetTabId:tab?.id||null});
  chrome.runtime.sendMessage({type:"OPEN_AGENT"});
 }catch(e){err.textContent="Ошибка: "+(e?.message||String(e));}
};