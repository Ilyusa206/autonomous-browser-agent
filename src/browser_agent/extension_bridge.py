from __future__ import annotations

from flask import Flask, jsonify, request
from browser_agent.agent import TOOL_SCHEMAS
from browser_agent.provider import GroqProvider

app = Flask(__name__)
provider: GroqProvider | None = None

def get_provider() -> GroqProvider:
    global provider
    if provider is None:
        provider = GroqProvider()
    return provider

@app.post("/api/plan")
def plan():
    data = request.get_json(force=True)
    return jsonify(get_provider().create_plan(task=str(data.get("task","")), observation=str(data.get("observation",""))))

@app.post("/api/decision")
def decision():
    data = request.get_json(force=True)
    p = get_provider()
    task, observation = str(data.get("task","")), str(data.get("observation",""))
    plan_data = data.get("plan") or {}
    plan_text = "PLAN:\n" + "\n".join(f"- {x}" for x in plan_data.get("steps", []))
    criteria = "\nSUCCESS CRITERIA:\n" + "\n".join(f"- {x}" for x in plan_data.get("success_criteria", []))
    last = str(data.get("last_action","(none)"))
    result = p.decide(task=task, observation=observation, history=[{"role":"user","content":plan_text+criteria+"\nLAST ACTION: "+last}], tools=TOOL_SCHEMAS)
    verification = None
    if result.kind == "finish":
        verification = p.verify_goal(task=task, observation=observation, candidate_answer=result.text or "", history_summary=last)
        if not verification.complete:
            feedback = "VERIFIER: NOT COMPLETE. Missing: " + "; ".join(verification.missing) + ". Continue with a tool action."
            result = p.decide(task=task, observation=observation, history=[{"role":"user","content":plan_text+criteria+"\n"+feedback}], tools=TOOL_SCHEMAS)
    return jsonify({"kind":result.kind,"name":result.name,"arguments":result.arguments or {},"text":result.text or "","verification":None if verification is None else {"complete":verification.complete,"summary":verification.summary,"missing":verification.missing}})

@app.get("/health")
def health():
    return jsonify({"ok":True,"mode":"extension-bridge","architecture":"planner+executor+verifier"})

def main() -> None:
    print("[bridge] Browser Agent: http://127.0.0.1:8766")
    print("[bridge] Planner + Executor + Verifier enabled.")
    app.run(host="127.0.0.1",port=8766,debug=False,threaded=True)

if __name__ == "__main__":
    main()
