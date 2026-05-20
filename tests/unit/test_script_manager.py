"""ScriptManager — init-script injection (7b T1.1).

The manager only ever touches ``ctx._cdp_send``, so we drive it with a tiny
mock context whose ``_cdp_send`` is an ``AsyncMock``. That keeps the tests
focused on the manager's bookkeeping (identifier map, preset lookup, tab-switch
replay) without standing up a real backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest

from agentcloak.browser.managers.script_manager import (
    PRESET_TEMPLATES,
    ScriptManager,
)

if TYPE_CHECKING:
    from agentcloak.browser.base import BrowserContextBase


def _make_mgr(identifiers: list[str] | None = None) -> tuple[ScriptManager, AsyncMock]:
    """Return a ScriptManager wired to a mock ctx whose _cdp_send is patched.

    ``identifiers`` supplies the sequence of CDP-assigned ids returned by
    successive ``addScriptToEvaluateOnNewDocument`` calls; remove returns an
    empty dict.
    """
    ids = list(identifiers or ["id-1", "id-2", "id-3"])

    async def _cdp_send(method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "Page.addScriptToEvaluateOnNewDocument":
            return {"identifier": ids.pop(0)}
        return {}

    ctx = AsyncMock()
    ctx._cdp_send = AsyncMock(side_effect=_cdp_send)
    mgr = ScriptManager(cast("BrowserContextBase", ctx))
    return mgr, ctx._cdp_send


class TestAdd:
    @pytest.mark.asyncio
    async def test_add_returns_identifier_and_tracks(self) -> None:
        mgr, send = _make_mgr(["abc"])

        ident = await mgr.add("console.log(1)")

        assert ident == "abc"
        assert mgr.list_scripts() == {"abc": "console.log(1)"}
        send.assert_awaited_once_with(
            "Page.addScriptToEvaluateOnNewDocument", {"source": "console.log(1)"}
        )

    @pytest.mark.asyncio
    async def test_add_multiple_distinct_ids(self) -> None:
        mgr, _ = _make_mgr(["a", "b"])

        await mgr.add("js1")
        await mgr.add("js2")

        assert mgr.list_scripts() == {"a": "js1", "b": "js2"}


class TestPreset:
    @pytest.mark.asyncio
    async def test_add_preset_injects_template(self) -> None:
        mgr, send = _make_mgr(["p1"])

        ident = await mgr.add_preset("fetch")

        assert ident == "p1"
        # The injected source is the preset template, not a bare name.
        sent_source = send.await_args.args[1]["source"]
        assert sent_source == PRESET_TEMPLATES["fetch"]
        assert "fetch" in sent_source

    @pytest.mark.asyncio
    async def test_unknown_preset_raises_keyerror(self) -> None:
        mgr, _ = _make_mgr()
        with pytest.raises(KeyError):
            await mgr.add_preset("does-not-exist")

    def test_all_presets_present(self) -> None:
        # The PRD asks for 3-5 presets; assert the documented set exists.
        assert {"fetch", "xhr", "json_parse", "crypto", "timing"} <= set(
            PRESET_TEMPLATES
        )


class TestRemove:
    @pytest.mark.asyncio
    async def test_remove_known_identifier(self) -> None:
        mgr, send = _make_mgr(["x"])
        await mgr.add("js")

        removed = await mgr.remove("x")

        assert removed is True
        assert mgr.list_scripts() == {}
        send.assert_awaited_with(
            "Page.removeScriptToEvaluateOnNewDocument", {"identifier": "x"}
        )

    @pytest.mark.asyncio
    async def test_remove_unknown_identifier_returns_false(self) -> None:
        mgr, _ = _make_mgr()
        removed = await mgr.remove("never-added")
        assert removed is False


class TestTabSwitchReplay:
    @pytest.mark.asyncio
    async def test_replay_reinjects_sources_with_fresh_ids(self) -> None:
        mgr, _ = _make_mgr(["old1", "old2", "new1", "new2"])
        await mgr.add("jsA")
        await mgr.add("jsB")

        await mgr.on_tab_switched()

        # Same sources, new identifiers (the page lost the old injections).
        assert mgr.list_scripts() == {"new1": "jsA", "new2": "jsB"}

    @pytest.mark.asyncio
    async def test_replay_noop_when_empty(self) -> None:
        mgr, send = _make_mgr()
        await mgr.on_tab_switched()
        send.assert_not_awaited()
