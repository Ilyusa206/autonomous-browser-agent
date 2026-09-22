from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
                },
                "required": ["ref", "text"],
            },
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
    def __init__(self, page, provider: GroqProvider, max_steps: int = 12) -> None:
        self.page = page
        self.provider = provider
        self.tools = BrowserTools(page)
        self.max_steps = max_steps

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
        print(f"[safety] confirmation required: {reason}")
        answer = input("[safety] Continue? [y/N]: ").strip().lower()
        return answer in {"y", "yes"}

    def run(self, task: str) -> AgentRunResult:
        history: list[dict[str, str]] = []
        last_action_summary = "(none yet)"
        consecutive_errors = 0

        for step in range(1, self.max_steps + 1):
            page_observation = observe_page(self.page)
            observation = page_observation.render()
            print(f"[agent] step {step}/{self.max_steps}: asking model")

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
                print(f"[agent] finish: {message}")
                return AgentRunResult(True, message, step)

            assert decision.name is not None
            arguments = decision.arguments or {}
            print(f"[agent] tool: {decision.name} {arguments}")

            risk_reason = self._risk_reason(decision.name, arguments, page_observation)
            if risk_reason and not self._confirm(risk_reason):
                message = "Stopped before a potentially destructive action because user confirmation was not granted."
                print(f"[safety] {message}")
                return AgentRunResult(False, message, step)

            result = self.tools.execute(decision.name, arguments)
            print(f"[agent] result: ok={result.ok} {result.message}")

            if result.ok:
                consecutive_errors = 0
            else:
                consecutive_errors += 1
                print(
                    f"[recovery] action failed ({consecutive_errors}/3); "
                    "fresh observation will be sent to the model so it can adapt"
                )
                if consecutive_errors >= 3:
                    message = "Stopped after 3 consecutive browser-action failures."
                    print(f"[recovery] {message}")
                    return AgentRunResult(False, message, step)

            last_action_summary = (
                f"{decision.name} {arguments} -> "
                f"ok={result.ok}; {result.message[:180]}"
            )

        message = f"Stopped after max_steps={self.max_steps} without a final answer."
        print(f"[agent] {message}")
        return AgentRunResult(False, message, self.max_steps)
