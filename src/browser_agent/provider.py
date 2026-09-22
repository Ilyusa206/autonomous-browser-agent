from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests
from dotenv import load_dotenv


@dataclass(frozen=True)
class ModelDecision:
    kind: str
    name: str | None = None
    arguments: dict[str, Any] | None = None
    text: str | None = None


class ZAIProvider:
    """Small provider adapter for Z.AI's documented Chat Completions API."""

    endpoint = "https://api.z.ai/api/paas/v4/chat/completions"

    def __init__(self, model: str = "glm-5.3") -> None:
        load_dotenv()
        self.api_key = os.getenv("ZAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("ZAI_API_KEY is missing. Put it in the local .env file.")
        self.model = model

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
                    "If the task is complete, answer concisely instead of calling another tool."
                ),
            },
            {
                "role": "user",
                "content": f"TASK:\n{task}\n\nCURRENT PAGE:\n{observation}",
            },
        ]
        messages.extend(history)

        response = requests.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "stream": False,
                "max_tokens": 1200,
            },
            timeout=90,
        )
        if not response.ok:
            raise RuntimeError(f"Z.AI API error {response.status_code}: {response.text[:500]}")

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
