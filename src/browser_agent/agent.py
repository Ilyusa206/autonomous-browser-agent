from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from browser_agent.observation import observe_page
from browser_agent.provider import GroqProvider
from browser_agent.tools import BrowserTools


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "Navigate the browser to a URL when the URL is known or supplied by the user.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Click an interactive element from the current observation using its temporary ref.",
            "parameters": {
                "type": "object",
                "properties": {"ref": {"type": "string", "description": "Element ref such as e3"}},
                "required": ["ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type",
            "description": "Enter text into an input-like element from the current observation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "text": {"type": "string"},
                    "clear": {"type": "boolean"},
                    "submit": {"type": "boolean", "description": "Press Enter after typing when the task requires submitting/searching."},
                },
                "required": ["ref", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press",
            "description": "Press a keyboard key on an observed element, useful for Enter, Escape, ArrowDown and dynamic widgets.",
            "parameters": {"type": "object", "properties": {"ref": {"type": "string"}, "key": {"type": "string"}}, "required": ["ref", "key"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll vertically. Positive values scroll down; negative values scroll up.",
            "parameters": {
                "type": "object",
                "properties": {"amount": {"type": "integer"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "back",
            "description": "Go back one page in browser history.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Pause and ask the user for missing information or a manual browser action that is required to continue, such as an address, login, CAPTCHA, permission, or preference. Do not use this for information already visible on the page.",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait briefly for dynamic page content to update.",
            "parameters": {
                "type": "object",
                "properties": {"milliseconds": {"type": "integer"}},
            },
        },
    },
]


@dataclass
class AgentRunResult:
    completed: bool
    message: str
    steps: int


class AutonomousAgent:
    def __init__(
        self,
        page,
        provider: GroqProvider,
        max_steps: int = 24,
        event_sink: Callable[[str, str], None] | None = None,
        confirm_callback: Callable[[str], bool] | None = None,
        ask_user_callback: Callable[[str], str] | None = None,
    ) -> None:
        self.page = page
        self.provider = provider
        self.tools = BrowserTools(page)
        self.max_steps = max_steps
        self.event_sink = event_sink
        self.confirm_callback = confirm_callback
        self.ask_user_callback = ask_user_callback

    def _emit(self, kind: str, message: str) -> None:
        print(message)
        if self.event_sink:
            self.event_sink(kind, message)

    def _risk_reason(self, name: str, arguments: dict[str, Any], observation) -> str | None:
        """Conservative generic gate for irreversible/high-impact browser actions."""
        if name != "click":
            return None
        ref = str(arguments.get("ref", ""))
        element = next((item for item in observation.elements if item.get("ref") == ref), None)
        if not element:
            return None
        haystack = " ".join(str(element.get(key, "")) for key in ("text", "name", "placeholder", "href")).lower()
        risky_phrases = (
            "delete", "remove", "erase", "destroy", "pay", "purchase", "buy now",
            "place order", "confirm order", "send money", "transfer", "unsubscribe",
            "удалить", "оплатить", "купить", "оформить заказ", "подтвердить заказ",
            "перевести", "отписаться",
        )
        matched = next((phrase for phrase in risky_phrases if phrase in haystack), None)
        if matched:
            return f"Potentially destructive or consequential click ({matched!r}) on {ref}"
        return None

    def _confirm(self, reason: str) -> bool:
        self._emit("safety", f"[safety] confirmation required: {reason}")
        if self.confirm_callback:
            return self.confirm_callback(reason)
        answer = input("[safety] Continue? [y/N]: ").strip().lower()
        return answer in {"y", "yes"}

    def _ask_user(self, question: str) -> str:
        self._emit("user_input", f"[user] {question}")
        if self.ask_user_callback:
            answer = self.ask_user_callback(question)
        else:
            answer = input(f"[user] {question}\n> ").strip()
        self._emit("user_answer", f"[user] Ответ получен: {answer or '(пользователь продолжил вручную)'}")
        return answer

    def _debug_fail_once(self, name: str, arguments: dict[str, Any]) -> bool:
        """Deterministic demo hook for proving recovery; disabled unless env flag is set."""
        import os

        if os.getenv("BROWSER_AGENT_DEBUG_FAIL_ONCE") != "1":
            return False
        if getattr(self, "_debug_failure_injected", False):
            return False
        if name not in {"click", "type"}:
            return False
        self._debug_failure_injected = True
        return True

    def run(self, task: str) -> AgentRunResult:
        history: list[dict[str, str]] = []
        last_action_summary = "(none yet)"
        consecutive_errors = 0
        recent_actions: list[str] = []

        for step in range(1, self.max_steps + 1):
            page_observation = observe_page(self.page)
            observation = page_observation.render()
            self._emit("thinking", f"[agent] step {step}/{self.max_steps}: asking model")

            decision = self.provider.decide(
                task=task,
                observation=observation,
                history=[
                    {
                        "role": "user",
                        "content": f"LAST ACTION: {last_action_summary}",
                    }
                ],
                tools=TOOL_SCHEMAS,
            )

            if decision.kind == "finish":
                message = decision.text or "Task complete."
                self._emit("finish", f"[agent] finish: {message}")
                return AgentRunResult(True, message, step)

            assert decision.name is not None
            arguments = decision.arguments or {}
            action_signature = f"{decision.name} {arguments}"
            self._emit("tool", f"[agent] tool: {decision.name} {arguments}")
            recent_actions.append(action_signature)
            recent_actions = recent_actions[-4:]
            if len(recent_actions) >= 3 and len(set(recent_actions[-3:])) == 1:
                self._emit("recovery", "[recovery] repeated identical action detected; forcing fresh state before continuing")
                self.page.wait_for_timeout(700)
                last_action_summary = "RECOVERY: previous action repeated without progress; inspect fresh page and choose a different action."
                recent_actions.clear()
                continue

            if decision.name == "ask_user":
                question = str(arguments.get("question", "")).strip() or "Нужно действие пользователя. Выполните его в браузере и продолжите."
                answer = self._ask_user(question)
                last_action_summary = f"USER INPUT: {answer or 'manual browser action completed'}"
                consecutive_errors = 0
                continue

            risk_reason = self._risk_reason(decision.name, arguments, page_observation)
            if risk_reason and not self._confirm(risk_reason):
                message = "Stopped before a potentially destructive action because user confirmation was not granted."
                self._emit("safety", f"[safety] {message}")
                return AgentRunResult(False, message, step)

            if self._debug_fail_once(decision.name, arguments):
                from browser_agent.tools import ToolResult
                result = ToolResult(
                    ok=False,
                    message="Injected transient browser failure for recovery demonstration",
                    observation=observation,
                )
            else:
                result = self.tools.execute(decision.name, arguments)
            self._emit("result", f"[agent] result: ok={result.ok} {result.message}")

            if result.ok:
                consecutive_errors = 0
            else:
                consecutive_errors += 1
                self._emit(
                    "recovery",
                    f"[recovery] action failed ({consecutive_errors}/3); "
                    "fresh observation will be sent to the model so it can adapt",
                )
                if consecutive_errors >= 3:
                    message = "Stopped after 3 consecutive browser-action failures."
                    self._emit("recovery", f"[recovery] {message}")
                    return AgentRunResult(False, message, step)

            last_action_summary = (
                f"{decision.name} {arguments} -> "
                f"ok={result.ok}; {result.message[:180]}"
            )

        message = f"Stopped after max_steps={self.max_steps} without a final answer."
        self._emit("finish", f"[agent] {message}")
        return AgentRunResult(False, message, self.max_steps)
