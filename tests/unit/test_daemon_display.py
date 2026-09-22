"""Exercise display selection through the daemon's actual startup path."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agentcloak.core.config import AgentcloakConfig, Paths
from agentcloak.daemon import server


@pytest.mark.parametrize(
    ("platform", "headless", "display", "expected_xvfb"),
    [
        ("darwin", False, "", False),
        ("win32", False, "", False),
        ("linux", False, "", True),
        ("linux", False, ":0", False),
        ("linux", True, "", False),
        ("darwin", True, "", False),
    ],
)
async def test_start_selects_display(
    tmp_path, monkeypatch, platform, headless, display, expected_xvfb
):
    cfg = AgentcloakConfig()
    cfg.browser.default_tier = "cloak"
    cfg.browser.headless = not headless  # Exercise the explicit CLI override.
    monkeypatch.setenv("DISPLAY", display)
    monkeypatch.setattr(
        server, "sys", SimpleNamespace(platform=platform, stderr=sys.stderr)
    )

    # Stop at the browser factory: all display setup has run, with no real
    # browser, HTTP server, virtual display or proxy started by this test.
    with (
        patch.object(server, "load_config", return_value=(Paths(tmp_path), cfg)),
        patch.object(server, "_has_live_daemon", return_value=False),
        patch.object(server, "ensure_bridge_token", return_value="test-token"),
        patch.object(server, "write_example_config"),
        patch.object(server, "_diagnose_launch_failure"),
        patch("httpcloak.LocalProxy"),
        patch.object(server, "XvfbManager") as xvfb,
        patch.object(
            server, "create_context", new_callable=AsyncMock
        ) as create_context,
    ):
        xvfb.return_value.ensure_display = AsyncMock(return_value=":99")
        create_context.side_effect = RuntimeError("stop at browser launch")
        with pytest.raises(RuntimeError, match="stop at browser launch"):
            await server.start(headless=headless)

        assert xvfb.called is expected_xvfb
        assert xvfb.return_value.ensure_display.await_count == int(expected_xvfb)
        create_context.assert_awaited_once()
        assert create_context.call_args.kwargs["headless"] is headless
