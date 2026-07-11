"""FastAPI application factory for the daemon.

`create_app()` is the single entry point for both production and tests. It
wires together: middlewares, exception handlers, routers, and the app.state
slots used by dependency providers. Long-lived resources (browser context,
local proxy, resume writer, etc.) are attached by `server.start()` after the
app is created — the factory itself stays free of side effects so tests can
construct it cheaply with mocks.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

import agentcloak
from agentcloak.daemon.exception_handlers import register_exception_handlers
from agentcloak.daemon.middleware import MetricsState, install_middlewares
from agentcloak.daemon.routes import register_routers

__all__ = ["create_app"]

if TYPE_CHECKING:
    from pathlib import Path


def create_app() -> FastAPI:
    """Build a FastAPI app with all routes/middleware/handlers wired."""
    app = FastAPI(
        title="agentcloak-daemon",
        description=(
            "Browser automation HTTP API. Routes are designed for AI agents "
            "and consumed by the agentcloak CLI and MCP server."
        ),
        version=agentcloak.__version__,
    )

    # app.state default slots — dependency providers expect these to exist.
    # ``configure_app_state()`` overwrites the runtime-meaningful ones (config
    # etc.) after the browser is up. Defaults here are only consumed if a
    # test forgets to call configure_app_state().
    app.state.browser_ctx = None
    app.state.remote_ctx = None
    app.state.bridge_ws = None
    app.state.ext_ws = None
    app.state.local_proxy = None
    app.state.resume_writer = None
    app.state.bridge_token = None
    app.state.config = None
    app.state.config_root = None
    app.state.shutdown_event = asyncio.Event()
    app.state.last_request_time = 0.0
    # Process-start reference for ``/health`` uptime. ``time.monotonic()`` (not
    # wall clock) so uptime stays correct across NTP steps / suspend.
    app.state.started_at = time.monotonic()
    # Request counters bumped by the metrics middleware and read back by
    # ``DiagnosticService.health``. Set here (not in ``configure_app_state``)
    # so TestClient-constructed apps get live counters without extra wiring.
    app.state.metrics = MetricsState()
    app.state.prev_snapshot_lines = None
    # ContextManager-owned slots — populated by ``server.start()`` after
    # the initial browser launch. Routes that only need the active tier
    # (e.g. health) can read these defaults safely during tests.
    app.state.context_manager = None
    app.state.local_ctx = None
    app.state.local_tier = None
    app.state.local_profile = None
    app.state.active_tier = None
    # The session id that switched the daemon to remote_bridge tier (set by
    # ``/launch``). ``get_browser_ctx`` routes only this session to the shared
    # extension-backed ``browser_ctx``; everyone else keeps an isolated local
    # browser. ``None`` falls back to the default session so a daemon booted
    # straight into remote_bridge serves header-less callers from the remote.
    app.state.remote_session_id = None
    # SessionManager owns every *named* session (X-Agentcloak-Session header);
    # the default session stays on ``browser_ctx`` above. ``None`` here means
    # "single-session mode" — the provider falls back to ``browser_ctx`` so
    # tests that never call ``configure_app_state`` keep working unchanged.
    app.state.session_manager = None
    # Embedded static file server for ``cloak serve`` (7a R7). Lazily created
    # on first ``/serve/start`` and torn down by the daemon shutdown path.
    app.state.file_server = None
    # BridgeService is instantiated by ``server.start()`` after the
    # initial config is loaded; routes that depend on it (``BridgeServiceDep``)
    # raise 503 if accessed before then. Defaulting to ``None`` here lets
    # tests construct an app without going through the full startup path.
    app.state.bridge_service = None

    install_middlewares(app)
    register_exception_handlers(app)
    register_routers(app)

    return app


def configure_app_state(
    app: FastAPI,
    *,
    browser_ctx: Any,
    local_proxy: Any = None,
    resume_writer: Any = None,
    bridge_token: str | None = None,
    config: Any = None,
    config_root: Path | None = None,
    session_manager: Any = None,
) -> None:
    """Attach runtime resources to an existing app.

    Called by `server.start()` after the browser is launched. Keeping this
    separate from `create_app()` makes the factory test-friendly.

    The watchdog timeout is driven by a local variable in
    :func:`agentcloak.daemon.server.start` — there's no
    ``app.state.idle_timeout`` slot because nothing reads it after this
    function returns.

    ``session_manager`` is the :class:`SessionManager` that multiplexes
    named (non-default) sessions. ``None`` keeps the daemon in
    single-session mode where every request resolves to ``browser_ctx``.
    """
    app.state.browser_ctx = browser_ctx
    app.state.local_proxy = local_proxy
    app.state.resume_writer = resume_writer
    app.state.bridge_token = bridge_token
    app.state.config = config
    app.state.config_root = config_root
    app.state.session_manager = session_manager
