"""BrowserContextBase dialog dispatch tests.

Phase 6d hoisted dialog dispatch from per-backend handlers into a single
``_dispatch_dialog_event`` on :class:`BrowserContextBase`. The two
backends now only:

* Normalise their backend-specific payload (Playwright ``Dialog`` /
  CDP ``Page.javascriptDialogOpening``) into the four primitive fields
  the dispatcher accepts.
* Implement :meth:`_auto_accept_dialog_impl` for the alert/beforeunload
  background-accept path.

These tests pin the dispatch contract: alert/beforeunload trigger
``_auto_accept_dialog_impl`` in the background and never block; confirm
and prompt stash a :class:`PendingDialog` and surface as
``DialogBlockedError`` on the next action.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcloak.browser.remote_ctx import RemoteBridgeContext


def _make_ctx() -> RemoteBridgeContext:
    """Pick the bridge backend because its ctor is cheapest to stand up.

    The dispatch logic lives on the base class so the backend choice is
    irrelevant for these tests — we just need *some* concrete subclass
    to instantiate the abstract base.
    """
    ws = MagicMock()
    ws.closed = False
    return RemoteBridgeContext(bridge_ws=ws)


class TestDispatchAlertAndBeforeUnload:
    """alert / beforeunload route to ``_auto_accept_dialog_impl`` in the background."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dialog_type", ["alert", "beforeunload"])
    async def test_auto_accept_path_does_not_block(self, dialog_type: str) -> None:
        ctx = _make_ctx()
        auto_accept = AsyncMock()
        ctx._auto_accept_dialog_impl = auto_accept  # type: ignore[method-assign]

        ctx._dispatch_dialog_event(
            dialog_type=dialog_type,
            message="please confirm",
            default_value="",
            url="https://example.com/",
        )
        # Dispatch is fire-and-forget — _auto_accept runs as a task.
        # Drain the queue so we can assert it was awaited.
        tasks = list(ctx._auto_dialog_tasks)
        assert tasks, "auto-accept must spawn a background task"
        await asyncio.gather(*tasks)

        auto_accept.assert_awaited_once()
        # No pending dialog should be stashed for the agent.
        assert ctx._pending_dialog is None
        # ``_last_auto_dialog`` carries the metadata for feedback collection.
        assert ctx._last_auto_dialog is not None
        assert ctx._last_auto_dialog["type"] == dialog_type

    @pytest.mark.asyncio
    async def test_task_self_cleans_from_strong_ref_set(self) -> None:
        """The spawned task removes itself from ``_auto_dialog_tasks`` on done."""
        ctx = _make_ctx()
        ctx._auto_accept_dialog_impl = AsyncMock()  # type: ignore[method-assign]

        ctx._dispatch_dialog_event(
            dialog_type="alert",
            message="x",
            default_value="",
            url="",
        )
        tasks = list(ctx._auto_dialog_tasks)
        await asyncio.gather(*tasks)
        # ``add_done_callback(self._auto_dialog_tasks.discard)`` cleans up.
        assert ctx._auto_dialog_tasks == set()


class TestDispatchConfirmAndPrompt:
    """confirm / prompt stash :class:`PendingDialog` and block subsequent actions."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dialog_type", ["confirm", "prompt"])
    async def test_pending_dialog_stashed(self, dialog_type: str) -> None:
        ctx = _make_ctx()
        auto_accept = AsyncMock()
        ctx._auto_accept_dialog_impl = auto_accept  # type: ignore[method-assign]

        ctx._dispatch_dialog_event(
            dialog_type=dialog_type,
            message="continue?",
            default_value="value",
            url="https://example.com/",
        )

        # Auto-accept must not run for confirm/prompt — agent decides.
        auto_accept.assert_not_called()
        assert ctx._pending_dialog is not None
        assert ctx._pending_dialog.dialog_type == dialog_type
        assert ctx._pending_dialog.message == "continue?"
        assert ctx._pending_dialog.default_value == "value"

    @pytest.mark.asyncio
    async def test_pending_dialog_blocks_action(self) -> None:
        """Once stashed, the base's ``_raise_if_dialog_blocked`` rejects actions."""
        from agentcloak.core.errors import DialogBlockedError

        ctx = _make_ctx()
        ctx._dispatch_dialog_event(
            dialog_type="confirm",
            message="ok?",
            default_value="",
            url="",
        )

        # Run an action — the base's gate fires before the impl is reached.
        with pytest.raises(DialogBlockedError) as exc_info:
            await ctx.action("click", "1")
        assert exc_info.value.dialog["type"] == "confirm"


class TestBackendNormalisation:
    """The backend event handlers must produce identical dispatch payloads."""

    def test_playwright_normalises_dialog_object_fields(self) -> None:
        """``PlaywrightContext._on_dialog`` extracts type/message/default_value/url."""
        # Import lazily so we don't need a real Page object — just confirm the
        # extraction path. We use a stub Dialog to mimic Playwright's shape.
        from agentcloak.browser.playwright_ctx import PlaywrightContext

        captured: dict[str, Any] = {}

        def fake_dispatch(
            *,
            dialog_type: str,
            message: str,
            default_value: str,
            url: str,
        ) -> None:
            captured.update(
                dialog_type=dialog_type,
                message=message,
                default_value=default_value,
                url=url,
            )

        # Build a context without going through the full launcher.
        ctx_cls = PlaywrightContext
        ctx = ctx_cls.__new__(ctx_cls)
        ctx._dispatch_dialog_event = fake_dispatch  # type: ignore[method-assign,assignment]
        # _on_dialog reads self._page.url for the source URL.
        page_stub = MagicMock()
        page_stub.url = "https://example.com/"
        ctx._tabs = {0: page_stub}
        ctx._active_tab = 0
        ctx._dialog_object = None

        dialog_stub = MagicMock()
        dialog_stub.type = "prompt"
        dialog_stub.message = "Enter name"
        dialog_stub.default_value = "anonymous"

        ctx._on_dialog(dialog_stub)
        assert captured == {
            "dialog_type": "prompt",
            "message": "Enter name",
            "default_value": "anonymous",
            "url": "https://example.com/",
        }
        # The Playwright Dialog must be stashed for later accept/dismiss.
        assert ctx._dialog_object is dialog_stub

    def test_remote_normalises_cdp_params(self) -> None:
        """``RemoteBridgeContext._handle_dialog_event`` extracts CDP fields."""
        ctx = _make_ctx()
        captured: dict[str, Any] = {}

        def fake_dispatch(
            *,
            dialog_type: str,
            message: str,
            default_value: str,
            url: str,
        ) -> None:
            captured.update(
                dialog_type=dialog_type,
                message=message,
                default_value=default_value,
                url=url,
            )

        ctx._dispatch_dialog_event = fake_dispatch  # type: ignore[method-assign,assignment]
        ctx._handle_dialog_event(
            {
                "type": "confirm",
                "message": "Delete?",
                "defaultPrompt": "yes",
            }
        )
        # CDP uses ``defaultPrompt``; both backends emit the same canonical
        # ``default_value`` field after normalisation.
        assert captured["dialog_type"] == "confirm"
        assert captured["message"] == "Delete?"
        assert captured["default_value"] == "yes"
        # Remote bridge doesn't surface the page URL through CDP params, so
        # the handler uses a sentinel — assert the contract.
        assert captured["url"] == "(remote)"
