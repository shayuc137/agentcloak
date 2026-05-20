"""Embedded static file server for ``cloak serve <dir>`` (7a R7).

The browser security layer blocks ``file://`` navigations, which makes it
painful to preview a locally-built HTML artifact. ``FileServer`` spins up a
tiny Starlette + uvicorn static server bound to localhost so the agent can
load those files over ``http://127.0.0.1:<port>/...`` instead.

Lifecycle:

* one server per daemon (``ServeService`` keeps the handle on ``app.state``);
* the bind port is auto-allocated from a free socket unless the caller asks
  for a specific one;
* the daemon's shutdown path calls :meth:`stop` so the listener never
  outlives the daemon.

The server is intentionally localhost-only — directory contents are exposed
without auth, so binding to ``0.0.0.0`` would leak the served directory onto
the LAN.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import structlog
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from agentcloak.core.errors import AgentBrowserError

__all__ = ["FileServer"]

logger = structlog.get_logger()

_BIND_HOST = "127.0.0.1"


def _pick_free_port() -> int:
    """Ask the OS for an unused TCP port on the loopback interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((_BIND_HOST, 0))
        return int(sock.getsockname()[1])


class FileServer:
    """A single embedded static-file uvicorn server."""

    def __init__(self) -> None:
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None
        self._directory: Path | None = None
        self._port: int | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def directory(self) -> str | None:
        return str(self._directory) if self._directory is not None else None

    @property
    def port(self) -> int | None:
        return self._port

    @property
    def url(self) -> str | None:
        if self._port is None:
            return None
        return f"http://{_BIND_HOST}:{self._port}"

    async def start(
        self, directory: str, *, port: int | None = None
    ) -> dict[str, object]:
        """Serve ``directory`` over HTTP, returning the bound location.

        Restarting (calling ``start`` while already running) stops the old
        server first so there's only ever one listener per daemon. Raises
        :class:`AgentBrowserError` when the directory is missing.
        """
        root = Path(directory).expanduser().resolve()
        if not root.is_dir():
            raise AgentBrowserError(
                error="serve_dir_not_found",
                hint=f"Directory does not exist or is not a directory: {root}",
                action="pass a path to an existing directory",
            )

        if self._server is not None:
            await self.stop()

        bound_port = port or _pick_free_port()
        app = Starlette(
            routes=[Mount("/", app=StaticFiles(directory=str(root), html=True))]
        )
        config = uvicorn.Config(
            app,
            host=_BIND_HOST,
            port=bound_port,
            log_level="warning",
            access_log=False,
            loop="asyncio",
        )
        server = uvicorn.Server(config)
        # Run ``_serve`` rather than ``serve``: the public ``serve`` wraps the
        # run in ``capture_signals()``, which (on the main thread, where the
        # daemon's event loop lives) would override the daemon's own
        # SIGINT/SIGTERM handlers and restore them on stop — a dangerous side
        # effect for a child component. ``_serve`` does the actual work without
        # touching process-level signals.
        self._task = asyncio.ensure_future(
            server._serve()  # pyright: ignore[reportPrivateUsage]
        )
        # Wait for uvicorn to flip ``started`` so a follow-up navigate doesn't
        # race the socket bind. Bail out (and surface the task's exception) if
        # the serve task dies during startup — e.g. the requested port is busy.
        for _ in range(100):
            if server.started:
                break
            if self._task.done():
                exc = self._task.exception()
                raise AgentBrowserError(
                    error="serve_start_failed",
                    hint=f"File server failed to bind port {bound_port}: {exc}",
                    action="retry without --port to auto-allocate a free port",
                )
            await asyncio.sleep(0.02)
        else:
            await self.stop()
            raise AgentBrowserError(
                error="serve_start_timeout",
                hint=f"File server did not start within ~2s on port {bound_port}",
                action="retry, or pick a different --port",
            )

        self._server = server
        self._directory = root
        self._port = bound_port
        logger.info("file_server_started", directory=str(root), port=bound_port)
        return {
            "running": True,
            "directory": str(root),
            "port": bound_port,
            "url": self.url,
        }

    async def stop(self) -> dict[str, bool]:
        """Stop the running server (no-op when nothing is running)."""
        if self._server is None:
            return {"stopped": False}
        self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            except Exception:
                logger.debug("file_server_stop_task_error", exc_info=True)
        logger.info("file_server_stopped", port=self._port)
        self._server = None
        self._task = None
        self._directory = None
        self._port = None
        return {"stopped": True}

    def status(self) -> dict[str, object]:
        """Return the current server state for ``GET /serve/status``."""
        return {
            "running": self.running,
            "directory": self.directory,
            "port": self.port,
            "url": self.url,
        }
