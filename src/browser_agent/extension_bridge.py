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


@app.post("/api/decision")
def decision():
    data = request.get_json(force=True)
    p = get_provider()
    task = str(data.get("task", ""))
    observation = str(data.get("observation", ""))
    last_action = str(data.get("last_action", "(none)"))

    result = p.decide(
        task=task,
        observation=observation,
        history=[{"role": "user", "content": f"LAST ACTION: {last_action}"}],
        tools=TOOL_SCHEMAS,
    )

    verification = None
    if result.kind == "finish":
        verification = p.verify_goal(
            task=task,
            observation=observation,
            candidate_answer=result.text or "",
            history_summary=last_action,
        )
        if not verification.complete:
            result = p.decide(
                task=task,
                observation=observation,
                history=[{
                    "role": "user",
                    "content": (
                        "VERIFIER: NOT COMPLETE. Missing outcomes: "
                        + "; ".join(verification.missing)
                        + ". Continue with a browser tool action; do not finish yet."
                    ),
                }],
                tools=TOOL_SCHEMAS,
            )

    return jsonify({
        "kind": result.kind,
        "name": result.name,
        "arguments": result.arguments or {},
        "text": result.text or "",
        "verification": None if verification is None else {
            "complete": verification.complete,
            "summary": verification.summary,
            "missing": verification.missing,
        },
    })


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "mode": "extension-bridge",
        "architecture": "executor+verifier",
    })


def main() -> None:
    print("[bridge] Browser Agent extension backend: http://127.0.0.1:8766")
    print("[bridge] Executor + strict goal verifier enabled.")
    print("[bridge] API keys stay here; the Opera extension never receives them.")
    app.run(host="127.0.0.1", port=8766, debug=False, threaded=True)


if __name__ == "__main__":
    main()
