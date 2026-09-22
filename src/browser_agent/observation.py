from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Page


@dataclass(frozen=True)
class PageObservation:
    url: str
    title: str
    text: str
    elements: list[dict[str, str]]

    def render(self) -> str:
        lines = [f"URL: {self.url}", f"TITLE: {self.title}", "", "INTERACTIVE ELEMENTS:"]
        if self.elements:
            for element in self.elements:
                details = " | ".join(
                    f"{key}={value!r}"
                    for key, value in element.items()
                    if key != "ref" and value
                )
                lines.append(f"[{element['ref']}] {details}")
        else:
            lines.append("(none)")

        lines.extend(["", "VISIBLE TEXT:", self.text or "(none)"])
        return "\n".join(lines)


def observe_page(page: Page, *, max_text_chars: int = 6000, max_elements: int = 80) -> PageObservation:
    """Return a compact, LLM-friendly snapshot instead of the full page HTML."""

    raw = page.locator("body").evaluate(
        """(body, maxElements) => {
            const candidates = Array.from(body.querySelectorAll(
                'a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"]'
            ));

            const visible = candidates.filter((el) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.visibility !== 'hidden'
                    && style.display !== 'none'
                    && rect.width > 0
                    && rect.height > 0;
            }).slice(0, maxElements);

            return {
                text: (body.innerText || '').replace(/\\s+/g, ' ').trim(),
                elements: visible.map((el, index) => ({
                    ref: 'e' + (index + 1),
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    type: el.getAttribute('type') || '',
                    text: (el.innerText || el.value || '').replace(/\\s+/g, ' ').trim().slice(0, 180),
                    name: el.getAttribute('aria-label') || el.getAttribute('name') || '',
                    placeholder: el.getAttribute('placeholder') || '',
                    href: el.tagName.toLowerCase() === 'a' ? (el.getAttribute('href') || '') : ''
                }))
            };
        }""",
        max_elements,
    )

    return PageObservation(
        url=page.url,
        title=page.title(),
        text=raw["text"][:max_text_chars],
        elements=raw["elements"],
    )
