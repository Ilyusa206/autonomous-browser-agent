from unittest.mock import Mock, patch

from browser_agent.agent import TOOL_SCHEMAS, AutonomousAgent
from browser_agent.observation import PageObservation
from browser_agent.provider import GoalVerification, ModelDecision
from browser_agent.tools import ToolResult


OBSERVATION = PageObservation(
    url="https://docs.example.test/api",
    title="API docs",
    text="run() executes the coroutine and closes the executor.",
    elements=[],
)


def test_tool_contract_exposes_read_and_user_pause_without_site_specific_tools() -> None:
    names = {item["function"]["name"] for item in TOOL_SCHEMAS}
    assert {"read_page", "find_text", "ask_user", "click", "type", "press"} <= names
    assert not any(site in str(TOOL_SCHEMAS).lower() for site in ("gmail", "yandex", "hh.ru", "python.org"))


class EvidenceProvider:
    model = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.verifier_state = ""

    def decide(self, *, task, observation, history, tools):
        self.calls += 1
        if self.calls == 1:
            return ModelDecision(kind="tool", name="read_page", arguments={"query": "run()"})
        return ModelDecision(kind="finish", text="run() executes a coroutine and manages the event loop.")

    def verify_goal(self, *, task, observation, candidate_answer, history_summary=""):
        self.verifier_state = history_summary
        return GoalVerification(complete="ev-" in history_summary, summary="grounded", missing=[])


def test_agent_reads_saves_evidence_and_verifies_against_state() -> None:
    provider = EvidenceProvider()
    tools = Mock()
    tools.execute.return_value = ToolResult(
        True,
        "read",
        OBSERVATION.render(),
        {"source_url": OBSERVATION.url, "title": "run()", "query": "run()", "content": OBSERVATION.text},
    )
    with patch("browser_agent.agent.observe_page", return_value=OBSERVATION), patch("browser_agent.agent.BrowserTools", return_value=tools):
        result = AutonomousAgent(object(), provider, max_steps=4).run("Explain run()")
    assert result.completed is True
    assert result.steps == 2
    assert result.state and len(result.state["evidence"]) == 1
    assert "run() executes" in provider.verifier_state


class RejectionProvider:
    model = "fake"

    def __init__(self) -> None:
        self.decisions = 0
        self.verifications = 0
        self.histories: list[str] = []

    def decide(self, *, task, observation, history, tools):
        self.decisions += 1
        self.histories.append(history[0]["content"])
        if self.decisions == 1:
            return ModelDecision(kind="finish", text="unsupported")
        if self.decisions == 2:
            return ModelDecision(kind="tool", name="read_page", arguments={"query": "run"})
        return ModelDecision(kind="finish", text="supported")

    def verify_goal(self, **kwargs):
        self.verifications += 1
        if self.verifications == 1:
            return GoalVerification(False, "No evidence", ["Read the relevant section"])
        return GoalVerification(True, "complete", [])


def test_verifier_rejection_changes_later_reasoning_context() -> None:
    provider = RejectionProvider()
    tools = Mock()
    tools.execute.return_value = ToolResult(
        True, "read", OBSERVATION.render(),
        {"source_url": OBSERVATION.url, "title": "run", "content": OBSERVATION.text},
    )
    with patch("browser_agent.agent.observe_page", return_value=OBSERVATION), patch("browser_agent.agent.BrowserTools", return_value=tools):
        result = AutonomousAgent(object(), provider, max_steps=5).run("Complete the workflow")
    assert result.completed is True
    assert "Read the relevant section" in provider.histories[1]


def test_safety_denial_stops_before_click() -> None:
    page = PageObservation(
        url="https://app.example.test/account", title="Account", text="Settings",
        elements=[{"ref": "e1", "text": "Delete account", "name": "", "placeholder": "", "href": "", "type": "button"}],
    )
    provider = Mock(model="fake")
    provider.decide.return_value = ModelDecision(kind="tool", name="click", arguments={"ref": "e1"})
    tools = Mock()
    with patch("browser_agent.agent.observe_page", return_value=page), patch("browser_agent.agent.BrowserTools", return_value=tools):
        result = AutonomousAgent(object(), provider, max_steps=2, confirm_callback=lambda _: False).run("Delete account")
    assert result.completed is False
    tools.execute.assert_not_called()


def test_safety_also_guards_enter_submission() -> None:
    page = PageObservation(
        url="https://shop.example.test/checkout", title="Checkout", text="Confirm order",
        elements=[
            {"ref": "e1", "text": "", "name": "Cardholder", "placeholder": "", "href": "", "type": "text"},
            {"ref": "e2", "text": "Confirm order", "name": "", "placeholder": "", "href": "", "type": "button"},
        ],
    )
    provider = Mock(model="fake")
    provider.decide.return_value = ModelDecision(kind="tool", name="press", arguments={"ref": "e1", "key": "Enter"})
    tools = Mock()
    with patch("browser_agent.agent.observe_page", return_value=page), patch("browser_agent.agent.BrowserTools", return_value=tools):
        result = AutonomousAgent(object(), provider, max_steps=2, confirm_callback=lambda _: False).run("Prepare order")
    assert result.completed is False
    tools.execute.assert_not_called()


class RecoveryProvider:
    model = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.saw_failure = False

    def decide(self, *, history, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return ModelDecision(kind="tool", name="click", arguments={"ref": "e1"})
        if self.calls == 2:
            self.saw_failure = "Stale or missing" in history[0]["content"]
            return ModelDecision(kind="tool", name="read_page", arguments={"query": "result"})
        return ModelDecision(kind="finish", text="Recovered result")

    def verify_goal(self, **kwargs):
        return GoalVerification(True, "complete", [])


def test_agent_recovers_from_stale_ref_using_failure_memory() -> None:
    provider = RecoveryProvider()
    tools = Mock()
    tools.execute.side_effect = [
        ToolResult(False, "ValueError: Stale or missing element ref: e1", OBSERVATION.render()),
        ToolResult(True, "read", OBSERVATION.render(), {"source_url": OBSERVATION.url, "title": "Result", "content": "Recovered result"}),
    ]
    with patch("browser_agent.agent.observe_page", return_value=OBSERVATION), patch("browser_agent.agent.BrowserTools", return_value=tools):
        result = AutonomousAgent(object(), provider, max_steps=5).run("Read the result")
    assert result.completed is True
    assert provider.saw_failure is True
    assert tools.execute.call_count == 2


def test_informational_finish_cannot_pass_without_extracted_evidence() -> None:
    provider = Mock(model="fake")
    provider.decide.return_value = ModelDecision(kind="finish", text="Unsupported factual answer")
    provider.verify_goal.return_value = GoalVerification(True, "would accept", [])
    with patch("browser_agent.agent.observe_page", return_value=OBSERVATION), patch("browser_agent.agent.BrowserTools"):
        result = AutonomousAgent(object(), provider, max_steps=3).run("Explain what run() does")
    assert result.completed is False
    assert result.state and result.state["finish_rejections"] == 3
    provider.verify_goal.assert_not_called()
