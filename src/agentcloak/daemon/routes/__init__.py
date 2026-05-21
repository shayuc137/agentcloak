"""FastAPI route definitions — thin shells over the service layer.

Each route handler does three things:

1. parse the Pydantic request body
2. delegate to a service in :mod:`agentcloak.daemon.services`
3. wrap the service's return value in the ``OkEnvelope`` shape

Business logic (stale-ref retry, snapshot diff, profile CRUD, capture export,
doctor checks) lives in the services. Routes intentionally avoid framework
specifics — when something goes wrong they either raise
:class:`AgentBrowserError` (caught by the global handler) or
:class:`HTTPException` with a structured detail dict.

The routes are split into feature-scoped modules so each file stays under
~300 lines and ``git diff`` noise stays localised. ``register_routers``
mounts the merged :data:`router` on a FastAPI app; consumers that want a
single ``router`` object (legacy tests, scripts) can keep importing it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from agentcloak.daemon.routes import (
    bridge,
    browser,
    capture,
    clipboard,
    console,
    debugger,
    download,
    emulation,
    graphql,
    interaction,
    lifecycle,
    pdf,
    performance,
    profiler,
    script,
    serve,
    session,
    sourcemap,
    spells,
    storage,
    streaming,
    tabs,
)
from agentcloak.daemon.routes import (
    route as route_mod,
)
from agentcloak.daemon.services import ActionService

__all__ = ["register_routers", "router"]


def _build_router() -> APIRouter:
    """Merge per-feature routers into a single ``APIRouter`` instance.

    Doing the merge inside a function avoids leaking the intermediate sub
    routers as top-level module attributes (they're already exposed via
    ``agentcloak.daemon.routes.<group>`` for anyone who needs them).
    """
    merged = APIRouter()
    for module in (
        lifecycle,
        browser,
        capture,
        bridge,
        tabs,
        interaction,
        spells,
        console,
        download,
        storage,
        clipboard,
        pdf,
        serve,
        # 7b T1/T2/T3/T4 reverse-engineering surfaces.
        script,
        route_mod,
        emulation,
        graphql,
        streaming,
        debugger,
        sourcemap,
        # 7f profiling / reverse-engineering aids.
        profiler,
        performance,
        # multi-session management
        session,
    ):
        merged.include_router(module.router)
    return merged


router = _build_router()


def register_routers(app: Any) -> None:
    """Register all routes on the FastAPI app."""
    app.include_router(router)


# --- Test-facing helper re-exports ------------------------------------------
# These three callables live on ``ActionService`` and are re-exported here so
# the route-level unit tests (``tests/unit/test_routes.py``) can exercise the
# parsing logic without going through the full daemon stack. They were
# previously module-level functions in the monolithic ``daemon/routes.py``;
# preserving the names keeps the test imports working after the split.

_batch_has_refs = ActionService.has_refs
_resolve_action_refs = ActionService.resolve_refs
_traverse = ActionService.traverse
