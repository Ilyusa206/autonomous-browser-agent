from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any


MAX_EVIDENCE = 12
MAX_EVIDENCE_CHARS = 1800
MAX_RECENT_ACTIONS = 10
MAX_VISITED_PAGES = 12
MAX_FAILURES = 8
MAX_COMPLETED_SUBGOALS = 10
MAX_VERIFIER_FEEDBACK = 5


def task_requires_evidence(task: str) -> bool:
    """Conservative, site-agnostic signal that the user requested an informational result."""
    lowered = f" {task.lower()} "
    phrases = (
        " explain", " summarize", " analyse", " analyze", " compare", " research", " read ",
        " find information", " tell me", " what is", " who is", " why ", " how ",
        " объясни", " расскажи", " кратко", " прочитай", " изучи", " найди информацию",
        " проанализ", " сравни", " для чего", " зачем", " что такое", " кто ", " почему", " как ",
    )
    return any(phrase in lowered for phrase in phrases)


def _clean(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def observation_identity(observation: str) -> tuple[str, str, str]:
    url_match = re.search(r"^URL:\s*(.*)$", observation, re.MULTILINE)
    title_match = re.search(r"^TITLE:\s*(.*)$", observation, re.MULTILINE)
    url = _clean(url_match.group(1) if url_match else "", 500)
    title = _clean(title_match.group(1) if title_match else "", 300)
    interactive = observation.partition("INTERACTIVE ELEMENTS:")[2]
    for marker in ("\nVIEWPORT TEXT:\n", "\nVISIBLE TEXT:\n", "\nPAGE START:\n"):
        interactive = interactive.partition(marker)[0]
    text_sections: list[str] = []
    for marker in ("\nVIEWPORT TEXT:\n", "\nVISIBLE TEXT:\n", "\nPAGE START:\n"):
        if marker in observation:
            section = observation.partition(marker)[2]
            for later in ("\nVIEWPORT TEXT:\n", "\nVISIBLE TEXT:\n", "\nPAGE START:\n"):
                if later != marker and later in section:
                    section = section.partition(later)[0]
            text_sections.append(section[:2200])
    stable = f"{url}\n{title}\n{interactive[:2200]}\n" + "\n".join(text_sections)
    stable = re.sub(r"\[e\d+\]", "[ref]", stable)
    stable = re.sub(r"\s+", " ", stable).strip()[:7000]
    fingerprint = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]
    return url, title, fingerprint


@dataclass
class Evidence:
    id: str
    source_url: str
    title: str
    content: str
    query: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Evidence":
        source_url = _clean(data.get("source_url") or data.get("url"), 500)
        title = _clean(data.get("title") or data.get("target"), 240)
        content = _clean(data.get("content") or data.get("text"), MAX_EVIDENCE_CHARS)
        query = _clean(data.get("query"), 240)
        digest = hashlib.sha256(f"{source_url}\n{content.lower()}".encode("utf-8")).hexdigest()[:12]
        return cls(id=str(data.get("id") or f"ev-{digest}"), source_url=source_url, title=title, content=content, query=query)


@dataclass
class ActionRecord:
    tool: str
    arguments: str
    ok: bool
    message: str
    before: str = ""
    after: str = ""
    progress: bool = False

    @property
    def signature(self) -> str:
        return f"{self.tool} {self.arguments}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActionRecord":
        arguments = data.get("arguments", "{}")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        return cls(
            tool=_clean(data.get("tool"), 80),
            arguments=_clean(arguments, 600),
            ok=bool(data.get("ok")),
            message=_clean(data.get("message"), 500),
            before=_clean(data.get("before"), 40),
            after=_clean(data.get("after"), 40),
            progress=bool(data.get("progress")),
        )


@dataclass
class AgentState:
    objective: str
    current_subgoal: str = "Inspect the current page and choose the first useful action"
    completed_subgoals: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    visited_pages: list[dict[str, str]] = field(default_factory=list)
    recent_actions: list[ActionRecord] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    remaining_work: list[str] = field(default_factory=list)
    verifier_feedback: list[str] = field(default_factory=list)
    current_fingerprint: str = ""
    no_progress_count: int = 0
    finish_rejections: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, *, objective: str) -> "AgentState":
        data = data or {}
        state = cls(
            objective=_clean(data.get("objective") or objective, 2000),
            current_subgoal=_clean(data.get("current_subgoal") or "Inspect the current page and choose the first useful action", 500),
            completed_subgoals=[_clean(x, 500) for x in data.get("completed_subgoals", [])][-MAX_COMPLETED_SUBGOALS:],
            evidence=[Evidence.from_dict(x) for x in data.get("evidence", []) if isinstance(x, dict)][-MAX_EVIDENCE:],
            visited_pages=[
                {"url": _clean(x.get("url"), 500), "title": _clean(x.get("title"), 240), "fingerprint": _clean(x.get("fingerprint"), 40)}
                for x in data.get("visited_pages", []) if isinstance(x, dict)
            ][-MAX_VISITED_PAGES:],
            recent_actions=[ActionRecord.from_dict(x) for x in data.get("recent_actions", []) if isinstance(x, dict)][-MAX_RECENT_ACTIONS:],
            failures=[_clean(x, 500) for x in data.get("failures", [])][-MAX_FAILURES:],
            remaining_work=[_clean(x, 500) for x in data.get("remaining_work", [])][:8],
            verifier_feedback=[_clean(x, 700) for x in data.get("verifier_feedback", [])][-MAX_VERIFIER_FEEDBACK:],
            current_fingerprint=_clean(data.get("current_fingerprint"), 40),
            no_progress_count=min(max(int(data.get("no_progress_count", 0)), 0), 20),
            finish_rejections=min(max(int(data.get("finish_rejections", 0)), 0), 10),
        )
        state._deduplicate_evidence()
        return state

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def observe(self, observation: str) -> str:
        url, title, fingerprint = observation_identity(observation)
        self.current_fingerprint = fingerprint
        if url:
            entry = {"url": url, "title": title, "fingerprint": fingerprint}
            if not self.visited_pages or self.visited_pages[-1] != entry:
                self.visited_pages.append(entry)
                self.visited_pages = self.visited_pages[-MAX_VISITED_PAGES:]
        return fingerprint

    def add_evidence(self, raw: dict[str, Any] | Evidence) -> bool:
        item = raw if isinstance(raw, Evidence) else Evidence.from_dict(raw)
        if not item.content:
            return False
        if any(existing.id == item.id for existing in self.evidence):
            return False
        self.evidence.append(item)
        self.evidence = self.evidence[-MAX_EVIDENCE:]
        self.no_progress_count = 0
        return True

    def ingest_result(self, result: dict[str, Any] | None, *, after_observation: str) -> bool:
        if not result:
            self.observe(after_observation)
            return False
        before = _clean(result.get("before_fingerprint") or self.current_fingerprint, 40)
        after = self.observe(after_observation)
        new_evidence = False
        evidence = result.get("evidence")
        if isinstance(evidence, dict):
            new_evidence = self.add_evidence(evidence)
        elif isinstance(evidence, list):
            for item in evidence:
                if isinstance(item, dict) and self.add_evidence(item):
                    new_evidence = True
        tool = _clean(result.get("tool"), 80)
        arguments = result.get("arguments") or {}
        arguments_text = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        ok = bool(result.get("ok"))
        message = _clean(result.get("message"), 500)
        changed_page = bool(before and after and before != after)
        progress = ok and (changed_page or new_evidence)
        record = ActionRecord(tool, arguments_text, ok, message, before, after, progress)
        self.recent_actions.append(record)
        self.recent_actions = self.recent_actions[-MAX_RECENT_ACTIONS:]
        if not ok:
            self.failures.append(f"{record.signature}: {message}")
            self.failures = self.failures[-MAX_FAILURES:]
            self.no_progress_count += 1
            self.current_subgoal = "Recover from the last failure using the fresh page state and a different strategy"
        elif progress:
            self.no_progress_count = 0
            if new_evidence:
                self.current_subgoal = "Use accumulated evidence to complete or continue the requested task"
                latest = self.evidence[-1]
                completed = f"Read {latest.title or latest.source_url}"
                if completed not in self.completed_subgoals:
                    self.completed_subgoals.append(completed)
            elif tool in {"navigate", "back"}:
                self.current_subgoal = "Inspect the destination and identify the next task-relevant action"
                if self.visited_pages:
                    latest_page = self.visited_pages[-1]
                    completed = f"Reached {latest_page.get('title') or latest_page.get('url')}"
                    if completed not in self.completed_subgoals:
                        self.completed_subgoals.append(completed)
            else:
                self.current_subgoal = "Inspect the updated interface and continue toward the objective"
            if tool == "find_text":
                self.current_subgoal = "Read the located content with read_page; locating text alone is not evidence"
        else:
            self.no_progress_count += 1
            if tool == "find_text":
                self.current_subgoal = "Read the located content with read_page or choose another extraction strategy"
            else:
                self.current_subgoal = "Choose a different action because the last one produced no observable progress"
        self.completed_subgoals = self.completed_subgoals[-MAX_COMPLETED_SUBGOALS:]
        return progress

    def repeated_no_progress_signature(self) -> str | None:
        if len(self.recent_actions) < 2:
            return None
        a, b = self.recent_actions[-2:]
        if a.signature == b.signature and not a.progress and not b.progress:
            return a.signature
        return None

    def reject_finish(self, missing: list[str], summary: str = "") -> None:
        self.finish_rejections += 1
        feedback = "; ".join(_clean(x, 300) for x in missing if x)
        if summary:
            feedback = f"{_clean(summary, 350)}. {feedback}".strip(". ")
        self.verifier_feedback.append(feedback or "Verifier found insufficient grounded evidence")
        self.verifier_feedback = self.verifier_feedback[-MAX_VERIFIER_FEEDBACK:]
        self.remaining_work = [_clean(x, 500) for x in missing if x][:8]
        if self.remaining_work:
            self.current_subgoal = self.remaining_work[0]

    def render(self, limit: int = 7000) -> str:
        head = [
            "AGENT STATE (bounded, authoritative across steps):",
            f"OBJECTIVE: {self.objective}",
            f"CURRENT SUBGOAL: {self.current_subgoal}",
        ]
        if self.completed_subgoals:
            head.append("COMPLETED: " + " | ".join(self.completed_subgoals[-5:]))
        if self.remaining_work:
            head.append("REMAINING: " + " | ".join(self.remaining_work[:6]))
        evidence_lines: list[str] = []
        if self.evidence:
            evidence_lines.append("EVIDENCE (use it; do not rediscover it):")
            for item in self.evidence[-8:]:
                evidence_lines.append(
                    f"- [{item.id}] {item.title or 'Extracted page content'} @ {item.source_url}: {item.content[:900]}"
                )
        else:
            evidence_lines.append("EVIDENCE: none yet. For informational tasks, use read_page before finishing.")
        tail: list[str] = []
        if self.visited_pages:
            tail.append("VISITED: " + " -> ".join(x["url"] for x in self.visited_pages[-6:] if x.get("url")))
        if self.recent_actions:
            tail.append("RECENT ACTIONS:")
            for action in self.recent_actions[-6:]:
                flag = "progress" if action.progress else "no-progress"
                tail.append(f"- {action.signature} -> ok={action.ok}, {flag}: {action.message}")
        repeated = self.repeated_no_progress_signature()
        if repeated:
            tail.append(f"RECOVERY DIRECTIVE: {repeated} repeated without state/evidence change. Choose a different strategy/tool.")
        if self.failures:
            tail.append("FAILURES: " + " | ".join(self.failures[-4:]))
        if self.verifier_feedback:
            tail.append("VERIFIER FEEDBACK: " + " | ".join(self.verifier_feedback[-3:]))
        tail.append(f"NO-PROGRESS COUNT: {self.no_progress_count}; FINISH REJECTIONS: {self.finish_rejections}")

        head_text = "\n".join(head)
        tail_text = "\n".join(tail)
        available = max(0, limit - len(head_text) - len(tail_text) - 2)
        evidence_text = "\n".join(evidence_lines)[:available]
        rendered = "\n".join(part for part in (head_text, evidence_text, tail_text) if part)
        if len(rendered) <= limit:
            return rendered
        # In pathological input, preserve the objective head and the recovery tail.
        tail_budget = min(len(tail_text), max(300, limit // 3))
        return (head_text[: max(0, limit - tail_budget - 1)] + "\n" + tail_text[-tail_budget:])[:limit]

    def _deduplicate_evidence(self) -> None:
        unique: list[Evidence] = []
        seen: set[str] = set()
        for item in self.evidence:
            if item.id in seen or not item.content:
                continue
            seen.add(item.id)
            unique.append(item)
        self.evidence = unique[-MAX_EVIDENCE:]
