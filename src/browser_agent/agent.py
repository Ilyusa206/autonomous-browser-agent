from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from browser_agent.observation import observe_page
from browser_agent.provider import BrowserLLMProvider
from browser_agent.state import ActionRecord, AgentState, task_requires_evidence
from browser_agent.tools import BrowserTools, ToolResult


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "Navigate to a URL when another page/site is required. Never repeat navigation to the current URL.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Click a currently observed interactive element by its temporary ref.",
            "parameters": {"type": "object", "properties": {"ref": {"type": "string"}}, "required": ["ref"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type",
            "description": "Enter text into an observed input, textarea, searchbox, or contenteditable element.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "text": {"type": "string"},
                    "clear": {"type": "boolean"},
                    "submit": {"type": "boolean", "description": "Press Enter/submit after typing."},
                },
                "required": ["ref", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press",
            "description": "Press a keyboard key on an observed element, e.g. Enter, Escape, ArrowDown or Space.",
            "parameters": {
                "type": "object",
                "properties": {"ref": {"type": "string"}, "key": {"type": "string"}},
                "required": ["ref", "key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_text",
            "description": "Locate literal text and scroll it into view. This does NOT read/store evidence; follow with read_page.",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": (
                "Read and extract bounded, meaningful text from the current page into persistent evidence. "
                "Use query to focus on a target. For informational tasks this is required before finishing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Optional target phrase/topic to extract around."},
                    "ref": {"type": "string", "description": "Optional observed element ref to read with nearby context."},
                    "max_chars": {"type": "integer", "description": "Bounded output size, 300-2400 characters."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll vertically; positive is down and negative is up.",
            "parameters": {"type": "object", "properties": {"amount": {"type": "integer"}}},
        },
    },
    {"type": "function", "function": {"name": "back", "description": "Go back one page.", "parameters": {"type": "object", "properties": {}}}},
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait briefly for dynamic content to update.",
            "parameters": {"type": "object", "properties": {"milliseconds": {"type": "integer"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Pause only for login/CAPTCHA/manual permission or information genuinely unavailable to the agent. Never request secrets.",
            "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
        },
    },
]


@dataclass
class AgentRunResult:
    completed: bool
    message: str
    steps: int
    state: dict[str, Any] | None = None


class AutonomousAgent:
    def __init__(
        self,
        page,
        provider: BrowserLLMProvider,
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
        submit_like = name == "type" and bool(arguments.get("submit"))
        submit_like = submit_like or (name == "press" and str(arguments.get("key", "")).lower() in {"enter", "space"})
        if name != "click" and not submit_like:
            return None
        ref = str(arguments.get("ref", ""))
        element = next((item for item in observation.elements if item.get("ref") == ref), None)
        if not element:
            return None
        candidates = [element]
        if submit_like:
            candidates.extend(observation.elements)
        haystack = " ".join(
            str(item.get(key, "")) for item in candidates for key in ("text", "name", "placeholder", "href", "type")
        ).lower()
        risky_phrases = (
            "delete", "remove", "erase", "destroy", "pay", "purchase", "buy now", "place order",
            "confirm order", "send", "submit application", "send money", "transfer", "unsubscribe",
            "удалить", "оплатить", "купить", "оформить заказ", "подтвердить заказ", "отправить",
            "откликнуться", "перевести", "отписаться",
        )
        matched = next((phrase for phrase in risky_phrases if phrase in haystack), None)
        return f"Potentially destructive or consequential submission ({matched!r}) through {ref}" if matched else None

    def _confirm(self, reason: str) -> bool:
        self._emit("safety", f"[safety] confirmation required: {reason}")
        if self.confirm_callback:
            return self.confirm_callback(reason)
        return input("[safety] Continue? [y/N]: ").strip().lower() in {"y", "yes"}

    def _ask_user(self, question: str) -> str:
        self._emit("user_input", f"[user] {question}")
        answer = self.ask_user_callback(question) if self.ask_user_callback else input(f"[user] {question}\n> ").strip()
        self._emit("user_answer", "[user] User completed the requested manual step.")
        return answer

    def _debug_fail_once(self, name: str) -> bool:
        import os

        if os.getenv("BROWSER_AGENT_DEBUG_FAIL_ONCE") != "1" or getattr(self, "_debug_failure_injected", False):
            return False
        if name not in {"click", "type"}:
            return False
        self._debug_failure_injected = True
        return True

    def run(self, task: str) -> AgentRunResult:
        state = AgentState(objective=task)
        consecutive_errors = 0

        for step in range(1, self.max_steps + 1):
            page_observation = observe_page(self.page)
            observation = page_observation.render()
            before_fingerprint = state.observe(observation)
            self._emit("thinking", f"[agent] step {step}/{self.max_steps}: {state.current_subgoal}")
            decision = self.provider.decide(
                task=task,
                observation=observation,
                history=[{"role": "user", "content": state.render()}],
                tools=TOOL_SCHEMAS,
            )

            if decision.kind == "finish":
                message = decision.text or "Task complete."
                if task_requires_evidence(task) and not state.evidence:
                    state.reject_finish(["Extract relevant page content with read_page before finishing"], "Informational result has no grounded evidence")
                    self._emit("recovery", "[verifier] informational completion rejected before verifier: no extracted evidence")
                    if state.finish_rejections >= 3:
                        return AgentRunResult(False, "Stopped after repeated ungrounded completion attempts.", step, state.to_dict())
                    continue
                verification = self.provider.verify_goal(
                    task=task,
                    observation=observation,
                    candidate_answer=message,
                    history_summary=state.render(),
                )
                if verification.complete:
                    self._emit("finish", f"[agent] finish: {message}")
                    return AgentRunResult(True, message, step, state.to_dict())
                state.reject_finish(verification.missing, verification.summary)
                self._emit("recovery", "[verifier] not complete: " + "; ".join(verification.missing or [verification.summary]))
                if state.finish_rejections >= 3:
                    message = "Stopped after 3 rejected finish attempts: " + (state.verifier_feedback[-1] if state.verifier_feedback else "insufficient evidence")
                    return AgentRunResult(False, message, step, state.to_dict())
                continue

            if not decision.name:
                state.failures.append("Provider returned a tool decision without a tool name")
                continue
            arguments = decision.arguments or {}
            self._emit("tool", f"[agent] tool: {decision.name} {arguments}")

            if decision.name == "ask_user":
                question = str(arguments.get("question", "")).strip() or "Complete the required manual browser step, then continue."
                if any(secret in question.lower() for secret in ("password", "парол", "otp", "2fa", "api key", "код подтверждения")):
                    question = "Complete authentication manually in the browser. Do not share passwords or verification codes with the agent."
                self._ask_user(question)
                state.recent_actions.append(ActionRecord("ask_user", json.dumps(arguments, ensure_ascii=False), True, "Manual step completed", before_fingerprint, before_fingerprint, False))
                continue

            risk_reason = self._risk_reason(decision.name, arguments, page_observation)
            if risk_reason and not self._confirm(risk_reason):
                message = "Stopped before a potentially destructive action because user confirmation was not granted."
                self._emit("safety", f"[safety] {message}")
                return AgentRunResult(False, message, step, state.to_dict())

            if self._debug_fail_once(decision.name):
                result = ToolResult(False, "Injected transient browser failure for recovery demonstration", observation)
            else:
                result = self.tools.execute(decision.name, arguments)
            self._emit("result", f"[agent] result: ok={result.ok} {result.message}")
            after_observation = result.observation or observe_page(self.page).render()
            state.ingest_result(
                {
                    "tool": decision.name,
                    "arguments": arguments,
                    "ok": result.ok,
                    "message": result.message,
                    "evidence": result.evidence,
                    "before_fingerprint": before_fingerprint,
                },
                after_observation=after_observation,
            )
            if result.evidence:
                self._emit("evidence", f"[evidence] saved from {result.evidence.get('source_url', '')}: {result.evidence.get('title', '')}")
            if result.ok:
                consecutive_errors = 0
            else:
                consecutive_errors += 1
                self._emit("recovery", f"[recovery] action failed ({consecutive_errors}/3); using fresh observation and failure memory")
                if consecutive_errors >= 3:
                    message = "Stopped after 3 consecutive browser-action failures."
                    return AgentRunResult(False, message, step, state.to_dict())
            repeated = state.repeated_no_progress_signature()
            if repeated:
                self._emit("recovery", f"[recovery] {repeated} repeated without progress; strategy change required")

        message = f"Stopped after max_steps={self.max_steps} without a verified final answer."
        self._emit("finish", f"[agent] {message}")
        return AgentRunResult(False, message, self.max_steps, state.to_dict())
