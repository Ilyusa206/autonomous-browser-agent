let agentWindowId = null;

async function openAgentWindow(bounds) {
  if (agentWindowId) {
    try {
      await chrome.windows.update(agentWindowId, { focused: true });
      return agentWindowId;
    } catch (_) {
      agentWindowId = null;
    }
  }

  const width = 430;
  const height = 720;
  const left = Math.max((bounds?.left ?? 0) + (bounds?.width ?? 1200) - width - 24, 0);
  const top = Math.max((bounds?.top ?? 0) + 70, 0);
  const windowInfo = await chrome.windows.create({
    url: chrome.runtime.getURL("agent.html"),
    type: "popup",
    width,
    height,
    left,
    top,
    focused: true,
  });
  agentWindowId = windowInfo.id ?? null;
  return agentWindowId;
}

chrome.windows.onRemoved.addListener((id) => {
  if (id === agentWindowId) agentWindowId = null;
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "OPEN_AGENT") {
    openAgentWindow(message.bounds)
      .then((id) => sendResponse({ ok: true, windowId: id }))
      .catch((error) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }

  if (!["AGENT_DECIDE", "AGENT_PLAN"].includes(message.type)) return;

  const endpoint =
    message.type === "AGENT_PLAN"
      ? "http://127.0.0.1:8766/api/plan"
      : "http://127.0.0.1:8766/api/decision";

  fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(message.payload),
  })
    .then(async (response) => {
      const text = await response.text();
      let data;
      try { data = JSON.parse(text); }
      catch (_) { throw new Error("Bridge returned non-JSON response: " + text.slice(0, 240)); }
      if (!response.ok) throw new Error(data.error || "Agent bridge error");
      sendResponse({ ok: true, data });
    })
    .catch((error) => sendResponse({ ok: false, error: String(error) }));
  return true;
});
