"""Daemon portfile read + stale-cleanup (06-02 routing fix, PRD R3).

The daemon writes ``~/.agentcloak/daemon.json`` (pid/port/host/version) after
binding its port so a CLI invocation discovers the live port even when the
daemon stepped off the default 18765. :func:`DaemonClient._read_daemon_file`
is the read side; these tests pin its four outcomes — fresh, stale (dead pid),
missing, malformed — and assert the stale file is removed so a future read
falls back to the configured default instead of a dead port.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import orjson

from agentcloak.client.daemon_client import _read_daemon_file


def _paths_for(tmp_path: Path) -> SimpleNamespace:
    """Minimal stand-in exposing only the ``daemon_file`` attribute read."""
    return SimpleNamespace(daemon_file=tmp_path / "daemon.json")


def _write_portfile(tmp_path: Path, *, pid: int, host: str, port: int) -> Path:
    target = tmp_path / "daemon.json"
    target.write_bytes(orjson.dumps({"pid": pid, "host": host, "port": port}))
    return target


def test_fresh_portfile_alive_pid_returns_host_port(tmp_path: Path) -> None:
    """A portfile whose pid is alive yields its recorded host/port."""
    _write_portfile(tmp_path, pid=os.getpid(), host="192.168.1.108", port=18770)
    host, port = _read_daemon_file(_paths_for(tmp_path))
    assert host == "192.168.1.108"
    assert port == 18770


def test_wildcard_host_normalized_to_localhost(tmp_path: Path) -> None:
    """Wildcard hosts (0.0.0.0, ::) are normalized to 127.0.0.1 for CLI connection."""
    _write_portfile(tmp_path, pid=os.getpid(), host="0.0.0.0", port=18770)
    host, port = _read_daemon_file(_paths_for(tmp_path))
    assert host == "127.0.0.1"
    assert port == 18770


def test_stale_portfile_dead_pid_returns_none_and_unlinks(tmp_path: Path) -> None:
    """A dead pid is treated as stale: returns ``(None, None)`` and deletes the file."""
    # PID 0x7FFFFFFF is astronomically unlikely to be a live process.
    portfile = _write_portfile(tmp_path, pid=0x7FFFFFFF, host="127.0.0.1", port=18766)
    host, port = _read_daemon_file(_paths_for(tmp_path))
    assert (host, port) == (None, None)
    # Stale file must be removed so the next read falls back to the default port.
    assert not portfile.exists()


def test_missing_portfile_returns_none(tmp_path: Path) -> None:
    """No portfile at all → ``(None, None)`` (caller falls back to config default)."""
    host, port = _read_daemon_file(_paths_for(tmp_path))
    assert (host, port) == (None, None)


def test_malformed_portfile_returns_none(tmp_path: Path) -> None:
    """A corrupt portfile is swallowed, not raised — caller uses the default."""
    (tmp_path / "daemon.json").write_bytes(b"{not valid json")
    host, port = _read_daemon_file(_paths_for(tmp_path))
    assert (host, port) == (None, None)


def test_portfile_without_pid_still_returns_host_port(tmp_path: Path) -> None:
    """A pid-less portfile skips the liveness probe and returns host/port as-is."""
    (tmp_path / "daemon.json").write_bytes(orjson.dumps({"host": "1.2.3.4", "port": 9}))
    host, port = _read_daemon_file(_paths_for(tmp_path))
    assert host == "1.2.3.4"
    assert port == 9
