"""Scenario F: error recovery — structured errors, auto-start detection."""

from __future__ import annotations

import pytest

from agentcloak.core.errors import DaemonConnectionError


async def test_connection_error_structured() -> None:
    """DaemonConnectionError should carry proper three-field envelope."""
    err = DaemonConnectionError(
        error="daemon_unreachable",
        hint="Cannot connect to daemon at 127.0.0.1:18765",
        action="run 'agentcloak daemon start' first",
    )
    d = err.to_dict()
    assert d["ok"] is False
    assert d["error"] == "daemon_unreachable"
    assert d["hint"]
    assert d["action"]


async def test_client_auto_start_flag() -> None:
    """DaemonClient should have auto_start capability."""
    from agentcloak.client import DaemonClient

    # With auto_start disabled, should raise immediately on connect failure
    client = DaemonClient(port=19999, auto_start=False)
    with pytest.raises(DaemonConnectionError) as exc_info:
        await client.health()
    assert exc_info.value.error == "daemon_unreachable"


async def test_client_reconnect_on_daemon_gone() -> None:
    """After auto-start, if daemon disappears, client resets and re-tries."""
    from unittest.mock import patch

    from agentcloak.client import DaemonClient

    client = DaemonClient(port=19998, auto_start=True)
    client._auto_started = True
    # Mock _ensure_daemon_async so it doesn't actually spawn a process
    with patch.object(client, "_ensure_daemon_async", return_value=False):
        with pytest.raises(DaemonConnectionError):
            await client.health()
    # _auto_started was reset (reconnect logic triggered)
    assert not client._auto_started
