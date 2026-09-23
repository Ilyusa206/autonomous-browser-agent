const task = document.querySelector("#task");
const err = document.querySelector("#err");

document.querySelector("#run").onclick = async () => {
  const value = task.value.trim();
  if (!value) {
    err.textContent = "Введите задачу.";
    return;
  }

  err.textContent = "Открываю mini-app…";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !/^https?:/i.test(tab.url || "")) {
      throw new Error("Запустите агента с обычной http/https веб-страницы.");
    }

    const sourceWindow = await chrome.windows.get(tab.windowId);
    await chrome.storage.local.set({
      pendingTask: value,
      targetTabId: tab.id,
    });

    const response = await chrome.runtime.sendMessage({
      type: "OPEN_AGENT",
      bounds: {
        left: sourceWindow.left,
        top: sourceWindow.top,
        width: sourceWindow.width,
        height: sourceWindow.height,
      },
    });
    if (!response?.ok) throw new Error(response?.error || "Mini-app unavailable");
    window.close();
  } catch (error) {
    err.textContent = "Ошибка: " + (error?.message || String(error));
  }
};
