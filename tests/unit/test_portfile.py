"""Read-only daemon discovery uses HTTP health, independent of PID namespaces."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from unittest.mock import MagicMock

import httpx
import orjson
import pytest

from agentcloak.client.daemon_client import _read_daemon_file


@pytest.fixture(autouse=True)
def healthy(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    get = MagicMock(
        return_value=httpx.Response(
            200,
            json={"ok": True},
            request=httpx.Request("GET", "http://localhost/health"),
        )
    )
    monkeypatch.setattr("agentcloak.client.daemon_client.httpx.get", get)
    return get


def _paths_for(tmp_path: Path) -> SimpleNamespace:
    """Minimal stand-in exposing only the ``daemon_file`` attribute read."""
    return SimpleNamespace(daemon_file=tmp_path / "daemon.json")


def _write_portfile(
    tmp_path: Path,
    *,
    pid: int,
    host: str,
    port: int,
    profile: str = "",
) -> Path:
    target = tmp_path / "daemon.json"
    target.write_bytes(
        orjson.dumps({"pid": pid, "host": host, "port": port, "profile": profile})
    )
    return target


def test_fresh_portfile_alive_pid_returns_host_port(tmp_path: Path) -> None:
    """A portfile whose pid is alive yields its recorded host/port."""
    _write_portfile(tmp_path, pid=os.getpid(), host="192.168.1.108", port=18770)
    host, port, profile = _read_daemon_file(_paths_for(tmp_path))
    assert host == "192.168.1.108"
    assert port == 18770
    assert profile is None


def test_wildcard_host_normalized_to_localhost(tmp_path: Path) -> None:
    """Wildcard hosts (0.0.0.0, ::) are normalized to 127.0.0.1 for CLI connection."""
    _write_portfile(tmp_path, pid=os.getpid(), host="0.0.0.0", port=18770)
    host, port, _profile = _read_daemon_file(_paths_for(tmp_path))
    assert host == "127.0.0.1"
    assert port == 18770


def test_stale_portfile_retains_original_bytes(
    tmp_path: Path, healthy: MagicMock
) -> None:
    portfile = _write_portfile(tmp_path, pid=0x7FFFFFFF, host="127.0.0.1", port=18766)
    before = portfile.read_bytes()
    healthy.side_effect = httpx.ConnectError("unreachable")
    assert _read_daemon_file(_paths_for(tmp_path)) == (None, None, None)
    assert portfile.read_bytes() == before


def test_invisible_pid_healthy_readonly_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    portfile = _write_portfile(tmp_path, pid=0x7FFFFFFF, host="127.0.0.1", port=18766)
    before = portfile.read_bytes()
    monkeypatch.setattr(
        "agentcloak.core.process.pid_alive",
        MagicMock(side_effect=AssertionError("PID probe forbidden")),
    )
    portfile.chmod(0o444)
    assert _read_daemon_file(_paths_for(tmp_path))[:2] == ("127.0.0.1", 18766)
    assert portfile.read_bytes() == before


def test_missing_portfile_returns_none(tmp_path: Path) -> None:
    """No portfile at all -> all-None (caller falls back to config default)."""
    host, port, profile = _read_daemon_file(_paths_for(tmp_path))
    assert (host, port, profile) == (None, None, None)


def test_malformed_portfile_returns_none(tmp_path: Path) -> None:
    """A corrupt portfile is swallowed, not raised — caller uses the default."""
    (tmp_path / "daemon.json").write_bytes(b"{not valid json")
    host, port, profile = _read_daemon_file(_paths_for(tmp_path))
    assert (host, port, profile) == (None, None, None)


def test_portfile_without_pid_still_returns_host_port(tmp_path: Path) -> None:
    """A pid-less portfile skips the liveness probe and returns host/port as-is."""
    (tmp_path / "daemon.json").write_bytes(orjson.dumps({"host": "1.2.3.4", "port": 9}))
    host, port, _profile = _read_daemon_file(_paths_for(tmp_path))
    assert host == "1.2.3.4"
    assert port == 9


def test_portfile_profile_uses_live_health(tmp_path: Path, healthy: MagicMock) -> None:
    """A switched profile must override the startup record."""
    healthy.return_value = httpx.Response(
        200,
        json={"ok": True, "active_profile": "current"},
        request=httpx.Request("GET", "http://localhost/health"),
    )
    _write_portfile(
        tmp_path, pid=os.getpid(), host="127.0.0.1", port=18765, profile="previous"
    )
    _host, _port, profile = _read_daemon_file(_paths_for(tmp_path))
    assert profile == "current"


def test_portfile_empty_profile_returns_none(tmp_path: Path) -> None:
    """An empty profile string in the portfile is normalized to None."""
    _write_portfile(
        tmp_path, pid=os.getpid(), host="127.0.0.1", port=18765, profile="previous"
    )
    _host, _port, profile = _read_daemon_file(_paths_for(tmp_path))
    assert profile is None
