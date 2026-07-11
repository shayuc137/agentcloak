"""Direct unit tests for hide routes and observation restoration."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcloak.browser.managers.hide_manager import HideManager
from agentcloak.core.config import AgentcloakConfig
from agentcloak.core.errors import AgentBrowserError
from agentcloak.daemon.models import HideAddRequest, HideRemoveRequest
from agentcloak.daemon.routes.browser import handle_screenshot
from agentcloak.daemon.routes.hide import (
    handle_hide_add,
    handle_hide_list,
    handle_hide_remove,
)

if TYPE_CHECKING:
    from fastapi import Request

    from agentcloak.browser.base import BrowserContextBase


def _request(profile: str | None = None) -> Request:
    request = MagicMock()
    request.app.state.local_profile = profile
    return cast("Request", request)


@pytest.mark.asyncio
async def test_hide_routes_delegate_and_report_session_scope() -> None:
    manager = MagicMock()
    manager.add = AsyncMock(return_value="hide-abc")
    manager.remove = AsyncMock(return_value=True)
    manager.list_selectors.return_value = [
        {
            "identifier": "builtin",
            "selector": "[data-cloak-hide]",
            "builtin": True,
        }
    ]
    ctx = MagicMock(seq=4, hide_manager=manager)
    request = _request()

    added = await handle_hide_add(HideAddRequest(selector=" .toolbar "), ctx, request)
    listed = await handle_hide_list(ctx, request)
    removed = await handle_hide_remove(
        HideRemoveRequest(identifier_or_selector="hide-abc"), ctx, request
    )

    manager.add.assert_awaited_once_with(".toolbar")
    manager.remove.assert_awaited_once_with("hide-abc")
    assert added["data"]["scope"] == "session-only"
    assert listed["data"]["count"] == 1
    assert removed["data"] == {"removed": True, "scope": "session-only"}


@pytest.mark.asyncio
async def test_profile_hide_route_persists_manager_state() -> None:
    manager = MagicMock()
    manager.add = AsyncMock(return_value="hide-abc")
    manager.persistent_selectors.return_value = [".toolbar"]
    ctx = MagicMock(seq=1, hide_manager=manager)

    with patch("agentcloak.daemon.routes.hide._persist") as persist:
        await handle_hide_add(HideAddRequest(selector=".toolbar"), ctx, _request("dos"))

    persist.assert_called_once_with("dos", [".toolbar"])


@pytest.mark.asyncio
async def test_blank_hide_selector_raises_structured_invalid_argument() -> None:
    ctx = MagicMock(seq=0, hide_manager=MagicMock())

    with pytest.raises(AgentBrowserError) as raised:
        await handle_hide_add(HideAddRequest(selector="   "), ctx, _request())

    assert raised.value.error == "invalid_argument"


@pytest.mark.asyncio
async def test_screenshot_route_restores_hide_style_after_capture_error() -> None:
    ctx = MagicMock(seq=0)
    ctx._cdp_send = AsyncMock(return_value={"identifier": "hide-script"})
    ctx._evaluate_impl = AsyncMock()
    ctx.screenshot = AsyncMock(side_effect=RuntimeError("capture failed"))
    manager = HideManager(cast("BrowserContextBase", ctx))
    ctx.hide_manager = manager

    with pytest.raises(RuntimeError, match="capture failed"):
        await handle_screenshot(
            ctx=ctx,
            config=AgentcloakConfig(),
            full_page=False,
            format="png",
            quality=80,
            output_path="",
            wait_selector="",
            wait_timeout=None,
            hide=None,
            keep_overlays=True,
        )

    scripts = [call.args[0] for call in ctx._evaluate_impl.await_args_list]
    assert any("style.disabled=true" in script for script in scripts)
    assert "style.disabled=false" in scripts[-1]
