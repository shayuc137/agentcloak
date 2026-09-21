"""Real-browser input and viewport acceptance fixture."""

from __future__ import annotations

from io import BytesIO
from typing import Any

import pytest
from PIL import Image

from agentcloak.core.errors import AgentBrowserError


async def test_viewport_pointer_keyboard_cdp(
    browser_context: Any, local_server: str
) -> None:
    ctx = browser_context
    await ctx.navigate(f"{local_server}/input-actions.html")
    await ctx.set_viewport(1280, 720)
    snapshot = await ctx.snapshot(mode="accessible")
    refs = {ref.text: index for index, ref in snapshot.selector_map.items()}
    editor, source, destination = (
        refs[name] for name in ("Editor", "Drag source", "Drop destination")
    )
    await ctx.action("fill", f"[{editor}]", text="preserved state")
    for width in (2560, 1024):
        shot = await ctx.screenshot(format="png", viewport=f"{width}x800")
        assert Image.open(BytesIO(shot)).size == (width, 800)
        dimensions = await ctx.raw_cdp(
            "Runtime.evaluate",
            {
                "expression": "[innerWidth, innerHeight, editor.value]",
                "returnByValue": True,
            },
        )
        assert dimensions["result"]["value"] == [1280, 720, "preserved state"]
    await ctx.action("press", f"[{editor}]", key="Ctrl+Enter")
    await ctx.action("hover", "", at="40,200")
    pointer = await ctx.raw_cdp(
        "Runtime.evaluate", {"expression": "pointer", "returnByValue": True}
    )
    assert pointer["result"]["value"] == [40, 200]
    await ctx.action("hover", f"[{source}]", offset="5,3")
    await ctx.action("drag", f"[{source}]", destination=f"[{destination}]", steps=20)
    sx, sy = await ctx._element_center_impl(source)
    tx, ty = await ctx._element_center_impl(destination)
    await ctx.action(
        "drag", "", from_point=f"{sx},{sy}", to_point=f"{tx},{ty}", steps=20
    )
    result = await ctx.raw_cdp(
        "Runtime.evaluate", {"expression": "events", "returnByValue": True}
    )
    events = result["result"]["value"]
    assert events["ctrlEnter"] == 1
    assert events["dragend"] == 2
    assert events["drop"] == 2
    assert events["trusted"] is True
    with pytest.raises(AgentBrowserError):
        await ctx.raw_cdp("Missing.command", timeout=1000)
    with pytest.raises(AgentBrowserError) as caught:
        await ctx.raw_cdp(
            "Runtime.evaluate",
            {"expression": "new Promise(() => {})", "awaitPromise": True},
            timeout=50,
        )
    assert caught.value.error == "cdp_timeout"
    assert (await ctx.raw_cdp("Runtime.evaluate", {"expression": "1+1"}))["result"][
        "value"
    ] == 2


@pytest.mark.parametrize("bridge_transport", [False, True])
async def test_drag_offscreen_reference(
    browser_context: Any, local_server: str, bridge_transport: bool
) -> None:
    from unittest.mock import MagicMock

    from agentcloak.browser.remote_ctx import RemoteBridgeContext

    ctx = browser_context
    await ctx.navigate(f"{local_server}/drag-offscreen.html")
    await ctx.set_viewport(1024, 720)
    snapshot = await ctx.snapshot(mode="accessible")
    refs = {ref.text: index for index, ref in snapshot.selector_map.items()}
    if bridge_transport:
        remote = RemoteBridgeContext(bridge_ws=MagicMock(closed=False))
        remote._selector_map = ctx._selector_map.copy()
        remote._backend_node_map = ctx._backend_node_map.copy()
        cdp = await ctx._page.context.new_cdp_session(ctx._page)

        async def send(command: str, params: dict[str, Any], **kw: Any) -> Any:
            assert command == "cdp"
            return await cdp.send(params["method"], params.get("params", {}))

        remote._send = send
        try:
            await remote._run_action(
                "drag", str(refs["Source"]), destination=str(refs["Destination"])
            )
        finally:
            await cdp.detach()
    else:
        await ctx.action(
            "drag", str(refs["Source"]), destination=str(refs["Destination"])
        )
    result = await ctx.raw_cdp(
        "Runtime.evaluate", {"expression": "dragLog", "returnByValue": True}
    )
    assert result["result"]["value"] == [
        ["down", "source", True],
        ["dragstart", True],
        ["drop", True],
    ]


async def test_screenshot_restores_actual_cdp_viewport(
    browser_context: Any, local_server: str
) -> None:
    ctx = browser_context
    await ctx.navigate(f"{local_server}/input-actions.html")
    await ctx.set_viewport(1280, 720)
    await ctx.raw_cdp(
        "Emulation.setDeviceMetricsOverride",
        {"width": 800, "height": 600, "deviceScaleFactor": 1, "mobile": False},
    )
    shot = await ctx.screenshot(format="png", viewport="1024x768")
    assert Image.open(BytesIO(shot)).size == (1024, 768)
    actual = await ctx.raw_cdp(
        "Runtime.evaluate",
        {"expression": "[innerWidth,innerHeight]", "returnByValue": True},
    )
    assert actual["result"]["value"] == [800, 600]
