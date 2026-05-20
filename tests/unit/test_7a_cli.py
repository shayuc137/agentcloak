"""CLI command tests for the 7a capability batch (CliRunner + mocked daemon).

The daemon HTTP layer is mocked at ``DaemonClient._send_sync`` (and
``pdf_sync`` for the PDF command, which decodes base64 locally), mirroring the
existing ``test_cli_commands.py`` approach.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from agentcloak.cli import output as cli_output
from agentcloak.cli.app import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_cli_mode() -> Any:
    cli_output.set_json_mode(enabled=False)
    cli_output.set_pretty(enabled=False)
    yield
    cli_output.set_json_mode(enabled=False)
    cli_output.set_pretty(enabled=False)


def _envelope(data: dict[str, Any], *, seq: int = 1) -> dict[str, Any]:
    return {"ok": True, "seq": seq, "data": data}


# ---------------------------------------------------------------------------
# Console (R1)
# ---------------------------------------------------------------------------


class TestConsoleCli:
    def test_show_text(self) -> None:
        payload = _envelope(
            {
                "entries": [
                    {
                        "seq": 1,
                        "level": "error",
                        "text": "boom",
                        "url": "https://x/a.js",
                        "line": 3,
                        "is_error": True,
                    }
                ],
                "seq": 1,
            }
        )
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["console", "show", "--level", "error"])
        assert result.exit_code == 0, result.stdout
        assert "[error] boom" in result.stdout
        assert "seq=1" in result.stdout

    def test_clear_routes_to_clear_endpoint(self) -> None:
        payload = _envelope({"cleared": True})
        with patch(
            "agentcloak.client.DaemonClient._send_sync", return_value=payload
        ) as m:
            result = runner.invoke(app, ["console", "show", "--clear"])
        assert result.exit_code == 0, result.stdout
        assert "console cleared" in result.stdout
        assert m.call_args.args[1] == "/console/clear"


# ---------------------------------------------------------------------------
# Download (R2)
# ---------------------------------------------------------------------------


class TestDownloadCli:
    def test_url_text(self) -> None:
        payload = _envelope({"path": "/tmp/f.pdf", "size": 1024})
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["download", "url", "https://x/f.pdf"])
        assert result.exit_code == 0, result.stdout
        assert "saved /tmp/f.pdf (1024 bytes)" in result.stdout

    def test_list_json(self) -> None:
        payload = _envelope({"downloads": [], "count": 0}, seq=2)
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["--json", "download", "list"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["data"]["count"] == 0


# ---------------------------------------------------------------------------
# Storage (R4)
# ---------------------------------------------------------------------------


class TestStorageCli:
    def test_get_single_value_bare(self) -> None:
        payload = _envelope({"type": "local", "key": "token", "value": "abc"})
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["storage", "get", "token"])
        assert result.exit_code == 0, result.stdout
        assert result.stdout.strip() == "abc"

    def test_set_confirmation(self) -> None:
        payload = _envelope({"type": "local", "key": "k", "set": True})
        with patch(
            "agentcloak.client.DaemonClient._send_sync", return_value=payload
        ) as m:
            result = runner.invoke(app, ["storage", "set", "k", "v"])
        assert result.exit_code == 0, result.stdout
        assert "set local.k" in result.stdout
        body = m.call_args.kwargs["json_body"]
        assert body == {"type": "local", "key": "k", "value": "v"}

    def test_get_full_dump(self) -> None:
        payload = _envelope(
            {"type": "local", "key": None, "value": {"a": "1", "b": "2"}}
        )
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["storage", "get"])
        assert result.exit_code == 0, result.stdout
        assert "a=1" in result.stdout
        assert "b=2" in result.stdout


# ---------------------------------------------------------------------------
# Clipboard (R5)
# ---------------------------------------------------------------------------


class TestClipboardCli:
    def test_read_bare(self) -> None:
        payload = _envelope({"text": "clip content"})
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["clipboard", "read"])
        assert result.exit_code == 0, result.stdout
        assert result.stdout.strip() == "clip content"

    def test_write(self) -> None:
        payload = _envelope({"written": True, "length": 5})
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["clipboard", "write", "hello"])
        assert result.exit_code == 0, result.stdout
        assert "wrote 5 chars" in result.stdout


# ---------------------------------------------------------------------------
# Cookies CRUD (R3)
# ---------------------------------------------------------------------------


class TestCookiesCrudCli:
    def test_set_direct(self) -> None:
        payload = _envelope({"set": 1, "seq": 2})
        with patch(
            "agentcloak.client.DaemonClient._send_sync", return_value=payload
        ) as m:
            result = runner.invoke(
                app, ["cookies", "set", "sid", "abc", "--domain", ".x.com"]
            )
        assert result.exit_code == 0, result.stdout
        assert "set 1 cookies" in result.stdout
        body = m.call_args.kwargs["json_body"]
        assert body["cookies"][0]["name"] == "sid"
        assert body["cookies"][0]["domain"] == ".x.com"

    def test_set_curl(self) -> None:
        payload = _envelope({"set": 2})
        with patch(
            "agentcloak.client.DaemonClient._send_sync", return_value=payload
        ) as m:
            result = runner.invoke(
                app, ["cookies", "set", "--curl", "curl https://x -H 'Cookie: a=1'"]
            )
        assert result.exit_code == 0, result.stdout
        assert m.call_args.kwargs["json_body"]["curl"]

    def test_set_no_args_errors(self) -> None:
        result = runner.invoke(app, ["cookies", "set"])
        # Business error → exit 1, no daemon call.
        assert result.exit_code == 1

    def test_clear(self) -> None:
        payload = _envelope({"cleared": True, "seq": 2})
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["cookies", "clear"])
        assert result.exit_code == 0, result.stdout
        assert "cleared all cookies" in result.stdout

    def test_delete(self) -> None:
        payload = _envelope({"deleted": 1, "name": "sid", "seq": 2})
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["cookies", "delete", "sid"])
        assert result.exit_code == 0, result.stdout
        assert 'deleted 1 cookie named "sid"' in result.stdout


# ---------------------------------------------------------------------------
# Serve (R7)
# ---------------------------------------------------------------------------


class TestServeCli:
    def test_status(self) -> None:
        payload = _envelope({"running": False})
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["serve", "status"])
        assert result.exit_code == 0, result.stdout
        assert "not running" in result.stdout

    def test_start(self, tmp_path: Path) -> None:
        payload = _envelope(
            {
                "running": True,
                "directory": str(tmp_path),
                "port": 12345,
                "url": "http://127.0.0.1:12345",
            }
        )
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["serve", "start", str(tmp_path)])
        assert result.exit_code == 0, result.stdout
        assert "http://127.0.0.1:12345" in result.stdout


# ---------------------------------------------------------------------------
# PDF (R6) — uses pdf_sync (base64 → local file write)
# ---------------------------------------------------------------------------


class TestPdfCli:
    def test_pdf_writes_local_file(self, tmp_path: Path) -> None:
        raw = b"%PDF-1.4 fake"
        payload = _envelope(
            {"base64": base64.b64encode(raw).decode(), "size": len(raw)}
        )
        dest = tmp_path / "page.pdf"
        with patch("agentcloak.client.DaemonClient.pdf_sync", return_value=payload):
            result = runner.invoke(app, ["pdf", "-o", str(dest)])
        assert result.exit_code == 0, result.stdout
        assert dest.read_bytes() == raw
        assert str(dest) in result.stdout

    def test_pdf_options_forwarded(self, tmp_path: Path) -> None:
        raw = b"x"
        payload = _envelope({"base64": base64.b64encode(raw).decode(), "size": 1})
        dest = tmp_path / "p.pdf"
        with patch(
            "agentcloak.client.DaemonClient.pdf_sync", return_value=payload
        ) as m:
            result = runner.invoke(
                app, ["pdf", "-o", str(dest), "--format", "Letter", "--landscape"]
            )
        assert result.exit_code == 0, result.stdout
        assert m.call_args.kwargs["format"] == "Letter"
        assert m.call_args.kwargs["landscape"] is True
