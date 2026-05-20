"""MCP tool tests for the 7a capability batch.

Each tool is registered against a ``FastMCP`` instance with a mock
:class:`DaemonClient`; the tool's ``.fn`` is awaited directly and the rendered
string / dispatched client call are asserted. The client's async methods return
the standard ``{ok, seq, data}`` envelope so ``format_call`` renders the inner
payload through the shared text renderers.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest
from mcp.server.fastmcp import FastMCP

from agentcloak.mcp.tools import clipboard, console, download, pdf, serve, storage


def _envelope(data: dict[str, Any], *, seq: int = 1) -> dict[str, Any]:
    return {"ok": True, "seq": seq, "data": data}


def _tool(module: Any, name: str, client: MagicMock) -> Any:
    mcp = FastMCP("t")
    module.register(mcp, client)
    return mcp._tool_manager._tools[name].fn  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Console (R1)
# ---------------------------------------------------------------------------


class TestConsoleTool:
    @pytest.mark.asyncio
    async def test_show(self) -> None:
        client = MagicMock()
        client.console = AsyncMock(
            return_value=_envelope(
                {"entries": [{"seq": 1, "level": "error", "text": "boom"}], "seq": 1}
            )
        )
        fn = _tool(console, "agentcloak_console", client)
        out = await fn(action="show", level="error")
        assert "[error] boom" in out
        client.console.assert_awaited_once_with(since=0, limit=0, level="error")

    @pytest.mark.asyncio
    async def test_clear(self) -> None:
        client = MagicMock()
        client.console_clear = AsyncMock(return_value=_envelope({"cleared": True}))
        fn = _tool(console, "agentcloak_console", client)
        out = await fn(action="clear")
        assert "console cleared" in out


# ---------------------------------------------------------------------------
# Download (R2)
# ---------------------------------------------------------------------------


class TestDownloadTool:
    @pytest.mark.asyncio
    async def test_url(self) -> None:
        client = MagicMock()
        client.download_url = AsyncMock(
            return_value=_envelope({"path": "/tmp/f.pdf", "size": 10})
        )
        fn = _tool(download, "agentcloak_download", client)
        out = await fn(action="url", url="https://x/f.pdf")
        assert "saved /tmp/f.pdf (10 bytes)" in out
        client.download_url.assert_awaited_once_with(
            url="https://x/f.pdf", output_dir=None
        )

    @pytest.mark.asyncio
    async def test_list(self) -> None:
        client = MagicMock()
        client.download_list = AsyncMock(
            return_value=_envelope({"downloads": [], "count": 0})
        )
        fn = _tool(download, "agentcloak_download", client)
        out = await fn(action="list")
        assert "no downloads" in out


# ---------------------------------------------------------------------------
# Storage (R4)
# ---------------------------------------------------------------------------


class TestStorageTool:
    @pytest.mark.asyncio
    async def test_get(self) -> None:
        client = MagicMock()
        client.storage_get = AsyncMock(
            return_value=_envelope({"type": "local", "key": "k", "value": "v"})
        )
        fn = _tool(storage, "agentcloak_storage", client)
        out = await fn(action="get", key="k")
        assert out == "v"
        client.storage_get.assert_awaited_once_with(type="local", key="k")

    @pytest.mark.asyncio
    async def test_set_missing_key_errors(self) -> None:
        client = MagicMock()
        client.storage_set = AsyncMock()
        fn = _tool(storage, "agentcloak_storage", client)
        out = await fn(action="set", key="", value="v")
        assert orjson.loads(out)["error"] == "missing_key"
        client.storage_set.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_clear(self) -> None:
        client = MagicMock()
        client.storage_clear = AsyncMock(
            return_value=_envelope({"type": "session", "cleared": True})
        )
        fn = _tool(storage, "agentcloak_storage", client)
        out = await fn(action="clear", type="session")
        assert "cleared sessionStorage" in out


# ---------------------------------------------------------------------------
# Clipboard (R5)
# ---------------------------------------------------------------------------


class TestClipboardTool:
    @pytest.mark.asyncio
    async def test_read(self) -> None:
        client = MagicMock()
        client.clipboard_read = AsyncMock(return_value=_envelope({"text": "x"}))
        fn = _tool(clipboard, "agentcloak_clipboard", client)
        assert await fn(action="read") == "x"

    @pytest.mark.asyncio
    async def test_write(self) -> None:
        client = MagicMock()
        client.clipboard_write = AsyncMock(
            return_value=_envelope({"written": True, "length": 3})
        )
        fn = _tool(clipboard, "agentcloak_clipboard", client)
        out = await fn(action="write", text="abc")
        assert "wrote 3 chars" in out


# ---------------------------------------------------------------------------
# PDF (R6)
# ---------------------------------------------------------------------------


class TestPdfTool:
    @pytest.mark.asyncio
    async def test_pdf_requires_output_path_and_writes(self) -> None:
        client = MagicMock()
        client.pdf = AsyncMock(
            return_value=_envelope({"path": "/tmp/a.pdf", "size": 99})
        )
        fn = _tool(pdf, "agentcloak_pdf", client)
        out = await fn(output_path="/tmp/a.pdf", format="A4")
        assert "saved /tmp/a.pdf (99 bytes)" in out
        client.pdf.assert_awaited_once_with(
            format="A4",
            landscape=False,
            scale=None,
            page_ranges=None,
            output_path="/tmp/a.pdf",
        )


# ---------------------------------------------------------------------------
# Serve (R7)
# ---------------------------------------------------------------------------


class TestServeTool:
    @pytest.mark.asyncio
    async def test_start(self) -> None:
        client = MagicMock()
        client.serve_start = AsyncMock(
            return_value=_envelope(
                {"running": True, "directory": "/tmp", "url": "http://127.0.0.1:9"}
            )
        )
        fn = _tool(serve, "agentcloak_serve", client)
        out = await fn(action="start", directory="/tmp")
        assert "serving /tmp at http://127.0.0.1:9" in out

    @pytest.mark.asyncio
    async def test_start_missing_dir_errors(self) -> None:
        client = MagicMock()
        client.serve_start = AsyncMock()
        fn = _tool(serve, "agentcloak_serve", client)
        out = await fn(action="start", directory="")
        assert orjson.loads(out)["error"] == "missing_directory"
        client.serve_start.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_status(self) -> None:
        client = MagicMock()
        client.serve_status = AsyncMock(return_value=_envelope({"running": False}))
        fn = _tool(serve, "agentcloak_serve", client)
        assert "not running" in await fn(action="status")
