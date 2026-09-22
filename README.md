# Autonomous Browser Agent

A test-task implementation of a generic AI agent that can operate a visible browser and complete multi-step web tasks.

## Current milestone

Milestone 1 intentionally contains only the browser foundation:

- Python 3.11+
- Playwright
- visible Chromium (headed mode)
- persistent browser profile
- CLI entry point
- no site-specific selectors or task-specific automation

The LLM provider and autonomous agent loop are the next milestone. Keeping the browser layer separate lets us validate the runtime first and avoids coupling the architecture to a provider before the model choice is verified.

## Quick start (Windows PowerShell)

```powershell
git clone https://github.com/Ilyusa206/autonomous-browser-agent.git
cd autonomous-browser-agent

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e .
python -m playwright install chromium

browser-agent
```

A visible Chromium window should open. The profile is stored locally in `.browser-profile/`, so cookies and login state survive restarts.

You can also start from a URL:

```powershell
browser-agent --url https://example.com
```

Close the browser window to stop the program.

## Security

Browser profiles, environment files, credentials, tokens and local artifacts are excluded from Git. Never commit authenticated browser state or API keys.

## Planned architecture

```text
User task
   |
Agent loop
   |---- LLM provider (Claude/OpenAI-compatible adapter)
   |
   |---- compact page observation
   |
   |---- generic browser tools
              |
           Playwright
              |
        visible Chromium
```

The final agent will discover page elements from the current page state rather than relying on selectors or routes hardcoded for individual websites.
