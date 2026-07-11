"""Tests for daemon/routes.py — route registration and response shapes.

Migrated to FastAPI TestClient (httpx-based) as part of the v0.2.0 aiohttp
→ FastAPI migration. The test surface (status codes, response envelopes,
business-level assertions) stays the same so we don't lose regression
coverage during the framework swap.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
from fastapi.testclient import TestClient

from agentcloak.browser.playwright_ctx import PlaywrightContext
from agentcloak.browser.state import PageSnapshot
from agentcloak.core.config import AgentcloakConfig
from agentcloak.core.errors import BrowserTimeoutError
from agentcloak.core.seq import RingBuffer, SeqCounter, SeqEvent
from agentcloak.daemon.app import create_app
from agentcloak.daemon.routes import (
    _batch_has_refs,
    _resolve_action_refs,
    _traverse,
)


def _mock_cdp() -> MagicMock:
    cdp = MagicMock()
    _listeners: dict[str, list] = {}

    def _on(event: str, callback: Any) -> None:
        _listeners.setdefault(event, []).append(callback)

    async def _send(method: str, params: Any = None) -> Any:
        if method == "Accessibility.getFullAXTree":
            return {
                "nodes": [
                    {"role": {"value": "RootWebArea"}, "name": {"value": "Test"}},
                    {"role": {"value": "link"}, "name": {"value": "A link"}},
                ]
            }
        if method == "Runtime.enable":
            main_ctx = {
                "context": {
                    "id": 1,
                    "origin": "https://example.com",
                    "name": "",
                    "auxData": {"isDefault": True, "type": "default", "frameId": "F1"},
                }
            }
            for cb in _listeners.get("Runtime.executionContextCreated", []):
                cb(main_ctx)
            return {}
        if method == "Runtime.disable":
            return {}
        if method == "Runtime.evaluate":
            return {"result": {"type": "string", "value": "hello"}}
        return {}

    cdp.on = MagicMock(side_effect=_on)
    cdp.send = AsyncMock(side_effect=_send)
    cdp.detach = AsyncMock()
    return cdp


def _mock_ctx() -> PlaywrightContext:
    page = MagicMock()
    page.on = MagicMock()
    page.url = "https://example.com"
    page.title = AsyncMock(return_value="Example")
    page.goto = AsyncMock(return_value=MagicMock(status=200))
    page.evaluate = AsyncMock(return_value="hello")
    page.screenshot = AsyncMock(return_value=b"fakepng")
    page.content = AsyncMock(return_value="<html></html>")
    page.context = MagicMock()
    page.context.new_cdp_session = AsyncMock(return_value=_mock_cdp())

    seq = SeqCounter()
    ring = RingBuffer()
    ring.append(
        SeqEvent(
            seq=0,
            kind="network",
            data={
                "method": "GET",
                "url": "https://example.com",
                "status": 200,
                "resource_type": "document",
            },
        )
    )

    ctx = PlaywrightContext(
        page=page,
        browser=MagicMock(),
        playwright=MagicMock(),
        seq_counter=seq,
        ring_buffer=ring,
    )
    return ctx


@pytest.fixture
def client() -> Any:
    app = create_app()
    app.state.browser_ctx = _mock_ctx()
    app.state.shutdown_event = asyncio.Event()
    with TestClient(app) as c:
        yield c


class TestRoutes:
    def test_health(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_health_includes_page_valid(self, client: TestClient) -> None:
        """`/health` exposes page_valid so agents can query without an op."""
        resp = client.get("/health")
        data = resp.json()
        # Fresh context starts with page_valid=True
        assert data.get("page_valid") is True

    def test_screenshot_blocked_after_navigate_failure(
        self, client: TestClient
    ) -> None:
        """navigate failure → screenshot returns no_valid_page (not stale page).

        This is the P0 silent-failure bug guard: agents that batch-execute
        commands must see the failure propagate, not get a screenshot of the
        previous successful page.
        """
        ctx = client.app.state.browser_ctx  # type: ignore[attr-defined]
        # Force the next goto to fail.
        ctx._page.goto = AsyncMock(side_effect=Exception("net::ERR_INVALID_URL"))
        resp = client.post("/navigate", json={"url": "file:///etc/passwd"})
        assert resp.status_code == 400
        # Page must now be marked invalid in the live context.
        assert ctx._page_valid is False

        # Subsequent screenshot returns the structured no_valid_page error,
        # not a stale screenshot.
        resp = client.get("/screenshot")
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "no_valid_page"
        assert "navigate" in data["action"].lower()

        # Evaluate is also blocked.
        resp = client.post("/evaluate", json={"js": "document.title"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "no_valid_page"

        # /health surfaces the invalid state.
        health = client.get("/health").json()
        assert health.get("page_valid") is False

    def test_navigate_failure_then_success_recovers(self, client: TestClient) -> None:
        """Successful navigate after a failure clears the no_valid_page guard."""
        ctx = client.app.state.browser_ctx  # type: ignore[attr-defined]
        # Mock the first navigate to fail.
        outcomes = [
            Exception("net::ERR_NAME_NOT_RESOLVED"),
            MagicMock(status=200),
        ]

        async def _goto(url: str, **kw: Any) -> Any:
            value = outcomes.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        ctx._page.goto = AsyncMock(side_effect=_goto)
        ctx._page.url = "https://example.com"

        resp = client.post("/navigate", json={"url": "https://bad.invalid"})
        assert resp.status_code == 400
        assert ctx._page_valid is False

        # Recovery navigate succeeds.
        resp = client.post("/navigate", json={"url": "https://example.com"})
        assert resp.status_code == 200
        assert ctx._page_valid is True

        # Screenshot now works.
        resp = client.get("/screenshot")
        assert resp.status_code == 200

    def test_navigate(self, client: TestClient) -> None:
        resp = client.post("/navigate", json={"url": "https://test.com"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "data" in data
        assert data["data"]["url"] == "https://example.com"

    def test_screenshot(self, client: TestClient) -> None:
        resp = client.get("/screenshot")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "base64" in data["data"]

    def test_screenshot_uses_config_format_and_allows_override(
        self, client: TestClient
    ) -> None:
        cfg = AgentcloakConfig()
        cfg.browser.screenshot_format = "png"
        client.app.state.config = cfg
        ctx = client.app.state.browser_ctx

        configured = client.get("/screenshot")
        assert configured.status_code == 200
        assert configured.json()["data"]["format"] == "png"
        ctx._page.screenshot.assert_awaited_with(full_page=False, type="png")

        ctx._page.screenshot.reset_mock()
        explicit = client.get("/screenshot?format=jpeg&quality=72")
        assert explicit.status_code == 200
        assert explicit.json()["data"]["format"] == "jpeg"
        ctx._page.screenshot.assert_awaited_with(
            full_page=False, type="jpeg", quality=72
        )

    def test_screenshot_waits_for_selector_before_capture(
        self, client: TestClient
    ) -> None:
        ctx = client.app.state.browser_ctx
        ctx.wait = AsyncMock(return_value={"elapsed_ms": 4})

        resp = client.get("/screenshot?wait_selector=%23ready&wait_timeout=1234")

        assert resp.status_code == 200
        ctx.wait.assert_awaited_once_with(
            condition="selector", value="#ready", timeout=1234, state="visible"
        )
        ctx._page.screenshot.assert_awaited_once()

    def test_screenshot_wait_timeout_keeps_structured_guidance(
        self, client: TestClient
    ) -> None:
        ctx = client.app.state.browser_ctx
        ctx.wait = AsyncMock(
            side_effect=BrowserTimeoutError(
                error="wait_timeout",
                hint="Wait condition 'selector' timed out after 100ms",
                action="increase timeout or check the condition",
            )
        )

        resp = client.get("/screenshot?wait_selector=%23ready&wait_timeout=100")

        assert resp.status_code == 400
        assert resp.json()["error"] == "wait_timeout"
        assert "increase timeout" in resp.json()["action"]
        ctx._page.screenshot.assert_not_awaited()

    def test_snapshot(self, client: TestClient) -> None:
        # ``include_selector_map`` defaults to ``False`` route-side so MCP
        # doesn't pay the token cost; CLI scripting opts back in here.
        resp = client.get("/snapshot?mode=accessible&include_selector_map=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "tree_text" in data["data"]
        assert "selector_map" in data["data"]

    def test_snapshot_selector_resets_diff_baseline(self, client: TestClient) -> None:
        ctx = client.app.state.browser_ctx
        ctx._cached_lines = [(0, '[1] button "Save"', 1)]
        ctx.snapshot = AsyncMock(
            return_value=PageSnapshot(
                seq=0,
                url="https://example.com",
                title="Example",
                mode="accessible",
                tree_text='[1] button "Save"',
                total_nodes=1,
                total_interactive=1,
            )
        )

        first = client.get("/snapshot?mode=accessible&selector=main")
        assert first.status_code == 200
        ctx.snapshot.assert_awaited_with(
            mode="accessible",
            max_nodes=0,
            max_chars=0,
            focus=0,
            offset=0,
            frames=False,
            selector="main",
        )

        changed_scope = client.get("/snapshot?mode=accessible&selector=aside&diff=true")
        assert changed_scope.json()["data"]["diff"] is False

        same_scope = client.get("/snapshot?mode=accessible&selector=aside&diff=true")
        assert same_scope.json()["data"]["diff"] is True

    def test_evaluate(self, client: TestClient) -> None:
        resp = client.post("/evaluate", json={"js": "1+1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "result" in data["data"]

    def test_network(self, client: TestClient) -> None:
        resp = client.get("/network?since=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "requests" in data["data"]

    def test_evaluate_small_result(self, client: TestClient) -> None:
        resp = client.post("/evaluate", json={"js": "1+1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["truncated"] is False
        assert "total_size" in data["data"]

    def test_evaluate_truncation(self, client: TestClient) -> None:
        resp = client.post("/evaluate", json={"js": "1+1", "max_return_size": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["truncated"] is True
        assert "[...truncated...]" in data["data"]["result"]

    def test_cdp_endpoint_no_port(self, client: TestClient) -> None:
        # default _mock_ctx has no _cdp_port → 503
        resp = client.get("/cdp/endpoint")
        assert resp.status_code == 503
        data = resp.json()
        assert data["error"] == "no_cdp_port"

    def test_cdp_endpoint_ok(self) -> None:
        ctx = _mock_ctx()
        ctx._cdp_port = 19222

        app = create_app()
        app.state.browser_ctx = ctx
        app.state.shutdown_event = asyncio.Event()

        # httpx.AsyncClient is used by /cdp/endpoint to probe the DevTools API.
        # Patch it to return our stub.
        mock_resp = MagicMock()
        mock_resp.json = MagicMock(
            return_value={
                "webSocketDebuggerUrl": "ws://127.0.0.1:19222/devtools/browser/abc"
            }
        )

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            TestClient(app) as c,
        ):
            resp = c.get("/cdp/endpoint")
            assert resp.status_code == 200
            data = resp.json()

        assert data["ok"] is True
        assert (
            data["data"]["ws_endpoint"] == "ws://127.0.0.1:19222/devtools/browser/abc"
        )
        assert data["data"]["port"] == 19222
        assert data["data"]["http_url"] == "http://127.0.0.1:19222"

    def test_capture_replay_entry_not_found(self, client: TestClient) -> None:
        resp = client.post(
            "/capture/replay",
            json={"url": "https://example.com/api", "method": "GET"},
        )
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"] == "capture_entry_not_found"

    def test_capture_replay_ok(self, client: TestClient) -> None:
        from datetime import UTC, datetime

        from agentcloak.core.capture import CaptureEntry

        ctx = client.app.state.browser_ctx
        ctx._capture_store.start()
        entry = CaptureEntry(
            seq=1,
            timestamp=datetime.now(tz=UTC).isoformat(),
            method="GET",
            url="https://example.com/api/data",
            status=200,
            resource_type="xhr",
            request_headers={"authorization": "Bearer tok", "host": "example.com"},
            response_headers={},
            content_type="application/json",
        )
        ctx._capture_store.add(entry)

        fetch_result = {"status": 200, "body": '{"ok":true}', "headers": {}}
        ctx.fetch = AsyncMock(return_value=fetch_result)

        resp = client.post(
            "/capture/replay",
            json={"url": "https://example.com/api/data", "method": "GET"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["replayed_from"]["url"] == "https://example.com/api/data"
        assert data["data"]["replayed_from"]["seq"] == 1
        call_kwargs = ctx.fetch.call_args
        passed_headers = call_kwargs.kwargs.get("headers") or {}
        assert "host" not in passed_headers
        assert "authorization" in passed_headers

    def test_profile_create_from_current_ok(
        self, tmp_path: Any, client: TestClient
    ) -> None:
        ctx = client.app.state.browser_ctx
        mock_bctx = MagicMock()
        mock_bctx.cookies = AsyncMock(return_value=[{"name": "sid", "value": "abc"}])
        ctx._get_browser_context = MagicMock(return_value=mock_bctx)

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        mock_paths = MagicMock()
        mock_paths.profiles_dir = tmp_path / "profiles"

        with (
            patch(
                "agentcloak.core.config.load_config",
                return_value=(mock_paths, MagicMock()),
            ),
            patch(
                "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)
            ),
        ):
            resp = client.post(
                "/profile/create-from-current",
                json={"name": "my-profile"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["profile"] == "my-profile"
        assert data["data"]["renamed"] is False
        assert data["data"]["cookie_count"] == 1

    def test_profile_create_from_current_renamed(
        self, tmp_path: Any, client: TestClient
    ) -> None:
        ctx = client.app.state.browser_ctx
        mock_bctx = MagicMock()
        mock_bctx.cookies = AsyncMock(return_value=[])
        ctx._get_browser_context = MagicMock(return_value=mock_bctx)

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        (profiles_dir / "my-site").mkdir()  # pre-existing profile

        mock_paths = MagicMock()
        mock_paths.profiles_dir = profiles_dir

        with (
            patch(
                "agentcloak.core.config.load_config",
                return_value=(mock_paths, MagicMock()),
            ),
            patch(
                "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)
            ),
        ):
            resp = client.post(
                "/profile/create-from-current",
                json={"name": "my-site"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["profile"] == "my-site-2"
        assert data["data"]["renamed"] is True

    def test_shutdown_sets_event(self, client: TestClient) -> None:
        """POST /shutdown must set shutdown_event without raising."""
        event: asyncio.Event = client.app.state.shutdown_event
        assert not event.is_set()
        resp = client.post("/shutdown")
        assert resp.status_code == 200
        assert event.is_set()

    def test_profile_create_from_current_writer_error(
        self, tmp_path: Any, client: TestClient
    ) -> None:
        """Subprocess failure returns 500 with error info."""
        ctx = client.app.state.browser_ctx
        mock_bctx = MagicMock()
        mock_bctx.cookies = AsyncMock(return_value=[])
        ctx._get_browser_context = MagicMock(return_value=mock_bctx)

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"launch failed"))
        mock_proc.returncode = 1

        mock_paths = MagicMock()
        mock_paths.profiles_dir = tmp_path / "profiles"

        with (
            patch(
                "agentcloak.core.config.load_config",
                return_value=(mock_paths, MagicMock()),
            ),
            patch(
                "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)
            ),
        ):
            resp = client.post(
                "/profile/create-from-current",
                json={"name": "my-profile"},
            )

        assert resp.status_code == 500
        data = resp.json()
        assert data["ok"] is False
        assert data["error"] == "profile_writer_failed"


class TestBatchRefResolution:
    """Unit tests for $N.path batch reference resolution (R4.4)."""

    def test_traverse_simple(self) -> None:
        obj = {"data": {"url": "https://example.com"}}
        assert _traverse(obj, "data.url") == "https://example.com"

    def test_traverse_single_key(self) -> None:
        obj = {"name": "test"}
        assert _traverse(obj, "name") == "test"

    def test_traverse_deep_path(self) -> None:
        obj = {"a": {"b": {"c": {"d": 42}}}}
        assert _traverse(obj, "a.b.c.d") == 42

    def test_traverse_missing_key_raises(self) -> None:
        obj = {"data": {"url": "test"}}
        with pytest.raises(KeyError):
            _traverse(obj, "data.missing")

    def test_traverse_non_dict_raises(self) -> None:
        obj = {"data": "string_value"}
        with pytest.raises(KeyError, match="Cannot traverse"):
            _traverse(obj, "data.nested")

    def test_batch_has_refs_true(self) -> None:
        actions = [
            {"kind": "click", "target": "5"},
            {"kind": "fill", "text": "$0.data.url"},
        ]
        assert _batch_has_refs(actions) is True

    def test_batch_has_refs_false(self) -> None:
        actions = [
            {"kind": "click", "target": "5"},
            {"kind": "fill", "text": "plain text"},
        ]
        assert _batch_has_refs(actions) is False

    def test_batch_has_refs_empty(self) -> None:
        assert _batch_has_refs([]) is False

    def test_batch_has_refs_non_string_values(self) -> None:
        actions = [{"kind": "click", "index": 5, "click_count": 1}]
        assert _batch_has_refs(actions) is False

    def test_resolve_action_refs_basic(self) -> None:
        params = {"kind": "fill", "text": "$0.data.url"}
        results = [{"data": {"url": "https://example.com"}}]
        resolved = _resolve_action_refs(params, results)
        assert resolved == {"kind": "fill", "text": "https://example.com"}

    def test_resolve_action_refs_no_refs(self) -> None:
        params = {"kind": "click", "target": "5"}
        results = [{"data": {"url": "test"}}]
        resolved = _resolve_action_refs(params, results)
        assert resolved == {"kind": "click", "target": "5"}

    def test_resolve_action_refs_non_string_preserved(self) -> None:
        params = {"kind": "click", "index": 5, "click_count": 1}
        resolved = _resolve_action_refs(params, [])
        assert resolved == {"kind": "click", "index": 5, "click_count": 1}

    def test_resolve_action_refs_out_of_bounds_preserved(self) -> None:
        params = {"text": "$99.data.url"}
        results = [{"data": {"url": "test"}}]
        resolved = _resolve_action_refs(params, results)
        # Out-of-bounds $N is preserved as-is
        assert resolved == {"text": "$99.data.url"}

    def test_resolve_action_refs_multiple(self) -> None:
        params = {"url": "$0.data.url", "title": "$1.data.title", "kind": "fill"}
        results = [
            {"data": {"url": "https://a.com"}},
            {"data": {"title": "Page B"}},
        ]
        resolved = _resolve_action_refs(params, results)
        assert resolved["url"] == "https://a.com"
        assert resolved["title"] == "Page B"
        assert resolved["kind"] == "fill"

    def test_resolve_action_refs_bad_path_raises(self) -> None:
        params = {"text": "$0.missing.key"}
        results = [{"data": {"url": "test"}}]
        with pytest.raises(KeyError):
            _resolve_action_refs(params, results)


class TestIncludeSnapshot:
    """Tests for includeSnapshot action parameter (R4.3)."""

    def test_action_include_snapshot(self, client: TestClient) -> None:
        ctx = client.app.state.browser_ctx
        ctx.action = AsyncMock(
            return_value={"ok": True, "clicked": True, "seq": 1, "action": "click"}
        )

        resp = client.post(
            "/action",
            json={"kind": "click", "target": "5", "include_snapshot": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "snapshot" in data["data"]
        snap = data["data"]["snapshot"]
        assert "tree_text" in snap
        assert "mode" in snap
        assert "total_nodes" in snap
        assert "total_interactive" in snap

    def test_action_without_include_snapshot(self, client: TestClient) -> None:
        ctx = client.app.state.browser_ctx
        ctx.action = AsyncMock(
            return_value={"ok": True, "clicked": True, "seq": 1, "action": "click"}
        )

        resp = client.post(
            "/action",
            json={"kind": "click", "target": "5"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "snapshot" not in data["data"]


class TestStaleRefRetry:
    """Tests for stale ref auto-retry (R4.5)."""

    def test_stale_ref_retries_on_numeric_target(self, client: TestClient) -> None:
        from agentcloak.core.errors import ElementNotFoundError

        ctx = client.app.state.browser_ctx
        call_count = 0

        async def action_side_effect(kind: str, target: str, **kw: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ElementNotFoundError(
                    error="element_not_found",
                    hint="Element [5] not in selector_map",
                    action="take a new snapshot",
                )
            return {"ok": True, "clicked": True, "seq": 2, "action": "click"}

        ctx.action = AsyncMock(side_effect=action_side_effect)

        resp = client.post(
            "/action",
            json={"kind": "click", "target": "5"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["retried"] is True
        assert call_count == 2

    def test_stale_ref_no_retry_on_non_numeric(self, client: TestClient) -> None:
        from agentcloak.core.errors import ElementNotFoundError

        ctx = client.app.state.browser_ctx
        ctx.action = AsyncMock(
            side_effect=ElementNotFoundError(
                error="element_not_found",
                hint="fill requires a target element",
                action="provide target",
            )
        )

        resp = client.post(
            "/action",
            json={"kind": "fill", "target": ""},
        )
        # AgentBrowserError → 400 via exception handler
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "element_not_found"


class TestBatchWithRefs:
    """Integration tests for $N batch references in handle_action_batch."""

    def test_batch_with_refs_resolves(self, client: TestClient) -> None:
        ctx = client.app.state.browser_ctx
        call_count = 0

        async def action_side_effect(kind: str, target: str, **kw: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if kind == "click":
                return {
                    "ok": True,
                    "clicked": True,
                    "seq": 1,
                    "action": "click",
                    "result_url": "https://clicked.com",
                }
            if kind == "fill":
                return {
                    "ok": True,
                    "filled": True,
                    "seq": 2,
                    "action": "fill",
                    "text": kw.get("text", ""),
                }
            return {"ok": True}

        ctx.action = AsyncMock(side_effect=action_side_effect)

        actions = [
            {"kind": "click", "target": "5"},
            {"kind": "fill", "target": "3", "text": "$0.result_url"},
        ]
        resp = client.post(
            "/action/batch",
            json={"actions": actions, "sleep": 0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        results = data["data"]["results"]
        assert len(results) == 2
        assert results[1]["text"] == "https://clicked.com"

    def test_batch_without_refs_delegates_to_backend(self, client: TestClient) -> None:
        ctx = client.app.state.browser_ctx
        ctx.action_batch = AsyncMock(
            return_value={"results": [], "completed": 0, "total": 0}
        )

        actions = [
            {"kind": "click", "target": "5"},
            {"kind": "fill", "target": "3", "text": "hello"},
        ]
        resp = client.post(
            "/action/batch",
            json={"actions": actions, "sleep": 0},
        )
        assert resp.status_code == 200
        ctx.action_batch.assert_called_once()

    def test_batch_ref_resolution_failure(self, client: TestClient) -> None:
        ctx = client.app.state.browser_ctx
        ctx.action = AsyncMock(
            return_value={
                "ok": True,
                "clicked": True,
                "seq": 1,
                "action": "click",
            }
        )

        actions = [
            {"kind": "click", "target": "5"},
            {"kind": "fill", "target": "3", "text": "$0.nonexistent.path"},
        ]
        resp = client.post(
            "/action/batch",
            json={"actions": actions, "sleep": 0},
        )
        assert resp.status_code == 200
        data = resp.json()
        results = data["data"]["results"]
        assert results[0]["ok"] is True
        assert results[1]["error"] == "ref_resolution_failed"
        assert data["data"]["aborted_reason"] == "ref_resolution_failed"


# Re-export orjson so the old TestClient body assertions continue to work.
_ = orjson
