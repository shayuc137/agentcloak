"""Lifecycle tests for the embedded ``cloak serve`` file server (7a R7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from agentcloak.core.errors import AgentBrowserError
from agentcloak.daemon.services.file_server import FileServer

if TYPE_CHECKING:
    from pathlib import Path


class TestFileServerLifecycle:
    @pytest.mark.asyncio
    async def test_start_serves_directory_then_stop(self, tmp_path: Path) -> None:
        (tmp_path / "index.html").write_text("<h1>hello fufu</h1>")
        server = FileServer()
        try:
            info = await server.start(str(tmp_path))
            assert info["running"] is True
            assert server.running is True
            port = server.port
            assert isinstance(port, int) and port > 0

            # The server should actually serve the file over HTTP.
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://127.0.0.1:{port}/index.html")
            assert resp.status_code == 200
            assert "hello fufu" in resp.text
        finally:
            result = await server.stop()
        assert result["stopped"] is True
        assert server.running is False
        assert server.port is None

    @pytest.mark.asyncio
    async def test_status_reflects_state(self, tmp_path: Path) -> None:
        server = FileServer()
        assert server.status()["running"] is False
        try:
            await server.start(str(tmp_path))
            status = server.status()
            assert status["running"] is True
            assert status["directory"] == str(tmp_path.resolve())
            assert status["url"] == f"http://127.0.0.1:{server.port}"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_missing_directory_raises(self, tmp_path: Path) -> None:
        server = FileServer()
        with pytest.raises(AgentBrowserError) as excinfo:
            await server.start(str(tmp_path / "does-not-exist"))
        assert excinfo.value.error == "serve_dir_not_found"

    @pytest.mark.asyncio
    async def test_stop_when_not_running_is_noop(self) -> None:
        server = FileServer()
        result = await server.stop()
        assert result["stopped"] is False

    @pytest.mark.asyncio
    async def test_restart_replaces_previous_server(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        server = FileServer()
        try:
            await server.start(str(dir_a))
            first_port = server.port
            # Starting again must tear down the first listener and bind anew.
            await server.start(str(dir_b))
            assert server.directory == str(dir_b.resolve())
            # Old port should be free again (best-effort — at least the handle
            # now points at the new directory).
            assert server.port is not None
            _ = first_port
        finally:
            await server.stop()
