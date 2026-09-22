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
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read the current page again without changing it.",
            "parameters": {"type": "object", "properties": {}},
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

    def run(self, task: str) -> AgentRunResult:
        history: list[dict[str, str]] = []

        for step in range(1, self.max_steps + 1):
            observation = observe_page(self.page).render()
            print(f"[agent] step {step}/{self.max_steps}: asking model")

            decision = self.provider.decide(
                task=task,
                observation=observation,
                history=history,
                tools=TOOL_SCHEMAS,
            )

            if decision.kind == "finish":
                message = decision.text or "Task complete."
                print(f"[agent] finish: {message}")
                return AgentRunResult(True, message, step)

            assert decision.name is not None
            arguments = decision.arguments or {}
            print(f"[agent] tool: {decision.name} {arguments}")

            result = self.tools.execute(decision.name, arguments)
            print(f"[agent] result: ok={result.ok} {result.message}")

            history.append(
                {
                    "role": "user",
                    "content": (
                        f"Previous action: {decision.name} {arguments}\n"
                        f"Result: ok={result.ok}; {result.message}\n"
                        "Continue the original task using the CURRENT PAGE observation."
                    ),
                }
            )
            history = history[-6:]

        message = f"Stopped after max_steps={self.max_steps} without a final answer."
        print(f"[agent] {message}")
        return AgentRunResult(False, message, self.max_steps)
