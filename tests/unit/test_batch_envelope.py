"""Batch action_batch() error envelope tests.

Verifies that step errors within action_batch() use the standard
three-field envelope {ok, error, hint, action} instead of bare str(exc).
GPT Pro review finding P2 (2026-06-27).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcloak.browser.remote_ctx import RemoteBridgeContext
from agentcloak.core.errors import BrowserTimeoutError


def _make_ctx() -> RemoteBridgeContext:
    ws = MagicMock()
    ws.closed = False
    ctx = RemoteBridgeContext(bridge_ws=ws)
    ctx._check_debugger_paused = MagicMock()
    ctx._check_browser_alive = MagicMock()
    ctx._check_page_valid = MagicMock()
    return ctx


class TestBatchWaitErrorEnvelope:
    """Wait step errors in batch should use standard envelope."""

    @pytest.mark.asyncio
    async def test_wait_agentbrowsererror_uses_to_dict(self) -> None:
        ctx = _make_ctx()
        err = BrowserTimeoutError(
            error="wait_timeout",
            hint="Timed out waiting for selector",
            action="check selector or increase timeout",
        )
        ctx.wait = AsyncMock(side_effect=err)

        result = await ctx.action_batch(
            [{"kind": "wait", "condition": "selector", "value": "#foo"}]
        )

        assert result["completed"] == 1
        step = result["results"][0]
        assert step["ok"] is False
        assert step["error"] == "wait_timeout"
        assert "hint" in step
        assert "action" in step
        assert step["step_index"] == 0
        assert step["kind"] == "wait"

    @pytest.mark.asyncio
    async def test_wait_generic_exception_wraps_with_envelope(self) -> None:
        ctx = _make_ctx()
        ctx.wait = AsyncMock(side_effect=RuntimeError("unexpected failure"))

        result = await ctx.action_batch(
            [{"kind": "wait", "condition": "ms", "value": "100"}]
        )

        assert result["completed"] == 1
        step = result["results"][0]
        assert step["ok"] is False
        assert step["error"] == "batch_step_failed"
        assert "unexpected failure" in step["hint"]
        assert "action" in step
        assert step["step_index"] == 0
        assert step["kind"] == "wait"

    @pytest.mark.asyncio
    async def test_wait_error_does_not_abort_batch(self) -> None:
        ctx = _make_ctx()
        call_count = 0

        async def mock_wait(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise BrowserTimeoutError(
                    error="wait_timeout",
                    hint="first wait failed",
                    action="retry",
                )
            return {"ok": True, "waited": "ms", "elapsed_ms": 100}

        ctx.wait = AsyncMock(side_effect=mock_wait)

        result = await ctx.action_batch(
            [
                {"kind": "wait", "condition": "selector", "value": "#a"},
                {"kind": "wait", "condition": "ms", "value": "100"},
            ]
        )

        assert result["completed"] == 2
        assert result["results"][0]["ok"] is False
        assert result["results"][0]["error"] == "wait_timeout"
        assert result["results"][1]["ok"] is True
