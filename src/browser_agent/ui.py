from __future__ import annotations

import queue
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

from browser_agent.agent import AutonomousAgent
from browser_agent.browser import BrowserController
from browser_agent.provider import GroqProvider

app = Flask(__name__)
events: queue.Queue[dict[str, str]] = queue.Queue()
state = {"running": False, "status": "Ready", "result": ""}
approval_event = threading.Event()
approval_value = {"allowed": False}


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Browser Agent</title>
<style>
:root{color-scheme:dark;--bg:#090b10;--panel:#11151d;--muted:#8b93a7;--line:#232a38;--text:#eef2ff;--accent:#7c8cff;--ok:#5bd6a2;--warn:#ffcb6b}
*{box-sizing:border-box}body{margin:0;font:15px Inter,ui-sans-serif,system-ui;background:radial-gradient(circle at 20% 0,#151a2b 0,#090b10 42%);color:var(--text)}
main{max-width:980px;margin:0 auto;padding:42px 28px}.brand{display:flex;align-items:center;gap:12px;margin-bottom:28px}.orb{width:34px;height:34px;border-radius:12px;background:linear-gradient(135deg,#9b8cff,#526dff);box-shadow:0 0 34px #6376ff55}.brand b{font-size:20px}.brand span{color:var(--muted)}
.card{background:#11151de8;border:1px solid var(--line);border-radius:20px;padding:22px;box-shadow:0 20px 60px #0007}.status{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}.pill{padding:7px 11px;border-radius:999px;background:#192033;color:#b9c4ff;font-size:12px}
textarea{width:100%;min-height:110px;resize:vertical;border:1px solid var(--line);border-radius:14px;background:#0b0e14;color:var(--text);padding:15px;font:inherit;outline:none}textarea:focus{border-color:#6576e8}
.row{display:flex;gap:10px;margin-top:12px}input{flex:1;border:1px solid var(--line);border-radius:12px;background:#0b0e14;color:var(--text);padding:12px}
button{border:0;border-radius:12px;padding:12px 18px;font-weight:700;cursor:pointer;background:var(--accent);color:white}button.secondary{background:#242b3a}.timeline{margin-top:18px;display:grid;gap:9px}.event{padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:#0d1118;color:#cbd2e3}.event.tool{border-left:3px solid var(--accent)}.event.finish{border-left:3px solid var(--ok)}.event.safety,.event.recovery{border-left:3px solid var(--warn)}
.approval{display:none;margin-top:14px;padding:16px;border:1px solid #765f2d;border-radius:14px;background:#211b10}.approval.show{display:block}.small{font-size:12px;color:var(--muted);margin-top:7px}
</style>
</head><body><main>
<div class="brand"><div class="orb"></div><div><b>Browser Agent</b><br><span>Autonomous Playwright control panel</span></div></div>
<section class="card">
<div class="status"><strong>Task</strong><span id="status" class="pill">Ready</span></div>
<textarea id="task" placeholder="What should I do in the browser?"></textarea>
<div class="row"><input id="url" value="https://www.python.org" aria-label="Start URL"><button id="run">Run agent</button></div>
<div class="small">Visible Chromium · persistent local profile · generic tools · safety confirmation</div>
<div id="approval" class="approval"><strong>Confirmation required</strong><p id="reason"></p><div class="row"><button class="secondary" onclick="approve(false)">Cancel</button><button onclick="approve(true)">Allow once</button></div></div>
<div id="timeline" class="timeline"></div>
</section></main>
<script>
let cursor=0;
const esc=s=>{const d=document.createElement('div');d.textContent=s;return d.innerHTML}
document.querySelector('#run').onclick=async()=>{
 document.querySelector('#timeline').innerHTML=''; cursor=0;
 const body={task:document.querySelector('#task').value,url:document.querySelector('#url').value};
 const r=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 if(!r.ok) alert((await r.json()).error);
};
async function approve(allowed){await fetch('/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({allowed})});document.querySelector('#approval').classList.remove('show')}
async function poll(){
 try{
  const r=await fetch('/events?cursor='+cursor); const data=await r.json(); cursor=data.cursor;
  document.querySelector('#status').textContent=data.status;
  for(const e of data.events){
   const div=document.createElement('div');div.className='event '+e.kind;div.innerHTML=esc(e.message);document.querySelector('#timeline').appendChild(div);
   if(e.kind==='safety'){document.querySelector('#reason').textContent=e.message;document.querySelector('#approval').classList.add('show')}
  }
 }catch(e){}
 setTimeout(poll,500)
} poll();
</script></body></html>"""

event_log: list[dict[str, str]] = []


def emit(kind: str, message: str) -> None:
    event_log.append({"kind": kind, "message": message})
    state["status"] = {
        "thinking": "Thinking", "tool": "Acting", "result": "Observing",
        "recovery": "Recovering", "safety": "Approval needed", "finish": "Done",
    }.get(kind, state["status"])


def confirm(reason: str) -> bool:
    approval_value["allowed"] = False
    approval_event.clear()
    approval_event.wait()
    return approval_value["allowed"]


@app.get("/")
def index():
    return render_template_string(HTML)


@app.get("/events")
def get_events():
    cursor = max(0, int(request.args.get("cursor", 0)))
    return jsonify({"events": event_log[cursor:], "cursor": len(event_log), "status": state["status"]})


@app.post("/approve")
def approve():
    approval_value["allowed"] = bool((request.get_json() or {}).get("allowed"))
    approval_event.set()
    return jsonify({"ok": True})


@app.post("/run")
def run_task():
    if state["running"]:
        return jsonify({"error": "Agent is already running"}), 409
    data = request.get_json() or {}
    task = str(data.get("task", "")).strip()
    url = str(data.get("url", "about:blank")).strip() or "about:blank"
    if not task:
        return jsonify({"error": "Task is required"}), 400

    event_log.clear()
    state.update(running=True, status="Starting", result="")

    def worker() -> None:
        controller = BrowserController(Path(".browser-profile"), url)
        try:
            page = controller.start()
            emit("result", f"Browser ready: {page.url}")
            result = AutonomousAgent(
                page, GroqProvider(), max_steps=12,
                event_sink=emit, confirm_callback=confirm,
            ).run(task)
            state["result"] = result.message
        except Exception as exc:
            emit("recovery", f"Agent stopped: {exc}")
        finally:
            state["running"] = False
            if state["status"] != "Done":
                state["status"] = "Ready"
            controller.close()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True})


def main() -> None:
    url = "http://127.0.0.1:8765"
    threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=8765, debug=False, threaded=True)


if __name__ == "__main__":
    main()
