"""RemoteBridgeContext — operates a remote browser via bridge WebSocket.

This adapter speaks WebSocket+CDP. All shared behavior (action dispatch,
snapshot caching, dialog handling, batch, wait orchestration, frame state) is
inherited from BrowserContextBase. The atomic methods translate each
operation into the appropriate ``cdp``/``evaluate``/``screenshot`` bridge
command.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, cast

import structlog

from agentcloak.browser._snapshot_builder import FrameData
from agentcloak.browser.base import (
    BrowserContextBase,
    classify_url_pattern,
    match_url_glob,
    match_url_substring,
)
from agentcloak.browser.state import (
    DownloadEntry,
    FrameInfo,
    PageSnapshot,
    TabInfo,
)
from agentcloak.core.capture import is_recordable_content
from agentcloak.core.errors import (
    BackendError,
    BrowserTimeoutError,
    ElementNotFoundError,
)
from agentcloak.core.types import StealthTier

if TYPE_CHECKING:
    from agentcloak.core.config import BrowserConfig

__all__ = ["RemoteBridgeContext"]

logger = structlog.get_logger()

# Paper sizes in inches for CDP Page.printToPDF (which wants explicit
# paperWidth/paperHeight rather than the named formats Playwright accepts).
_PAPER_SIZES_IN: dict[str, tuple[float, float]] = {
    "a4": (8.27, 11.69),
    "letter": (8.5, 11.0),
    "legal": (8.5, 14.0),
    "a3": (11.69, 16.54),
}


def _pdf_options_to_cdp(options: dict[str, Any]) -> dict[str, Any]:
    """Translate Playwright-style PDF options into CDP printToPDF params.

    Mirrors the option vocabulary the daemon route exposes (format, landscape,
    scale, margin, pageRanges) so both backends accept the same request shape.
    """
    params: dict[str, Any] = {"printBackground": True}
    fmt = str(options.get("format", "A4")).lower()
    width, height = _PAPER_SIZES_IN.get(fmt, _PAPER_SIZES_IN["a4"])
    params["paperWidth"] = width
    params["paperHeight"] = height
    if options.get("landscape"):
        params["landscape"] = True
    if options.get("scale") is not None:
        params["scale"] = float(options["scale"])
    if options.get("pageRanges"):
        params["pageRanges"] = str(options["pageRanges"])
    margin = options.get("margin")
    if isinstance(margin, dict):
        margin_obj = cast("dict[str, Any]", margin)
        # Playwright margins are CSS strings ("1cm"); CDP wants inches. Only a
        # plain inch float is passed straight through; anything else is left to
        # the CDP defaults to avoid mis-converting units.
        for side, key in (
            ("top", "marginTop"),
            ("bottom", "marginBottom"),
            ("left", "marginLeft"),
            ("right", "marginRight"),
        ):
            val = margin_obj.get(side)
            if isinstance(val, int | float):
                params[key] = float(val)
    return params


def _fulfill_params(request_id: str, rule: Any) -> dict[str, Any]:
    """Build CDP ``Fetch.fulfillRequest`` params from a fulfill rule.

    CDP wants the body base64-encoded and the content type expressed as a
    response header, unlike Playwright's named ``content_type``/``body`` args —
    this translates the shared RouteRule shape into the bridge dialect.
    """
    params: dict[str, Any] = {
        "requestId": request_id,
        "responseCode": rule.status or 200,
    }
    headers: list[dict[str, str]] = []
    if rule.content_type:
        headers.append({"name": "Content-Type", "value": str(rule.content_type)})
    if headers:
        params["responseHeaders"] = headers
    if rule.body is not None:
        params["body"] = base64.b64encode(str(rule.body).encode()).decode()
    return params


class _BridgeWS(Protocol):
    """The minimal WebSocket interface the bridge context speaks to.

    Implemented by :class:`agentcloak.daemon.routes._BridgeWSAdapter`, which
    wraps a Starlette/FastAPI ``WebSocket`` object. Defined as a Protocol so
    the browser layer never imports a specific server framework — the daemon
    can swap transports without rippling into ``remote_ctx``.
    """

    @property
    def closed(self) -> bool: ...

    async def send_str(self, data: str) -> None: ...

    async def close(self) -> None: ...


class RemoteBridgeContext(BrowserContextBase):
    """BrowserContext backed by a remote Chrome via bridge WebSocket."""

    def __init__(
        self,
        *,
        bridge_ws: _BridgeWS,
        browser_config: BrowserConfig | None = None,
    ) -> None:
        super().__init__(browser_config=browser_config)
        self._ws = bridge_ws
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # _active_frame is set to a frameId string (or None for main) on this
        # backend. The base class declares the slot but stores Any.
        self._active_frame_id: str | None = None
        # Network capture state — populated from CDP ``Network.*`` events the
        # Extension forwards after ``Network.enable`` is sent. The dict keys
        # by CDP requestId so requestWillBeSent / responseReceived /
        # loadingFinished can stitch a single entry together. ``_capture_tasks``
        # tracks the background tasks fetching response bodies so they aren't
        # garbage-collected mid-flight.
        self._pending_captures: dict[str, dict[str, Any]] = {}
        self._capture_tasks: set[asyncio.Task[None]] = set()

        # 7b T1.3: route interception state. ``_fetch_enabled`` guards the
        # one-time ``Fetch.requestPaused`` registration; ``_route_tasks`` holds
        # the background coroutines that resume each paused request (the CDP
        # event callback must stay synchronous, so the actual continue/fulfill/
        # fail is dispatched to a task).
        self._fetch_enabled: bool = False
        self._route_tasks: set[asyncio.Task[None]] = set()

    @property
    def stealth_tier(self) -> StealthTier:
        return StealthTier.REMOTE_BRIDGE

    def browser_description(self) -> str:
        """Bridge backend — actual Chrome lives on the user's machine.

        We don't probe the remote Chrome version here; the bridge handshake
        doesn't currently surface it, and most agents only need to know
        "this is the user's real browser" anyway. A future enhancement could
        carry ``chrome.version`` through the bridge handshake.
        """
        return "Bridge (Chrome)"

    # ------------------------------------------------------------------
    # Bridge plumbing — send command + read response
    # ------------------------------------------------------------------

    async def send_command(
        self, cmd: str, params: dict[str, Any] | None = None, **kw: Any
    ) -> dict[str, Any]:
        return await self._send(cmd, params, **kw)

    async def _send(
        self, cmd: str, params: dict[str, Any] | None = None, **kw: Any
    ) -> dict[str, Any]:
        if self._ws.closed:
            raise BackendError(
                error="bridge_disconnected",
                hint="Bridge WebSocket is closed",
                action="check bridge process on the remote machine",
            )

        msg_id = str(uuid.uuid4())[:8]
        message: dict[str, Any] = {"id": msg_id, "cmd": cmd}
        if params:
            message["params"] = params
        message.update(kw)

        await self._ws.send_str(json.dumps(message))

        try:
            response = await asyncio.wait_for(self._wait_response(msg_id), timeout=60.0)
        except TimeoutError as exc:
            raise BrowserTimeoutError(
                error="bridge_timeout",
                hint=f"Bridge command '{cmd}' timed out after 60s",
                action="check bridge and extension connectivity",
            ) from exc

        if not response.get("ok"):
            raise BackendError(
                error="bridge_command_failed",
                hint=response.get("error", "unknown error"),
                action=f"check command '{cmd}' parameters",
            )

        return response.get("data", {})

    async def _wait_response(self, msg_id: str) -> dict[str, Any]:
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        try:
            return await fut
        finally:
            self._pending.pop(msg_id, None)

    def feed_message(self, data: str) -> None:
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            return

        if msg.get("type") == "cdp_event":
            method = msg.get("method", "")
            params = msg.get("params", {})
            if method == "Page.javascriptDialogOpening":
                self._handle_dialog_event(params)
            elif method == "Runtime.consoleAPICalled":
                self._handle_console_event(params)
            elif method == "Runtime.exceptionThrown":
                self._handle_exception_event(params)
            elif method == "Page.downloadWillBegin":
                self._handle_download_begin(params)
            elif method.startswith("Network.") and self._capture_store.recording:
                # Only build capture entries while recording — dropping events
                # otherwise saves memory on busy pages.
                self._handle_network_event(method, params)
            # 7b: fan the same event out to any reverse-engineering manager
            # that registered via ``_on_cdp_event``. This runs unconditionally
            # — outside the ``recording`` guard above — because managers like
            # StreamingMonitor also want ``Network.*`` events (webSocketFrame*,
            # eventSourceMessageReceived) even when capture is off. The legacy
            # handlers above and the manager dispatch are independent consumers
            # of the same event.
            self._dispatch_cdp_event(method, params)
            return

        if msg.get("type") == "tab_event":
            # The extension fires these so we don't have to poll. Right now
            # the only state we care about is informational logging — the
            # extension already owns activeTabId tracking; the daemon side
            # mostly needs to know "the user reclaimed control" eventually,
            # which is out of scope for this phase.
            event = msg.get("event")
            tab_id = msg.get("tabId")
            if event == "removed":
                logger.debug("ext_tab_removed", tab_id=tab_id)
            elif event == "updated":
                logger.debug(
                    "ext_tab_updated",
                    tab_id=tab_id,
                    url=msg.get("url"),
                )
            return

        msg_id = msg.get("id")
        if msg_id and msg_id in self._pending:
            self._pending[msg_id].set_result(msg)

    # ------------------------------------------------------------------
    # Atomic: navigation + page info
    # ------------------------------------------------------------------

    async def _navigate_impl(self, url: str, *, timeout: float) -> dict[str, Any]:
        return await self._send("navigate", {"url": url})

    async def _get_page_info(self) -> tuple[str, str]:
        # Use the dedicated page_info bridge command instead of evaluate.
        # snapshot() calls this on every invocation, so making it depend
        # on evaluate (and thus on CSP, on async-promise plumbing, on
        # structured-clone serialization, etc.) was the single largest
        # source of "snapshot has no URL" bugs in the v0.2.0 dogfood.
        # chrome.tabs.get on the extension side is the canonical source
        # and works even while the page is mid-navigation.
        try:
            result = await self._send("page_info", {})
        except BackendError:
            # Extension hasn't been updated yet — degrade rather than crash.
            return "", ""
        return str(result.get("url", "")), str(result.get("title", ""))

    async def resume_snapshot(self) -> dict[str, Any]:
        """Resume data sourced from the extension's tab inventory.

        Falls back to empty fields if the extension is mid-reconnect — the
        daemon should not crash because a single tab probe failed.
        """
        data = await super().resume_snapshot()
        try:
            url, title = await self._get_page_info()
            data["url"] = url
            data["title"] = title
        except Exception:
            logger.debug("resume_page_info_failed", exc_info=True)

        try:
            tabs = await self._tab_list_impl()
            data["tabs"] = [{"tab_id": t.tab_id, "url": t.url} for t in tabs]
        except Exception:
            logger.debug("resume_tab_list_failed", exc_info=True)
        return data

    # ------------------------------------------------------------------
    # Atomic: AX tree + DOM/content snapshots
    # ------------------------------------------------------------------

    async def _get_ax_tree(self, *, frames: bool = False) -> list[dict[str, Any]]:
        cdp_result = await self._send(
            "cdp",
            {"method": "Accessibility.getFullAXTree", "params": {"pierce": True}},
        )
        return cdp_result.get("nodes", [])

    async def _get_child_frame_trees(self) -> list[FrameData]:
        child_frames: list[FrameData] = []
        try:
            result = await self._send(
                "cdp",
                {"method": "Page.getFrameTree", "params": {}},
            )
        except BackendError:
            return child_frames

        frame_tree = result.get("frameTree", {})
        for child in frame_tree.get("childFrames", []):
            frame_info = child.get("frame", {})
            frame_id = frame_info.get("id", "")
            frame_name = frame_info.get("name", "")
            frame_url = frame_info.get("url", "")
            if not frame_id:
                continue
            try:
                ax_result = await self._send(
                    "cdp",
                    {
                        "method": "Accessibility.getFullAXTree",
                        "params": {"frameId": frame_id},
                    },
                )
                nodes = ax_result.get("nodes", [])
                if nodes:
                    child_frames.append(
                        FrameData(
                            frame_id=frame_id,
                            name=frame_name,
                            url=frame_url,
                            nodes=nodes,
                        )
                    )
            except Exception:
                logger.debug(
                    "frame_ax_tree_failed",
                    frame_id=frame_id,
                    frame_name=frame_name,
                    exc_info=True,
                )
        return child_frames

    async def _snapshot_dom_impl(self) -> str:
        # Remote bridge currently doesn't ship a full-HTML snapshot — fall back
        # to raising invalid_snapshot_mode so callers can degrade to text or
        # accessible mode (historical behavior preserved across the FastAPI
        # rewrite).
        raise BackendError(
            error="invalid_snapshot_mode",
            hint="DOM snapshot not supported on the remote bridge backend",
            action="use accessible, compact, or content",
        )

    async def _snapshot_content_impl(self) -> str:
        text_result = await self._send(
            "evaluate", {"js": "document.body?.innerText || ''"}
        )
        return str(text_result.get("result", ""))

    async def _network_entries(self, *, since_seq: int) -> list[dict[str, Any]]:
        # Remote bridge doesn't surface a dedicated network queue today; the
        # ring buffer (populated via CDP events when added) is the source.
        return []

    async def snapshot(
        self,
        *,
        mode: str = "compact",
        max_nodes: int = 0,
        max_chars: int = 0,
        focus: int = 0,
        offset: int = 0,
        frames: bool = False,
    ) -> PageSnapshot:
        # Remote bridge only supports accessible / compact / content modes.
        if mode == "dom":
            raise BackendError(
                error="invalid_snapshot_mode",
                hint=f"Unknown mode: {mode}",
                action="use one of: accessible, compact, content",
            )
        return await super().snapshot(
            mode=mode,
            max_nodes=max_nodes,
            max_chars=max_chars,
            focus=focus,
            offset=offset,
            frames=frames,
        )

    # ------------------------------------------------------------------
    # Element resolution via backendDOMNodeId
    # ------------------------------------------------------------------

    async def _resolve_element_center(self, ref: int) -> tuple[float, float]:
        """Resolve [N] ref to element center coordinates via backendDOMNodeId."""
        self._require_snapshot(ref)
        backend_id = self._backend_node_map.get(ref)
        if backend_id is None:
            raise BackendError(
                error="element_not_found",
                hint=f"Ref [{ref}] not in current snapshot",
                action="re-snapshot and use a valid [N] ref",
            )
        desc = await self._send(
            "cdp",
            {
                "method": "DOM.describeNode",
                "params": {"backendNodeId": backend_id},
            },
        )
        node_id = desc.get("node", {}).get("nodeId", 0)
        if not node_id:
            resolve_result = await self._send(
                "cdp",
                {
                    "method": "DOM.resolveNode",
                    "params": {"backendNodeId": backend_id},
                },
            )
            object_id = resolve_result.get("object", {}).get("objectId")
            if not object_id:
                raise BackendError(
                    error="element_not_resolved",
                    hint=(
                        f"Could not resolve backendNodeId {backend_id} for ref [{ref}]"
                    ),
                    action=(
                        "re-snapshot and retry — the element may have been removed"
                    ),
                )
            box = await self._send(
                "cdp",
                {
                    "method": "Runtime.callFunctionOn",
                    "params": {
                        "objectId": object_id,
                        "functionDeclaration": (
                            "function(){"
                            "const r=this.getBoundingClientRect();"
                            "return JSON.stringify("
                            "{x:r.x,y:r.y,w:r.width,h:r.height})}"
                        ),
                        "returnByValue": True,
                    },
                },
            )
            rect = json.loads(box.get("result", {}).get("value", "{}"))
            cx = rect.get("x", 0) + rect.get("w", 0) / 2
            cy = rect.get("y", 0) + rect.get("h", 0) / 2
            return float(cx), float(cy)
        box_model = await self._send(
            "cdp",
            {"method": "DOM.getBoxModel", "params": {"nodeId": node_id}},
        )
        content = box_model.get("model", {}).get("content", [0] * 8)
        cx = (content[0] + content[4]) / 2
        cy = (content[1] + content[5]) / 2
        return float(cx), float(cy)

    async def _dispatch_click(self, x: float, y: float) -> None:
        """Send mousePressed + mouseReleased via CDP at the given coordinates."""
        for event_type in ("mousePressed", "mouseReleased"):
            await self._send(
                "cdp",
                {
                    "method": "Input.dispatchMouseEvent",
                    "params": {
                        "type": event_type,
                        "x": x,
                        "y": y,
                        "button": "left",
                        "clickCount": 1,
                    },
                },
            )

    # ------------------------------------------------------------------
    # Atomic: actions
    # ------------------------------------------------------------------

    async def _click_impl(
        self,
        *,
        target: str,
        x: float | None,
        y: float | None,
        button: str,
        click_count: int,
        force: bool = False,
    ) -> dict[str, Any]:
        if x is not None and y is not None:
            cx, cy = float(x), float(y)
        else:
            cx, cy = await self._resolve_element_center(int(target))
        await self._dispatch_click(cx, cy)
        return {"clicked": True}

    async def _fill_impl(self, *, target: str, text: str) -> dict[str, Any]:
        if target:
            cx, cy = await self._resolve_element_center(int(target))
            await self._dispatch_click(cx, cy)
        await self._set_active_value(text)
        return {"filled": True, "text": text}

    async def _type_impl(
        self, *, target: str, text: str, delay: float
    ) -> dict[str, Any]:
        if target:
            cx, cy = await self._resolve_element_center(int(target))
            await self._dispatch_click(cx, cy)
        await self._set_active_value(text)
        return {"typed": True, "text": text}

    async def _set_active_value(self, text: str) -> None:
        val = json.dumps(text)
        js = (
            f"(() => {{"
            f" const el = document.activeElement;"
            f" if (el) {{"
            f" el.value = {val};"
            f" el.dispatchEvent(new Event('input',"
            f" {{bubbles:true}}));"
            f" el.dispatchEvent(new Event('change',"
            f" {{bubbles:true}}));"
            f" }}"
            f"}})()"
        )
        await self._send("evaluate", {"js": js})

    async def _scroll_impl(
        self,
        *,
        target: str,
        direction: str,
        amount: int,
    ) -> dict[str, Any]:
        delta_x, delta_y = 0, 0
        if direction == "down":
            delta_y = amount
        elif direction == "up":
            delta_y = -amount
        elif direction == "right":
            delta_x = amount
        elif direction == "left":
            delta_x = -amount
        if target:
            cx, cy = await self._resolve_element_center(int(target))
        else:
            cx, cy = 640.0, 400.0
        await self._send(
            "cdp",
            {
                "method": "Input.dispatchMouseEvent",
                "params": {
                    "type": "mouseWheel",
                    "x": cx,
                    "y": cy,
                    "deltaX": delta_x,
                    "deltaY": delta_y,
                },
            },
        )
        return {"scrolled": True, "direction": direction, "amount": amount}

    async def _hover_impl(
        self,
        *,
        target: str,
        x: float | None,
        y: float | None,
    ) -> dict[str, Any]:
        if x is not None and y is not None:
            cx, cy = float(x), float(y)
        else:
            if not target:
                raise BackendError(
                    error="element_not_found",
                    hint="hover requires a target element",
                    action=(
                        "provide 'target' as '[N]' ref from snapshot,"
                        " or use (x, y) coordinates"
                    ),
                )
            cx, cy = await self._resolve_element_center(int(target))
        await self._send(
            "cdp",
            {
                "method": "Input.dispatchMouseEvent",
                "params": {"type": "mouseMoved", "x": cx, "y": cy},
            },
        )
        return {"hovered": True}

    async def _select_impl(
        self,
        *,
        target: str,
        value: str | None,
        label: str | None,
    ) -> dict[str, Any]:
        if not target:
            raise BackendError(
                error="element_not_found",
                hint="select requires a target element",
                action="provide 'target' as '[N]' ref from snapshot",
            )
        self._require_snapshot(int(target))
        backend_id = self._backend_node_map.get(int(target))
        if backend_id is None:
            raise BackendError(
                error="element_not_found",
                hint=f"Ref [{target}] not in current snapshot",
                action="re-snapshot and use a valid [N] ref",
            )
        resolve_result = await self._send(
            "cdp",
            {
                "method": "DOM.resolveNode",
                "params": {"backendNodeId": backend_id},
            },
        )
        object_id = resolve_result.get("object", {}).get("objectId")
        if not object_id:
            raise BackendError(
                error="element_not_resolved",
                hint=(
                    f"Could not resolve backendNodeId {backend_id} for ref [{target}]"
                ),
                action="re-snapshot and retry — the element may have been removed",
            )
        if value is not None:
            set_js = (
                "function() {"
                f"  this.value = {json.dumps(value)};"
                "  this.dispatchEvent(new Event('input', {bubbles:true}));"
                "  this.dispatchEvent(new Event('change', {bubbles:true}));"
                "}"
            )
        else:
            set_js = (
                "function() {"
                "  const opts = Array.from(this.options);"
                "  const opt = opts.find("
                f"o => o.text === {json.dumps(label)});"
                "  if (opt) { this.value = opt.value; }"
                "  this.dispatchEvent(new Event('input', {bubbles:true}));"
                "  this.dispatchEvent(new Event('change', {bubbles:true}));"
                "}"
            )
        await self._send(
            "cdp",
            {
                "method": "Runtime.callFunctionOn",
                "params": {
                    "objectId": object_id,
                    "functionDeclaration": set_js,
                    "returnByValue": True,
                },
            },
        )
        return {"selected": True, "value": value, "label": label}

    async def _press_impl(self, *, target: str, key: str) -> dict[str, Any]:
        # Note: target is intentionally ignored on remote bridge — the CDP key
        # event dispatches at the focused element. Callers wanting to focus
        # first should issue a click or fill on the target before pressing.
        await self._send(
            "cdp",
            {
                "method": "Input.dispatchKeyEvent",
                "params": {"type": "keyDown", "key": key},
            },
        )
        await self._send(
            "cdp",
            {
                "method": "Input.dispatchKeyEvent",
                "params": {"type": "keyUp", "key": key},
            },
        )
        return {"pressed": True, "key": key}

    async def _keydown_impl(self, *, key: str) -> dict[str, Any]:
        await self._send(
            "cdp",
            {
                "method": "Input.dispatchKeyEvent",
                "params": {"type": "keyDown", "key": key},
            },
        )
        return {"keydown": True, "key": key}

    async def _keyup_impl(self, *, key: str) -> dict[str, Any]:
        await self._send(
            "cdp",
            {
                "method": "Input.dispatchKeyEvent",
                "params": {"type": "keyUp", "key": key},
            },
        )
        return {"keyup": True, "key": key}

    # ------------------------------------------------------------------
    # Atomic: wait (polling CDP loop)
    # ------------------------------------------------------------------

    async def _wait_impl(
        self,
        *,
        condition: str,
        value: str,
        timeout: int,
        state: str,
    ) -> dict[str, Any]:
        t0 = time.monotonic()
        deadline = t0 + timeout / 1000

        if condition == "ms":
            await asyncio.sleep(int(value) / 1000)
            return {}

        if condition == "selector":
            state_check = ""
            if state == "visible":
                state_check = (
                    " && el.offsetParent !== null"
                    " && getComputedStyle(el).visibility !== 'hidden'"
                )
            elif state == "hidden":
                state_check = (
                    " && (el.offsetParent === null"
                    " || getComputedStyle(el).visibility === 'hidden')"
                )
            elif state == "attached":
                state_check = ""
            elif state == "detached":
                await self._poll_js(
                    f"!document.querySelector({json.dumps(value)})",
                    deadline,
                    condition,
                    timeout,
                )
                return {}

            js_expr = (
                f"(() => {{"
                f"  const el = document.querySelector({json.dumps(value)});"
                f"  return !!(el{state_check});"
                f"}})()"
            )
            await self._poll_js(js_expr, deadline, condition, timeout)

        elif condition == "url":
            kind, processed = classify_url_pattern(value)

            def _matches(current_url: str) -> bool:
                if kind == "glob":
                    return match_url_glob(processed, current_url)
                return processed in current_url

            while True:
                url, _ = await self._get_page_info()
                if _matches(url):
                    break
                if time.monotonic() >= deadline:
                    raise BrowserTimeoutError(
                        error="wait_timeout",
                        hint=f"Wait condition 'url' timed out after {timeout}ms",
                        action="increase timeout or check the URL pattern",
                    )
                await asyncio.sleep(0.25)

        elif condition == "load":
            js_expr = (
                "document.readyState === 'complete'"
                if value != "domcontentloaded"
                else "document.readyState !== 'loading'"
            )
            await self._poll_js(js_expr, deadline, condition, timeout)

        elif condition == "js":
            await self._poll_js(value, deadline, condition, timeout)

        else:
            raise BackendError(
                error="invalid_wait_condition",
                hint=f"Unknown condition: '{condition}'",
                action="use one of: selector, url, load, js, ms",
            )

        return {}

    async def _poll_js(
        self,
        expression: str,
        deadline: float,
        condition: str,
        timeout: int,
    ) -> None:
        while True:
            if time.monotonic() >= deadline:
                raise BrowserTimeoutError(
                    error="wait_timeout",
                    hint=f"Wait condition '{condition}' timed out after {timeout}ms",
                    action="increase timeout or check the condition",
                )
            try:
                result = await self._send("evaluate", {"js": expression})
                if result.get("result"):
                    return
            except BackendError:
                raise
            except Exception:
                pass
            await asyncio.sleep(0.25)

    # ------------------------------------------------------------------
    # Atomic: upload
    # ------------------------------------------------------------------

    async def _upload_impl(self, index: int, files: list[str]) -> dict[str, Any]:
        self._require_snapshot(index)
        backend_id = self._backend_node_map.get(index)
        if backend_id is None:
            raise BackendError(
                error="element_not_found",
                hint=f"Ref [{index}] not in current snapshot",
                action="re-snapshot and use a valid [N] ref for a file input",
            )
        await self._send(
            "cdp",
            {
                "method": "DOM.setFileInputFiles",
                "params": {
                    "files": files,
                    "backendNodeId": backend_id,
                },
            },
        )
        return {"uploaded": True}

    async def _upload_auto_find_impl(
        self, files: list[str], *, nth: int
    ) -> dict[str, Any]:
        # CDP DOM.querySelectorAll surfaces hidden inputs (display:none stays in
        # the DOM), so drag-drop uploaders without a visible file input still
        # resolve. getDocument first to get the root nodeId to query under.
        doc = await self._send("cdp", {"method": "DOM.getDocument", "params": {}})
        root_id = doc.get("root", {}).get("nodeId", 0)
        if not root_id:
            raise BackendError(
                error="upload_failed",
                hint="Could not read the document root via CDP",
                action="reload the page and retry",
            )
        result = await self._send(
            "cdp",
            {
                "method": "DOM.querySelectorAll",
                "params": {"nodeId": root_id, "selector": 'input[type="file"]'},
            },
        )
        node_ids: list[int] = list(result.get("nodeIds", []))
        count = len(node_ids)
        if count == 0:
            raise ElementNotFoundError(
                error="no_file_input_found",
                hint="No <input type=file> elements found on the page",
                action="check the page has a file input, or pass --index for a"
                " specific element",
            )
        if nth < 0 or nth >= count:
            raise ElementNotFoundError(
                error="file_input_index_out_of_range",
                hint=f"--nth {nth} out of range ({count} file input(s) found)",
                action=f"use --nth between 0 and {count - 1}",
            )
        await self._send(
            "cdp",
            {
                "method": "DOM.setFileInputFiles",
                "params": {"files": files, "nodeId": node_ids[nth]},
            },
        )
        return {"uploaded": True, "candidates_count": count, "used_nth": nth}

    # ------------------------------------------------------------------
    # Atomic: console capture (7a R1)
    # ------------------------------------------------------------------

    # CDP console types map onto the level vocabulary agents filter by.
    _CONSOLE_TYPE_MAP: ClassVar[dict[str, str]] = {"warning": "warn"}

    async def _console_setup_impl(self) -> None:
        # The Runtime domain must be enabled before consoleAPICalled /
        # exceptionThrown events flow. Unlike the Playwright backend (which
        # registers listeners for free), remote console capture is opt-in so
        # we don't spam the bridge with Runtime traffic until asked.
        try:
            await self._send("cdp", {"method": "Runtime.enable", "params": {}})
        except Exception:
            logger.warning("runtime_enable_failed", exc_info=True)

    def _handle_console_event(self, params: dict[str, Any]) -> None:
        raw_type = str(params.get("type", "log"))
        args = params.get("args", [])
        parts: list[str] = []
        if isinstance(args, list):
            arg_list = cast("list[Any]", args)
            for arg in arg_list:
                if not isinstance(arg, dict):
                    continue
                arg_obj = cast("dict[str, Any]", arg)
                if "value" in arg_obj:
                    parts.append(str(arg_obj.get("value")))
                elif arg_obj.get("description"):
                    parts.append(str(arg_obj.get("description")))
                elif arg_obj.get("unserializableValue"):
                    parts.append(str(arg_obj.get("unserializableValue")))
        text = " ".join(parts)
        url, line, column = self._console_location(params.get("stackTrace"))
        self._record_console_entry(
            level=self._CONSOLE_TYPE_MAP.get(raw_type, raw_type),
            text=text,
            url=url,
            line=line,
            column=column,
            is_error=False,
        )

    def _handle_exception_event(self, params: dict[str, Any]) -> None:
        raw_details = params.get("exceptionDetails", {})
        if not isinstance(raw_details, dict):
            return
        details = cast("dict[str, Any]", raw_details)
        exception = details.get("exception", {})
        text = ""
        if isinstance(exception, dict):
            exc_obj = cast("dict[str, Any]", exception)
            text = str(exc_obj.get("description") or exc_obj.get("value") or "")
        if not text:
            text = str(details.get("text", "Uncaught exception"))
        self._record_console_entry(
            level="error",
            text=text,
            url=str(details.get("url", "")),
            line=details.get("lineNumber"),
            column=details.get("columnNumber"),
            is_error=True,
        )

    @staticmethod
    def _console_location(
        stack_trace: Any,
    ) -> tuple[str, int | None, int | None]:
        """Pull (url, line, column) from a CDP stackTrace's top frame."""
        if not isinstance(stack_trace, dict):
            return "", None, None
        trace_obj = cast("dict[str, Any]", stack_trace)
        frames = trace_obj.get("callFrames", [])
        if isinstance(frames, list) and frames:
            top = cast("list[Any]", frames)[0]
            if isinstance(top, dict):
                top_obj = cast("dict[str, Any]", top)
                return (
                    str(top_obj.get("url", "")),
                    top_obj.get("lineNumber"),
                    top_obj.get("columnNumber"),
                )
        return "", None, None

    # ------------------------------------------------------------------
    # Atomic: download (7a R2)
    # ------------------------------------------------------------------

    def _handle_download_begin(self, params: dict[str, Any]) -> None:
        """Hand a click-triggered download's metadata to a parked waiter."""
        self._resolve_download_waiter(
            {
                "url": str(params.get("url", "")),
                "suggested_filename": str(params.get("suggestedFilename", "")),
                "guid": str(params.get("guid", "")),
            }
        )

    async def _bridge_cookie_jar(self) -> Any:
        """Build an httpx cookie jar from the remote browser's cookies."""
        import httpx

        jar = httpx.Cookies()
        with contextlib.suppress(Exception):
            raw = await self._send("cookies", {})
            cookies: list[dict[str, Any]] = cast(
                "list[dict[str, Any]]", raw if isinstance(raw, list) else []
            )
            for c in cookies:
                if c.get("name"):
                    jar.set(
                        str(c.get("name")),
                        str(c.get("value", "")),
                        domain=str(c.get("domain", "")),
                        path=str(c.get("path", "/")),
                    )
        return jar

    async def _download_url_impl(self, url: str, output_dir: str) -> DownloadEntry:
        # Direct-URL download works even on the remote backend: the daemon
        # fetches the URL itself, carrying the remote browser's cookies so an
        # authenticated resource still resolves. The file lands on the daemon
        # host, which is what an agent driving the CLI expects.
        import httpx

        # Shared filename-derivation helper lives in the Playwright module; both
        # backends run the identical direct-URL download path. Reusing it keeps
        # Content-Disposition parsing consistent across backends.
        from agentcloak.browser.playwright_ctx import (
            _download_filename,  # pyright: ignore[reportPrivateUsage]
        )

        jar = await self._bridge_cookie_jar()
        out_dir = Path(output_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            async with httpx.AsyncClient(
                cookies=jar, timeout=httpx.Timeout(60.0), follow_redirects=True
            ) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    filename = _download_filename(url, resp.headers)
                    dest = out_dir / filename
                    size = 0
                    with dest.open("wb") as fh:
                        async for chunk in resp.aiter_bytes():
                            fh.write(chunk)
                            size += len(chunk)
        except httpx.HTTPStatusError as exc:
            raise BackendError(
                error="download_failed",
                hint=f"Download of {url} returned HTTP {exc.response.status_code}",
                action="check the URL is reachable with the current session",
            ) from exc
        except httpx.RequestError as exc:
            raise BackendError(
                error="download_failed",
                hint=f"Download of {url} failed: {exc}",
                action="check URL and network connectivity",
            ) from exc

        return DownloadEntry(
            filename=dest.name,
            path=str(dest.resolve()),
            size=size,
            url=url,
            source="url",
        )

    async def _download_wait_impl(
        self,
        output_dir: str,
        *,
        timeout: float,
        _waiter: asyncio.Future[Any] | None = None,
    ) -> DownloadEntry:
        with contextlib.suppress(Exception):
            await self._send(
                "cdp",
                {
                    "method": "Page.setDownloadBehavior",
                    "params": {"behavior": "allow", "downloadPath": output_dir},
                },
            )

        if _waiter is None:
            loop = asyncio.get_running_loop()
            _waiter = loop.create_future()
            self._download_waiters.append(_waiter)
        try:
            meta = await asyncio.wait_for(_waiter, timeout=timeout)
        except TimeoutError as exc:
            with contextlib.suppress(ValueError):
                self._download_waiters.remove(_waiter)
            raise BrowserTimeoutError(
                error="download_timeout",
                hint=f"No download started within {timeout}s",
                action="trigger the download (click) after calling 'download wait'",
            ) from exc

        download_url = str(meta.get("url", ""))
        if not download_url:
            raise BackendError(
                error="download_failed",
                hint="Download started but no source URL was reported",
                action="use 'download url' with the file URL instead",
            )
        entry = await self._download_url_impl(download_url, output_dir)
        return DownloadEntry(
            filename=entry.filename,
            path=entry.path,
            size=entry.size,
            url=download_url,
            source="event",
        )

    # ------------------------------------------------------------------
    # Atomic: clipboard (7a R5)
    # ------------------------------------------------------------------

    async def _grant_clipboard(self) -> None:
        if self._clipboard_granted:
            return
        with contextlib.suppress(Exception):
            await self._send(
                "cdp",
                {
                    "method": "Browser.grantPermissions",
                    "params": {
                        "permissions": [
                            "clipboardReadWrite",
                            "clipboardSanitizedWrite",
                        ]
                    },
                },
            )
        self._clipboard_granted = True

    async def _clipboard_read_impl(self) -> str:
        await self._grant_clipboard()
        result = await self._send("evaluate", {"js": "navigator.clipboard.readText()"})
        return str(result.get("result") or "")

    async def _clipboard_write_impl(self, text: str) -> None:
        await self._grant_clipboard()
        js = f"navigator.clipboard.writeText({json.dumps(text)})"
        await self._send("evaluate", {"js": js})

    # ------------------------------------------------------------------
    # Atomic: PDF (7a R6)
    # ------------------------------------------------------------------

    async def _pdf_impl(self, options: dict[str, Any]) -> bytes:
        cdp_params = _pdf_options_to_cdp(options)
        try:
            result = await self._send(
                "cdp",
                {"method": "Page.printToPDF", "params": cdp_params},
            )
        except BackendError as exc:
            raise BackendError(
                error="pdf_failed",
                hint=str(exc.hint),
                action="check the PDF options or remote Chrome support",
            ) from exc
        data = str(result.get("data", ""))
        try:
            return base64.b64decode(data)
        except Exception as exc:
            raise BackendError(
                error="pdf_failed",
                hint="Remote browser returned no PDF data",
                action="retry, or verify the page finished loading",
            ) from exc

    # ------------------------------------------------------------------
    # Atomic: cookies CRUD (7a R3)
    # ------------------------------------------------------------------

    async def _cookies_set_impl(self, cookies: list[dict[str, Any]]) -> None:
        for cookie in cookies:
            await self._send(
                "cdp",
                {"method": "Network.setCookie", "params": cookie},
            )

    async def _cookies_clear_impl(self) -> None:
        await self._send(
            "cdp",
            {"method": "Network.clearBrowserCookies", "params": {}},
        )

    async def _cookies_delete_impl(self, name: str, *, domain: str | None) -> int:
        params: dict[str, Any] = {"name": name}
        if domain is not None:
            params["domain"] = domain
        await self._send(
            "cdp",
            {"method": "Network.deleteCookies", "params": params},
        )
        # CDP deleteCookies reports no count; return 1 to signal "delete issued"
        # without claiming a precise number we can't observe.
        return 1

    # ------------------------------------------------------------------
    # Atomic: dialog
    # ------------------------------------------------------------------

    def _handle_dialog_event(self, params: dict[str, Any]) -> None:
        # Backend-agnostic dispatch (alert/beforeunload vs confirm/prompt)
        # lives on the base class; we only normalise the CDP payload into
        # the four primitive fields the dispatcher expects.
        self._dispatch_dialog_event(
            dialog_type=str(params.get("type", "alert")),
            message=str(params.get("message", "")),
            default_value=str(params.get("defaultPrompt", "")),
            url="(remote)",
        )

    async def _auto_accept_dialog_impl(self) -> None:
        """Auto-accept via CDP ``Page.handleJavaScriptDialog``."""
        try:
            await self._send(
                "cdp",
                {
                    "method": "Page.handleJavaScriptDialog",
                    "params": {"accept": True},
                },
            )
        except Exception:
            logger.debug("auto_accept_dialog_failed", exc_info=True)

    async def _dialog_handle_impl(
        self, action: str, *, text: str | None = None
    ) -> dict[str, Any]:
        accept = action == "accept"
        params: dict[str, Any] = {"accept": accept}
        if text is not None and accept:
            params["promptText"] = text
        try:
            await self._send(
                "cdp",
                {"method": "Page.handleJavaScriptDialog", "params": params},
            )
        except Exception as exc:
            logger.debug("dialog_handle_error", error=str(exc))
        return {}

    # ------------------------------------------------------------------
    # Atomic: evaluate / screenshot
    # ------------------------------------------------------------------

    async def _evaluate_impl(self, js: str, *, world: str) -> Any:
        result = await self._send("evaluate", {"js": js})
        return result.get("result")

    async def _screenshot_impl(
        self, *, full_page: bool, fmt: str, quality: int
    ) -> bytes:
        result = await self._send("screenshot", {})
        b64 = result.get("base64", "")
        return base64.b64decode(b64)

    # ------------------------------------------------------------------
    # Atomic: fetch via Playwright API (browser cookies + UA)
    # ------------------------------------------------------------------

    async def _fetch_impl(
        self,
        url: str,
        *,
        method: str,
        body: str | None,
        headers: dict[str, str] | None,
        timeout: float,
    ) -> dict[str, Any]:
        # Remote backend has no local cookie jar — bridge does a fetch-from-page.
        # For now we route via JS evaluate so cookies/UA come from the remote
        # browser context. This mirrors the spec described in the API surface.
        import json as _json

        params: dict[str, Any] = {
            "url": url,
            "method": method,
            "headers": headers or {},
        }
        if body is not None:
            params["body"] = body
        params["timeout"] = timeout

        result = await self._send("fetch", params)
        # Some bridges return parsed result, fall back to raw payload if not.
        if "body" not in result:
            with contextlib.suppress(Exception):
                result = _json.loads(_json.dumps(result))
        return result

    # ------------------------------------------------------------------
    # Atomic: tabs
    # ------------------------------------------------------------------

    async def _tab_list_impl(self) -> list[TabInfo]:
        # The extension's "tabs" command already filters out chrome:// URLs
        # and returns the full set of user tabs. The TabInfo dataclass we
        # return here is what the daemon serializes to CLI/MCP, so it has to
        # match the Playwright adapter's shape exactly (tab_id is int, etc).
        try:
            raw = await self._send("tabs", {})
        except BackendError:
            return []
        # _send is annotated dict[str, Any] but cmdTabs sets `data` to a
        # plain list, which _send returns directly. Cast straight to the
        # documented runtime shape — pyright can't prove it, the extension
        # contract does.
        entries: list[dict[str, Any]] = cast(
            "list[dict[str, Any]]", raw if isinstance(raw, list) else []
        )
        out: list[TabInfo] = []
        for entry in entries:
            tab_id_val = entry.get("id")
            if not isinstance(tab_id_val, int):
                continue
            out.append(
                TabInfo(
                    tab_id=tab_id_val,
                    url=str(entry.get("url", "")),
                    title=str(entry.get("title", "")),
                    active=bool(entry.get("active", False)),
                )
            )
        return out

    async def _tab_new_impl(self, url: str | None) -> dict[str, Any]:
        result = await self._send("tab_new", {"url": url} if url else {})
        return result

    async def _tab_close_impl(self, tab_id: int) -> dict[str, Any]:
        return await self._send("tab_close", {"tab_id": tab_id})

    async def _tab_switch_impl(self, tab_id: int) -> dict[str, Any]:
        return await self._send("tab_switch", {"tab_id": tab_id})

    # ------------------------------------------------------------------
    # Atomic: frames
    # ------------------------------------------------------------------

    async def _frame_list_impl(self) -> list[FrameInfo]:
        try:
            result = await self._send(
                "cdp",
                {"method": "Page.getFrameTree", "params": {}},
            )
        except BackendError:
            return [FrameInfo(name="(main)", url="", is_current=True)]

        frames: list[FrameInfo] = []
        self._collect_frames(result.get("frameTree", {}), frames)
        return frames

    def _collect_frames(
        self,
        frame_tree: dict[str, Any],
        out: list[FrameInfo],
    ) -> None:
        frame = frame_tree.get("frame", {})
        frame_id = frame.get("id", "")
        frame_name = frame.get("name", "")
        frame_url = frame.get("url", "")
        is_main = frame.get("parentId") is None or frame.get("parentId") == ""

        if is_main and not frame_name:
            frame_name = "(main)"

        is_current = (self._active_frame_id is None and is_main) or (
            self._active_frame_id == frame_id
        )
        out.append(
            FrameInfo(
                name=frame_name or frame_id,
                url=frame_url,
                is_current=is_current,
            )
        )
        for child in frame_tree.get("childFrames", []):
            self._collect_frames(child, out)

    async def _frame_focus_impl(
        self, *, name: str | None, url: str | None, main: bool
    ) -> dict[str, Any]:
        if main:
            self._active_frame_id = None
            self._active_frame = None
            return {
                "ok": True,
                "action": "frame_focus",
                "frame": "(main)",
                "url": "",
            }
        try:
            result = await self._send(
                "cdp",
                {"method": "Page.getFrameTree", "params": {}},
            )
        except BackendError as exc:
            raise BackendError(
                error="frame_not_supported",
                hint="Could not retrieve frame tree via CDP",
                action="ensure the Page domain is available on the extension",
            ) from exc
        target = self._find_frame(result.get("frameTree", {}), name=name, url=url)
        if target is None:
            frames_list = await self._frame_list_impl()
            available = [f.name or f.url[:60] for f in frames_list]
            raise BackendError(
                error="frame_not_found",
                hint=f"No frame matching name={name!r} url={url!r}",
                action=f"available frames: {available}",
            )
        self._active_frame_id = target["id"]
        self._active_frame = target["id"]
        return {
            "ok": True,
            "action": "frame_focus",
            "frame": target.get("name") or "(unnamed)",
            "url": target.get("url", ""),
        }

    def _find_frame(
        self,
        frame_tree: dict[str, Any],
        *,
        name: str | None = None,
        url: str | None = None,
    ) -> dict[str, Any] | None:
        frame = frame_tree.get("frame", {})
        if name and frame.get("name") == name:
            return frame
        if url:
            kind, processed = classify_url_pattern(url)
            frame_url = frame.get("url", "")
            if kind == "glob":
                if match_url_glob(processed, frame_url):
                    return frame
            elif match_url_substring(url, frame_url):
                return frame
        for child in frame_tree.get("childFrames", []):
            found = self._find_frame(child, name=name, url=url)
            if found:
                return found
        return None

    # ------------------------------------------------------------------
    # Atomic: raw CDP / close
    # ------------------------------------------------------------------

    async def _raw_cdp_impl(self, method: str, params: dict[str, Any] | None) -> Any:
        return await self.send_command(
            "cdp", {"method": method, "params": params or {}}
        )

    # ------------------------------------------------------------------
    # Persistent CDP transport (7b)
    # ------------------------------------------------------------------
    # The bridge has no session object to cache — the Extension keeps one
    # debugger attachment per tab and forwards every CDP event back through
    # ``feed_message`` already (see the global ``chrome.debugger.onEvent``
    # forwarder in background.js). So ``_cdp_send_impl`` reuses the same
    # ``cdp`` command shape as ``_raw_cdp_impl``; the only new wire message is
    # ``enable_domain``, which asks the Extension to ``<Domain>.enable`` on the
    # active tab (the events then flow without further plumbing here).

    async def _cdp_send_impl(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._send("cdp", {"method": method, "params": params})

    async def _cdp_enable_domain_impl(self, domain: str) -> None:
        await self._send("enable_domain", {"domain": domain})

    # ------------------------------------------------------------------
    # Header injection (7b T1.2)
    # ------------------------------------------------------------------

    async def _set_extra_headers_impl(self, headers: dict[str, str]) -> None:
        # Network.setExtraHTTPHeaders needs the Network domain enabled; enabling
        # is idempotent on the Extension side (it tracks enabled domains).
        await self._cdp_enable_domain("Network")
        await self._send(
            "cdp",
            {"method": "Network.setExtraHTTPHeaders", "params": {"headers": headers}},
        )

    # ------------------------------------------------------------------
    # Route interception (7b T1.3)
    # ------------------------------------------------------------------
    # The Extension forwards every CDP event through ``feed_message`` →
    # ``_dispatch_cdp_event``, so we register a synchronous ``Fetch.requestPaused``
    # handler once and (re-)issue ``Fetch.enable`` whenever rules change. The
    # handler can't await (callbacks must stay non-blocking), so it spawns a
    # task that resumes the request via continue/fulfill/fail. Matching reuses
    # the shared RouteManager so the disposition matches the Playwright backend.

    async def _route_add_impl(self, rule: Any) -> None:
        if not self._fetch_enabled:
            self._on_cdp_event("Fetch.requestPaused", self._on_request_paused)
            self._fetch_enabled = True
        # Enable Fetch with a catch-all pattern; the per-rule decision happens
        # in the handler. Re-enabling is harmless and keeps the pattern current.
        await self._send(
            "cdp",
            {"method": "Fetch.enable", "params": {"patterns": [{"urlPattern": "*"}]}},
        )

    async def _route_remove_impl(self, pattern: str | None) -> None:
        # When no rules remain, fully disable Fetch so requests stop pausing.
        # The RouteManager removes the rule from its list before/after this call,
        # so we check the live count to decide.
        remaining = self.route_manager.list_rules()
        if pattern is None or not remaining:
            with contextlib.suppress(Exception):
                await self._send("cdp", {"method": "Fetch.disable", "params": {}})
            self._fetch_enabled = False

    def _on_request_paused(self, params: dict[str, Any]) -> None:
        """Synchronous ``Fetch.requestPaused`` callback — dispatch to a task.

        Resuming a paused request is an async CDP round-trip, but event
        callbacks must not block the dispatch loop, so we hand the work to a
        tracked task (the same pattern the capture path uses for response
        bodies).
        """
        task = asyncio.ensure_future(self._resume_paused_request(params))
        self._route_tasks.add(task)
        task.add_done_callback(self._route_tasks.discard)

    async def _resume_paused_request(self, params: dict[str, Any]) -> None:
        request_id = str(params.get("requestId", ""))
        if not request_id:
            return
        request = cast("dict[str, Any]", params.get("request") or {})
        url = str(request.get("url", ""))
        method = str(request.get("method", "")) or None
        resource_type = params.get("resourceType")
        rule = self.route_manager.match(
            url,
            resource_type=str(resource_type) if resource_type else None,
            method=method,
        )
        try:
            if rule is None or rule.action == "continue":
                await self._send(
                    "cdp",
                    {
                        "method": "Fetch.continueRequest",
                        "params": {"requestId": request_id},
                    },
                )
            elif rule.action == "abort":
                await self._send(
                    "cdp",
                    {
                        "method": "Fetch.failRequest",
                        "params": {
                            "requestId": request_id,
                            "errorReason": "Aborted",
                        },
                    },
                )
            elif rule.action == "fulfill":
                await self._send(
                    "cdp",
                    {
                        "method": "Fetch.fulfillRequest",
                        "params": _fulfill_params(request_id, rule),
                    },
                )
        except Exception:
            logger.debug("route_resume_failed", request_id=request_id, exc_info=True)

    # ------------------------------------------------------------------
    # Capture (CDP Network domain)
    # ------------------------------------------------------------------
    # PlaywrightAdapter records via Page event listeners wired at launch
    # time. RemoteBridge has no equivalent — we drive ``Network.enable``
    # over the existing CDP channel and reconstruct entries from the events
    # the Extension forwards back through ``feed_message``.

    async def _capture_setup_impl(self) -> None:
        try:
            await self._send(
                "cdp",
                {"method": "Network.enable", "params": {}},
            )
        except Exception:
            logger.warning("network_enable_failed", exc_info=True)

    async def _capture_teardown_impl(self) -> None:
        try:
            await self._send(
                "cdp",
                {"method": "Network.disable", "params": {}},
            )
        except Exception:
            logger.warning("network_disable_failed", exc_info=True)
        # Drop any in-flight entries that never reached ``loadingFinished``
        # — they would otherwise leak across capture sessions.
        self._pending_captures.clear()

    def _handle_network_event(self, method: str, params: dict[str, Any]) -> None:
        """Stitch CDP ``Network.*`` events into :class:`CaptureEntry` records.

        Each requestId moves through three states:
        requestWillBeSent → responseReceived → loadingFinished. The first
        two mutate the pending entry; the last triggers an async
        ``Network.getResponseBody`` and pushes the finalised entry into
        the shared :class:`CaptureStore`.
        """
        request_id = str(params.get("requestId", ""))
        if not request_id:
            return

        if method == "Network.requestWillBeSent":
            request_obj = cast("dict[str, Any]", params.get("request") or {})
            req_headers = cast("dict[str, Any]", request_obj.get("headers") or {})
            self._pending_captures[request_id] = {
                "request_id": request_id,
                "url": str(request_obj.get("url", "")),
                "method": str(request_obj.get("method", "GET")),
                "request_headers": self._stringify_headers(req_headers),
                "request_body": request_obj.get("postData"),
                # CDP uses lowercase resource type strings ("xhr", "fetch",
                # "stylesheet", ...). Default to "other" for safety.
                "resource_type": str(params.get("type", "other")).lower(),
                "wall_time": float(params.get("wallTime", 0) or 0),
                "request_seq": self._seq_counter.value,
                "status": 0,
                "content_type": "",
                "response_headers": {},
            }
            return

        if method == "Network.responseReceived":
            entry = self._pending_captures.get(request_id)
            if entry is None:
                return
            response_obj = cast("dict[str, Any]", params.get("response") or {})
            resp_headers = cast("dict[str, Any]", response_obj.get("headers") or {})
            entry["status"] = int(response_obj.get("status", 0) or 0)
            entry["response_headers"] = self._stringify_headers(resp_headers)
            entry["content_type"] = str(response_obj.get("mimeType", "") or "")
            return

        if method == "Network.loadingFinished":
            entry = self._pending_captures.pop(request_id, None)
            if entry is None:
                return
            task = asyncio.ensure_future(self._finalize_capture(entry))
            self._capture_tasks.add(task)
            task.add_done_callback(self._capture_tasks.discard)
            return

        if method == "Network.loadingFailed":
            # Request died before producing a body — keep the partial entry
            # if we at least saw a status, but skip getResponseBody since
            # it would just 404.
            entry = self._pending_captures.pop(request_id, None)
            if entry is None or entry.get("status", 0) == 0:
                return
            task = asyncio.ensure_future(
                self._finalize_capture(entry, fetch_body=False)
            )
            self._capture_tasks.add(task)
            task.add_done_callback(self._capture_tasks.discard)

    async def _finalize_capture(
        self, entry: dict[str, Any], *, fetch_body: bool = True
    ) -> None:
        """Fetch the response body if recordable, then push to the store."""
        from datetime import UTC, datetime

        from agentcloak.core.capture import build_capture_entry

        content_type = str(entry.get("content_type", ""))
        raw_body: str | None = None
        if fetch_body and is_recordable_content(content_type):
            try:
                body_result = await self._send(
                    "cdp",
                    {
                        "method": "Network.getResponseBody",
                        "params": {"requestId": entry["request_id"]},
                    },
                )
                raw_body = str(body_result.get("body", ""))
                if body_result.get("base64Encoded"):
                    try:
                        raw_body = base64.b64decode(raw_body).decode(
                            "utf-8", errors="replace"
                        )
                    except Exception:
                        raw_body = ""
            except Exception:
                logger.debug(
                    "get_response_body_failed",
                    request_id=entry.get("request_id"),
                    exc_info=True,
                )
                raw_body = None

        # CDP exposes wallTime as seconds-since-epoch; convert to ISO when
        # available so the entry's timestamp matches the network event the
        # extension observed. The shared builder falls back to "now" when
        # we leave timestamp=None.
        wall_time = float(entry.get("wall_time", 0) or 0)
        timestamp = (
            datetime.fromtimestamp(wall_time, tz=UTC).isoformat()
            if wall_time > 0
            else None
        )

        capture_entry = build_capture_entry(
            seq=int(entry.get("request_seq", self._seq_counter.value)),
            method=str(entry.get("method", "GET")),
            url=str(entry.get("url", "")),
            status=int(entry.get("status", 0)),
            resource_type=str(entry.get("resource_type", "other")),
            request_headers=dict(entry.get("request_headers", {})),
            response_headers=dict(entry.get("response_headers", {})),
            request_body=entry.get("request_body"),
            raw_response_body=raw_body,
            content_type=content_type,
            timestamp=timestamp,
        )
        # ``add()`` enforces its own resource-type / extension skip filter,
        # so we don't double-filter here — matches Playwright's behaviour.
        self._capture_store.add(capture_entry)

    @staticmethod
    def _stringify_headers(raw: dict[str, Any]) -> dict[str, str]:
        """CDP header values arrive as strings or lists — flatten to ``str``."""
        result: dict[str, str] = {}
        for k, v in raw.items():
            if isinstance(v, list):
                # Each header line gets concatenated with ", " to mirror the
                # representation Playwright uses in its own capture entries.
                result[str(k)] = ", ".join(str(item) for item in v)  # type: ignore[arg-type]
            else:
                result[str(k)] = str(v)
        return result

    async def _close_impl(self) -> None:
        if not self._ws.closed:
            await self._ws.close()
