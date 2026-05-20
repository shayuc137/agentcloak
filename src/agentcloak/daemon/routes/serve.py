"""Local file-server routes (7a R7) — ``cloak serve <dir>``.

The browser security layer blocks ``file://`` navigations, so previewing a
locally-built HTML artifact needs an HTTP origin. These routes drive a
localhost-only :class:`FileServer` (Starlette static files on uvicorn) that the
daemon shutdown path tears down. This capability lives entirely in the daemon
layer — it never touches the browser context.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from agentcloak.daemon.dependencies import FileServerDep  # noqa: TC001
from agentcloak.daemon.models import (
    OkEnvelope,
    ServeStartRequest,
    ServeStatusResponse,
    ServeStopResponse,
)
from agentcloak.daemon.routes._helpers import _ok

__all__ = ["router"]

router = APIRouter()


@router.post("/serve/start", response_model=OkEnvelope[ServeStatusResponse])
async def handle_serve_start(
    body: ServeStartRequest, file_server: FileServerDep
) -> dict[str, Any]:
    # ``serve_dir_not_found`` / ``serve_start_failed`` raised by the service
    # bubble up to the global handler as structured envelopes.
    result = await file_server.start(body.directory, port=body.port)
    return _ok(result, seq=0)


@router.post("/serve/stop", response_model=OkEnvelope[ServeStopResponse])
async def handle_serve_stop(file_server: FileServerDep) -> dict[str, Any]:
    result = await file_server.stop()
    return _ok(result, seq=0)


@router.get("/serve/status", response_model=OkEnvelope[ServeStatusResponse])
async def handle_serve_status(file_server: FileServerDep) -> dict[str, Any]:
    result = file_server.status()
    return _ok(result, seq=0)
