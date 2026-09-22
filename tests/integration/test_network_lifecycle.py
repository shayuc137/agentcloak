"""Document ownership and bounded settling with real HTTP streams."""

import asyncio
import socket
import time

import pytest
import pytest_asyncio
import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, Response, StreamingResponse
from starlette.routing import Route

from .test_recovery_evidence import evaluate
from .test_recovery_evidence import private_api as private_api


@pytest_asyncio.fixture
async def streaming_site():
    async def page(request):
        return HTMLResponse(
            '<button id="noop">Noop</button>'
            '<button id="load" onclick="'
            "fetch('/slow').then(()=>this.textContent='Ready')\">Load</button>"
            '<script>window.source=new EventSource("/events");</script>'
        )

    async def events(request):
        async def chunks():
            while True:
                yield b": heartbeat\n\n"
                await asyncio.sleep(0.05)

        return StreamingResponse(chunks(), media_type="text/event-stream")

    async def slow(request):
        await asyncio.sleep(0.6)
        return Response("done")

    app = Starlette(
        routes=[Route("/", page), Route("/events", events), Route("/slow", slow)]
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        server.should_exit = True
        await task


async def pending(client):
    response = await client.get("/network", params={"pending": True})
    assert response.is_success, response.text
    return response.json()["data"]["requests"]


async def wait_streams(client, count):
    async with asyncio.timeout(5):
        while True:
            entries = await pending(client)
            streams = [
                item for item in entries if item["resource_type"] == "eventsource"
            ]
            if len(streams) == count and all(item["status"] == 200 for item in streams):
                return streams
            await asyncio.sleep(0.02)


async def test_document_requests_retire_without_hiding_live_streams(
    private_api, streaming_site
):
    client, _ = private_api
    try:
        for _ in range(5):
            response = await client.post("/navigate", json={"url": streaming_site})
            assert response.is_success, response.text
            streams = await wait_streams(client, 1)
            # Unfinished fetches at unload must disappear with the old document.
            await evaluate(client, "void fetch('/slow')")
        await asyncio.sleep(0.7)
        assert len(await pending(client)) == 1
        old_seq = streams[0]["seq"]
        await evaluate(
            client, "history.pushState({},'', '?view=2'); location.hash='section'"
        )
        assert (await wait_streams(client, 1))[0]["seq"] == old_seq
        # Aborted navigations do not retire the current document's stream.
        rule = await client.post(
            "/route/add",
            json={"pattern": "**/blocked", "action": "fulfill", "status": 204},
        )
        assert rule.is_success, rule.text
        failed = await client.post(
            "/navigate", json={"url": streaming_site + "blocked"}
        )
        assert failed.is_error
        assert (await wait_streams(client, 1))[0]["seq"] == old_seq
        assert (await client.post("/navigate", json={"url": streaming_site})).is_success
        await evaluate(
            client,
            "window.child=document.createElement('iframe');child.src='/';document.body.append(child);true",
        )
        await wait_streams(client, 2)
        removed = await client.post(
            "/evaluate",
            json={
                "js": "document.querySelector('iframe').remove()",
                "world": "isolated",
            },
        )
        assert removed.is_success, removed.text
        await wait_streams(client, 1)
        tab = (await client.post("/tab/new", json={"url": streaming_site})).json()[
            "data"
        ]
        await wait_streams(client, 2)
        await client.post("/navigate", json={"url": streaming_site})
        await wait_streams(client, 2)
        await client.post("/tab/close", json={"tab_id": tab["tab_id"]})
        await wait_streams(client, 1)
    finally:
        await client.post("/session/close", json={"force": True})


@pytest.mark.parametrize("batch_mode", ["plain", "refs", "secure"])
async def test_batch_settles_fetch_but_not_eventsource(
    private_api, streaming_site, batch_mode
):
    client, config = private_api
    config.browser.action_timeout = 5000
    if batch_mode == "secure":
        config.security.content_scan = True
        config.security.content_scan_patterns = ["UnsafeMarker"]
    try:
        await client.post("/navigate", json={"url": streaming_site})
        await wait_streams(client, 1)
        for selector in ("#noop", "#load"):
            start = time.monotonic()
            response = await client.post(
                "/action/batch",
                json={
                    "actions": [
                        {"kind": "click", "selector": selector},
                        {"kind": "wait", "condition": "ms", "value": "0"},
                        {
                            "kind": "snapshot",
                            **(
                                {"selector": "$0.selector"}
                                if batch_mode == "refs"
                                else {}
                            ),
                        },
                    ],
                    "settle_timeout": 2500,
                },
            )
            assert response.is_success, response.text
            elapsed = time.monotonic() - start
            data = response.json()["data"]
            assert elapsed < 2, (elapsed, data)
            assert data["results"][0]["pending_requests"] >= 1
            if selector == "#load":
                assert elapsed >= 0.5
                assert "Ready" in str(data["results"][-1]), data
            assert len(await wait_streams(client, 1)) == 1
    finally:
        await client.post("/session/close", json={"force": True})
