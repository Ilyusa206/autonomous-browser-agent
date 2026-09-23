let agentWindowId=null;
async function openAgentWindow(bounds){
  if(agentWindowId){
    try{await chrome.windows.update(agentWindowId,{focused:true});return agentWindowId}catch(_){agentWindowId=null}
  }
  const width=430,height=720,left=Math.max((bounds?.left||0)+(bounds?.width||1200)-width-24,0),top=Math.max((bounds?.top||0)+70,0);\n  const w=await chrome.windows.create({url:chrome.runtime.getURL("agent.html"),type:"popup",width,height,left,top,focused:true});
  agentWindowId=w.id||null; return agentWindowId;
}
chrome.windows.onRemoved.addListener(id=>{if(id===agentWindowId)agentWindowId=null});
chrome.runtime.onMessage.addListener((message,_sender,sendResponse)=>{
 if(message.type==="OPEN_AGENT"){
  openAgentWindow(message.bounds).then(id=>sendResponse({ok:true,windowId:id})).catch(e=>sendResponse({ok:false,error:String(e)}));
  return true;
 }
 if(!["AGENT_DECIDE","AGENT_PLAN"].includes(message.type))return;
 fetch(message.type==="AGENT_PLAN"?"http://127.0.0.1:8766/api/plan":"http://127.0.0.1:8766/api/decision",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(message.payload)})
  .then(async x=>{const data=await x.json();if(!x.ok)throw Error(data.error||"Agent bridge error");sendResponse({ok:true,data})})
  .catch(e=>sendResponse({ok:false,error:String(e)}));
 return true;
});