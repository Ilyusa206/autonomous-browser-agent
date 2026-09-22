from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from browser_agent.observation import interactive_locator, observe_page


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    message: str
    observation: str | None = None


class BrowserTools:
    """Generic browser actions. No website-specific routes or selectors live here."""

    def __init__(self, page: Page) -> None:
        self.page = page

    def _after_action(self, message: str) -> ToolResult:
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=2500)
        except PlaywrightTimeoutError:
            pass
        self.page.wait_for_timeout(250)
        return ToolResult(ok=True, message=message, observation=observe_page(self.page).render())

    def _element_by_ref(self, ref: str):
        if not ref.startswith("e") or not ref[1:].isdigit():
            raise ValueError(f"Invalid element ref: {ref}")

        index = int(ref[1:]) - 1
        if index < 0:
            raise ValueError(f"Invalid element ref: {ref}")

        locator = interactive_locator(self.page).filter(visible=True).nth(index)
        if locator.count() == 0:
            raise ValueError(f"Element not found for ref: {ref}")
        return locator

    def navigate(self, url: str) -> ToolResult:
        self.page.goto(url, wait_until="domcontentloaded")
        return self._after_action(f"Navigated to {url}")

    def click(self, ref: str) -> ToolResult:
        element = self._element_by_ref(ref)
        element.click(timeout=5000)
        return self._after_action(f"Clicked {ref}")

    def type_text(self, ref: str, text: str, *, clear: bool = True) -> ToolResult:
        element = self._element_by_ref(ref)
        if clear:
            element.fill(text, timeout=5000)
        else:
            element.press_sequentially(text, delay=10, timeout=5000)
        return self._after_action(f"Typed into {ref}")

    def scroll(self, amount: int = 700) -> ToolResult:
        self.page.mouse.wheel(0, amount)
        return self._after_action(f"Scrolled by {amount}px")

    def back(self) -> ToolResult:
        self.page.go_back(wait_until="domcontentloaded")
        return self._after_action("Went back")

    def wait(self, milliseconds: int = 1000) -> ToolResult:
        self.page.wait_for_timeout(milliseconds)
        return self._after_action(f"Waited {milliseconds}ms")

    def read(self) -> ToolResult:
        return ToolResult(ok=True, message="Read current page", observation=observe_page(self.page).render())

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            if name == "navigate":
                return self.navigate(str(arguments["url"]))
            if name == "click":
                return self.click(str(arguments["ref"]))
            if name == "type":
                return self.type_text(
                    str(arguments["ref"]),
                    str(arguments["text"]),
                    clear=bool(arguments.get("clear", True)),
                )
            if name == "scroll":
                return self.scroll(int(arguments.get("amount", 700)))
            if name == "back":
                return self.back()
            if name == "wait":
                return self.wait(int(arguments.get("milliseconds", 1000)))
            if name == "read":
                return self.read()
            return ToolResult(ok=False, message=f"Unknown tool: {name}")
        except Exception as exc:
            return ToolResult(
                ok=False,
                message=f"{type(exc).__name__}: {exc}",
                observation=observe_page(self.page).render(),
            )
