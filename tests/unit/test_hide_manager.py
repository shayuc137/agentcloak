"""HideManager selector, CSS injection, and temporary toggle tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcloak.browser.managers.hide_manager import HideManager

if TYPE_CHECKING:
    from agentcloak.browser.base import BrowserContextBase


def _manager() -> tuple[HideManager, MagicMock]:
    ids = iter(("script-1", "script-2", "script-3", "script-4"))

    async def _send(method: str, _params: dict[str, Any]) -> dict[str, Any]:
        if method == "Page.addScriptToEvaluateOnNewDocument":
            return {"identifier": next(ids)}
        return {}

    ctx = MagicMock()
    ctx._cdp_send = AsyncMock(side_effect=_send)
    ctx._evaluate_impl = AsyncMock()
    return HideManager(cast("BrowserContextBase", ctx)), ctx


@pytest.mark.asyncio
async def test_add_builds_builtin_and_user_css_for_init_and_current_page() -> None:
    manager, ctx = _manager()

    identifier = await manager.add(".toolbar")

    assert identifier.startswith("hide-")
    assert manager.css_for() == (
        "[data-cloak-hide], .toolbar { display: none !important; }"
    )
    source = ctx._cdp_send.await_args_list[-1].args[1]["source"]
    assert 'const id="__cloak_hide__"' in source
    assert "display: none !important" in source
    ctx._evaluate_impl.assert_awaited_once_with(source, world="main")


@pytest.mark.asyncio
async def test_builtin_is_always_listed_and_cannot_be_removed() -> None:
    manager, ctx = _manager()

    assert await manager.remove("builtin") is False
    assert await manager.remove(HideManager.BUILTIN) is False
    assert manager.list_selectors() == [
        {
            "identifier": "builtin",
            "selector": "[data-cloak-hide]",
            "source": "builtin",
            "builtin": True,
        }
    ]
    ctx._cdp_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_observation_restores_persistent_style_when_body_raises() -> None:
    manager, ctx = _manager()
    await manager.add(".persistent")
    ctx._evaluate_impl.reset_mock()

    with pytest.raises(RuntimeError, match="capture failed"):
        async with manager.observation(keep_overlays=True):
            raise RuntimeError("capture failed")

    calls = [call.args[0] for call in ctx._evaluate_impl.await_args_list]
    assert "style.disabled=true" in calls[0]
    assert ".persistent" in calls[-1]
    assert "style.disabled=false" in calls[-1]


def test_css_for_extra_is_deduplicated_and_keep_overlays_disables_all() -> None:
    manager, _ = _manager()

    assert manager.css_for([HideManager.BUILTIN, ".once", ".once"]) == (
        "[data-cloak-hide], .once { display: none !important; }"
    )
    assert manager.css_for([".once"], keep_overlays=True) is None


@pytest.mark.asyncio
async def test_navigation_reapplies_current_style_without_reregistering() -> None:
    manager, ctx = _manager()
    await manager.add(".toolbar")
    ctx._cdp_send.reset_mock()
    ctx._evaluate_impl.reset_mock()

    await manager.on_navigated()

    ctx._cdp_send.assert_not_awaited()
    source = ctx._evaluate_impl.await_args.args[0]
    assert ".toolbar" in source


@pytest.mark.asyncio
async def test_load_sets_profile_source_add_sets_session_source() -> None:
    manager, _ctx = _manager()
    await manager.load([".overlay", ".banner"])
    selectors = manager.list_selectors()

    profile_entries = [s for s in selectors if s["source"] == "profile"]
    assert len(profile_entries) == 2
    assert {s["selector"] for s in profile_entries} == {".overlay", ".banner"}

    builtin_entry = [s for s in selectors if s["source"] == "builtin"]
    assert len(builtin_entry) == 1

    await manager.add(".extra")
    selectors = manager.list_selectors()
    session_entries = [s for s in selectors if s["source"] == "session"]
    assert len(session_entries) == 1
    assert session_entries[0]["selector"] == ".extra"
