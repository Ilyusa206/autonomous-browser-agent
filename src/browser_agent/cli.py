from __future__ import annotations

import argparse
import json
from pathlib import Path

from browser_agent.agent import AutonomousAgent
from browser_agent.browser import BrowserController
from browser_agent.observation import observe_page
from browser_agent.provider import BrowserLLMProvider, get_provider_from_env
from browser_agent.tools import BrowserTools


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="browser-agent")
    parser.add_argument("--url", default="about:blank")
    parser.add_argument("--profile", type=Path, default=Path(".browser-profile"))
    parser.add_argument("--observe", action="store_true")
    parser.add_argument("--tool", choices=["read", "click", "type", "scroll", "back", "wait", "navigate"])
    parser.add_argument("--args", default="{}")
    parser.add_argument("--ref")
    parser.add_argument("--text")
    parser.add_argument("--task", help="Run the autonomous LLM browser loop for this task.")
    parser.add_argument("--model", default="openai/gpt-oss-120b")
    parser.add_argument("--max-steps", type=int, default=24)
    return parser


def _tool_arguments(args: argparse.Namespace) -> dict:
    if args.ref is not None or args.text is not None:
        parsed: dict[str, object] = {}
        if args.ref is not None:
            parsed["ref"] = args.ref
        if args.text is not None:
            parsed["text"] = args.text
        return parsed
    try:
        return json.loads(args.args)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            "Invalid --args JSON. In PowerShell prefer --ref/--text for simple tests."
        ) from exc


def main() -> None:
    args = build_parser().parse_args()
    controller = BrowserController(profile_dir=args.profile, start_url=args.url)

    try:
        page = controller.start()
        print(f"[browser] ready: {page.url}")

        if args.task:
            AutonomousAgent(
                page,
                GroqProvider(model=args.model),
                max_steps=args.max_steps,
            ).run(args.task)
        elif args.observe:
            print(observe_page(page).render())

        if args.tool:
            result = BrowserTools(page).execute(args.tool, _tool_arguments(args))
            print(f"ok={result.ok} {result.message}")
            if result.observation:
                print(result.observation)

        controller.wait_until_closed()
    except KeyboardInterrupt:
        print("\n[browser] interrupted")
    finally:
        controller.close()


if __name__ == "__main__":
    main()
