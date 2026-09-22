from __future__ import annotations

import argparse
from pathlib import Path

from browser_agent.browser import BrowserController


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="browser-agent",
        description="Open the persistent visible browser used by the autonomous agent.",
    )
    parser.add_argument(
        "--url",
        default="about:blank",
        help="Optional URL to open after Chromium starts.",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path(".browser-profile"),
        help="Persistent Chromium profile directory.",
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
        controller.wait_until_closed()
    except KeyboardInterrupt:
        print("\n[browser] interrupted")
    finally:
        controller.close()


if __name__ == "__main__":
    main()
