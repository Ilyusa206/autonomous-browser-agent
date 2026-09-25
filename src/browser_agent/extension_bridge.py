from __future__ import annotations

from flask import Flask, jsonify, request

from browser_agent.agent import TOOL_SCHEMAS
from browser_agent.provider import BrowserLLMProvider, get_provider_from_env

app = Flask(__name__)
provider: BrowserLLMProvider | None = None


def get_provider() -> BrowserLLMProvider:
    global provider
    if provider is None:
        provider = get_provider_from_env()
    return provider


@app.errorhandler(Exception)
def handle_error(exc: Exception):
    app.logger.exception("bridge request failed")
    return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


@app.post("/api/plan")
def plan():
    data = request.get_json(force=True) or {}
    task = str(data.get("task", "")).strip()
    observation = str(data.get("observation", ""))
    if not task:
        return jsonify({"error": "task is required"}), 400
    return jsonify(get_provider().create_plan(task=task, observation=observation))


@app.post("/api/decision")
def decision():
    data = request.get_json(force=True) or {}
    p = get_provider()
    task = str(data.get("task", "")).strip()
    observation = str(data.get("observation", ""))
    if not task:
        return jsonify({"error": "task is required"}), 400

    plan_data = data.get("plan") or {}
    plan_text = "PLAN:\n" + "\n".join(f"- {x}" for x in plan_data.get("steps", []))
    criteria = "\nSUCCESS CRITERIA:\n" + "\n".join(
        f"- {x}" for x in plan_data.get("success_criteria", [])
    )
    last = str(data.get("last_action", "(none)"))
    context = plan_text + criteria + "\nLAST ACTION: " + last

    result = p.decide(
        task=task,
        observation=observation,
        history=[{"role": "user", "content": context}],
        tools=TOOL_SCHEMAS,
    )

    verification = None
    if result.kind == "finish":
        verification = p.verify_goal(
            task=task,
            observation=observation,
            candidate_answer=result.text or "",
            history_summary=last,
        )
        if not verification.complete:
            feedback = (
                "VERIFIER: NOT COMPLETE. Missing: "
                + "; ".join(verification.missing)
                + ". Continue with a browser tool action; do not finish."
            )
            result = p.decide(
                task=task,
                observation=observation,
                history=[{"role": "user", "content": context + "\n" + feedback}],
                tools=TOOL_SCHEMAS,
            )

    return jsonify(
        {
            "kind": result.kind,
            "name": result.name,
            "arguments": result.arguments or {},
            "text": result.text or "",
            "verification": None
            if verification is None
            else {
                "complete": verification.complete,
                "summary": verification.summary,
                "missing": verification.missing,
            },
        }
    )


@app.get("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "mode": "extension-bridge",
            "architecture": "executor+verifier",
            "provider": type(get_provider()).__name__,
            "model": get_provider().model,
        }
    )


def main() -> None:
    print("[bridge] Browser Agent: http://127.0.0.1:8766")
    print("[bridge] Executor + Verifier enabled.")
    p = get_provider()
    print(f"[bridge] Provider: {type(p).__name__} / {p.model}")
    app.run(host="127.0.0.1", port=8766, debug=False, threaded=True)


if __name__ == "__main__":
    main()
