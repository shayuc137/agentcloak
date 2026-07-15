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
import json
import re
import time
from abc import ABC, abstractmethod
from collections import deque
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import unquote, urlsplit

import structlog

from agentcloak.browser.state import (
    ConsoleEntry,
    DownloadEntry,
    ElementRef,
    FrameInfo,
    PageSnapshot,
    PendingDialog,
    TabInfo,
)
from agentcloak.core.capture import CaptureStore
from agentcloak.core.config import BrowserConfig
from agentcloak.core.errors import (
    AgentBrowserError,
    BackendError,
    BrowserTimeoutError,
    DebuggerPausedError,
    DialogBlockedError,
    ElementNotFoundError,
    NavigationError,
)
from agentcloak.core.seq import RingBuffer, SeqCounter, SeqEvent
from agentcloak.core.storage_snapshot import (
    read_storage_snapshot,
    resolve_storage_snapshot_path,
    write_storage_snapshot,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from agentcloak.browser.managers import (
        DebuggerManager,
        HideManager,
        RouteManager,
        RouteRule,
        ScriptManager,
        SourceMapManager,
        StreamingMonitor,
    )
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

_ANCHOR_SCROLL_POLL_INTERVAL = 0.2
_ANCHOR_SCROLL_TIMEOUT = 3.0


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
        browser_config: BrowserConfig | None = None,
        profile_dir: Path | None = None,
    ) -> None:
        # --- Shared state ---
        self._seq_counter: SeqCounter = seq_counter or SeqCounter()
        self._ring_buffer: RingBuffer = ring_buffer or RingBuffer()
        # Phase 6d: BrowserConfig is injected at launch time so navigate/
        # screenshot/wait/fetch/action_batch can read timeouts and quality
        # defaults straight from the dataclass instead of re-parsing
        # config.toml on every public call. Defaults to a fresh
        # BrowserConfig() so backends constructed without explicit
        # configuration (mostly unit tests) keep working with hard-coded
        # dataclass defaults.
        self._browser_config: BrowserConfig = browser_config or BrowserConfig()
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
        # Holds the asyncio tasks spawned by ``_dispatch_dialog_event`` to
        # auto-accept alert/beforeunload dialogs. We keep strong refs so the
        # event loop doesn't GC the coroutine before it resolves.
        self._auto_dialog_tasks: set[asyncio.Task[None]] = set()

        # R5: Frame switching — active frame state. Subclasses interpret the
        # value (e.g. PlaywrightAdapter stores a Frame object, RemoteBridge
        # stores a frameId string).
        self._active_frame: Any = None

        # Track whether the underlying browser has been observed closed so the
        # next request can raise a structured error instead of a raw exception.
        self._browser_closed: bool = False

        # ``_page_valid`` tracks whether the active page reflects the agent's
        # most recent intent. A failed ``navigate()`` flips this to False so
        # downstream page-bound operations (screenshot, evaluate, snapshot,
        # action, upload, wait, frame ops) fail fast with a recovery hint
        # instead of silently running against the stale previous page. The
        # next successful navigate restores it.
        self._page_valid: bool = True

        # R1 (7a): Console capture. Console messages arrive asynchronously
        # via page events, not as user actions, so they get their own
        # monotonic counter (``_console_seq``) and ring buffer instead of
        # sharing the action ``_seq_counter``. ``console_entries(since=N)``
        # pages through this the same way ``network --since`` does.
        self._console_buffer: deque[ConsoleEntry] = deque(
            maxlen=self._browser_config.console_buffer_size
        )
        self._console_seq: int = 0
        self._console_listening: bool = False

        # R2 (7a): Completed downloads (both direct-URL and click-triggered).
        self._downloads: list[DownloadEntry] = []
        # Click-triggered download events arrive on the page's ``download``
        # listener; ``download_wait`` parks a future here so the next event
        # can hand the Download object back to the waiter.
        self._download_waiters: list[asyncio.Future[Any]] = []

        # R5 (7a): clipboard permission is granted lazily on first use and
        # remembered so we don't re-issue ``Browser.grantPermissions`` per call.
        self._clipboard_granted: bool = False

        # 7b: CDP event-stream plumbing for the reverse-engineering managers
        # (debugger / streaming / sourcemap). ``_cdp_event_handlers`` maps a
        # CDP method name — or a domain prefix ending in "." such as
        # ``"Debugger."`` — to the callbacks registered via
        # :meth:`_on_cdp_event`. ``_dispatch_cdp_event`` (driven by the
        # backend's persistent CDP session) fans an incoming event out to all
        # matching callbacks. ``_enabled_domains`` lets
        # :meth:`_cdp_enable_domain` stay idempotent so two managers asking for
        # the same domain only issue one ``<Domain>.enable``.
        self._cdp_event_handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._enabled_domains: set[str] = set()

        # 7b T1/T2/T3/T4: reverse-engineering managers, constructed lazily on
        # first use so a session that never touches them pays nothing.
        # ``script_manager`` / ``route_manager`` / ``streaming_monitor`` /
        # ``debugger`` / ``sourcemap`` are the public accessors below; the slots
        # stay ``None`` until then.
        self._script_mgr: ScriptManager | None = None
        self._route_mgr: RouteManager | None = None
        self._streaming_mgr: StreamingMonitor | None = None
        self._debugger_mgr: DebuggerManager | None = None
        self._sourcemap_mgr: SourceMapManager | None = None
        self._hide_mgr: HideManager | None = None

        # 7b T1.2: extra HTTP headers injected on every request. Kept here so
        # ``emulation headers`` can report the active set; the backend applies
        # them via ``_set_extra_headers_impl``.
        self._extra_headers: dict[str, str] = {}

        # localStorage persistence: profile directory for snapshot dump/restore.
        # None in ephemeral mode or RemoteBridge — all localStorage logic is
        # skipped when this is unset.
        self._profile_dir: Path | None = profile_dir

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def script_manager(self) -> ScriptManager:
        """Lazily-constructed init-script injector (7b T1.1)."""
        if self._script_mgr is None:
            from agentcloak.browser.managers import ScriptManager

            self._script_mgr = ScriptManager(self)
        return self._script_mgr

    @property
    def hide_manager(self) -> HideManager:
        """Lazily construct the persistent page-overlay hider."""
        if self._hide_mgr is None:
            from agentcloak.browser.managers import HideManager

            self._hide_mgr = HideManager(self)
        return self._hide_mgr

    @property
    def route_manager(self) -> RouteManager:
        """Lazily-constructed network-route interceptor (7b T1.3)."""
        if self._route_mgr is None:
            from agentcloak.browser.managers import RouteManager

            self._route_mgr = RouteManager(self)
        return self._route_mgr

    @property
    def streaming_monitor(self) -> StreamingMonitor:
        """Lazily-constructed WebSocket/SSE capture monitor (7b T2)."""
        if self._streaming_mgr is None:
            from agentcloak.browser.managers import StreamingMonitor

            self._streaming_mgr = StreamingMonitor(self)
        return self._streaming_mgr

    @property
    def debugger(self) -> DebuggerManager:
        """Lazily-constructed CDP debugger manager (7b T3)."""
        if self._debugger_mgr is None:
            from agentcloak.browser.managers import DebuggerManager

            self._debugger_mgr = DebuggerManager(self)
        return self._debugger_mgr

    @property
    def sourcemap(self) -> SourceMapManager:
        """Lazily-constructed source-map parser (7b T4).

        Mines the debugger's script inventory for ``sourceMapURL``s and parses
        them on demand. Pure data work — no CDP domain of its own — so it stays
        free until an agent actually resolves a position.
        """
        if self._sourcemap_mgr is None:
            from agentcloak.browser.managers import SourceMapManager

            self._sourcemap_mgr = SourceMapManager(self)
        return self._sourcemap_mgr

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
        force: bool = False,
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
    async def _upload_auto_find_impl(
        self, files: list[str], *, nth: int
    ) -> dict[str, Any]:
        """Find ``input[type=file]`` elements (including hidden ones) and upload.

        Used when ``upload`` is called without an explicit ``[N]`` index — modern
        drag-and-drop uploaders (Dropzone, react-dropzone, Ant Upload) hide the
        real ``<input type=file>`` with ``display:none``, so it never shows up in
        the accessibility tree and has no snapshot ref. This locates all matching
        inputs via ``querySelectorAll`` and attaches ``files`` to the ``nth`` one.

        Returns ``{uploaded, candidates_count, used_nth}``. Raises
        :class:`ElementNotFoundError` (``no_file_input_found``) when there are no
        file inputs, or (``file_input_index_out_of_range``) when ``nth`` exceeds
        the number found.
        """

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
    async def _auto_accept_dialog_impl(self) -> None:
        """Auto-accept the currently-pending alert/beforeunload dialog.

        Subclasses route to whichever underlying primitive their backend
        exposes (Playwright ``dialog.accept()`` vs CDP
        ``Page.handleJavaScriptDialog``). Called from
        :meth:`_dispatch_dialog_event` for alert/beforeunload only; the
        agent-driven accept/dismiss path runs through
        :meth:`_dialog_handle_impl`.
        """

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

    # --- Console capture (7a R1) ---

    @abstractmethod
    async def _console_setup_impl(self) -> None:
        """Wire backend console/error listeners so messages flow into the buffer.

        Idempotent — the public :meth:`console_entries` calls it once and
        flips :attr:`_console_listening`. Playwright registers
        ``page.on('console')`` + ``page.on('pageerror')``; RemoteBridge enables
        the CDP ``Runtime`` domain so ``consoleAPICalled`` /
        ``exceptionThrown`` events arrive over the bridge.
        """

    # --- Download (7a R2) ---

    @abstractmethod
    async def _download_url_impl(self, url: str, output_dir: str) -> DownloadEntry:
        """Fetch ``url`` server-side (reusing browser cookies) into ``output_dir``.

        The SSRF guard runs in the public :meth:`download_url` before this is
        called, so implementations may fetch directly.
        """

    @abstractmethod
    async def _download_wait_impl(
        self,
        output_dir: str,
        *,
        timeout: float,
        _waiter: asyncio.Future[Any] | None = None,
    ) -> DownloadEntry:
        """Block until the next click-triggered download finishes, saving it.

        When ``_waiter`` is provided (pre-armed by :meth:`download_wait_click`),
        use it instead of creating a new future. This avoids the race where the
        download event resolves the pre-armed future before a newly-created one
        is registered.
        """

    # --- Clipboard (7a R5) ---

    @abstractmethod
    async def _clipboard_read_impl(self) -> str:
        """Return the system clipboard text (granting permission as needed)."""

    @abstractmethod
    async def _clipboard_write_impl(self, text: str) -> None:
        """Write ``text`` to the system clipboard (granting permission as needed)."""

    # --- PDF (7a R6) ---

    @abstractmethod
    async def _pdf_impl(self, options: dict[str, Any]) -> bytes:
        """Render the current page to PDF bytes (headless Chromium only)."""

    # --- Cookies CRUD (7a R3) ---

    @abstractmethod
    async def _cookies_set_impl(self, cookies: list[dict[str, Any]]) -> None:
        """Inject the given cookie objects into the browser context."""

    @abstractmethod
    async def _cookies_clear_impl(self) -> None:
        """Remove all cookies from the browser context."""

    @abstractmethod
    async def _cookies_delete_impl(self, name: str, *, domain: str | None) -> int:
        """Delete cookies matching ``name`` (optionally scoped to ``domain``).

        Returns the number of cookies removed.
        """

    # --- CDP transport (7b) ---

    @abstractmethod
    async def _cdp_send_impl(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Send a raw CDP command and return its result.

        Unlike :meth:`_raw_cdp_impl` (a one-shot session that detaches
        immediately), this routes through whatever persistent CDP channel the
        backend keeps alive for event streaming — Playwright caches a
        per-tab ``CDPSession``; RemoteBridge forwards over the bridge
        WebSocket. The public wrapper :meth:`_cdp_send` owns audit logging and
        the closed-browser guard, so implementations only translate the call.
        """

    @abstractmethod
    async def _cdp_enable_domain_impl(self, domain: str) -> None:
        """Enable a CDP domain (``<domain>.enable``) on the persistent channel.

        Idempotency is handled by the public :meth:`_cdp_enable_domain`
        wrapper via :attr:`_enabled_domains`; implementations may assume they
        are only called once per domain and should just issue the enable.
        """

    # --- Header injection (7b T1.2) ---

    @abstractmethod
    async def _set_extra_headers_impl(self, headers: dict[str, str]) -> None:
        """Apply ``headers`` to every subsequent request.

        Playwright uses ``page.set_extra_http_headers``; RemoteBridge issues
        CDP ``Network.setExtraHTTPHeaders``. An empty dict clears the override.
        The public :meth:`set_extra_headers` owns the audit log and the
        ``_extra_headers`` bookkeeping.
        """

    # --- Route interception (7b T1.3) ---

    @abstractmethod
    async def _route_add_impl(self, rule: RouteRule) -> None:
        """Start intercepting requests matching ``rule``.

        Playwright registers a ``page.route`` handler; RemoteBridge derives a
        ``Fetch.enable`` pattern and resumes paused requests in its event
        handler. Called by :meth:`RouteManager.add` and on tab replay.
        """

    @abstractmethod
    async def _route_remove_impl(self, pattern: str | None) -> None:
        """Stop intercepting ``pattern`` (or everything when ``None``).

        Playwright calls ``page.unroute``; RemoteBridge re-derives the live
        ``Fetch`` patterns (disabling entirely when no rules remain).
        """

    # ------------------------------------------------------------------
    # CDP event stream (7b) — shared transport for reverse-engineering
    # managers. Concrete; backends only implement the two ``_impl`` atoms
    # above plus call :meth:`_dispatch_cdp_event` from their event listener.
    # ------------------------------------------------------------------

    async def _cdp_send(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send a CDP command through the persistent channel, with guards.

        Centralises the closed-browser check, the failed-navigate guard, and
        audit logging so every reverse-engineering manager goes through one
        choke point instead of touching the backend session directly. This is
        the single security/audit funnel referenced by design decision D-Q3.
        """
        self._check_browser_alive()
        self._check_page_valid()
        logger.debug("cdp_send", method=method)
        return await self._cdp_send_impl(method, params or {})

    def _on_cdp_event(
        self, method: str, callback: Callable[[dict[str, Any]], None]
    ) -> None:
        """Register ``callback`` for a CDP event ``method``.

        ``method`` is matched two ways by :meth:`_dispatch_cdp_event`:

        * **exact** — ``"Debugger.paused"`` fires only on that event.
        * **prefix** — a key ending in ``"."`` (e.g. ``"Network."``) fires for
          every event in that domain. Managers use this to catch a whole
          domain without enumerating each event name.

        Callbacks receive the event ``params`` dict. They run synchronously in
        the event-listener context, so they must stay non-blocking — park a
        :class:`asyncio.Future` (the debugger pause pattern) rather than
        awaiting inside the callback.
        """
        handlers = self._cdp_event_handlers.setdefault(method, [])
        if callback not in handlers:
            handlers.append(callback)

    def _dispatch_cdp_event(self, method: str, params: dict[str, Any]) -> None:
        """Fan a CDP event out to every matching registered callback.

        Called by the backend's persistent-session event forwarder
        (Playwright ``session.on("event", ...)`` / RemoteBridge
        ``feed_message``). A single dict is iterated as ``list(...)`` so a
        callback that registers another handler mid-dispatch can't mutate the
        list we're walking. Callback exceptions are swallowed (logged at
        debug) so one misbehaving manager can't break event delivery to the
        others.
        """
        for cb in list(self._cdp_event_handlers.get(method, [])):
            try:
                cb(params)
            except Exception:
                logger.debug("cdp_event_handler_failed", method=method, exc_info=True)
        for prefix, cbs in list(self._cdp_event_handlers.items()):
            if prefix.endswith(".") and method.startswith(prefix):
                for cb in list(cbs):
                    try:
                        cb(params)
                    except Exception:
                        logger.debug(
                            "cdp_event_handler_failed", method=method, exc_info=True
                        )

    async def _cdp_enable_domain(self, domain: str) -> None:
        """Idempotently enable a CDP domain on the persistent channel.

        Tracks enabled domains in :attr:`_enabled_domains` so repeated calls
        (e.g. StreamingMonitor and DebuggerManager both wanting ``Network``)
        only issue one ``<Domain>.enable``. We guard browser liveness but not
        page validity — enabling a domain is transport setup that should
        succeed even when the last navigate failed.
        """
        if domain in self._enabled_domains:
            return
        self._check_browser_alive()
        await self._cdp_enable_domain_impl(domain)
        self._enabled_domains.add(domain)

    # ------------------------------------------------------------------
    # Header injection (7b T1.2)
    # ------------------------------------------------------------------

    async def set_extra_headers(self, headers: dict[str, str]) -> dict[str, Any]:
        """Inject ``headers`` on every subsequent request.

        Reverse-engineering and API debugging often need a forged
        ``Authorization`` / ``X-Requested-With`` / custom token on outgoing
        requests. The headers persist until replaced; passing an empty dict
        clears them. Audited because it silently rewrites every request — a
        security-relevant override the operator should be able to trace.
        """
        self._check_browser_alive()
        await self._set_extra_headers_impl(headers)
        self._extra_headers = dict(headers)
        logger.info(
            "audit_action",
            action="set_extra_headers",
            header_names=sorted(headers.keys()),
        )
        return {"headers": dict(self._extra_headers), "count": len(self._extra_headers)}

    def list_extra_headers(self) -> dict[str, str]:
        """Return the currently-active extra headers."""
        return dict(self._extra_headers)

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

    def _check_page_valid(self) -> None:
        """Raise if the last ``navigate()`` failed and no successful one has run since.

        Page-bound operations (screenshot, evaluate, snapshot, action,
        upload, selector/js waits, frame ops) call this so a silent fallback
        to the stale previous page can never happen. ``navigate()`` itself
        is exempt — it is the recovery path.
        """
        if not self._page_valid:
            raise NavigationError(
                error="no_valid_page",
                hint="Last navigate failed — the browser is still on the previous page",
                action="run 'cloak navigate <url>' to load a new page first",
            )

    def mark_page_invalid(self) -> None:
        """Public hook for outer wrappers (e.g. :class:`SecureBrowserContext`)
        to invalidate the page when their pre-flight rejects a navigate before
        it ever reaches the inner backend.

        Without this, an IDPI scheme block (``file://``) or whitelist miss
        would raise :class:`SecurityError` while leaving ``_page_valid``
        True — so the very next ``screenshot``/``evaluate`` would silently
        run on the previous page. This is the exact silent-failure trap PRD
        05-19 closes.
        """
        self._page_valid = False

    # ------------------------------------------------------------------
    # navigate / snapshot / evaluate / network / screenshot
    # ------------------------------------------------------------------

    async def navigate(
        self, url: str, *, timeout: float | None = None
    ) -> dict[str, Any]:
        self._check_debugger_paused()
        self._check_browser_alive()
        if timeout is None:
            timeout = float(self._browser_config.navigation_timeout)

        # Dump localStorage before navigating away from the current origin so
        # token refreshes that happened since the last dump are captured.
        if self._profile_dir is not None:
            target_origin = self._extract_origin(url)
            if target_origin:
                current_origin = await self._get_current_origin()
                if current_origin and current_origin != target_origin:
                    await self._dump_localstorage_for_origin()

        # Flag flips on the failure / success edge, not on entry. If we
        # invalidated *before* awaiting ``_navigate_impl``, a concurrent
        # ``screenshot`` request could observe ``_page_valid = False`` mid-
        # navigation and erroneously raise ``no_valid_page`` even though
        # the previous page is still operable. Settling the flag in the
        # except/success branches keeps the bug fix tight to its real
        # cause (navigation actually failing) and avoids accidental
        # collateral damage to overlapping requests.
        try:
            result = await self._navigate_impl(url, timeout=timeout)
        except Exception as exc:
            self._page_valid = False
            self._maybe_mark_browser_closed(exc)
            raise
        self._page_valid = True
        await self._notify_managers_on_navigated()

        # Restore localStorage for the new origin after navigation succeeds.
        await self._restore_localstorage()

        anchor = await self._maybe_scroll_to_hash(url)
        if anchor is not None:
            result["anchor"] = anchor

        new_seq = self._seq_counter.increment_action()
        self._ring_buffer.append(
            SeqEvent(seq=new_seq, kind="navigate", data={"url": url})
        )
        logger.info("audit_action", action="navigate", seq=new_seq, url=url)
        result.setdefault("seq", new_seq)
        await self._ensure_console_cdp()
        if self._browser_config.auto_stream_monitor:
            await self.streaming_monitor.ensure_listening()
        return result

    async def _maybe_scroll_to_hash(self, url: str) -> str | None:
        """Restore native anchor behavior for asynchronously rendered targets."""
        try:
            fragment = unquote(urlsplit(url).fragment)
            # Skip hashbang routes (#!/app) and param-style fragments
            # (#access_token=...&state=...) — neither is an element anchor.
            if (
                not fragment
                or fragment.startswith("!")
                or any(ch in fragment for ch in "/=&")
            ):
                return None

            fragment_json = json.dumps(fragment)
            js = (
                "(() => {"
                f"const el=document.getElementById({fragment_json});"
                "if(!el)return false;"
                "el.scrollIntoView({block:'start'});"
                "return true;"
                "})()"
            )
            deadline = asyncio.get_running_loop().time() + _ANCHOR_SCROLL_TIMEOUT
            while True:
                if await self._evaluate_impl(js, world="main"):
                    return "scrolled"
                if asyncio.get_running_loop().time() >= deadline:
                    return "not_found"
                await asyncio.sleep(_ANCHOR_SCROLL_POLL_INTERVAL)
        except Exception:
            return "not_found"

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
        ``capture_active``, ``stealth_tier``, ``page_valid``. Missing keys
        default to empty / ``False``. ``page_valid`` mirrors
        :attr:`_page_valid` so agents can query whether subsequent
        page-bound ops will fail without having to attempt them first.
        """
        return {
            "url": "",
            "title": "",
            "tabs": [],
            "capture_active": self._capture_store.recording,
            "stealth_tier": self.stealth_tier.value,
            "page_valid": self._page_valid,
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
        selector: str = "",
    ) -> PageSnapshot:
        self._check_debugger_paused()
        self._check_browser_alive()
        self._check_page_valid()
        selector = selector.strip()
        if selector and frames:
            raise BackendError(
                error="snapshot_selector_with_frames",
                hint="Selector-scoped snapshots cannot merge child frame trees",
                action="use --selector without --frames, or focus a frame first",
            )
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
                selector=selector,
            )
        if mode == "dom":
            if selector:
                raise BackendError(
                    error="snapshot_selector_unsupported",
                    hint="Selector scope is not supported for DOM snapshots",
                    action="use compact, accessible, or content mode",
                )
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
        selector: str,
    ) -> PageSnapshot:
        from agentcloak.browser._snapshot_builder import (
            FrameData,
            build_snapshot,
            scope_ax_tree,
        )

        raw_nodes = await self._get_ax_tree(frames=frames)
        if selector:
            backend_node_id = await self._resolve_snapshot_selector(selector)
            raw_nodes = scope_ax_tree(raw_nodes, backend_node_id)
            if not raw_nodes:
                raise BackendError(
                    error="snapshot_selector_not_accessible",
                    hint=f"Selector '{selector}' has no accessibility-tree node",
                    action=(
                        "select an accessible ancestor such as main, form, or section"
                    ),
                )
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

    async def _resolve_snapshot_selector(self, selector: str) -> int:
        """Resolve a main-document CSS selector to its backend DOM node id."""
        try:
            document = await self._cdp_send(
                "DOM.getDocument", {"depth": -1, "pierce": True}
            )
            root = document.get("root", {})
            root_id = (
                cast("dict[str, Any]", root).get("nodeId")
                if isinstance(root, dict)
                else None
            )
            if not isinstance(root_id, int) or root_id <= 0:
                raise BackendError(
                    error="snapshot_selector_failed",
                    hint="CDP did not return a document root",
                    action="retry after the page finishes loading",
                )
            match = await self._cdp_send(
                "DOM.querySelector", {"nodeId": root_id, "selector": selector}
            )
            node_id = match.get("nodeId")
            if not isinstance(node_id, int) or node_id <= 0:
                raise BackendError(
                    error="snapshot_selector_not_found",
                    hint=f"No element matches selector '{selector}'",
                    action="check the selector in the main document and retry",
                )
            described = await self._cdp_send("DOM.describeNode", {"nodeId": node_id})
            node = described.get("node", {})
            backend_id = (
                cast("dict[str, Any]", node).get("backendNodeId")
                if isinstance(node, dict)
                else None
            )
            if not isinstance(backend_id, int) or backend_id <= 0:
                raise BackendError(
                    error="snapshot_selector_failed",
                    hint=f"Could not resolve selector '{selector}' to a backend node",
                    action="retry after the page finishes loading",
                )
            return backend_id
        except BackendError as exc:
            if exc.error.startswith("snapshot_selector_"):
                raise
            raise self._snapshot_selector_query_error(selector, exc) from exc
        except Exception as exc:
            raise self._snapshot_selector_query_error(selector, exc) from exc

    @staticmethod
    def _snapshot_selector_query_error(selector: str, exc: Exception) -> BackendError:
        detail = str(getattr(exc, "hint", exc))
        lowered = detail.lower()
        stale_markers = (
            "could not find node",
            "no node with given id",
            "document updated",
            "cannot find context with specified id",
        )
        if any(marker in lowered for marker in stale_markers):
            return BackendError(
                error="snapshot_selector_not_found",
                hint=f"The document changed while resolving selector '{selector}'",
                action="retry the snapshot after the page finishes loading",
            )
        return BackendError(
            error="snapshot_selector_invalid",
            hint=f"Could not query selector '{selector}': {detail}",
            action="fix the CSS selector or retry after the page finishes loading",
        )

    async def evaluate(self, js: str, *, world: str = "main") -> Any:
        self._check_debugger_paused()
        self._check_browser_alive()
        self._check_page_valid()
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
        output_path: str | None = None,
    ) -> bytes:
        # ``output_path`` writes the capture to disk in addition to returning
        # the bytes. Writing lives here rather than in ``_screenshot_impl`` so
        # both backends share one path-handling code path (it is pure local
        # I/O, independent of how the bytes were produced). The daemon route
        # inspects ``output_path`` to decide whether to base64 the bytes or
        # return ``{path, size}``.
        self._check_debugger_paused()
        self._check_browser_alive()
        self._check_page_valid()
        if quality is None:
            quality = self._browser_config.screenshot_quality
        try:
            data = await self._screenshot_impl(
                full_page=full_page, fmt=format, quality=quality
            )
        except Exception as exc:
            self._maybe_mark_browser_closed(exc)
            raise
        if output_path:
            from pathlib import Path

            Path(output_path).expanduser().write_bytes(data)
        return data

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

    def _check_debugger_paused(self) -> None:
        """Raise :class:`DebuggerPausedError` if execution is paused (7b T3).

        Symmetric with :meth:`_raise_if_dialog_blocked`: a page-bound action
        (navigate, click, evaluate, screenshot, ...) can't run while execution is
        suspended at a breakpoint — the page's JS isn't servicing events. Debugger
        commands themselves (resume/step/inspect) are exempt; they're how the
        agent gets *out* of the paused state. The guard is a cheap attribute peek
        in the common case (no debugger constructed → ``_debugger_mgr is None``).
        """
        if self._debugger_mgr is not None and self._debugger_mgr.is_paused:
            raise DebuggerPausedError(
                error="debugger_paused",
                hint="Page execution is paused at a breakpoint",
                action="use 'debugger resume' or 'debugger step' first",
                paused_info=self._debugger_mgr.get_paused_summary(),
            )

    def _dispatch_dialog_event(
        self,
        *,
        dialog_type: str,
        message: str,
        default_value: str,
        url: str,
    ) -> None:
        """Route a freshly-observed dialog into either auto-accept or pending.

        Called by backend-specific event handlers (Playwright
        ``page.on('dialog')``, CDP ``Page.javascriptDialogOpening``)
        after they normalise the underlying payload into four plain
        strings. alert / beforeunload are accepted in the background so
        scripts driven by ``window.alert()`` keep flowing; confirm /
        prompt are stashed as ``self._pending_dialog`` and surface as a
        ``DialogBlockedError`` on the next action — the agent must call
        ``dialog accept`` / ``dialog dismiss`` to clear it.
        """
        if dialog_type in ("alert", "beforeunload"):
            self._last_auto_dialog = {"type": dialog_type, "message": message}
            task = asyncio.ensure_future(self._auto_accept_dialog_impl())
            self._auto_dialog_tasks.add(task)
            task.add_done_callback(self._auto_dialog_tasks.discard)
            logger.info(
                "dialog_auto_accepted",
                dialog_type=dialog_type,
                message=message[:100],
            )
        else:
            self._pending_dialog = PendingDialog(
                dialog_type=dialog_type,
                message=message,
                default_value=default_value,
                url=url,
            )
            logger.info(
                "dialog_pending",
                dialog_type=dialog_type,
                message=message[:100],
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
            timeout = self._browser_config.action_timeout
        self._check_debugger_paused()
        self._check_browser_alive()
        # Selector and JS conditions evaluate against the live page; if the
        # last navigate failed, waiting against the stale page is the same
        # silent-failure trap screenshot/evaluate suffer from. ``url``,
        # ``load``, and ``ms`` operate on navigation/timer state that may
        # legitimately settle even when the current page is stale.
        if condition in ("selector", "js"):
            self._check_page_valid()
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

    async def upload(
        self, index: int | None, files: list[str], *, nth: int = 0
    ) -> dict[str, Any]:
        from pathlib import Path

        self._check_debugger_paused()
        self._check_browser_alive()
        self._check_page_valid()
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

        # ``index is None`` (no snapshot ref) → auto-find hidden file inputs;
        # an explicit index takes precedence and ``nth`` is ignored.
        auto_find = index is None
        try:
            if auto_find:
                impl_result = await self._upload_auto_find_impl(validated, nth=nth)
            else:
                impl_result = await self._upload_impl(index, validated)
        except Exception as exc:
            self._maybe_mark_browser_closed(exc)
            raise

        new_seq = self._seq_counter.increment_action()
        ref = f"[{index}]" if not auto_find else f"auto(nth={nth})"
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
            ref=ref,
            url=url,
        )
        result: dict[str, Any] = {
            "ok": True,
            "action": "upload",
            "ref": ref,
            "files": [Path(f).name for f in validated],
            "seq": new_seq,
        }
        if not auto_find:
            result["index"] = index
        # Surface how many file inputs were found and which one was used so the
        # agent can re-issue with a different ``--nth`` if it picked wrong.
        if "candidates_count" in impl_result:
            result["candidates_count"] = impl_result["candidates_count"]
        if "used_nth" in impl_result:
            result["used_nth"] = impl_result["used_nth"]
        return result

    # ------------------------------------------------------------------
    # Frame list / focus
    # ------------------------------------------------------------------

    async def frame_list(self) -> list[FrameInfo]:
        self._check_page_valid()
        return await self._frame_list_impl()

    async def frame_focus(
        self,
        *,
        name: str | None = None,
        url: str | None = None,
        main: bool = False,
    ) -> dict[str, Any]:
        self._check_page_valid()
        return await self._frame_focus_impl(name=name, url=url, main=main)

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    async def tab_list(self) -> list[TabInfo]:
        return await self._tab_list_impl()

    async def tab_new(self, url: str | None = None) -> dict[str, Any]:
        result = await self._tab_new_impl(url)
        await self._replay_managers_on_new_page()
        return result

    async def tab_close(self, tab_id: int) -> dict[str, Any]:
        return await self._tab_close_impl(tab_id)

    async def tab_switch(self, tab_id: int) -> dict[str, Any]:
        result = await self._tab_switch_impl(tab_id)
        await self._replay_managers_on_new_page()
        return result

    async def _replay_managers_on_new_page(self) -> None:
        """Re-apply init scripts / route rules onto the now-active page.

        A new or switched-to tab inherits none of the previous page's injected
        init scripts or ``page.route`` handlers, so the script and route
        managers replay their tracked state. Only managers that were actually
        used (constructed) do anything — the lazy slots stay ``None`` for
        sessions that never touched these capabilities, keeping tab switches
        free of overhead in the common path.
        """
        if self._script_mgr is not None:
            await self._script_mgr.on_tab_switched()
        if self._hide_mgr is not None:
            await self._hide_mgr.on_tab_switched()
        if self._route_mgr is not None:
            await self._route_mgr.on_tab_switched()
        # The debugger's domain state is per-page: a switched-to tab has its own
        # Debugger session and the old breakpoint ids are meaningless there. The
        # manager re-inits (disable → re-enable) and re-sets every breakpoint.
        if self._debugger_mgr is not None:
            await self._debugger_mgr.on_tab_switched()
        # The streaming monitor's ``Network`` enable + WS/SSE handlers were
        # registered on the previous tab's CDP session, so a switched-to tab
        # captures nothing until we re-arm against its own session.
        if self._streaming_mgr is not None:
            await self._streaming_mgr.on_tab_switched()

    async def _notify_managers_on_navigated(self) -> None:
        """Let event-driven managers react to a completed navigation.

        Symmetric with :meth:`_replay_managers_on_new_page` but for in-place
        navigations rather than tab changes: the streaming monitor drops its
        connection inventory because a navigation tears down any open WebSocket
        / EventSource (their ``requestId``s are now dead). As with the replay
        path, only managers that were actually constructed do anything — the
        lazy slots stay ``None`` for sessions that never used the capability,
        keeping the common navigate path free of overhead. Init scripts and
        route rules survive a navigation natively, so they need no hook here.
        """
        if self._streaming_mgr is not None:
            await self._streaming_mgr.on_navigated()
        if self._hide_mgr is not None:
            await self._hide_mgr.on_navigated()
        # The debugger drops its now-stale script inventory and re-applies XHR
        # breakpoints (Chrome resets DOMDebugger state on navigation); URL
        # breakpoints re-bind natively so they're left alone.
        if self._debugger_mgr is not None:
            await self._debugger_mgr.on_navigated()

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
        from agentcloak.core.ssrf_guard import validate_outbound_url

        self._check_debugger_paused()
        self._check_browser_alive()
        validate_outbound_url(url)
        if timeout is None:
            timeout = float(self._browser_config.navigation_timeout)
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

    # ------------------------------------------------------------------
    # localStorage persistence (profile mode only)
    # ------------------------------------------------------------------

    _LS_DUMP_JS = (
        "JSON.stringify({o:location.origin,"
        "d:Object.fromEntries(Object.keys(localStorage)"
        ".map(k=>[k,localStorage.getItem(k)]))})"
    )

    _LS_RESTORE_JS_TEMPLATE = (
        "(()=>{{const d={entries_json};"
        "Object.keys(d).forEach(k=>localStorage.setItem(k,d[k]))}})()"
    )

    async def _dump_localstorage_for_origin(self) -> None:
        if self._profile_dir is None:
            return
        try:
            raw = await self.evaluate(self._LS_DUMP_JS)
            if not isinstance(raw, str):
                logger.debug("ls_dump_skip", reason="evaluate returned non-string")
                return
            parsed = json.loads(raw)
            origin = parsed.get("o", "")
            data = parsed.get("d", {})
            if not origin or not isinstance(data, dict):
                logger.debug("ls_dump_skip", reason="invalid origin or data")
                return
            path = resolve_storage_snapshot_path(self._profile_dir)
            write_storage_snapshot(path, origin, cast("dict[str, str]", data))
            logger.info(
                "ls_dump_ok", origin=origin, keys=len(cast("dict[str, str]", data))
            )
        except Exception as exc:
            logger.debug("ls_dump_error", error=str(exc))

    async def _dump_localstorage_all_tabs(self) -> None:
        if self._profile_dir is None:
            return
        try:
            tabs = await self.tab_list()
        except Exception:
            return
        if len(tabs) <= 1:
            await self._dump_localstorage_for_origin()
            return
        active_tab: int | None = None
        for tab in tabs:
            if tab.active:
                active_tab = tab.tab_id
                break
        for tab in tabs:
            try:
                if not tab.active:
                    await self.tab_switch(tab.tab_id)
                await self._dump_localstorage_for_origin()
            except Exception:
                continue
        if active_tab is not None:
            with contextlib.suppress(Exception):
                await self.tab_switch(active_tab)

    async def _restore_localstorage(self) -> None:
        if self._profile_dir is None:
            return
        try:
            origin = await self.evaluate("location.origin")
            if not isinstance(origin, str) or not origin or origin == "null":
                return
            path = resolve_storage_snapshot_path(self._profile_dir)
            snapshot = read_storage_snapshot(path)
            entries = snapshot.get(origin)
            if not entries:
                return
            js = self._LS_RESTORE_JS_TEMPLATE.format(entries_json=json.dumps(entries))
            await self.evaluate(js)
        except Exception:
            pass

    def _extract_origin(self, url: str) -> str:
        """Extract origin from a URL string for comparison."""
        try:
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https"):
                return ""
            port = f":{parts.port}" if parts.port else ""
            return f"{parts.scheme}://{parts.hostname}{port}"
        except Exception:
            return ""

    async def _get_current_origin(self) -> str:
        try:
            origin = await self.evaluate("location.origin")
            if isinstance(origin, str) and origin and origin != "null":
                return origin
        except Exception:
            pass
        return ""

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._dump_localstorage_all_tabs()
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
    # Console capture (7a R1)
    # ------------------------------------------------------------------

    def _record_console_entry(
        self,
        *,
        level: str,
        text: str,
        url: str = "",
        line: int | None = None,
        column: int | None = None,
        is_error: bool = False,
    ) -> None:
        """Append one sanitized console message to the ring buffer.

        Backend event handlers normalise their native payload into these
        fields and call this; the shared method owns seq assignment and
        terminal-injection sanitisation so both backends stay consistent.
        """
        from agentcloak.core.text_sanitize import sanitize_terminal_text

        self._console_seq += 1
        self._console_buffer.append(
            ConsoleEntry(
                seq=self._console_seq,
                level=level,
                text=sanitize_terminal_text(text),
                timestamp=time.time(),
                url=url,
                line=line,
                column=column,
                is_error=is_error,
            )
        )

    async def _ensure_console_cdp(self) -> None:
        """Activate CDP console capture if not already listening.

        Called eagerly after navigate so page-load console messages are not
        lost between page load and the first ``console show`` query.
        Idempotent — skips if already set up.
        """
        if self._console_listening:
            return
        with contextlib.suppress(Exception):
            await self._console_setup_impl()
        self._console_listening = True

    async def console_entries(
        self,
        *,
        since: int = 0,
        limit: int = 0,
        level: str | None = None,
    ) -> dict[str, Any]:
        """Return buffered console messages newer than ``since``.

        ``level`` filters to a single log level (``log``/``warn``/``error``/
        ``info``/``debug``). ``limit`` caps the number returned (most recent
        kept). The returned ``seq`` is the highest console seq seen so the
        caller can pass it back as ``since`` next time.
        """
        await self._ensure_console_cdp()

        entries = [e for e in self._console_buffer if e.seq > since]
        if level:
            entries = [e for e in entries if e.level == level]
        if limit > 0:
            entries = entries[-limit:]
        return {
            "entries": [
                {
                    "seq": e.seq,
                    "level": e.level,
                    "text": e.text,
                    "url": e.url,
                    "line": e.line,
                    "column": e.column,
                    "is_error": e.is_error,
                    "timestamp": e.timestamp,
                }
                for e in entries
            ],
            "seq": self._console_seq,
        }

    async def console_clear(self) -> dict[str, Any]:
        """Drop all buffered console messages."""
        self._console_buffer.clear()
        return {"cleared": True}

    # ------------------------------------------------------------------
    # Download (7a R2)
    # ------------------------------------------------------------------

    def _resolve_download_waiter(self, download: Any) -> None:
        """Hand a freshly-observed download object to the oldest parked waiter.

        Backend ``download`` event handlers call this. If no one is waiting
        the event is dropped — agents opt into capturing a download by calling
        ``download_wait`` *before* triggering the click.
        """
        while self._download_waiters:
            fut = self._download_waiters.pop(0)
            if not fut.done():
                fut.set_result(download)
                return

    async def download_url(self, url: str, *, output_dir: str) -> dict[str, Any]:
        """Download ``url`` directly (server-side, with browser cookies).

        The SSRF guard rejects private/loopback/link-local targets before any
        request is made. The saved file is recorded in :meth:`download_list`.
        """
        from agentcloak.core.ssrf_guard import validate_download_url

        self._check_browser_alive()
        validate_download_url(url)
        entry = await self._download_url_impl(url, output_dir)
        self._downloads.append(entry)
        new_seq = self._seq_counter.increment_action()
        logger.info(
            "audit_action",
            action="download_url",
            seq=new_seq,
            url=url,
            path=entry.path,
            size=entry.size,
        )
        return {
            "filename": entry.filename,
            "path": entry.path,
            "size": entry.size,
            "url": entry.url,
            "source": entry.source,
            "seq": new_seq,
        }

    async def download_wait(
        self, *, output_dir: str, timeout: float | None = None
    ) -> dict[str, Any]:
        """Wait for the next click-triggered download and save it to ``output_dir``."""
        self._check_browser_alive()
        self._check_page_valid()
        if timeout is None:
            timeout = float(self._browser_config.navigation_timeout)
        entry = await self._download_wait_impl(output_dir, timeout=timeout)
        self._downloads.append(entry)
        return {
            "filename": entry.filename,
            "path": entry.path,
            "size": entry.size,
            "url": entry.url,
            "source": entry.source,
        }

    async def download_list(self) -> dict[str, Any]:
        """Return all downloads saved during this session."""
        return {
            "downloads": [
                {
                    "filename": e.filename,
                    "path": e.path,
                    "size": e.size,
                    "url": e.url,
                    "source": e.source,
                }
                for e in self._downloads
            ],
            "count": len(self._downloads),
        }

    async def download_wait_click(
        self,
        *,
        index: int,
        output_dir: str,
        timeout: float | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Arm a download waiter, click ``[index]``, then await the download."""
        self._check_browser_alive()
        self._check_page_valid()
        if timeout is None:
            timeout = float(self._browser_config.navigation_timeout)

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._download_waiters.append(fut)

        try:
            await self.action("click", target=str(index), force=force)
        except Exception:
            if not fut.done():
                fut.cancel()
            with contextlib.suppress(ValueError):
                self._download_waiters.remove(fut)
            raise

        entry = await self._download_wait_impl(output_dir, timeout=timeout, _waiter=fut)
        self._downloads.append(entry)
        return {
            "filename": entry.filename,
            "path": entry.path,
            "size": entry.size,
            "url": entry.url,
            "source": entry.source,
        }

    # ------------------------------------------------------------------
    # Clipboard (7a R5)
    # ------------------------------------------------------------------

    async def clipboard_read(self) -> dict[str, Any]:
        """Read the system clipboard text."""
        self._check_browser_alive()
        self._check_page_valid()
        text = await self._clipboard_read_impl()
        return {"text": text}

    async def clipboard_write(self, text: str) -> dict[str, Any]:
        """Write ``text`` to the system clipboard."""
        self._check_browser_alive()
        self._check_page_valid()
        await self._clipboard_write_impl(text)
        return {"written": True, "length": len(text)}

    # ------------------------------------------------------------------
    # PDF (7a R6)
    # ------------------------------------------------------------------

    async def pdf(self, *, options: dict[str, Any] | None = None) -> bytes:
        """Render the current page to PDF bytes.

        Only headless Chromium can produce a PDF; backends raise
        ``pdf_not_supported`` otherwise. The daemon route decides whether to
        write the bytes to disk or base64-encode them, mirroring screenshot.
        """
        self._check_browser_alive()
        self._check_page_valid()
        return await self._pdf_impl(options or {})

    # ------------------------------------------------------------------
    # Cookies CRUD (7a R3)
    # ------------------------------------------------------------------

    async def cookies_set(self, cookies: list[dict[str, Any]]) -> dict[str, Any]:
        """Inject cookie objects into the browser context."""
        self._check_browser_alive()
        await self._cookies_set_impl(cookies)
        new_seq = self._seq_counter.increment_action()
        logger.info(
            "audit_action", action="cookies_set", seq=new_seq, count=len(cookies)
        )
        return {"set": len(cookies), "seq": new_seq}

    async def cookies_clear(self) -> dict[str, Any]:
        """Remove all cookies from the browser context."""
        self._check_browser_alive()
        await self._cookies_clear_impl()
        new_seq = self._seq_counter.increment_action()
        logger.info("audit_action", action="cookies_clear", seq=new_seq)
        return {"cleared": True, "seq": new_seq}

    async def cookies_delete(
        self, name: str, *, domain: str | None = None
    ) -> dict[str, Any]:
        """Delete cookies matching ``name`` (optionally scoped to ``domain``)."""
        self._check_browser_alive()
        removed = await self._cookies_delete_impl(name, domain=domain)
        new_seq = self._seq_counter.increment_action()
        logger.info("audit_action", action="cookies_delete", seq=new_seq, name=name)
        return {"deleted": removed, "name": name, "seq": new_seq}

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
        # 7b T3: a breakpoint pause blocks every page action — the page's JS
        # isn't running, so clicking/filling/etc. would hang or no-op. Surfaces
        # a :class:`DebuggerPausedError` (409) the agent clears with resume/step.
        self._check_debugger_paused()
        # R1: Dialog interrupts every action. ``_raise_if_dialog_blocked``
        # bubbles a :class:`DialogBlockedError` which the FastAPI exception
        # handler turns into a 409 response with dialog metadata attached.
        self._raise_if_dialog_blocked()

        self._check_browser_alive()
        self._check_page_valid()

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
            button = kw.get("button", "left")
            click_count = int(kw.get("click_count", 1))
            force = bool(kw.get("force", False))
            if force and (button != "left" or click_count != 1):
                raise AgentBrowserError(
                    error="invalid_argument",
                    hint="force click only supports a single left click",
                    action="remove --force or use a coordinate click",
                )
            return await self._click_impl(
                target=target,
                x=kw.get("x"),
                y=kw.get("y"),
                button=button,
                click_count=click_count,
                force=force,
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
        if settle_timeout is None:
            settle_timeout = self._browser_config.batch_settle_timeout
        _default_wait_timeout = self._browser_config.action_timeout

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
                except AgentBrowserError as exc:
                    result = exc.to_dict()
                    result["step_index"] = i
                    result["kind"] = "wait"
                except Exception as exc:
                    result = {
                        "ok": False,
                        "error": "batch_step_failed",
                        "hint": str(exc),
                        "action": "check the wait condition and retry",
                        "step_index": i,
                        "kind": "wait",
                    }
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
