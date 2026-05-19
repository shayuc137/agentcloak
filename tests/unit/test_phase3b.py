"""Tests for Phase 3b — token auth, cookies export, mDNS, PyInstaller spec."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from agentcloak.cli.app import app
from agentcloak.core.discovery import _has_zeroconf, discover_daemon, register_daemon

runner = CliRunner()


class TestTokenAuth:
    """Token gating for WS endpoints — exercises :class:`BridgeService`.

    The helper used to live as ``_check_bridge_token`` on
    ``daemon/routes.py`` and was extracted along with the rest of the
    WebSocket lifecycle into :class:`BridgeService` (PRD R3). The
    behaviour the service implements is identical — these tests just
    invoke it through the new owning class.
    """

    @staticmethod
    def _mock_ws(
        *, client_host: str, bridge_token: str | None, auth: str | None
    ) -> MagicMock:
        ws = MagicMock()
        client = MagicMock()
        client.host = client_host
        ws.client = client
        ws.headers = {"Authorization": auth} if auth else {}
        return ws

    @staticmethod
    def _make_service(*, bridge_token: str | None) -> Any:
        from agentcloak.daemon.services.bridge_service import BridgeService

        state = MagicMock()
        state.bridge_token = bridge_token
        return BridgeService(state)

    def test_check_bridge_token_localhost_bypass(self) -> None:
        svc = self._make_service(bridge_token="secret123")
        ws = self._mock_ws(client_host="127.0.0.1", bridge_token="secret123", auth=None)
        assert svc._check_bridge_token(ws) is True

    def test_check_bridge_token_valid(self) -> None:
        svc = self._make_service(bridge_token="secret123")
        ws = self._mock_ws(
            client_host="192.168.1.100",
            bridge_token="secret123",
            auth="Bearer secret123",
        )
        assert svc._check_bridge_token(ws) is True

    def test_check_bridge_token_invalid(self) -> None:
        svc = self._make_service(bridge_token="secret123")
        ws = self._mock_ws(
            client_host="192.168.1.100",
            bridge_token="secret123",
            auth="Bearer wrong_token",
        )
        assert svc._check_bridge_token(ws) is False

    def test_check_bridge_token_missing(self) -> None:
        svc = self._make_service(bridge_token="secret123")
        ws = self._mock_ws(
            client_host="192.168.1.100",
            bridge_token="secret123",
            auth=None,
        )
        assert svc._check_bridge_token(ws) is False

    def test_check_bridge_token_no_token_set(self) -> None:
        svc = self._make_service(bridge_token=None)
        ws = self._mock_ws(
            client_host="192.168.1.100",
            bridge_token=None,
            auth=None,
        )
        assert svc._check_bridge_token(ws) is True


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


class TestPyInstallerSpec:
    def test_build_script_exists(self) -> None:
        script = Path(__file__).parent.parent.parent / "scripts" / "build_bridge.py"
        assert script.is_file()

    def test_build_script_is_valid_python(self) -> None:
        script = Path(__file__).parent.parent.parent / "scripts" / "build_bridge.py"
        compile(script.read_text(), str(script), "exec")


class TestRemoteBridgeContextPublicAPI:
    def test_send_command_exists(self) -> None:
        from agentcloak.browser.remote_ctx import RemoteBridgeContext

        assert hasattr(RemoteBridgeContext, "send_command")
