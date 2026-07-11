"""Tests for remote bridge — CLI, RemoteBridgeContext, extension files.

The standalone bridge process (``bridge/server.py`` + ``bridge/config.py``)
was removed in the 06-02 routing fix: the Chrome extension now connects
directly to the daemon's ``/ext`` WebSocket. Tests that exercised the
standalone process / its config were dropped with it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from agentcloak.browser.remote_ctx import RemoteBridgeContext
from agentcloak.cli.app import app
from agentcloak.core.types import StealthTier

runner = CliRunner()


class TestRemoteBridgeContext:
    def test_stealth_tier(self) -> None:
        ws = MagicMock()
        ws.closed = False
        ctx = RemoteBridgeContext(bridge_ws=ws)
        assert ctx.stealth_tier == StealthTier.REMOTE_BRIDGE

    def test_seq_starts_at_zero(self) -> None:
        ws = MagicMock()
        ws.closed = False
        ctx = RemoteBridgeContext(bridge_ws=ws)
        assert ctx.seq == 0

    def test_feed_message_resolves_pending(self) -> None:
        import asyncio

        ws = MagicMock()
        ws.closed = False
        ctx = RemoteBridgeContext(bridge_ws=ws)

        loop = asyncio.new_event_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        ctx._pending["test-id"] = fut

        ctx.feed_message(json.dumps({"id": "test-id", "ok": True, "data": {}}))
        assert fut.done()
        result = fut.result()
        assert result["ok"] is True
        loop.close()

    def test_feed_message_ignores_invalid_json(self) -> None:
        ws = MagicMock()
        ctx = RemoteBridgeContext(bridge_ws=ws)
        ctx.feed_message("not json")

    def test_feed_message_ignores_unknown_id(self) -> None:
        ws = MagicMock()
        ctx = RemoteBridgeContext(bridge_ws=ws)
        ctx.feed_message(json.dumps({"id": "unknown", "ok": True}))

    def test_feed_message_stores_prompt_dialog_as_pending(self) -> None:
        """confirm/prompt dialogs must surface as ``_pending_dialog``.

        ``Page.javascriptDialogOpening`` for alert + beforeunload auto-accepts
        (covered separately because the auto-accept path schedules an async
        task that needs a running loop). confirm/prompt block on agent input
        and *must* live in ``_pending_dialog`` so the dialog status route
        returns the right state.
        """
        ws = MagicMock()
        ws.closed = False
        ctx = RemoteBridgeContext(bridge_ws=ws)
        assert ctx._pending_dialog is None

        ctx.feed_message(
            json.dumps(
                {
                    "type": "cdp_event",
                    "method": "Page.javascriptDialogOpening",
                    "params": {
                        "type": "prompt",
                        "message": "Enter your name",
                        "defaultPrompt": "Anonymous",
                        "url": "https://example.com/",
                    },
                }
            )
        )

        pending = ctx._pending_dialog
        assert pending is not None
        assert pending.dialog_type == "prompt"
        assert pending.message == "Enter your name"
        assert pending.default_value == "Anonymous"

    def test_feed_message_stores_confirm_dialog_as_pending(self) -> None:
        """Confirm dialogs also need to block until the agent decides."""
        ws = MagicMock()
        ws.closed = False
        ctx = RemoteBridgeContext(bridge_ws=ws)

        ctx.feed_message(
            json.dumps(
                {
                    "type": "cdp_event",
                    "method": "Page.javascriptDialogOpening",
                    "params": {
                        "type": "confirm",
                        "message": "Delete this item?",
                        "url": "https://example.com/",
                    },
                }
            )
        )

        pending = ctx._pending_dialog
        assert pending is not None
        assert pending.dialog_type == "confirm"
        assert pending.message == "Delete this item?"

    @pytest.mark.asyncio
    async def test_send_raises_when_disconnected(self) -> None:
        from agentcloak.core.errors import BackendError

        ws = MagicMock()
        ws.closed = True
        ctx = RemoteBridgeContext(bridge_ws=ws)

        with pytest.raises(BackendError, match="Bridge WebSocket"):
            await ctx._send("navigate", {"url": "http://example.com"})


class TestBridgeCLI:
    def test_bridge_help_lists_commands(self) -> None:
        result = runner.invoke(app, ["bridge", "--help"])
        # ``start`` was removed with the standalone bridge; the surviving
        # subcommands are all UX/diagnostic helpers.
        assert "start" not in result.stdout
        assert "doctor" in result.stdout
        assert "claim" in result.stdout
        assert "finalize" in result.stdout
        assert "token" in result.stdout

    def test_bridge_doctor_reports_new_checks(self) -> None:
        # ``--json`` opts back into the envelope shape these assertions rely on.
        # No daemon runs in the unit-test environment, so ``doctor`` exits 1 —
        # we assert on the check *names* (the contract), not pass/fail.
        result = runner.invoke(app, ["--json", "bridge", "doctor"])
        data = json.loads(result.stdout)
        assert "ok" in data
        checks = data["data"]["checks"]
        names = [c["name"] for c in checks]
        # Standalone-bridge checks are gone; the new contract probes the daemon
        # + extension attachment instead.
        assert "bridge_config" not in names
        assert "daemon" in names
        assert "extension_connected" in names
        assert "extension_files" in names

    def test_bridge_extension_path(self) -> None:
        result = runner.invoke(app, ["--json", "bridge", "extension-path"])
        data = json.loads(result.stdout)
        path = data["data"]["path"]
        assert "agentcloak-chrome-extension" in path


class TestExtensionFiles:
    def test_manifest_exists(self) -> None:
        ext_dir = (
            Path(__file__).parent.parent.parent
            / "src"
            / "agentcloak"
            / "bridge"
            / "agentcloak-chrome-extension"
        )
        assert (ext_dir / "manifest.json").is_file()
        assert (ext_dir / "background.js").is_file()
        assert (ext_dir / "options.html").is_file()
        assert (ext_dir / "options.js").is_file()

    def test_evaluate_prefers_exception_description(self) -> None:
        """RemoteBridge must not collapse actionable JS errors to bare Uncaught."""
        ext_dir = (
            Path(__file__).parent.parent.parent
            / "src"
            / "agentcloak"
            / "bridge"
            / "agentcloak-chrome-extension"
        )
        source = (ext_dir / "background.js").read_text(encoding="utf-8")
        block_start = source.index("if (result.exceptionDetails)")
        block_end = source.index("// Runtime.evaluate returns", block_start)
        block = source[block_start:block_end]

        assert block.index("exc.exception?.description") < block.index("usefulText ||")
        assert 'const marker = " ... [truncated]"' in block
        assert "errMsg.length > 400" in block

    def test_manifest_valid(self) -> None:
        ext_dir = (
            Path(__file__).parent.parent.parent
            / "src"
            / "agentcloak"
            / "bridge"
            / "agentcloak-chrome-extension"
        )
        data = json.loads((ext_dir / "manifest.json").read_text())
        assert data["manifest_version"] == 3
        assert data.get("name") == "agentcloak"
        assert "debugger" in data["permissions"]
        assert "cookies" in data["permissions"]
        assert "tabs" in data["permissions"]
        assert "storage" in data["permissions"]
        assert "<all_urls>" in data["host_permissions"]
        assert data.get("options_page") == "options.html"
