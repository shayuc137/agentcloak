"""DOS #9: dialog decisions preserve the actual page and unsaved state."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from agentcloak.core.errors import AgentBrowserError


@pytest.mark.parametrize("guard", ["beforeunload", "confirm"])
@pytest.mark.parametrize("decision", ["accept", "dismiss"])
async def test_dialog_navigation_recovery(
    browser_context: Any, local_server: str, guard: str, decision: str
) -> None:
    ctx = browser_context
    await ctx.navigate(f"{local_server}/dialog-guard.html")
    try:
        if guard == "beforeunload":
            await ctx.action("click", "", selector="#arm")
        else:
            # The cleanup evaluate in the old implementation can itself wait
            # behind confirm; bound it without answering the user's dialog.
            snap = await ctx.snapshot()
            index = next(
                i
                for i, ref in snap.selector_map.items()
                if ref.text == "SPA navigation"
            )
            task = asyncio.create_task(ctx.action("click", str(index)))
            async with asyncio.timeout(2):
                while await ctx.dialog_status() is None:
                    await asyncio.sleep(0.01)
            try:
                await asyncio.wait_for(asyncio.shield(task), 0.2)
            except TimeoutError:
                pass
            except AgentBrowserError as exc:
                assert exc.error == "blocked_by_dialog"
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        with pytest.raises(AgentBrowserError) as caught:
            await ctx.navigate(f"{local_server}/result.html", timeout=0.5)
        assert caught.value.error == "blocked_by_dialog"
        assert guard in caught.value.hint
        assert "dialog accept" in caught.value.action
        if guard == "confirm":
            assert "Discard unsaved order?" in caught.value.hint
        assert (await ctx.dialog_status()).dialog_type == guard
        await ctx.dialog_handle(decision)
        if decision == "accept":
            expected = "result.html" if guard == "beforeunload" else "#next"
            await ctx.wait(condition="url", value=expected, timeout=3000)
            assert expected in (await ctx.snapshot()).url
        else:
            assert "dialog-guard.html" in (await ctx.snapshot()).url
            assert (
                await ctx.evaluate("document.querySelector('#draft').value")
                == "unsaved order"
            )
        assert (await ctx.resume_snapshot())["page_valid"] is True
    finally:
        if await ctx.dialog_status() is not None:
            await ctx.dialog_handle("dismiss")
        with contextlib.suppress(Exception):
            await ctx.evaluate("window.onbeforeunload=null")
        # The old broken state may forbid evaluate, so use the backend page
        # only for teardown; no test assertion uses this escape hatch.
        with contextlib.suppress(Exception):
            await ctx._page.evaluate("window.onbeforeunload=null")
