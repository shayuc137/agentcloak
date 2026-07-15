"""Daemon route tests for the 7a capability batch.

Covers console (R1), download (R2), cookies CRUD (R3), storage (R4),
clipboard (R5), pdf (R6), serve (R7), and the screenshot ``output_path``
extension (R8). The browser context is a MagicMock with AsyncMock methods so
the routes can be exercised without a real browser; ``ctx.seq`` is a
PropertyMock to mirror the live counter.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from fastapi.testclient import TestClient

from agentcloak.daemon.app import create_app

if TYPE_CHECKING:
    from pathlib import Path


def _ctx() -> MagicMock:
    ctx = MagicMock()
    type(ctx).seq = PropertyMock(return_value=7)
    return ctx


def _client(ctx: MagicMock) -> TestClient:
    app = create_app()
    app.state.browser_ctx = ctx
    return TestClient(app)


# ---------------------------------------------------------------------------
# R1: Console
# ---------------------------------------------------------------------------


class TestConsoleRoutes:
    def test_console_show(self) -> None:
        ctx = _ctx()
        ctx.console_entries = AsyncMock(
            return_value={
                "entries": [
                    {
                        "seq": 1,
                        "level": "error",
                        "text": "boom",
                        "url": "https://x/app.js",
                        "line": 10,
                        "column": 2,
                        "is_error": True,
                        "timestamp": 1.0,
                    }
                ],
                "seq": 1,
            }
        )
        resp = _client(ctx).get("/console", params={"since": 0, "level": "error"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["entries"][0]["text"] == "boom"
        ctx.console_entries.assert_awaited_once_with(since=0, limit=0, level="error")

    def test_console_clear(self) -> None:
        ctx = _ctx()
        ctx.console_clear = AsyncMock(return_value={"cleared": True})
        resp = _client(ctx).post("/console/clear")
        assert resp.status_code == 200
        assert resp.json()["data"]["cleared"] is True


# ---------------------------------------------------------------------------
# R2: Download
# ---------------------------------------------------------------------------


class TestDownloadRoutes:
    def test_download_url(self) -> None:
        ctx = _ctx()
        ctx.download_url = AsyncMock(
            return_value={
                "filename": "f.pdf",
                "path": "/tmp/f.pdf",
                "size": 1234,
                "url": "https://x/f.pdf",
                "source": "url",
                "seq": 8,
            }
        )
        resp = _client(ctx).post("/download/url", json={"url": "https://x/f.pdf"})
        assert resp.status_code == 200
        assert resp.json()["data"]["path"] == "/tmp/f.pdf"
        # output_dir defaults to the system temp dir when omitted.
        args, kwargs = ctx.download_url.await_args
        assert args[0] == "https://x/f.pdf"
        assert kwargs["output_dir"]

    def test_download_wait(self) -> None:
        ctx = _ctx()
        ctx.download_wait = AsyncMock(
            return_value={
                "filename": "report.csv",
                "path": "/tmp/report.csv",
                "size": 99,
                "url": "",
                "source": "event",
            }
        )
        resp = _client(ctx).post("/download/wait", json={})
        assert resp.status_code == 200
        assert resp.json()["data"]["source"] == "event"

    def test_download_list(self) -> None:
        ctx = _ctx()
        ctx.download_list = AsyncMock(return_value={"downloads": [], "count": 0})
        resp = _client(ctx).get("/download/list")
        assert resp.status_code == 200
        assert resp.json()["data"]["count"] == 0

    def test_download_url_ssrf_blocked_bubbles(self) -> None:
        from agentcloak.core.errors import SecurityError

        ctx = _ctx()
        ctx.download_url = AsyncMock(
            side_effect=SecurityError(
                error="download_target_blocked",
                hint="resolves to a non-public address",
                action="blocked",
            )
        )
        resp = _client(ctx).post(
            "/download/url", json={"url": "http://169.254.169.254/"}
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "download_target_blocked"


# ---------------------------------------------------------------------------
# R3: Cookies CRUD
# ---------------------------------------------------------------------------


class TestCookieCrudRoutes:
    def test_cookies_set_direct(self) -> None:
        ctx = _ctx()
        ctx.cookies_set = AsyncMock(return_value={"set": 1, "seq": 8})
        resp = _client(ctx).post(
            "/cookies/set",
            json={"cookies": [{"name": "a", "value": "b", "domain": "x", "path": "/"}]},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["set"] == 1

    def test_cookies_set_from_curl(self) -> None:
        ctx = _ctx()
        captured: dict[str, Any] = {}

        async def _set(cookies: list[dict[str, Any]]) -> dict[str, Any]:
            captured["cookies"] = cookies
            return {"set": len(cookies), "seq": 8}

        ctx.cookies_set = AsyncMock(side_effect=_set)
        curl = "curl 'https://example.com/' -H 'Cookie: sid=abc; t=1'"
        resp = _client(ctx).post("/cookies/set", json={"curl": curl})
        assert resp.status_code == 200
        names = {c["name"] for c in captured["cookies"]}
        assert names == {"sid", "t"}

    def test_cookies_set_empty_is_400(self) -> None:
        ctx = _ctx()
        ctx.cookies_set = AsyncMock()
        resp = _client(ctx).post("/cookies/set", json={})
        assert resp.status_code == 400
        assert resp.json()["error"] == "no_cookies"
        ctx.cookies_set.assert_not_awaited()

    def test_cookies_clear(self) -> None:
        ctx = _ctx()
        ctx.cookies_clear = AsyncMock(return_value={"cleared": True, "seq": 8})
        resp = _client(ctx).post("/cookies/clear")
        assert resp.status_code == 200
        assert resp.json()["data"]["cleared"] is True

    def test_cookies_delete(self) -> None:
        ctx = _ctx()
        ctx.cookies_delete = AsyncMock(
            return_value={"deleted": 2, "name": "sid", "seq": 8}
        )
        resp = _client(ctx).post(
            "/cookies/delete", json={"name": "sid", "domain": ".x.com"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] == 2
        ctx.cookies_delete.assert_awaited_once_with("sid", domain=".x.com")


# ---------------------------------------------------------------------------
# R4: Storage
# ---------------------------------------------------------------------------


class TestStorageRoutes:
    def test_storage_get_single_key(self) -> None:
        ctx = _ctx()
        ctx.evaluate = AsyncMock(side_effect=["https://example.com/page", "tok"])
        resp = _client(ctx).post("/storage/get", json={"type": "local", "key": "token"})
        assert resp.status_code == 200
        assert resp.json()["data"]["value"] == "tok"
        js = ctx.evaluate.await_args.args[0]
        assert 'getItem("token")' in js

    def test_storage_set(self) -> None:
        ctx = _ctx()
        ctx.evaluate = AsyncMock(side_effect=["https://example.com/", None])
        resp = _client(ctx).post(
            "/storage/set", json={"type": "session", "key": "k", "value": "v"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["set"] is True
        assert "sessionStorage" in ctx.evaluate.await_args.args[0]

    def test_storage_delete(self) -> None:
        ctx = _ctx()
        ctx.evaluate = AsyncMock(side_effect=["https://example.com/", None])
        resp = _client(ctx).post("/storage/delete", json={"key": "k"})
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] is True

    def test_storage_clear(self) -> None:
        ctx = _ctx()
        ctx.evaluate = AsyncMock(side_effect=["https://example.com/", None])
        resp = _client(ctx).post("/storage/clear", json={"type": "local"})
        assert resp.status_code == 200
        assert resp.json()["data"]["cleared"] is True

    def test_storage_invalid_type_is_400(self) -> None:
        ctx = _ctx()
        ctx.evaluate = AsyncMock(side_effect=["https://example.com/", None])
        resp = _client(ctx).post("/storage/get", json={"type": "cookies"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_storage_type"

    def test_storage_origin_error_on_about_blank(self) -> None:
        ctx = _ctx()
        ctx.evaluate = AsyncMock(return_value="about:blank")
        resp = _client(ctx).post("/storage/get", json={"type": "local"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "storage_origin_error"


# ---------------------------------------------------------------------------
# R5: Clipboard
# ---------------------------------------------------------------------------


class TestClipboardRoutes:
    def test_clipboard_read(self) -> None:
        ctx = _ctx()
        ctx.clipboard_read = AsyncMock(return_value={"text": "copied"})
        resp = _client(ctx).get("/clipboard/read")
        assert resp.status_code == 200
        assert resp.json()["data"]["text"] == "copied"

    def test_clipboard_write(self) -> None:
        ctx = _ctx()
        ctx.clipboard_write = AsyncMock(return_value={"written": True, "length": 5})
        resp = _client(ctx).post("/clipboard/write", json={"text": "hello"})
        assert resp.status_code == 200
        assert resp.json()["data"]["length"] == 5
        ctx.clipboard_write.assert_awaited_once_with("hello")


# ---------------------------------------------------------------------------
# R6: PDF
# ---------------------------------------------------------------------------


class TestPdfRoute:
    def test_pdf_base64_when_no_output_path(self) -> None:
        ctx = _ctx()
        ctx.pdf = AsyncMock(return_value=b"%PDF-1.4 fake")
        resp = _client(ctx).post("/pdf", json={"format": "A4"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert base64.b64decode(data["base64"]) == b"%PDF-1.4 fake"
        assert data["size"] == len(b"%PDF-1.4 fake")

    def test_pdf_writes_file_with_output_path(self, tmp_path: Path) -> None:
        ctx = _ctx()
        ctx.pdf = AsyncMock(return_value=b"%PDF-1.4 fake")
        dest = tmp_path / "out.pdf"
        resp = _client(ctx).post(
            "/pdf", json={"format": "A4", "output_path": str(dest)}
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["path"] == str(dest)
        assert dest.read_bytes() == b"%PDF-1.4 fake"

    def test_pdf_options_forwarded(self) -> None:
        ctx = _ctx()
        ctx.pdf = AsyncMock(return_value=b"x")
        _client(ctx).post(
            "/pdf",
            json={"format": "Letter", "landscape": True, "page_ranges": "1-2"},
        )
        opts = ctx.pdf.await_args.kwargs["options"]
        assert opts["format"] == "Letter"
        assert opts["landscape"] is True
        assert opts["pageRanges"] == "1-2"

    def test_pdf_not_supported_bubbles(self) -> None:
        from agentcloak.core.errors import BackendError

        ctx = _ctx()
        ctx.pdf = AsyncMock(
            side_effect=BackendError(
                error="pdf_not_supported",
                hint="needs headless",
                action="restart headless",
            )
        )
        resp = _client(ctx).post("/pdf", json={})
        assert resp.status_code == 400
        assert resp.json()["error"] == "pdf_not_supported"


# ---------------------------------------------------------------------------
# R7: Serve
# ---------------------------------------------------------------------------


class TestServeRoutes:
    def test_serve_start_stop_status(self, tmp_path: Path) -> None:
        app = create_app()
        client = TestClient(app)

        resp = client.post("/serve/start", json={"directory": str(tmp_path)})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["running"] is True
        port = data["port"]

        status = client.get("/serve/status").json()["data"]
        assert status["running"] is True
        assert status["port"] == port

        stopped = client.post("/serve/stop").json()["data"]
        assert stopped["stopped"] is True

        # After stop, status reports not running.
        assert client.get("/serve/status").json()["data"]["running"] is False

    def test_serve_missing_dir_is_error(self, tmp_path: Path) -> None:
        app = create_app()
        client = TestClient(app)
        resp = client.post("/serve/start", json={"directory": str(tmp_path / "nope")})
        assert resp.status_code == 400
        assert resp.json()["error"] == "serve_dir_not_found"


# ---------------------------------------------------------------------------
# R8: Screenshot output_path
# ---------------------------------------------------------------------------


class TestScreenshotOutputPath:
    def test_screenshot_writes_file_when_output_path(self, tmp_path: Path) -> None:
        ctx = _ctx()
        ctx.screenshot = AsyncMock(return_value=b"\x89PNGfake")
        dest = tmp_path / "shot.png"
        resp = _client(ctx).get(
            "/screenshot", params={"format": "png", "output_path": str(dest)}
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["path"] == str(dest)
        assert "base64" not in data or not data["base64"]
        assert dest.read_bytes() == b"\x89PNGfake"

    def test_screenshot_base64_without_output_path(self) -> None:
        ctx = _ctx()
        ctx.screenshot = AsyncMock(return_value=b"\x89PNGfake")
        resp = _client(ctx).get("/screenshot", params={"format": "png"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert base64.b64decode(data["base64"]) == b"\x89PNGfake"


@pytest.mark.parametrize(
    "path,method",
    [
        ("/console", "GET"),
        ("/console/clear", "POST"),
        ("/download/list", "GET"),
        ("/storage/get", "POST"),
        ("/clipboard/read", "GET"),
        ("/pdf", "POST"),
        ("/cookies/clear", "POST"),
    ],
)
def test_routes_registered(path: str, method: str) -> None:
    """Every 7a route must be wired into the FastAPI app."""
    app = create_app()
    spec = app.openapi()
    assert path in spec["paths"]
    assert method.lower() in spec["paths"][path]
