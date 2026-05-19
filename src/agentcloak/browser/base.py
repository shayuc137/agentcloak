"""BrowserContextBase — ABC for browser backends.

Both PlaywrightAdapter and RemoteBridgeAdapter inherit from this base, which
owns the shared behavior (action dispatch, element resolution, feedback
collection, snapshot caching, frame tracking, seq + ring buffer state).

Subclasses implement a small set of atomic operations — see the ``@abstractmethod``
decorators below. Everything else (the ``action()`` orchestrator, the
``action_batch()`` runner, the ``dialog_status()`` accessor, etc.) lives here so
adding a new feature only touches one place.

The contract used to be a ``typing.Protocol`` with 21 method signatures and no
shared behavior. Two backends ended up duplicating ~500 lines of action
dispatch logic each. Promoting to an ABC keeps the same public surface but
moves the common machinery into one file.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import re
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import structlog

from agentcloak.browser.state import (
    ElementRef,
    FrameInfo,
    PageSnapshot,
    PendingDialog,
    TabInfo,
)
from agentcloak.core.capture import CaptureStore
from agentcloak.core.errors import (
    BackendError,
    BrowserTimeoutError,
    DialogBlockedError,
    ElementNotFoundError,
)
from agentcloak.core.seq import RingBuffer, SeqCounter, SeqEvent

if TYPE_CHECKING:
    from agentcloak.core.types import StealthTier

__all__ = [
    "BrowserContextBase",
    "classify_url_pattern",
    "match_url_glob",
    "match_url_substring",
]

logger = structlog.get_logger()


# Action kinds the base class will dispatch via subclass _xxx_impl() methods.
_VALID_ACTION_KINDS: frozenset[str] = frozenset(
    {
        "click",
        "fill",
        "type",
        "scroll",
        "hover",
        "select",
        "press",
        "keydown",
        "keyup",
    }
)


# Markers used by base-level browser self-healing heuristic. We can't import
# Playwright's error types here (the base must stay backend-agnostic), so we
# match on substrings of the exception message instead.
_BROWSER_CLOSED_HINTS: tuple[str, ...] = (
    "target closed",
    "browser has been closed",
    "browser closed",
    "websocket connection closed",
    "connection lost",
    "browser disconnected",
    "page closed",
)


def _looks_like_browser_closed(exc: BaseException) -> bool:
    """Return True if the exception message smells like a closed browser/page."""
    msg = str(exc).lower()
    return any(hint in msg for hint in _BROWSER_CLOSED_HINTS)


def classify_url_pattern(pattern: str) -> tuple[str, str]:
    """Classify a user-supplied URL pattern.

    Used by ``wait --url`` and ``frame focus --url`` so both surfaces agree
    on what ``"*"`` and ``"glob:"`` mean. The native Playwright glob does
    not let ``*`` cross ``/``, which surprised users who wrote
    ``--url "*example*"`` expecting "contains".

    Returns ``("glob", processed_pattern)`` when the caller should pass the
    pattern to Playwright's glob engine, or ``("substring", processed_pattern)``
    when the caller should fall back to ``processed_pattern in url``.

    Rules:

    * ``"glob:<pat>"`` strips the prefix and routes to native glob (escape
      hatch for users who want exact glob semantics).
    * ``*`` strictly inside the pattern (i.e. surviving ``strip("*")``)
      signals real glob intent → native glob.
    * Pure text, ``"*xxx"``, ``"xxx*"``, or ``"*xxx*"`` → substring match
      on the ``*``-stripped value. This covers the common
      ``--url "callback?code="`` and ``--url "*dashboard*"`` cases.
    * ``?`` is always treated as a literal — it normally appears in URL
      query strings, not as a glob single-character wildcard.
    """
    if pattern.startswith("glob:"):
        return ("glob", pattern[len("glob:") :])
    stripped = pattern.strip("*")
    if "*" in stripped:
        return ("glob", pattern)
    return ("substring", stripped)


def match_url_substring(pattern: str, url: str) -> bool:
    """Substring URL match honouring leading/trailing ``*`` as plain text.

    Companion to :func:`classify_url_pattern` for the ``substring`` branch.
    Polling loops in subclasses call this once per iteration so the
    behaviour stays identical across backends.
    """
    return pattern.strip("*") in url


def match_url_glob(pattern: str, url: str) -> bool:
    """Playwright-compatible glob match.

    Used by backends that don't have Playwright's glob matcher in-process
    (RemoteBridge). Mirrors the rules ``classify_url_pattern`` assumes:

    * ``*`` matches any character except ``/``
    * ``**`` matches any character including ``/``
    * ``?`` is **literal** (URL query strings, not single-char wildcard)
    * Everything else is regex-escaped
    * Implicit ``^...$`` anchoring so ``"example.com"`` doesn't match
      ``"https://example.com.evil/"``
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.fullmatch("".join(out), url) is not None


class BrowserContextBase(ABC):
    """Browser context with shared behavior. Subclasses implement raw operations."""

    def __init__(
        self,
        *,
        seq_counter: SeqCounter | None = None,
        ring_buffer: RingBuffer | None = None,
        capture_store: CaptureStore | None = None,
    ) -> None:
        # --- Shared state ---
        self._seq_counter: SeqCounter = seq_counter or SeqCounter()
        self._ring_buffer: RingBuffer = ring_buffer or RingBuffer()
        # Default empty store so the remote backend (which doesn't capture
        # natively) still satisfies ``ctx.capture_store`` access from the
        # daemon. Subclasses can replace it with a real store.
        self._capture_store: CaptureStore = capture_store or CaptureStore()

        # Element + snapshot caches populated by snapshot()
        self._selector_map: dict[int, ElementRef] = {}
        self._backend_node_map: dict[int, int] = {}
        self._cached_lines: list[tuple[int, str, int | None]] = []
        self._cached_mode: str = ""

        # R0: Proactive State Feedback transient state.
        self._pending_request_count: int = 0
        self._last_navigation_event: dict[str, str] | None = None
        self._last_new_tab_event: dict[str, Any] | None = None
        self._last_download_event: dict[str, str] | None = None
        self._last_auto_dialog: dict[str, str] | None = None

        # R1: Dialog handling.
        self._pending_dialog: PendingDialog | None = None

        # R5: Frame switching — active frame state. Subclasses interpret the
        # value (e.g. PlaywrightAdapter stores a Frame object, RemoteBridge
        # stores a frameId string).
        self._active_frame: Any = None

        # Track whether the underlying browser has been observed closed so the
        # next request can raise a structured error instead of a raw exception.
        self._browser_closed: bool = False

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def seq(self) -> int:
        return self._seq_counter.value

    @property
    def capture_store(self) -> CaptureStore:
        """Network capture store. Always present so daemon code can rely on it."""
        return self._capture_store

    @property
    @abstractmethod
    def stealth_tier(self) -> StealthTier: ...

    def browser_description(self) -> str:
        """Return a short human-readable description of this backend.

        Format: ``<Name> <version>`` (e.g. ``CloakBrowser 0.3.5``,
        ``Playwright Chromium``). Default falls back to ``Unknown`` so missing
        overrides surface visibly in doctor output rather than silently
        defaulting to one specific backend's label.

        The method is intentionally a method (not a property) and instance-bound
        — multi-session deployments will eventually have multiple ``ctx``
        instances per daemon, each describing its own browser. Hardcoded
        class-level strings would have made that future awkward.
        """
        return "Unknown"

    # ------------------------------------------------------------------
    # Atomic methods — subclasses must implement
    # ------------------------------------------------------------------

    @abstractmethod
    async def _navigate_impl(self, url: str, *, timeout: float) -> dict[str, Any]: ...

    @abstractmethod
    async def _get_ax_tree(self, *, frames: bool = False) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def _get_child_frame_trees(self) -> list[Any]:
        """Return list[FrameData] from the snapshot builder."""

    @abstractmethod
    async def _get_page_info(self) -> tuple[str, str]:
        """Return (url, title) for the current active page."""

    # --- Action atomics ---

    @abstractmethod
    async def _click_impl(
        self,
        *,
        target: str,
        x: float | None,
        y: float | None,
        button: str,
        click_count: int,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def _fill_impl(self, *, target: str, text: str) -> dict[str, Any]: ...

    @abstractmethod
    async def _type_impl(
        self, *, target: str, text: str, delay: float
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def _scroll_impl(
        self,
        *,
        target: str,
        direction: str,
        amount: int,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def _hover_impl(
        self,
        *,
        target: str,
        x: float | None,
        y: float | None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def _select_impl(
        self,
        *,
        target: str,
        value: str | None,
        label: str | None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def _press_impl(self, *, target: str, key: str) -> dict[str, Any]: ...

    @abstractmethod
    async def _keydown_impl(self, *, key: str) -> dict[str, Any]: ...

    @abstractmethod
    async def _keyup_impl(self, *, key: str) -> dict[str, Any]: ...

    @abstractmethod
    async def _evaluate_impl(self, js: str, *, world: str) -> Any: ...

    @abstractmethod
    async def _screenshot_impl(
        self, *, full_page: bool, fmt: str, quality: int
    ) -> bytes: ...

    @abstractmethod
    async def _close_impl(self) -> None: ...

    @abstractmethod
    async def _raw_cdp_impl(
        self, method: str, params: dict[str, Any] | None
    ) -> Any: ...

    @abstractmethod
    async def _tab_list_impl(self) -> list[TabInfo]: ...

    @abstractmethod
    async def _tab_new_impl(self, url: str | None) -> dict[str, Any]: ...

    @abstractmethod
    async def _tab_close_impl(self, tab_id: int) -> dict[str, Any]: ...

    @abstractmethod
    async def _tab_switch_impl(self, tab_id: int) -> dict[str, Any]: ...

    @abstractmethod
    async def _frame_list_impl(self) -> list[FrameInfo]: ...

    @abstractmethod
    async def _frame_focus_impl(
        self, *, name: str | None, url: str | None, main: bool
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def _upload_impl(self, index: int, files: list[str]) -> dict[str, Any]: ...

    @abstractmethod
    async def _fetch_impl(
        self,
        url: str,
        *,
        method: str,
        body: str | None,
        headers: dict[str, str] | None,
        timeout: float,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def _dialog_handle_impl(
        self, action: str, *, text: str | None
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def _wait_impl(
        self,
        *,
        condition: str,
        value: str,
        timeout: int,
        state: str,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def _snapshot_dom_impl(self) -> str: ...

    async def _snapshot_content_impl(self) -> str:
        """Deprecated: content mode now uses the unified AX-tree path.

        Kept as a non-abstract fallback so existing subclasses compile
        without changes. New backends should not override this.
        """
        return ""

    @abstractmethod
    async def _network_entries(self, *, since_seq: int) -> list[dict[str, Any]]: ...

    # ------------------------------------------------------------------
    # Browser self-healing
    # ------------------------------------------------------------------

    def _maybe_mark_browser_closed(self, exc: BaseException) -> None:
        if _looks_like_browser_closed(exc):
            self._browser_closed = True
            logger.warning("browser_closed_detected", error=str(exc))

    def _check_browser_alive(self) -> None:
        """Raise structured error if we've seen the browser go away."""
        if self._browser_closed:
            raise BackendError(
                error="browser_closed",
                hint="The browser process has been closed or disconnected",
                action="daemon will recover on next launch; restart with"
                " 'agentcloak daemon start' or reissue from the CLI",
            )

    # ------------------------------------------------------------------
    # navigate / snapshot / evaluate / network / screenshot
    # ------------------------------------------------------------------

    async def navigate(
        self, url: str, *, timeout: float | None = None
    ) -> dict[str, Any]:
        self._check_browser_alive()
        if timeout is None:
            from agentcloak.core.config import load_config

            _, _cfg = load_config()
            timeout = float(_cfg.navigation_timeout)
        try:
            result = await self._navigate_impl(url, timeout=timeout)
        except Exception as exc:
            self._maybe_mark_browser_closed(exc)
            raise

        new_seq = self._seq_counter.increment_action()
        self._ring_buffer.append(
            SeqEvent(seq=new_seq, kind="navigate", data={"url": url})
        )
        logger.info("audit_action", action="navigate", seq=new_seq, url=url)
        result.setdefault("seq", new_seq)
        return result

    # ------------------------------------------------------------------
    # Resume snapshot — backend-agnostic session state probe
    # ------------------------------------------------------------------

    async def resume_snapshot(self) -> dict[str, Any]:
        """Return live session data used to update ``resume.json``.

        Subclasses override to surface backend-specific state (active page
        URL, open tab list, etc.) without forcing the daemon to introspect
        private attributes. ``url`` and ``title`` represent the *active*
        tab; ``tabs`` is the full per-backend tab inventory.

        Returns a dict with keys: ``url``, ``title``, ``tabs``,
        ``capture_active``, ``stealth_tier``. Missing keys default to
        empty / ``False``.
        """
        return {
            "url": "",
            "title": "",
            "tabs": [],
            "capture_active": self._capture_store.recording,
            "stealth_tier": self.stealth_tier.value,
        }

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
        self._check_browser_alive()
        # ``content`` joins ``accessible``/``compact`` on the unified AX-tree
        # path so it picks up StaticText aggregation (proper word spacing) and
        # ``--frames`` iframe merging for free. The old
        # ``document.body.innerText`` shortcut left inline-element boundaries
        # without separators ("Hacker Newsnew") and bypassed every other
        # snapshot feature; ``_snapshot_content_impl`` is now dead code kept
        # only so ABC subclasses don't drift.
        if mode in ("accessible", "compact", "content"):
            return await self._build_tree_snapshot(
                mode=mode,
                max_nodes=max_nodes,
                max_chars=max_chars,
                focus=focus,
                offset=offset,
                frames=frames,
            )
        if mode == "dom":
            html = await self._snapshot_dom_impl()
            url, title = await self._get_page_info()
            return PageSnapshot(
                seq=self._seq_counter.value,
                url=url,
                title=title,
                mode="dom",
                tree_text=html,
            )
        raise BackendError(
            error="invalid_snapshot_mode",
            hint=f"Unknown mode: {mode}",
            action="use one of: accessible, compact, dom, content",
        )

    async def _build_tree_snapshot(
        self,
        *,
        mode: str,
        max_nodes: int,
        max_chars: int,
        focus: int,
        offset: int,
        frames: bool,
    ) -> PageSnapshot:
        from agentcloak.browser._snapshot_builder import FrameData, build_snapshot

        raw_nodes = await self._get_ax_tree(frames=frames)
        frame_trees: list[FrameData] | None = None
        if frames:
            collected = await self._get_child_frame_trees()
            frame_trees = list(collected) if collected else None

        url, title = await self._get_page_info()

        result = build_snapshot(
            raw_nodes,
            mode=mode,
            max_nodes=max_nodes,
            max_chars=max_chars,
            focus=focus,
            offset=offset,
            seq=self._seq_counter.value,
            url=url,
            title=title,
            frame_trees=frame_trees,
        )
        self._backend_node_map = result.backend_node_map
        self._selector_map = result.selector_map
        self._cached_lines = result.cached_lines
        self._cached_mode = mode
        return result.snapshot

    async def evaluate(self, js: str, *, world: str = "main") -> Any:
        self._check_browser_alive()
        try:
            result = await self._evaluate_impl(js, world=world)
        except Exception as exc:
            self._maybe_mark_browser_closed(exc)
            raise

        new_seq = self._seq_counter.increment_action()
        self._ring_buffer.append(
            SeqEvent(seq=new_seq, kind="evaluate", data={"js": js[:200]})
        )
        url, _ = await self._get_page_info()
        logger.info(
            "audit_action",
            action="evaluate",
            seq=new_seq,
            js_length=len(js),
            url=url,
        )
        return result

    async def network(
        self, *, since: int | str = "last_action"
    ) -> list[dict[str, Any]]:
        if since == "last_action":
            since_seq = self._seq_counter.last_action_seq
        else:
            since_seq = int(since)
        # Some adapters (Playwright) collect from the ring buffer; remote bridge
        # may have its own queue. Default implementation walks the ring buffer.
        events = self._ring_buffer.since(since_seq)
        base = [e.data for e in events if e.kind == "network"]
        extra = await self._network_entries(since_seq=since_seq)
        return [*base, *extra] if extra else base

    async def screenshot(
        self,
        *,
        full_page: bool = False,
        format: str = "jpeg",
        quality: int | None = None,
    ) -> bytes:
        self._check_browser_alive()
        if quality is None:
            from agentcloak.core.config import load_config

            _, _cfg = load_config()
            quality = _cfg.screenshot_quality
        try:
            return await self._screenshot_impl(
                full_page=full_page, fmt=format, quality=quality
            )
        except Exception as exc:
            self._maybe_mark_browser_closed(exc)
            raise

    # ------------------------------------------------------------------
    # Dialog handling
    # ------------------------------------------------------------------

    def _raise_if_dialog_blocked(self) -> None:
        """Raise :class:`DialogBlockedError` if a dialog is currently pending.

        Previously this returned a dict that callers had to inspect, which
        meant every layer above (action dispatch, batch, daemon route,
        exception handler) duplicated the ``error == "blocked_by_dialog"``
        check. Raising lets the FastAPI exception handler do the translation
        in one place.
        """
        if self._pending_dialog is None:
            return
        d = self._pending_dialog
        dialog: dict[str, Any] = {
            "type": d.dialog_type,
            "message": d.message,
        }
        if d.default_value:
            dialog["default_value"] = d.default_value
        raise DialogBlockedError(
            error="blocked_by_dialog",
            hint="A dialog is pending — handle it before continuing",
            action="use 'dialog accept' or 'dialog dismiss'",
            dialog=dialog,
        )

    async def dialog_status(self) -> PendingDialog | None:
        return self._pending_dialog

    async def dialog_handle(
        self, action_type: str, *, text: str | None = None
    ) -> dict[str, Any]:
        if self._pending_dialog is None:
            return {"ok": True, "handled": False, "message": "no pending dialog"}

        dialog_info = {
            "type": self._pending_dialog.dialog_type,
            "message": self._pending_dialog.message,
        }
        await self._dialog_handle_impl(action_type, text=text)
        self._pending_dialog = None

        new_seq = self._seq_counter.increment_action()
        self._ring_buffer.append(
            SeqEvent(
                seq=new_seq,
                kind="dialog",
                data={"action": action_type, **dialog_info},
            )
        )
        return {
            "ok": True,
            "handled": True,
            "action": action_type,
            "dialog": dialog_info,
            "seq": new_seq,
        }

    # ------------------------------------------------------------------
    # Wait
    # ------------------------------------------------------------------

    async def wait(
        self,
        *,
        condition: str,
        value: str = "",
        timeout: int | None = None,
        state: str = "visible",
    ) -> dict[str, Any]:
        if timeout is None:
            from agentcloak.core.config import load_config

            _, _cfg = load_config()
            timeout = _cfg.action_timeout
        self._check_browser_alive()
        t0 = time.monotonic()
        try:
            await self._wait_impl(
                condition=condition, value=value, timeout=timeout, state=state
            )
        except BackendError:
            raise
        except BrowserTimeoutError:
            raise
        except Exception as exc:
            self._maybe_mark_browser_closed(exc)
            if "timeout" in str(exc).lower():
                raise BrowserTimeoutError(
                    error="wait_timeout",
                    hint=f"Wait condition '{condition}' timed out after {timeout}ms",
                    action="increase timeout or check the condition",
                ) from exc
            raise BackendError(
                error="wait_failed",
                hint=str(exc),
                action="check the wait condition value",
            ) from exc

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        new_seq = self._seq_counter.increment_action()
        self._ring_buffer.append(
            SeqEvent(
                seq=new_seq,
                kind="wait",
                data={"condition": condition, "value": value},
            )
        )
        return {
            "ok": True,
            "action": "wait",
            "condition": condition,
            "elapsed_ms": elapsed_ms,
            "seq": new_seq,
        }

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    async def upload(self, index: int, files: list[str]) -> dict[str, Any]:
        from pathlib import Path

        self._check_browser_alive()
        validated: list[str] = []
        for f in files:
            p = Path(f)
            if not p.is_file():
                raise BackendError(
                    error="upload_file_not_found",
                    hint=f"File not found: {f}",
                    action="check the file path and permissions",
                )
            validated.append(str(p.resolve()))

        try:
            await self._upload_impl(index, validated)
        except Exception as exc:
            self._maybe_mark_browser_closed(exc)
            raise

        new_seq = self._seq_counter.increment_action()
        self._ring_buffer.append(
            SeqEvent(
                seq=new_seq,
                kind="upload",
                data={
                    "index": index,
                    "files": [Path(f).name for f in validated],
                },
            )
        )
        url, _ = await self._get_page_info()
        logger.info(
            "audit_action",
            action="upload",
            seq=new_seq,
            files=[Path(f).name for f in validated],
            ref=f"[{index}]",
            url=url,
        )
        return {
            "ok": True,
            "action": "upload",
            "ref": f"[{index}]",
            "files": [Path(f).name for f in validated],
            "seq": new_seq,
        }

    # ------------------------------------------------------------------
    # Frame list / focus
    # ------------------------------------------------------------------

    async def frame_list(self) -> list[FrameInfo]:
        return await self._frame_list_impl()

    async def frame_focus(
        self,
        *,
        name: str | None = None,
        url: str | None = None,
        main: bool = False,
    ) -> dict[str, Any]:
        return await self._frame_focus_impl(name=name, url=url, main=main)

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    async def tab_list(self) -> list[TabInfo]:
        return await self._tab_list_impl()

    async def tab_new(self, url: str | None = None) -> dict[str, Any]:
        return await self._tab_new_impl(url)

    async def tab_close(self, tab_id: int) -> dict[str, Any]:
        return await self._tab_close_impl(tab_id)

    async def tab_switch(self, tab_id: int) -> dict[str, Any]:
        return await self._tab_switch_impl(tab_id)

    # ------------------------------------------------------------------
    # Fetch / Close / Raw CDP
    # ------------------------------------------------------------------

    async def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        body: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self._check_browser_alive()
        if timeout is None:
            from agentcloak.core.config import load_config

            _, _cfg = load_config()
            timeout = float(_cfg.navigation_timeout)
        result = await self._fetch_impl(
            url, method=method, body=body, headers=headers, timeout=timeout
        )
        new_seq = self._seq_counter.increment_action()
        self._ring_buffer.append(
            SeqEvent(
                seq=new_seq,
                kind="fetch",
                data={
                    "method": method.upper(),
                    "url": url,
                    "status": result.get("status", 0),
                },
            )
        )
        return result

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._close_impl()

    async def raw_cdp(self, method: str, params: dict[str, Any] | None = None) -> Any:
        return await self._raw_cdp_impl(method, params)

    # ------------------------------------------------------------------
    # Capture (network traffic recording)
    # ------------------------------------------------------------------
    # The Playwright backend captures via Page event listeners wired at launch
    # time, so it needs no extra setup here. The RemoteBridge backend relies
    # on CDP ``Network.*`` events from the Chrome Extension; ``_capture_*_impl``
    # hooks give it a place to send ``Network.enable``/``Network.disable``
    # without forcing the route layer to know which backend is active.

    async def capture_start(self) -> dict[str, Any]:
        """Start recording network traffic. Subclasses may extend via hooks."""
        self._capture_store.start()
        try:
            await self._capture_setup_impl()
        except Exception:
            logger.debug("capture_setup_failed", exc_info=True)
        return {"recording": True}

    async def capture_stop(self) -> dict[str, Any]:
        """Stop recording network traffic. Subclasses may extend via hooks."""
        try:
            await self._capture_teardown_impl()
        except Exception:
            logger.debug("capture_teardown_failed", exc_info=True)
        self._capture_store.stop()
        return {"recording": False, "entries": len(self._capture_store)}

    async def _capture_setup_impl(self) -> None:
        """Hook for backend-specific capture setup. Default no-op."""
        return None

    async def _capture_teardown_impl(self) -> None:
        """Hook for backend-specific capture teardown. Default no-op."""
        return None

    # ------------------------------------------------------------------
    # Element resolution (shared helpers — subclasses can override
    # _click_impl etc. and look up the element themselves)
    # ------------------------------------------------------------------

    def _require_snapshot(self, index: int) -> ElementRef:
        if not self._selector_map:
            raise ElementNotFoundError(
                error="no_snapshot",
                hint="No snapshot taken yet — selector_map is empty",
                action="run 'snapshot' first to populate the selector_map",
            )
        if index not in self._selector_map:
            count = len(self._selector_map)
            raise ElementNotFoundError(
                error="element_not_found",
                hint=f"Index [{index}] not in selector_map ({count} entries)",
                action="run 'snapshot' to refresh the selector_map,"
                " then retry with a valid index",
            )
        return self._selector_map[index]

    def _get_ref(self, index: int) -> str:
        return f"[{index}]"

    # ------------------------------------------------------------------
    # Action dispatch
    # ------------------------------------------------------------------

    async def action(self, kind: str, target: str, **kw: Any) -> dict[str, Any]:
        # R1: Dialog interrupts every action. ``_raise_if_dialog_blocked``
        # bubbles a :class:`DialogBlockedError` which the FastAPI exception
        # handler turns into a 409 response with dialog metadata attached.
        self._raise_if_dialog_blocked()

        self._check_browser_alive()

        if kind not in _VALID_ACTION_KINDS:
            raise BackendError(
                error="invalid_action_kind",
                hint=f"Unknown action kind: '{kind}'",
                action=f"use one of: {', '.join(sorted(_VALID_ACTION_KINDS))}",
            )

        pre_url, _ = await self._get_page_info()

        # R0: reset per-action transient state before executing.
        self._last_navigation_event = None
        self._last_new_tab_event = None
        self._last_download_event = None

        try:
            result = await self._run_action(kind, target, **kw)
        except Exception as exc:
            self._maybe_mark_browser_closed(exc)
            raise

        # Subclasses may run post-action housekeeping (settling DOM, removing
        # locator markers, etc.).
        await self._post_action_cleanup()

        post_url, _ = await self._get_page_info()
        caused_navigation = (
            post_url != pre_url or self._last_navigation_event is not None
        )

        new_seq = self._seq_counter.increment_action()
        self._ring_buffer.append(
            SeqEvent(
                seq=new_seq,
                kind="action",
                data={"action": kind, "target": target, **kw},
            )
        )

        # R6: audit / current_value for fill+select.
        if kind in ("fill", "select"):
            current_val = kw.get("text") or kw.get("value") or kw.get("label")
            if current_val is not None:
                result["current_value"] = str(current_val)

        result["ok"] = True
        result["seq"] = new_seq
        result["action"] = kind
        if caused_navigation:
            result["caused_navigation"] = True
            result["new_url"] = post_url

        self._collect_feedback(result)
        return result

    async def _run_action(self, kind: str, target: str, **kw: Any) -> dict[str, Any]:
        if kind == "click":
            return await self._click_impl(
                target=target,
                x=kw.get("x"),
                y=kw.get("y"),
                button=kw.get("button", "left"),
                click_count=int(kw.get("click_count", 1)),
            )
        if kind == "fill":
            return await self._fill_impl(target=target, text=str(kw.get("text", "")))
        if kind == "type":
            return await self._type_impl(
                target=target,
                text=str(kw.get("text", "")),
                delay=float(kw.get("delay", 0)),
            )
        if kind == "scroll":
            return await self._scroll_impl(
                target=target,
                direction=str(kw.get("direction", "down")),
                amount=int(kw.get("amount", 300)),
            )
        if kind == "hover":
            return await self._hover_impl(target=target, x=kw.get("x"), y=kw.get("y"))
        if kind == "select":
            value_raw = kw.get("value")
            label_raw = kw.get("label")
            value = str(value_raw) if value_raw is not None else None
            label = str(label_raw) if label_raw is not None else None
            if value is None and label is None:
                raise BackendError(
                    error="select_missing_option",
                    hint="select requires 'value' or 'label' parameter",
                    action="provide 'value' (option value) or 'label' (visible text)",
                )
            return await self._select_impl(target=target, value=value, label=label)
        if kind == "press":
            key = str(kw.get("key", ""))
            if not key:
                raise BackendError(
                    error="press_missing_key",
                    hint="press requires 'key' parameter",
                    action="provide 'key' (e.g. 'Enter', 'Tab', 'Escape')",
                )
            return await self._press_impl(target=target, key=key)
        if kind == "keydown":
            key = str(kw.get("key", ""))
            if not key:
                raise BackendError(
                    error="keydown_missing_key",
                    hint="keydown requires 'key' parameter",
                    action="provide 'key' (e.g. 'Shift', 'Control', 'Alt')",
                )
            return await self._keydown_impl(key=key)
        if kind == "keyup":
            key = str(kw.get("key", ""))
            if not key:
                raise BackendError(
                    error="keyup_missing_key",
                    hint="keyup requires 'key' parameter",
                    action="provide 'key' (e.g. 'Shift', 'Control', 'Alt')",
                )
            return await self._keyup_impl(key=key)
        raise BackendError(
            error="invalid_action_kind",
            hint=f"Unknown action kind: '{kind}'",
            action=f"use one of: {', '.join(sorted(_VALID_ACTION_KINDS))}",
        )

    async def _post_action_cleanup(self) -> None:
        """Hook for backend-specific cleanup after an action runs.

        Default no-op. PlaywrightAdapter overrides to wait for load state
        and strip locator marker attributes.
        """
        return None

    def _collect_feedback(self, result: dict[str, Any]) -> None:
        """R0: Attach proactive state feedback fields to action result."""
        if self._pending_request_count > 0:
            result["pending_requests"] = self._pending_request_count
        if self._pending_dialog is not None:
            d = self._pending_dialog
            result["dialog"] = {
                "type": d.dialog_type,
                "message": d.message,
            }
            if d.default_value:
                result["dialog"]["default_value"] = d.default_value
        if self._last_navigation_event is not None:
            result["navigation"] = self._last_navigation_event
            self._last_navigation_event = None
        if self._last_new_tab_event is not None:
            result["new_tab"] = self._last_new_tab_event
            self._last_new_tab_event = None
        if self._last_download_event is not None:
            result["download"] = self._last_download_event
            self._last_download_event = None

    # ------------------------------------------------------------------
    # Batch
    # ------------------------------------------------------------------

    async def action_batch(
        self,
        actions: list[dict[str, Any]],
        *,
        sleep: float = 0.0,
        settle_timeout: int | None = None,
    ) -> dict[str, Any]:
        from agentcloak.core.config import load_config

        _, _cfg = load_config()
        if settle_timeout is None:
            settle_timeout = _cfg.batch_settle_timeout
        _default_wait_timeout = _cfg.action_timeout

        results: list[dict[str, Any]] = []
        total = len(actions)
        if total == 0:
            return {"results": [], "completed": 0, "total": 0}

        for i, act in enumerate(actions):
            kind = act.get("kind", act.get("action", ""))
            index = act.get("index")
            target = str(index) if index is not None else act.get("target", "")
            extra = {
                k: v
                for k, v in act.items()
                if k not in ("kind", "action", "index", "target")
            }

            if kind == "wait":
                try:
                    result = await self.wait(
                        condition=extra.get("condition", "ms"),
                        value=str(extra.get("value", "1000")),
                        timeout=int(extra.get("timeout", _default_wait_timeout)),
                        state=str(extra.get("state", "visible")),
                    )
                except Exception as exc:
                    result = {"ok": False, "error": str(exc), "action": "wait"}
                results.append(result)
                continue

            # Read-after-write settle: if previous action left pending requests
            # and this is a snapshot, wait until the count drops.
            if (
                i > 0
                and kind == "snapshot"
                and results
                and results[-1].get("pending_requests", 0) > 0
            ):
                await self._settle_pending_requests(settle_timeout)

            try:
                result = await self.action(str(kind), str(target), **extra)
            except DialogBlockedError as exc:
                # Convert to dict for the partial results payload — agents
                # need the dialog metadata to know how to recover.
                blocked_result = exc.to_dict()
                blocked_result["seq"] = self._seq_counter.value
                results.append(blocked_result)
                remaining = [
                    {
                        "index": j,
                        "kind": actions[j].get("kind", actions[j].get("action", "")),
                    }
                    for j in range(i + 1, total)
                ]
                return {
                    "results": results,
                    "completed": i,
                    "total": total,
                    "aborted_reason": "dialog_pending",
                    "dialog": exc.dialog,
                    "remaining": remaining,
                }
            results.append(result)

            if result.get("caused_navigation"):
                return {
                    "results": results,
                    "completed": i + 1,
                    "total": total,
                    "aborted_reason": "url_changed",
                }

            if sleep > 0 and i < total - 1:
                await asyncio.sleep(sleep)

        return {"results": results, "completed": total, "total": total}

    async def _settle_pending_requests(self, timeout_ms: int) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_ms / 1000
        while self._pending_request_count > 0:
            if loop.time() >= deadline:
                break
            await asyncio.sleep(0.1)


def screenshot_to_base64(data: bytes) -> str:
    """Encode screenshot bytes as base64 ASCII (re-exported for back-compat)."""
    return base64.b64encode(data).decode("ascii")
