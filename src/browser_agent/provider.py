from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import requests
from dotenv import load_dotenv


@dataclass(frozen=True)
class ModelDecision:
    kind: str
    name: str | None = None
    arguments: dict[str, Any] | None = None
    text: str | None = None


@dataclass(frozen=True)
class GoalVerification:
    complete: bool
    summary: str
    missing: list[str]


class BrowserLLMProvider(Protocol):
    model: str

    def create_plan(self, *, task: str, observation: str) -> dict[str, Any]: ...
    def verify_goal(self, *, task: str, observation: str, candidate_answer: str, history_summary: str = "") -> GoalVerification: ...
    def decide(self, *, task: str, observation: str, history: list[dict[str, str]], tools: list[dict[str, Any]]) -> ModelDecision: ...


def _compact_observation(observation: str, limit: int = 6500) -> str:
    """Keep URL/title/elements and a bounded visible-text tail to control latency/cost."""
    if len(observation) <= limit:
        return observation
    marker = "\nVISIBLE TEXT:\n"
    head, sep, text = observation.partition(marker)
    if not sep:
        return observation[:limit]
    remaining = max(limit - len(head) - len(marker), 500)
    return head + marker + text[:remaining]


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

    def create_plan(self, *, task: str, observation: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": "You are a browser-agent planner. Return JSON only: objective string, steps array, success_criteria array. Make 3-7 site-agnostic outcome steps. Never invent selectors or routes. Never plan to request, collect, or type passwords, OTP/2FA codes, API keys, or other secrets. Assume an existing browser session may already be authenticated; inspect it first. If authentication is actually required, plan for the user to complete login manually."},
            {"role": "user", "content": f"TASK:\n{task}\n\nSTARTING PAGE:\n{_compact_observation(observation, 4200)}"},
        ]
        response = self._post_with_rate_limit_retry(headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json={"model": self.model, "messages": messages, "response_format": {"type": "json_object"}, "stream": False, "max_completion_tokens": 350, "reasoning_effort": "low"}, timeout=90)
        if not response.ok:
            raise RuntimeError(f"Groq planner error {response.status_code}: {response.text[:500]}")
        data = json.loads(response.json()["choices"][0]["message"].get("content") or "{}")
        return {"objective": str(data.get("objective", task)), "steps": [str(x) for x in data.get("steps", [])][:7], "success_criteria": [str(x) for x in data.get("success_criteria", [])][:7]}

    def verify_goal(self, *, task: str, observation: str, candidate_answer: str, history_summary: str = "") -> GoalVerification:
        messages = [
            {"role": "system", "content": "You are a strict browser-agent verifier. A success claim is not evidence. Every task constraint and requested side effect must be observable. Return JSON only: complete boolean, summary string, missing array."},
            {"role": "user", "content": f"TASK:\n{task}\n\nCANDIDATE:\n{candidate_answer}\n\nLAST ACTION:\n{history_summary}\n\nPAGE:\n{_compact_observation(observation, 5000)}"},
        ]
        response = self._post_with_rate_limit_retry(headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json={"model": self.model, "messages": messages, "response_format": {"type": "json_object"}, "stream": False, "max_completion_tokens": 300, "reasoning_effort": "low"}, timeout=90)
        if not response.ok:
            raise RuntimeError(f"Groq verifier error {response.status_code}: {response.text[:500]}")
        data = json.loads(response.json()["choices"][0]["message"].get("content") or "{}")
        return GoalVerification(bool(data.get("complete")), str(data.get("summary", "")), [str(x) for x in data.get("missing", [])])

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
                    "Be proactive: make ordinary reversible choices yourself when the task leaves them unspecified, "
                    "such as which search engine, public website, marketplace, or delivery service to try first. "
                    "Do not ask the user to choose a site or service unless that choice is materially consequential "
                    "or the user explicitly constrained it. If progress requires information only the user can provide "
                    "(for example a delivery address or genuinely necessary preference), or a manual "
                    "browser action such as CAPTCHA, login, or browser "
                    "permission, call ask_user with a concise question instead of guessing, repeatedly scrolling, "
                    "or trying to bypass the challenge. NEVER request passwords, OTP/2FA codes, API keys, or other secrets; ask the user to perform authentication manually in the browser, then continue from a fresh observation. After the user responds, inspect the fresh page and continue. "
                    "Never call ask_user merely to ask the user to click, focus, open, scroll, or type into an element that appears in CURRENT PAGE; use the available browser tools yourself. If an action fails, inspect the fresh observation and try a different observed element or interaction before escalating. Never navigate to the current URL again just to refresh state; use wait or inspect the fresh observation. " "The CURRENT PAGE observation is fresh after every browser action, so do not request "
                    "a redundant read. If the visible text already answers the task, finish immediately. "
                    "If the task is complete, answer concisely instead of calling another tool."
                ),
            },
            {
                "role": "user",
                "content": f"TASK:\n{task}\n\nCURRENT PAGE:\n{_compact_observation(observation)}",
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


class OpenAIProvider(GroqProvider):
    """Official OpenAI runtime using the Chat Completions tool-calling contract."""

    endpoint = "https://api.openai.com/v1/chat/completions"

    def __init__(self, model: str | None = None, event_sink: Callable[[str, str], None] | None = None) -> None:
        load_dotenv()
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is missing. Put it in the local .env file.")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5-mini")
        self.event_sink = event_sink

    def _post_with_rate_limit_retry(self, **kwargs):
        response = requests.post(self.endpoint, **kwargs)
        return response

    def create_plan(self, *, task: str, observation: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": "You are a browser-agent planner. Return JSON only: objective string, steps array, success_criteria array. Make 3-7 site-agnostic outcome steps. Never invent selectors or routes."},
            {"role": "user", "content": f"TASK:\n{task}\n\nSTARTING PAGE:\n{_compact_observation(observation, 4200)}"},
        ]
        response = requests.post(self.endpoint, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json={"model": self.model, "messages": messages, "response_format": {"type": "json_object"}, "max_completion_tokens": 300}, timeout=90)
        if not response.ok:
            raise RuntimeError(f"OpenAI planner error {response.status_code}: {response.text[:500]}")
        data = json.loads(response.json()["choices"][0]["message"].get("content") or "{}")
        return {"objective": str(data.get("objective", task)), "steps": [str(x) for x in data.get("steps", [])][:7], "success_criteria": [str(x) for x in data.get("success_criteria", [])][:7]}

    def verify_goal(self, *, task: str, observation: str, candidate_answer: str, history_summary: str = "") -> GoalVerification:
        messages = [
            {"role": "system", "content": "You are a strict browser-agent verifier. Every task constraint and requested side effect must be observable. Return JSON only: complete boolean, summary string, missing array."},
            {"role": "user", "content": f"TASK:\n{task}\n\nCANDIDATE:\n{candidate_answer}\n\nLAST ACTION:\n{history_summary}\n\nPAGE:\n{_compact_observation(observation, 5000)}"},
        ]
        response = requests.post(self.endpoint, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json={"model": self.model, "messages": messages, "response_format": {"type": "json_object"}, "max_completion_tokens": 250}, timeout=90)
        if not response.ok:
            raise RuntimeError(f"OpenAI verifier error {response.status_code}: {response.text[:500]}")
        data = json.loads(response.json()["choices"][0]["message"].get("content") or "{}")
        return GoalVerification(bool(data.get("complete")), str(data.get("summary", "")), [str(x) for x in data.get("missing", [])])


class AnthropicProvider:
    """Official Claude Messages API adapter with native client-side tool use."""

    endpoint = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str | None = None, event_sink: Callable[[str, str], None] | None = None) -> None:
        load_dotenv()
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is missing. Put it in the local .env file.")
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
        self.event_sink = event_sink

    @property
    def headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(self.endpoint, headers=self.headers, json=payload, timeout=90)
        if not response.ok:
            raise RuntimeError(f"Anthropic API error {response.status_code}: {response.text[:500]}")
        return response.json()

    def _json(self, system: str, user: str, max_tokens: int) -> dict[str, Any]:
        payload = self._request({"model": self.model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]})
        text = "".join(x.get("text", "") for x in payload.get("content", []) if x.get("type") == "text").strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        return json.loads(text)

    def create_plan(self, *, task: str, observation: str) -> dict[str, Any]:
        data = self._json("You are a browser-agent planner. Return valid JSON only with objective, steps, success_criteria. Use 3-7 site-agnostic outcome steps. Never invent selectors or routes. Never request credentials or secrets; assume the browser may already be logged in and inspect first. If login is required, the user performs it manually.", f"TASK:\n{task}\n\nSTARTING PAGE:\n{_compact_observation(observation, 4200)}", 350)
        return {"objective": str(data.get("objective", task)), "steps": [str(x) for x in data.get("steps", [])][:7], "success_criteria": [str(x) for x in data.get("success_criteria", [])][:7]}

    def verify_goal(self, *, task: str, observation: str, candidate_answer: str, history_summary: str = "") -> GoalVerification:
        data = self._json("You are a strict browser-agent verifier. Return valid JSON only with complete boolean, summary string, missing array. Every requested side effect must be visibly evidenced.", f"TASK:\n{task}\nCANDIDATE:\n{candidate_answer}\nLAST ACTION:\n{history_summary}\nPAGE:\n{_compact_observation(observation, 5000)}", 300)
        return GoalVerification(bool(data.get("complete")), str(data.get("summary", "")), [str(x) for x in data.get("missing", [])])

    def decide(self, *, task: str, observation: str, history: list[dict[str, str]], tools: list[dict[str, Any]]) -> ModelDecision:
        system = (
            "You are an autonomous browser agent. Complete the task with generic browser tools. "
            "Element refs are temporary and only valid for the current observation. Never invent refs, selectors, or routes. "
            "Choose ordinary reversible details yourself. Ask the user only for information they alone can provide, login/CAPTCHA, "
            "or consequential confirmation. Never request passwords, OTP/2FA codes, API keys, or other secrets; ask the user to authenticate manually in the browser. Assume the existing browser session may already be logged in and inspect it first. Never ask the user to click/focus/open/scroll/type an element that is present in CURRENT PAGE; use browser tools yourself. If an action fails, adapt using a fresh observation before escalating. Never navigate to the current URL repeatedly. The current observation is fresh. If complete, answer concisely."
        )
        anthropic_tools = [{"name": t["function"]["name"], "description": t["function"].get("description", ""), "input_schema": t["function"]["parameters"]} for t in tools]
        messages = [{"role": "user", "content": f"TASK:\n{task}\n\nCURRENT PAGE:\n{_compact_observation(observation)}"}]
        messages.extend(history)
        payload = self._request({"model": self.model, "max_tokens": 450, "system": system, "messages": messages, "tools": anthropic_tools, "tool_choice": {"type": "auto"}})
        for block in payload.get("content", []):
            if block.get("type") == "tool_use":
                return ModelDecision(kind="tool", name=block.get("name"), arguments=block.get("input") or {})
        text = "".join(x.get("text", "") for x in payload.get("content", []) if x.get("type") == "text").strip()
        return ModelDecision(kind="finish", text=text)


def get_provider_from_env(event_sink: Callable[[str, str], None] | None = None) -> BrowserLLMProvider:
    load_dotenv()
    name = os.getenv("BROWSER_AGENT_PROVIDER", "groq").strip().lower()
    if name == "openai":
        return OpenAIProvider(event_sink=event_sink)
    if name in {"anthropic", "claude"}:
        return AnthropicProvider(event_sink=event_sink)
    if name == "groq":
        return GroqProvider(model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"), event_sink=event_sink)
    raise RuntimeError(f"Unsupported BROWSER_AGENT_PROVIDER={name!r}; use openai, anthropic, or groq.")
