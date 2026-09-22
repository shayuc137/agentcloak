"""Public HTTP recovery and evidence contracts with real local browser targets."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
import sys
import time
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
import uvicorn
from PIL import Image
from websockets.asyncio.client import connect

from agentcloak.core.config import AgentcloakConfig
from agentcloak.daemon.app import create_app
from agentcloak.daemon.services.session_manager import SessionManager


@pytest_asyncio.fixture
async def private_api(browser_context):
    config = AgentcloakConfig()
    config.browser.action_timeout = 1500
    config.browser.humanize = False
    app = create_app()
    app.state.config = config
    app.state.active_tier = browser_context.stealth_tier
    app.state.local_profile = None
    app.state.browser_ctx = browser_context

    async def ensure_local():
        return browser_context

    async def close_remote_session(*args, **kwargs):
        return False

    app.state.context_manager = SimpleNamespace(
        ensure_local=ensure_local, close_remote_session=close_remote_session
    )
    manager = SessionManager(config, app_state=app.state)
    app.state.session_manager = manager
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
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            timeout=10,
            headers={"X-Agentcloak-Session": "primary"},
        ) as client:
            yield client, config
    finally:
        await manager.close_all()
        server.should_exit = True
        await task


async def navigate(client, local_server, page="input-actions.html", **kwargs):
    response = await client.post(
        "/navigate", json={"url": f"{local_server}/{page}", **kwargs}
    )
    assert response.is_success, response.text
    return response.json()["data"]


async def evaluate(client, js):
    response = await client.post("/evaluate", json={"js": js})
    assert response.is_success, response.text
    return response.json()["data"]


@pytest.mark.parametrize("source", ["file", "stdin"])
async def test_cdp_large_parameters_through_cli(
    private_api, local_server, tmp_path, source
):
    client, _ = private_api
    (tmp_path / "config.toml").write_text(
        f"[daemon]\nhost='127.0.0.1'\nport={client.base_url.port}\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTCLOAK_")}
    env.update(
        AGENTCLOAK_HOME=str(tmp_path),
        AGENTCLOAK_SESSION="primary",
        AGENTCLOAK_SKIP_FIRST_RUN_BANNER="1",
    )
    content = 'body::after { content: "你好\\world"; }\n' * 12000
    payload = json.dumps(
        {
            "expression": "window.largePayload="
            + json.dumps(content, ensure_ascii=False)
            + ";true",
            "returnByValue": True,
        },
        ensure_ascii=False,
    ).encode()
    assert len(payload) > 350_000
    path = tmp_path / "parameters.json"
    path.write_bytes(payload)

    async def cli(*args, stdin=None):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "agentcloak",
            *args,
            "--json",
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(stdin), 30)
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()
        assert process.returncode == 0, stderr.decode()
        response = json.loads(stdout)
        assert response["ok"] is True
        return response["data"]

    await cli("navigate", f"{local_server}/input-actions.html")
    await cli(
        "cdp",
        "send",
        "Runtime.evaluate",
        "--params-file",
        str(path) if source == "file" else "-",
        stdin=payload if source == "stdin" else None,
    )
    digest = await cli(
        "js",
        "evaluate",
        "crypto.subtle.digest('SHA-256',new TextEncoder().encode(window.largePayload))"
        ".then(b=>Array.from(new Uint8Array(b),"
        "v=>v.toString(16).padStart(2,'0')).join(''))",
    )
    assert digest["result"] == hashlib.sha256(content.encode()).hexdigest()


async def test_key_failure_does_not_pollute_click(private_api, local_server):
    client, _ = private_api
    await navigate(client, local_server)
    await evaluate(
        client,
        "window.keys=[]; "
        "document.addEventListener('keydown',e=>keys.push([e.key,e.ctrlKey]));"
        "document.addEventListener('click',e=>window.clickedCtrl=e.ctrlKey)",
    )
    valid = await client.post("/action", json={"kind": "press", "key": "ctrl+enter"})
    assert valid.is_success, valid.text
    before = (await evaluate(client, "keys.slice()"))["result"]
    for key in ("ctrl+notAKey", "ctrl+help", "ctrl+F13", "ctrl+é"):
        invalid = await client.post("/action", json={"kind": "press", "key": key})
        assert invalid.json()["error"] == "invalid_argument"
    assert (await evaluate(client, "keys"))["result"] == before
    snapshot = (
        await client.get("/snapshot", params={"include_selector_map": True})
    ).json()["data"]
    index = next(
        int(index)
        for index, ref in snapshot["selector_map"].items()
        if ref.get("text") == "Drag source"
    )
    clicked = await client.post("/action", json={"kind": "click", "index": index})
    assert clicked.is_success, clicked.text
    result = (await evaluate(client, "({keys,clickedCtrl})"))["result"]
    assert ["Enter", True] in result["keys"]
    assert result["clickedCtrl"] is False


async def test_evidence_and_target_identity(private_api, local_server):
    client, _ = private_api
    await navigate(client, local_server, expect_path="/input-actions.html")
    wrong = await client.post(
        "/navigate", json={"url": f"{local_server}/index.html", "expect_path": "/other"}
    )
    assert wrong.json()["error"] == "url_mismatch"
    await navigate(client, local_server)
    other = await client.post(
        "/navigate",
        headers={"X-Agentcloak-Session": "sibling"},
        json={"url": f"{local_server}/form.html"},
    )
    assert other.is_success
    endpoint = (await client.get("/cdp/endpoint", params={"page": "true"})).json()[
        "data"
    ]
    assert endpoint["target_id"] in endpoint["ws_endpoint"]
    async with connect(endpoint["ws_endpoint"]) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": "location.pathname",
                        "returnByValue": True,
                    },
                }
            )
        )
        response = json.loads(await ws.recv())
        assert response["result"]["result"]["value"] == "/input-actions.html"
    capture = await client.get(
        "/screenshot",
        params={
            "format": "png",
            "viewport": "700x500",
            "expect_url": "*/input-actions.html",
        },
    )
    assert capture.is_success, capture.text
    data = capture.json()["data"]
    assert data["url"] == f"{local_server}/input-actions.html"
    assert data["title"]
    assert data["viewport"] == {"width": 700, "height": 500}
    assert data["dpr"] == 1
    with Image.open(BytesIO(base64.b64decode(data["base64"]))) as image:
        assert image.size == (data["pixel_width"], data["pixel_height"]) == (700, 500)
    mismatch = await client.get("/screenshot", params={"expect_url": "*/login"})
    assert mismatch.json()["error"] == "url_mismatch"
    requests = (await client.get("/network", params={"since": "last_action"})).json()[
        "data"
    ]["requests"]
    assert any(request["url"].endswith("/input-actions.html") for request in requests)


async def test_busy_force_close_and_disconnect(private_api, local_server):
    client, config = private_api
    await navigate(client, local_server)
    await client.post(
        "/navigate",
        headers={"X-Agentcloak-Session": "sibling"},
        json={"url": f"{local_server}/form.html"},
    )
    config.browser.action_timeout = 250
    pending = asyncio.create_task(
        client.post(
            "/cdp/send",
            json={
                "method": "Runtime.evaluate",
                "params": {"expression": "new Promise(()=>{})", "awaitPromise": True},
                "timeout": 10000,
            },
        )
    )
    for _ in range(100):
        sessions = (await client.get("/session/list")).json()["data"]["sessions"]
        if any("/cdp/send" in item["active_actions"] for item in sessions):
            break
        await asyncio.sleep(0.01)
    assert (await client.get("/network", params={"pending": True})).is_success
    assert (await client.get("/record/status")).is_success
    busy = await client.get("/snapshot")
    assert busy.json()["error"] == "session_busy", busy.text
    assert "/cdp/send" in busy.json()["hint"]
    start = time.monotonic()
    closed = await client.post("/session/close", json={"force": True})
    assert closed.is_success, closed.text
    assert time.monotonic() - start < 1
    assert (await pending).json()["error"] == "session_cancelled"
    await navigate(client, local_server)
    sibling = await client.post(
        "/evaluate",
        headers={"X-Agentcloak-Session": "sibling"},
        json={"js": "location.pathname"},
    )
    assert sibling.json()["data"]["result"] == "/form.html"
    with pytest.raises(httpx.ReadTimeout):
        await client.post("/evaluate", json={"js": "new Promise(()=>{})"}, timeout=0.05)
    for _ in range(100):
        sessions = (await client.get("/session/list")).json()["data"]["sessions"]
        primary = next(item for item in sessions if item["session_id"] == "primary")
        if not primary["active_actions"]:
            break
        await asyncio.sleep(0.01)
    assert primary["active_actions"] == []
    assert primary["queued"] == 0
    await navigate(client, local_server)


async def test_frozen_renderer_timeout_and_recovery(private_api, local_server):
    client, config = private_api
    await navigate(client, local_server)
    endpoint = (await client.get("/cdp/endpoint", params={"page": True})).json()[
        "data"
    ]["ws_endpoint"]
    config.browser.action_timeout = 250
    async with connect(endpoint) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": "while(true){}"},
                }
            )
        )
        await asyncio.sleep(0.05)
        health = await client.get("/health", timeout=0.5)
        assert health.is_success
        start = time.monotonic()
        response = await client.get("/screenshot")
        assert response.json()["error"] == "action_timeout", response.text
        assert time.monotonic() - start < 1
        start = time.monotonic()
        closed = await client.post("/session/close", json={"force": True})
        assert closed.is_success, closed.text
        assert time.monotonic() - start < 1
    await navigate(client, local_server)


@pytest.mark.parametrize("observation", ["evaluate", "screenshot"])
async def test_popup_feedback_and_recreated_page(
    private_api, local_server, observation
):
    client, config = private_api
    config.browser.action_timeout = 3000
    await navigate(client, local_server)
    opened = []
    for _ in range(8):
        result = await evaluate(client, "window.open('/form.html'); true")
        opened.append(result["new_tab"]["tab_id"])
        assert opened[-1] > 0
        await navigate(client, local_server)
    assert result["warning"]
    close = await client.post("/tab/close", json={"others": True})
    assert close.is_success, close.text
    assert close.json()["data"] == {"closed": opened}
    again = await client.post("/tab/close", json={"others": True})
    assert again.json()["data"] == {"closed": []}
    tabs = (await client.get("/tabs")).json()["data"]["tabs"]
    assert len(tabs) == 1
    endpoint = (await client.get("/cdp/endpoint", params={"page": True})).json()["data"]
    await client.post(
        "/cdp/send",
        json={
            "method": "Target.closeTarget",
            "params": {"targetId": endpoint["target_id"]},
        },
    )
    lost = (
        await client.post("/evaluate", json={"js": "location.pathname"})
        if observation == "evaluate"
        else await client.get("/screenshot")
    )
    assert lost.json()["error"] in {"page_recreated", "page_lost"}, lost.text
    malformed = await client.post("/navigate", json={})
    assert malformed.status_code == 422
    blank = await client.get("/screenshot")
    assert blank.json()["error"] == "page_recreated", blank.text
    await navigate(client, local_server)
    assert (await evaluate(client, "location.pathname"))[
        "result"
    ] == "/input-actions.html"


async def test_force_close_releases_shared_origin_stream_connections(private_api):
    from starlette.applications import Starlette
    from starlette.responses import HTMLResponse, StreamingResponse
    from starlette.routing import Route

    client, config = private_api
    config.browser.action_timeout = 1000
    config.browser.navigation_timeout = 1
    active_streams = 0
    pool_full = asyncio.Event()

    async def page(request):
        return HTMLResponse("<title>Stream test</title><button>Ready</button>")

    async def events(request):
        async def chunks():
            nonlocal active_streams
            active_streams += 1
            if active_streams == 6:
                pool_full.set()
            try:
                while True:
                    yield b": heartbeat\n\n"
                    await asyncio.sleep(0.1)
            finally:
                active_streams -= 1

        return StreamingResponse(chunks(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/", page), Route("/events", events)])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, http="h11", log_level="error")
    )
    task = asyncio.create_task(server.serve())
    url = f"http://127.0.0.1:{port}/"
    sibling = {"X-Agentcloak-Session": "stream-sibling"}
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        assert (await client.post("/navigate", json={"url": url})).is_success
        await evaluate(
            client,
            "window.streams = Array.from({length: 6}, "
            "() => new EventSource('/events'));"
            " true",
        )
        await asyncio.wait_for(pool_full.wait(), 5)
        pending = await client.get(
            "/network", params={"pending": True, "filter": "*/events"}
        )
        streams = pending.json()["data"]["requests"]
        assert len(streams) == 6
        assert all(
            entry["pending"] and entry["resource_type"] == "eventsource"
            for entry in streams
        )
        assert (
            await client.get(
                "/network",
                params={"pending": True, "filter": "*/events"},
                headers=sibling,
            )
        ).json()["data"]["count"] == 0
        response = await client.post("/navigate", json={"url": url}, headers=sibling)
        assert response.json()["error"] == "action_timeout", response.text
        assert (await client.get("/health", timeout=0.5)).is_success
        start = time.monotonic()
        closed = await client.post("/session/close", json={"force": True})
        assert closed.is_success, closed.text
        assert time.monotonic() - start < 1
        async with asyncio.timeout(3):
            while active_streams:
                await asyncio.sleep(0.01)
        restored = await client.post("/navigate", json={"url": url}, headers=sibling)
        assert restored.is_success, restored.text
        assert restored.json()["data"]["title"] == "Stream test"
    finally:
        await client.post("/session/close", json={"force": True})
        await client.post("/session/close", json={"force": True}, headers=sibling)
        server.should_exit = True
        await task
