"""Pixel, media and session ownership contracts through a real HTTP daemon."""

import asyncio
import base64
import os
from io import BytesIO

import pytest
from PIL import Image

from .test_recovery_evidence import evaluate, navigate
from .test_recovery_evidence import private_api as private_api

ENVIRONMENT = """({
    dark:matchMedia('(prefers-color-scheme: dark)').matches,
    reduce:matchMedia('(prefers-reduced-motion: reduce)').matches,
    coarse:matchMedia('(pointer: coarse)').matches,
    fine:matchMedia('(pointer: fine)').matches,
    touch:navigator.maxTouchPoints
})"""
HEADED = os.environ.get("AGENTCLOAK_TEST_HEADED", "").lower() in ("1", "true")
METRICS = "[innerWidth,innerHeight,devicePixelRatio]"


async def test_density_and_temporary_capture(private_api, local_server):
    client, _ = private_api
    await navigate(client, local_server)
    response = await client.post(
        "/viewport", json={"width": 640, "height": 480, "dpr": 2}
    )
    assert response.is_success, response.text
    assert response.json()["data"]["dpr"] == 2
    await client.post("/viewport", json={"width": 600, "height": 400})
    assert (await evaluate(client, METRICS))["result"] == [600, 400, 2]
    for params, size in (
        ({}, (1200, 800)),
        ({"viewport": "300x200"}, (600, 400)),
        ({"dpr": 1.5}, (900, 600)),
        ({"viewport": "300x200", "dpr": 3}, (900, 600)),
    ):
        capture = await client.get("/screenshot", params={"format": "png", **params})
        assert capture.is_success, capture.text
        data = capture.json()["data"]
        with Image.open(BytesIO(base64.b64decode(data["base64"]))) as image:
            assert image.size == (data["pixel_width"], data["pixel_height"]) == size
        assert data["dpr"] == params.get("dpr", 2)
        assert (await evaluate(client, METRICS))["result"] == [600, 400, 2]
    failed = await client.get(
        "/screenshot",
        params={"viewport": "300x200", "dpr": 3, "expect_url": "*/missing"},
    )
    assert failed.json()["error"] == "url_mismatch"
    assert (await evaluate(client, METRICS))["result"] == [600, 400, 2]
    await evaluate(
        client, "document.body.style.height='1200px';document.body.style.margin='0'"
    )
    capture = await client.get(
        "/screenshot", params={"format": "png", "full_page": True}
    )
    assert capture.is_success, capture.text
    data = capture.json()["data"]
    assert data["pixel_height"] >= 2400
    assert data["pixel_width"] == 1200
    assert (await evaluate(client, METRICS))["result"] == [600, 400, 2]
    await client.post(
        "/cdp/send",
        json={
            "method": "Emulation.setDeviceMetricsOverride",
            "params": {
                "width": 500,
                "height": 320,
                "deviceScaleFactor": 1.5,
                "mobile": False,
            },
        },
    )
    capture = await client.get("/screenshot", params={"viewport": "300x200", "dpr": 2})
    assert capture.is_success, capture.text
    assert (await evaluate(client, METRICS))["result"] == [500, 320, 1.5]


async def test_session_environment_inheritance_and_reset(private_api, local_server):
    client, _ = private_api
    await navigate(client, local_server)
    original = (await evaluate(client, ENVIRONMENT))["result"]
    sibling_headers = {"X-Agentcloak-Session": "sibling"}
    await client.post(
        "/navigate", headers=sibling_headers, json={"url": f"{local_server}/form.html"}
    )
    await client.post("/tab/new", json={"url": f"{local_server}/input-actions.html"})
    result = await client.post(
        "/emulation",
        json={
            "color_scheme": "dark",
            "reduced_motion": True,
            **({"pointer": "coarse"} if HEADED else {}),
        },
    )
    assert result.is_success, result.text
    overridden = {
        **original,
        "dark": True,
        "reduce": True,
        **({"coarse": True, "fine": False, "touch": 1} if HEADED else {}),
    }
    assert (await evaluate(client, ENVIRONMENT))["result"] == overridden
    sibling = await client.post(
        "/evaluate", headers=sibling_headers, json={"js": ENVIRONMENT}
    )
    assert sibling.json()["data"]["result"] == original
    # Both already-owned and newly created tabs inherit the session environment.
    await client.post("/tab/switch", json={"tab_id": 0})
    assert (await evaluate(client, ENVIRONMENT))["result"] == overridden
    await client.post("/tab/new", json={"url": f"{local_server}/input-actions.html"})
    assert (await evaluate(client, ENVIRONMENT))["result"] == overridden
    await client.post("/tab/new", json={"url": f"{local_server}/emulation.html"})
    assert (await evaluate(client, "initialEnvironment"))["result"] == {
        "dark": True,
        "reduce": True,
    }
    capture = await client.get("/screenshot", params={"format": "png"})
    with Image.open(
        BytesIO(base64.b64decode(capture.json()["data"]["base64"]))
    ) as image:
        assert image.convert("RGB").getpixel((0, 0)) == (16, 16, 16)
    await evaluate(client, f"window.open('{local_server}/form.html'); 'opened'")
    tabs = (await client.get("/tabs")).json()["data"]["tabs"]
    popup = max(tab["tab_id"] for tab in tabs)
    await client.post("/tab/switch", json={"tab_id": popup})
    assert (await evaluate(client, ENVIRONMENT))["result"] == overridden
    changed = await client.post(
        "/emulation",
        json={"reduced_motion": False, **({"pointer": "fine"} if HEADED else {})},
    )
    assert changed.json()["data"]["color_scheme"] == "dark"
    actual = (await evaluate(client, ENVIRONMENT))["result"]
    assert actual["dark"] and not actual["reduce"] and actual["fine"]
    await client.post("/viewport", json={"width": 600, "height": 400, "dpr": 2})
    reset = await client.post("/emulation", json={"reset": True})
    assert reset.json()["data"] == {
        "color_scheme": None,
        "reduced_motion": None,
        "pointer": None,
    }
    assert (await evaluate(client, ENVIRONMENT))["result"] == original
    assert (await evaluate(client, METRICS))["result"] == [600, 400, 2]
    await client.post("/tab/switch", json={"tab_id": 0})
    assert (await evaluate(client, ENVIRONMENT))["result"] == original


async def test_invalid_environment_and_density_leave_page_unchanged(
    private_api, local_server
):
    client, _ = private_api
    await navigate(client, local_server)
    before = (await evaluate(client, f"[{METRICS}, {ENVIRONMENT}]"))["result"]
    for body in (
        {"color_scheme": "blue"},
        {"pointer": "huge"},
        {"reset": True, "reduced_motion": False},
    ):
        response = await client.post("/emulation", json=body)
        assert response.status_code == 422
    for dpr in ("nan", "inf", "-1", "0"):
        response = await client.post(
            "/viewport", json={"width": 300, "height": 200, "dpr": dpr}
        )
        assert response.status_code == 422
        response = await client.get(
            "/screenshot", params={"dpr": dpr, "viewport": "300x200"}
        )
        assert response.status_code == 422
    assert (await evaluate(client, f"[{METRICS}, {ENVIRONMENT}]"))["result"] == before


async def test_cancelled_capture_restores_density(
    browser_context, local_server, monkeypatch
):
    ctx = await browser_context.fork_session()
    entered = asyncio.Event()

    async def blocked_capture(**kwargs):
        entered.set()
        await asyncio.Future()

    try:
        await ctx.navigate(f"{local_server}/input-actions.html")
        await ctx.set_viewport(600, 400, dpr=2)
        monkeypatch.setattr(ctx, "_screenshot_impl", blocked_capture)
        task = asyncio.create_task(ctx.screenshot(viewport="300x200", dpr=3))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = await ctx.raw_cdp(
            "Runtime.evaluate", {"expression": METRICS, "returnByValue": True}
        )
        assert result["result"]["value"] == [600, 400, 2]
    finally:
        await ctx.close()


@pytest.mark.skipif(HEADED, reason="headless rejection contract")
async def test_headless_pointer_rejected_before_changes(private_api, local_server):
    client, _ = private_api
    await navigate(client, local_server)
    before = (await evaluate(client, ENVIRONMENT))["result"]
    for pointer in ("fine", "coarse"):
        response = await client.post(
            "/emulation", json={"pointer": pointer, "color_scheme": "dark"}
        )
        assert response.json()["error"] == "unsupported_operation"
        assert (await evaluate(client, ENVIRONMENT))["result"] == before
    state = await client.post("/emulation", json={})
    assert state.json()["data"] == {
        "color_scheme": None,
        "reduced_motion": None,
        "pointer": None,
    }
