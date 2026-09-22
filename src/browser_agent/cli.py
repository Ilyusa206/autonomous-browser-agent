from __future__ import annotations

import argparse
import json
from pathlib import Path

from browser_agent.browser import BrowserController
from browser_agent.observation import observe_page
from browser_agent.tools import BrowserTools


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="browser-agent",
        description="Run the browser foundation used by the autonomous agent.",
    )
    parser.add_argument("--url", default="about:blank", help="Optional URL to open after Chromium starts.")
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path(".browser-profile"),
        help="Persistent Chromium profile directory.",
    )
    parser.add_argument(
        "--observe",
        action="store_true",
        help="Print a compact LLM-friendly observation of the current page.",
    )
    parser.add_argument(
        "--tool",
        choices=["read", "click", "type", "scroll", "back", "wait", "navigate"],
        help="Run one generic browser tool after startup (debug helper).",
    )
    parser.add_argument(
        "--args",
        default="{}",
        help='JSON arguments for --tool, for example: --args "{\"ref\": \"e1\"}"',
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    print(f"[browser] profile: {args.profile.resolve()}")
    print("[browser] starting visible Chromium; close the browser window to exit")

    controller = BrowserController(profile_dir=args.profile, start_url=args.url)
    try:
        page = controller.start()
        print(f"[browser] ready: {page.url}")

        if args.observe:
            print("\n--- PAGE OBSERVATION ---")
            print(observe_page(page).render())
            print("--- END OBSERVATION ---\n")

        if args.tool:
            tool_args = json.loads(args.args)
            result = BrowserTools(page).execute(args.tool, tool_args)
            print(f"\n--- TOOL RESULT: {args.tool} ---")
            print(f"ok={result.ok}")
            print(result.message)
            if result.observation:
                print(result.observation)
            print("--- END TOOL RESULT ---\n")

        controller.wait_until_closed()
    except KeyboardInterrupt:
        print("\n[browser] interrupted")
    finally:
        controller.close()


if __name__ == "__main__":
    main()
