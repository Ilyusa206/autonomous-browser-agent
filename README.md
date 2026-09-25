# Autonomous Browser Agent

Тестовое задание AI Developer: site-agnostic агент, который получает одну сложную задачу, управляет текущей вкладкой Chromium/Opera, накапливает найденные факты и завершает работу только после проверки результата.

Основной runtime — browser extension + локальный Python bridge. Он работает в уже открытой пользовательской сессии браузера, поэтому cookies и авторизация не переносятся в отдельный automation-профиль. Playwright CLI/UI сохранены как reference adapter и используют отдельный persistent profile.

## Что реализовано

- автономный цикл `observe → decide → act/read → update state → verify`;
- generic browser tools без selectors, URL или сценариев под конкретные сайты;
- bounded agent state между всеми шагами;
- `read_page`, который извлекает ограниченный смысловой фрагмент и сохраняет его как evidence;
- различие между `find_text` (найти/прокрутить) и `read_page` (прочитать/запомнить);
- completion verifier, который получает текущую страницу и накопленное состояние;
- progress detection по fingerprint страницы и появлению нового evidence;
- память неудачных стратегий, stale-ref recovery и явная остановка no-progress loop;
- временные DOM refs, SPA/dynamic controls, contenteditable, keyboard actions и open shadow DOM;
- повторное подключение content script после полной навигации/BFCache;
- deterministic safety gate для удаления, оплаты, заказа, перевода и отправки данных/отклика;
- OpenAI, Anthropic, Ollama и Groq providers;
- telemetry по provider/model, фазе, duration и размерам prompt/tools;
- mini-app с текущим подэтапом, timeline, evidence, recovery и финальным ответом.

## Архитектура

```text
User task
   ↓
Extension mini-app ── current authenticated browser tab
   ↓                         ↑
compact observation     generic actuator
   ↓                         ↑
Python bridge → bounded AgentState → LLM decision
                    │              │
                    ├─ evidence    ├─ browser action
                    ├─ pages       ├─ read_page
                    ├─ actions     ├─ ask_user
                    ├─ failures    └─ candidate finish
                    └─ remaining             ↓
                                      evidence-aware verifier
```

`extension/content.js` наблюдает и изменяет страницу. `extension/agent.js` управляет жизненным циклом задачи. `extension_bridge.py` восстанавливает присланный state, применяет результат предыдущего browser action и вызывает provider. Один HTTP-вызов не обязан помнить предыдущий: сериализованный bounded state возвращается mini-app и передаётся в следующий вызов.

### Agent state

`src/browser_agent/state.py` хранит:

- `objective` и `current_subgoal`;
- завершённые подэтапы и `remaining_work`;
- evidence: URL, title, query и извлечённый content;
- посещённые страницы/fingerprints;
- последние actions и факт реального прогресса;
- failures и verifier feedback;
- счётчики no-progress и отклонённых завершений.

Память жёстко ограничена: до 12 evidence по 1800 символов, 10 последних actions, 12 состояний страниц, 8 failures и 5 verifier feedback. Evidence дедуплицируется по источнику и content hash. Полный DOM и бесконечная message history модели не отправляются.

### Observation и refs

Наблюдение содержит URL/title, до 48 наиболее релевантных interactive elements, текст текущего viewport и ограниченное начало страницы. Password input values маскируются. Временные refs (`e1`, `e2`...) пересоздаются при каждом наблюдении, поэтому stale ref становится recoverable tool failure, а не скрытым кликом по другому элементу.

Поддерживаются links, buttons, inputs, textareas, selects, role-based controls, contenteditable, tabindex controls и open shadow roots. После смены документа controller ждёт и при необходимости повторно внедряет actuator.

### Read / extract / evidence

`find_text` только находит literal text и прокручивает его в viewport. `read_page` извлекает bounded semantic blocks (`heading`, `p`, `li`, `dt/dd`, `pre/code`, `blockquote`, `article`) с фокусом на optional `query` или observed `ref`.

Результат имеет структуру:

```json
{
  "source_url": "https://example.test/docs",
  "title": "Documentation",
  "query": "target function",
  "content": "bounded extracted text"
}
```

Bridge добавляет результат в state. Следующий executor decision и verifier видят evidence даже после перехода на другую страницу.

### Progress, recovery и completion

Успешный tool call сам по себе не считается прогрессом. Прогресс — это изменение fingerprint страницы или новое evidence. Повтор одинакового action без прогресса остаётся в ledger и превращается в явную recovery directive для reasoning layer. После шести бесполезных итераций агент останавливается с диагностикой.

`finish` — только кандидат. Verifier проверяет исходную задачу против current page и полного AgentState. Его missing items записываются в `remaining_work` и влияют на следующий decision. После трёх отклонённых попыток завершения агент останавливается, а не попадает в `finish → reject → finish` loop.

### Safety и ручное вмешательство

Без отдельного подтверждения блокируются опасные clicks, Enter/Space submission и `type(..., submit=true)`, если surrounding form/dialog/section содержит признаки удаления, оплаты, покупки, перевода, отправки заявки/отклика и других consequential actions.

Агент должен самостоятельно дойти до этой границы. Login/CAPTCHA выполняются пользователем вручную в целевой вкладке. Агент не просит пароль, OTP/2FA или API key. После ручного продолжения он получает fresh observation.

## Установка — Windows PowerShell

```powershell
git clone https://github.com/Ilyusa206/autonomous-browser-agent.git
cd autonomous-browser-agent

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
python -m playwright install chromium
Copy-Item .env.example .env
```

### Конфигурация provider

Локальная разработка через Ollama:

```text
BROWSER_AGENT_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_MODEL=qwen3:8b
```

Официальный OpenAI runtime:

```text
BROWSER_AGENT_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=<official model id available to the account>
```

Официальный Anthropic runtime:

```text
BROWSER_AGENT_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=<official model id available to the account>
```

Anthropic model ID намеренно не зашит: доступные модели зависят от аккаунта и меняются. Groq остаётся development fallback через `GROQ_API_KEY`/`GROQ_MODEL`. Секреты, `.env`, cookies и browser profiles исключены из Git.

## Основной запуск: extension + bridge

1. Откройте `opera://extensions` или `chrome://extensions`.
2. Включите режим разработчика и загрузите каталог `extension/` как unpacked extension.
3. Запустите bridge:

```powershell
.\.venv\Scripts\Activate.ps1
browser-agent-bridge
```

4. Откройте обычную HTTP/HTTPS-вкладку и нажмите иконку **Browser Agent**.
5. Введите задачу целиком и нажмите **Запустить**.

Health check bridge:

```powershell
Invoke-RestMethod http://127.0.0.1:8766/health
```

### Playwright reference adapter

```powershell
browser-agent --url https://www.python.org --task "Открой документацию по asyncio.run(), прочитай нужный раздел и кратко объясни по-русски назначение функции." --max-steps 20
```

```powershell
browser-agent-ui
```

Playwright adapter использует `.browser-profile`; extension path использует текущую пользовательскую сессию браузера.

## Проверки

```powershell
python -m pytest -q
node --check extension/background.js
node --check extension/popup.js
node --check extension/agent.js
node --check extension/content.js
python -m json.tool extension/manifest.json | Out-Null
```

Автоматизированные tests покрывают state/evidence accumulation, budgets, deduplication, read result contract, stale refs, recovery, verifier feedback, bridge state round-trip, safety и provider contracts. CI выполняет тот же Python/JavaScript/manifest validation.

После любых изменений reasoning/tool layer публичные browser E2E должны быть перепроверены вручную, прежде чем отмечать их PASS. Рекомендуемый acceptance set:

1. Python docs: найти, прочитать и по-русски объяснить `asyncio.run()`.
2. Незнакомый публичный docs-сайт: несколько действий, targeted read, grounded final answer.
3. Локальная dynamic fixture: input → transition → read result → verified finish.
4. Stale/transient failure: fresh observation → другая стратегия → completion.
5. Локальная destructive fixture: дойти до опасного action и остановиться до подтверждения.

Нельзя использовать реальные удаления писем, заказы, платежи или отклики как acceptance test.

## Почему не MCP

Tools остаются typed in-process/browser-extension capabilities. Это сохраняет минимальный integration surface и позволяет проверять state/evidence/recovery независимо от transport. Граница tool schemas уже отделена; вынесение её в MCP не меняет core reasoning architecture и не требуется для текущего задания.

## Ограничения

- качество автономных решений зависит от выбранной модели; CPU-only `qwen3:8b` может быть медленным и слабее official OpenAI/Anthropic runtime;
- нет screenshot/vision reasoning и closed-shadow-DOM access;
- safety classifier консервативный и может запросить лишнее подтверждение;
- браузер запрещает content scripts на внутренних `chrome://`/`opera://` страницах;
- CAPTCHA и login требуют ручного действия;
- agent не выполняет финальный платёж, покупку, удаление или отправку без подтверждения;
- extension E2E необходимо запускать в реальном Chromium/Opera окружении; unit/integration green не заменяет этот прогон.
