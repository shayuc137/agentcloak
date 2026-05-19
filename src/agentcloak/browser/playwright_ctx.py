"""PlaywrightContext — browser backend implemented on top of BrowserContextBase.

The class only implements the small set of atomic methods declared by the base
class (``_navigate_impl``, ``_click_impl``, ``_get_ax_tree`` etc.). The
orchestrators (``action()``, ``snapshot()``, ``action_batch()``, ``wait()``,
``upload()``, ``fetch()``) all live in ``BrowserContextBase``.

What stays here:
- Playwright-specific event listeners (network, dialog, framenavigated, etc.)
- Multi-tab bookkeeping (pages keyed by tab_id)
- CDP-driven element resolution (backendDOMNodeId → data-cloak-ref marker)
- Backend factory ``launch_playwright`` and the ``screenshot_to_base64`` helper
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import shutil
import socket
from datetime import UTC
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from agentcloak.browser._snapshot_builder import FrameData
from agentcloak.browser.base import (
    BrowserContextBase,
    classify_url_pattern,
    match_url_substring,
)
from agentcloak.browser.state import (
    FrameInfo,
    PendingDialog,
    TabInfo,
)
from agentcloak.core.capture import CaptureEntry, CaptureStore
from agentcloak.core.errors import (
    BackendError,
    BrowserTimeoutError,
    ElementNotFoundError,
    NavigationError,
)
from agentcloak.core.seq import RingBuffer, SeqCounter, SeqEvent
from agentcloak.core.types import StealthTier

__all__ = ["PlaywrightContext", "launch_playwright", "screenshot_to_base64"]

logger = structlog.get_logger()

_SNAP_CHROMIUM = "/snap/chromium/current/usr/lib/chromium-browser/chrome"


def screenshot_to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def find_free_port() -> int:
    """Bind to port 0 and return the OS-assigned free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
    ) -> None:
        super().__init__(
            seq_counter=seq_counter,
            ring_buffer=ring_buffer,
            capture_store=capture_store,
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

    def _on_request_start(self, _request: Any) -> None:
        self._pending_request_count += 1

    def _on_request_end(self, _request: Any) -> None:
        if self._pending_request_count > 0:
            self._pending_request_count -= 1

    def _on_dialog(self, dialog: Any) -> None:
        dtype = dialog.type
        if dtype in ("alert", "beforeunload"):
            self._last_auto_dialog = {
                "type": dtype,
                "message": dialog.message,
            }
            _accept_task = asyncio.ensure_future(dialog.accept())
            self._pending_captures.add(_accept_task)
            _accept_task.add_done_callback(self._pending_captures.discard)
            logger.info(
                "dialog_auto_accepted",
                dialog_type=dtype,
                message=dialog.message[:100],
            )
        else:
            self._pending_dialog = PendingDialog(
                dialog_type=dtype,
                message=dialog.message,
                default_value=dialog.default_value or "",
                url=self._page.url,
            )
            self._dialog_object = dialog
            logger.info(
                "dialog_pending",
                dialog_type=dtype,
                message=dialog.message[:100],
            )

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
            from datetime import datetime

            from agentcloak.core.capture import is_recordable_content, truncate_body

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

            resp_body: str | None = None
            if is_recordable_content(content_type):
                try:
                    raw = await response.body()
                    resp_body = truncate_body(raw.decode("utf-8", errors="replace"))
                except Exception:
                    pass

            entry = CaptureEntry(
                seq=self._seq_counter.value,
                timestamp=datetime.now(UTC).isoformat(),
                method=request.method,
                url=request.url,
                status=response.status,
                resource_type=request.resource_type,
                request_headers=req_headers,
                response_headers=resp_headers,
                request_body=req_body,
                response_body=resp_body,
                content_type=content_type,
                duration_ms=0.0,
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
    )
