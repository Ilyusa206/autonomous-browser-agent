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
state = {"running": False, "status": "Готов", "result": ""}
approval_event = threading.Event()
approval_value = {"allowed": False}
user_input_event = threading.Event()
user_input_value = {"answer": ""}


HTML = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Браузерный AI-агент</title>
<style>
:root{color-scheme:dark;--bg:#090b10;--panel:#11151d;--muted:#8b93a7;--line:#232a38;--text:#eef2ff;--accent:#7c8cff;--ok:#5bd6a2;--warn:#ffcb6b}
*{box-sizing:border-box}body{margin:0;font:15px Inter,ui-sans-serif,system-ui;background:radial-gradient(circle at 20% 0,#151a2b 0,#090b10 42%);color:var(--text)}
main{max-width:980px;margin:0 auto;padding:42px 28px}.brand{display:flex;align-items:center;gap:12px;margin-bottom:28px}.orb{width:34px;height:34px;border-radius:12px;background:linear-gradient(135deg,#9b8cff,#526dff);box-shadow:0 0 34px #6376ff55}.brand b{font-size:20px}.brand span{color:var(--muted)}
.card{background:#11151de8;border:1px solid var(--line);border-radius:20px;padding:22px;box-shadow:0 20px 60px #0007}.status{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}.pill{padding:7px 11px;border-radius:999px;background:#192033;color:#b9c4ff;font-size:12px}
textarea{width:100%;min-height:110px;resize:vertical;border:1px solid var(--line);border-radius:14px;background:#0b0e14;color:var(--text);padding:15px;font:inherit;outline:none}textarea:focus{border-color:#6576e8}
.row{display:flex;gap:10px;margin-top:12px}input{flex:1;border:1px solid var(--line);border-radius:12px;background:#0b0e14;color:var(--text);padding:12px}
button{border:0;border-radius:12px;padding:12px 18px;font-weight:700;cursor:pointer;background:var(--accent);color:white}button.secondary{background:#242b3a}.timeline{margin-top:18px;display:grid;gap:9px}.event{padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:#0d1118;color:#cbd2e3}.event.tool{border-left:3px solid var(--accent)}.event.finish{border-left:3px solid var(--ok)}.event.safety,.event.recovery{border-left:3px solid var(--warn)}
.approval,.userprompt{display:none;margin-top:14px;padding:16px;border:1px solid #765f2d;border-radius:14px;background:#211b10}.approval.show,.userprompt.show{display:block}.small{font-size:12px;color:var(--muted);margin-top:7px}.event .meta{font-size:11px;color:var(--muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.06em}.answer{margin-top:18px;padding:18px;border:1px solid #294c40;border-radius:14px;background:#0d1916;display:none}.answer.show{display:block}.answer b{color:var(--ok)}details{margin-top:16px;color:var(--muted)}summary{cursor:pointer}
</style>
</head><body><main>
<div class="brand"><div class="orb"></div><div><b>Браузерный AI-агент</b><br><span>Автономное управление браузером</span></div></div>
<section class="card">
<div class="status"><strong>Задача</strong><span id="status" class="pill">Готов</span></div>
<textarea id="task" placeholder="Что нужно сделать в браузере?"></textarea>
<div class="row"><input id="url" value="about:blank" aria-label="Стартовый URL"><button id="run">Запустить</button></div>
<div class="small">Стартовый URL необязателен · видимый Chromium · постоянная сессия · универсальные инструменты · подтверждение опасных действий</div>
<div id="approval" class="approval"><strong>Нужно подтверждение</strong><p id="reason"></p><div class="row"><button class="secondary" onclick="approve(false)">Отмена</button><button onclick="approve(true)">Разрешить один раз</button></div></div>
<div id="userprompt" class="userprompt approval"><strong>Нужна ваша помощь</strong><p id="question"></p><div class="row"><input id="useranswer" placeholder="Ответьте здесь или выполните действие вручную в браузере"><button onclick="answerUser()">Продолжить</button></div><div class="small">Если агент просит решить CAPTCHA, войти в аккаунт или дать разрешение браузеру — сделайте это в открытом Chromium и нажмите «Продолжить».</div></div>
<div id="answer" class="answer"><b>Результат</b><p id="answerText"></p></div><div id="timeline" class="timeline"></div>
</section></main>
<script>
let cursor=0;
const esc=s=>{const d=document.createElement('div');d.textContent=s;return d.innerHTML}
document.querySelector('#run').onclick=async()=>{
 document.querySelector('#timeline').innerHTML=''; document.querySelector('#answer').classList.remove('show'); cursor=0;
 const body={task:document.querySelector('#task').value,url:document.querySelector('#url').value};
 const r=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 if(!r.ok) alert((await r.json()).error);
};
async function answerUser(){const answer=document.querySelector('#useranswer').value;await fetch('/answer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answer})});document.querySelector('#useranswer').value='';document.querySelector('#userprompt').classList.remove('show')}
async function approve(allowed){await fetch('/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({allowed})});document.querySelector('#approval').classList.remove('show')}
async function poll(){
 try{
  const r=await fetch('/events?cursor='+cursor); const data=await r.json(); cursor=data.cursor;
  document.querySelector('#status').textContent=data.status;
  for(const e of data.events){
   const labels={thinking:'Анализ',tool:'Действие',result:'Браузер',recovery:'Восстановление',safety:'Безопасность',waiting:'Ожидание',user_input:'Нужна ваша помощь',user_answer:'Продолжение',finish:'Готово'};
   const clean=e.message.replace(/^\[(agent|recovery|safety|provider)\]\s*/,'').replace(/^step (\d+)\/(\d+): asking model$/,'Шаг $1 из $2 · анализ страницы').replace(/^tool: click \{'ref': '([^']+)'\}$/,'Нажатие на элемент $1').replace(/^tool: type \{'ref': '([^']+)', 'text': '([^']+)'\}$/,'Ввод «$2» в элемент $1').replace(/^tool: scroll \{'amount': (-?\d+)\}$/,'Прокрутка страницы на $1 px').replace(/^result: ok=True /,'').replace(/^finish: /,'').replace(/^Navigated to /,'Перешёл на ').replace(/^Clicked (e\d+)$/,'Нажал на $1').replace(/^Typed into (e\d+)$/,'Ввёл текст в $1').replace(/^Scrolled by (-?\d+)px$/,'Прокрутил страницу на $1 px').replace(/^Waited (\d+)ms$/,'Подождал $1 мс').replace(/^Went back$/,'Вернулся на предыдущую страницу').replace(/^Read current page$/,'Прочитал текущую страницу').replace(/^tool: navigate \{'url': '([^']+)'\}$/,'Переход на $1').replace(/^tool: wait \{'milliseconds': (\d+)\}$/,'Ожидание $1 мс').replace(/^tool: back \{\}$/,'Возврат назад');
   const div=document.createElement('div');div.className='event '+e.kind;div.innerHTML='<div class="meta">'+esc(labels[e.kind]||e.kind)+'</div>'+esc(clean);document.querySelector('#timeline').appendChild(div);
   if(e.kind==='finish'){document.querySelector('#answerText').textContent=clean;document.querySelector('#answer').classList.add('show')}
   if(e.kind==='safety'){document.querySelector('#reason').textContent=e.message;document.querySelector('#approval').classList.add('show')}
   if(e.kind==='user_input'){document.querySelector('#question').textContent=e.message.replace(/^\[user\]\s*/, '');document.querySelector('#userprompt').classList.add('show');document.querySelector('#useranswer').focus()}
  }
 }catch(e){}
 setTimeout(poll,500)
} poll();
</script></body></html>"""

event_log: list[dict[str, str]] = []


def emit(kind: str, message: str) -> None:
    event_log.append({"kind": kind, "message": message})
    state["status"] = {
        "thinking": "Анализирую", "tool": "Выполняю", "result": "Проверяю",
        "recovery": "Восстанавливаюсь", "safety": "Нужно подтверждение",
        "waiting": "Жду лимит API", "user_input": "Жду вас", "user_answer": "Продолжаю", "finish": "Готово",
    }.get(kind, state["status"])


def ask_user(question: str) -> str:
    user_input_value["answer"] = ""
    user_input_event.clear()
    user_input_event.wait()
    return user_input_value["answer"]


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


@app.post("/answer")
def answer_user():
    user_input_value["answer"] = str((request.get_json() or {}).get("answer", "")).strip()
    user_input_event.set()
    return jsonify({"ok": True})


@app.post("/approve")
def approve():
    approval_value["allowed"] = bool((request.get_json() or {}).get("allowed"))
    approval_event.set()
    return jsonify({"ok": True})


@app.post("/run")
def run_task():
    if state["running"]:
        return jsonify({"error": "Агент уже выполняет задачу"}), 409
    data = request.get_json() or {}
    task = str(data.get("task", "")).strip()
    url = str(data.get("url", "about:blank")).strip() or "about:blank"
    if not task:
        return jsonify({"error": "Введите задачу"}), 400

    event_log.clear()
    state.update(running=True, status="Запускаюсь", result="")

    def worker() -> None:
        controller = BrowserController(Path(".browser-profile"), url)
        try:
            page = controller.start()
            emit("result", f"Браузер готов: {page.url}")
            result = AutonomousAgent(
                page, GroqProvider(event_sink=emit), max_steps=12,
                event_sink=emit, confirm_callback=confirm, ask_user_callback=ask_user,
            ).run(task)
            state["result"] = result.message
        except Exception as exc:
            emit("recovery", f"Агент остановлен: {exc}")
        finally:
            state["running"] = False
            if state["status"] != "Готово":
                state["status"] = "Готов"
            controller.close()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True})


def main() -> None:
    url = "http://127.0.0.1:8765"
    threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=8765, debug=False, threaded=True)


if __name__ == "__main__":
    main()
