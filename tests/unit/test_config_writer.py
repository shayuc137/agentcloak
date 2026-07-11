from __future__ import annotations

from typing import TYPE_CHECKING

from agentcloak.core.config import Paths
from agentcloak.core.config_writer import config_set_batch, config_unset

if TYPE_CHECKING:
    from pathlib import Path


def test_runtime_screenshot_keys_do_not_require_restart(tmp_path: Path) -> None:
    paths = Paths(root=tmp_path)

    confirmations, hint = config_set_batch(
        paths,
        [
            "browser.screenshot_format",
            "png",
            "browser.screenshot_quality",
            "95",
        ],
    )

    assert confirmations == [
        'browser.screenshot_format = "png"',
        "browser.screenshot_quality = 95",
    ]
    assert hint == ""


def test_launch_bound_browser_key_still_requires_restart(tmp_path: Path) -> None:
    paths = Paths(root=tmp_path)

    _, hint = config_set_batch(paths, ["browser.headless", "false"])

    assert hint == " (restart daemon to apply)"


def test_mixed_runtime_and_launch_keys_require_restart(tmp_path: Path) -> None:
    paths = Paths(root=tmp_path)

    _, hint = config_set_batch(
        paths,
        ["browser.screenshot_format", "png", "browser.viewport_width", "1440"],
    )

    assert hint == " (restart daemon to apply)"


def test_runtime_unset_applies_on_next_request(tmp_path: Path) -> None:
    paths = Paths(root=tmp_path)
    config_set_batch(paths, ["browser.screenshot_format", "png"])

    message, hint = config_unset(paths, "browser.screenshot_format")

    assert message == (
        "browser.screenshot_format unset (will use default on next request)"
    )
    assert hint == ""
