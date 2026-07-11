"""RemoteBridgeContext CDP action path tests.

These are the high-risk paths from the architecture audit (B2): click /
fill / scroll / hover / element-resolution. ``test_bridge.py`` only covers
message-feed and dialog-event paths; the actual CDP command sequences each
``_xxx_impl`` builds had no regression test before this file.

Mock strategy
-------------
We mock the WebSocket transport at the ``_send`` level — the lower the
mock layer the more brittle the test against unrelated refactors. Each
test sets up a ``_send_responses`` queue (or callable mapping) that
mimics what the bridge would return for each CDP command, then drives
the public ``_xxx_impl`` method and asserts on the ``_send`` call list.

We avoid using the public ``action()`` method here because it pulls in
the entire base-class orchestration (snapshot freshness check,
dialog gates, post-action settle). Those paths are covered by
``test_routes.py`` / the integration suite. The blind spot the PRD
identifies is the *CDP command sequence*, so the impl-level tests
isolate that.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcloak.browser.remote_ctx import RemoteBridgeContext
from agentcloak.browser.state import ElementRef


def _make_ctx() -> RemoteBridgeContext:
    """Build a context with a non-closed mock WebSocket.

    ``_ws.closed`` must be False so :py:meth:`_send` does not short-circuit
    with ``bridge_disconnected``. We never actually send anything because
    ``_send`` itself is mocked per-test.
    """
    ws = MagicMock()
    ws.closed = False
    return RemoteBridgeContext(bridge_ws=ws)


def _seed_selector_map(ctx: RemoteBridgeContext, *, ref: int = 1) -> None:
    """Populate the selector_map + backend_node_map so element resolution works.

    The base class's ``_require_snapshot`` checks both maps; tests that
    target element refs need them seeded. The values are arbitrary as long
    as they're consistent within a single test.
    """
    elem = ElementRef(
        index=ref,
        tag="button",
        role="button",
        text="OK",
    )
    ctx._selector_map = {ref: elem}
    ctx._backend_node_map = {ref: 42}


# ---------------------------------------------------------------------------
# B2.1: _resolve_element_center
# ---------------------------------------------------------------------------


class TestResolveElementCenter:
    """Element resolution: backendNodeId → describeNode → boxModel coordinates."""

    @pytest.mark.asyncio
    async def test_describe_node_returns_node_id_path(self) -> None:
        """Happy path: DOM.describeNode returns nodeId → DOM.getBoxModel."""
        ctx = _make_ctx()
        _seed_selector_map(ctx, ref=3)

        # CDP returns ``content`` as an 8-tuple [x1,y1, x2,y1, x2,y2, x1,y2]
        # for a 100x40 box at (10, 20).
        describe_resp = {"node": {"nodeId": 99}}
        boxmodel_resp = {"model": {"content": [10, 20, 110, 20, 110, 60, 10, 60]}}

        ctx._send = AsyncMock(side_effect=[describe_resp, boxmodel_resp])  # type: ignore[method-assign]

        cx, cy = await ctx._resolve_element_center(3)

        # Center is (60, 40) — (10+110)/2, (20+60)/2.
        assert cx == 60.0
        assert cy == 40.0

        # Verify the CDP sequence.
        calls = ctx._send.call_args_list
        assert calls[0].args[0] == "cdp"
        assert calls[0].args[1]["method"] == "DOM.describeNode"
        assert calls[0].args[1]["params"]["backendNodeId"] == 42
        assert calls[1].args[1]["method"] == "DOM.getBoxModel"
        assert calls[1].args[1]["params"]["nodeId"] == 99

    @pytest.mark.asyncio
    async def test_no_node_id_falls_back_to_resolve_node(self) -> None:
        """describeNode returns nodeId=0 → resolveNode + Runtime.callFunctionOn."""
        ctx = _make_ctx()
        _seed_selector_map(ctx, ref=5)

        # describeNode returns no nodeId → fallback path triggers.
        describe_resp = {"node": {"nodeId": 0}}
        resolve_resp = {"object": {"objectId": "obj-7"}}
        bbox_resp = {
            "result": {"value": json.dumps({"x": 100, "y": 50, "w": 40, "h": 20})}
        }
        ctx._send = AsyncMock(side_effect=[describe_resp, resolve_resp, bbox_resp])  # type: ignore[method-assign]

        cx, cy = await ctx._resolve_element_center(5)

        # x + w/2 = 100 + 20 = 120; y + h/2 = 50 + 10 = 60.
        assert cx == 120.0
        assert cy == 60.0

        calls = ctx._send.call_args_list
        assert calls[1].args[1]["method"] == "DOM.resolveNode"
        assert calls[2].args[1]["method"] == "Runtime.callFunctionOn"
        # The function should call getBoundingClientRect.
        fn_decl = calls[2].args[1]["params"]["functionDeclaration"]
        assert "getBoundingClientRect" in fn_decl


class TestScopedSnapshot:
    @pytest.mark.asyncio
    async def test_scoped_refs_keep_remote_backend_node_mapping(self) -> None:
        ctx = _make_ctx()
        ctx._get_ax_tree = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                {
                    "nodeId": "root",
                    "role": {"value": "RootWebArea"},
                    "name": {"value": "Page"},
                    "childIds": ["main", "toolbar"],
                },
                {
                    "nodeId": "main",
                    "role": {"value": "main"},
                    "name": {"value": "App"},
                    "childIds": ["save"],
                    "backendDOMNodeId": 42,
                },
                {
                    "nodeId": "save",
                    "role": {"value": "button"},
                    "name": {"value": "Save"},
                    "backendDOMNodeId": 43,
                },
                {
                    "nodeId": "toolbar",
                    "role": {"value": "toolbar"},
                    "name": {"value": "Agentation"},
                    "backendDOMNodeId": 99,
                },
            ]
        )
        ctx._resolve_snapshot_selector = AsyncMock(  # type: ignore[method-assign]
            return_value=42
        )
        ctx._get_page_info = AsyncMock(  # type: ignore[method-assign]
            return_value=("https://example.test", "Example")
        )

        snap = await ctx.snapshot(mode="accessible", selector="main")

        assert "Save" in snap.tree_text
        assert "Agentation" not in snap.tree_text
        assert ctx._backend_node_map == {1: 43}


# ---------------------------------------------------------------------------
# B2.2: _click_impl
# ---------------------------------------------------------------------------


class TestClickImpl:
    """``_click_impl`` — resolve element, dispatch press + release."""

    @pytest.mark.asyncio
    async def test_click_dispatches_mouse_press_and_release(self) -> None:
        """Click resolves the element then issues two Input.dispatchMouseEvent calls."""
        ctx = _make_ctx()
        _seed_selector_map(ctx, ref=1)

        # _resolve_element_center → describeNode + getBoxModel, then two
        # dispatchMouseEvent calls. Use a queue of responses.
        responses: list[dict[str, Any]] = [
            # describeNode
            {"node": {"nodeId": 11}},
            # getBoxModel — center will be (50, 30)
            {"model": {"content": [40, 20, 60, 20, 60, 40, 40, 40]}},
            # mousePressed + mouseReleased return empty dicts
            {},
            {},
        ]
        ctx._send = AsyncMock(side_effect=responses)  # type: ignore[method-assign]

        result = await ctx._click_impl(
            target="1", x=None, y=None, button="left", click_count=1
        )

        assert result == {"clicked": True}
        calls = ctx._send.call_args_list
        # 4 calls: describeNode, getBoxModel, mousePressed, mouseReleased.
        assert len(calls) == 4
        # Both mouse events should target the same coords (50, 30).
        press_params = calls[2].args[1]["params"]
        release_params = calls[3].args[1]["params"]
        assert press_params["type"] == "mousePressed"
        assert release_params["type"] == "mouseReleased"
        assert press_params["x"] == release_params["x"] == 50.0
        assert press_params["y"] == release_params["y"] == 30.0
        assert press_params["button"] == "left"

    @pytest.mark.asyncio
    async def test_click_with_explicit_coordinates_skips_resolution(self) -> None:
        """When x/y are supplied, no DOM lookup is performed."""
        ctx = _make_ctx()
        # Note: no selector_map seeded — coordinate path must not need it.
        ctx._send = AsyncMock(return_value={})  # type: ignore[method-assign]

        result = await ctx._click_impl(
            target="", x=123.0, y=456.0, button="left", click_count=1
        )

        assert result == {"clicked": True}
        calls = ctx._send.call_args_list
        # Only 2 calls (no element resolution).
        assert len(calls) == 2
        assert all(c.args[1]["method"] == "Input.dispatchMouseEvent" for c in calls)
        assert calls[0].args[1]["params"]["x"] == 123.0
        assert calls[0].args[1]["params"]["y"] == 456.0

    @pytest.mark.asyncio
    async def test_force_click_invokes_dom_click_without_mouse_events(self) -> None:
        ctx = _make_ctx()
        _seed_selector_map(ctx, ref=7)
        ctx._send = AsyncMock(  # type: ignore[method-assign]
            side_effect=[{"object": {"objectId": "obj-7"}}, {}]
        )

        result = await ctx._click_impl(
            target="7", x=None, y=None, button="left", click_count=1, force=True
        )

        assert result == {"clicked": True}
        calls = ctx._send.call_args_list
        assert [call.args[1]["method"] for call in calls] == [
            "DOM.resolveNode",
            "Runtime.callFunctionOn",
        ]
        params = calls[1].args[1]["params"]
        assert params["objectId"] == "obj-7"
        assert "this.click()" in params["functionDeclaration"]
        assert params["userGesture"] is True

    @pytest.mark.asyncio
    async def test_force_click_rejects_stale_ref_before_cdp(self) -> None:
        from agentcloak.core.errors import ElementNotFoundError

        ctx = _make_ctx()
        _seed_selector_map(ctx, ref=1)
        ctx._send = AsyncMock(return_value={})  # type: ignore[method-assign]

        with pytest.raises(ElementNotFoundError, match="not in selector_map"):
            await ctx._click_impl(
                target="9", x=None, y=None, button="left", click_count=1, force=True
            )

        ctx._send.assert_not_awaited()


# ---------------------------------------------------------------------------
# B2.3: _fill_impl
# ---------------------------------------------------------------------------


class TestFillImpl:
    """``_fill_impl`` — click target, then JS to set active element value."""

    @pytest.mark.asyncio
    async def test_fill_resolves_clicks_then_sets_value(self) -> None:
        ctx = _make_ctx()
        _seed_selector_map(ctx, ref=2)

        # Element resolution (describeNode + getBoxModel) → press + release →
        # final evaluate("...") for setting the value.
        responses: list[dict[str, Any]] = [
            {"node": {"nodeId": 22}},
            {"model": {"content": [0, 0, 100, 0, 100, 50, 0, 50]}},
            {},
            {},
            {"result": None},
        ]
        ctx._send = AsyncMock(side_effect=responses)  # type: ignore[method-assign]

        result = await ctx._fill_impl(target="2", text="hello world")

        assert result == {"filled": True, "text": "hello world"}
        calls = ctx._send.call_args_list
        # Last call is evaluate, not cdp — that's how the bridge sets value.
        final_call = calls[-1]
        assert final_call.args[0] == "evaluate"
        js_payload = final_call.args[1]["js"]
        # The injected JS must JSON-encode the text so quotes are escaped.
        assert json.dumps("hello world") in js_payload
        assert "document.activeElement" in js_payload
        # Native prototype setters update React/Vue trackers before events fire.
        assert "HTMLInputElement.prototype" in js_payload
        assert "HTMLTextAreaElement.prototype" in js_payload
        assert "HTMLSelectElement.prototype" in js_payload
        assert "Object.getOwnPropertyDescriptor(proto,'value')?.set" in js_payload
        assert js_payload.index("Event('input'") < js_payload.index("Event('change'")


# ---------------------------------------------------------------------------
# B2.3b: _select_impl
# ---------------------------------------------------------------------------


class TestSelectImpl:
    @pytest.mark.asyncio
    async def test_select_uses_native_setter(self) -> None:
        ctx = _make_ctx()
        _seed_selector_map(ctx, ref=4)
        ctx._send = AsyncMock(  # type: ignore[method-assign]
            side_effect=[{"object": {"objectId": "obj-select"}}, {}]
        )

        result = await ctx._select_impl(target="4", value="active", label=None)

        assert result == {"selected": True, "value": "active", "label": None}
        calls = ctx._send.call_args_list
        assert calls[0].args[1]["method"] == "DOM.resolveNode"
        params = calls[1].args[1]["params"]
        assert params["objectId"] == "obj-select"
        fn_decl = params["functionDeclaration"]
        assert "HTMLSelectElement.prototype" in fn_decl
        assert "setter.call(this,value)" in fn_decl
        assert fn_decl.index("Event('input'") < fn_decl.index("Event('change'")


# ---------------------------------------------------------------------------
# B2.4: _scroll_impl
# ---------------------------------------------------------------------------


class TestScrollImpl:
    """``_scroll_impl`` — wheel event with deltaX/deltaY by direction."""

    @pytest.mark.asyncio
    async def test_scroll_down_no_target_uses_default_coords(self) -> None:
        """No target → wheel event at the default center (640, 400)."""
        ctx = _make_ctx()
        ctx._send = AsyncMock(return_value={})  # type: ignore[method-assign]

        result = await ctx._scroll_impl(target="", direction="down", amount=500)

        assert result == {"scrolled": True, "direction": "down", "amount": 500}
        call = ctx._send.call_args
        assert call.args[0] == "cdp"
        assert call.args[1]["method"] == "Input.dispatchMouseEvent"
        params = call.args[1]["params"]
        assert params["type"] == "mouseWheel"
        # Default coords when no target.
        assert params["x"] == 640.0
        assert params["y"] == 400.0
        # Down → deltaY positive, deltaX 0.
        assert params["deltaY"] == 500
        assert params["deltaX"] == 0

    @pytest.mark.asyncio
    async def test_scroll_left_negative_delta_x(self) -> None:
        """left direction → deltaX negative."""
        ctx = _make_ctx()
        ctx._send = AsyncMock(return_value={})  # type: ignore[method-assign]

        await ctx._scroll_impl(target="", direction="left", amount=200)

        params = ctx._send.call_args.args[1]["params"]
        assert params["deltaX"] == -200
        assert params["deltaY"] == 0


# ---------------------------------------------------------------------------
# B2.5: _hover_impl
# ---------------------------------------------------------------------------


class TestHoverImpl:
    """``_hover_impl`` — mouseMoved event at element center or coords."""

    @pytest.mark.asyncio
    async def test_hover_with_coordinates(self) -> None:
        """Explicit x/y bypasses element resolution."""
        ctx = _make_ctx()
        ctx._send = AsyncMock(return_value={})  # type: ignore[method-assign]

        result = await ctx._hover_impl(target="", x=200.0, y=100.0)

        assert result == {"hovered": True}
        call = ctx._send.call_args
        params = call.args[1]["params"]
        assert params["type"] == "mouseMoved"
        assert params["x"] == 200.0
        assert params["y"] == 100.0

    @pytest.mark.asyncio
    async def test_hover_without_target_or_coords_raises(self) -> None:
        """Missing both target and coords → BackendError."""
        from agentcloak.core.errors import BackendError

        ctx = _make_ctx()
        ctx._send = AsyncMock(return_value={})  # type: ignore[method-assign]

        with pytest.raises(BackendError, match="hover requires a target"):
            await ctx._hover_impl(target="", x=None, y=None)
