"""Tests for the shared DaemonClient — sync + async with mocked httpx."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import orjson
import pytest

from agentcloak.client import DaemonClient
from agentcloak.core.errors import AgentBrowserError, DaemonConnectionError


def _make_response(status_code: int, payload: bytes) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=payload,
        headers={"content-type": "application/json"},
    )


class TestDaemonClientSync:
    """The sync path is what CLI commands use — must surface structured errors."""

    def test_connect_error_raises_daemon_unreachable_when_auto_start_off(self) -> None:
        client = DaemonClient(host="127.0.0.1", port=19999, auto_start=False)
        with patch("httpx.Client") as mock_cls:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.request = MagicMock(side_effect=httpx.ConnectError("refused"))
            mock_cls.return_value = ctx

            with pytest.raises(DaemonConnectionError) as exc_info:
                client.health_sync()
            assert exc_info.value.error == "daemon_unreachable"

    def test_error_envelope_raises_agent_browser_error(self) -> None:
        client = DaemonClient(host="127.0.0.1", port=19999, auto_start=False)
        body = orjson.dumps(
            {
                "ok": False,
                "error": "test_error",
                "hint": "test hint",
                "action": "test action",
            }
        )
        with patch("httpx.Client") as mock_cls:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.request = MagicMock(return_value=_make_response(400, body))
            mock_cls.return_value = ctx

            with pytest.raises(AgentBrowserError) as exc_info:
                client.health_sync()
            assert exc_info.value.error == "test_error"
            assert exc_info.value.hint == "test hint"
            assert exc_info.value.action == "test action"

    def test_success_envelope_returns_dict(self) -> None:
        client = DaemonClient(host="127.0.0.1", port=19999, auto_start=False)
        body = orjson.dumps({"ok": True, "seq": 7, "data": {"title": "Test"}})
        with patch("httpx.Client") as mock_cls:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.request = MagicMock(return_value=_make_response(200, body))
            mock_cls.return_value = ctx

            result = client.health_sync()
            assert result["ok"] is True
            assert result["seq"] == 7
            assert result["data"] == {"title": "Test"}

    def test_timeout_raises_browser_timeout_error(self) -> None:
        from agentcloak.core.errors import BrowserTimeoutError

        client = DaemonClient(host="127.0.0.1", port=19999, auto_start=False)
        with patch("httpx.Client") as mock_cls:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.request = MagicMock(side_effect=httpx.ReadTimeout("slow"))
            mock_cls.return_value = ctx

            with pytest.raises(BrowserTimeoutError) as exc_info:
                client.health_sync()
            assert exc_info.value.error == "daemon_timeout"

    def test_connect_timeout_classified_distinctly(self) -> None:
        """A3: ConnectTimeout must produce its own error code, not generic timeout."""
        client = DaemonClient(host="127.0.0.1", port=19999, auto_start=False)
        with patch("httpx.Client") as mock_cls:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.request = MagicMock(side_effect=httpx.ConnectTimeout("tcp"))
            mock_cls.return_value = ctx

            with pytest.raises(DaemonConnectionError) as exc_info:
                client.health_sync()
            assert exc_info.value.error == "daemon_connect_timeout"

    def test_network_error_raises_daemon_network_error(self) -> None:
        """A3: NetworkError other than ConnectError → daemon_network_error."""
        client = DaemonClient(host="127.0.0.1", port=19999, auto_start=False)
        with patch("httpx.Client") as mock_cls:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.request = MagicMock(side_effect=httpx.ReadError("conn reset"))
            mock_cls.return_value = ctx

            with pytest.raises(DaemonConnectionError) as exc_info:
                client.health_sync()
            assert exc_info.value.error == "daemon_network_error"

    def test_request_error_raises_daemon_request_failed(self) -> None:
        """A3: catch-all RequestError → daemon_request_failed."""
        from agentcloak.core.errors import AgentBrowserError

        client = DaemonClient(host="127.0.0.1", port=19999, auto_start=False)
        with patch("httpx.Client") as mock_cls:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.request = MagicMock(side_effect=httpx.UnsupportedProtocol("bad scheme"))
            mock_cls.return_value = ctx

            with pytest.raises(AgentBrowserError) as exc_info:
                client.health_sync()
            assert exc_info.value.error == "daemon_request_failed"

    def test_invalid_response_body_classified(self) -> None:
        """Non-JSON daemon body should not leak the parse error to the agent."""
        from agentcloak.core.errors import AgentBrowserError

        client = DaemonClient(host="127.0.0.1", port=19999, auto_start=False)
        with patch("httpx.Client") as mock_cls:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.request = MagicMock(return_value=_make_response(500, b"<html>"))
            mock_cls.return_value = ctx

            with pytest.raises(AgentBrowserError) as exc_info:
                client.health_sync()
            assert exc_info.value.error == "daemon_invalid_response"


class TestDaemonClientAsync:
    """MCP tools use the async path — same envelope, same exceptions."""

    @pytest.mark.asyncio
    async def test_connect_error_raises_when_auto_start_off(self) -> None:
        client = DaemonClient(host="127.0.0.1", port=19999, auto_start=False)
        with patch("httpx.AsyncClient") as mock_cls:
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=False)
            ctx.request = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_cls.return_value = ctx

            with pytest.raises(DaemonConnectionError) as exc_info:
                await client.health()
            assert exc_info.value.error == "daemon_unreachable"

    @pytest.mark.asyncio
    async def test_error_envelope_raises_agent_browser_error(self) -> None:
        client = DaemonClient(host="127.0.0.1", port=19999, auto_start=False)
        body = orjson.dumps(
            {
                "ok": False,
                "error": "navigation_failed",
                "hint": "bad URL",
                "action": "check URL",
            }
        )
        with patch("httpx.AsyncClient") as mock_cls:
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=False)
            ctx.request = AsyncMock(return_value=_make_response(400, body))
            mock_cls.return_value = ctx

            with pytest.raises(AgentBrowserError) as exc_info:
                await client.navigate("http://bad")
            assert exc_info.value.error == "navigation_failed"


class TestAutoStartGuards:
    """Auto-start reconnect: when _auto_started=True and daemon is gone,
    the client probes health, resets _auto_started, and attempts re-spawn."""

    def test_reconnect_on_daemon_gone_sync(self) -> None:
        client = DaemonClient(host="127.0.0.1", port=19998, auto_start=True)
        client._auto_started = True
        with (
            patch("httpx.Client") as mock_cls,
            patch.object(client, "_probe_health_sync", return_value=False),
            patch.object(client, "_ensure_daemon_sync", return_value=False),
        ):
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.request = MagicMock(side_effect=httpx.ConnectError("gone"))
            mock_cls.return_value = ctx

            with pytest.raises(DaemonConnectionError):
                client.health_sync()
            # _auto_started was reset so re-spawn was attempted
            assert not client._auto_started

    @pytest.mark.asyncio
    async def test_reconnect_on_daemon_gone_async(self) -> None:
        client = DaemonClient(host="127.0.0.1", port=19998, auto_start=True)
        client._auto_started = True
        with (
            patch("httpx.AsyncClient") as mock_cls,
            patch.object(client, "_probe_health_async", return_value=False),
            patch.object(client, "_ensure_daemon_async", return_value=False),
        ):
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=ctx)
            ctx.__aexit__ = AsyncMock(return_value=False)
            ctx.request = AsyncMock(side_effect=httpx.ConnectError("gone"))
            mock_cls.return_value = ctx

            with pytest.raises(DaemonConnectionError):
                await client.health()
            assert not client._auto_started
