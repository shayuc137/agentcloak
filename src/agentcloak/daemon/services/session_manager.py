"""Per-caller tabs and scheduling over the daemon's shared browser/profile."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from agentcloak.browser.secure_ctx import SecureBrowserContext
from agentcloak.core.errors import AgentBrowserError
from agentcloak.core.resume import ResumeWriter
from agentcloak.core.types import StealthTier

if TYPE_CHECKING:
    from collections.abc import Callable

    from agentcloak.core.config import AgentcloakConfig

__all__ = ["SessionManager", "SessionSlot"]
DEFAULT_SESSION_ID = "default"


@dataclass
class SessionSlot:
    session_id: str
    ctx: Any | None = None
    last_request_time: float = field(default_factory=time.monotonic)
    tier: StealthTier = StealthTier.CLOAK
    created_at: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    observation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    resume: ResumeWriter = field(default_factory=ResumeWriter)
    cache: SimpleNamespace = field(default_factory=SimpleNamespace)
    users: int = 0

    @property
    def state(self) -> str:
        return "active" if self.ctx is not None else "suspended"


class SessionManager:
    def __init__(
        self,
        config: AgentcloakConfig,
        *,
        app_state: Any,
        hide_selectors_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        self._config = config
        self._state = app_state
        self._hide_selectors_provider = hide_selectors_provider
        self._sessions: dict[str, SessionSlot] = {}
        self._lock = asyncio.Lock()
        self._owner: Any = None

    def slot(self, session_id: str) -> SessionSlot:
        return self._sessions.setdefault(session_id, SessionSlot(session_id=session_id))

    def peek(self, session_id: str) -> Any:
        slot = self._sessions.get(session_id)
        return slot.ctx if slot is not None else None

    async def get_or_create(self, session_id: str) -> Any:
        async with self._lock:
            owner = await self._state.context_manager.ensure_local()
            if owner is not self._owner:
                await self._close_slots()
                self._owner = owner
            slot = self.slot(session_id)
            if slot.ctx is not None and not slot.ctx.is_alive():
                await self._close_ctx(slot.ctx)
                slot.ctx = None
                slot.cache = SimpleNamespace()
                slot.resume = ResumeWriter()
            if slot.ctx is None:
                raw = await owner.fork_session()
                slot.ctx = SecureBrowserContext(raw, self._config)
                slot.tier = raw.stealth_tier
                selectors = (
                    self._hide_selectors_provider()
                    if self._hide_selectors_provider
                    else []
                )
                await slot.ctx.hide_manager.load(selectors)
            slot.last_request_time = time.monotonic()
            return slot.ctx

    async def launch_session(
        self, session_id: str, tier: StealthTier, **profile: str | None
    ) -> dict[str, Any]:
        async with self._lock:
            current_profile = self._state.local_profile
            changing = (
                tier != self._state.active_tier
                or profile.get("profile", current_profile) != current_profile
            )
            if changing and any(
                slot.session_id != session_id and (slot.ctx is not None or slot.users)
                for slot in self._sessions.values()
            ):
                raise AgentBrowserError(
                    error="sessions_active",
                    hint="Changing browser tier/profile would affect another session",
                    action="close the other sessions before switching tier/profile",
                )
            slot = self._sessions.get(session_id)
            if slot is not None and slot.ctx is not None:
                await self._close_ctx(slot.ctx)
                slot.ctx = None
                slot.cache = SimpleNamespace()
                slot.resume = ResumeWriter()
            return await self._state.context_manager.switch_tier(tier, **profile)

    async def close_session(self, session_id: str) -> bool:
        async with self._lock:
            slot = self._sessions.get(session_id)
            if slot is None or slot.ctx is None:
                return False
            await self._close_ctx(slot.ctx)
            slot.ctx = None
            slot.cache = SimpleNamespace()
            slot.resume = ResumeWriter()
            return True

    async def _close_slots(self) -> None:
        for slot in self._sessions.values():
            if slot.ctx is not None:
                await self._close_ctx(slot.ctx)
            slot.ctx = None
            slot.cache = SimpleNamespace()

    async def close_all(self) -> None:
        async with self._lock:
            await self._close_slots()
            self._sessions.clear()

    def list_sessions(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        return [
            {
                "session_id": slot.session_id,
                "state": slot.state,
                "tier": slot.tier.value,
                "idle_seconds": round(now - slot.last_request_time, 1),
            }
            for slot in self._sessions.values()
        ]

    async def cleanup_idle(self, timeout: float) -> list[str]:
        if timeout <= 0:
            return []
        suspended: list[str] = []
        async with self._lock:
            now = time.monotonic()
            for session_id, slot in self._sessions.items():
                if slot.ctx is None or slot.users or slot.lock.locked():
                    continue
                if now - slot.last_request_time < timeout:
                    continue
                await self._close_ctx(slot.ctx)
                slot.ctx = None
                slot.cache = SimpleNamespace()
                suspended.append(session_id)
        return suspended

    def all_idle(self, timeout: float) -> bool:
        now = time.monotonic()
        return all(
            not slot.users
            and (slot.ctx is None or now - slot.last_request_time >= timeout)
            for slot in self._sessions.values()
        )

    @property
    def active_count(self) -> int:
        return sum(slot.ctx is not None for slot in self._sessions.values())

    async def _close_ctx(self, ctx: Any) -> None:
        with contextlib.suppress(Exception):
            await ctx.close()
