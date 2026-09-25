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


class StubbornReadProvider:
    model = "fake"

    def __init__(self) -> None:
        self.tool_names: list[list[str]] = []

    def decide(self, **kwargs):
        names = [tool["function"]["name"] for tool in kwargs["tools"]]
        self.tool_names.append(names)
        if "read_page" not in names:
            return ModelDecision(kind="tool", name="click", arguments={"ref": "e1"})
        return ModelDecision(kind="tool", name="read_page", arguments={"query": "same target", "max_chars": 2400})

    def verify_goal(self, **kwargs):
        return GoalVerification(False, "not done", [])


def test_bridge_blocks_third_stalled_read_action_family() -> None:
    fake = StubbornReadProvider()
    extension_bridge.provider = fake
    client = extension_bridge.app.test_client()
    observation = "URL: https://example.test\nTITLE: Example\n\nINTERACTIVE ELEMENTS:\n[e1] link \"Better source\"\n\nVIEWPORT TEXT:\nsame target"
    state = {
        "objective": "Explain target",
        "recent_actions": [
            {"tool": "read_page", "arguments": {"query": "same target"}, "ok": True, "message": "read", "progress": False},
            {"tool": "read_page", "arguments": {"query": "same target", "max_chars": 1000}, "ok": True, "message": "read", "progress": False},
        ],
        "no_progress_count": 2,
    }
    response = client.post("/api/decision", json={"task": "Explain target", "observation": observation, "state": state}).get_json()
    assert response["kind"] == "tool"
    assert response["name"] == "click"
    assert "read_page" not in fake.tool_names[-1]
    assert response["state"]["no_progress_count"] == 2
    extension_bridge.provider = None


def test_blocked_model_retry_does_not_trigger_global_no_progress_stop() -> None:
    fake = StubbornReadProvider()
    extension_bridge.provider = fake
    client = extension_bridge.app.test_client()
    observation = "URL: https://example.test\nTITLE: Example\n\nVIEWPORT TEXT:\nsame target"
    state = {
        "objective": "Explain target",
        "recent_actions": [
            {"tool": "read_page", "arguments": {"query": "same target"}, "ok": True, "message": "read", "progress": False},
            {"tool": "read_page", "arguments": {"query": "same target"}, "ok": True, "message": "read", "progress": False},
        ],
        "no_progress_count": 5,
    }
    first = client.post("/api/decision", json={"task": "Explain target", "observation": observation, "state": state}).get_json()
    assert first["kind"] == "tool"
    assert first["name"] == "click"
    assert first["state"]["no_progress_count"] == 5
    assert "read_page" not in fake.tool_names[-1]
    extension_bridge.provider = None


class AnswerWhenToollessProvider:
    model = "fake"

    def __init__(self) -> None:
        self.tool_names: list[list[str]] = []

    def decide(self, **kwargs):
        names = [tool["function"]["name"] for tool in kwargs["tools"]]
        self.tool_names.append(names)
        if not names:
            return ModelDecision(kind="finish", text="Grounded answer from accumulated evidence")
        return ModelDecision(kind="tool", name="read_page", arguments={"query": "target"})

    def verify_goal(self, **kwargs):
        return GoalVerification(True, "Evidence answers the task", [])


def test_stalled_extraction_with_fresh_evidence_forces_verified_completion_checkpoint() -> None:
    fake = AnswerWhenToollessProvider()
    extension_bridge.provider = fake
    client = extension_bridge.app.test_client()
    observation = "URL: https://example.test/docs\nTITLE: Docs\n\nVIEWPORT TEXT:\ntarget limitation"
    state = {
        "objective": "Read target and explain its limitation",
        "evidence": [{"source_url": "https://example.test/docs", "title": "Docs", "content": "target does X; limitation is Y", "query": "target"}],
        "recent_actions": [
            {"tool": "read_page", "arguments": {"query": "target"}, "ok": True, "message": "new evidence", "progress": True},
            {"tool": "read_page", "arguments": {"query": "target"}, "ok": True, "message": "duplicate", "progress": False},
            {"tool": "read_page", "arguments": {"query": "target"}, "ok": True, "message": "duplicate", "progress": False},
        ],
    }
    response = client.post("/api/decision", json={"task": state["objective"], "observation": observation, "state": state}).get_json()
    assert fake.tool_names[-1] == []
    assert response["kind"] == "finish"
    assert response["verification"]["complete"] is True
    assert response["text"] == "Grounded answer from accumulated evidence"
    extension_bridge.provider = None
