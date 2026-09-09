"""Read-only Chromium discovery shared by launch and local diagnostics."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

_SNAP_CHROMIUM = "/snap/chromium/current/usr/lib/chromium-browser/chrome"
_CHROMIUM_BINARIES = (
    "chromium-browser",
    "chromium",
    "google-chrome-stable",
    "google-chrome",
)


def find_system_chromium() -> str | None:
    """Use the same system executable priority as Playwright launches."""
    if Path(_SNAP_CHROMIUM).is_file():
        return _SNAP_CHROMIUM
    for name in _CHROMIUM_BINARIES:
        if path := shutil.which(name):
            return path
    return None


def find_playwright_chromium(*, headless: bool) -> str | None:
    """Find the configured system browser or the current Playwright build.

    Playwright's public dry-run command resolves its platform, revision and
    PLAYWRIGHT_BROWSERS_PATH override without downloading or launching a
    browser. The first install location is the requested Chromium build;
    later locations belong to supporting tools such as FFmpeg. Restrict the
    search to that build so an obsolete cached version cannot mask a missing
    dependency. Headless launches use Playwright's separate headless shell.

    A subprocess also works when doctor is called from MCP's asyncio loop.
    """
    system_binary = find_system_chromium()
    if system_binary:
        return system_binary
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "playwright",
            "install",
            "--dry-run",
            "--only-shell" if headless else "--no-shell",
            "chromium",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=15,
    )
    match = re.search(r"^\s*Install location:\s*(.+)$", result.stdout, re.MULTILINE)
    if match is None:
        raise RuntimeError("Playwright dry-run did not report a browser location")
    directory = Path(match.group(1).strip())
    names = (
        {
            "headless_shell",
            "headless_shell.exe",
            "chrome-headless-shell",
            "chrome-headless-shell.exe",
        }
        if headless
        else {"chrome", "chrome.exe", "Chromium"}
    )
    for candidate in sorted(directory.rglob("*")):
        if candidate.name in names and candidate.is_file():
            return str(candidate)
    return None
