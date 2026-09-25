let currentTask = "";
let running = false;
let cancelled = false;
let step = 0;
let tabId = null;
let runNonce = 0;
let agentState = null;
let lastResult = null;

const maxSteps = 30;
const $ = selector => document.querySelector(selector);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function setStatus(text) {
  const element = $("#state");
  if (element) element.textContent = text;
}

function log(text, className = "") {
  const host = $("#log");
  if (!host) return;
  const element = document.createElement("div");
  element.className = `event ${className}`;
  element.textContent = text;
  host.appendChild(element);
  element.scrollIntoView({ block: "nearest" });
}

function renderProgress() {
  const progress = $("#plan");
  if (!progress) return;
  if (!agentState) {
    progress.textContent = "Инициализация состояния…";
    return;
  }
  const remaining = (agentState.remaining_work || []).slice(0, 3);
  const completed = (agentState.completed_subgoals || []).slice(-3);
  const lines = [
    `Сейчас: ${agentState.current_subgoal || "анализ текущей страницы"}`,
    ...completed.map(item => `✓ ${item}`),
    ...remaining.map(item => `→ ${item}`),
    `Шаги без прогресса: ${agentState.no_progress_count || 0}`
  ];
  progress.textContent = lines.join("\n");

  const evidenceHost = $("#evidence");
  if (evidenceHost) {
    const evidence = agentState.evidence || [];
    evidenceHost.textContent = evidence.length
      ? evidence.slice(-4).map(item => `• ${item.title || "Страница"}: ${String(item.content || "").slice(0, 180)}`).join("\n")
      : "Пока нет извлечённых фактов";
  }
}

async function activeTab() {
  if (tabId) {
    try { return await chrome.tabs.get(tabId); }
    catch (_) { tabId = null; }
  }
  const tabs = await chrome.tabs.query({});
  return tabs.find(tab => tab.active && /^https?:/.test(tab.url || ""))
    || tabs.find(tab => /^https?:/.test(tab.url || ""));
}

async function sendRaw(type, payload = {}) {
  const id = tabId || (await activeTab())?.id;
  if (!id) throw new Error("No web tab");
  tabId = id;
  return chrome.tabs.sendMessage(id, { type, ...payload });
}

async function ensureActuator(timeout = 10000) {
  const deadline = Date.now() + timeout;
  let lastError;
  while (Date.now() < deadline) {
    const tab = await activeTab();
    if (!tab?.id) { lastError = new Error("No web tab"); await sleep(350); continue; }
    tabId = tab.id;
    if (!/^https?:/i.test(tab.url || "")) {
      lastError = new Error(`Target tab is not on an http/https document yet: ${tab.url || ""}`);
      await sleep(350);
      continue;
    }
    try {
      const response = await chrome.tabs.sendMessage(tabId, { type: "OBSERVE" });
      if (response?.ok) return response;
      lastError = new Error(response?.message || "Actuator not ready");
    } catch (error) {
      lastError = error;
      try {
        await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
        const response = await chrome.tabs.sendMessage(tabId, { type: "OBSERVE" });
        if (response?.ok) return response;
      } catch (injectionError) { lastError = injectionError; }
    }
    await sleep(350);
  }
  throw new Error(`Не удалось подключиться к веб-вкладке после навигации: ${lastError?.message || "timeout"}`);
}

function browserObservation(tab, reason = "") {
  return {
    ok: true,
    browserLevel: true,
    observation: [
      `URL: ${tab?.url || ""}`, `TITLE: ${tab?.title || ""}`, "",
      `BROWSER STATE: page content is temporarily unavailable${reason ? `: ${reason}` : ""}`,
      "Navigation remains available."
    ].join("\n")
  };
}

function isPolicyBlocked(error) {
  return /ExtensionsSettings policy|cannot be scripted|Cannot access contents|chrome:\/\/|opera:\/\//i.test(String(error?.message || error));
}

function isPortGone(error) {
  return /Receiving end does not exist|Could not establish connection|message (?:port|channel) is closed|message port closed|back\/forward cache|extension port/i.test(String(error?.message || error));
}

async function message(type, payload = {}) {
  if (type === "OBSERVE") {
    try { return await ensureActuator(); }
    catch (error) {
      const tab = await activeTab();
      if (tab?.id && isPolicyBlocked(error)) return browserObservation(tab, error.message);
      throw error;
    }
  }
  if (type === "ACT" && payload.name === "navigate") {
    const tab = await activeTab();
    if (!tab?.id) throw new Error("No web tab");
    await chrome.tabs.update(tab.id, { url: payload.args?.url });
    await sleep(500);
    return { ok: true, message: "Navigating" };
  }
  if (type === "ACT" && payload.name === "back") {
    const tab = await activeTab();
    if (!tab?.id) throw new Error("No web tab");
    await chrome.tabs.goBack(tab.id);
    await sleep(500);
    return { ok: true, message: "Went back" };
  }
  await ensureActuator();
  return sendRaw(type, payload);
}

async function actWithNavigationRecovery(name, args) {
  try { return await message("ACT", { name, args }); }
  catch (error) {
    if (!["navigate", "back"].includes(name) || !isPortGone(error)) throw error;
    log("↻ Навигация сменила документ — переподключаю browser actuator", "warn");
    await sleep(500);
    const observation = await ensureActuator(12000);
    return { ok: true, message: "Navigation completed; actuator reconnected", observation: observation?.observation || "" };
  }
}

async function backend(type, payload) {
  return new Promise((resolve, reject) => chrome.runtime.sendMessage({ type, payload }, response => {
    if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
    if (!response?.ok) return reject(new Error(response?.error || "Bridge unavailable"));
    resolve(response.data);
  }));
}

async function human(question, { confirmOnly = false } = {}) {
  return new Promise(resolve => {
    const host = $("#human");
    if (!host) return resolve("");
    host.replaceChildren();
    const card = document.createElement("div"); card.className = "ask";
    const text = document.createElement("div"); text.textContent = question; card.appendChild(text);
    let input = null;
    if (!confirmOnly) {
      input = document.createElement("input");
      input.placeholder = "Ответ (не вводите пароли или коды)";
      card.appendChild(input);
    }
    const buttons = document.createElement("div"); buttons.className = "buttons";
    const yes = document.createElement("button"); yes.textContent = confirmOnly ? "Разрешить" : "Продолжить";
    const no = document.createElement("button"); no.textContent = confirmOnly ? "Запретить" : "Отмена";
    buttons.append(yes, no); card.appendChild(buttons); host.appendChild(card);
    yes.addEventListener("click", () => { const value = input?.value?.trim() || "yes"; host.replaceChildren(); resolve(value); });
    no.addEventListener("click", () => { host.replaceChildren(); resolve(""); });
    input?.focus();
  });
}

async function run() {
  if (running) return;
  const nonce = ++runNonce;
  currentTask = $("#task")?.value.trim() || "";
  if (!currentTask) return;
  if (!tabId) tabId = (await activeTab())?.id;
  if (!tabId) { setStatus("Откройте обычную веб-страницу"); return; }

  running = true; cancelled = false; step = 0; agentState = null; lastResult = null;
  $("#log")?.replaceChildren();
  setStatus("Запуск…");
  renderProgress();

  try {
    await message("OBSERVE");
    if (nonce !== runNonce) return;
    setStatus("Работаю…");

    while (running && !cancelled && step++ < maxSteps) {
      const observed = await message("OBSERVE");
      log(`Шаг ${step}: анализ состояния и накопленных фактов`);
      setStatus("Модель анализирует…");
      const decision = await backend("AGENT_DECIDE", {
        task: currentTask,
        observation: observed.observation,
        state: agentState,
        last_result: lastResult
      });
      if (cancelled || nonce !== runNonce) return;
      agentState = decision.state || agentState;
      lastResult = null;
      renderProgress();

      if (decision.verification && !decision.verification.complete)
        log(`Verifier: ${(decision.verification.missing || []).join("; ") || decision.verification.summary}`, "warn");
      if (decision.kind === "retry") { setStatus("Продолжаю после verifier…"); continue; }
      if (decision.kind === "stop") {
        log(decision.text || "Остановлено с диагностикой", "err");
        setStatus("Остановлено"); running = false; return;
      }
      if (decision.kind === "finish") {
        log(decision.text || "Готово", "ok");
        setStatus("Готово · проверено"); running = false; return;
      }
      if (decision.name === "ask_user") {
        let question = decision.arguments?.question || "Нужны данные пользователя";
        if (/password|парол|verification code|код подтверждения|2fa|otp|api key/i.test(question))
          question = "Выполните вход вручную в управляемой вкладке. Пароль и коды агенту не передавайте.";
        await human(question, { confirmOnly: /вход|login|captcha|авторизац/i.test(question) });
        lastResult = { tool: "ask_user", arguments: decision.arguments || {}, ok: true, message: "User completed manual step", before_fingerprint: agentState?.current_fingerprint || "" };
        continue;
      }

      const args = decision.arguments || {};
      const signature = `${decision.name} ${JSON.stringify(args)}`;
      log(`→ ${signature}`, "tool");
      setStatus(decision.name === "read_page" ? "Читаю страницу…" : "Выполняю действие…");
      let result;
      try {
        result = await actWithNavigationRecovery(decision.name, args);
      } catch (error) {
        result = { ok: false, message: error.message };
        if (isPortGone(error)) {
          log("↻ Документ сменился — восстанавливаю actuator", "warn");
          try { await ensureActuator(12000); result.message += "; actuator reconnected"; }
          catch (reconnectError) { if (!isPolicyBlocked(reconnectError)) throw reconnectError; }
        }
      }
      if (cancelled || nonce !== runNonce) return;

      if (result?.blocked) {
        const answer = await human(`${result.message} Разрешить это действие?`, { confirmOnly: true });
        if (!answer) {
          result = { ok: false, message: "User denied consequential action" };
          log("Действие отменено пользователем", "warn");
        } else result = await actWithNavigationRecovery(decision.name, { ...args, confirmed: true });
      }

      lastResult = {
        tool: decision.name,
        arguments: args,
        ok: Boolean(result?.ok),
        message: result?.message || "No result",
        evidence: result?.evidence || null,
        before_fingerprint: agentState?.current_fingerprint || ""
      };
      log(`${result?.ok ? "✓" : "↻"} ${lastResult.message}`, result?.ok ? "ok" : "warn");
      if (result?.evidence) log(`Прочитано и сохранено: ${result.evidence.title || result.evidence.source_url}`, "evidence");
      if (["navigate", "back"].includes(decision.name)) {
        try { await ensureActuator(12000); }
        catch (error) { if (!isPolicyBlocked(error)) throw error; }
      } else await sleep(250);
    }
    if (running) setStatus("Лимит шагов");
    running = false;
  } catch (error) {
    log(`Ошибка: ${error.message}`, "err");
    setStatus("Ошибка");
    running = false;
  }
}

$("#run")?.addEventListener("click", run);
$("#stop")?.addEventListener("click", () => { runNonce++; cancelled = true; running = false; setStatus("Остановлено"); });
$("#close")?.addEventListener("click", () => window.close());

chrome.storage.local.get(["pendingTask", "targetTabId"], values => {
  if (values.targetTabId) tabId = values.targetTabId;
  if (values.pendingTask) {
    const task = $("#task");
    if (task) task.value = values.pendingTask;
    chrome.storage.local.remove(["pendingTask"]);
    run();
  }
});
