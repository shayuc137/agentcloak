"""DaemonClient profile learning and auto-restart tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from agentcloak.client.daemon_client import DaemonClient


def _make_client() -> DaemonClient:
    with patch.object(DaemonClient, "__init__", lambda self, **kw: None):
        client = DaemonClient.__new__(DaemonClient)
    client._host = "127.0.0.1"
    client._port = 18765
    client._base = "http://127.0.0.1:18765"
    client._auto_start = True
    client._auto_started = False
    client._learned_profile = None
    client._request_timeout_s = 30.0
    client._connect_timeout_s = 5.0
    client._startup_budget_s = 10.0
    client._poll_interval_s = 0.2
    client._session_id = "default"
    client._cfg = MagicMock()
    return client


def test_learn_profile_from_health_response() -> None:
    client = _make_client()
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = {"active_profile": "dos", "version": "0.3.4"}
    client._learn_profile(resp)
    assert client._learned_profile == "dos"


def test_learn_profile_skips_empty() -> None:
    client = _make_client()
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = {"active_profile": "", "version": "0.3.4"}
    client._learn_profile(resp)
    assert client._learned_profile is None


def test_learn_profile_survives_json_error() -> None:
    client = _make_client()
    resp = MagicMock(spec=httpx.Response)
    resp.json.side_effect = ValueError("not json")
    client._learn_profile(resp)
    assert client._learned_profile is None


def test_respawn_passes_learned_profile() -> None:
    client = _make_client()
    client._auto_started = True
    client._learned_profile = "dos"

    with (
        patch.object(client, "_probe_health_sync", return_value=False),
        patch.object(client, "_ensure_daemon_sync", return_value=True) as ensure,
        patch.object(client, "_do_request_sync", return_value={"ok": True}),
    ):
        client._handle_connect_error_sync(
            httpx.ConnectError("refused"),
            "GET",
            "/health",
            json_body=None,
            params=None,
        )

    ensure.assert_called_once_with(profile="dos")
