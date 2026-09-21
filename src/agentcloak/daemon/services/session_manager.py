"""Per-caller tabs and scheduling over the daemon's shared browser/profile."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from agentcloak.browser.secure_ctx import SecureBrowserContext
from agentcloak.core.errors import AgentBrowserError
from agentcloak.core.resume import ResumeWriter
from agentcloak.core.types import StealthTier
from agentcloak.core.workspace import workspace_state_dir

if TYPE_CHECKING:
    from collections.abc import Callable

    from agentcloak.core.config import AgentcloakConfig

__all__ = ["SessionManager", "SessionSlot"]
DEFAULT_SESSION_ID = "default"
logger = logging.getLogger(__name__)


@dataclass
class SessionSlot:
    session_id: str
    workspace_id: str = ""
    ctx: Any | None = None
    last_request_time: float = field(default_factory=time.monotonic)
    tier: StealthTier = StealthTier.CLOAK
    created_at: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    observation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    resume: ResumeWriter = field(default_factory=ResumeWriter)
    cache: SimpleNamespace = field(default_factory=SimpleNamespace)
    users: int = 0
    label: str = ""
    workspace_path: str = ""
    queued: int = 0
    tasks: set[asyncio.Task[None]] = field(default_factory=set[asyncio.Task[None]])
    active: dict[asyncio.Task[None], str] = field(
        default_factory=dict[asyncio.Task[None], str]
    )
    page_recreated: bool = False
    has_page: bool = False
    closing: bool = False

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
        self._sessions: dict[tuple[str, str], SessionSlot] = {}
        self._workspaces: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._owner: Any = None

    @property
    def config(self) -> AgentcloakConfig:
        return self._config

    def slot(self, session_id: str, *, workspace_id: str = "") -> SessionSlot:
        key = (workspace_id, session_id)
        if key not in self._sessions:
            self._sessions[key] = SessionSlot(
                session_id=session_id, workspace_id=workspace_id
            )
        return self._sessions[key]

    def peek(self, session_id: str, *, workspace_id: str = "") -> Any:
        slot = self._sessions.get((workspace_id, session_id))
        return slot.ctx if slot is not None else None

    async def _workspace_owner(self, owner: Any, workspace_id: str) -> Any:
        if self._config.browser.isolation == "shared":
            return owner
        workspace = self._workspaces.get(workspace_id)
        if workspace is not None and not workspace.is_alive():
            for slot in self._sessions.values():
                if slot.workspace_id == workspace_id:
                    await self._reset_slot(slot)
            await self._close_ctx(workspace)
            self._workspaces.pop(workspace_id)
            workspace = None
        if workspace is None:
            root = getattr(self._state, "config_root", None)
            profile = self._state.local_profile or ""
            state_path = (
                workspace_state_dir(Path(root), workspace_id, profile) / "storage.json"
                if root
                else None
            )
            workspace = await owner.fork_workspace(state_path)
            self._workspaces[workspace_id] = workspace
        return workspace

    async def get_or_create(self, session_id: str, *, workspace_id: str = "") -> Any:
        async with self._lock:
            owner = await self._state.context_manager.ensure_local()
            if owner is not self._owner:
                await self._close_slots()
                self._owner = owner
            owner = await self._workspace_owner(owner, workspace_id)
            slot = self.slot(session_id, workspace_id=workspace_id)
            if slot.ctx is not None and not slot.ctx.is_alive():
                await self._close_ctx(slot.ctx)
                slot.ctx = None
                slot.cache = SimpleNamespace()
                slot.resume = ResumeWriter()
            if slot.ctx is None:
                slot.page_recreated = slot.has_page
                raw = await owner.fork_session()
                slot.ctx = SecureBrowserContext(raw, self._config)
                slot.has_page = True
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
        self,
        session_id: str,
        tier: StealthTier,
        *,
        workspace_id: str = "",
        **profile: str | None,
    ) -> dict[str, Any]:
        async with self._lock:
            if (
                tier == StealthTier.REMOTE_BRIDGE
                and self._config.browser.isolation == "workspace"
            ):
                raise AgentBrowserError(
                    error="workspace_isolation_unavailable",
                    hint="RemoteBridge cannot isolate browser storage by workspace",
                    action="use a local backend or restart with isolation=shared",
                )
            remote_owner = getattr(self._state, "remote_session_id", None)
            remote_workspace = getattr(self._state, "remote_workspace_id", "") or ""
            if (
                self._state.active_tier == StealthTier.REMOTE_BRIDGE
                and remote_owner is not None
                and (remote_workspace, remote_owner) != (workspace_id, session_id)
            ):
                raise AgentBrowserError(
                    error="bridge_session_in_use",
                    hint="The Bridge is owned by another workspace or session",
                    action="close it from the owning session before launching",
                )
            current_profile = self._state.local_profile
            changing = (
                tier != self._state.active_tier
                or profile.get("profile", current_profile) != current_profile
            )
            if changing and any(
                (slot.workspace_id, slot.session_id) != (workspace_id, session_id)
                and (slot.ctx is not None or slot.users)
                for slot in self._sessions.values()
            ):
                raise AgentBrowserError(
                    error="sessions_active",
                    hint="Changing browser tier/profile would affect another session",
                    action="close the other sessions before switching tier/profile",
                )
            slot = self._sessions.get((workspace_id, session_id))
            if slot is not None and slot.ctx is not None:
                await self._close_ctx(slot.ctx)
                slot.ctx = None
                slot.cache = SimpleNamespace()
                slot.resume = ResumeWriter()
            if changing:
                await self._close_slots()
            result = await self._state.context_manager.switch_tier(tier, **profile)
            self._state.remote_session_id = (
                session_id if tier == StealthTier.REMOTE_BRIDGE else None
            )
            self._state.remote_workspace_id = (
                workspace_id if tier == StealthTier.REMOTE_BRIDGE else ""
            )
            return result

    async def force_close_session(
        self, session_id: str, *, workspace_id: str = ""
    ) -> bool:
        slot = self.slot(session_id, workspace_id=workspace_id)
        if slot.closing:
            raise AgentBrowserError(
                error="session_busy",
                hint="Session recovery is already in progress",
                action="retry after recovery",
            )
        slot.closing = True
        try:
            tasks = list(slot.tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                try:
                    async with asyncio.timeout(0.15):
                        await asyncio.gather(*tasks, return_exceptions=True)
                except TimeoutError:
                    logger.warning(
                        "Interrupted blocked cleanup for session %s", session_id
                    )
            ctx = slot.ctx
            slot.cache = SimpleNamespace()
            slot.resume = ResumeWriter()
            slot.has_page = False
            slot.page_recreated = False
            if ctx is not None:
                try:
                    async with asyncio.timeout(0.7):
                        await ctx.force_close()
                except TimeoutError as exc:
                    raise AgentBrowserError(
                        error="session_close_timeout",
                        hint="The browser did not finish closing this session",
                        action="retry session close --force; session retained",
                    ) from exc
            slot.ctx = None
            # Keep workspace storage alive; frozen renderers cannot serialize it.
            return ctx is not None
        finally:
            slot.closing = False

    async def close_session(self, session_id: str, *, workspace_id: str = "") -> bool:
        async with self._lock:
            slot = self._sessions.get((workspace_id, session_id))
            if slot is None or slot.ctx is None:
                return False
            await self._reset_slot(slot)
            slot.has_page = False
            slot.page_recreated = False
            await self._release_unused_workspaces()
            return True

    async def _reset_slot(self, slot: SessionSlot) -> None:
        if slot.ctx is not None:
            await self._close_ctx(slot.ctx)
        slot.ctx = None
        slot.cache = SimpleNamespace()
        slot.resume = ResumeWriter()

    async def _release_unused_workspaces(self) -> None:
        active = {
            slot.workspace_id
            for slot in self._sessions.values()
            if slot.ctx is not None
        }
        for workspace_id in set(self._workspaces) - active:
            await self._close_ctx(self._workspaces.pop(workspace_id))

    async def _close_slots(self) -> None:
        for slot in self._sessions.values():
            await self._reset_slot(slot)
        await self._release_unused_workspaces()

    async def close_all(self) -> None:
        async with self._lock:
            await self._close_slots()
            self._sessions.clear()

    def list_sessions(
        self, *, workspace_id: str = "", all_workspaces: bool = False
    ) -> list[dict[str, Any]]:
        now = time.monotonic()
        return [
            {
                "session_id": slot.session_id,
                "workspace_id": slot.workspace_id,
                "state": slot.state,
                "label": slot.label or slot.session_id,
                "workspace_path": slot.workspace_path,
                "active_actions": list(slot.active.values()),
                "queued": slot.queued,
                "tier": slot.tier.value,
                "idle_seconds": round(now - slot.last_request_time, 1),
            }
            for slot in self._sessions.values()
            if all_workspaces or slot.workspace_id == workspace_id
        ]

    async def cleanup_idle(self, timeout: float) -> list[str]:
        if timeout <= 0:
            return []
        suspended: list[str] = []
        async with self._lock:
            now = time.monotonic()
            for slot in self._sessions.values():
                if slot.ctx is None or slot.users or slot.lock.locked():
                    continue
                if now - slot.last_request_time < timeout:
                    continue
                await self._reset_slot(slot)
                suspended.append(slot.session_id)
            await self._release_unused_workspaces()
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
        try:
            await ctx.close()
        except Exception:
            logger.warning(
                "Context cleanup or workspace persistence failed", exc_info=True
            )
