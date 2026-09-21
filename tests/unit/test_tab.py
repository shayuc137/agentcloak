"""Tests for multi-tab management in PlaywrightContext."""

from __future__ import annotations

from typing import Any
from unittest.mock import DEFAULT, AsyncMock, MagicMock

import pytest

from agentcloak.browser.playwright_ctx import PlaywrightContext
from agentcloak.core.errors import ElementNotFoundError
from agentcloak.core.seq import RingBuffer, SeqCounter
from tests.image_data import PNG


def _default_page(
    url: str = "https://example.com", title: str = "Example"
) -> MagicMock:
    page = MagicMock()
    page.on = MagicMock()
    page.url = url
    page.title = AsyncMock(return_value=title)
    page.goto = AsyncMock(return_value=MagicMock(status=200))
    page.evaluate = AsyncMock(return_value="result")

    def evaluate_identity(js: str, *args: Any, **kwargs: Any) -> Any:
        if "performance.timeOrigin" in js:
            return {
                "url": page.url,
                "title": "Example",
                "viewport": {"width": 1280, "height": 720},
                "dpr": 1,
                "document_id": 123,
            }
        return DEFAULT

    page.evaluate.side_effect = evaluate_identity
    page.screenshot = AsyncMock(return_value=PNG)
    page.content = AsyncMock(return_value="<html><body>Hello</body></html>")
    page.close = AsyncMock()
    page.context = MagicMock()
    page.context.new_cdp_session = AsyncMock(return_value=_mock_cdp_session())
    page.context.new_page = AsyncMock()
    return page


def _mock_cdp_session(eval_value: Any = "result") -> MagicMock:
    cdp = MagicMock()
    _listeners: dict[str, list] = {}

    def _on(event: str, callback: Any) -> None:
        _listeners.setdefault(event, []).append(callback)

    async def _send(method: str, params: Any = None) -> Any:
        if method == "Accessibility.getFullAXTree":
            return {
                "nodes": [
                    {"role": {"value": "RootWebArea"}, "name": {"value": "Test"}},
                    {"role": {"value": "link"}, "name": {"value": "Click me"}},
                ]
            }
        if method == "Runtime.enable":
            main_ctx = {
                "context": {
                    "id": 1,
                    "origin": "https://example.com",
                    "name": "",
                    "auxData": {"isDefault": True, "type": "default", "frameId": "F1"},
                }
            }
            for cb in _listeners.get("Runtime.executionContextCreated", []):
                cb(main_ctx)
            return {}
        if method == "Runtime.disable":
            return {}
        if method == "Runtime.evaluate":
            return {"result": {"type": "object", "value": eval_value}}
        return {}

    cdp.on = MagicMock(side_effect=_on)
    cdp.send = AsyncMock(side_effect=_send)
    cdp.detach = AsyncMock()
    return cdp


def _make_ctx(
    *,
    page: Any | None = None,
    browser_context: Any | None = None,
) -> PlaywrightContext:
    mock_page = page if page is not None else _default_page()
    mock_browser_ctx = browser_context if browser_context is not None else MagicMock()
    if browser_context is None:
        new_page_mock = _default_page("about:blank", "")
        mock_browser_ctx.new_page = AsyncMock(return_value=new_page_mock)
    return PlaywrightContext(
        page=mock_page,
        browser=MagicMock(),
        playwright=MagicMock(),
        seq_counter=SeqCounter(),
        ring_buffer=RingBuffer(),
        browser_context=mock_browser_ctx,
    )


class TestTabList:
    @pytest.mark.asyncio
    async def test_initial_tab(self) -> None:
        """Launch creates tab 0 from the initial page."""
        ctx = _make_ctx()
        tabs = await ctx.tab_list()
        assert len(tabs) == 1
        assert tabs[0].tab_id == 0
        assert tabs[0].active is True
        assert tabs[0].url == "https://example.com"

    @pytest.mark.asyncio
    async def test_multiple_tabs(self) -> None:
        """After creating tabs, all show up in list."""
        browser_ctx = MagicMock()
        pages: list[MagicMock] = []
        for i in range(3):
            p = _default_page(f"https://tab{i}.com", f"Tab {i}")
            pages.append(p)
        call_count = 0

        async def _new_page() -> MagicMock:
            nonlocal call_count
            result = pages[call_count]
            call_count += 1
            return result

        browser_ctx.new_page = AsyncMock(side_effect=_new_page)

        ctx = _make_ctx(browser_context=browser_ctx)
        await ctx.tab_new()
        await ctx.tab_new()
        tabs = await ctx.tab_list()
        assert len(tabs) == 3
        tab_ids = [t.tab_id for t in tabs]
        assert 0 in tab_ids
        assert 1 in tab_ids
        assert 2 in tab_ids


class TestTabNew:
    @pytest.mark.asyncio
    async def test_create_blank(self) -> None:
        """tab_new without URL returns a new tab."""
        new_page = _default_page("about:blank", "")
        browser_ctx = MagicMock()
        browser_ctx.new_page = AsyncMock(return_value=new_page)

        ctx = _make_ctx(browser_context=browser_ctx)
        result = await ctx.tab_new()
        assert result["tab_id"] == 1
        assert result["url"] == "about:blank"

    @pytest.mark.asyncio
    async def test_create_with_url(self) -> None:
        """tab_new with URL navigates to that URL."""
        new_page = _default_page("https://new.com", "New Page")
        new_page.goto = AsyncMock(return_value=MagicMock(status=200))
        browser_ctx = MagicMock()
        browser_ctx.new_page = AsyncMock(return_value=new_page)

        ctx = _make_ctx(browser_context=browser_ctx)
        result = await ctx.tab_new("https://new.com")
        assert result["tab_id"] == 1
        assert result["url"] == "https://new.com"
        new_page.goto.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_tab_becomes_active(self) -> None:
        """New tab automatically becomes the active tab."""
        new_page = _default_page("about:blank", "")
        browser_ctx = MagicMock()
        browser_ctx.new_page = AsyncMock(return_value=new_page)

        ctx = _make_ctx(browser_context=browser_ctx)
        result = await ctx.tab_new()
        assert ctx._active_tab == result["tab_id"]

    @pytest.mark.asyncio
    async def test_tab_ids_increment(self) -> None:
        """Tab IDs are monotonically increasing."""
        browser_ctx = MagicMock()
        browser_ctx.new_page = AsyncMock(
            side_effect=[
                _default_page("about:blank", ""),
                _default_page("about:blank", ""),
                _default_page("about:blank", ""),
            ]
        )

        ctx = _make_ctx(browser_context=browser_ctx)
        r1 = await ctx.tab_new()
        r2 = await ctx.tab_new()
        r3 = await ctx.tab_new()
        assert r1["tab_id"] == 1
        assert r2["tab_id"] == 2
        assert r3["tab_id"] == 3


class TestTabClose:
    @pytest.mark.asyncio
    async def test_close_nonexistent(self) -> None:
        """Closing a tab that does not exist raises error."""
        ctx = _make_ctx()
        with pytest.raises(ElementNotFoundError) as exc_info:
            await ctx.tab_close(999)
        assert exc_info.value.error == "tab_not_found"

    @pytest.mark.asyncio
    async def test_close_inactive_tab(self) -> None:
        """Closing a non-active tab keeps active tab unchanged."""
        new_page = _default_page("about:blank", "")
        browser_ctx = MagicMock()
        browser_ctx.new_page = AsyncMock(return_value=new_page)

        ctx = _make_ctx(browser_context=browser_ctx)
        await ctx.tab_new()  # tab 1, becomes active
        await ctx.tab_switch(0)  # back to tab 0
        result = await ctx.tab_close(1)
        assert result["closed"] == 1
        assert ctx._active_tab == 0

    @pytest.mark.asyncio
    async def test_close_active_tab_switches_to_max(self) -> None:
        """Closing the active tab switches to the highest remaining ID."""
        browser_ctx = MagicMock()
        browser_ctx.new_page = AsyncMock(
            side_effect=[
                _default_page("about:blank", ""),
                _default_page("about:blank", ""),
            ]
        )

        ctx = _make_ctx(browser_context=browser_ctx)
        await ctx.tab_new()  # tab 1
        await ctx.tab_new()  # tab 2, now active
        await ctx.tab_close(2)
        assert ctx._active_tab == 1

    @pytest.mark.asyncio
    async def test_close_last_tab_auto_creates(self) -> None:
        """Closing the only tab auto-creates an about:blank tab."""
        initial_page = _default_page()
        auto_page = _default_page("about:blank", "")
        browser_ctx = MagicMock()
        browser_ctx.new_page = AsyncMock(return_value=auto_page)

        ctx = _make_ctx(page=initial_page, browser_context=browser_ctx)
        result = await ctx.tab_close(0)
        assert result["closed"] == 0
        assert "auto_created" in result
        new_tab_id = result["auto_created"]
        assert ctx._active_tab == new_tab_id
        tabs = await ctx.tab_list()
        assert len(tabs) == 1
        assert tabs[0].tab_id == new_tab_id


class TestTabSwitch:
    @pytest.mark.asyncio
    async def test_switch_success(self) -> None:
        """Switch changes the active tab."""
        new_page = _default_page("https://other.com", "Other")
        browser_ctx = MagicMock()
        browser_ctx.new_page = AsyncMock(return_value=new_page)

        ctx = _make_ctx(browser_context=browser_ctx)
        await ctx.tab_new()  # tab 1
        await ctx.tab_switch(0)
        assert ctx._active_tab == 0

    @pytest.mark.asyncio
    async def test_switch_nonexistent(self) -> None:
        """Switching to a nonexistent tab raises error."""
        ctx = _make_ctx()
        with pytest.raises(ElementNotFoundError) as exc_info:
            await ctx.tab_switch(42)
        assert exc_info.value.error == "tab_not_found"

    @pytest.mark.asyncio
    async def test_switch_returns_tab_info(self) -> None:
        """Switch returns the tab's url and title."""
        ctx = _make_ctx()
        result = await ctx.tab_switch(0)
        assert result["tab_id"] == 0
        assert result["url"] == "https://example.com"
        assert result["title"] == "Example"


class TestActiveTabSemantics:
    @pytest.mark.asyncio
    async def test_navigate_uses_active_tab(self) -> None:
        """Navigate operates on the active tab's page."""
        page0 = _default_page("https://a.com", "A")
        page1 = _default_page("https://b.com", "B")
        browser_ctx = MagicMock()
        browser_ctx.new_page = AsyncMock(return_value=page1)

        ctx = _make_ctx(page=page0, browser_context=browser_ctx)
        await ctx.tab_new()  # tab 1 is now active, backed by page1
        await ctx.navigate("https://c.com")
        # page1 should have received the goto call, not page0
        page1.goto.assert_called_once()
        page0.goto.assert_not_called()

    @pytest.mark.asyncio
    async def test_screenshot_uses_active_tab(self) -> None:
        """Screenshot reads from the active tab's page."""
        page0 = _default_page()
        page1 = _default_page()
        page1.screenshot = AsyncMock(return_value=PNG)
        browser_ctx = MagicMock()
        browser_ctx.new_page = AsyncMock(return_value=page1)

        ctx = _make_ctx(page=page0, browser_context=browser_ctx)
        await ctx.tab_new()  # active is now tab 1
        result = await ctx.screenshot()
        assert result == PNG
        page1.screenshot.assert_awaited_once()
        page0.screenshot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_evaluate_uses_active_tab(self) -> None:
        """Evaluate runs JS on the active tab's page."""
        page0 = _default_page()
        page1 = _default_page()
        page1.context.new_cdp_session = AsyncMock(
            return_value=_mock_cdp_session(eval_value=42)
        )
        browser_ctx = MagicMock()
        browser_ctx.new_page = AsyncMock(return_value=page1)

        ctx = _make_ctx(page=page0, browser_context=browser_ctx)
        await ctx.tab_new()
        result = await ctx.evaluate("1+1")
        assert result == 42


class TestEphemeralMode:
    @pytest.mark.asyncio
    async def test_tab_new_without_browser_context(self) -> None:
        """In ephemeral mode (_browser_context=None), uses page.context."""
        page = _default_page()
        new_page = _default_page("about:blank", "")
        page.context.new_page = AsyncMock(return_value=new_page)

        ctx = PlaywrightContext(
            page=page,
            browser=MagicMock(),
            playwright=MagicMock(),
            seq_counter=SeqCounter(),
            ring_buffer=RingBuffer(),
            browser_context=None,
        )
        result = await ctx.tab_new()
        assert result["tab_id"] == 1
        page.context.new_page.assert_called_once()
