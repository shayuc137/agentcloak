"""Resolve the current browser build without starting or downloading it."""

import subprocess
from unittest.mock import patch

import pytest

from agentcloak.browser.binaries import (
    find_playwright_chromium,
    find_system_chromium,
)


@pytest.mark.parametrize("headless", [True, False])
def test_system_browser_avoids_driver_probe(headless):
    with (
        patch(
            "agentcloak.browser.binaries.find_system_chromium",
            return_value="/usr/bin/chromium",
        ),
        patch("subprocess.run") as run,
    ):
        assert find_playwright_chromium(headless=headless) == "/usr/bin/chromium"
    run.assert_not_called()


@pytest.mark.parametrize(
    "layout",
    [
        "chrome-linux64/chrome",
        "chrome-win/chrome.exe",
        "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        "chrome-headless-shell-linux64/chrome-headless-shell",
        "chrome-headless-shell-win64/chrome-headless-shell.exe",
        "chrome-linux/headless_shell",
        "chrome-win/headless_shell.exe",
    ],
)
def test_managed_browser_layouts(tmp_path, layout):
    current = tmp_path / "custom cache" / "current-build"
    executable = current / layout
    executable.parent.mkdir(parents=True)
    executable.touch()
    headless = "headless" in layout
    result = subprocess.CompletedProcess(
        [],
        0,
        stdout=(
            f"Requested browser\n  Install location:    {current}\n"
            f"FFmpeg\n  Install location:    {tmp_path / 'ffmpeg'}\n"
        ),
    )
    with (
        patch("agentcloak.browser.binaries.find_system_chromium", return_value=None),
        patch("subprocess.run", return_value=result) as run,
    ):
        assert find_playwright_chromium(headless=headless) == str(executable)
    argv = run.call_args.args[0]
    assert "--dry-run" in argv
    assert ("--only-shell" if headless else "--no-shell") in argv
    assert run.call_args.kwargs["timeout"] == 15


def test_stale_browser_and_supporting_tools_do_not_mask_missing_build(tmp_path):
    old = tmp_path / "old-build"
    old.mkdir()
    (old / "chrome").touch()
    result = subprocess.CompletedProcess(
        [],
        0,
        stdout=(
            f"Chromium\n  Install location:    {tmp_path / 'missing-build'}\n"
            f"Other\n  Install location:    {old}\n"
        ),
    )
    with (
        patch("agentcloak.browser.binaries.find_system_chromium", return_value=None),
        patch("subprocess.run", return_value=result),
    ):
        assert find_playwright_chromium(headless=False) is None


def test_unrecognized_driver_output_is_explicit():
    with (
        patch("agentcloak.browser.binaries.find_system_chromium", return_value=None),
        patch(
            "subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="")
        ),
        pytest.raises(RuntimeError, match="did not report a browser location"),
    ):
        find_playwright_chromium(headless=False)


def test_snap_browser_precedes_path(tmp_path):
    snap = tmp_path / "chrome"
    snap.touch()
    with (
        patch("agentcloak.browser.binaries._SNAP_CHROMIUM", str(snap)),
        patch("shutil.which") as which,
    ):
        assert find_system_chromium() == str(snap)
    which.assert_not_called()


def test_path_browser_resolution(tmp_path):
    with (
        patch("agentcloak.browser.binaries._SNAP_CHROMIUM", str(tmp_path / "missing")),
        patch(
            "shutil.which", side_effect=[None, None, "/usr/bin/google-chrome-stable"]
        ),
    ):
        assert find_system_chromium() == "/usr/bin/google-chrome-stable"


async def test_diagnostic_can_probe_from_an_async_caller(tmp_path):
    from agentcloak.daemon.services.diagnostic_service import DiagnosticService

    executable = tmp_path / "chrome-headless-shell"
    executable.touch()
    result = subprocess.CompletedProcess(
        [], 0, stdout=f"  Install location: {tmp_path}\n"
    )
    with (
        patch("agentcloak.browser.binaries.find_system_chromium", return_value=None),
        patch("subprocess.run", return_value=result),
    ):
        assert DiagnosticService._check_chromium(headless=True)["ok"] is True
