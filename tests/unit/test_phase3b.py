"""Tests for Phase 3b — cookies export, mDNS discovery.

Token-auth tests moved out with the standalone bridge: the daemon's ``/ext``
endpoint now verifies a hello-token inline in
:meth:`BridgeService.handle_ext_connection` rather than via the removed
``_check_bridge_token`` Bearer-header helper. The PyInstaller-spec test went
away with ``scripts/build_bridge.py`` (the standalone bridge executable).
"""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from agentcloak.cli.app import app
from agentcloak.core.discovery import _has_zeroconf, discover_daemon, register_daemon

runner = CliRunner()


class TestCookiesCLI:
    def test_cookies_help(self) -> None:
        result = runner.invoke(app, ["cookies", "--help"])
        assert "export" in result.stdout

    def test_cookies_export_help(self) -> None:
        result = runner.invoke(app, ["cookies", "export", "--help"])
        assert "--url" in result.stdout
        assert "--output" in result.stdout


class TestMDNS:
    def test_has_zeroconf_without_package(self) -> None:
        with patch.dict("sys.modules", {"zeroconf": None}):
            assert _has_zeroconf() is False

    def test_discover_daemon_without_zeroconf(self) -> None:
        with patch("agentcloak.core.discovery._has_zeroconf", return_value=False):
            assert discover_daemon() is None

    def test_register_daemon_without_zeroconf(self) -> None:
        with patch("agentcloak.core.discovery._has_zeroconf", return_value=False):
            assert register_daemon(9222) is False


class TestRemoteBridgeContextPublicAPI:
    def test_send_command_exists(self) -> None:
        from agentcloak.browser.remote_ctx import RemoteBridgeContext

        assert hasattr(RemoteBridgeContext, "send_command")
