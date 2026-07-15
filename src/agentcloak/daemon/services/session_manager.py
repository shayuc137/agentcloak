"""SessionManager — multi-session browser multiplexing.

A single daemon can serve several independent callers at once: two Claude
Code sessions, an MCP client and a CLI, different AI platforms. Each gets
its own isolated browser so cookies, storage, and reverse-engineering
manager state never bleed across sessions.

Relationship to :class:`ContextManager`
---------------------------------------
The two are deliberately orthogonal:

* :class:`ContextManager` owns the **"default" session** — the single
  ``app.state.browser_ctx`` slot, tier hot-switching (local ↔ remote
  bridge), the httpcloak proxy binding, and the warm-cache idle timer.
  Requests with no ``X-Agentcloak-Session`` header (every legacy CLI
  invocation) keep landing there, unchanged.
* :class:`SessionManager` owns **every other session**. It never touches
  ``browser_ctx``; it keeps its own ``dict[session_id -> SessionSlot]``
  and each slot carries an independent browser. The provider
  (:func:`agentcloak.daemon.dependencies.get_browser_ctx`) is what routes
  a named session here and leaves ``"default"`` with the existing path.

Keeping the default session out of this manager means the mature
tier-switch / remote-bridge / proxy machinery is reused verbatim and the
backward-compatible behaviour is provably untouched.

Session lifecycle (three states)
---------------------------------
================  ===========  ==========  ======================================
state             ``ctx``      browser     trigger
================  ===========  ==========  ======================================
registered        ``None``     none        slot created, browser not yet built
active            ``ctx``      running     ``get_or_create`` built a browser
suspended         ``None``     closed      per-session idle timeout reclaimed it
================  ===========  ==========  ======================================

A suspended session keeps its metadata (so ``session list`` still shows
it and the next request transparently rebuilds the browser) but releases
the ~300 MB the Chromium process held.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from agentcloak.browser import create_context
from agentcloak.browser.cloak_ctx import TURNSTILE_PATCH_DIR
from agentcloak.browser.secure_ctx import SecureBrowserContext
from agentcloak.core.config import resolve_tier
from agentcloak.core.types import StealthTier

if TYPE_CHECKING:
    from collections.abc import Callable

    from agentcloak.core.config import AgentcloakConfig

__all__ = ["SessionManager", "SessionSlot"]

logger = structlog.get_logger()

# The session bucket that maps to ``app.state.browser_ctx`` and the
# ContextManager-owned default backend. SessionManager never stores a slot
# under this id — the provider short-circuits it before reaching here — but
# the constant is shared so both sides agree on the spelling.
DEFAULT_SESSION_ID = "default"


@dataclass
class SessionSlot:
    """One named session's browser + bookkeeping.

    ``ctx`` is ``None`` while the session is registered-but-not-launched or
    after idle reclamation (suspended). ``last_request_time`` uses
    :func:`time.monotonic` so the idle math is immune to wall-clock jumps,
    matching the daemon's existing idle watchdog.
    """

    session_id: str
    ctx: Any | None = None  # SecureBrowserContext when active, else None
    last_request_time: float = field(default_factory=time.monotonic)
    tier: StealthTier = StealthTier.CLOAK
    created_at: float = field(default_factory=time.time)

    @property
    def state(self) -> str:
        """Human-facing lifecycle state for ``session list`` output."""
        return "active" if self.ctx is not None else "suspended"


class SessionManager:
    """Multiplexes named browser sessions over a single daemon.

    Parameters
    ----------
    config:
        Snapshot of :class:`AgentcloakConfig`. Drives viewport, humanize,
        headless, proxy, and the Chromium flag composition for each
        per-session browser — kept in lockstep with
        :meth:`ContextManager._launch_local` so the two launch paths can't
        drift.
    """

    def __init__(
        self,
        config: AgentcloakConfig,
        *,
        hide_selectors_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        self._config = config
        self._hide_selectors_provider = hide_selectors_provider
        self._sessions: dict[str, SessionSlot] = {}
        # All mutation of ``_sessions`` and per-slot browser launch/close runs
        # under this lock. Browser creation is slow (a few hundred ms), so the
        # lock also serialises concurrent first-touches of the *same* session
        # id — without it two racing requests could each launch a browser and
        # leak one. FastAPI is single-loop so contention is brief.
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Acquisition
    # ------------------------------------------------------------------

    async def get_or_create(self, session_id: str) -> Any:
        """Return the live ctx for ``session_id``, launching one if needed.

        Three paths collapse into one public method:

        1. session active (``ctx`` set) → refresh ``last_request_time``,
           return it.
        2. session suspended (slot exists, ``ctx`` is ``None``) → relaunch a
           browser, flip to active.
        3. session unknown → register the slot and launch.

        Always returns a :class:`SecureBrowserContext`-wrapped backend so the
        daemon sees the same API surface as the default session.
        """
        async with self._lock:
            slot = self._sessions.get(session_id)
            if slot is not None and slot.ctx is not None:
                slot.last_request_time = time.monotonic()
                return slot.ctx

            if slot is None:
                slot = SessionSlot(session_id=session_id)
                self._sessions[session_id] = slot
                logger.info("session_registered", session_id=session_id)

            ctx = await self._launch_browser()
            slot.ctx = ctx
            slot.last_request_time = time.monotonic()
            logger.info(
                "session_browser_launched",
                session_id=session_id,
                tier=slot.tier.value,
            )
            return ctx

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    async def close_session(self, session_id: str) -> bool:
        """Close ``session_id``'s browser and drop the slot.

        Returns ``True`` if a known session was removed, ``False`` if no such
        session existed (so the route can answer ``unknown_session`` without
        treating it as an error).
        """
        async with self._lock:
            slot = self._sessions.pop(session_id, None)
            if slot is None:
                return False
            if slot.ctx is not None:
                await self._close_ctx(slot.ctx, session_id=session_id)
            logger.info("session_closed", session_id=session_id)
            return True

    async def close_all(self) -> None:
        """Close every session's browser. Used by daemon shutdown."""
        async with self._lock:
            for session_id, slot in list(self._sessions.items()):
                if slot.ctx is not None:
                    await self._close_ctx(slot.ctx, session_id=session_id)
            self._sessions.clear()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return a serialisable summary of every *named* managed session.

        The default session is intentionally *not* included — it lives on
        ``app.state.browser_ctx`` under :class:`ContextManager`, outside this
        manager's bookkeeping. ``cloak session list`` therefore shows only the
        named (``X-Agentcloak-Session``) sessions; the default browser's state
        is surfaced through ``/health`` instead. Keeping the boundary here means
        the manager never has to reach across into ContextManager-owned state.
        """
        now = time.monotonic()
        out: list[dict[str, Any]] = []
        for slot in self._sessions.values():
            out.append(
                {
                    "session_id": slot.session_id,
                    "state": slot.state,
                    "tier": slot.tier.value,
                    "idle_seconds": round(now - slot.last_request_time, 1),
                }
            )
        return out

    # ------------------------------------------------------------------
    # Idle reclamation
    # ------------------------------------------------------------------

    async def cleanup_idle(self, timeout: float) -> list[str]:
        """Suspend sessions idle longer than ``timeout`` seconds.

        Closes the browser (freeing RAM) but keeps the slot so the metadata
        survives and the next request rebuilds transparently. Returns the
        list of suspended session ids so the watchdog can log them.
        """
        if timeout <= 0:
            return []
        now = time.monotonic()
        suspended: list[str] = []
        async with self._lock:
            for session_id, slot in self._sessions.items():
                if slot.ctx is None:
                    continue
                if now - slot.last_request_time < timeout:
                    continue
                await self._close_ctx(slot.ctx, session_id=session_id)
                slot.ctx = None
                suspended.append(session_id)
        if suspended:
            logger.info("sessions_suspended_idle", session_ids=suspended)
        return suspended

    def all_idle(self, timeout: float) -> bool:
        """True if no session holds a browser that is younger than ``timeout``.

        The global idle watchdog uses this together with the default
        session's own activity to decide whether the whole daemon may shut
        down. An empty manager is trivially idle.
        """
        if not self._sessions:
            return True
        now = time.monotonic()
        for slot in self._sessions.values():
            if slot.ctx is None:
                continue
            if now - slot.last_request_time < timeout:
                return False
        return True

    @property
    def active_count(self) -> int:
        """Number of sessions currently holding a live browser."""
        return sum(1 for slot in self._sessions.values() if slot.ctx is not None)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _launch_browser(self) -> Any:
        """Launch a fresh per-session browser, wrapped in the security layer.

        Mirrors :meth:`ContextManager._launch_local`: same tier resolution,
        same Chromium flag composition, same Turnstile patch extension for
        the cloak tier. Per-session browsers do not bind the httpcloak local
        proxy — only the daemon's default startup wires that in, so
        ``proxy_url`` stays ``None`` here (matching the hot-switch path).
        """
        tier = StealthTier(resolve_tier(self._config.browser.default_tier))
        # remote_bridge has no standalone local browser to launch; a named
        # session can't ride the shared extension, so fall back to the cloak
        # backend which is always launchable.
        if tier == StealthTier.REMOTE_BRIDGE:
            tier = StealthTier.CLOAK

        extensions = [str(TURNSTILE_PATCH_DIR)] if tier == StealthTier.CLOAK else None

        chromium_args: list[str] = list(self._config.browser.extra_args)
        if not self._config.browser.dns_over_https:
            chromium_args.append("--disable-features=DnsOverHttps")

        raw_ctx = await create_context(
            tier=tier,
            headless=self._config.browser.headless,
            viewport_width=self._config.browser.viewport_width,
            viewport_height=self._config.browser.viewport_height,
            profile_dir=None,
            humanize=self._config.browser.humanize,
            extensions=extensions,
            proxy_url=None,
            browser_proxy=self._config.browser.proxy or None,
            extra_args=chromium_args,
            browser_config=self._config.browser,
        )
        ctx = SecureBrowserContext(raw_ctx, self._config)
        # Session browsers keep ephemeral data but inherit the daemon's
        # profile-scoped hide selectors — hiding overlays is observation
        # behaviour, not browser state.
        selectors = (
            self._hide_selectors_provider()
            if self._hide_selectors_provider is not None
            else []
        )
        await ctx.hide_manager.load(selectors)
        return ctx

    async def _close_ctx(self, ctx: Any, *, session_id: str) -> None:
        """Best-effort close of a session's browser.

        ``BrowserContextBase.close`` already swallows backend errors, but we
        guard again so a single wedged browser never blocks reclamation of
        the rest.
        """
        with contextlib.suppress(Exception):
            await ctx.close()
        logger.info("session_browser_closed", session_id=session_id)
