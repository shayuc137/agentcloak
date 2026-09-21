"""FastAPI dependency providers for the daemon.

Centralizes access to the browser context, configuration, and other app-scoped
resources. Route handlers depend on these via `Annotated[T, Depends(...)]` so
that wiring stays explicit and easy to override during testing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, HTTPException, Request

from agentcloak.core.config import AgentcloakConfig, load_config
from agentcloak.core.types import StealthTier

if TYPE_CHECKING:
    import asyncio

    from agentcloak.core.resume import ResumeWriter
    from agentcloak.daemon.services.bridge_service import BridgeService

__all__ = [
    "DEFAULT_SESSION_ID",
    "ActiveTierDep",
    "BridgeServiceDep",
    "BridgeTokenDep",
    "BrowserCtxDep",
    "ConfigDep",
    "ContextManagerDep",
    "FileServerDep",
    "LocalProxyDep",
    "OptionalBrowserCtxDep",
    "RemoteCtxDep",
    "RequiredRemoteCtxDep",
    "ResumeWriterDep",
    "ShutdownEventDep",
    "SnapshotCache",
    "SnapshotCacheDep",
    "get_active_tier",
    "get_bridge_service",
    "get_bridge_token",
    "get_browser_ctx",
    "get_config",
    "get_context_manager",
    "get_file_server",
    "get_local_proxy",
    "get_optional_browser_ctx",
    "get_remote_ctx",
    "get_resume_writer",
    "get_shutdown_event",
    "get_snapshot_cache",
    "require_remote_ctx",
]


@dataclass
class SnapshotCache:
    """Thin wrapper around ``app.state.prev_snapshot_lines`` read/write.

    Snapshot diff mode needs to compare the current AX tree against the
    previous one. The previous tree lives on ``app.state`` because diff
    semantics span across requests, but routes shouldn't poke
    ``app.state`` directly — they go through :class:`SnapshotCacheDep`
    so the access is explicit and easy to mock.
    """

    _state: Any

    @property
    def prev_lines(self) -> Any:
        """Return the previously cached snapshot lines, or ``None``."""
        return getattr(self._state, "prev_snapshot_lines", None)

    @prev_lines.setter
    def prev_lines(self, value: Any) -> None:
        self._state.prev_snapshot_lines = value

    @property
    def signature(self) -> Any:
        """Return the scope identity associated with ``prev_lines``."""
        return getattr(self._state, "prev_snapshot_signature", None)

    @signature.setter
    def signature(self, value: Any) -> None:
        self._state.prev_snapshot_signature = value


_SESSION_HEADER = "x-agentcloak-session"
DEFAULT_SESSION_ID = "default"


def _session_id_of(request: Request) -> str:
    """Return the requested session id, defaulting to ``"default"``.

    Header names are case-insensitive in Starlette; we read the lower-cased
    form for clarity.
    """
    return request.headers.get(_SESSION_HEADER, DEFAULT_SESSION_ID)


def session_id_of(request: Request) -> str:
    """Public alias of :func:`_session_id_of` for cross-module use."""
    return _session_id_of(request)


def workspace_scope(request: Request) -> dict[str, str]:
    workspace_id = request.headers.get("x-agentcloak-workspace", "").strip()
    return {"workspace_id": workspace_id} if workspace_id else {}


def _reject_workspace_bridge(request: Request) -> None:
    config = getattr(request.app.state, "config", None)
    if config is not None and config.browser.isolation == "workspace":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "workspace_isolation_unavailable",
                "hint": "RemoteBridge cannot isolate browser storage by workspace",
                "action": "use a local backend or restart with isolation=shared",
            },
        )


def _routes_to_remote(request: Request) -> bool:
    """Only the Bridge owner may operate the connected user tab."""
    state = request.app.state
    if getattr(state, "active_tier", None) != StealthTier.REMOTE_BRIDGE:
        return False
    launching_session = getattr(state, "remote_session_id", None) or DEFAULT_SESSION_ID
    workspace = getattr(state, "remote_workspace_id", "") or ""
    return (
        _session_id_of(request) == launching_session
        and workspace_scope(request).get("workspace_id", "") == workspace
    )


def _browser_not_ready(request: Request) -> HTTPException:
    """Build the standard ``browser_not_ready`` 503 for the default session."""
    # Tailor the hint based on whether the daemon is in remote_bridge mode
    # (extension not paired) or just hasn't finished startup yet. The hint
    # tells the agent which knob to turn — paying the cost of one ``getattr``
    # keeps the error actionable instead of "wait and retry".
    active_tier = getattr(request.app.state, "active_tier", None)
    tier_value = getattr(active_tier, "value", active_tier)
    if tier_value == "remote_bridge":
        return HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "error": "browser_not_ready",
                "hint": "remote_bridge tier active but no extension connected",
                "action": (
                    "install and connect the agentcloak Chrome extension, "
                    "or call /launch with tier=cloak to use a local browser"
                ),
            },
        )
    return HTTPException(
        status_code=503,
        detail={
            "ok": False,
            "error": "browser_not_ready",
            "hint": "Browser context is not initialized",
            "action": "wait a moment for daemon startup, then retry",
        },
    )


async def get_browser_ctx(request: Request) -> Any:
    """Resolve the caller's tab facade without sharing mutable page state."""
    if _routes_to_remote(request):
        _reject_workspace_bridge(request)
        ctx = request.app.state.browser_ctx
        if ctx is None:
            raise _browser_not_ready(request)
        return ctx
    if getattr(request.app.state, "active_tier", None) == StealthTier.REMOTE_BRIDGE:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "session_isolation_unavailable",
                "hint": "The connected Bridge tab belongs to another session",
                "action": "use the owning session or launch a local browser",
            },
        )
    session_mgr = getattr(request.app.state, "session_manager", None)
    if session_mgr is not None:
        ctx = await session_mgr.get_or_create(
            _session_id_of(request), **workspace_scope(request)
        )
        slot = session_mgr.slot(_session_id_of(request), **workspace_scope(request))
        if slot.page_recreated and request.url.path != "/navigate":
            raise HTTPException(
                status_code=409,
                detail={
                    "ok": False,
                    "error": "page_recreated",
                    "hint": "Previous page lost; a blank page was created",
                    "action": "navigate to the intended URL before capturing",
                },
            )
        return ctx
    ctx = request.app.state.browser_ctx
    if ctx is None:
        raise _browser_not_ready(request)
    return ctx


async def get_optional_browser_ctx(request: Request) -> Any:
    """Health discovery must never allocate a tab or start a browser."""
    if _routes_to_remote(request):
        return getattr(request.app.state, "browser_ctx", None)
    manager = getattr(request.app.state, "session_manager", None)
    if manager is not None:
        return manager.peek(_session_id_of(request), **workspace_scope(request))
    return getattr(request.app.state, "browser_ctx", None)


def get_context_manager(request: Request) -> Any:
    """Return the daemon's :class:`ContextManager`.

    Routes that mutate the active tier (``/launch``) depend on this. The
    manager is created during ``server.start()`` and lives for the
    lifetime of the daemon, so the only failure mode is "daemon still
    bootstrapping" — surfaced as 503 so callers can retry.
    """
    mgr = getattr(request.app.state, "context_manager", None)
    if mgr is None:
        raise HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "error": "context_manager_not_ready",
                "hint": "Daemon is still initialising",
                "action": "retry after a brief delay",
            },
        )
    return mgr


def get_remote_ctx(request: Request) -> Any:
    """Get the bridge/extension remote context if connected, else None."""
    state = request.app.state
    cfg = getattr(state, "config", None)
    if cfg is not None and cfg.browser.isolation == "workspace":
        return None
    if getattr(state, "session_manager", None) is not None and not _routes_to_remote(
        request
    ):
        return None
    return getattr(state, "remote_ctx", None)


def require_remote_ctx(request: Request) -> Any:
    """Get the remote ctx, raising a 400 envelope if no bridge is connected."""
    _reject_workspace_bridge(request)
    remote = getattr(request.app.state, "remote_ctx", None)
    if remote is None:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "no_bridge_connected",
                "hint": "No Chrome Extension connected via bridge or /ext",
                "action": "ensure the Chrome Extension is connected",
            },
        )
    if getattr(
        request.app.state, "session_manager", None
    ) is not None and not _routes_to_remote(request):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "bridge_session_mismatch",
                "hint": "The connected tab belongs to another workspace or session",
                "action": "launch remote_bridge from the owning session",
            },
        )
    return remote


def get_config(request: Request) -> AgentcloakConfig:
    """Get request-time config, reloading production TOML changes immediately.

    Production startup records ``config_root``; tests that inject only
    ``app.state.config`` retain a deterministic snapshot and never touch the
    user's real config file. When a profile is active the per-profile
    ``config.toml`` overlay is re-applied after every reload so ``/health``
    and other config-dependent routes see the merged view.
    """
    config_root = getattr(request.app.state, "config_root", None)
    if config_root is not None:
        from agentcloak.core.config import apply_profile_config

        paths, cfg = load_config(root=config_root)
        local_profile = getattr(request.app.state, "local_profile", None)
        if local_profile:
            apply_profile_config(cfg, paths.profiles_dir / str(local_profile))
        return cfg
    cfg: AgentcloakConfig | None = getattr(request.app.state, "config", None)
    if cfg is not None:
        return cfg
    _, cfg = load_config()
    return cfg


def get_resume_writer(request: Request) -> ResumeWriter | None:
    """Keep each caller's URL and recent actions out of sibling resume data."""
    manager = getattr(request.app.state, "session_manager", None)
    if manager is not None:
        return manager.slot(_session_id_of(request), **workspace_scope(request)).resume
    return getattr(request.app.state, "resume_writer", None)


def get_local_proxy(request: Request) -> Any:
    """Return the httpcloak ``LocalProxy`` handle, or ``None`` if disabled.

    The proxy is only created when the daemon launches a local CloakBrowser
    with httpcloak installed — remote_bridge and Playwright tiers leave this
    ``None``. Surfaced through ``/health`` so an agent can confirm the TLS
    fingerprint preset.
    """
    return getattr(request.app.state, "local_proxy", None)


def get_active_tier(request: Request) -> Any:
    """Return the current :class:`StealthTier`, or ``None`` if not seeded yet.

    Set by :class:`ContextManager` after the initial launch. Routes that need
    a strict guarantee should depend on ``ContextManagerDep`` instead.
    """
    return getattr(request.app.state, "active_tier", None)


def get_snapshot_cache(request: Request) -> SnapshotCache:
    """Keep snapshot diffs scoped to the caller session."""
    manager = getattr(request.app.state, "session_manager", None)
    state = (
        manager.slot(_session_id_of(request), **workspace_scope(request)).cache
        if manager is not None
        else request.app.state
    )
    return SnapshotCache(state)


def get_shutdown_event(request: Request) -> asyncio.Event | None:
    """Return the asyncio Event that signals graceful daemon shutdown.

    ``POST /shutdown`` sets this event; ``server.start()`` watches it to
    drive uvicorn's shutdown sequence.
    """
    return getattr(request.app.state, "shutdown_event", None)


def get_bridge_token(request: Request) -> str | None:
    """Return the active bridge auth token, or ``None`` if unset.

    The token is regenerated on first start and persisted to
    ``config.toml``. ``POST /bridge/token/reset`` rotates it and updates
    this slot atomically so already-paired extensions reject on next
    reconnect.
    """
    return getattr(request.app.state, "bridge_token", None)


def get_file_server(request: Request) -> Any:
    """Return the daemon's :class:`FileServer`, creating it on first use.

    The server is lazily instantiated so the ``serve`` capability costs
    nothing until an agent actually calls ``/serve/start``. The handle lives
    on ``app.state`` so the daemon shutdown path can stop it.
    """
    from agentcloak.daemon.services import FileServer

    server = getattr(request.app.state, "file_server", None)
    if server is None:
        server = FileServer()
        request.app.state.file_server = server
    return server


def get_bridge_service(request: Request) -> BridgeService:
    """Return the :class:`BridgeService` that owns bridge WebSocket lifecycle.

    Created at daemon startup and attached to ``app.state``. Routes that
    accept Chrome Extension WebSocket connections delegate the entire
    connect → message-pump → disconnect flow to this service so the
    route handler stays a thin transport adapter.
    """
    svc = getattr(request.app.state, "bridge_service", None)
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "error": "bridge_service_not_ready",
                "hint": "Daemon bridge service is still initialising",
                "action": "retry after a brief delay",
            },
        )
    return svc


def get_session_manager(request: Request) -> Any:
    """Return the :class:`SessionManager` or ``None`` in single-session mode."""
    return getattr(request.app.state, "session_manager", None)


SessionManagerDep = Annotated[Any, Depends(get_session_manager)]
BrowserCtxDep = Annotated[Any, Depends(get_browser_ctx)]
OptionalBrowserCtxDep = Annotated[Any, Depends(get_optional_browser_ctx)]
RemoteCtxDep = Annotated[Any, Depends(get_remote_ctx)]
RequiredRemoteCtxDep = Annotated[Any, Depends(require_remote_ctx)]
ConfigDep = Annotated[AgentcloakConfig, Depends(get_config)]
ContextManagerDep = Annotated[Any, Depends(get_context_manager)]
ResumeWriterDep = Annotated[Any, Depends(get_resume_writer)]
LocalProxyDep = Annotated[Any, Depends(get_local_proxy)]
ActiveTierDep = Annotated[Any, Depends(get_active_tier)]
SnapshotCacheDep = Annotated[SnapshotCache, Depends(get_snapshot_cache)]
ShutdownEventDep = Annotated[Any, Depends(get_shutdown_event)]
BridgeTokenDep = Annotated[Any, Depends(get_bridge_token)]
BridgeServiceDep = Annotated[Any, Depends(get_bridge_service)]
FileServerDep = Annotated[Any, Depends(get_file_server)]
