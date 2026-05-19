"""Tests for browser/playwright_ctx.py — PlaywrightContext with mocked Playwright."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcloak.browser.playwright_ctx import PlaywrightContext
from agentcloak.core.errors import BackendError, BrowserTimeoutError, NavigationError
from agentcloak.core.seq import RingBuffer, SeqCounter

_NODE_ID_COUNTER = 0


def _cdp_node(
    role: str,
    name: str,
    *,
    node_id: str = "",
    child_ids: list[str] | None = None,
    backend_dom_id: int | None = None,
    properties: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    global _NODE_ID_COUNTER
    if not node_id:
        _NODE_ID_COUNTER += 1
        node_id = str(_NODE_ID_COUNTER)
    node: dict[str, Any] = {
        "nodeId": node_id,
        "role": {"value": role},
        "name": {"value": name},
    }
    if child_ids is not None:
        node["childIds"] = child_ids
    if backend_dom_id is not None:
        node["backendDOMNodeId"] = backend_dom_id
    if properties is not None:
        node["properties"] = properties
    return node


def _ax_tree_response() -> dict[str, Any]:
    return {
        "nodes": [
            _cdp_node(
                "RootWebArea",
                "Example",
                node_id="root",
                child_ids=["h1", "lnk", "btn", "txt"],
            ),
            _cdp_node("heading", "Main Title", node_id="h1"),
            _cdp_node("link", "Click me", node_id="lnk", backend_dom_id=10),
            _cdp_node(
                "button",
                "Submit",
                node_id="btn",
                backend_dom_id=11,
            ),
            _cdp_node(
                "textbox",
                "Search",
                node_id="txt",
                backend_dom_id=12,
            ),
        ]
    }


def _mock_cdp_session() -> MagicMock:
    cdp = MagicMock()
    # Track event listeners so Runtime.enable can replay contexts.
    _listeners: dict[str, list] = {}

    def _on(event: str, callback: Any) -> None:
        _listeners.setdefault(event, []).append(callback)

    async def _send(method: str, params: Any = None) -> Any:
        if method == "Accessibility.getFullAXTree":
            return _ax_tree_response()
        if method == "Runtime.enable":
            # Replay existing execution contexts — simulates CDP behavior.
            main_ctx = {
                "context": {
                    "id": 1,
                    "origin": "https://example.com",
                    "name": "",
                    "auxData": {
                        "isDefault": True,
                        "type": "default",
                        "frameId": "F1",
                    },
                }
            }
            for cb in _listeners.get("Runtime.executionContextCreated", []):
                cb(main_ctx)
            return {}
        if method == "Runtime.disable":
            return {}
        if method == "Runtime.evaluate":
            return {"result": {"type": "string", "value": "result"}}
        return {}

    cdp.on = MagicMock(side_effect=_on)
    cdp.send = AsyncMock(side_effect=_send)
    cdp.detach = AsyncMock()
    return cdp


def _default_page() -> MagicMock:
    page = MagicMock()
    page.on = MagicMock()
    page.url = "https://example.com"
    page.title = AsyncMock(return_value="Example")
    page.goto = AsyncMock(return_value=MagicMock(status=200))
    page.evaluate = AsyncMock(return_value="result")
    page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nfakedata")
    page.content = AsyncMock(return_value="<html><body>Hello</body></html>")
    page.context = MagicMock()
    page.context.new_cdp_session = AsyncMock(return_value=_mock_cdp_session())
    return page


def _make_ctx(
    *,
    page: Any | None = None,
) -> PlaywrightContext:
    mock_page = page if page is not None else _default_page()
    return PlaywrightContext(
        page=mock_page,
        browser=MagicMock(),
        playwright=MagicMock(),
        seq_counter=SeqCounter(),
        ring_buffer=RingBuffer(),
    )


class TestNavigate:
    @pytest.mark.asyncio
    async def test_navigate_success(self) -> None:
        ctx = _make_ctx()
        result = await ctx.navigate("https://example.com")
        assert result["url"] == "https://example.com"
        assert result["title"] == "Example"
        assert result["status"] == 200
        assert ctx.seq == 1

    @pytest.mark.asyncio
    async def test_navigate_timeout(self) -> None:
        page = MagicMock()
        page.on = MagicMock()
        page.goto = AsyncMock(side_effect=Exception("Timeout 30000ms exceeded"))
        ctx = _make_ctx(page=page)
        with pytest.raises(BrowserTimeoutError):
            await ctx.navigate("https://slow.example.com")

    @pytest.mark.asyncio
    async def test_navigate_failure(self) -> None:
        page = MagicMock()
        page.on = MagicMock()
        page.goto = AsyncMock(side_effect=Exception("net::ERR_NAME_NOT_RESOLVED"))
        ctx = _make_ctx(page=page)
        with pytest.raises(NavigationError):
            await ctx.navigate("https://bad.example.com")


class TestSnapshot:
    @pytest.mark.asyncio
    async def test_accessible_mode(self) -> None:
        ctx = _make_ctx()
        snap = await ctx.snapshot(mode="accessible")
        assert snap.mode == "accessible"
        assert len(snap.selector_map) == 3
        assert "[1]" in snap.tree_text
        assert "Click me" in snap.tree_text

    @pytest.mark.asyncio
    async def test_dom_mode(self) -> None:
        ctx = _make_ctx()
        snap = await ctx.snapshot(mode="dom")
        assert snap.mode == "dom"
        assert "<html>" in snap.tree_text

    @pytest.mark.asyncio
    async def test_content_mode(self) -> None:
        """Content mode walks the AX tree and emits names without ref/role labels.

        After the unification with accessible/compact (P2 #9), content mode
        runs the same ``build_snapshot`` pipeline and inherits StaticText
        aggregation. The output is plain text with no ``[N]`` refs, no role
        tokens, and no ARIA attribute strings — just what a human would read.
        """
        ctx = _make_ctx()
        snap = await ctx.snapshot(mode="content")
        assert snap.mode == "content"
        # Names from the AX tree fixture show up as bare text.
        assert "Click me" in snap.tree_text
        assert "Submit" in snap.tree_text
        assert "Main Title" in snap.tree_text
        # No ``[N]`` refs or role tokens leak into content output.
        assert "[1]" not in snap.tree_text
        assert "button" not in snap.tree_text
        assert "heading" not in snap.tree_text

    @pytest.mark.asyncio
    async def test_accessible_filters_inline_text_box(self) -> None:
        """InlineTextBox and LineBreak roles are filtered from accessible output."""
        cdp = MagicMock()

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Accessibility.getFullAXTree":
                return {
                    "nodes": [
                        _cdp_node(
                            "RootWebArea",
                            "Page",
                            node_id="r",
                            child_ids=["b1", "itb", "lb", "st"],
                        ),
                        _cdp_node("button", "OK", node_id="b1"),
                        _cdp_node("InlineTextBox", "some inline text", node_id="itb"),
                        _cdp_node("LineBreak", "", node_id="lb"),
                        _cdp_node("StaticText", "visible", node_id="st"),
                    ]
                }
            return {}

        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        snap = await ctx.snapshot(mode="accessible")
        assert "InlineTextBox" not in snap.tree_text
        assert "LineBreak" not in snap.tree_text
        assert "OK" in snap.tree_text
        assert "visible" in snap.tree_text

    @pytest.mark.asyncio
    async def test_compact_mode(self) -> None:
        """Compact mode: interactive elements + ancestor context roles."""
        cdp = MagicMock()

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Accessibility.getFullAXTree":
                return {
                    "nodes": [
                        _cdp_node(
                            "RootWebArea",
                            "Page",
                            node_id="cr",
                            child_ids=["nav", "btn1"],
                        ),
                        _cdp_node(
                            "navigation",
                            "Main Nav",
                            node_id="nav",
                            child_ids=["lnk1"],
                        ),
                        _cdp_node("link", "Home", node_id="lnk1"),
                        _cdp_node("button", "Submit", node_id="btn1"),
                    ]
                }
            return {}

        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        snap = await ctx.snapshot(mode="compact")
        assert snap.mode == "compact"
        assert len(snap.selector_map) == 2
        assert "[1]" in snap.tree_text
        # navigation is an ancestor context role of the link, preserved
        assert "Main Nav" in snap.tree_text
        # RootWebArea is not interactive and not a context role
        assert "RootWebArea" not in snap.tree_text

    @pytest.mark.asyncio
    async def test_invalid_mode(self) -> None:
        ctx = _make_ctx()
        with pytest.raises(BackendError):
            await ctx.snapshot(mode="invalid")


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_evaluate_success(self) -> None:
        ctx = _make_ctx()
        result = await ctx.evaluate("document.title")
        assert result == "result"
        assert ctx.seq == 1

    @pytest.mark.asyncio
    async def test_evaluate_error(self) -> None:
        err_cdp = MagicMock()
        err_cdp.send = AsyncMock(side_effect=Exception("SyntaxError"))
        err_cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=err_cdp)
        ctx = _make_ctx(page=page)
        with pytest.raises(BackendError):
            await ctx.evaluate("bad code{{{")

    @pytest.mark.asyncio
    async def test_evaluate_utility_world(self) -> None:
        ctx = _make_ctx()
        result = await ctx.evaluate("document.title", world="utility")
        assert result == "result"
        assert ctx.seq == 1

    @pytest.mark.asyncio
    async def test_evaluate_cdp_exception_details(self) -> None:
        """CDP exceptionDetails in response raises BackendError."""
        cdp = MagicMock()
        _listeners: dict[str, list] = {}

        def _on(event: str, callback: Any) -> None:
            _listeners.setdefault(event, []).append(callback)

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Runtime.enable":
                main_ctx = {
                    "context": {
                        "id": 1,
                        "origin": "",
                        "name": "",
                        "auxData": {
                            "isDefault": True,
                            "type": "default",
                            "frameId": "F1",
                        },
                    }
                }
                for cb in _listeners.get("Runtime.executionContextCreated", []):
                    cb(main_ctx)
                return {}
            if method == "Runtime.disable":
                return {}
            if method == "Runtime.evaluate":
                return {
                    "result": {"type": "object"},
                    "exceptionDetails": {"text": "ReferenceError: x is not defined"},
                }
            return {}

        cdp.on = MagicMock(side_effect=_on)
        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        with pytest.raises(BackendError) as exc_info:
            await ctx.evaluate("x")
        assert "ReferenceError" in exc_info.value.hint

    @pytest.mark.asyncio
    async def test_evaluate_undefined_returns_none(self) -> None:
        """CDP result type 'undefined' returns Python None."""
        cdp = MagicMock()
        _listeners: dict[str, list] = {}

        def _on(event: str, callback: Any) -> None:
            _listeners.setdefault(event, []).append(callback)

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Runtime.enable":
                main_ctx = {
                    "context": {
                        "id": 1,
                        "origin": "",
                        "name": "",
                        "auxData": {
                            "isDefault": True,
                            "type": "default",
                            "frameId": "F1",
                        },
                    }
                }
                for cb in _listeners.get("Runtime.executionContextCreated", []):
                    cb(main_ctx)
                return {}
            if method == "Runtime.disable":
                return {}
            if method == "Runtime.evaluate":
                return {"result": {"type": "undefined"}}
            return {}

        cdp.on = MagicMock(side_effect=_on)
        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        result = await ctx.evaluate("void 0")
        assert result is None

    @pytest.mark.asyncio
    async def test_evaluate_main_world_passes_context_id(self) -> None:
        """Main world evaluate passes contextId from Runtime.enable discovery."""
        cdp = MagicMock()
        _listeners: dict[str, list] = {}
        captured_params: dict[str, Any] = {}

        def _on(event: str, callback: Any) -> None:
            _listeners.setdefault(event, []).append(callback)

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Runtime.enable":
                main_ctx = {
                    "context": {
                        "id": 42,
                        "origin": "https://example.com",
                        "name": "",
                        "auxData": {
                            "isDefault": True,
                            "type": "default",
                            "frameId": "F1",
                        },
                    }
                }
                for cb in _listeners.get("Runtime.executionContextCreated", []):
                    cb(main_ctx)
                return {}
            if method == "Runtime.disable":
                return {}
            if method == "Runtime.evaluate":
                captured_params.update(params or {})
                return {"result": {"type": "string", "value": "ok"}}
            return {}

        cdp.on = MagicMock(side_effect=_on)
        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        result = await ctx.evaluate("window.jQuery")
        assert result == "ok"
        assert captured_params["contextId"] == 42

    @pytest.mark.asyncio
    async def test_evaluate_main_world_no_context_raises(self) -> None:
        """Raises BackendError when no main world context is found."""
        cdp = MagicMock()

        def _on(event: str, callback: Any) -> None:
            pass  # never fires any context events

        async def _send(method: str, params: Any = None) -> Any:
            if method in ("Runtime.enable", "Runtime.disable"):
                return {}
            return {}

        cdp.on = MagicMock(side_effect=_on)
        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        with pytest.raises(BackendError) as exc_info:
            await ctx.evaluate("1+1")
        assert "main world" in exc_info.value.hint


class TestScreenshot:
    @pytest.mark.asyncio
    async def test_returns_bytes(self) -> None:
        ctx = _make_ctx()
        result = await ctx.screenshot()
        assert isinstance(result, bytes)

    @pytest.mark.asyncio
    async def test_does_not_increment_seq(self) -> None:
        ctx = _make_ctx()
        await ctx.screenshot()
        assert ctx.seq == 0

    @pytest.mark.asyncio
    async def test_default_format_jpeg(self) -> None:
        """Default screenshot format is JPEG with quality 80."""
        page = _default_page()
        ctx = _make_ctx(page=page)
        await ctx.screenshot()
        page.screenshot.assert_called_once_with(
            full_page=False, type="jpeg", quality=80
        )

    @pytest.mark.asyncio
    async def test_png_format_no_quality(self) -> None:
        """PNG format omits quality parameter."""
        page = _default_page()
        ctx = _make_ctx(page=page)
        await ctx.screenshot(format="png")
        page.screenshot.assert_called_once_with(full_page=False, type="png")


class TestSeqBehavior:
    @pytest.mark.asyncio
    async def test_navigate_increments(self) -> None:
        ctx = _make_ctx()
        await ctx.navigate("https://a.com")
        await ctx.navigate("https://b.com")
        assert ctx.seq == 2

    @pytest.mark.asyncio
    async def test_snapshot_does_not_increment(self) -> None:
        ctx = _make_ctx()
        await ctx.navigate("https://a.com")
        await ctx.snapshot()
        assert ctx.seq == 1

    @pytest.mark.asyncio
    async def test_evaluate_increments(self) -> None:
        ctx = _make_ctx()
        await ctx.evaluate("1+1")
        assert ctx.seq == 1


class TestSnapshotProperties:
    """Tests for R1: ARIA property extraction."""

    @pytest.mark.asyncio
    async def test_checkbox_checked_state(self) -> None:
        """Checkbox shows checked state in output."""
        cdp = MagicMock()

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Accessibility.getFullAXTree":
                return {
                    "nodes": [
                        _cdp_node(
                            "RootWebArea",
                            "Page",
                            node_id="pr",
                            child_ids=["cb1"],
                        ),
                        _cdp_node(
                            "checkbox",
                            "I agree",
                            node_id="cb1",
                            backend_dom_id=20,
                            properties=[
                                {
                                    "name": "checked",
                                    "value": {"type": "tristate", "value": True},
                                }
                            ],
                        ),
                    ]
                }
            return {}

        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        snap = await ctx.snapshot(mode="accessible")
        assert "checked" in snap.tree_text
        assert '"I agree"' in snap.tree_text

    @pytest.mark.asyncio
    async def test_textbox_value_shown(self) -> None:
        """Textbox shows current value in output."""
        cdp = MagicMock()

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Accessibility.getFullAXTree":
                return {
                    "nodes": [
                        _cdp_node(
                            "RootWebArea",
                            "Page",
                            node_id="pr2",
                            child_ids=["tb1"],
                        ),
                        {
                            "nodeId": "tb1",
                            "role": {"value": "textbox"},
                            "name": {"value": "Email"},
                            "value": {"value": "user@example.com"},
                            "backendDOMNodeId": 30,
                        },
                    ]
                }
            return {}

        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        snap = await ctx.snapshot(mode="accessible")
        assert 'value="user@example.com"' in snap.tree_text

    @pytest.mark.asyncio
    async def test_password_redaction(self) -> None:
        """Password fields have their values redacted."""
        cdp = MagicMock()

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Accessibility.getFullAXTree":
                return {
                    "nodes": [
                        _cdp_node(
                            "RootWebArea",
                            "Page",
                            node_id="pr3",
                            child_ids=["pw1"],
                        ),
                        {
                            "nodeId": "pw1",
                            "role": {"value": "textbox"},
                            "name": {"value": "Password"},
                            "value": {"value": "s3cr3t!"},
                            "backendDOMNodeId": 40,
                            "properties": [
                                {
                                    "name": "autocomplete",
                                    "value": {
                                        "type": "string",
                                        "value": "current-password",
                                    },
                                }
                            ],
                        },
                    ]
                }
            return {}

        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        snap = await ctx.snapshot(mode="accessible")
        assert "s3cr3t!" not in snap.tree_text
        assert "••••" in snap.tree_text

    @pytest.mark.asyncio
    async def test_disabled_button(self) -> None:
        """Disabled button shows disabled state."""
        cdp = MagicMock()

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Accessibility.getFullAXTree":
                return {
                    "nodes": [
                        _cdp_node(
                            "RootWebArea",
                            "Page",
                            node_id="pr4",
                            child_ids=["db1"],
                        ),
                        _cdp_node(
                            "button",
                            "Submit",
                            node_id="db1",
                            backend_dom_id=50,
                            properties=[
                                {
                                    "name": "disabled",
                                    "value": {"type": "boolean", "value": True},
                                }
                            ],
                        ),
                    ]
                }
            return {}

        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        snap = await ctx.snapshot(mode="accessible")
        assert "disabled" in snap.tree_text

    @pytest.mark.asyncio
    async def test_expanded_false_shown(self) -> None:
        """expanded=false is meaningful and should be output."""
        cdp = MagicMock()

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Accessibility.getFullAXTree":
                return {
                    "nodes": [
                        _cdp_node(
                            "RootWebArea",
                            "Page",
                            node_id="pr5",
                            child_ids=["mb1"],
                        ),
                        _cdp_node(
                            "button",
                            "Menu",
                            node_id="mb1",
                            backend_dom_id=60,
                            properties=[
                                {
                                    "name": "expanded",
                                    "value": {"type": "boolean", "value": False},
                                },
                                {
                                    "name": "haspopup",
                                    "value": {"type": "string", "value": "menu"},
                                },
                            ],
                        ),
                    ]
                }
            return {}

        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        snap = await ctx.snapshot(mode="accessible")
        assert "expanded=false" in snap.tree_text
        assert "haspopup=menu" in snap.tree_text


class TestSnapshotTree:
    """Tests for R2: tree structure with indentation."""

    @pytest.mark.asyncio
    async def test_indentation_present(self) -> None:
        """Tree output uses 1-space indentation per depth level.

        v0.2.3 cut the indent step from 2 to 1 — the structural information
        is still there (each level is one space deeper than its parent), but
        the leading whitespace stops eating tokens on dense pages.
        """
        cdp = MagicMock()

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Accessibility.getFullAXTree":
                return {
                    "nodes": [
                        _cdp_node(
                            "RootWebArea",
                            "Page",
                            node_id="tr",
                            child_ids=["tnav"],
                        ),
                        _cdp_node(
                            "navigation",
                            "Main",
                            node_id="tnav",
                            child_ids=["tl1"],
                        ),
                        _cdp_node(
                            "link",
                            "Home",
                            node_id="tl1",
                            backend_dom_id=70,
                        ),
                    ]
                }
            return {}

        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        snap = await ctx.snapshot(mode="accessible")
        lines = snap.tree_text.split("\n")
        # RootWebArea at depth 0, navigation at depth 1, link at depth 2
        nav_line = next(ln for ln in lines if "Main" in ln)
        link_line = next(ln for ln in lines if "Home" in ln)
        # nav is child of root, depth 1 → 1 leading space (not 2 anymore)
        assert nav_line.startswith(" ") and not nav_line.startswith("  ")
        # link is child of nav, depth 2 → 2 leading spaces (was 4)
        assert link_line.startswith("  ") and not link_line.startswith("   ")

    @pytest.mark.asyncio
    async def test_generic_fold_single_child(self) -> None:
        """Generic nodes with no name and <= 1 child are folded."""
        cdp = MagicMock()

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Accessibility.getFullAXTree":
                return {
                    "nodes": [
                        _cdp_node(
                            "RootWebArea",
                            "Page",
                            node_id="fr",
                            child_ids=["fgen"],
                        ),
                        _cdp_node(
                            "generic",
                            "",
                            node_id="fgen",
                            child_ids=["fb1"],
                        ),
                        _cdp_node(
                            "button",
                            "Click",
                            node_id="fb1",
                            backend_dom_id=80,
                        ),
                    ]
                }
            return {}

        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        snap = await ctx.snapshot(mode="accessible")
        assert "generic" not in snap.tree_text
        assert "Click" in snap.tree_text

    @pytest.mark.asyncio
    async def test_static_text_dedup(self) -> None:
        """StaticText matching parent name is deduplicated."""
        cdp = MagicMock()

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Accessibility.getFullAXTree":
                return {
                    "nodes": [
                        _cdp_node(
                            "RootWebArea",
                            "Page",
                            node_id="dr",
                            child_ids=["dl1"],
                        ),
                        _cdp_node(
                            "link",
                            "Home",
                            node_id="dl1",
                            backend_dom_id=90,
                            child_ids=["ds1"],
                        ),
                        _cdp_node(
                            "StaticText",
                            "Home",
                            node_id="ds1",
                        ),
                    ]
                }
            return {}

        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        snap = await ctx.snapshot(mode="accessible")
        # "Home" should appear only once (in the link line), not twice
        assert snap.tree_text.count("Home") == 1


class TestProgressiveLoading:
    """Tests for R4/R5: progressive loading features."""

    @pytest.mark.asyncio
    async def test_max_nodes_truncation(self) -> None:
        """max_nodes limits output and shows truncation summary."""
        cdp = MagicMock()

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Accessibility.getFullAXTree":
                children = [f"ml{i}" for i in range(10)]
                nodes = [
                    _cdp_node(
                        "RootWebArea",
                        "Page",
                        node_id="mr",
                        child_ids=children,
                    ),
                ]
                for i in range(10):
                    nodes.append(
                        _cdp_node(
                            "link",
                            f"Link {i}",
                            node_id=f"ml{i}",
                            backend_dom_id=100 + i,
                        )
                    )
                return {"nodes": nodes}
            return {}

        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        snap = await ctx.snapshot(mode="accessible", max_nodes=3)
        assert snap.truncated_at > 0
        assert "not shown" in snap.tree_text
        assert snap.total_interactive == 10

    @pytest.mark.asyncio
    async def test_offset_pagination(self) -> None:
        """offset skips initial elements."""
        cdp = MagicMock()

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Accessibility.getFullAXTree":
                children = [f"ol{i}" for i in range(5)]
                nodes = [
                    _cdp_node(
                        "RootWebArea",
                        "Page",
                        node_id="or",
                        child_ids=children,
                    ),
                ]
                for i in range(5):
                    nodes.append(
                        _cdp_node(
                            "link",
                            f"Link {i}",
                            node_id=f"ol{i}",
                            backend_dom_id=200 + i,
                        )
                    )
                return {"nodes": nodes}
            return {}

        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        snap = await ctx.snapshot(mode="accessible", offset=3)
        assert "Link 0" not in snap.tree_text
        assert "Link 3" in snap.tree_text or "Link 4" in snap.tree_text

    @pytest.mark.asyncio
    async def test_focus_subtree(self) -> None:
        """--focus=N returns subtree around the target ref."""
        cdp = MagicMock()

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Accessibility.getFullAXTree":
                return {
                    "nodes": [
                        _cdp_node(
                            "RootWebArea",
                            "Page",
                            node_id="fcr",
                            child_ids=["fcnav", "fcbtn"],
                        ),
                        _cdp_node(
                            "navigation",
                            "Nav",
                            node_id="fcnav",
                            child_ids=["fcl1", "fcl2"],
                        ),
                        _cdp_node(
                            "link",
                            "Home",
                            node_id="fcl1",
                            backend_dom_id=300,
                        ),
                        _cdp_node(
                            "link",
                            "About",
                            node_id="fcl2",
                            backend_dom_id=301,
                        ),
                        _cdp_node(
                            "button",
                            "Unrelated",
                            node_id="fcbtn",
                            backend_dom_id=302,
                        ),
                    ]
                }
            return {}

        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        # First snapshot to build cache
        await ctx.snapshot(mode="accessible")
        # Focus on ref [1] (first link)
        snap = await ctx.snapshot(mode="accessible", focus=1)
        assert "Home" in snap.tree_text

    @pytest.mark.asyncio
    async def test_action_on_truncated_ref(self) -> None:
        """Agent can action on refs that were not visible in truncated output."""
        cdp = MagicMock()

        async def _send(method: str, params: Any = None) -> Any:
            if method == "Accessibility.getFullAXTree":
                children = [f"atl{i}" for i in range(10)]
                nodes = [
                    _cdp_node(
                        "RootWebArea",
                        "Page",
                        node_id="atr",
                        child_ids=children,
                    ),
                ]
                for i in range(10):
                    nodes.append(
                        _cdp_node(
                            "button",
                            f"Btn {i}",
                            node_id=f"atl{i}",
                            backend_dom_id=400 + i,
                        )
                    )
                return {"nodes": nodes}
            return {}

        cdp.send = AsyncMock(side_effect=_send)
        cdp.detach = AsyncMock()
        page = _default_page()
        page.context.new_cdp_session = AsyncMock(return_value=cdp)
        ctx = _make_ctx(page=page)
        snap = await ctx.snapshot(mode="accessible", max_nodes=3)
        # Ref [10] should exist in selector_map even though truncated
        assert 10 in ctx._selector_map
        assert snap.total_interactive == 10


class TestProperties:
    def test_stealth_tier(self) -> None:
        ctx = _make_ctx()
        assert ctx.stealth_tier.value == "playwright"


class TestPageValid:
    """Tests for `_page_valid` flag — silent navigate failure prevention.

    The flag flips to False on every ``navigate()`` attempt and back to True
    only on success. Page-bound ops (screenshot, evaluate, snapshot, action,
    upload, frame_list/focus, selector/js waits) check the flag and raise
    ``NavigationError(error="no_valid_page")`` so the agent can't silently
    keep operating on the previous page after a failed navigate.
    """

    @pytest.mark.asyncio
    async def test_initial_page_valid_true(self) -> None:
        """Fresh context starts with page_valid=True (so first ops work)."""
        ctx = _make_ctx()
        assert ctx._page_valid is True

    @pytest.mark.asyncio
    async def test_navigate_success_keeps_page_valid(self) -> None:
        """Successful navigate leaves page_valid=True."""
        ctx = _make_ctx()
        await ctx.navigate("https://example.com")
        assert ctx._page_valid is True

    @pytest.mark.asyncio
    async def test_navigate_failure_marks_page_invalid(self) -> None:
        """Failed navigate flips page_valid to False."""
        page = MagicMock()
        page.on = MagicMock()
        page.goto = AsyncMock(side_effect=Exception("net::ERR_NAME_NOT_RESOLVED"))
        ctx = _make_ctx(page=page)
        with pytest.raises(NavigationError):
            await ctx.navigate("https://bad.example.com")
        assert ctx._page_valid is False

    @pytest.mark.asyncio
    async def test_navigate_timeout_marks_page_invalid(self) -> None:
        """Navigate timeout flips page_valid to False (same path as failure)."""
        page = MagicMock()
        page.on = MagicMock()
        page.goto = AsyncMock(side_effect=Exception("Timeout 30000ms exceeded"))
        ctx = _make_ctx(page=page)
        with pytest.raises(BrowserTimeoutError):
            await ctx.navigate("https://slow.example.com")
        assert ctx._page_valid is False

    @pytest.mark.asyncio
    async def test_screenshot_blocks_when_invalid(self) -> None:
        """screenshot() must raise no_valid_page when last navigate failed."""
        ctx = _make_ctx()
        ctx._page_valid = False
        with pytest.raises(NavigationError) as exc_info:
            await ctx.screenshot()
        assert exc_info.value.error == "no_valid_page"
        assert "navigate" in exc_info.value.action.lower()

    @pytest.mark.asyncio
    async def test_evaluate_blocks_when_invalid(self) -> None:
        """evaluate() must raise no_valid_page when last navigate failed."""
        ctx = _make_ctx()
        ctx._page_valid = False
        with pytest.raises(NavigationError) as exc_info:
            await ctx.evaluate("document.title")
        assert exc_info.value.error == "no_valid_page"

    @pytest.mark.asyncio
    async def test_snapshot_blocks_when_invalid(self) -> None:
        """snapshot() must raise no_valid_page when last navigate failed."""
        ctx = _make_ctx()
        ctx._page_valid = False
        with pytest.raises(NavigationError) as exc_info:
            await ctx.snapshot(mode="accessible")
        assert exc_info.value.error == "no_valid_page"

    @pytest.mark.asyncio
    async def test_action_blocks_when_invalid(self) -> None:
        """action() (click/fill/...) must raise no_valid_page when invalid."""
        ctx = _make_ctx()
        ctx._page_valid = False
        with pytest.raises(NavigationError) as exc_info:
            await ctx.action("click", "1")
        assert exc_info.value.error == "no_valid_page"

    @pytest.mark.asyncio
    async def test_successful_navigate_after_failure_recovers(self) -> None:
        """A successful navigate after a failure restores page_valid=True."""
        # Build a page that fails the first goto and succeeds on the second.
        page = _default_page()
        outcomes = [Exception("net::ERR_NAME_NOT_RESOLVED"), MagicMock(status=200)]

        async def _goto(url: str, **kw: Any) -> Any:
            value = outcomes.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        page.goto = AsyncMock(side_effect=_goto)
        ctx = _make_ctx(page=page)

        with pytest.raises(NavigationError):
            await ctx.navigate("https://bad.example.com")
        assert ctx._page_valid is False

        # Page-bound ops are now blocked.
        with pytest.raises(NavigationError):
            await ctx.screenshot()

        # Successful retry restores the flag and unblocks downstream ops.
        await ctx.navigate("https://good.example.com")
        assert ctx._page_valid is True
        # Screenshot should now work.
        result = await ctx.screenshot()
        assert isinstance(result, bytes)

    @pytest.mark.asyncio
    async def test_fetch_unaffected_by_invalid_page(self) -> None:
        """fetch() is HTTP — page_valid flag must not block it."""
        ctx = _make_ctx()
        ctx._page_valid = False
        # The default _fetch_impl on PlaywrightContext uses APIRequestContext
        # — patch it out to verify the flag itself doesn't gate the call.
        ctx._fetch_impl = AsyncMock(  # type: ignore[method-assign]
            return_value={"status": 200, "body": "ok", "headers": {}}
        )
        result = await ctx.fetch("https://api.example.com/data")
        assert result["status"] == 200

    @pytest.mark.asyncio
    async def test_network_unaffected_by_invalid_page(self) -> None:
        """network() reads the ring buffer — page_valid flag must not block it."""
        ctx = _make_ctx()
        ctx._page_valid = False
        # Should not raise — ring buffer is independent of page state.
        entries = await ctx.network(since=0)
        assert entries == []

    @pytest.mark.asyncio
    async def test_wait_ms_unaffected_by_invalid_page(self) -> None:
        """wait --ms is a pure timer — must work even with invalid page."""
        ctx = _make_ctx()
        ctx._page_valid = False
        ctx._wait_impl = AsyncMock(  # type: ignore[method-assign]
            return_value={"condition": "ms", "value": "10"}
        )
        result = await ctx.wait(condition="ms", value="10", timeout=1000)
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_wait_selector_blocks_when_invalid(self) -> None:
        """wait --selector evaluates against the live page — must be guarded."""
        ctx = _make_ctx()
        ctx._page_valid = False
        with pytest.raises(NavigationError) as exc_info:
            await ctx.wait(condition="selector", value="#x", timeout=100)
        assert exc_info.value.error == "no_valid_page"

    @pytest.mark.asyncio
    async def test_resume_snapshot_exposes_page_valid(self) -> None:
        """resume_snapshot() returns page_valid for agent introspection."""
        ctx = _make_ctx()
        data = await ctx.resume_snapshot()
        assert data["page_valid"] is True

        ctx._page_valid = False
        data = await ctx.resume_snapshot()
        assert data["page_valid"] is False

    @pytest.mark.asyncio
    async def test_mark_page_invalid_public_hook(self) -> None:
        """``mark_page_invalid()`` is the public seam outer wrappers call.

        ``SecureBrowserContext`` uses this when its pre-flight rejects a
        navigate before the inner backend ever sees it; covered here at the
        base level so the contract is explicit and pyright doesn't see
        outside callers poking at the private flag directly.
        """
        ctx = _make_ctx()
        assert ctx._page_valid is True
        ctx.mark_page_invalid()
        assert ctx._page_valid is False
        # Subsequent page-bound ops must now refuse to run.
        with pytest.raises(NavigationError) as exc_info:
            await ctx.screenshot()
        assert exc_info.value.error == "no_valid_page"
