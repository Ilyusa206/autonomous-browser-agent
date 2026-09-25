from browser_agent.tools import BrowserTools, ToolResult


def test_tool_result_shape() -> None:
    result = ToolResult(ok=True, message="ok", observation="snapshot")

    assert result.ok is True
    assert result.message == "ok"
    assert result.observation == "snapshot"


class FakePage:
    def is_closed(self):
        return False

    def evaluate(self, _script, arguments):
        assert arguments["query"] == "asyncio.run"
        assert arguments["maxChars"] == 900
        return {
            "source_url": "https://docs.python.org/3/library/asyncio-runner.html",
            "title": "Runners",
            "query": "asyncio.run",
            "content": "asyncio.run executes a coroutine and manages an event loop.",
        }

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def wait_for_timeout(self, _milliseconds):
        return None

    def locator(self, _selector):
        return MissingLocator()


class MissingLocator:
    def count(self):
        return 0


def test_read_page_returns_bounded_structured_evidence() -> None:
    tools = BrowserTools(FakePage())
    tools._safe_observation = lambda: "fresh observation"
    result = tools.read_page(query="asyncio.run", max_chars=900)
    assert result.ok is True
    assert result.evidence == {
        "source_url": "https://docs.python.org/3/library/asyncio-runner.html",
        "title": "Runners",
        "query": "asyncio.run",
        "content": "asyncio.run executes a coroutine and manages an event loop.",
    }
    assert result.observation == "fresh observation"


def test_stale_ref_becomes_recoverable_tool_failure() -> None:
    tools = BrowserTools(FakePage())
    tools._safe_observation = lambda: "fresh observation after DOM replacement"
    result = tools.execute("click", {"ref": "e99"})
    assert result.ok is False
    assert "Stale or missing element ref" in result.message
    assert result.observation == "fresh observation after DOM replacement"
