"""Legacy ownership discovery must not erase healthy or unreadable records."""

from unittest.mock import AsyncMock

import httpx
import orjson
import pytest

from agentcloak.core.config import Paths, load_config
from agentcloak.core.errors import AgentBrowserError
from agentcloak.daemon import server


def test_state_root_environment(tmp_path, monkeypatch):
    root = tmp_path / "state"
    root.mkdir()
    (root / "config.toml").write_text("[daemon]\nport=19431\n")
    monkeypatch.setenv("AGENTCLOAK_HOME", str(root))
    paths, config = load_config()
    assert paths.root == root
    assert config.daemon.port == 19431
    explicit, _ = load_config(root=tmp_path / "explicit")
    assert explicit.root == tmp_path / "explicit"


async def test_legacy_healthy_daemon_is_not_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCLOAK_HOME", str(tmp_path))
    paths = Paths(tmp_path)
    (tmp_path / "daemon.pid").write_text("2147483647")
    paths.daemon_file.write_bytes(orjson.dumps({"port": 19431, "host": "0.0.0.0"}))
    paths.active_session_file.write_bytes(b'{"port":19431}')
    records = [paths.daemon_file, paths.active_session_file, tmp_path / "daemon.pid"]
    before = [record.read_bytes() for record in records]

    def health(url, **kwargs):
        if ":19431/" in url:
            raise httpx.ConnectError("stale recorded endpoint")
        return httpx.Response(
            200,
            json={"ok": True, "service": "agentcloak-daemon"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(server.httpx, "get", health)
    launch = AsyncMock()
    monkeypatch.setattr(server, "_start_owned", launch)
    with pytest.raises(AgentBrowserError, match="already"):
        await server.start()
    launch.assert_not_called()
    assert [record.read_bytes() for record in records] == before


async def test_startup_failure_releases_ownership(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCLOAK_HOME", str(tmp_path))
    launch = AsyncMock(side_effect=RuntimeError("launch failed"))
    monkeypatch.setattr(server, "_start_owned", launch)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="launch failed"):
            await server.start()
    assert launch.await_count == 2
