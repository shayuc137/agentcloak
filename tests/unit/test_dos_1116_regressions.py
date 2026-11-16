"""DOS 11-16 batch: ref safety, action echo, evaluate scalars, route removal, units.

Each test names the DOS issue it guards. The failure modes were silent (a
click on an unseen element, ``True`` vs ``true``, "added rule" for a removal),
so these assert the exact user-visible output, not just status codes.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agentcloak.browser.managers.route_manager import RouteManager
from agentcloak.browser.remote_ctx import RemoteBridgeContext
from agentcloak.browser.state import ElementRef
from agentcloak.cli.app import app
from agentcloak.core.errors import AgentBrowserError, BackendError, ElementNotFoundError
from agentcloak.core.input import parse_duration
from agentcloak.core.text_renderers import (
    render_action_text,
    render_evaluate_text,
    render_route_remove_text,
)
from agentcloak.daemon.app import create_app

runner = CliRunner()

# A page whose menu is closed: only the nav link remains. A full snapshot
# numbers it [1]; the menu item "阿采" is gone.
_NAV_ONLY_TREE: list[dict[str, Any]] = [
    {
        "nodeId": "root",
        "role": {"value": "RootWebArea"},
        "name": {"value": "Orders"},
        "childIds": ["nav"],
    },
    {
        "nodeId": "nav",
        "role": {"value": "link"},
        "name": {"value": "客户"},
        "backendDOMNodeId": 7,
    },
]


def _remote_ctx() -> RemoteBridgeContext:
    ws = MagicMock(closed=False)
    ws.send_str = AsyncMock()
    ctx = RemoteBridgeContext(bridge_ws=ws)
    ctx._get_ax_tree = AsyncMock(return_value=_NAV_ONLY_TREE)  # type: ignore[method-assign]
    ctx._get_page_info = AsyncMock(  # type: ignore[method-assign]
        return_value=("https://dos.test/orders", "Orders")
    )
    return ctx


# ---------------------------------------------------------------------------
# #1 — refs only mean what the latest snapshot showed
# ---------------------------------------------------------------------------


class TestRefsFollowLatestSnapshot:
    async def test_zero_hit_find_leaves_no_clickable_refs(self) -> None:
        ctx = _remote_ctx()
        snap = await ctx.snapshot(mode="compact", find="阿采")
        assert snap.selector_map == {}

        with pytest.raises(ElementNotFoundError) as exc_info:
            ctx._require_snapshot(2)

        assert exc_info.value.error == "element_not_found"
        assert "--find '阿采'" in exc_info.value.hint
        assert "0 refs" in exc_info.value.hint

    async def test_unfiltered_snapshot_reports_ref_count(self) -> None:
        ctx = _remote_ctx()
        await ctx.snapshot(mode="compact")

        with pytest.raises(ElementNotFoundError) as exc_info:
            ctx._require_snapshot(2)

        assert "(1 refs)" in exc_info.value.hint
        assert ctx._require_snapshot(1).text == "客户"

    def test_before_any_snapshot_says_so(self) -> None:
        ctx = _remote_ctx()
        with pytest.raises(ElementNotFoundError) as exc_info:
            ctx._require_snapshot(1)
        assert exc_info.value.error == "no_snapshot"


def _playwright_ctx(tree: list[dict[str, Any]]) -> Any:
    from agentcloak.browser.playwright_ctx import PlaywrightContext
    from agentcloak.core.seq import RingBuffer, SeqCounter

    page = MagicMock()
    page.on = MagicMock()
    ctx = PlaywrightContext(
        page=page,
        browser=MagicMock(),
        playwright=MagicMock(),
        seq_counter=SeqCounter(),
        ring_buffer=RingBuffer(),
    )
    ctx._get_ax_tree = AsyncMock(return_value=tree)
    ctx._resolve_by_backend_node = AsyncMock(return_value="locator")
    ctx._selector_map = {
        5: ElementRef(index=5, tag="button", role="button", text="删除")
    }
    ctx._backend_node_map = {5: 100}
    return ctx


def _button(node_id: str, name: str, backend_id: int) -> dict[str, Any]:
    return {
        "nodeId": node_id,
        "role": {"value": "button"},
        "name": {"value": name},
        "backendDOMNodeId": backend_id,
    }


class TestStaleRefReidentification:
    """A stale backend node is re-found only by an exact, unique identity."""

    async def test_unique_exact_match_rebinds_ref(self) -> None:
        ctx = _playwright_ctx(
            [_button("a", "删除", 200), _button("b", "删除订单", 201)]
        )

        assert await ctx._resolve_by_role(5) == "locator"

        ctx._resolve_by_backend_node.assert_awaited_once_with(200, 5)
        assert ctx._backend_node_map[5] == 200

    async def test_ambiguous_identity_fails(self) -> None:
        ctx = _playwright_ctx([_button("a", "删除", 200), _button("b", "删除", 201)])

        with pytest.raises(BackendError, match="2 elements now match"):
            await ctx._resolve_by_role(5)
        ctx._resolve_by_backend_node.assert_not_awaited()

    async def test_no_same_role_fallback(self) -> None:
        """The old fallback clicked the first element with the same role."""
        ctx = _playwright_ctx([_button("a", "保存", 200)])

        with pytest.raises(BackendError, match="no longer on the page"):
            await ctx._resolve_by_role(5)
        ctx._resolve_by_backend_node.assert_not_awaited()


# ---------------------------------------------------------------------------
# #2 — the action echo names its target
# ---------------------------------------------------------------------------


def test_click_echo_names_role_and_name() -> None:
    text = render_action_text(
        "click", "2", {"role": "link", "name": "客户", "pending_requests": 2}
    )
    assert text == 'clicked [2] link "客户" | pending_requests: 2'


def test_nameless_target_echoes_role_only() -> None:
    assert render_action_text("click", "4", {"role": "button"}) == "clicked [4] button"


# ---------------------------------------------------------------------------
# #3 — evaluate scalars use JSON spelling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "true"),
        (False, "false"),
        (None, "null"),
        (3, "3"),
        (10**25, "10000000000000000000000000"),
        ("", ""),
        ("hello", "hello"),
        ([True, None], "[\n  true,\n  null\n]"),
    ],
)
def test_evaluate_scalars_match_nested_spelling(value: Any, expected: str) -> None:
    assert render_evaluate_text({"result": value}) == expected


# ---------------------------------------------------------------------------
# #7 — route remove accepts IDs and never reports a removal as an addition
# ---------------------------------------------------------------------------


def _route_client() -> TestClient:
    ctx = MagicMock()
    ctx.seq = 0
    ctx._route_add_impl = AsyncMock()
    ctx._route_remove_impl = AsyncMock()
    ctx.route_manager = RouteManager(ctx)
    application = create_app()
    application.state.browser_ctx = ctx
    return TestClient(application)


class TestRouteRemove:
    def test_remove_by_id_reports_rule_and_remaining(self) -> None:
        client = _route_client()
        first = client.post(
            "/route/add", json={"pattern": "*/api/orders", "action": "abort"}
        )
        client.post("/route/add", json={"pattern": "*/ads", "action": "abort"})
        rule_id = first.json()["data"]["identifier"]

        resp = client.post("/route/remove", json={"target": rule_id})

        data = resp.json()["data"]
        assert resp.status_code == 200
        assert data["removed_rules"] == [
            {"identifier": rule_id, "pattern": "*/api/orders"}
        ]
        assert data["count"] == 1
        assert render_route_remove_text(data) == (
            f"removed rule {rule_id} */api/orders (1 active)"
        )

    def test_unknown_target_fails_with_active_rules(self) -> None:
        client = _route_client()
        client.post("/route/add", json={"pattern": "*/ads", "action": "abort"})

        resp = client.post("/route/remove", json={"target": "deadbeef0000"})

        assert resp.status_code == 404
        body = resp.json()
        assert body["error"] == "route_not_found"
        assert "*/ads" in body["hint"]

    def test_legacy_pattern_field_still_removes(self) -> None:
        client = _route_client()
        client.post("/route/add", json={"pattern": "*/ads", "action": "abort"})

        resp = client.post("/route/remove", json={"pattern": "*/ads"})

        assert resp.json()["data"]["removed"] == 1

    def test_cli_sends_target_and_never_prints_added(self) -> None:
        payload = {
            "ok": True,
            "seq": 0,
            "data": {
                "pattern": "abc123def456",
                "removed": 1,
                "removed_rules": [{"identifier": "abc123def456", "pattern": "*/x"}],
                "count": 0,
            },
        }
        with patch(
            "agentcloak.client.DaemonClient._send_sync", return_value=payload
        ) as send:
            result = runner.invoke(app, ["route", "remove", "abc123def456"])

        assert result.exit_code == 0, result.output
        assert send.call_args.kwargs["json_body"] == {"target": "abc123def456"}
        assert "added" not in result.output
        assert "removed rule abc123def456 */x (0 active)" in result.output


# ---------------------------------------------------------------------------
# #8 — timeouts accept units; bare numbers keep each flag's unit and warn
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "unit", "expected"),
    [
        ("45", "ms", (45.0, False)),
        ("45s", "ms", (45000.0, True)),
        ("45000ms", "s", (45.0, True)),
        ("1.5S", "s", (1.5, True)),
        ("60", "s", (60.0, False)),
    ],
)
def test_parse_duration(raw: str, unit: str, expected: tuple[float, bool]) -> None:
    assert parse_duration(raw, unit=unit) == expected


@pytest.mark.parametrize("raw", ["", "abc", "-5", "5m", "5 sec"])
def test_parse_duration_rejects(raw: str) -> None:
    with pytest.raises(AgentBrowserError):
        parse_duration(raw, unit="ms")


def _sent_timeout(args: list[str]) -> tuple[Any, str]:
    with patch(
        "agentcloak.client.DaemonClient._send_sync",
        return_value={"ok": True, "seq": 0, "data": {}},
    ) as send:
        result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return send.call_args.kwargs["json_body"]["timeout"], result.output


def test_wait_seconds_suffix_becomes_ms() -> None:
    timeout, output = _sent_timeout(["wait", "--selector", "#x", "--timeout", "45s"])
    assert timeout == 45000
    assert "warning" not in output


def test_bare_seconds_on_ms_flag_warns_but_runs() -> None:
    """The DOS run passed 45 meaning seconds and timed out after 45ms."""
    timeout, output = _sent_timeout(["wait", "--selector", "#x", "--timeout", "45"])
    assert timeout == 45
    assert "warning: --timeout 45 means 45ms; write 45s" in output


def test_navigate_ms_suffix_becomes_seconds() -> None:
    timeout, _ = _sent_timeout(["navigate", "https://x.test", "--timeout", "45000ms"])
    assert timeout == 45.0
