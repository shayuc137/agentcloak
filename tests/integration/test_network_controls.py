"""Real-browser route, script and console regression coverage."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentcloak.browser.managers.route_manager import RouteRule


async def test_route_script_and_tab_replay(
    browser_context: Any, local_server: str
) -> None:
    ctx = browser_context
    await ctx.route_manager.remove(None)
    for identifier in ctx.script_manager.list_scripts():
        await ctx.script_manager.remove(identifier)
    identifier = await ctx.script_manager.add("window.fixtureInit = true")
    assert (await ctx.script_manager.statuses())[identifier] == "not injected"
    rule = RouteRule("network-data.txt", "fulfill", body="Mock response")
    await ctx.route_manager.add(rule)
    try:
        await ctx.navigate(f"{local_server}/network-controls.html?first")
        await ctx.wait(
            condition="js",
            value="document.querySelector('#status').textContent === 'Mock response'",
        )
        assert await ctx.evaluate("window.initSeen") is True
        assert (await ctx.script_manager.statuses())[identifier] == "injected"
        assert rule.hits == 1
        original = next(tab.tab_id for tab in await ctx.tab_list() if tab.active)
        added = await ctx.tab_new(f"{local_server}/network-controls.html?new-tab")
        await ctx.wait(
            condition="js",
            value="document.querySelector('#status').textContent === 'Mock response'",
        )
        assert await ctx.evaluate("window.initSeen") is True
        assert rule.hits == 2
        await ctx.tab_switch(original)
        assert set((await ctx.script_manager.statuses()).values()) == {"injected"}
        await ctx.navigate(f"{local_server}/network-controls.html?back")
        assert await ctx.evaluate("window.initSeen") is True
        await ctx.tab_switch(added["tab_id"])
        await ctx.tab_close(added["tab_id"])
        await ctx.navigate(f"{local_server}/network-controls.html?after-close")
        assert await ctx.evaluate("window.initSeen") is True
    finally:
        await ctx.route_manager.remove(None)
        for identifier in ctx.script_manager.list_scripts():
            await ctx.script_manager.remove(identifier)


async def test_hold_snapshot_screenshot_release(
    browser_context: Any, local_server: str
) -> None:
    ctx = browser_context
    rule = RouteRule("network-data.txt", "hold")
    await ctx.route_manager.add(rule)
    try:
        await asyncio.wait_for(
            ctx.navigate(f"{local_server}/network-controls.html?hold"), 5
        )
        async with asyncio.timeout(5):
            while not ctx.route_manager.pending():
                await asyncio.sleep(0.01)
        snapshot = await ctx.snapshot(mode="accessible")
        assert "Loading" in snapshot.tree_text
        screenshot = await asyncio.wait_for(ctx.screenshot(), 5)
        assert screenshot
        pending = ctx.route_manager.pending()
        assert rule.hits == 1 and len(pending) == 1
        assert ctx.route_manager.release(pending[0]["identifier"]) == 1
        await ctx.wait(
            condition="js",
            value="document.querySelector('#status').textContent==='Backend response'",
        )
        assert ctx.route_manager.pending() == []
    finally:
        await ctx.route_manager.remove(None)


async def test_console_two_documents_and_clear(
    browser_context: Any, local_server: str
) -> None:
    ctx = browser_context
    await ctx.console_clear()
    for page in ("one", "two"):
        await ctx.navigate(f"{local_server}/network-controls.html?{page}")
        await ctx.evaluate("console.log('late:' + location.search)")
    await ctx.evaluate(
        "location.href = " + repr(f"{local_server}/network-controls.html?three")
    )
    await ctx.wait(condition="url", value="**/*?three")
    async with asyncio.timeout(5):
        while not any(
            e["text"] == "fixture:?three"
            for e in (await ctx.console_entries())["entries"]
        ):
            await asyncio.sleep(0.01)
    entries = (await ctx.console_entries())["entries"]
    rows = [e for e in entries if e["text"].startswith("fixture:")]
    late = [e["text"] for e in entries if e["text"].startswith("late:")]
    assert late == ["late:?one", "late:?two"]
    assert [e["text"] for e in rows] == [
        "fixture:?one",
        "fixture:?two",
        "fixture:?three",
    ]
    assert all(
        e["timestamp"] > 0 and e["page_url"].endswith(e["text"].split(":")[1])
        for e in rows
    )
    await ctx.console_clear()
    assert (await ctx.console_entries())["entries"] == []


async def test_hold_cleanup_on_remove_and_tab_close(
    browser_context: Any, local_server: str
) -> None:
    ctx = browser_context
    rule = RouteRule("network-data.txt", "hold")
    await ctx.route_manager.add(rule)
    await ctx.navigate(f"{local_server}/network-controls.html?remove")
    async with asyncio.timeout(5):
        while not ctx.route_manager.pending():
            await asyncio.sleep(0.01)
    await ctx.route_manager.remove(None)
    await ctx.wait(
        condition="js",
        value="document.querySelector('#status').textContent==='Backend response'",
    )
    assert ctx.route_manager.pending() == []
    await ctx.route_manager.add(RouteRule("network-data.txt", "hold"))
    try:
        tab = await ctx.tab_new(f"{local_server}/network-controls.html?close")
        async with asyncio.timeout(5):
            while not ctx.route_manager.pending():
                await asyncio.sleep(0.01)
        await ctx.tab_close(tab["tab_id"])
        assert ctx.route_manager.pending() == []
    finally:
        await ctx.route_manager.remove(None)


async def test_failed_tab_switch_preserves_init_script(
    browser_context: Any, local_server: str
) -> None:
    from agentcloak.core.errors import ElementNotFoundError

    ctx = browser_context
    identifier = await ctx.script_manager.add("window.fixtureInit = true")
    try:
        await ctx.navigate(f"{local_server}/network-controls.html?before-failure")
        with pytest.raises(ElementNotFoundError):
            await ctx.tab_switch(99999)
        await ctx.navigate(f"{local_server}/network-controls.html?after-failure")
        assert await ctx.evaluate("window.initSeen") is True
        assert (await ctx.script_manager.statuses())[identifier] == "injected"
    finally:
        for identifier in ctx.script_manager.list_scripts():
            await ctx.script_manager.remove(identifier)


async def test_background_tab_error_keeps_source_page(
    browser_context: Any, local_server: str
) -> None:
    ctx = browser_context
    origin = f"{local_server}/network-controls.html?error-origin"
    await ctx.navigate(origin)
    original = next(tab.tab_id for tab in await ctx.tab_list() if tab.active)
    background = ctx._tabs[original]
    added = await ctx.tab_new(f"{local_server}/network-controls.html?foreground")
    try:
        await ctx.console_clear()
        await background.evaluate(
            "(() => { const script = document.createElement('script'); "
            'script.textContent = "setTimeout(() => { '
            "throw new Error('background-origin') }, 0)\"; "
            "document.body.append(script); })()"
        )
        async with asyncio.timeout(5):
            while True:
                rows = [
                    e
                    for e in (await ctx.console_entries())["entries"]
                    if "background-origin" in e["text"]
                ]
                if rows:
                    break
                await asyncio.sleep(0.01)
        assert len(rows) == 1
        assert all(e["page_url"] == origin for e in rows)
    finally:
        await ctx.tab_close(added["tab_id"])


@pytest.mark.parametrize("kind", ["log", "error"])
async def test_timer_message_survives_immediate_self_navigation(
    browser_context: Any, kind: str
) -> None:
    ctx = browser_context
    await ctx.navigate("about:blank?timer-start")
    await ctx.console_clear()
    message = f"timer-before-navigation-{kind}"
    action = {
        "log": f"console.log('{message}')",
        "error": f"throw new Error('{message}')",
    }[kind]
    redirect = "location.href = 'about:blank?timer-next'"
    body = (
        f"{action}; {redirect}"
        if kind == "log"
        else f"setTimeout(() => {{ {redirect} }}, 0); {action}"
    )
    await ctx.evaluate(f"setTimeout(() => {{ {body} }}, 50)")
    await asyncio.sleep(0.2)
    entries = (await ctx.console_entries())["entries"]
    rows = [entry for entry in entries if message in entry["text"]]
    assert len(rows) == 1
    assert rows[0]["page_url"] == "about:blank?timer-start"
    assert rows[0]["is_error"] is (kind != "log")
    assert (await ctx.console_entries())["entries"] == entries
    await ctx.console_clear()
    assert (await ctx.console_entries())["entries"] == []


async def test_console_setup_on_existing_page_preserves_console_and_closes(
    browser_context: Any, local_server: str
) -> None:
    ctx = await browser_context.fork_session()
    try:
        await ctx._page.goto(f"{local_server}/network-controls.html?existing")
        await ctx.evaluate("window.savedDebug = console.debug; true")
        await ctx.console_clear()
        assert await ctx.evaluate("console.debug === window.savedDebug") is True
        await ctx.evaluate(
            "console.log('Error: ordinary text'); "
            "setTimeout(() => { throw new Error('current-document-error') }, 0)"
        )
        async with asyncio.timeout(3):
            while not any(
                "current-document-error" in entry["text"]
                for entry in (await ctx.console_entries())["entries"]
            ):
                await asyncio.sleep(0.01)
        rows = (await ctx.console_entries())["entries"]
        ordinary = next(
            entry for entry in rows if entry["text"] == "Error: ordinary text"
        )
        assert ordinary["is_error"] is False
        assert (
            len([entry for entry in rows if "current-document-error" in entry["text"]])
            == 1
        )
    finally:
        await ctx.close()
    assert not ctx._cdp_sessions
    assert not ctx._console_native_tabs


async def test_unhandled_rejection_is_captured_once(browser_context: Any) -> None:
    ctx = browser_context
    await ctx.navigate("about:blank?rejection")
    await ctx.console_clear()
    await ctx.evaluate(
        "setTimeout(() => { "
        "Promise.reject(new Error('unhandled-rejection-probe')) }, 0)"
    )
    async with asyncio.timeout(3):
        while not any(
            "unhandled-rejection-probe" in entry["text"]
            for entry in (await ctx.console_entries())["entries"]
        ):
            await asyncio.sleep(0.01)
    entries = (await ctx.console_entries())["entries"]
    rows = [entry for entry in entries if "unhandled-rejection-probe" in entry["text"]]
    assert len(rows) == 1
    assert rows[0]["is_error"] is True
    assert rows[0]["page_url"] == "about:blank?rejection"


async def test_console_source_follows_spa_history_and_hash(
    browser_context: Any,
) -> None:
    ctx = browser_context
    await ctx.navigate("about:blank?spa-start")
    await ctx.console_clear()
    await ctx.evaluate("history.pushState(null, '', '#push'); console.log('spa-push')")
    await ctx.evaluate("location.hash = 'hash'; console.log('spa-hash')")
    rows = {
        entry["text"]: entry["page_url"]
        for entry in (await ctx.console_entries())["entries"]
        if entry["text"].startswith("spa-")
    }
    assert rows == {
        "spa-push": "about:blank?spa-start#push",
        "spa-hash": "about:blank?spa-start#hash",
    }
