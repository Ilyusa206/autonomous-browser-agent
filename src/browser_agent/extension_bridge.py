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
    p = get_provider()\n    result = p.decide(
        task=str(data.get("task", "")),
        observation=str(data.get("observation", "")),
        history=[{"role": "user", "content": f"LAST ACTION: {data.get('last_action', '(none)')}"}],
        tools=TOOL_SCHEMAS,
    )
    return jsonify({
        "kind": result.kind,
        "name": result.name,
        "arguments": result.arguments or {},
        "text": result.text or "",
    })


@app.get("/health")
def health():
    return jsonify({"ok": True, "mode": "extension-bridge"})


def main() -> None:
    print("[bridge] Browser Agent extension backend: http://127.0.0.1:8766")
    print("[bridge] Executor + strict goal verifier enabled.")\n    print("[bridge] API keys stay here; the Opera extension never receives them.")
    app.run(host="127.0.0.1", port=8766, debug=False, threaded=True)


if __name__ == "__main__":
    main()
