(() => {
  if (window.__browserAgentActuator) return;
  window.__browserAgentActuator = true;

  const REF = "data-browser-agent-ref";
  const BASE = [
    "a", "button", "input", "textarea", "select", "summary",
    '[role="button"]', '[role="link"]', '[role="menuitem"]', '[role="option"]',
    '[role="tab"]', '[role="checkbox"]', '[role="radio"]', '[role="textbox"]',
    '[role="searchbox"]', '[contenteditable="true"]', "[tabindex]"
  ].join(",");
  const SEMANTIC = "h1,h2,h3,h4,p,li,dt,dd,pre,code,blockquote,article";
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const clean = value => String(value || "").replace(/\s+/g, " ").trim();

  function visible(element) {
    if (!(element instanceof Element)) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden"
      && Number(style.opacity || 1) !== 0 && rect.width > 1 && rect.height > 1;
  }

  function inViewport(element) {
    if (!visible(element)) return false;
    const rect = element.getBoundingClientRect();
    return rect.bottom >= 0 && rect.top <= innerHeight && rect.right >= 0 && rect.left <= innerWidth;
  }

  function roots() {
    const output = [document];
    const queue = [document.documentElement];
    const seen = new Set(queue);
    while (queue.length) {
      const node = queue.shift();
      if (!node) continue;
      if (node.shadowRoot) output.push(node.shadowRoot);
      for (const child of node.children || []) {
        if (!seen.has(child)) { seen.add(child); queue.push(child); }
      }
      if (node.shadowRoot) {
        for (const child of node.shadowRoot.children || []) {
          if (!seen.has(child)) { seen.add(child); queue.push(child); }
        }
      }
    }
    return output;
  }

  function allCandidates() {
    const output = new Set();
    for (const root of roots()) {
      for (const element of root.querySelectorAll(BASE)) if (visible(element)) output.add(element);
    }
    return [...output];
  }

  function label(element) {
    const id = element.id;
    const explicitLabel = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
    const labelledBy = element.getAttribute("aria-labelledby");
    return clean([
      element.getAttribute("aria-label"),
      labelledBy && document.getElementById(labelledBy)?.innerText,
      element.getAttribute("placeholder"), element.getAttribute("title"),
      element.getAttribute("name"), explicitLabel?.innerText, element.innerText,
      element.getAttribute("type") === "password" ? "" : element.value
    ].filter(Boolean).join(" "));
  }

  function clearRefs() {
    for (const root of roots()) for (const element of root.querySelectorAll(`[${REF}]`)) element.removeAttribute(REF);
  }

  function observedElements() {
    clearRefs();
    const candidates = allCandidates().sort((a, b) => {
      const ar = a.getBoundingClientRect(), br = b.getBoundingClientRect();
      const av = inViewport(a) ? 0 : 1, bv = inViewport(b) ? 0 : 1;
      return av - bv || Math.abs(ar.top - innerHeight / 2) - Math.abs(br.top - innerHeight / 2);
    }).slice(0, 48);
    return candidates.map((element, index) => {
      const ref = `e${index + 1}`;
      element.setAttribute(REF, ref);
      return {
        ref,
        tag: element.tagName.toLowerCase(),
        role: element.getAttribute("role") || "",
        type: element.getAttribute("type") || "",
        text: element.getAttribute("type") === "password" ? "" : clean(element.innerText || element.value).slice(0, 120),
        name: label(element).slice(0, 150),
        placeholder: clean(element.getAttribute("placeholder")).slice(0, 100),
        href: element.tagName === "A" ? String(element.href || "").slice(0, 220) : "",
        editable: element.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName),
        disabled: Boolean(element.disabled || element.getAttribute("aria-disabled") === "true"),
        viewport: inViewport(element)
      };
    });
  }

  function semanticBlocks() {
    const output = [];
    const seen = new Set();
    for (const root of roots()) for (const element of root.querySelectorAll(SEMANTIC)) {
      if (!visible(element)) continue;
      const text = clean(element.innerText || element.textContent);
      if (text.length < 2 || seen.has(text)) continue;
      seen.add(text);
      output.push({ element, text, viewport: inViewport(element), top: element.getBoundingClientRect().top + scrollY });
    }
    return output;
  }

  function observe() {
    const elements = observedElements();
    const blocks = semanticBlocks();
    const viewportText = clean(blocks.filter(block => block.viewport).map(block => block.text).join(" ")).slice(0, 1800);
    const pageStart = clean(blocks.slice(0, 18).map(block => block.text).join(" ")).slice(0, 1000);
    return [
      `URL: ${location.href}`, `TITLE: ${document.title}`, "", "INTERACTIVE ELEMENTS:",
      ...elements.map(element => `[${element.ref}] ${Object.entries(element)
        .filter(([key, value]) => key !== "ref" && value !== "" && value !== false)
        .map(([key, value]) => `${key}=${JSON.stringify(value)}`).join(" | ")}`),
      "", "VIEWPORT TEXT:", viewportText || "(none)", "", "PAGE START:", pageStart || "(none)"
    ].join("\n");
  }

  function findRef(ref) {
    for (const root of roots()) {
      const found = root.querySelector(`[${REF}="${CSS.escape(ref)}"]`);
      if (found) return found;
    }
    return null;
  }

  function elementByRef(ref) {
    if (!/^e\d+$/.test(String(ref || ""))) throw new Error(`Invalid element ref: ${ref}`);
    const element = findRef(ref);
    if (!element) throw new Error(`Stale or missing element ref: ${ref}; request a fresh observation`);
    return element;
  }

  function risky(element) {
    const container = element.closest("form,[role=dialog],dialog,section") || element;
    const controls = [...container.querySelectorAll("button,input[type=submit],a,[role=button]")].slice(0, 30);
    const text = clean([
      element.innerText, element.value, element.getAttribute("aria-label"),
      element.getAttribute("title"), element.href, container.innerText,
      ...controls.map(control => label(control))
    ].filter(Boolean).join(" ")).toLowerCase();
    return [
      "delete", "remove", "erase", "destroy", "pay", "purchase", "buy now", "place order",
      "confirm order", "send money", "transfer", "submit application", "send application", "unsubscribe",
      "удалить", "оплатить", "купить", "оформить заказ", "подтвердить заказ", "перевести",
      "отправить отклик", "откликнуться", "отписаться"
    ].some(phrase => text.includes(phrase));
  }

  function nativeSet(element, text) {
    if ("value" in element) {
      const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
        : element instanceof HTMLInputElement ? HTMLInputElement.prototype : null;
      const setter = prototype && Object.getOwnPropertyDescriptor(prototype, "value")?.set;
      if (setter) setter.call(element, text); else element.value = text;
    } else if (element.isContentEditable) element.textContent = text;
    else throw new Error("Element is not editable");
  }

  function readPage(args) {
    const query = clean(args.query);
    const ref = clean(args.ref);
    const maxChars = Math.min(Math.max(Number(args.max_chars) || 1800, 300), 2400);
    let blocks = [];
    if (ref) {
      const target = elementByRef(ref);
      const container = target.closest("article,section,li,form,main,div") || target;
      blocks = [{ element: container, text: clean(container.innerText || target.innerText || target.value), score: 1000, match: 0, top: 0 }];
    } else {
      blocks = semanticBlocks().map(block => ({ ...block, score: block.viewport ? 20 : 0, match: 0 }));
    }

    const needle = query.toLowerCase();
    const tokens = needle.split(/\\s+/).filter(token => token.length > 1);
    for (const block of blocks) {
      const lower = block.text.toLowerCase();
      if (needle && lower.includes(needle)) block.match += 200;
      block.match += tokens.reduce((score, token) => score + (lower.includes(token) ? 15 : 0), 0);
      block.score += block.match;
    }

    let selected = [];
    if (needle && !ref) {
      const matches = blocks.filter(block => block.match > 0).sort((a, b) => b.score - a.score || a.top - b.top);
      const anchor = matches[0];
      if (anchor) {
        const ordered = [...blocks].sort((a, b) => a.top - b.top);
        const index = ordered.indexOf(anchor);
        const nearby = ordered.slice(Math.max(0, index - 2), Math.min(ordered.length, index + 4));
        const section = anchor.element?.closest?.("article,section,main,dl,div");
        const sectionText = clean(section?.innerText || "");
        if (sectionText && sectionText.length <= maxChars * 2) {
          selected.push({ ...anchor, text: sectionText, score: anchor.score + 500 });
        }
        selected.push(...nearby);
        selected.push(...matches.slice(1, 4));
      }
    } else {
      selected = [...blocks].sort((a, b) => b.score - a.score || a.top - b.top);
    }

    let content = "";
    const seen = new Set();
    for (const block of selected) {
      const text = clean(block.text);
      if (!text || seen.has(text) || (content && content.includes(text))) continue;
      seen.add(text);
      const next = content ? `${content}\n${text}` : text;
      if (next.length > maxChars) {
        const remaining = maxChars - content.length - (content ? 1 : 0);
        if (remaining > 80) content = content ? `${content}\n${text.slice(0, remaining)}` : text.slice(0, maxChars);
        break;
      }
      content = next;
    }
    if (!content) content = clean(document.body?.innerText).slice(0, maxChars);
    if (!content) throw new Error("No readable content found on the current page");
    return {
      message: `Read ${content.length} characters from current page${query ? ` for ${query}` : ""}`,
      evidence: { source_url: location.href, title: document.title, query, content }
    };
  }

  async function act(name, args) {
    if (name === "navigate" || name === "back") throw new Error("Navigation is handled by the extension controller");
    if (name === "read_page") return readPage(args);
    if (name === "find_text") {
      const query = clean(args.text).toLowerCase();
      if (!query) throw new Error("find_text requires non-empty text");
      const hit = semanticBlocks().find(block => block.text.toLowerCase().includes(query));
      if (!hit) throw new Error(`Text not found: ${args.text}`);
      hit.element.scrollIntoView({ block: "center" });
      await sleep(350);
      return { message: `Found and scrolled to text: ${args.text}` };
    }
    if (name === "scroll") {
      scrollBy({ top: Number(args.amount) || 700, behavior: "smooth" });
      await sleep(450);
      return { message: "Scrolled" };
    }
    if (name === "wait") {
      await sleep(Math.min(Math.max(Number(args.milliseconds) || 1000, 100), 5000));
      return { message: "Waited" };
    }
    const element = elementByRef(args.ref);
    if (name === "click") {
      if (risky(element) && !args.confirmed) return { blocked: true, message: `Confirmation required: ${label(element) || args.ref}` };
      element.scrollIntoView({ block: "center" });
      element.focus({ preventScroll: true });
      element.click();
      await sleep(500);
      return { message: `Clicked ${args.ref}` };
    }
    if (name === "type") {
      if (args.submit && risky(element) && !args.confirmed)
        return { blocked: true, message: `Confirmation required before submitting: ${label(element) || args.ref}` };
      element.scrollIntoView({ block: "center" });
      element.focus({ preventScroll: true });
      if (args.clear !== false) nativeSet(element, "");
      nativeSet(element, String(args.text || ""));
      try {
        element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: args.text }));
      } catch (_) { element.dispatchEvent(new Event("input", { bubbles: true })); }
      element.dispatchEvent(new Event("change", { bubbles: true }));
      if (args.submit) {
        for (const type of ["keydown", "keypress", "keyup"])
          element.dispatchEvent(new KeyboardEvent(type, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true }));
        if (element.form && typeof element.form.requestSubmit === "function") element.form.requestSubmit();
      }
      await sleep(500);
      return { message: `Typed ${args.ref}` };
    }
    if (name === "press") {
      if (["enter", "space"].includes(String(args.key || "").toLowerCase()) && risky(element) && !args.confirmed)
        return { blocked: true, message: `Confirmation required before consequential key action: ${label(element) || args.ref}` };
      element.focus({ preventScroll: true });
      for (const type of ["keydown", "keypress", "keyup"])
        element.dispatchEvent(new KeyboardEvent(type, { key: args.key, code: args.key, bubbles: true, cancelable: true }));
      await sleep(150);
      return { message: `Pressed ${args.key}` };
    }
    throw new Error(`Unknown tool ${name}`);
  }

  chrome.runtime.onMessage.addListener((message, _sender, reply) => {
    if (message.type === "OBSERVE") {
      try { reply({ ok: true, observation: observe() }); }
      catch (error) { reply({ ok: false, message: error.message }); }
      return;
    }
    if (message.type === "ACT") {
      act(message.name, message.args || {})
        .then(result => reply(result.blocked ? { ok: false, ...result } : { ok: true, ...result }))
        .catch(error => reply({ ok: false, message: error.message }));
      return true;
    }
  });
})();
