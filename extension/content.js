(() => {
  if (window.__browserAgentInstalled) return;
  window.__browserAgentInstalled = true;

  const REF = "data-browser-agent-ref";
  const SELECTOR = 'a,button,input,textarea,select,[role="button"],[role="link"],[role="menuitem"],[role="option"],[role="tab"],[contenteditable="true"]';
  let host, shadow, running = false, cancelled = false, task = "", step = 0;
  const maxSteps = 30;

  function mount() {
    if (host) { host.style.display = host.style.display === "none" ? "block" : "none"; return; }
    host = document.createElement("div");
    host.id = "__browser_agent_host";
    Object.assign(host.style, {position:"fixed",top:"16px",right:"16px",zIndex:"2147483647"});
    shadow = host.attachShadow({mode:"open"});
    shadow.innerHTML = `
      <style>
        *{box-sizing:border-box} .box{width:390px;max-height:calc(100vh - 32px);font:14px/1.45 Inter,system-ui,sans-serif;background:#111318;color:#f5f7fb;border:1px solid #30343d;border-radius:18px;box-shadow:0 24px 70px #0008;overflow:hidden}
        header{display:flex;align-items:center;justify-content:space-between;padding:14px 16px;background:#181b22;border-bottom:1px solid #2b3039} h1{font-size:14px;margin:0}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#62d68b;margin-right:8px}
        main{padding:14px} textarea{width:100%;min-height:92px;resize:vertical;background:#0b0d11;color:#fff;border:1px solid #343944;border-radius:12px;padding:11px;outline:none} textarea:focus{border-color:#737bff}
        .row{display:flex;gap:8px;margin-top:10px}button{border:0;border-radius:10px;padding:9px 13px;font-weight:650;cursor:pointer}.run{background:#737bff;color:white;flex:1}.stop{background:#292d36;color:#ddd}.close{background:transparent;color:#aaa;padding:2px 6px;font-size:18px}
        .status{margin:12px 0 8px;color:#aeb5c2}.log{height:260px;overflow:auto;background:#0b0d11;border:1px solid #252932;border-radius:12px;padding:9px}.event{padding:7px 4px;border-bottom:1px solid #1c2027;word-break:break-word}.tool{color:#8db6ff}.ok{color:#79dfa0}.warn{color:#ffca72}.err{color:#ff8585}
        .ask{margin-top:10px;padding:10px;border:1px solid #574d25;background:#211d0e;border-radius:10px}.ask input{width:100%;margin-top:8px;background:#111;color:#fff;border:1px solid #555;border-radius:8px;padding:8px}
      </style>
      <div class="box"><header><h1><span class="dot"></span>Browser Agent · this tab</h1><button class="close">×</button></header><main>
      <textarea id="task" placeholder="Что нужно сделать в браузере?"></textarea><div class="row"><button class="run">Запустить агента</button><button class="stop">Стоп</button></div>
      <div class="status">Готов. Используется текущая авторизованная вкладка.</div><div class="log"></div><div id="human"></div></main></div>`;
    document.documentElement.appendChild(host);
    shadow.querySelector(".close").onclick=()=>host.style.display="none";
    shadow.querySelector(".stop").onclick=()=>{cancelled=true; running=false; setStatus("Остановлен пользователем");};
    shadow.querySelector(".run").onclick=start;
  }
  function log(text, cls=""){const el=document.createElement("div");el.className="event "+cls;el.textContent=text;shadow.querySelector(".log").appendChild(el);el.scrollIntoView(); }
  function setStatus(text){shadow.querySelector(".status").textContent=text;}
  function visible(el){const r=el.getBoundingClientRect(),s=getComputedStyle(el);return s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0;}
  function observe(){
    document.querySelectorAll("["+REF+"]").forEach(e=>e.removeAttribute(REF));
    const els=[...document.querySelectorAll(SELECTOR)].filter(visible).slice(0,80);
    const elements=els.map((el,i)=>{const ref="e"+(i+1);el.setAttribute(REF,ref);return {ref,tag:el.tagName.toLowerCase(),role:el.getAttribute("role")||"",type:el.getAttribute("type")||"",text:(el.innerText||el.value||"").replace(/\s+/g," ").trim().slice(0,180),name:el.getAttribute("aria-label")||el.getAttribute("name")||el.getAttribute("title")||"",placeholder:el.getAttribute("placeholder")||"",href:el.tagName==="A"?(el.href||"").slice(0,220):""};});
    return {url:location.href,title:document.title,text:(document.body?.innerText||"").replace(/\s+/g," ").trim().slice(0,4200),elements};
  }
  function renderObs(o){return ["URL: "+o.url,"TITLE: "+o.title,"","INTERACTIVE ELEMENTS:",...o.elements.map(e=>`[${e.ref}] ${Object.entries(e).filter(([k,v])=>k!=="ref"&&v).map(([k,v])=>k+"="+JSON.stringify(v)).join(" | ")}`),"","VISIBLE TEXT:",o.text].join("\n");}
  function el(ref){const x=document.querySelector(`[${REF}="${CSS.escape(ref)}"]`);if(!x)throw new Error("Stale/missing ref "+ref);return x;}
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  async function human(question, confirmOnly=false){
    return new Promise(resolve=>{const box=shadow.querySelector("#human");box.innerHTML=`<div class="ask"><div></div><input placeholder="Ответ (если нужен)"><div class="row"><button class="run">Продолжить</button><button class="stop">Отмена</button></div></div>`;box.querySelector("div div").textContent=question;box.querySelector(".run").onclick=()=>{const v=box.querySelector("input").value;box.innerHTML="";resolve(confirmOnly?"yes":v)};box.querySelector(".stop").onclick=()=>{box.innerHTML="";resolve("")};});
  }
  function risky(node){const t=[node.innerText,node.value,node.getAttribute("aria-label"),node.href].filter(Boolean).join(" ").toLowerCase();return ["delete","remove","pay","purchase","buy now","place order","send money","удалить","оплатить","купить","оформить заказ","перевести"].some(x=>t.includes(x));}
  async function act(name,a){
    if(name==="navigate"){location.href=a.url;return "Navigating";}
    if(name==="back"){history.back();return "Went back";}
    if(name==="scroll"){scrollBy({top:a.amount||700,behavior:"smooth"});await sleep(500);return "Scrolled";}
    if(name==="wait"){await sleep(Math.min(Math.max(a.milliseconds||1000,100),5000));return "Waited";}
    const node=el(a.ref);
    if(name==="click"){if(risky(node)){const yes=await human("⚠️ Агент хочет выполнить потенциально значимое действие. Разрешить?",true);if(!yes)throw new Error("User denied consequential action");}node.scrollIntoView({block:"center"});node.click();await sleep(700);return "Clicked "+a.ref;}
    if(name==="type"){node.focus();if(a.clear!==false){if("value" in node)node.value="";else node.textContent="";}if("value" in node)node.value=a.text;else node.textContent=a.text;node.dispatchEvent(new InputEvent("input",{bubbles:true,inputType:"insertText",data:a.text}));node.dispatchEvent(new Event("change",{bubbles:true}));if(a.submit)node.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",code:"Enter",bubbles:true}));await sleep(500);return "Typed "+a.ref;}
    if(name==="press"){node.focus();node.dispatchEvent(new KeyboardEvent("keydown",{key:a.key,code:a.key,bubbles:true}));node.dispatchEvent(new KeyboardEvent("keyup",{key:a.key,code:a.key,bubbles:true}));await sleep(400);return "Pressed "+a.key;}
    throw new Error("Unknown tool "+name);
  }
  async function decide(payload){return new Promise((resolve,reject)=>chrome.runtime.sendMessage({type:"AGENT_DECIDE",payload},r=>{if(chrome.runtime.lastError)return reject(new Error(chrome.runtime.lastError.message));if(!r?.ok)return reject(new Error(r?.error||"Bridge unavailable"));resolve(r.data);}));}
  async function start(){
    if(running)return; task=shadow.querySelector("#task").value.trim();if(!task)return;running=true;cancelled=false;step=0;shadow.querySelector(".log").innerHTML="";setStatus("Агент работает…");
    let last="(none yet)";
    try{
      while(running&&!cancelled&&step++<maxSteps){
        const o=observe();log(`Шаг ${step}: анализ страницы`);
        const d=await decide({task,observation:renderObs(o),last_action:last});
        if(d.kind==="finish"){log(d.text||"Готово","ok");setStatus("Завершено");running=false;return;}
        if(d.name==="ask_user"){const answer=await human(d.arguments?.question||"Нужно действие пользователя");last="USER INPUT: "+(answer||"manual action completed");continue;}
        log("→ "+d.name+" "+JSON.stringify(d.arguments||{}),"tool");
        try{last=d.name+" -> "+await act(d.name,d.arguments||{});log("✓ "+last,"ok");}
        catch(e){last=d.name+" failed: "+e.message;log("↻ "+last,"warn");}
        await sleep(350);
      }
      if(running)setStatus("Достигнут лимит шагов");running=false;
    }catch(e){log("Ошибка: "+e.message,"err");setStatus("Ошибка — backend запущен?");running=false;}
  }
  chrome.runtime.onMessage.addListener(m=>{if(m.type==="TOGGLE_AGENT")mount();});
})();
