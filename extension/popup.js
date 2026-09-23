const task=document.querySelector("#task"),err=document.querySelector("#err");
document.querySelector("#run").onclick=async()=>{
 const value=task.value.trim(); if(!value){err.textContent="Введите задачу.";return;}
 try{
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  const url=chrome.runtime.getURL("agent.html")+"?tab="+encodeURIComponent(tab?.id||"")+"&task="+encodeURIComponent(value);
  await chrome.tabs.create({url,active:true});
  window.close();
 }catch(e){err.textContent="Ошибка: "+(e?.message||String(e));}
};