chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id) return;
  try { await chrome.tabs.sendMessage(tab.id, {type: "TOGGLE_AGENT"}); }
  catch (error) { console.warn("Browser Agent cannot run on this page:", error); }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!["AGENT_DECIDE","AGENT_PLAN"].includes(message.type)) return;
  fetch(message.type === "AGENT_PLAN" ? "http://127.0.0.1:8766/api/plan" : "http://127.0.0.1:8766/api/decision", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(message.payload)
  }).then(async response => {
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Agent bridge error");
    sendResponse({ok: true, data});
  }).catch(error => sendResponse({ok: false, error: String(error)}));
  return true;
});
