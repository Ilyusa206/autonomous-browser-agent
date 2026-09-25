from __future__ import annotations

from flask import Flask, jsonify, request

from browser_agent.agent import TOOL_SCHEMAS
from browser_agent.provider import BrowserLLMProvider, get_provider_from_env
from browser_agent.state import AgentState, task_requires_evidence

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

    state = AgentState.from_dict(data.get("state"), objective=task)
    last_result = data.get("last_result")
    if isinstance(last_result, dict):
        state.ingest_result(last_result, after_observation=observation)
    else:
        state.observe(observation)
    context = state.render()

    if state.no_progress_count >= 6:
        return jsonify(
            {
                "kind": "stop",
                "name": None,
                "arguments": {},
                "text": "Stopped after repeated actions produced no page change or new evidence. "
                "The last failed strategies are preserved in agent state.",
                "verification": None,
                "state": state.to_dict(),
            }
        )

    result = p.decide(
        task=task,
        observation=observation,
        history=[{"role": "user", "content": context}],
        tools=TOOL_SCHEMAS,
    )

    # A weak local model may ignore textual recovery directives. Enforce the
    # generic no-progress invariant at the controller boundary instead of
    # spending more browser steps on the same extraction family.
    if result.kind == "tool" and result.name == "read_page":
        stalled_reads = [a for a in state.recent_actions[-6:] if a.tool == "read_page" and not a.progress]
        if len(stalled_reads) >= 2:
            state.current_subgoal = (
                "Extraction is stalled. Do not read this page again; use observed navigation/links, "
                "navigate to a better source supported by the UI, or finish from existing evidence."
            )
            return jsonify(
                {
                    "kind": "retry",
                    "name": None,
                    "arguments": {},
                    "text": "Repeated read_page blocked by controller. This retry does not consume the browser no-progress budget; choose a different action family.",
                    "verification": None,
                    "state": state.to_dict(),
                }
            )

    verification = None
    if result.kind == "finish":
        if task_requires_evidence(task) and not state.evidence:
            state.reject_finish(
                ["Extract relevant page content with read_page before finishing"],
                "Informational result has no grounded evidence",
            )
            return jsonify(
                {
                    "kind": "retry" if state.finish_rejections < 3 else "stop",
                    "name": None,
                    "arguments": {},
                    "text": "Informational completion rejected: no extracted evidence.",
                    "verification": {
                        "complete": False,
                        "summary": "Informational result has no grounded evidence",
                        "missing": ["Extract relevant page content with read_page before finishing"],
                    },
                    "state": state.to_dict(),
                }
            )
        verification = p.verify_goal(
            task=task,
            observation=observation,
            candidate_answer=result.text or "",
            history_summary=context,
        )
        if not verification.complete:
            state.reject_finish(verification.missing, verification.summary)
            if state.finish_rejections >= 3:
                return jsonify(
                    {
                        "kind": "stop",
                        "name": None,
                        "arguments": {},
                        "text": "Stopped after three rejected completion attempts: "
                        + (state.verifier_feedback[-1] if state.verifier_feedback else "insufficient grounded evidence"),
                        "verification": {
                            "complete": False,
                            "summary": verification.summary,
                            "missing": verification.missing,
                        },
                        "state": state.to_dict(),
                    }
                )
            return jsonify(
                {
                    "kind": "retry",
                    "name": None,
                    "arguments": {},
                    "text": "Verifier rejected completion; continue from the missing work in agent state.",
                    "verification": {
                        "complete": False,
                        "summary": verification.summary,
                        "missing": verification.missing,
                    },
                    "state": state.to_dict(),
                }
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
            "state": state.to_dict(),
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
