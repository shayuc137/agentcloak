"""P3 RemoteBridge close cleanup + M2 max_chars line-level truncation.

GPT Pro review findings P3 + M2 (2026-06-27).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcloak.browser.remote_ctx import RemoteBridgeContext


def _make_bridge_ctx() -> RemoteBridgeContext:
    ws = MagicMock()
    ws.closed = False
    ws.close = AsyncMock()
    return RemoteBridgeContext(bridge_ws=ws)


class TestRemoteBridgeCloseCleanup:
    """P3: _close_impl cancels tasks and fails pending futures."""

    @pytest.mark.asyncio
    async def test_close_cancels_route_tasks(self) -> None:
        ctx = _make_bridge_ctx()

        async def forever() -> None:
            await asyncio.sleep(999)

        task = asyncio.create_task(forever())
        ctx._route_tasks.add(task)

        await ctx._close_impl()

        assert task.cancelled()
        assert len(ctx._route_tasks) == 0

    @pytest.mark.asyncio
    async def test_close_cancels_capture_tasks(self) -> None:
        ctx = _make_bridge_ctx()

        async def forever() -> None:
            await asyncio.sleep(999)

        task = asyncio.create_task(forever())
        ctx._capture_tasks.add(task)

        await ctx._close_impl()

        assert task.cancelled()
        assert len(ctx._capture_tasks) == 0

    @pytest.mark.asyncio
    async def test_close_fails_pending_futures(self) -> None:
        ctx = _make_bridge_ctx()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        ctx._pending["test-id"] = fut

        await ctx._close_impl()

        assert fut.done()
        with pytest.raises(Exception, match="Bridge closed"):
            fut.result()
        assert len(ctx._pending) == 0

    @pytest.mark.asyncio
    async def test_close_clears_pending_captures(self) -> None:
        ctx = _make_bridge_ctx()
        ctx._pending_captures["req-1"] = {"url": "https://example.com"}

        await ctx._close_impl()

        assert len(ctx._pending_captures) == 0

    @pytest.mark.asyncio
    async def test_close_still_closes_ws(self) -> None:
        ctx = _make_bridge_ctx()
        await ctx._close_impl()
        ctx._ws.close.assert_awaited_once()


class TestMaxCharsLineTruncation:
    """M2: max_chars truncation preserves complete lines."""

    def test_truncation_preserves_whole_lines(self) -> None:
        from agentcloak.browser._snapshot_builder import build_snapshot

        nodes = [
            {
                "nodeId": 1,
                "role": {"value": "WebArea"},
                "name": {"value": "Test"},
                "childIds": [2, 3, 4],
            },
            {
                "nodeId": 2,
                "role": {"value": "button"},
                "name": {"value": "Submit"},
                "backendDOMNodeId": 10,
                "childIds": [],
            },
            {
                "nodeId": 3,
                "role": {"value": "link"},
                "name": {"value": "Home"},
                "backendDOMNodeId": 11,
                "childIds": [],
            },
            {
                "nodeId": 4,
                "role": {"value": "textbox"},
                "name": {"value": "Search"},
                "backendDOMNodeId": 12,
                "childIds": [],
            },
        ]
        snap = build_snapshot(
            nodes,
            url="https://example.com",
            title="Test",
            seq=1,
            mode="accessible",
            max_chars=50,
        )
        text = snap.snapshot.tree_text
        assert text.endswith("use --offset or --focus to continue]")
        lines = text.split("\n")
        for line in lines[:-1]:
            assert not line.endswith("...")
            if "[" in line and "]" in line:
                bracket_start = line.index("[")
                bracket_end = line.index("]")
                assert bracket_end > bracket_start
