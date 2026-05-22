"""HTTP middleware — localhost gating, request-time tracking, request metrics.

Error envelopes live in ``exception_handlers.py``; this package handles the
cross-cutting request concerns:

* localhost-only access enforcement (the daemon is unauthenticated),
* recording ``last_request_time`` for the idle watchdog,
* request counters for ``/health`` (see :mod:`.metrics`).

:func:`install_middlewares` wires them in the right order — metrics last so it
sits at the outermost layer and counts every request that reaches the daemon.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse

from agentcloak.daemon.middleware.metrics import (
    MetricsState,
    install_metrics_middleware,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import FastAPI, Request
    from starlette.responses import Response

__all__ = ["MetricsState", "install_middlewares"]


# WebSocket endpoints handle their own localhost gating via the bridge token.
# Health is intentionally open so monitoring tools can probe it from anywhere.
_LOCALHOST_BYPASS_PATHS = frozenset(
    {"/health", "/bridge/ws", "/ext", "/openapi.json", "/docs", "/redoc"}
)
# `testclient` is the synthetic client.host that Starlette's TestClient sets
# on its requests. Keeping it in the local set lets the standard test suite
# bypass the localhost gate without having to override the middleware.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _is_localhost(request: Request) -> bool:
    client = request.client
    if not client:
        return True
    return client.host in _LOCAL_HOSTS


def install_middlewares(app: FastAPI) -> None:
    """Attach the localhost gate + request-time recorder + metrics counter.

    Order matters: ``@app.middleware`` is LIFO (last registered runs
    outermost), so the metrics middleware is installed *after* the localhost
    gate to make ``request_count`` include requests the gate rejects.
    """

    @app.middleware("http")
    async def _localhost_and_activity(  # type: ignore[reportUnusedFunction]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Localhost gate
        if request.url.path not in _LOCALHOST_BYPASS_PATHS and not _is_localhost(
            request
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "ok": False,
                    "error": "forbidden",
                    "hint": "Only localhost connections are allowed",
                    "action": "connect from 127.0.0.1 or ::1",
                },
            )

        app.state.last_request_time = time.monotonic()
        return await call_next(request)

    install_metrics_middleware(app)
