from browser_agent.tools import ToolResult


def test_tool_result_shape() -> None:
    result = ToolResult(ok=True, message="ok", observation="snapshot")

    assert result.ok is True
    assert result.message == "ok"
    assert result.observation == "snapshot"
