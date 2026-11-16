"""DOS #4: native CSS element bounds, DPR-aware crop and surface parity."""

from __future__ import annotations

import base64
from io import BytesIO
from typing import Any

import pytest
from PIL import Image

from agentcloak.core.errors import AgentBrowserError

from .test_recovery_evidence import private_api as private_api


@pytest.mark.parametrize("dpr", [1, 1.5, 2])
@pytest.mark.parametrize("padding", [0, 8])
async def test_native_element_crop(
    browser_context: Any, local_server: str, dpr: float, padding: int
) -> None:
    ctx = browser_context
    await ctx.navigate(f"{local_server}/visual-evidence.html")
    old_viewport = await ctx._get_viewport_impl()
    old_dpr = await ctx._get_dpr_impl()
    raw = await ctx.screenshot(
        format="png", selector="#control", padding=padding, viewport="800x600", dpr=dpr
    )
    with Image.open(BytesIO(raw)) as image:
        assert image.size == ((140 + 2 * padding) * dpr, (40 + 2 * padding) * dpr)
    assert ctx.screenshot_metadata["clip"] == {
        "x": 100 - padding,
        "y": 80 - padding,
        "width": 140 + 2 * padding,
        "height": 40 + 2 * padding,
    }
    assert await ctx._get_viewport_impl() == old_viewport
    assert await ctx._get_dpr_impl() == old_dpr


async def test_offscreen_crop_scrolls_and_annotation_reuses_refs(
    browser_context: Any, local_server: str
) -> None:
    ctx = browser_context
    await ctx.navigate(f"{local_server}/visual-evidence.html")
    raw = await ctx.screenshot(format="png", selector="#lower", annotate=True)
    with Image.open(BytesIO(raw)) as image:
        assert image.size == (140, 40)
        # The label must be drawn inside the crop, not clipped away above it.
        region = image.convert("RGB").crop((4, 4, 25, 15))
        red = sum(
            r > 180 and g < 80 and b < 80
            for r, g, b in (
                region.getpixel((x, y))
                for x in range(region.width)
                for y in range(region.height)
            )
        )
        assert red > 100
    assert await ctx.evaluate("scrollY") > 0
    assert any(
        a["name"] == "Lower control" for a in ctx.screenshot_metadata["annotations"]
    )
    assert all(
        a["name"] != "Save example" for a in ctx.screenshot_metadata["annotations"]
    )


@pytest.mark.parametrize(
    ("selector", "extra", "expected"),
    [
        ("#missing", {}, "snapshot_selector_not_found"),
        ("button", {}, "selector_ambiguous"),
        ("[", {}, "snapshot_selector_invalid"),
        ("#control", {"full_page": True}, "invalid_argument"),
        ("#control", {"padding": -1}, "invalid_argument"),
        ("", {"padding": 1}, "invalid_argument"),
        ("#control", {"padding": float("nan")}, "invalid_argument"),
    ],
)
async def test_invalid_element_capture(
    browser_context: Any, local_server: str, selector: str, extra: dict, expected: str
) -> None:
    await browser_context.navigate(f"{local_server}/visual-evidence.html")
    with pytest.raises(AgentBrowserError) as caught:
        await browser_context.screenshot(selector=selector, **extra)
    assert caught.value.error == expected


async def test_zero_box_and_padding_clamp(
    browser_context: Any, local_server: str
) -> None:
    ctx = browser_context
    await ctx.navigate(f"{local_server}/visual-evidence.html")
    await ctx.evaluate("document.querySelector('#control').style.display='none'")
    with pytest.raises(AgentBrowserError) as caught:
        await ctx.screenshot(selector="#control")
    assert caught.value.error == "screenshot_empty_element"
    await ctx.evaluate(
        "document.body.insertAdjacentHTML('beforeend',"
        "'<div id=zero style=width:0;height:0></div>')"
    )
    with pytest.raises(AgentBrowserError) as caught:
        await ctx.screenshot(selector="#zero")
    assert caught.value.error == "screenshot_empty_element"
    await ctx.evaluate("document.querySelector('#control').style.display='block'")
    raw = await ctx.screenshot(format="png", selector="#control", padding=10000)
    with Image.open(BytesIO(raw)) as image:
        assert image.size == (1280, 720)


async def test_unreachable_fixed_element_is_not_a_padding_capture(
    browser_context: Any, local_server: str
) -> None:
    await browser_context.navigate(f"{local_server}/visual-evidence.html")
    await browser_context.evaluate(
        "Object.assign(document.querySelector('#control').style,"
        "{position:'fixed',left:'-1000px',top:'0px'})"
    )
    with pytest.raises(AgentBrowserError) as caught:
        await browser_context.screenshot(selector="#control", padding=2000)
    assert caught.value.error == "screenshot_outside_viewport"


async def test_crop_through_daemon(private_api: Any, local_server: str) -> None:
    client, config = private_api
    config.browser.action_timeout = 5000
    response = await client.post(
        "/navigate", json={"url": f"{local_server}/visual-evidence.html"}
    )
    assert response.is_success
    response = await client.get(
        "/screenshot",
        params={"selector": "#control", "padding": 8, "format": "png", "dpr": 2},
    )
    assert response.is_success, response.text
    data = response.json()["data"]
    assert data["clip"] == {"x": 92, "y": 72, "width": 156, "height": 56}
    with Image.open(BytesIO(base64.b64decode(data["base64"]))) as image:
        assert image.size == (312, 112)
