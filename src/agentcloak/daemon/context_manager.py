"""ContextManager — hot-switches the active browser context.

The daemon owns three slots on ``app.state``:

* ``browser_ctx`` — the live context every route uses. Wrapped by
  :class:`SecureBrowserContext`. May be ``None`` when no browser is
  available (typically ``remote_bridge`` mode awaiting the extension).
* ``local_ctx`` — cached local backend (CloakBrowser or Playwright). Kept
  warm across switches so swapping back to local doesn't pay the relaunch
  cost. Auto-closed after :attr:`AgentcloakConfig.local_idle_timeout`
  seconds of dormancy when the active tier is ``remote_bridge``.
* ``remote_ctx`` — the extension-backed :class:`RemoteBridgeContext`. Set
  by the ``/ext`` handler on connect and torn down on disconnect.

The manager itself is stateless beyond the slots above plus an idle
:class:`asyncio.TimerHandle`. All operations run on the daemon event
loop so we don't need locking — FastAPI is single-loop and each route
awaits at well-defined points.

Why an explicit manager instead of mutating ``app.state`` directly:

* The manager owns the lifecycle invariant "secure wrapper always
  wraps the active backend" — every route handler reads from
  ``app.state.browser_ctx`` and never has to think about wrapping.
* Idle timeout, cached local reuse, and the "no extension yet"
  state machine all live in one place.
* ``on_extension_connected`` / ``on_extension_disconnected`` are the
  hooks the WS endpoint calls so the activation transition stays in
  sync with the websocket lifecycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from agentcloak.browser import create_context
from agentcloak.browser.cloak_ctx import TURNSTILE_PATCH_DIR
from agentcloak.browser.secure_ctx import SecureBrowserContext
from agentcloak.core.types import StealthTier

if TYPE_CHECKING:
    from pathlib import Path

    from agentcloak.core.config import AgentcloakConfig

__all__ = ["ContextManager"]

logger = logging.getLogger(__name__)


class ContextManager:
    """Hot-switches the active browser context.

    Parameters
    ----------
    app_state:
        FastAPI ``app.state`` object. The manager reads/writes
        ``browser_ctx``, ``local_ctx``, ``remote_ctx``, ``local_tier``,
        ``local_profile``, ``active_tier`` on it.
    config:
        Snapshot of :class:`AgentcloakConfig`. Determines viewport size,
        humanize flag, idle timeout, etc.
    """

    def __init__(self, app_state: Any, config: AgentcloakConfig) -> None:
        self._state = app_state
        self._config = config
        self._idle_timer: asyncio.TimerHandle | None = None
        self._switching = asyncio.Lock()

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def seed_initial(
        self,
        *,
        active_tier: StealthTier,
        local_ctx: Any | None,
        local_tier: StealthTier | None,
        local_profile: str | None,
    ) -> None:
        """Seed the manager with state created during daemon startup.

        ``server.start()`` already builds the initial browser context (or
        decides to skip it for remote_bridge mode). This call attaches
        that state to the manager without doing a redundant launch.
        """
        self._state.local_ctx = local_ctx
        self._state.local_tier = local_tier
        self._state.local_profile = local_profile
        self._state.active_tier = active_tier
        # browser_ctx was already set by ``configure_app_state``; nothing
        # else to do until the user requests a hot switch.

    # ------------------------------------------------------------------
    # External lifecycle hooks
    # ------------------------------------------------------------------

    def on_extension_connected(self, remote_ctx: Any) -> None:
        """Record a freshly-connected extension context.

        If the current active tier is ``remote_bridge``, the secure
        wrapper is rebuilt around the new remote and exposed to routes
        immediately. Otherwise the context is held in reserve — the
        user may invoke ``bridge claim/finalize`` against the extension
        without switching the agent's active backend.
        """
        self._state.remote_ctx = remote_ctx
        if self._state.active_tier == StealthTier.REMOTE_BRIDGE:
            self._state.browser_ctx = SecureBrowserContext(remote_ctx, self._config)
            logger.info(
                "context_manager: extension connected, activated as browser_ctx"
            )
        else:
            logger.info(
                "context_manager: extension connected, holding (active tier: %s)",
                self._state.active_tier,
            )

    def on_extension_disconnected(self) -> None:
        """Clear the remote slot; if remote was active, drop browser_ctx."""
        was_remote = self._state.active_tier == StealthTier.REMOTE_BRIDGE
        self._state.remote_ctx = None
        if was_remote:
            self._state.browser_ctx = None
            logger.info("context_manager: extension disconnected, browser_ctx cleared")

    # ------------------------------------------------------------------
    # Tier switching
    # ------------------------------------------------------------------

    async def switch_tier(
        self,
        tier: StealthTier,
        *,
        profile: str | None = None,
    ) -> dict[str, Any]:
        """Hot-switch the active browser context.

        Returns a small descriptor of the resulting state so the route
        handler can hand it straight to the caller.
        """
        async with self._switching:
            if tier == StealthTier.AUTO:
                # Mirror ``resolve_tier`` so 'auto' always lands on a
                # concrete enum value the rest of the code understands.
                tier = StealthTier.CLOAK

            if tier == StealthTier.REMOTE_BRIDGE:
                await self._activate_remote()
            elif tier in (StealthTier.CLOAK, StealthTier.PLAYWRIGHT):
                await self._activate_local(tier, profile)
            else:
                raise ValueError(f"Unsupported tier: {tier}")

            return {
                "active_tier": tier.value,
                "browser_ready": self._state.browser_ctx is not None,
                "remote_connected": self._state.remote_ctx is not None,
                "local_cached": self._state.local_ctx is not None,
                "profile": profile,
            }

    async def _activate_remote(self) -> None:
        """Activate the remote-bridge backend, keeping the local cache warm."""
        self._cancel_idle_timer()
        # Start the idle timer so a warm local browser doesn't sit
        # consuming RAM forever after the user switches to remote.
        if self._state.local_ctx is not None:
            self._start_idle_timer()

        if self._state.remote_ctx is not None:
            self._state.browser_ctx = SecureBrowserContext(
                self._state.remote_ctx, self._config
            )
            logger.info("context_manager: activated remote_bridge (extension live)")
        else:
            self._state.browser_ctx = None
            logger.info(
                "context_manager: switched to remote_bridge, waiting for extension"
            )
        self._state.active_tier = StealthTier.REMOTE_BRIDGE

    async def _activate_local(
        self,
        tier: StealthTier,
        profile: str | None,
    ) -> None:
        """Activate a local backend, re-using the cached browser when possible."""
        self._cancel_idle_timer()
        same_tier = self._state.local_tier == tier
        same_profile = (self._state.local_profile or None) == (profile or None)
        cache_hit = self._state.local_ctx is not None and same_tier and same_profile

        if not cache_hit:
            # Different tier or different profile — old cache is no longer
            # useful. Close it before launching a replacement so we never
            # have two local browsers alive at once.
            await self._close_local()
            raw_ctx = await self._launch_local(tier, profile)
            self._state.local_ctx = raw_ctx
            self._state.local_tier = tier
            self._state.local_profile = profile

        assert self._state.local_ctx is not None
        self._state.browser_ctx = SecureBrowserContext(
            self._state.local_ctx, self._config
        )
        self._state.active_tier = tier
        logger.info(
            "context_manager: activated local tier=%s profile=%s (cache_hit=%s)",
            tier.value,
            profile,
            cache_hit,
        )

    async def _launch_local(
        self,
        tier: StealthTier,
        profile: str | None,
    ) -> Any:
        """Launch a new local backend for ``tier``/``profile``."""
        from agentcloak.core.config import load_config

        paths, _ = load_config()
        profile_dir: Path | None = None
        if profile:
            profile_dir = paths.profiles_dir / profile
            profile_dir.mkdir(parents=True, exist_ok=True)

        extensions = [str(TURNSTILE_PATCH_DIR)] if tier == StealthTier.CLOAK else None

        # Keep the Chromium flag composition in lockstep with
        # ``server.py``'s startup launch — split out here would risk
        # drift between the two paths users can reach the launcher from.
        chromium_args: list[str] = list(self._config.browser.extra_args)
        if not self._config.browser.dns_over_https:
            chromium_args.append("--disable-features=DnsOverHttps")

        return await create_context(
            tier=tier,
            headless=self._config.browser.headless,
            viewport_width=self._config.browser.viewport_width,
            viewport_height=self._config.browser.viewport_height,
            profile_dir=profile_dir,
            humanize=self._config.browser.humanize,
            extensions=extensions,
            # Switch-time launches don't bind to the local TLS proxy —
            # only the initial server start wires httpcloak in. Spelling
            # this out keeps the manager free of httpcloak fallbacks.
            proxy_url=None,
            browser_proxy=self._config.browser.proxy or None,
            extra_args=chromium_args,
            browser_config=self._config.browser,
        )

    # ------------------------------------------------------------------
    # Idle close machinery
    # ------------------------------------------------------------------

    def _start_idle_timer(self) -> None:
        timeout = self._config.bridge.local_idle_timeout
        if timeout <= 0:
            # 0 means "never close" — common when the user wants a
            # permanent warm cache (cheap RAM, fast switch-back).
            return
        loop = asyncio.get_event_loop()
        self._idle_timer = loop.call_later(
            timeout, lambda: asyncio.ensure_future(self._idle_close())
        )

    def _cancel_idle_timer(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    async def _idle_close(self) -> None:
        """Close the cached local browser if we're still on remote tier."""
        if self._state.active_tier != StealthTier.REMOTE_BRIDGE:
            return
        if self._state.local_ctx is None:
            return
        logger.info("context_manager: local idle timeout reached, closing cache")
        await self._close_local()

    async def _close_local(self) -> None:
        """Tear down the cached local browser."""
        ctx = self._state.local_ctx
        if ctx is None:
            return
        with contextlib.suppress(Exception):
            await ctx.close()
        self._state.local_ctx = None
        self._state.local_tier = None
        self._state.local_profile = None
        # If the active context happens to be backed by the local one,
        # invalidate browser_ctx too so the next request gets a clean
        # ``browser_not_ready`` error rather than racing a dead browser.
        if self._state.active_tier in (
            StealthTier.CLOAK,
            StealthTier.PLAYWRIGHT,
        ):
            self._state.browser_ctx = None

    # ------------------------------------------------------------------
    # Process shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Close every backend the manager owns."""
        self._cancel_idle_timer()
        await self._close_local()
        # The remote context is owned by the websocket handler — closing
        # it from here would race the handler's own teardown. Just drop
        # references so the daemon shutdown can complete cleanly.
        self._state.remote_ctx = None
        self._state.browser_ctx = None
