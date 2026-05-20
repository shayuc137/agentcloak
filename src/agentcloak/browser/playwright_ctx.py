"""PlaywrightContext — browser backend implemented on top of BrowserContextBase.

The class only implements the small set of atomic methods declared by the base
class (``_navigate_impl``, ``_click_impl``, ``_get_ax_tree`` etc.). The
orchestrators (``action()``, ``snapshot()``, ``action_batch()``, ``wait()``,
``upload()``, ``fetch()``) all live in ``BrowserContextBase``.

What stays here:
- Playwright-specific event listeners (network, dialog, framenavigated, etc.)
- Multi-tab bookkeeping (pages keyed by tab_id)
- CDP-driven element resolution (backendDOMNodeId → data-cloak-ref marker)
- Backend factory ``launch_playwright``

``screenshot_to_base64`` lives on :mod:`agentcloak.browser.base` so daemon
routes can call it without depending on a specific backend module. Older
versions duplicated the helper here too; the duplicate was a layering hazard
(daemon code accidentally pulled in Playwright internals) and was removed.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import re
import shutil
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import unquote, urlparse

import httpx
import structlog

from agentcloak.browser._snapshot_builder import FrameData
from agentcloak.browser.base import (
    BrowserContextBase,
    classify_url_pattern,
    match_url_substring,
)
from agentcloak.browser.state import (
    DownloadEntry,
    FrameInfo,
    TabInfo,
)
from agentcloak.core.errors import (
    BackendError,
    BrowserTimeoutError,
    ElementNotFoundError,
    NavigationError,
)
from agentcloak.core.seq import RingBuffer, SeqCounter, SeqEvent
from agentcloak.core.types import StealthTier

if TYPE_CHECKING:
    from agentcloak.core.capture import CaptureStore
    from agentcloak.core.config import BrowserConfig

__all__ = ["PlaywrightContext", "launch_playwright"]

logger = structlog.get_logger()

_SNAP_CHROMIUM = "/snap/chromium/current/usr/lib/chromium-browser/chrome"


def find_free_port() -> int:
    """Bind to port 0 and return the OS-assigned free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_FILENAME_STAR_RE = re.compile(r"filename\*=(?:[^']*'')?([^;]+)", re.IGNORECASE)
_FILENAME_RE = re.compile(r'filename="?([^";]+)"?', re.IGNORECASE)


def _download_filename(url: str, headers: Any) -> str:
    """Derive a safe download filename from Content-Disposition or the URL.

    Prefers RFC 5987 ``filename*`` then plain ``filename`` in the
    Content-Disposition header, falling back to the URL path's basename, then
    a generic ``download.bin``. Strips path separators so a malicious header
    can't write outside the target directory.
    """
    disposition = ""
    with contextlib.suppress(Exception):
        disposition = headers.get("content-disposition", "") or ""

    name = ""
    star = _FILENAME_STAR_RE.search(disposition)
    if star:
        name = unquote(star.group(1).strip().strip('"'))
    else:
        plain = _FILENAME_RE.search(disposition)
        if plain:
            name = plain.group(1).strip()

    if not name:
        path = urlparse(url).path
        name = path.rsplit("/", 1)[-1] if path else ""

    # Strip any directory components and reject empty/relative names.
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in (".", ".."):
        return "download.bin"
    return name


def _find_chromium() -> str | None:
    if Path(_SNAP_CHROMIUM).is_file():
        return _SNAP_CHROMIUM
    for name in (
        "chromium-browser",
        "chromium",
        "google-chrome-stable",
        "google-chrome",
    ):
        path = shutil.which(name)
        if path:
            return path
    return None


class PlaywrightContext(BrowserContextBase):
    """BrowserContext implementation backed by Playwright."""

    def __init__(
        self,
        *,
        page: Any,
        browser: Any | None,
        playwright: Any,
        seq_counter: SeqCounter,
        ring_buffer: RingBuffer,
        browser_context: Any | None = None,
        proxy_url: str | None = None,
        capture_store: CaptureStore | None = None,
        cdp_port: int | None = None,
        browser_config: BrowserConfig | None = None,
    ) -> None:
        super().__init__(
            seq_counter=seq_counter,
            ring_buffer=ring_buffer,
            capture_store=capture_store,
            browser_config=browser_config,
        )

        # Multi-tab state: map tab_id -> Page, initial page is tab 0
        self._tabs: dict[int, Any] = {0: page}
        self._active_tab: int = 0
        self._next_tab_id: int = 1
        self._browser = browser
        self._playwright = playwright
        self._browser_context = browser_context
        self._proxy_url = proxy_url
        self._pending_captures: set[asyncio.Task[None]] = set()
        self._cdp_port: int | None = cdp_port
        # Playwright Dialog object retained so dialog_handle can accept/dismiss.
        self._dialog_object: Any = None

        # 7b: persistent CDP sessions keyed by tab_id, for the reverse-
        # engineering managers that need long-lived event streams (debugger,
        # WebSocket/SSE, sourcemap). This is deliberately separate from the
        # seven short-lived ``new_cdp_session + detach`` call sites elsewhere
        # in this file (snapshot/evaluate/clipboard/raw_cdp) — those are
        # one-shot and must NOT be migrated here in 7b. A persistent session
        # forwards every CDP event to ``_dispatch_cdp_event`` via the generic
        # Playwright ``"event"`` listener, and is invalidated when its tab
        # closes (see ``_invalidate_cdp_session``).
        self._cdp_sessions: dict[int, Any] = {}

        # 7b T1.3: registered ``page.route`` handlers keyed by the rule pattern,
        # so ``_route_remove_impl`` can unroute a single rule by passing the
        # exact callable Playwright registered (it matches handlers by identity).
        self._route_handlers: dict[str, Any] = {}

        self._setup_network_listeners(page)
        self._setup_feedback_listeners(page)

    # ------------------------------------------------------------------
    # Active page / target frame
    # ------------------------------------------------------------------

    @property
    def _page(self) -> Any:
        """Return the active tab's Page object."""
        return self._tabs[self._active_tab]

    @property
    def _target_frame(self) -> Any:
        """Return the active frame (or main page) for actions/snapshots."""
        if self._active_frame is not None:
            return self._active_frame
        return self._page

    @property
    def stealth_tier(self) -> StealthTier:
        return StealthTier.PLAYWRIGHT

    def browser_description(self) -> str:
        """Plain Playwright Chromium — no stealth patches, version is generic."""
        return "Playwright Chromium"

    async def _get_page_info(self) -> tuple[str, str]:
        try:
            url = str(self._page.url)
        except Exception:
            url = ""
        try:
            title = str(await self._page.title())
        except Exception:
            title = ""
        return url, title

    async def resume_snapshot(self) -> dict[str, Any]:
        """Active tab url/title + full tab inventory keyed by ``tab_id``.

        Wraps :meth:`_get_page_info` for the active tab, then walks
        ``self._tabs`` for the per-tab URLs the daemon persists to
        ``resume.json``. Swallowing exceptions keeps the daemon
        recoverable even when a page or tab is mid-navigation.
        """
        data = await super().resume_snapshot()
        url, title = await self._get_page_info()
        data["url"] = url
        data["title"] = title

        tabs: list[dict[str, Any]] = []
        for tid, pg in self._tabs.items():
            try:
                tabs.append({"tab_id": tid, "url": str(pg.url)})
            except Exception:
                tabs.append({"tab_id": tid, "url": ""})
        data["tabs"] = tabs
        return data

    # ------------------------------------------------------------------
    # Event listeners
    # ------------------------------------------------------------------

    def _setup_network_listeners(self, page: Any | None = None) -> None:
        target = page if page is not None else self._page
        target.on("response", self._on_response)

    def _setup_feedback_listeners(self, page: Any | None = None) -> None:
        target = page if page is not None else self._page
        target.on("request", self._on_request_start)
        target.on("requestfinished", self._on_request_end)
        target.on("requestfailed", self._on_request_end)
        target.on("dialog", self._on_dialog)
        target.on("framenavigated", self._on_frame_navigated)
        target.on("download", self._on_download)
        # Console capture (7a R1): wire listeners eagerly so messages emitted
        # before the first ``console`` query (e.g. during navigate) are not
        # lost. The ring buffer caps growth; ``_console_setup_impl`` is a
        # no-op on this backend because registration already happened here.
        target.on("console", self._on_console)
        target.on("pageerror", self._on_page_error)

    def _on_request_start(self, _request: Any) -> None:
        self._pending_request_count += 1

    def _on_request_end(self, _request: Any) -> None:
        if self._pending_request_count > 0:
            self._pending_request_count -= 1

    def _on_dialog(self, dialog: Any) -> None:
        # Stash the Playwright Dialog object up front so both auto-accept
        # (for alert/beforeunload) and the agent-driven _dialog_handle_impl
        # path can reach it via ``self._dialog_object``. The base class
        # owns the alert-vs-confirm/prompt dispatch logic so this stays
        # backend-agnostic.
        self._dialog_object = dialog
        self._dispatch_dialog_event(
            dialog_type=dialog.type,
            message=dialog.message,
            default_value=dialog.default_value or "",
            url=self._page.url,
        )

    async def _auto_accept_dialog_impl(self) -> None:
        """Accept the stored Playwright Dialog via its native ``accept()``."""
        dialog = self._dialog_object
        if dialog is None:
            return
        try:
            await dialog.accept()
        except Exception:
            logger.debug("auto_accept_dialog_failed", exc_info=True)
        finally:
            # Auto-accepted dialogs are resolved, so clear the slot
            # otherwise ``_dialog_handle_impl`` would try to operate on a
            # dead reference.
            self._dialog_object = None

    def _on_frame_navigated(self, frame: Any) -> None:
        try:
            if frame == self._page.main_frame:
                self._last_navigation_event = {
                    "url": frame.url,
                }
        except Exception:
            pass

    def _on_download(self, download: Any) -> None:
        with contextlib.suppress(Exception):
            self._last_download_event = {
                "filename": download.suggested_filename,
            }
        # Hand the Download object to a parked ``download_wait`` caller, if any.
        with contextlib.suppress(Exception):
            self._resolve_download_waiter(download)

    # Playwright console types use "warning"; agents/CLI filter on "warn".
    _CONSOLE_LEVEL_MAP: ClassVar[dict[str, str]] = {"warning": "warn"}

    def _on_console(self, msg: Any) -> None:
        with contextlib.suppress(Exception):
            loc: dict[str, Any] = {}
            with contextlib.suppress(Exception):
                loc = msg.location or {}
            raw_type = str(getattr(msg, "type", "log"))
            self._record_console_entry(
                level=self._CONSOLE_LEVEL_MAP.get(raw_type, raw_type),
                text=str(getattr(msg, "text", "")),
                url=str(loc.get("url", "")),
                line=loc.get("lineNumber"),
                column=loc.get("columnNumber"),
                is_error=False,
            )

    def _on_page_error(self, error: Any) -> None:
        with contextlib.suppress(Exception):
            self._record_console_entry(
                level="error",
                text=str(error),
                url="",
                line=None,
                column=None,
                is_error=True,
            )

    def _on_response(self, response: Any) -> None:
        try:
            request = response.request
            self._ring_buffer.append(
                SeqEvent(
                    seq=self._seq_counter.value,
                    kind="network",
                    data={
                        "method": request.method,
                        "url": request.url,
                        "status": response.status,
                        "resource_type": request.resource_type,
                    },
                )
            )
            if self._capture_store.recording:
                task = asyncio.ensure_future(
                    self._record_capture_async(request, response)
                )
                self._pending_captures.add(task)
                task.add_done_callback(self._pending_captures.discard)
        except Exception:
            logger.debug("on_response_error", exc_info=True)

    async def _record_capture_async(self, request: Any, response: Any) -> None:
        try:
            from agentcloak.core.capture import (
                build_capture_entry,
                is_recordable_content,
            )

            req_headers: dict[str, str] = {}
            try:
                for k, v in request.headers.items():
                    req_headers[k] = v
            except Exception:
                pass

            resp_headers: dict[str, str] = {}
            try:
                for k, v in response.headers.items():
                    resp_headers[k] = v
            except Exception:
                pass

            content_type = resp_headers.get(
                "content-type", resp_headers.get("Content-Type", "")
            )

            req_body: str | None = None
            try:
                if request.method in ("POST", "PUT", "PATCH"):
                    req_body = request.post_data
            except Exception:
                pass

            # Only fetch the response body if the content-type suggests
            # we'll actually keep it — saves a roundtrip on every image,
            # font, and JS asset the page loads.
            raw_response_body: str | None = None
            if is_recordable_content(content_type):
                try:
                    raw = await response.body()
                    raw_response_body = raw.decode("utf-8", errors="replace")
                except Exception:
                    pass

            entry = build_capture_entry(
                seq=self._seq_counter.value,
                method=request.method,
                url=request.url,
                status=response.status,
                resource_type=request.resource_type,
                request_headers=req_headers,
                response_headers=resp_headers,
                request_body=req_body,
                raw_response_body=raw_response_body,
                content_type=content_type,
            )
            self._capture_store.add(entry)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Atomic: navigate / ax tree / page snapshots
    # ------------------------------------------------------------------

    async def _navigate_impl(self, url: str, *, timeout: float) -> dict[str, Any]:
        try:
            resp = await self._page.goto(
                url, timeout=timeout * 1000, wait_until="domcontentloaded"
            )
        except Exception as exc:
            if "timeout" in str(exc).lower():
                raise BrowserTimeoutError(
                    error="navigation_timeout",
                    hint=f"Page did not load within {timeout}s",
                    action=f"retry with longer timeout or check URL: {url}",
                ) from exc
            # Playwright appends a verbose "Call log:" section to its error
            # messages — useful for human debugging but noise for agents and
            # CLI users. Strip it so the hint stays concise and actionable.
            msg = str(exc)
            if "\nCall log:" in msg:
                msg = msg[: msg.index("\nCall log:")]
            raise NavigationError(
                error="navigation_failed",
                hint=msg.strip(),
                action="check URL and network connectivity",
            ) from exc

        status = resp.status if resp else 0
        return {
            "url": self._page.url,
            "title": await self._page.title(),
            "status": status,
        }

    async def _get_ax_tree(self, *, frames: bool = False) -> list[dict[str, Any]]:
        if (
            self._active_frame is not None
            and self._active_frame != self._page.main_frame
        ):
            return await self._get_frame_ax_tree(self._active_frame)
        cdp = await self._page.context.new_cdp_session(self._page)
        try:
            tree = await cdp.send("Accessibility.getFullAXTree", {"pierce": True})
        finally:
            await cdp.detach()
        return tree.get("nodes", [])

    async def _get_frame_ax_tree(self, frame: Any) -> list[dict[str, Any]]:
        """Get AX tree for a focused frame (cross-origin or same-origin)."""
        # Cross-origin iframes have their own CDP session
        try:
            cdp = await self._page.context.new_cdp_session(frame)
            try:
                tree = await cdp.send("Accessibility.getFullAXTree", {"pierce": True})
            finally:
                await cdp.detach()
            return tree.get("nodes", [])
        except Exception:
            pass
        # Same-origin iframe: use main page session + frameId scope
        cdp = await self._page.context.new_cdp_session(self._page)
        try:
            fid = await self._resolve_cdp_frame_id(cdp, frame)
            params: dict[str, Any] = {"pierce": True}
            if fid:
                params["frameId"] = fid
            tree = await cdp.send("Accessibility.getFullAXTree", params)
        finally:
            await cdp.detach()
        return tree.get("nodes", [])

    @staticmethod
    async def _resolve_cdp_frame_id(cdp: Any, frame: Any) -> str | None:
        """Get the CDP frameId for a Playwright Frame via Page.getFrameTree."""
        try:
            result = await cdp.send("Page.getFrameTree", {})
        except Exception:
            return None

        def _find(node: dict[str, Any]) -> str | None:
            f = node.get("frame", {})
            if (frame.name and f.get("name") == frame.name) or (
                frame.url and f.get("url") == frame.url
            ):
                return f.get("id")
            for child in node.get("childFrames", []):
                found = _find(child)
                if found:
                    return found
            return None

        return _find(result.get("frameTree", {}))

    async def _get_child_frame_trees(self) -> list[FrameData]:
        child_frames: list[FrameData] = []
        for frame in self._page.frames:
            if frame == self._page.main_frame:
                continue
            try:
                cdp = await self._page.context.new_cdp_session(frame)
                try:
                    tree = await cdp.send(
                        "Accessibility.getFullAXTree", {"pierce": True}
                    )
                finally:
                    await cdp.detach()
                nodes = tree.get("nodes", [])
                if nodes:
                    frame_name = frame.name or ""
                    frame_url = frame.url or ""
                    frame_id = frame_name or frame_url or str(id(frame))
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
                    frame_name=getattr(frame, "name", ""),
                    frame_url=getattr(frame, "url", ""),
                    exc_info=True,
                )
        return child_frames

    async def _snapshot_dom_impl(self) -> str:
        html = await self._page.content()
        truncated = html[:100_000]
        if len(html) > 100_000:
            truncated += "\n[...truncated...]"
        return truncated

    async def _snapshot_content_impl(self) -> str:
        text: str = await self._page.evaluate("document.body?.innerText || ''")
        return text

    async def _network_entries(self, *, since_seq: int) -> list[dict[str, Any]]:
        # Playwright back-end records via on_response → ring buffer; base class
        # already pulls them. Nothing extra here.
        return []

    # ------------------------------------------------------------------
    # Element resolution
    # ------------------------------------------------------------------

    async def _resolve_element(self, index: int) -> Any:
        """Resolve a selector_map index to a Playwright ElementHandle/Locator."""
        self._require_snapshot(index)

        backend_node_id = self._backend_node_map.get(index)
        if backend_node_id is not None:
            try:
                return await self._resolve_by_backend_node(backend_node_id, index)
            except Exception as exc:
                logger.debug(
                    "cdp_resolve_fallback",
                    index=index,
                    backend_node_id=backend_node_id,
                    error=str(exc),
                )

        return await self._resolve_by_role(index)

    async def _resolve_by_backend_node(self, backend_node_id: int, index: int) -> Any:
        """Resolve via CDP backendDOMNodeId — exact match, no re-snapshot."""
        marker = f"__cloak_{index}"
        cdp = await self._page.context.new_cdp_session(self._page)
        try:
            result = await cdp.send(
                "DOM.resolveNode", {"backendNodeId": backend_node_id}
            )
            object_id = result["object"]["objectId"]
            await cdp.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": object_id,
                    "functionDeclaration": "function() {"
                    f" this.setAttribute('data-cloak-ref','{marker}');"
                    " }",
                },
            )
        finally:
            await cdp.detach()
        locator = self._page.locator(f'[data-cloak-ref="{marker}"]')
        if await locator.count() == 0:
            raise BackendError(
                error="element_resolve_failed",
                hint=f"CDP resolved [{index}] but locator found nothing",
                action="the element may have been removed — run 'snapshot' to refresh",
            )
        return locator.first

    async def _resolve_by_role(self, index: int) -> Any:
        ref = self._selector_map[index]
        role_name = ref.role.lower()

        if ref.text:
            locator = self._page.get_by_role(role_name, name=ref.text, exact=False)
            try:
                count = await locator.count()
                if count > 0:
                    if count > 1:
                        logger.debug(
                            "role_resolve_ambiguous",
                            index=index,
                            role=role_name,
                            name=ref.text,
                            matches=count,
                        )
                    return locator.first
            except Exception as exc:
                logger.debug(
                    "role_name_resolve_failed",
                    index=index,
                    role=role_name,
                    name=ref.text,
                    error=str(exc),
                )

        locator = self._page.get_by_role(role_name)
        try:
            count = await locator.count()
            if count > 0:
                logger.debug(
                    "role_only_resolve",
                    index=index,
                    role=role_name,
                    matches=count,
                )
                return locator.first
        except Exception as exc:
            logger.debug(
                "role_only_resolve_failed",
                index=index,
                role=role_name,
                error=str(exc),
            )

        raise BackendError(
            error="element_resolve_failed",
            hint=f"Could not resolve [{index}] <{ref.role}> '{ref.text}' in the DOM",
            action="the page may have changed — run 'snapshot' to refresh, then retry",
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
    ) -> dict[str, Any]:
        if x is not None and y is not None:
            await self._page.mouse.click(
                float(x), float(y), button=button, click_count=int(click_count)
            )
            return {"clicked": True, "x": x, "y": y}

        if not target:
            raise ElementNotFoundError(
                error="element_not_found",
                hint="click requires a target element",
                action=(
                    "provide 'target' as '[N]' ref from snapshot,"
                    " or use (x, y) coordinates"
                ),
            )

        index = int(target)
        element = await self._resolve_element(index)
        await element.click(button=button, click_count=int(click_count))
        ref = self._get_ref(index)
        return {"clicked": True, "index": index, "element": ref}

    async def _fill_impl(self, *, target: str, text: str) -> dict[str, Any]:
        if not target:
            raise ElementNotFoundError(
                error="element_not_found",
                hint="fill requires a target element",
                action="provide 'target' as '[N]' ref from snapshot",
            )
        index = int(target)
        element = await self._resolve_element(index)
        await element.fill(str(text))
        ref = self._get_ref(index)
        return {"filled": True, "index": index, "text": text, "element": ref}

    async def _type_impl(
        self, *, target: str, text: str, delay: float
    ) -> dict[str, Any]:
        if not target:
            raise ElementNotFoundError(
                error="element_not_found",
                hint="type requires a target element",
                action="provide 'target' as '[N]' ref from snapshot",
            )
        index = int(target)
        element = await self._resolve_element(index)
        await element.press_sequentially(str(text), delay=float(delay))
        ref = self._get_ref(index)
        return {"typed": True, "index": index, "text": text, "element": ref}

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
            index = int(target)
            element = await self._resolve_element(index)
            await element.scroll_into_view_if_needed()
            return {"scrolled": True, "index": index, "direction": direction}

        await self._page.mouse.wheel(delta_x, delta_y)
        return {"scrolled": True, "direction": direction, "amount": amount}

    async def _hover_impl(
        self,
        *,
        target: str,
        x: float | None,
        y: float | None,
    ) -> dict[str, Any]:
        if x is not None and y is not None:
            await self._page.mouse.move(float(x), float(y))
            return {"hovered": True, "x": x, "y": y}

        if not target:
            raise ElementNotFoundError(
                error="element_not_found",
                hint="hover requires a target element",
                action=(
                    "provide 'target' as '[N]' ref from snapshot,"
                    " or use (x, y) coordinates"
                ),
            )

        index = int(target)
        element = await self._resolve_element(index)
        await element.hover()
        ref = self._get_ref(index)
        return {"hovered": True, "index": index, "element": ref}

    async def _select_impl(
        self,
        *,
        target: str,
        value: str | None,
        label: str | None,
    ) -> dict[str, Any]:
        if not target:
            raise ElementNotFoundError(
                error="element_not_found",
                hint="select requires a target element",
                action="provide 'target' as '[N]' ref from snapshot",
            )
        index = int(target)
        element = await self._resolve_element(index)

        if value is not None:
            await element.select_option(value=value)
        elif label is not None:
            await element.select_option(label=label)

        ref = self._get_ref(index)
        return {
            "selected": True,
            "index": index,
            "value": value,
            "label": label,
            "element": ref,
        }

    async def _press_impl(self, *, target: str, key: str) -> dict[str, Any]:
        if target:
            index = int(target)
            element = await self._resolve_element(index)
            await element.press(str(key))
            ref = self._get_ref(index)
            return {"pressed": True, "key": key, "index": index, "element": ref}
        await self._page.keyboard.press(str(key))
        return {"pressed": True, "key": key}

    async def _keydown_impl(self, *, key: str) -> dict[str, Any]:
        await self._page.keyboard.down(str(key))
        return {"keydown": True, "key": key}

    async def _keyup_impl(self, *, key: str) -> dict[str, Any]:
        await self._page.keyboard.up(str(key))
        return {"keyup": True, "key": key}

    async def _post_action_cleanup(self) -> None:
        with contextlib.suppress(Exception):
            await self._page.wait_for_load_state("domcontentloaded", timeout=2000)
        with contextlib.suppress(Exception):
            await self._page.evaluate(
                "document.querySelectorAll('[data-cloak-ref]')"
                ".forEach(e=>e.removeAttribute('data-cloak-ref'))"
            )

    # ------------------------------------------------------------------
    # Atomic: wait
    # ------------------------------------------------------------------

    async def _wait_impl(
        self,
        *,
        condition: str,
        value: str,
        timeout: int,
        state: str,
    ) -> dict[str, Any]:
        target = self._target_frame

        if condition == "selector":
            await target.wait_for_selector(value, state=state, timeout=timeout)
        elif condition == "url":
            kind, processed = classify_url_pattern(value)
            if kind == "glob":
                # Native Playwright glob — '*' does not cross '/'.
                await self._page.wait_for_url(processed, timeout=timeout)
            else:
                # Substring path: pass a predicate so Playwright handles the
                # wait + timeout machinery for us. Returns immediately when
                # the current URL already contains the keyword.
                keyword = processed

                def _url_contains(url: str) -> bool:
                    return keyword in url

                await self._page.wait_for_url(_url_contains, timeout=timeout)
        elif condition == "load":
            await self._page.wait_for_load_state(value, timeout=timeout)
        elif condition == "js":
            await self._page.wait_for_function(value, timeout=timeout)
        elif condition == "ms":
            await asyncio.sleep(int(value) / 1000)
        else:
            raise BackendError(
                error="invalid_wait_condition",
                hint=f"Unknown condition: '{condition}'",
                action="use one of: selector, url, load, js, ms",
            )

        return {"condition": condition, "value": value}

    # ------------------------------------------------------------------
    # Atomic: upload
    # ------------------------------------------------------------------

    async def _upload_impl(self, index: int, files: list[str]) -> dict[str, Any]:
        element = await self._resolve_element(index)
        await element.set_input_files(files)
        return {"uploaded": True}

    # ------------------------------------------------------------------
    # Atomic: console capture (7a R1)
    # ------------------------------------------------------------------

    async def _console_setup_impl(self) -> None:
        # Listeners are registered eagerly in ``_setup_feedback_listeners`` so
        # nothing emitted before the first query is missed. Nothing to do here.
        return None

    # ------------------------------------------------------------------
    # Atomic: download (7a R2)
    # ------------------------------------------------------------------

    async def _browser_cookie_jar(self) -> tuple[httpx.Cookies, str]:
        """Build an httpx cookie jar + UA from the live browser context.

        Shared by direct-URL download (and mirrors the cookie-sync logic in
        ``_fetch_impl``) so a server-side download carries the same session as
        the logged-in browser.
        """
        context = self._page.context
        cookies_raw: list[dict[str, Any]] = await context.cookies()
        jar = httpx.Cookies()
        for c in cookies_raw:
            jar.set(
                c["name"],
                c["value"],
                domain=c.get("domain", ""),
                path=c.get("path", "/"),
            )
        ua: str = await self._page.evaluate("navigator.userAgent")
        return jar, ua

    async def _download_url_impl(self, url: str, output_dir: str) -> DownloadEntry:
        jar, ua = await self._browser_cookie_jar()
        client_kwargs: dict[str, Any] = {
            "cookies": jar,
            "timeout": httpx.Timeout(60.0),
            "follow_redirects": True,
        }
        if self._proxy_url:
            client_kwargs["proxy"] = self._proxy_url

        out_dir = Path(output_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                async with client.stream(
                    "GET", url, headers={"User-Agent": ua}
                ) as resp:
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
                action="check the URL is reachable and not behind auth you lack",
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
        self, output_dir: str, *, timeout: float
    ) -> DownloadEntry:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._download_waiters.append(fut)
        try:
            download = await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError as exc:
            with contextlib.suppress(ValueError):
                self._download_waiters.remove(fut)
            raise BrowserTimeoutError(
                error="download_timeout",
                hint=f"No download started within {timeout}s",
                action="trigger the download (click) after calling 'download wait'",
            ) from exc

        out_dir = Path(output_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / download.suggested_filename
        await download.save_as(str(dest))
        size = dest.stat().st_size if dest.is_file() else 0
        return DownloadEntry(
            filename=dest.name,
            path=str(dest.resolve()),
            size=size,
            url=str(getattr(download, "url", "")),
            source="event",
        )

    # ------------------------------------------------------------------
    # Atomic: clipboard (7a R5)
    # ------------------------------------------------------------------

    async def _grant_clipboard(self) -> None:
        """Grant clipboard read/write once via CDP ``Browser.grantPermissions``."""
        if self._clipboard_granted:
            return
        origin = ""
        with contextlib.suppress(Exception):
            origin = str(self._page.url)
        cdp = await self._page.context.new_cdp_session(self._page)
        try:
            params: dict[str, Any] = {
                "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"],
            }
            if origin and origin.startswith(("http://", "https://")):
                params["origin"] = origin
            await cdp.send("Browser.grantPermissions", params)
        except Exception:
            logger.debug("clipboard_grant_failed", exc_info=True)
        finally:
            await cdp.detach()
        self._clipboard_granted = True

    async def _clipboard_read_impl(self) -> str:
        await self._grant_clipboard()
        try:
            result = await self._page.evaluate("navigator.clipboard.readText()")
        except Exception as exc:
            raise BackendError(
                error="clipboard_read_failed",
                hint=str(exc),
                action=(
                    "clipboard read requires headed or bridge mode"
                    " — headless Chromium denies clipboard-read;"
                    " use clipboard write or evaluate instead"
                ),
            ) from exc
        return str(result or "")

    async def _clipboard_write_impl(self, text: str) -> None:
        await self._grant_clipboard()
        try:
            await self._page.evaluate("(t) => navigator.clipboard.writeText(t)", text)
        except Exception as exc:
            raise BackendError(
                error="clipboard_write_failed",
                hint=str(exc),
                action="ensure the page is focused and clipboard access is allowed",
            ) from exc

    # ------------------------------------------------------------------
    # Atomic: PDF (7a R6)
    # ------------------------------------------------------------------

    async def _pdf_impl(self, options: dict[str, Any]) -> bytes:
        try:
            return await self._page.pdf(**options)
        except Exception as exc:
            msg = str(exc).lower()
            if "headless" in msg or "non-headless" in msg or "pdf" in msg:
                raise BackendError(
                    error="pdf_not_supported",
                    hint="PDF export requires headless Chromium",
                    action="restart the daemon with headless mode to export PDF",
                ) from exc
            raise BackendError(
                error="pdf_failed",
                hint=str(exc),
                action="check the PDF options (format, margin, pageRanges)",
            ) from exc

    # ------------------------------------------------------------------
    # Atomic: cookies CRUD (7a R3)
    # ------------------------------------------------------------------

    async def _cookies_set_impl(self, cookies: list[dict[str, Any]]) -> None:
        await self._get_browser_context().add_cookies(cookies)

    async def _cookies_clear_impl(self) -> None:
        await self._get_browser_context().clear_cookies()

    async def _cookies_delete_impl(self, name: str, *, domain: str | None) -> int:
        context = self._get_browser_context()
        existing: list[dict[str, Any]] = await context.cookies()
        # Playwright >=1.43 supports filtered clear_cookies(name=, domain=);
        # count the matches first so we can report how many were removed.
        matched = [
            c
            for c in existing
            if c.get("name") == name and (domain is None or c.get("domain") == domain)
        ]
        clear_kwargs: dict[str, Any] = {"name": name}
        if domain is not None:
            clear_kwargs["domain"] = domain
        try:
            await context.clear_cookies(**clear_kwargs)
        except TypeError:
            # Older Playwright without filtered clear: clear all, re-add the
            # cookies we did not want to delete.
            await context.clear_cookies()
            survivors = [c for c in existing if c not in matched]
            if survivors:
                await context.add_cookies(survivors)
        return len(matched)

    # ------------------------------------------------------------------
    # Atomic: dialog handle
    # ------------------------------------------------------------------

    async def _dialog_handle_impl(
        self, action: str, *, text: str | None = None
    ) -> dict[str, Any]:
        if self._dialog_object is None:
            return {}
        try:
            if action == "accept":
                if text is not None:
                    await self._dialog_object.accept(text)
                else:
                    await self._dialog_object.accept()
            else:
                await self._dialog_object.dismiss()
        except Exception as exc:
            logger.debug("dialog_handle_error", error=str(exc))
        self._dialog_object = None
        return {}

    # ------------------------------------------------------------------
    # Atomic: evaluate
    # ------------------------------------------------------------------

    async def _evaluate_impl(self, js: str, *, world: str) -> Any:
        if world == "main":
            return await self._evaluate_main_world(js)
        try:
            return await self._page.evaluate(js)
        except Exception as exc:
            raise BackendError(
                error="evaluate_failed",
                hint=str(exc),
                action="check JS syntax and page context",
            ) from exc

    async def _evaluate_main_world(self, js: str) -> Any:
        """Evaluate JS in the page's main execution context via CDP."""
        cdp = await self._page.context.new_cdp_session(self._page)
        try:
            contexts: list[dict[str, Any]] = []

            def _on_ctx(params: dict[str, Any]) -> None:
                contexts.append(params["context"])

            cdp.on("Runtime.executionContextCreated", _on_ctx)
            await cdp.send("Runtime.enable")

            main_ctx_id: int | None = None
            for ec in contexts:
                aux: dict[str, Any] = ec.get("auxData", {})
                if aux.get("isDefault") is True:
                    main_ctx_id = ec["id"]
                    break

            if main_ctx_id is None:
                raise BackendError(
                    error="evaluate_failed",
                    hint="could not find main world execution context",
                    action="ensure page is loaded before evaluating",
                )

            resp = await cdp.send(
                "Runtime.evaluate",
                {
                    "expression": js,
                    "contextId": main_ctx_id,
                    "returnByValue": True,
                    "awaitPromise": True,
                    "userGesture": True,
                },
            )
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(
                error="evaluate_failed",
                hint=str(exc),
                action="check JS syntax and page context",
            ) from exc
        finally:
            with contextlib.suppress(Exception):
                await cdp.send("Runtime.disable")
            await cdp.detach()

        if "exceptionDetails" in resp:
            desc = resp["exceptionDetails"].get("text", "JS exception")
            raise BackendError(
                error="evaluate_failed",
                hint=desc,
                action="check JS syntax and page context",
            )

        result_obj = resp.get("result", {})
        if result_obj.get("type") == "undefined":
            return None
        return result_obj.get("value")

    # ------------------------------------------------------------------
    # Atomic: screenshot
    # ------------------------------------------------------------------

    async def _screenshot_impl(
        self, *, full_page: bool, fmt: str, quality: int
    ) -> bytes:
        kwargs: dict[str, Any] = {"full_page": full_page, "type": fmt}
        if fmt == "jpeg":
            kwargs["quality"] = quality
        return await self._page.screenshot(**kwargs)

    # ------------------------------------------------------------------
    # Atomic: fetch (HTTP via browser cookies + UA)
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
        context = self._page.context

        cookies_raw: list[dict[str, Any]] = await context.cookies()
        cookie_jar = httpx.Cookies()
        for c in cookies_raw:
            cookie_jar.set(
                c["name"],
                c["value"],
                domain=c.get("domain", ""),
                path=c.get("path", "/"),
            )

        ua: str = await self._page.evaluate("navigator.userAgent")
        req_headers: dict[str, str] = {"User-Agent": ua}
        if headers:
            req_headers.update(headers)

        client_kwargs: dict[str, Any] = {
            "cookies": cookie_jar,
            "timeout": httpx.Timeout(timeout),
            "follow_redirects": True,
        }
        if self._proxy_url:
            client_kwargs["proxy"] = self._proxy_url

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.request(
                    method.upper(),
                    url,
                    headers=req_headers,
                    content=body.encode("utf-8") if body else None,
                )
        except httpx.TimeoutException as exc:
            raise BrowserTimeoutError(
                error="fetch_timeout",
                hint=f"HTTP request to {url} timed out after {timeout}s",
                action="retry with a larger 'timeout' value, or check the URL",
            ) from exc
        except httpx.RequestError as exc:
            raise BackendError(
                error="fetch_request_failed",
                hint=f"Request to {url} failed: {exc}",
                action="check URL and network connectivity",
            ) from exc

        content_type = resp.headers.get("content-type", "")
        is_binary = not (
            "text/" in content_type
            or "json" in content_type
            or "xml" in content_type
            or "javascript" in content_type
            or "html" in content_type
        )

        max_body = 100_000
        if is_binary:
            raw = resp.content
            if len(raw) > max_body:
                resp_body = base64.b64encode(raw[:max_body]).decode("ascii")
                truncated = True
            else:
                resp_body = base64.b64encode(raw).decode("ascii")
                truncated = False
            body_encoding = "base64"
        else:
            resp_body = resp.text
            truncated = len(resp_body) > max_body
            if truncated:
                resp_body = resp_body[:max_body] + "\n[...truncated...]"
            body_encoding = "text"

        parsed = urlparse(url)
        cookies_used = [
            c["name"]
            for c in cookies_raw
            if parsed.hostname
            and (
                parsed.hostname == c.get("domain", "")
                or parsed.hostname.endswith(c.get("domain", ""))
            )
        ]

        _useful_headers = {
            "content-type",
            "content-length",
            "content-encoding",
            "set-cookie",
            "location",
            "cache-control",
            "x-ratelimit-remaining",
            "x-ratelimit-limit",
            "retry-after",
            "www-authenticate",
        }
        filtered_headers = {
            k: v for k, v in resp.headers.items() if k.lower() in _useful_headers
        }

        parsed_body: Any = resp_body
        if body_encoding == "text" and "json" in content_type:
            import contextlib as _ctxlib
            import json as _json

            with _ctxlib.suppress(Exception):
                parsed_body = _json.loads(resp_body)

        return {
            "status": resp.status_code,
            "headers": filtered_headers,
            "body": parsed_body,
            "body_encoding": body_encoding,
            "truncated": truncated,
            "content_type": content_type,
            "cookies_used": cookies_used,
            "url": str(resp.url),
        }

    # ------------------------------------------------------------------
    # Atomic: tabs
    # ------------------------------------------------------------------

    def _get_browser_context(self) -> Any:
        """Return the Playwright BrowserContext, whether persistent or ephemeral."""
        if self._browser_context is not None:
            return self._browser_context
        return self._page.context

    async def _tab_list_impl(self) -> list[TabInfo]:
        result: list[TabInfo] = []
        for tid, page in self._tabs.items():
            try:
                url = page.url
            except Exception:
                url = ""
            try:
                title = await page.title()
            except Exception:
                title = ""
            result.append(
                TabInfo(
                    tab_id=tid,
                    url=url,
                    title=title,
                    active=(tid == self._active_tab),
                )
            )
        return result

    async def _tab_new_impl(self, url: str | None) -> dict[str, Any]:
        pw_ctx = self._get_browser_context()
        new_page = await pw_ctx.new_page()
        new_id = self._next_tab_id
        self._next_tab_id += 1
        self._tabs[new_id] = new_page
        self._active_tab = new_id
        self._active_frame = None
        self._setup_network_listeners(new_page)
        self._setup_feedback_listeners(new_page)

        result: dict[str, Any] = {"tab_id": new_id}
        if url:
            nav = await self.navigate(url)
            result["url"] = nav.get("url", url)
            result["title"] = nav.get("title", "")
        else:
            result["url"] = new_page.url
            try:
                result["title"] = await new_page.title()
            except Exception:
                result["title"] = ""
        return result

    async def _tab_close_impl(self, tab_id: int) -> dict[str, Any]:
        if tab_id not in self._tabs:
            raise ElementNotFoundError(
                error="tab_not_found",
                hint=(
                    f"Tab {tab_id} does not exist"
                    f" (open tabs: {sorted(self._tabs.keys())})"
                ),
                action="use 'tab list' to see available tab IDs",
            )
        page = self._tabs.pop(tab_id)
        if self._browser_context is not None:
            pw_ctx = self._browser_context
        else:
            pw_ctx = page.context
        # Drop any persistent CDP session bound to this tab before the page
        # goes away, so a reverse-engineering manager can't reuse a dead
        # session on a recycled tab_id.
        await self._invalidate_cdp_session(tab_id)
        await page.close()

        if not self._tabs:
            new_page = await pw_ctx.new_page()
            new_id = self._next_tab_id
            self._next_tab_id += 1
            self._tabs[new_id] = new_page
            self._active_tab = new_id
            self._active_frame = None
            self._setup_network_listeners(new_page)
            self._setup_feedback_listeners(new_page)
            return {"closed": tab_id, "auto_created": new_id}

        if self._active_tab == tab_id:
            self._active_tab = max(self._tabs.keys())
            self._active_frame = None

        return {"closed": tab_id}

    async def _tab_switch_impl(self, tab_id: int) -> dict[str, Any]:
        if tab_id not in self._tabs:
            raise ElementNotFoundError(
                error="tab_not_found",
                hint=(
                    f"Tab {tab_id} does not exist"
                    f" (open tabs: {sorted(self._tabs.keys())})"
                ),
                action="use 'tab list' to see available tab IDs",
            )
        self._active_tab = tab_id
        self._active_frame = None
        page = self._tabs[tab_id]
        try:
            url = page.url
        except Exception:
            url = ""
        try:
            title = await page.title()
        except Exception:
            title = ""
        return {"tab_id": tab_id, "url": url, "title": title}

    # ------------------------------------------------------------------
    # Atomic: frames
    # ------------------------------------------------------------------

    async def _frame_list_impl(self) -> list[FrameInfo]:
        frames = self._page.frames
        result: list[FrameInfo] = []
        for frame in frames:
            is_current = frame == (
                self._active_frame
                if self._active_frame is not None
                else self._page.main_frame
            )
            is_main = frame == self._page.main_frame
            fname = frame.name or "(main)" if is_main else frame.name or ""
            result.append(
                FrameInfo(
                    name=fname,
                    url=frame.url,
                    is_current=is_current,
                )
            )
        return result

    async def _frame_focus_impl(
        self, *, name: str | None, url: str | None, main: bool
    ) -> dict[str, Any]:
        if main:
            self._active_frame = None
            return {
                "ok": True,
                "action": "frame_focus",
                "frame": "(main)",
                "url": self._page.main_frame.url,
            }

        target_frame = None
        if name:
            target_frame = self._page.frame(name=name)
        elif url:
            kind, processed = classify_url_pattern(url)
            if kind == "glob":
                # Native Playwright glob ('*' does not cross '/').
                target_frame = self._page.frame(url=processed)
            else:
                # Substring fallback so '--url "default.asp"' just works.
                for frame in self._page.frames:
                    if match_url_substring(url, frame.url or ""):
                        target_frame = frame
                        break

        if target_frame is None:
            available = [f.name or f.url[:60] for f in self._page.frames]
            raise BackendError(
                error="frame_not_found",
                hint=f"No frame matching name={name!r} url={url!r}",
                action=f"available frames: {available}",
            )

        self._active_frame = target_frame
        return {
            "ok": True,
            "action": "frame_focus",
            "frame": target_frame.name or "(unnamed)",
            "url": target_frame.url,
        }

    # ------------------------------------------------------------------
    # Persistent CDP session (7b) — event-stream transport for managers
    # ------------------------------------------------------------------

    async def _get_or_create_cdp_session(self) -> Any:
        """Return the active tab's persistent ``CDPSession``, creating once.

        Cache hit returns the existing session; on miss we open a new session
        bound to the active page and wire its generic ``"event"`` listener
        into :meth:`_dispatch_cdp_event`. Playwright's ``CDPSession`` emits two
        signals per CDP event — the method name itself and the catch-all
        ``"event"`` (carrying ``{"method", "params"}``); we subscribe to the
        latter so a single forward covers every domain the managers enable.
        """
        tab_id = self._active_tab
        existing = self._cdp_sessions.get(tab_id)
        if existing is not None:
            return existing
        session = await self._page.context.new_cdp_session(self._page)

        def _forward(event: dict[str, Any]) -> None:
            self._dispatch_cdp_event(event.get("method", ""), event.get("params", {}))

        session.on("event", _forward)
        self._cdp_sessions[tab_id] = session
        return session

    async def _invalidate_cdp_session(self, tab_id: int) -> None:
        """Detach and forget the persistent CDP session for ``tab_id``.

        Called when a tab closes so a stale session bound to a dead page is
        never reused. Detach failures are swallowed — the session is already
        being torn down with its page, and a half-closed session must not
        block tab cleanup.
        """
        session = self._cdp_sessions.pop(tab_id, None)
        if session is None:
            return
        with contextlib.suppress(Exception):
            await session.detach()

    async def _cdp_send_impl(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        session = await self._get_or_create_cdp_session()
        try:
            raw: dict[str, Any] | None = await session.send(method, params)
            return dict(raw) if raw is not None else {}
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(
                error="cdp_call_failed",
                hint=f"{method}: {exc}",
                action="check CDP method name and parameters",
            ) from exc

    async def _cdp_enable_domain_impl(self, domain: str) -> None:
        session = await self._get_or_create_cdp_session()
        try:
            await session.send(f"{domain}.enable")
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(
                error="cdp_domain_enable_failed",
                hint=f"{domain}.enable: {exc}",
                action=f"check that the '{domain}' CDP domain is supported",
            ) from exc

    # ------------------------------------------------------------------
    # Header injection (7b T1.2)
    # ------------------------------------------------------------------

    async def _set_extra_headers_impl(self, headers: dict[str, str]) -> None:
        await self._page.set_extra_http_headers(headers)

    # ------------------------------------------------------------------
    # Route interception (7b T1.3)
    # ------------------------------------------------------------------
    # We register a single catch-all ``page.route("**/*")`` handler keyed by
    # the rule's pattern string. The handler defers the actual match decision
    # to the shared :class:`RouteManager` so abort/fulfill/continue semantics
    # and field precedence are identical to the RemoteBridge backend (DRY —
    # one matcher, two transports). Keying by pattern lets ``unroute`` target a
    # single rule without disturbing the others.

    async def _route_add_impl(self, rule: Any) -> None:
        glob = self._route_glob(rule.pattern)

        async def _handler(route: Any, request: Any) -> None:
            await self._apply_route(route, request, rule)

        # Stash the handler so a later ``page.unroute`` can pass the same
        # callable (Playwright matches handlers by identity).
        self._route_handlers[rule.pattern] = _handler
        await self._page.route(glob, _handler)

    async def _route_remove_impl(self, pattern: str | None) -> None:
        if pattern is None:
            for pat, handler in list(self._route_handlers.items()):
                with contextlib.suppress(Exception):
                    await self._page.unroute(self._route_glob(pat), handler)
            self._route_handlers.clear()
            return
        handler = self._route_handlers.pop(pattern, None)
        with contextlib.suppress(Exception):
            await self._page.unroute(self._route_glob(pattern), handler)

    @staticmethod
    def _route_glob(pattern: str) -> str:
        """Map a rule pattern to a Playwright route glob.

        A bare substring rule (no ``*``) becomes ``*<substr>*`` so Playwright's
        matcher fires; the precise disposition is still decided by the shared
        RouteManager matcher inside the handler. Patterns that already contain
        ``*`` are passed through unchanged.
        """
        return pattern if "*" in pattern else f"*{pattern}*"

    async def _apply_route(self, route: Any, request: Any, rule: Any) -> None:
        """Execute ``rule`` against a paused Playwright request."""
        # Re-check via the shared matcher: the catch-all glob can over-match
        # (e.g. ``*api*`` also matches ``api`` rules with stricter method /
        # resource filters), so confirm the full rule actually applies before
        # acting; otherwise let the request continue untouched.
        applicable = self.route_manager.match(
            request.url,
            resource_type=getattr(request, "resource_type", None),
            method=getattr(request, "method", None),
        )
        if applicable is None or applicable.pattern != rule.pattern:
            with contextlib.suppress(Exception):
                await route.fallback()
            return

        if rule.action == "abort":
            await route.abort()
            return
        if rule.action == "fulfill":
            kwargs: dict[str, Any] = {"status": rule.status or 200}
            if rule.content_type:
                kwargs["content_type"] = rule.content_type
            if rule.body is not None:
                kwargs["body"] = rule.body
            await route.fulfill(**kwargs)
            return
        # "continue" — let it proceed unmodified.
        await route.continue_()

    # ------------------------------------------------------------------
    # Atomic: raw CDP / close
    # ------------------------------------------------------------------

    async def _raw_cdp_impl(self, method: str, params: dict[str, Any] | None) -> Any:
        cdp = await self._page.context.new_cdp_session(self._page)
        try:
            return await cdp.send(method, params or {})
        except Exception as exc:
            raise BackendError(
                error="cdp_call_failed",
                hint=f"{method}: {exc}",
                action="check CDP method name and parameters",
            ) from exc
        finally:
            await cdp.detach()

    async def _close_impl(self) -> None:
        # Detach persistent CDP sessions first. Closing the browser/context
        # below tears them down anyway, but detaching explicitly avoids a
        # spurious "session orphaned" warning on a still-attached session and
        # keeps the cache from outliving the browser if close() is retried.
        for tab_id in list(self._cdp_sessions.keys()):
            await self._invalidate_cdp_session(tab_id)
        if self._browser is not None:
            await self._browser.close()
        elif self._browser_context is not None:
            await self._browser_context.close()
        if self._playwright is not None:
            await self._playwright.stop()


async def launch_playwright(
    *,
    headless: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    profile_dir: Path | None = None,
    proxy_url: str | None = None,
    browser_proxy: str | None = None,
    extra_args: list[str] | None = None,
    browser_config: BrowserConfig | None = None,
) -> PlaywrightContext:
    """Launch a Playwright browser and return a context.

    See :func:`agentcloak.browser.create_context` for the rationale on
    keeping ``proxy_url`` (httpcloak/fetch) and ``browser_proxy``
    (Chromium upstream) as two separate parameters.
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    executable = _find_chromium()

    cdp_port = find_free_port()
    # User-supplied ``extra_args`` come after agentcloak defaults so the
    # last-occurrence-wins Chromium semantics let users override
    # anything we set.
    chrome_args = [
        "--no-sandbox",
        f"--remote-debugging-port={cdp_port}",
        *(extra_args or []),
    ]

    proxy_kwargs: dict[str, Any] = {}
    if browser_proxy:
        proxy_kwargs["proxy"] = {"server": browser_proxy}

    if profile_dir is not None:
        launch_kwargs: dict[str, Any] = {
            "headless": headless,
            "args": chrome_args,
            "viewport": {"width": viewport_width, "height": viewport_height},
            **proxy_kwargs,
        }
        if executable:
            launch_kwargs["executable_path"] = executable

        try:
            browser_context = await pw.chromium.launch_persistent_context(
                str(profile_dir),
                **launch_kwargs,
            )
        except Exception as exc:
            await pw.stop()
            raise BackendError(
                error="browser_launch_failed",
                hint=str(exc),
                action="run 'playwright install chromium' or install system chromium",
            ) from exc

        pages = browser_context.pages
        page = pages[0] if pages else await browser_context.new_page()

        seq_counter = SeqCounter()
        ring_buffer = RingBuffer()

        return PlaywrightContext(
            page=page,
            browser=None,
            playwright=pw,
            seq_counter=seq_counter,
            ring_buffer=ring_buffer,
            browser_context=browser_context,
            proxy_url=proxy_url,
            cdp_port=cdp_port,
            browser_config=browser_config,
        )

    launch_args: dict[str, Any] = {
        "headless": headless,
        "args": chrome_args,
        **proxy_kwargs,
    }
    if executable:
        launch_args["executable_path"] = executable

    try:
        browser = await pw.chromium.launch(**launch_args)
    except Exception as exc:
        await pw.stop()
        raise BackendError(
            error="browser_launch_failed",
            hint=str(exc),
            action="run 'playwright install chromium' or install system chromium",
        ) from exc

    ctx = await browser.new_context(
        viewport={"width": viewport_width, "height": viewport_height}
    )
    page = await ctx.new_page()

    seq_counter = SeqCounter()
    ring_buffer = RingBuffer()

    return PlaywrightContext(
        page=page,
        browser=browser,
        playwright=pw,
        seq_counter=seq_counter,
        ring_buffer=ring_buffer,
        proxy_url=proxy_url,
        cdp_port=cdp_port,
        browser_config=browser_config,
    )
