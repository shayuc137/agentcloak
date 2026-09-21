"""Subprocess helper: write cookies and localStorage into a persistent Chromium profile.

Run as:
    python -m agentcloak.browser._profile_writer \
        --profile-dir /path/to/profile \
        --state-file /path/to/state.json \
        [--executable-path /path/to/chrome]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any


async def _run(profile_dir: str, state_json: str, executable_path: str | None) -> None:
    state: dict[str, Any] = json.loads(state_json)

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        exec_path = executable_path or pw.chromium.executable_path
        ctx = await pw.chromium.launch_persistent_context(
            profile_dir,
            headless=True,
            executable_path=exec_path,
        )
        try:
            await ctx.add_cookies(state["cookies"])
            if state.get("local_storage"):
                # Import into native storage without contacting the original site
                # or running its scripts. Navigations never replay this seed.
                await ctx.route(
                    "**/*",
                    lambda route: route.fulfill(
                        status=200, content_type="text/html", body="<!doctype html>"
                    ),
                )
                page = await ctx.new_page()
                for origin, entries in state["local_storage"].items():
                    await page.goto(origin)
                    await page.evaluate(
                        "entries => { for (const [k,v] of Object.entries(entries)) "
                        "localStorage.setItem(k,v); }",
                        entries,
                    )
        finally:
            await ctx.close()
    finally:
        await pw.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-dir", required=True, help="Profile directory.")
    parser.add_argument(
        "--state-file",
        required=True,
        help="Path to JSON cookies and localStorage file.",
    )
    parser.add_argument("--executable-path", default=None, help="Chrome binary.")
    args = parser.parse_args()

    state_json = Path(args.state_file).read_text(encoding="utf-8")

    asyncio.run(_run(args.profile_dir, state_json, args.executable_path))


if __name__ == "__main__":
    main()
