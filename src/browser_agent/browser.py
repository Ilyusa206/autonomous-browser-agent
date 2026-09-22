from __future__ import annotations

from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright


class BrowserController:
    """Owns a visible Chromium persistent context."""

    def __init__(self, profile_dir: Path, start_url: str = "about:blank") -> None:
        self.profile_dir = profile_dir.expanduser().resolve()
        self.start_url = start_url
        self._playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def start(self) -> Page:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()

        self.context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=False,
            viewport={"width": 1440, "height": 900},
        )

        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

        if self.start_url != "about:blank":
            self.page.goto(self.start_url, wait_until="domcontentloaded")

        return self.page

    def wait_until_closed(self) -> None:
        if self.context is None:
            return

        while self.context.pages:
            self.context.pages[0].wait_for_timeout(250)

    def close(self) -> None:
        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                pass
            self.context = None

        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def __enter__(self) -> "BrowserController":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
