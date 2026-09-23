chrome.runtime.onInstalled.addListener(()=>{chrome.sidePanel?.setPanelBehavior({openPanelOnActionClick:false}).catch(()=>{});});
chrome.runtime.onMessage.addListener((message,_sender,sendResponse)=>{
 if(message.type==="OPEN_AGENT"){
  const windowId=message.windowId;
  chrome.sidePanel.open({windowId}).then(()=>sendResponse({ok:true})).catch(e=>sendResponse({ok:false,error:String(e)}));
  return true;
 }
 if(!["AGENT_DECIDE","AGENT_PLAN"].includes(message.type))return;
 fetch(message.type==="AGENT_PLAN"?"http://127.0.0.1:8766/api/plan":"http://127.0.0.1:8766/api/decision",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(message.payload)})
  .then(async x=>{const data=await x.json();if(!x.ok)throw Error(data.error||"Agent bridge error");sendResponse({ok:true,data})})
  .catch(e=>sendResponse({ok:false,error:String(e)}));
 return true;
});