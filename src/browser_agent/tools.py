from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from browser_agent.observation import REF_ATTRIBUTE, observe_page


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    message: str
    observation: str | None = None
    evidence: dict[str, str] | None = None


class BrowserTools:
    """Generic resilient browser actions. No website-specific selectors live here."""

    def __init__(self, page: Page) -> None:
        self.page = page

    def _safe_observation(self) -> str | None:
        try:
            return observe_page(self.page).render()
        except Exception as exc:
            return f"(observation unavailable: {type(exc).__name__}: {exc})"

    def _after_action(self, message: str, evidence: dict[str, str] | None = None) -> ToolResult:
        if self.page.is_closed():
            return ToolResult(False, "Browser page closed during action")
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=1800)
        except PlaywrightTimeoutError:
            pass
        self.page.wait_for_timeout(350)
        return ToolResult(True, message, self._safe_observation(), evidence)

    def _element_by_ref(self, ref: str):
        if not ref.startswith("e") or not ref[1:].isdigit():
            raise ValueError(f"Invalid element ref: {ref}")
        locator = self.page.locator(f'[{REF_ATTRIBUTE}="{ref}"]')
        if locator.count() != 1:
            raise ValueError(f"Stale or missing element ref: {ref}; observe the page again")
        return locator

    def navigate(self, url: str) -> ToolResult:
        self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
        return self._after_action(f"Navigated to {url}")

    def click(self, ref: str) -> ToolResult:
        element = self._element_by_ref(ref)
        element.scroll_into_view_if_needed(timeout=3000)
        element.click(timeout=6000)
        return self._after_action(f"Clicked {ref}")

    def type_text(self, ref: str, text: str, *, clear: bool = True, submit: bool = False) -> ToolResult:
        element = self._element_by_ref(ref)
        element.scroll_into_view_if_needed(timeout=3000)
        element.focus()
        if clear:
            try:
                element.fill(text, timeout=5000)
            except Exception:
                element.press("Control+A")
                element.press_sequentially(text, delay=20, timeout=7000)
        else:
            element.press_sequentially(text, delay=20, timeout=7000)
        try:
            value = element.input_value(timeout=1000)
            if text and value != text:
                element.press("Control+A")
                element.press_sequentially(text, delay=25, timeout=7000)
        except Exception:
            pass
        if submit:
            element.press("Enter")
        return self._after_action(f"Typed into {ref}" + (" and submitted" if submit else ""))

    def press(self, ref: str, key: str) -> ToolResult:
        element = self._element_by_ref(ref)
        element.press(key, timeout=4000)
        return self._after_action(f"Pressed {key} on {ref}")

    def find_text(self, text: str) -> ToolResult:
        query = text.strip()
        if not query:
            return ToolResult(False, "find_text requires non-empty text", self._safe_observation())
        locator = self.page.get_by_text(query, exact=False).first
        if locator.count() == 0:
            return ToolResult(False, f"Text not found: {query}", self._safe_observation())
        locator.scroll_into_view_if_needed(timeout=4000)
        return self._after_action(f"Found and scrolled to text: {query}")

    def read_page(self, *, query: str = "", ref: str = "", max_chars: int = 1800) -> ToolResult:
        """Extract bounded semantic text and return it as durable agent evidence."""
        max_chars = min(max(int(max_chars), 300), 2400)
        raw = self.page.evaluate(
            r"""({query, ref, refAttr, maxChars}) => {
                const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
                const visible = el => {
                    if (!(el instanceof Element)) return false;
                    const style = getComputedStyle(el), rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden'
                        && Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0;
                };
                const roots = [document];
                for (let i = 0; i < roots.length; i++) {
                    const root = roots[i];
                    for (const node of root.querySelectorAll('*')) if (node.shadowRoot) roots.push(node.shadowRoot);
                }
                const findRef = value => {
                    for (const root of roots) {
                        const found = root.querySelector(`[${refAttr}="${CSS.escape(value)}"]`);
                        if (found) return found;
                    }
                    return null;
                };
                let blocks = [];
                if (ref) {
                    const target = findRef(ref);
                    if (!target) throw new Error(`Stale or missing element ref: ${ref}`);
                    const container = target.closest('article,section,li,form,main,div') || target;
                    blocks.push({text: clean(container.innerText || target.innerText || target.value), score: 1000, match: 0, top: 0});
                } else {
                    const selector = 'h1,h2,h3,h4,p,li,dt,dd,pre,code,blockquote,article';
                    const seen = new Set();
                    for (const root of roots) for (const el of root.querySelectorAll(selector)) {
                        if (!visible(el)) continue;
                        const text = clean(el.innerText || el.textContent);
                        if (text.length < 2 || seen.has(text)) continue;
                        seen.add(text);
                        const rect = el.getBoundingClientRect();
                        blocks.push({text, score: rect.bottom >= 0 && rect.top <= innerHeight ? 20 : 0, match: 0, top: rect.top + scrollY});
                    }
                }
                const needle = clean(query).toLowerCase();
                const tokens = needle.split(/\s+/).filter(x => x.length > 1);
                for (const block of blocks) {
                    const lower = block.text.toLowerCase();
                    if (needle && lower.includes(needle)) block.match += 200;
                    block.match += tokens.reduce((n, token) => n + (lower.includes(token) ? 15 : 0), 0);
                    block.score += block.match;
                }
                if (needle && !ref) blocks = blocks.filter(x => x.match > 0).sort((a,b) => b.score-a.score || a.top-b.top);
                else blocks.sort((a,b) => b.score-a.score || a.top-b.top);
                let content = '';
                for (const block of blocks) {
                    if (content.includes(block.text)) continue;
                    const next = content ? `${content}\n${block.text}` : block.text;
                    if (next.length > maxChars) {
                        if (!content) content = block.text.slice(0, maxChars);
                        break;
                    }
                    content = next;
                }
                if (!content) content = clean(document.body?.innerText).slice(0, maxChars);
                return {source_url: location.href, title: document.title, query: clean(query), content: content.slice(0, maxChars)};
            }""",
            {"query": query, "ref": ref, "refAttr": REF_ATTRIBUTE, "maxChars": max_chars},
        )
        if not raw.get("content"):
            return ToolResult(False, "No readable content found on the current page", self._safe_observation())
        evidence = {key: str(raw.get(key, "")) for key in ("source_url", "title", "query", "content")}
        label = f" for {query!r}" if query else ""
        return self._after_action(f"Read {len(evidence['content'])} characters from current page{label}", evidence)

    def scroll(self, amount: int = 700) -> ToolResult:
        self.page.mouse.wheel(0, amount)
        return self._after_action(f"Scrolled by {amount}px")

    def back(self) -> ToolResult:
        self.page.go_back(wait_until="domcontentloaded", timeout=10000)
        return self._after_action("Went back")

    def wait(self, milliseconds: int = 1000) -> ToolResult:
        self.page.wait_for_timeout(min(max(milliseconds, 100), 5000))
        return self._after_action(f"Waited {milliseconds}ms")

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            if name == "navigate": return self.navigate(str(arguments["url"]))
            if name == "click": return self.click(str(arguments["ref"]))
            if name == "type":
                return self.type_text(str(arguments["ref"]), str(arguments["text"]),
                    clear=bool(arguments.get("clear", True)), submit=bool(arguments.get("submit", False)))
            if name == "press": return self.press(str(arguments["ref"]), str(arguments["key"]))
            if name == "find_text": return self.find_text(str(arguments["text"]))
            if name == "read_page":
                return self.read_page(
                    query=str(arguments.get("query", "")),
                    ref=str(arguments.get("ref", "")),
                    max_chars=int(arguments.get("max_chars", 1800)),
                )
            if name == "scroll": return self.scroll(int(arguments.get("amount", 700)))
            if name == "back": return self.back()
            if name == "wait": return self.wait(int(arguments.get("milliseconds", 1000)))
            return ToolResult(False, f"Unknown tool: {name}")
        except Exception as exc:
            return ToolResult(False, f"{type(exc).__name__}: {exc}", self._safe_observation())
