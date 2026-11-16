"""DOS #6: ordinary clicks while SSE and two-second polling stay active."""

from __future__ import annotations

import asyncio
import json
import socket
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request  # noqa: TC002 (Starlette runtime annotation)
from starlette.responses import HTMLResponse, Response, StreamingResponse
from starlette.routing import Route

from agentcloak.core.errors import AgentBrowserError

from .test_recovery_evidence import private_api as private_api


def click_site_app() -> Starlette:
    async def page(_: Request) -> Response:
        return HTMLResponse(
            (Path(__file__).parent / "pages" / "click-liveness.html").read_text()
        )

    async def events(_: Request) -> Response:
        async def chunks() -> AsyncGenerator[bytes, None]:
            while not app.state.stopping:
                yield b"data: heartbeat\n\n"
                await asyncio.sleep(0.1)

        return StreamingResponse(chunks(), media_type="text/event-stream")

    async def poll(_: Request) -> Response:
        await asyncio.sleep(0.1)
        return Response("[]", media_type="application/json")

    app = Starlette(
        routes=[
            Route("/", page),
            Route("/click-events", events),
            Route("/click-poll", poll),
        ]
    )
    app.state.stopping = False
    return app


@pytest_asyncio.fixture
async def click_site(browser_context: Any) -> AsyncGenerator[str, None]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = click_site_app()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        # Close the live SSE before waiting for the server's graceful shutdown.
        await browser_context.navigate("about:blank")
        app.state.stopping = True
        server.should_exit = True
        await task


async def test_clicks_with_sse_and_polling(
    browser_context: Any, click_site: str
) -> None:
    await browser_context.navigate(click_site)
    await browser_context.wait(
        condition="js", value="window.streamMessages > 0 && window.pollCount > 0"
    )
    durations = []
    # Repeat across polling ticks, refreshing refs just as an observe-act loop does.
    for _ in range(4):
        for name in (
            "Page action",
            "Order row action",
            "Open order",
            "Modal action",
            "Close order",
        ):
            snapshot = await browser_context.snapshot(mode="accessible")
            index = next(
                i for i, ref in snapshot.selector_map.items() if ref.text == name
            )
            started = time.monotonic()
            result = await browser_context.action("click", str(index))
            durations.append(time.monotonic() - started)
            assert result["clicked"]
        await asyncio.sleep(2)
    assert await browser_context.evaluate("window.pageClicks") == 8
    assert await browser_context.evaluate("window.modalClicks") == 4
    assert await browser_context.evaluate("window.pollCount") >= 4
    assert any(
        "/click-events" in request["url"]
        for request in await browser_context.network(since=0, pending=True)
    )
    assert max(durations) < 3, durations
    print(f"clicks={len(durations)} max={max(durations):.3f}s durations={durations}")


@pytest.mark.parametrize("detached", [False, True], ids=["covered", "detached-marker"])
async def test_blocked_click_fails_promptly(
    browser_context: Any, click_site: str, detached: bool
) -> None:
    await browser_context.navigate(click_site)
    snapshot = await browser_context.snapshot(mode="accessible")
    name = "Replaceable action" if detached else "Page action"
    index = next(i for i, ref in snapshot.selector_map.items() if ref.text == name)
    await browser_context.evaluate(
        "document.querySelector('#backdrop').hidden=false;"
        f"window.replaceMarked={str(detached).lower()}"
    )
    started = time.monotonic()
    with pytest.raises(AgentBrowserError) as caught:
        await browser_context.action("click", str(index))
    elapsed = time.monotonic() - started
    print(f"detached={detached} elapsed={elapsed:.3f}s error={caught.value}")
    assert elapsed < 3
    assert caught.value.error == (
        "element_resolve_failed" if detached else "element_covered"
    )
    if not detached:
        assert "Close user menu" in caught.value.hint
        assert "z-panel" in caught.value.hint
        assert "Escape" in caught.value.action
        assert "Escape" in caught.value.hint
        assert "\n" not in caught.value.hint
        assert len(caught.value.hint) < 350
    assert await browser_context.evaluate("window.pageClicks") == 0


async def test_click_waits_for_short_overlay_animation(
    browser_context: Any, click_site: str
) -> None:
    await browser_context.navigate(click_site)
    await browser_context.evaluate(
        "document.querySelector('#backdrop').hidden=false;"
        "setTimeout(()=>document.querySelector('#backdrop').hidden=true,300)"
    )
    result = await browser_context.action("click", "", selector="#page-action")
    assert result["clicked"]
    assert await browser_context.evaluate("window.pageClicks") == 1


async def test_click_timeout_explains_readiness_wait(
    browser_context: Any, click_site: str, monkeypatch: Any
) -> None:
    await browser_context.navigate(click_site)
    await browser_context.evaluate(
        "document.querySelector('#page-action').style.visibility='hidden'"
    )
    monkeypatch.setattr(browser_context._browser_config, "action_timeout", 300)
    with pytest.raises(AgentBrowserError) as caught:
        await browser_context.action("click", "", selector="#page-action")
    assert caught.value.error == "action_timeout"
    assert "visible" in caught.value.hint
    assert "\n" not in caught.value.hint
    assert len(caught.value.hint) < 200


async def test_click_allows_button_enabled_after_three_seconds(
    browser_context: Any, click_site: str
) -> None:
    await browser_context.navigate(click_site)
    await browser_context.evaluate(
        "document.querySelector('#page-action').disabled=true;"
        "setTimeout(()=>document.querySelector('#page-action').disabled=false,3000)"
    )
    started = time.monotonic()
    result = await browser_context.action("click", "", selector="#page-action")
    assert result["clicked"]
    assert time.monotonic() - started >= 2.5
    assert await browser_context.evaluate("window.pageClicks") == 1


async def test_click_failure_through_http_and_mcp(
    private_api: Any, click_site: str
) -> None:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient
    from agentcloak.mcp.tools.interaction import register

    client, config = private_api
    # This fixture normally gives the request queue only 1.5s; leave room
    # for the backend's 2s diagnostic instead of testing queue cancellation.
    config.browser.action_timeout = 5000
    response = await client.post("/navigate", json={"url": click_site})
    assert response.is_success
    await client.post(
        "/evaluate", json={"js": "document.querySelector('#backdrop').hidden=false"}
    )
    response = await client.post(
        "/action", json={"kind": "click", "selector": "#page-action"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "element_covered"
    assert "Close user menu" in response.json()["hint"]
    daemon = DaemonClient(
        host="127.0.0.1",
        port=client.base_url.port,
        session_id="primary",
        auto_start=False,
    )
    await daemon.navigate(click_site)
    await daemon.evaluate("document.querySelector('#backdrop').hidden=false")
    mcp = FastMCP("click-error-probe")
    register(mcp, daemon)
    tool = mcp._tool_manager._tools["agentcloak_action"]
    result = json.loads(await tool.fn(kind="click", selector="#page-action"))
    assert result["error"] == "element_covered"
    assert "Close user menu" in result["hint"]
    assert "Escape" in result["action"]
