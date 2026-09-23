# Автономный браузерный AI-агент

Тестовое задание: универсальный AI-агент, который получает текстовую задачу и автономно управляет браузером. Основной demo-контур — расширение для Chromium-совместимого браузера с persistent mini-app и управлением текущей пользовательской вкладкой; Playwright-контур сохранён как отдельный reference/fallback adapter.

> Репозиторий и интерфейс намеренно оформлены на русском языке. Имена Python-модулей, API и технических сущностей оставлены на английском как стандарт разработки.

## Что уже работает

- видимый Chromium в headed-режиме;
- постоянный локальный профиль: cookies и ручная авторизация сохраняются между запусками;
- автономный цикл «наблюдение → решение модели → действие → новое наблюдение»;
- универсальные инструменты `navigate`, `click`, `type`, `scroll`, `back`, `wait`;
- никаких селекторов и маршрутов, зашитых под конкретные сайты;
- компактное наблюдение вместо отправки модели полного HTML/DOM;
- временные ссылки на элементы вида `e1`, `e2`, которые пересоздаются после изменения страницы;
- ограниченная память: текущая страница + краткий результат последнего действия вместо накопления всех страниц;
- восстановление после ошибок browser action;
- повтор запросов при временном rate limit LLM-провайдера;
- safety gate: потенциально необратимые действия требуют подтверждения пользователя;
- CLI и локальная web-панель с live timeline.

## Архитектура

```text
Пользователь
    │
    ▼
Browser Extension mini-app
    │
    ├── fresh compact observation
    ├── bounded action memory / recovery
    ├── deterministic safety confirmation
    │
    ▼
Local Python bridge
    │
    ▼
LLM provider ── Ollama / Anthropic / OpenAI / Groq
    │
    ▼
generic browser tool call
    │
    ▼
content-script actuator
    │
    └──────────► текущая видимая вкладка Chromium/Opera GX

candidate finish ──► verifier ──► final answer / continue
```

Основной demo-контур не делает отдельный blocking LLM-вызов для предварительного плана: первый executor decision сразу начинает работу. Verifier вызывается только при попытке завершить задачу. Это сохраняет автономный цикл и уменьшает latency локальных CPU-моделей. Playwright-контур остаётся отдельным reference/fallback adapter.

Browser layer не знает о конкретных сайтах. Модель получает только компактное описание текущей страницы и схемы универсальных инструментов, сама выбирает элемент и следующее действие. Observation имеет жёсткий размерный budget: большой список интерактивных элементов не может вытеснить весь visible-text или бесконтрольно увеличить prompt.

## Быстрый запуск — Windows PowerShell

```powershell
git clone https://github.com/Ilyusa206/autonomous-browser-agent.git
cd autonomous-browser-agent

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
python -m playwright install chromium
```

Создайте локальный `.env`:

```text
GROQ_API_KEY=your_key_here
```

Файл `.env` и профиль браузера исключены из Git.

### Визуальная панель

```powershell
browser-agent-ui
```

Панель откроется локально на `http://127.0.0.1:8765`. Введите стартовый URL и задачу, затем нажмите **«Запустить»**. Управляемый Chromium откроется отдельно, а действия агента будут отображаться в панели.

### CLI

```powershell
browser-agent --url https://www.python.org --task "Найди документацию Python по asyncio и кратко объясни, для чего используется asyncio." --max-steps 12
```

## Управление контекстом

Агент не отправляет модели полную веб-страницу. Observation содержит URL, title, ограниченный visible text и ограниченный список видимых интерактивных элементов. После каждого browser action создаётся новое observation.

Предыдущие страницы не накапливаются в prompt. Модель получает текущую страницу и компактное резюме только последнего действия. Это ограничивает рост контекста и уменьшает расход токенов на длинных сценариях.

## Надёжность

Browser action возвращает структурированный результат `ok/message/observation`. После ошибки агент получает свежее состояние страницы и может выбрать другой ход. После трёх последовательных browser errors выполнение останавливается.

Для воспроизводимой демонстрации recovery предусмотрен opt-in debug hook:

```powershell
$env:BROWSER_AGENT_DEBUG_FAIL_ONCE="1"
browser-agent --url https://example.com --task "Узнай, какая организация поддерживает example domains." --max-steps 8
Remove-Item Env:BROWSER_AGENT_DEBUG_FAIL_ONCE
```

В обычном режиме hook полностью выключен.

## Безопасность

Перед потенциально необратимым действием — например удалением, оплатой, покупкой или переводом — deterministic safety layer останавливает выполнение и требует явного подтверждения. Решение LLM само по себе не является разрешением на такое действие.

API-ключи, `.env`, browser profile, auth state и локальные артефакты не должны попадать в Git.

## LLM и провайдер

Agent core не привязан к одному LLM transport. Поддерживаются official Anthropic, official OpenAI, Groq development fallback и локальный Ollama через OpenAI-compatible endpoint.

Для локальной разработки рекомендуется Ollama: модель выполняется на собственной машине/сервере, API-ключ не нужен, нет внешнего rate limit, а browser orchestration остаётся тем же:

```text
Browser Agent -> BrowserLLMProvider -> Ollama -> local model
                              \-> Anthropic / OpenAI / Groq
```

Пример локальной конфигурации:

```text
BROWSER_AGENT_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_MODEL=qwen3:8b
```

Локальный runtime выбран как engineering/development fallback, а не как попытка подменить требование задания. Исходная формулировка требует Claude или OpenAI, поэтому финальная демонстрация должна использовать соответствующий provider, если такой доступ легитимно доступен. Provider abstraction позволяет переключить runtime переменными окружения без изменения browser tools, context engineering, safety или agent loop.

## Почему не MCP

Для однодневного прототипа browser tools реализованы как прямые typed Python tools: это уменьшает integration surface и позволяет проверить сам автономный цикл. Граница между агентом и инструментами уже выделена, поэтому их можно вынести за MCP transport позднее без изменения логики наблюдения и принятия решений.

Основной demo-контур теперь реализован как browser extension + локальный Python bridge. Agent mini-app живёт отдельно от DOM управляемой страницы, поэтому полная навигация не должна уничтожать задачу. Content script отвечает только за observation/action. Side Panel API намеренно не используется: Opera GX в проверенной среде не предоставляет `chrome.sidePanel`; вместо него используется extension popup-window.

## Проверенные сценарии

1. Example Domain: агент самостоятельно перешёл на IANA и определил организацию, поддерживающую example domains.
2. Python.org: агент прошёл от главной страницы к документации, выполнил поиск `asyncio`, открыл документацию и сформулировал ответ.
3. Recovery: первое browser action было детерминированно сломано debug hook; агент получил fresh observation, повторно принял решение и завершил задачу.
4. Safety: на локальной странице действие `Delete account` было остановлено до клика, запрошено подтверждение и отменено пользователем.

## Ограничения и следующие шаги

- локальная модель может быть существенно медленнее облачной без подходящей GPU; качество tool calling зависит от выбранной модели;
- бесплатный Groq tier может вводить паузы из-за TPM/TPD rate limits;
- safety-классификатор сейчас консервативный и основан на семантике выбранного элемента;
- нет полноценного vision/screenshot reasoning;
- нет sub-agent architecture;
- для финального видео нужен действующий Claude/OpenAI API key и end-to-end прогон выбранного runtime;
- MCP оставлен как дальнейшее развитие; Opera GX Side Panel API недоступен в проверенной среде, поэтому UI использует отдельное persistent mini-app окно.
