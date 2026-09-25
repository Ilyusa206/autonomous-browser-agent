from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import requests
from dotenv import load_dotenv


EXECUTOR_PROMPT = (
    "You are an autonomous browser agent. Complete the user's whole task with generic tools. "
    "CURRENT PAGE is the fresh UI snapshot; refs are temporary and must never be invented. "
    "AGENT STATE is bounded durable memory and remains authoritative across steps. Use saved EVIDENCE instead of rediscovering facts. "
    "find_text only locates text; read_page reads it and stores grounded evidence. For informational tasks, call read_page before finishing. "
    "Choose ordinary reversible details yourself. Ask the user only for login/CAPTCHA/manual permission or genuinely unavailable information, never passwords, OTP/2FA codes, API keys, or other secrets. "
    "Treat existing account-specific UI as an authenticated session. Use observed controls yourself. Adapt after failures and obey recovery directives. "
    "Do not repeat a no-progress action or revisit a page without a concrete reason. Finish only when the complete requested result is supported by accumulated evidence/state."
)

VERIFIER_PROMPT = (
    "You are a strict browser-agent verifier. Check the entire task against CURRENT PAGE and AGENT STATE, especially accumulated EVIDENCE. "
    "A claim in the candidate answer is not evidence. Informational answers require extracted evidence; workflows require observable final-state evidence. "
    "Return JSON only: complete boolean, summary string, missing array."
)


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
    """Hard-bound an observation while preserving page identity and useful evidence."""
    if limit <= 0:
        return ""
    if len(observation) <= limit:
        return observation

    marker = next(
        (candidate for candidate in ("\nVIEWPORT TEXT:\n", "\nVISIBLE TEXT:\n", "\nPAGE START:\n") if candidate in observation),
        "",
    )
    head, sep, visible_text = observation.partition(marker) if marker else (observation, "", "")
    if not sep:
        return observation[:limit]

    # Interactive-element dumps can themselves exceed the budget. Keep the
    # beginning (URL/title + highest-ranked elements) and reserve space for
    # visible page evidence instead of allowing the header to overflow.
    text_budget = min(1200, max(300, limit // 4))
    head_budget = max(0, limit - len(marker) - text_budget)
    compact = head[:head_budget] + marker + visible_text[:text_budget]
    return compact[:limit]


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

    def _post_with_rate_limit_retry(self, *, phase: str = "request", **kwargs):
        """Retry transient Groq rate limits without turning them into browser-action failures."""
        payload = kwargs.get("json") or {}
        prompt_chars = sum(len(str(item.get("content", ""))) for item in payload.get("messages", []))
        tool_chars = len(json.dumps(payload.get("tools", []), ensure_ascii=False))
        started = time.perf_counter()
        print(f"[provider] {phase} start provider={type(self).__name__} model={self.model} prompt_chars={prompt_chars} tool_chars={tool_chars}")
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            response = requests.post(self.endpoint, **kwargs)
            if response.status_code != 429 or attempt == max_attempts:
                elapsed = time.perf_counter() - started
                print(f"[provider] {phase} done provider={type(self).__name__} model={self.model} status={response.status_code} duration={elapsed:.2f}s")
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
            {"role": "system", "content": "Plan a browser task. Return JSON only with objective, steps, success_criteria. Use 2-5 short outcome steps. No selectors, invented routes, credentials, OTPs, API keys, or secrets. Existing sessions may already be authenticated; inspect before assuming login is needed."},
            {"role": "user", "content": f"TASK:\n{task}\n\nSTARTING PAGE:\n{_compact_observation(observation, 900)}"},
        ]
        response = self._post_with_rate_limit_retry(phase="plan", headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json={"model": self.model, "messages": messages, "response_format": {"type": "json_object"}, "stream": False, "max_completion_tokens": 350, "reasoning_effort": "low"}, timeout=90)
        if not response.ok:
            raise RuntimeError(f"Groq planner error {response.status_code}: {response.text[:500]}")
        data = json.loads(response.json()["choices"][0]["message"].get("content") or "{}")
        return {"objective": str(data.get("objective", task)), "steps": [str(x) for x in data.get("steps", [])][:7], "success_criteria": [str(x) for x in data.get("success_criteria", [])][:7]}

    def verify_goal(self, *, task: str, observation: str, candidate_answer: str, history_summary: str = "") -> GoalVerification:
        messages = [
            {"role": "system", "content": VERIFIER_PROMPT},
            {"role": "user", "content": f"TASK:\n{task}\n\nCANDIDATE:\n{candidate_answer}\n\nLAST ACTION:\n{history_summary}\n\nPAGE:\n{_compact_observation(observation, 5000)}"},
        ]
        response = self._post_with_rate_limit_retry(phase="verify", headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json={"model": self.model, "messages": messages, "response_format": {"type": "json_object"}, "stream": False, "max_completion_tokens": 300, "reasoning_effort": "low"}, timeout=90)
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
                "content": EXECUTOR_PROMPT,
            },
            {
                "role": "user",
                "content": f"TASK:\n{task}\n\nCURRENT PAGE:\n{_compact_observation(observation)}",
            },
        ]
        messages.extend(history)

        response = self._post_with_rate_limit_retry(
            phase="decision",
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


class OllamaProvider(GroqProvider):
    """Local Ollama runtime using its native /api/chat contract."""

    default_base_url = "http://127.0.0.1:11434"

    def __init__(self, model: str | None = None, event_sink: Callable[[str, str], None] | None = None) -> None:
        load_dotenv()
        self.api_key = "ollama"
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen3:8b")
        self.event_sink = event_sink
        configured = os.getenv("OLLAMA_BASE_URL", self.default_base_url).rstrip("/")
        if configured.endswith("/v1"):
            configured = configured[:-3]
        if configured.endswith("/chat/completions"):
            configured = configured[: -len("/chat/completions")]
            if configured.endswith("/v1"):
                configured = configured[:-3]
        self.endpoint = configured.rstrip("/") + "/api/chat"

    def _native_request(self, payload: dict[str, Any], *, timeout: int = 90, phase: str = "request") -> dict[str, Any]:
        body = dict(payload)
        body["model"] = self.model
        body["stream"] = False
        body["think"] = False
        started = time.perf_counter()
        prompt_chars = sum(len(str(m.get("content", ""))) for m in body.get("messages", []))
        tool_chars = len(json.dumps(body.get("tools", []), ensure_ascii=False))
        print(f"[ollama] {phase} start model={self.model} prompt_chars={prompt_chars} tool_chars={tool_chars}")
        try:
            response = requests.post(self.endpoint, json=body, timeout=timeout)
        except requests.Timeout as exc:
            raise RuntimeError(
                f"Local Ollama timed out while running {self.model}. "
                "The model may be too slow for the current browser-agent request."
            ) from exc
        except requests.ConnectionError as exc:
            raise RuntimeError(
                "Cannot connect to local Ollama. Start it first and make sure the configured model is pulled."
            ) from exc
        elapsed = time.perf_counter() - started
        if not response.ok:
            raise RuntimeError(f"Ollama API error {response.status_code}: {response.text[:500]}")
        data = response.json()
        print(
            f"[ollama] {phase} done {elapsed:.2f}s "
            f"prompt_eval={data.get('prompt_eval_count', '?')} eval={data.get('eval_count', '?')}"
        )
        return data

    def create_plan(self, *, task: str, observation: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": "You are a browser-agent planner. Return JSON only: objective string, steps array, success_criteria array. Make 3-7 site-agnostic outcome steps. Never invent selectors or routes. Never plan to request, collect, or type passwords, OTP/2FA codes, API keys, or other secrets. Assume an existing browser session may already be authenticated; inspect it first. If authentication is actually required, plan for the user to complete login manually."},
            {"role": "user", "content": f"TASK:\n{task}\n\nSTARTING PAGE:\n{_compact_observation(observation, 4200)}"},
        ]
        payload = self._native_request(
            {
                "messages": messages,
                "format": "json",
                "options": {"num_predict": 220},
            },
            phase="plan",
        )
        content = payload.get("message", {}).get("content") or ""
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            print("[ollama] plan JSON was truncated/malformed; using a compact fallback plan")
            return {
                "objective": task,
                "steps": ["Inspect the current page", "Take the next browser action from observed evidence", "Verify the requested result"],
                "success_criteria": ["The requested result is supported by the final page observation"],
            }
        return {
            "objective": str(data.get("objective", task)),
            "steps": [str(x) for x in data.get("steps", [])][:7],
            "success_criteria": [str(x) for x in data.get("success_criteria", [])][:7],
        }

    def verify_goal(self, *, task: str, observation: str, candidate_answer: str, history_summary: str = "") -> GoalVerification:
        messages = [
            {"role": "system", "content": VERIFIER_PROMPT},
            {"role": "user", "content": f"TASK:\n{task}\n\nCANDIDATE:\n{candidate_answer}\n\nLAST ACTION:\n{history_summary}\n\nPAGE:\n{_compact_observation(observation, 5000)}"},
        ]
        payload = self._native_request(
            {
                "messages": messages,
                "format": "json",
                "options": {"num_predict": 160},
            },
            phase="verify",
        )
        data = json.loads(payload.get("message", {}).get("content") or "{}")
        return GoalVerification(
            bool(data.get("complete")),
            str(data.get("summary", "")),
            [str(x) for x in data.get("missing", [])],
        )

    def decide(
        self,
        *,
        task: str,
        observation: str,
        history: list[dict[str, str]],
        tools: list[dict[str, Any]],
    ) -> ModelDecision:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": EXECUTOR_PROMPT,
            },
            {"role": "user", "content": f"TASK:\n{task}\n\nCURRENT PAGE:\n{_compact_observation(observation, 3600)}"},
        ]
        messages.extend(history)
        payload = self._native_request(
            {
                "messages": messages,
                "tools": tools,
                "options": {"num_predict": 220},
            },
            phase="decision",
        )
        message = payload.get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            function = tool_calls[0].get("function") or {}
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            return ModelDecision(kind="tool", name=function.get("name"), arguments=arguments)
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

    def _post_with_rate_limit_retry(self, *, phase: str = "request", **kwargs):
        return super()._post_with_rate_limit_retry(phase=phase, **kwargs)

    def create_plan(self, *, task: str, observation: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": "You are a browser-agent planner. Return JSON only: objective string, steps array, success_criteria array. Make 3-7 site-agnostic outcome steps. Never invent selectors or routes."},
            {"role": "user", "content": f"TASK:\n{task}\n\nSTARTING PAGE:\n{_compact_observation(observation, 4200)}"},
        ]
        response = self._post_with_rate_limit_retry(phase="plan", headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json={"model": self.model, "messages": messages, "response_format": {"type": "json_object"}, "max_completion_tokens": 300}, timeout=90)
        if not response.ok:
            raise RuntimeError(f"OpenAI planner error {response.status_code}: {response.text[:500]}")
        data = json.loads(response.json()["choices"][0]["message"].get("content") or "{}")
        return {"objective": str(data.get("objective", task)), "steps": [str(x) for x in data.get("steps", [])][:7], "success_criteria": [str(x) for x in data.get("success_criteria", [])][:7]}

    def verify_goal(self, *, task: str, observation: str, candidate_answer: str, history_summary: str = "") -> GoalVerification:
        messages = [
            {"role": "system", "content": VERIFIER_PROMPT},
            {"role": "user", "content": f"TASK:\n{task}\n\nCANDIDATE:\n{candidate_answer}\n\nLAST ACTION:\n{history_summary}\n\nPAGE:\n{_compact_observation(observation, 5000)}"},
        ]
        response = self._post_with_rate_limit_retry(phase="verify", headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json={"model": self.model, "messages": messages, "response_format": {"type": "json_object"}, "max_completion_tokens": 250}, timeout=90)
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
        self.model = model or os.getenv("ANTHROPIC_MODEL", "").strip()
        if not self.model:
            raise RuntimeError("ANTHROPIC_MODEL is missing. Set an official model ID available to your Anthropic account.")
        self.event_sink = event_sink

    @property
    def headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    def _request(self, payload: dict[str, Any], *, phase: str = "request") -> dict[str, Any]:
        started = time.perf_counter()
        prompt_chars = sum(len(str(item.get("content", ""))) for item in payload.get("messages", []))
        tool_chars = len(json.dumps(payload.get("tools", []), ensure_ascii=False))
        print(f"[provider] {phase} start provider=AnthropicProvider model={self.model} prompt_chars={prompt_chars} tool_chars={tool_chars}")
        response = requests.post(self.endpoint, headers=self.headers, json=payload, timeout=90)
        elapsed = time.perf_counter() - started
        print(f"[provider] {phase} done provider=AnthropicProvider model={self.model} status={response.status_code} duration={elapsed:.2f}s")
        if not response.ok:
            raise RuntimeError(f"Anthropic API error {response.status_code}: {response.text[:500]}")
        return response.json()

    def _json(self, system: str, user: str, max_tokens: int) -> dict[str, Any]:
        payload = self._request({"model": self.model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]}, phase="structured")
        text = "".join(x.get("text", "") for x in payload.get("content", []) if x.get("type") == "text").strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        return json.loads(text)

    def create_plan(self, *, task: str, observation: str) -> dict[str, Any]:
        data = self._json("You are a browser-agent planner. Return valid JSON only with objective, steps, success_criteria. Use 3-7 site-agnostic outcome steps. Never invent selectors or routes. Never request credentials or secrets; assume the browser may already be logged in and inspect first. If login is required, the user performs it manually.", f"TASK:\n{task}\n\nSTARTING PAGE:\n{_compact_observation(observation, 4200)}", 350)
        return {"objective": str(data.get("objective", task)), "steps": [str(x) for x in data.get("steps", [])][:7], "success_criteria": [str(x) for x in data.get("success_criteria", [])][:7]}

    def verify_goal(self, *, task: str, observation: str, candidate_answer: str, history_summary: str = "") -> GoalVerification:
        data = self._json(VERIFIER_PROMPT, f"TASK:\n{task}\nCANDIDATE:\n{candidate_answer}\nAGENT STATE:\n{history_summary}\nPAGE:\n{_compact_observation(observation, 5000)}", 300)
        return GoalVerification(bool(data.get("complete")), str(data.get("summary", "")), [str(x) for x in data.get("missing", [])])

    def decide(self, *, task: str, observation: str, history: list[dict[str, str]], tools: list[dict[str, Any]]) -> ModelDecision:
        system = EXECUTOR_PROMPT
        anthropic_tools = [{"name": t["function"]["name"], "description": t["function"].get("description", ""), "input_schema": t["function"]["parameters"]} for t in tools]
        messages = [{"role": "user", "content": f"TASK:\n{task}\n\nCURRENT PAGE:\n{_compact_observation(observation)}"}]
        messages.extend(history)
        payload = self._request({"model": self.model, "max_tokens": 450, "system": system, "messages": messages, "tools": anthropic_tools, "tool_choice": {"type": "auto"}}, phase="decision")
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
    if name in {"ollama", "local"}:
        return OllamaProvider(event_sink=event_sink)
    if name in {"anthropic", "claude"}:
        return AnthropicProvider(event_sink=event_sink)
    if name == "groq":
        return GroqProvider(model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"), event_sink=event_sink)
    raise RuntimeError(f"Unsupported BROWSER_AGENT_PROVIDER={name!r}; use openai, anthropic, ollama, or groq.")
