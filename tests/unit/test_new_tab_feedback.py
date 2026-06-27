"""Proactive new_tab feedback tests.

Verifies that _last_new_tab_event is populated when a new tab appears,
so the action result includes new_tab metadata for agent awareness.

GPT Pro review finding P1 (2026-06-27).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from agentcloak.browser.remote_ctx import RemoteBridgeContext


def _make_bridge_ctx() -> RemoteBridgeContext:
    ws = MagicMock()
    ws.closed = False
    return RemoteBridgeContext(bridge_ws=ws)


class TestRemoteBridgeNewTabEvent:
    """Bridge backend processes tab_event created messages."""

    def test_tab_created_sets_new_tab_event(self) -> None:
        ctx = _make_bridge_ctx()
        assert ctx._last_new_tab_event is None

        import json

        ctx.feed_message(
            json.dumps(
                {
                    "type": "tab_event",
                    "event": "created",
                    "tabId": 42,
                    "url": "https://example.com/new",
                }
            )
        )

        assert ctx._last_new_tab_event is not None
        assert ctx._last_new_tab_event["tab_id"] == 42
        assert ctx._last_new_tab_event["url"] == "https://example.com/new"

    def test_tab_updated_does_not_set_new_tab(self) -> None:
        ctx = _make_bridge_ctx()
        import json

        ctx.feed_message(
            json.dumps(
                {
                    "type": "tab_event",
                    "event": "updated",
                    "tabId": 1,
                    "url": "https://example.com",
                }
            )
        )
        assert ctx._last_new_tab_event is None

    def test_tab_removed_does_not_set_new_tab(self) -> None:
        ctx = _make_bridge_ctx()
        import json

        ctx.feed_message(
            json.dumps({"type": "tab_event", "event": "removed", "tabId": 1})
        )
        assert ctx._last_new_tab_event is None


class TestCollectFeedbackNewTab:
    """_collect_feedback includes new_tab and clears the slot."""

    @pytest.mark.asyncio
    async def test_new_tab_appears_in_feedback(self) -> None:
        ctx = _make_bridge_ctx()
        ctx._last_new_tab_event = {"tab_id": 5, "url": "https://new.page/"}

        result: dict[str, Any] = {"ok": True}
        ctx._collect_feedback(result)

        assert "new_tab" in result
        assert result["new_tab"]["tab_id"] == 5
        assert result["new_tab"]["url"] == "https://new.page/"
        assert ctx._last_new_tab_event is None

    @pytest.mark.asyncio
    async def test_no_new_tab_no_field(self) -> None:
        ctx = _make_bridge_ctx()
        result: dict[str, Any] = {"ok": True}
        ctx._collect_feedback(result)
        assert "new_tab" not in result
