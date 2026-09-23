from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Locator, Page


INTERACTIVE_SELECTOR = (
    'a,button,input,textarea,select,[role="button"],[role="link"],[role="menuitem"],'
    '[role="option"],[role="tab"],[role="checkbox"],[role="radio"],[contenteditable="true"]'
)
REF_ATTRIBUTE = "data-browser-agent-ref"


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
                    f"{key}={value!r}" for key, value in element.items()
                    if key != "ref" and value
                )
                lines.append(f"[{element['ref']}] {details}")
        else:
            lines.append("(none)")
        lines.extend(["", "VISIBLE TEXT:", self.text or "(none)"])
        return "\n".join(lines)


def interactive_locator(page: Page) -> Locator:
    return page.locator(INTERACTIVE_SELECTOR)


def observe_page(page: Page, *, max_text_chars: int = 4200, max_elements: int = 80) -> PageObservation:
    """Compact snapshot with refs attached to the actual DOM nodes for this observation."""
    if page.is_closed():
        raise RuntimeError("Browser page is closed")

    raw = page.locator("body").evaluate(
        """(body, args) => {
            const [maxElements, refAttr] = args;
            const selector = 'a,button,input,textarea,select,[role="button"],[role="link"],[role="menuitem"],[role="option"],[role="tab"],[role="checkbox"],[role="radio"],[contenteditable="true"]';
            body.querySelectorAll('[' + refAttr + ']').forEach(el => el.removeAttribute(refAttr));
            const viewportH = window.innerHeight || document.documentElement.clientHeight;
            const viewportW = window.innerWidth || document.documentElement.clientWidth;
            const candidates = Array.from(body.querySelectorAll(selector));
            const visible = candidates.filter((el) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none'
                    && rect.width > 0 && rect.height > 0;
            });
            visible.sort((a,b) => {
                const ar=a.getBoundingClientRect(), br=b.getBoundingClientRect();
                const ain=ar.bottom>=0 && ar.top<=viewportH && ar.right>=0 && ar.left<=viewportW;
                const bin=br.bottom>=0 && br.top<=viewportH && br.right>=0 && br.left<=viewportW;
                return Number(bin)-Number(ain);
            });
            const picked = visible.slice(0, maxElements);
            return {
                text: (body.innerText || '').replace(/\s+/g, ' ').trim(),
                elements: picked.map((el, index) => {
                    const ref='e'+(index+1); el.setAttribute(refAttr, ref);
                    const rect=el.getBoundingClientRect();
                    return {
                        ref,
                        tag: el.tagName.toLowerCase(),
                        role: el.getAttribute('role') || '',
                        type: el.getAttribute('type') || '',
                        text: (el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0,180),
                        name: el.getAttribute('aria-label') || el.getAttribute('name') || el.getAttribute('title') || '',
                        placeholder: el.getAttribute('placeholder') || '',
                        href: el.tagName.toLowerCase()==='a' ? (el.href || '').slice(0,220) : '',
                        state: [el.disabled?'disabled':'', el.getAttribute('aria-expanded') ? 'expanded='+el.getAttribute('aria-expanded') : ''].filter(Boolean).join(','),
                        viewport: rect.bottom>=0 && rect.top<=viewportH ? 'in-view' : 'off-screen'
                    };
                })
            };
        }""",
        [max_elements, REF_ATTRIBUTE],
    )
    return PageObservation(page.url, page.title(), raw["text"][:max_text_chars], raw["elements"])
