from browser_agent import extension_bridge
from browser_agent.provider import GoalVerification, ModelDecision


class BridgeProvider:
    model = "fake"

    def __init__(self) -> None:
        self.contexts: list[str] = []

    def decide(self, *, task, observation, history, tools):
        context = history[0]["content"]
        self.contexts.append(context)
        if "EVIDENCE (use it" in context:
            return ModelDecision(kind="finish", text="Grounded answer")
        return ModelDecision(kind="tool", name="read_page", arguments={"query": "target"})

    def verify_goal(self, *, history_summary, **kwargs):
        return GoalVerification("EVIDENCE (use it" in history_summary, "checked", [])


def test_bridge_carries_evidence_between_stateless_http_decisions() -> None:
    fake = BridgeProvider()
    extension_bridge.provider = fake
    client = extension_bridge.app.test_client()
    observation = "URL: https://example.test\nTITLE: Example\n\nVIEWPORT TEXT:\ntarget fact"
    first = client.post("/api/decision", json={"task": "Explain target", "observation": observation}).get_json()
    assert first["kind"] == "tool"
    second = client.post(
        "/api/decision",
        json={
            "task": "Explain target",
            "observation": observation,
            "state": first["state"],
            "last_result": {
                "tool": "read_page", "arguments": {"query": "target"}, "ok": True, "message": "read",
                "before_fingerprint": first["state"]["current_fingerprint"],
                "evidence": {"source_url": "https://example.test", "title": "Target", "content": "target fact"},
            },
        },
    ).get_json()
    assert second["kind"] == "finish"
    assert len(second["state"]["evidence"]) == 1
    assert "target fact" in fake.contexts[-1]
    extension_bridge.provider = None
