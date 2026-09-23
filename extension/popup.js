const task=document.querySelector("#task"),err=document.querySelector("#err");
document.querySelector("#run").onclick=async()=>{
  const value=task.value.trim();
  if(!value){err.textContent="Введите задачу.";return;}
  err.textContent="Открываю Browser Agent…";
  try{
    const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
    await chrome.storage.local.set({pendingTask:value,targetTabId:tab?.id||null});
    const created=await chrome.windows.create({
      url:chrome.runtime.getURL("agent.html"),
      type:"popup",
      width:430,
      height:720,
      focused:true
    });
    if(!created) throw new Error("Opera не создала окно");
    window.close();
  }catch(e){
    console.error(e);
    err.textContent="Не удалось открыть mini-app: "+(e?.message||String(e));
  }
};