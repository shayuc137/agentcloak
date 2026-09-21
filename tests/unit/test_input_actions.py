"""Input boundary, interrupted gesture and temporary viewport regressions."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from agentcloak.browser.remote_ctx import RemoteBridgeContext
from agentcloak.cli.app import app
from agentcloak.core.errors import AgentBrowserError
from agentcloak.core.input import normalize_key, parse_point, parse_ref, parse_viewport


def context() -> RemoteBridgeContext:
    ws = MagicMock(closed=False)
    ws.send_str = AsyncMock()
    return RemoteBridgeContext(bridge_ws=ws)


@pytest.mark.parametrize("value", ["12", "[12]"])
def test_snapshot_references(value: str) -> None:
    assert parse_ref(value) == 12


@pytest.mark.parametrize("value", ["[]", "[12", "12]", "-1", "abc"])
def test_bad_references(value: str) -> None:
    with pytest.raises(AgentBrowserError):
        parse_ref(value)


@pytest.mark.parametrize("value", ["nan,1", "1,inf", "1", "1,2,3"])
def test_bad_coordinates(value: str) -> None:
    with pytest.raises(AgentBrowserError):
        parse_point(value)


@pytest.mark.parametrize("value", ["0x100", "100x-1", "17000x100", "1.5x100"])
def test_bad_viewport(value: str) -> None:
    with pytest.raises(AgentBrowserError):
        parse_viewport(value)


@pytest.mark.parametrize(
    "key,expected",
    [
        ("CTRL+Enter", "Control+Enter"),
        ("cmd+a", "Meta+a"),
        ("Command+Opt+x", "Meta+Alt+x"),
        ("option+Shift+a", "Alt+Shift+a"),
    ],
)
def test_modifier_aliases(key: str, expected: str) -> None:
    assert normalize_key(key) == expected


@pytest.mark.parametrize("fails", [False, True])
async def test_screenshot_restores_viewport_even_on_failure(fails: bool) -> None:
    ctx = context()
    ctx._get_viewport_impl = AsyncMock(return_value=(1280, 720))
    ctx._set_viewport_impl = AsyncMock()
    ctx._screenshot_impl = AsyncMock(
        side_effect=RuntimeError("capture failed") if fails else None,
        return_value=b"image",
    )
    if fails:
        with pytest.raises(RuntimeError, match="capture failed"):
            await ctx.screenshot(viewport="1024x768")
    else:
        assert await ctx.screenshot(viewport="1024x768") == b"image"
    assert [call.args for call in ctx._set_viewport_impl.await_args_list] == [
        (1024, 768),
        (1280, 720),
    ]


async def test_drag_releases_button_after_move_failure() -> None:
    ctx = context()
    ctx._cdp_send_impl = AsyncMock(
        side_effect=[{}, {}, RuntimeError("move failed"), {}]
    )
    with pytest.raises(RuntimeError, match="move failed"):
        await ctx._run_action("drag", "", from_point="1,2", to_point="20,30", steps=2)
    last = ctx._cdp_send_impl.await_args_list[-1]
    assert last.args[1]["type"] == "mouseReleased"
    assert last.args[1]["buttons"] == 0


@pytest.mark.parametrize(
    "params", [{"steps": 0}, {"steps": 1.2}, {"to_point": "nan,2"}]
)
async def test_drag_rejects_bad_input_before_mouse_down(params: dict[str, Any]) -> None:
    ctx = context()
    ctx._cdp_send_impl = AsyncMock()
    with pytest.raises(AgentBrowserError):
        await ctx._run_action(
            "drag", "", **{"from_point": "1,2", "to_point": "3,4", **params}
        )
    ctx._cdp_send_impl.assert_not_called()


async def test_cdp_timeout_removes_bridge_pending_request() -> None:
    ctx = context()
    with pytest.raises(AgentBrowserError) as caught:
        await ctx.raw_cdp("Runtime.evaluate", {}, timeout=5)
    assert caught.value.error == "cdp_timeout"
    assert ctx._pending == {}


async def test_cdp_cancellation_removes_bridge_pending_request() -> None:
    ctx = context()
    task = asyncio.create_task(ctx.raw_cdp("Runtime.evaluate", {}))
    while not ctx._pending:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ctx._pending == {}


async def test_bridge_press_dispatches_real_ctrl_enter_and_focus() -> None:
    ctx = context()
    ctx._resolve_element_object_id = AsyncMock(return_value="input-object")
    ctx._cdp_send_impl = AsyncMock(return_value={})
    await ctx._run_action("press", "[4]", key="Ctrl+Enter")
    events = [call.args for call in ctx._cdp_send_impl.await_args_list]
    assert events[0][0] == "Runtime.callFunctionOn"
    assert events[1][1]["key"] == "Enter"
    assert events[1][1]["modifiers"] == 2
    assert events[1][1]["windowsVirtualKeyCode"] == 13
    assert events[2][1]["type"] == "keyUp"


@pytest.mark.parametrize(
    "args,expected",
    [
        (["click", "[12]"], {"kind": "click", "index": 12}),
        (["fill", "[12]", "hello"], {"kind": "fill", "index": 12, "text": "hello"}),
        (
            ["hover", "[12]", "--offset", "1,2"],
            {"kind": "hover", "index": 12, "offset": "1,2"},
        ),
        (["press", "Ctrl+Enter"], {"kind": "press", "key": "Control+Enter"}),
        (["drag", "[1]", "[2]"], {"kind": "drag", "target": "1", "destination": "2"}),
    ],
)
def test_cli_inputs(args: list[str], expected: dict[str, Any]) -> None:
    with patch(
        "agentcloak.client.DaemonClient._send_sync",
        return_value={"ok": True, "data": {}},
    ) as request:
        result = CliRunner().invoke(app, ["--json", *args])
    assert result.exit_code == 0, result.output
    assert request.call_args.kwargs["json_body"].items() >= expected.items()


@pytest.mark.parametrize(
    "args,path,body,data",
    [
        (
            ["viewport", "set", "1920x1080"],
            "/viewport",
            {"width": 1920, "height": 1080},
            {"width": 1920, "height": 1080},
        ),
        (
            [
                "cdp",
                "send",
                "Runtime.evaluate",
                "--params",
                '{"expression":"1+1"}',
                "--timeout",
                "250",
            ],
            "/cdp/send",
            {
                "method": "Runtime.evaluate",
                "params": {"expression": "1+1"},
                "timeout": 250,
            },
            {"result": {"value": 2}},
        ),
    ],
)
def test_cli_new_routes(
    args: list[str], path: str, body: dict[str, Any], data: dict[str, Any]
) -> None:
    with patch(
        "agentcloak.client.DaemonClient._send_sync",
        return_value={"ok": True, "data": data},
    ) as request:
        result = CliRunner().invoke(app, ["--json", *args])
    assert result.exit_code == 0, result.output
    assert request.call_args.args == ("POST", path)
    assert request.call_args.kwargs["json_body"] == body


async def test_bridge_pending_fails_when_session_closes() -> None:
    ctx = context()
    task = asyncio.create_task(ctx.raw_cdp("Runtime.evaluate", {}))
    while not ctx._pending:
        await asyncio.sleep(0)
    await ctx.close()
    with pytest.raises(AgentBrowserError) as caught:
        await task
    assert caught.value.error == "bridge_disconnected"
    assert ctx._pending == {}


def test_cdp_http_timeout_preserves_explicit_request_budget() -> None:
    from agentcloak.client import DaemonClient

    client = DaemonClient()
    timeout = client._request_timeout("/cdp/send", {"timeout": 180000})
    assert timeout.read is not None and timeout.read >= 185
    assert timeout.connect == client._connect_timeout_s


async def test_bracket_reference_keeps_stale_retry() -> None:
    from agentcloak.core.errors import ElementNotFoundError
    from agentcloak.daemon.services.action_service import ActionService

    ctx = MagicMock()
    ctx.action = AsyncMock(
        side_effect=[
            ElementNotFoundError(
                error="element_not_found", hint="stale", action="refresh"
            ),
            {"clicked": True},
        ]
    )
    ctx.snapshot = AsyncMock()
    result, retried = await ActionService().execute(ctx, "click", "[12]", extra={})
    assert result == {"clicked": True} and retried
    ctx.snapshot.assert_awaited_once_with(mode="compact")
    assert ctx.action.await_args.args == ("click", "12")


async def test_bracket_reference_keeps_content_scan() -> None:
    from agentcloak.browser.secure_ctx import SecureBrowserContext
    from agentcloak.browser.state import ElementRef, PageSnapshot
    from agentcloak.core.config import AgentcloakConfig
    from agentcloak.core.errors import SecurityError

    cfg = AgentcloakConfig()
    cfg.security.content_scan = True
    cfg.security.content_scan_patterns = ["ignore previous instructions"]
    inner = MagicMock()
    inner.snapshot = AsyncMock(
        return_value=PageSnapshot(
            seq=1,
            url="https://example.com",
            title="",
            mode="accessible",
            tree_text="",
            selector_map={
                12: ElementRef(
                    index=12,
                    tag="button",
                    role="button",
                    text="ignore previous instructions",
                )
            },
        )
    )
    inner.action = AsyncMock()
    with pytest.raises(SecurityError):
        await SecureBrowserContext(inner, cfg).action("click", "[12]")
    inner.action.assert_not_called()


async def test_offscreen_destination_failure_releases_started_drag() -> None:
    ctx = context()
    ctx._element_center_impl = AsyncMock(
        side_effect=[(20.0, 20.0), RuntimeError("target detached")]
    )
    ctx._get_viewport_impl = AsyncMock(return_value=(1024, 720))
    ctx._cdp_send_impl = AsyncMock(return_value={})
    with pytest.raises(RuntimeError, match="target detached"):
        await ctx._run_action("drag", "1", destination="2")
    event_types = [call.args[1]["type"] for call in ctx._cdp_send_impl.await_args_list]
    assert event_types == ["mouseMoved", "mousePressed", "mouseMoved", "mouseReleased"]
    assert ctx._cdp_send_impl.await_args.args[1]["buttons"] == 0


async def test_raw_cdp_keeps_state_but_timeout_preserves_manager_channel() -> None:
    from agentcloak.browser.playwright_ctx import PlaywrightContext
    from agentcloak.core.seq import RingBuffer, SeqCounter

    page = MagicMock()
    raw_channel = MagicMock(send=AsyncMock(return_value={}), detach=AsyncMock())
    manager_channel = MagicMock(detach=AsyncMock())
    page.context.new_cdp_session = AsyncMock(return_value=raw_channel)
    ctx = PlaywrightContext(
        page=page,
        browser=MagicMock(),
        playwright=MagicMock(),
        seq_counter=SeqCounter(),
        ring_buffer=RingBuffer(),
    )
    ctx._cdp_sessions[0] = manager_channel
    await ctx.raw_cdp("Runtime.evaluate", {"expression": "1"})
    await ctx.raw_cdp("Runtime.evaluate", {"expression": "2"})
    page.context.new_cdp_session.assert_awaited_once()
    raw_channel.detach.assert_not_awaited()

    async def hang(*args: Any) -> dict[str, Any]:
        await asyncio.sleep(10)
        return {}

    raw_channel.send.side_effect = hang
    with pytest.raises(AgentBrowserError) as caught:
        await ctx.raw_cdp("Runtime.evaluate", {}, timeout=5)
    assert caught.value.error == "cdp_timeout"
    assert ctx._raw_cdp_sessions == {}
    assert ctx._cdp_sessions == {0: manager_channel}
    raw_channel.detach.assert_awaited_once()
    manager_channel.detach.assert_not_awaited()


async def test_tab_cleanup_detaches_only_its_raw_and_manager_channels() -> None:
    from agentcloak.browser.playwright_ctx import PlaywrightContext
    from agentcloak.core.seq import RingBuffer, SeqCounter

    ctx = PlaywrightContext(
        page=MagicMock(),
        browser=MagicMock(),
        playwright=MagicMock(),
        seq_counter=SeqCounter(),
        ring_buffer=RingBuffer(),
    )
    raw_channel = MagicMock(detach=AsyncMock())
    sibling_channel = MagicMock(detach=AsyncMock())
    manager_channel = MagicMock(detach=AsyncMock())
    ctx._raw_cdp_sessions = {0: raw_channel, 1: sibling_channel}
    ctx._cdp_sessions = {0: manager_channel}
    await ctx._invalidate_cdp_session(0)
    assert ctx._raw_cdp_sessions == {1: sibling_channel}
    assert ctx._cdp_sessions == {}
    raw_channel.detach.assert_awaited_once()
    manager_channel.detach.assert_awaited_once()
    sibling_channel.detach.assert_not_awaited()
