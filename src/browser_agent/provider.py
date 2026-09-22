from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests
from dotenv import load_dotenv


@dataclass(frozen=True)
class ModelDecision:
    kind: str
    name: str | None = None
    arguments: dict[str, Any] | None = None
    text: str | None = None


class GroqProvider:
    """Provider adapter for Groq's OpenAI-compatible Chat Completions API."""

    endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, model: str = "openai/gpt-oss-120b", event_sink: Callable[[str, str], None] | None = None) -> None:
        load_dotenv()
        self.api_key = os.getenv("GROQ_API_KEY")
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is missing. Put it in the local .env file.")
        self.model = model
        self.event_sink = event_sink

    def _post_with_rate_limit_retry(self, **kwargs):
        """Retry transient Groq rate limits without turning them into browser-action failures."""
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            response = requests.post(self.endpoint, **kwargs)
            if response.status_code != 429 or attempt == max_attempts:
                return response

            retry_after = response.headers.get("retry-after")
            try:
                delay = float(retry_after) if retry_after else 15.0
            except ValueError:
                delay = 15.0
            delay = min(max(delay + 1.0, 2.0), 30.0)
            message = f"Лимит API: повтор через {delay:.0f} с ({attempt}/{max_attempts - 1})"
            print(f"[provider] {message}")
            if self.event_sink:
                self.event_sink("waiting", message)
            time.sleep(delay)

        return response

    def decide(
        self,
        *,
        task: str,
        observation: str,
        history: list[dict[str, str]],
        tools: list[dict[str, Any]],
    ) -> ModelDecision:
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You are an autonomous browser agent. Complete the user's task using only the "
                    "provided generic browser tools. Inspect the compact page observation; element "
                    "refs such as e1 are temporary and valid only for the current observation. "
                    "Do not invent refs or assume site-specific routes/selectors. Work step by step. "
                    "Treat the starting page only as the browser's current state, not as a hint about where the task "
                    "must be completed. First decide whether the current site is relevant to the user's task. If it is "
                    "unrelated, do not misuse its local search box; navigate to an appropriate general web search or "
                    "relevant service using ordinary web knowledge, then continue from observations. "
                    "If progress requires information only the user can provide (for example delivery address, "
                    "account choice, preference), or a manual browser action such as CAPTCHA, login, or browser "
                    "permission, call ask_user with a concise question instead of guessing, repeatedly scrolling, "
                    "or trying to bypass the challenge. After the user responds, inspect the fresh page and continue. "
                    "The CURRENT PAGE observation is fresh after every browser action, so do not request "
                    "a redundant read. If the visible text already answers the task, finish immediately. "
                    "If the task is complete, answer concisely instead of calling another tool."
                ),
            },
            {
                "role": "user",
                "content": f"TASK:\n{task}\n\nCURRENT PAGE:\n{observation}",
            },
        ]
        messages.extend(history)

        response = self._post_with_rate_limit_retry(
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "parallel_tool_calls": False,
                "stream": False,
                "max_completion_tokens": 450,
                "reasoning_effort": "low",
            },
            timeout=90,
        )
        if not response.ok:
            raise RuntimeError(f"Groq API error {response.status_code}: {response.text[:500]}")

        payload = response.json()
        message = payload["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []

        if tool_calls:
            call = tool_calls[0]
            function = call["function"]
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            return ModelDecision(kind="tool", name=function["name"], arguments=arguments)

        return ModelDecision(kind="finish", text=(message.get("content") or "").strip())
