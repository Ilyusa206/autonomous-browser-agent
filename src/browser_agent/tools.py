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


class BrowserTools:
    """Generic resilient browser actions. No website-specific selectors live here."""

    def __init__(self, page: Page) -> None:
        self.page = page

    def _safe_observation(self) -> str | None:
        try:
            return observe_page(self.page).render()
        except Exception as exc:
            return f"(observation unavailable: {type(exc).__name__}: {exc})"

    def _after_action(self, message: str) -> ToolResult:
        if self.page.is_closed():
            return ToolResult(False, "Browser page closed during action")
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=1800)
        except PlaywrightTimeoutError:
            pass
        self.page.wait_for_timeout(350)
        return ToolResult(True, message, self._safe_observation())

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
            if name == "scroll": return self.scroll(int(arguments.get("amount", 700)))
            if name == "back": return self.back()
            if name == "wait": return self.wait(int(arguments.get("milliseconds", 1000)))
            return ToolResult(False, f"Unknown tool: {name}")
        except Exception as exc:
            return ToolResult(False, f"{type(exc).__name__}: {exc}", self._safe_observation())
