"""Local access and cancellable, bounded per-session HTTP scheduling."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import unquote

import structlog
from fastapi.responses import JSONResponse
from starlette.requests import Request

from agentcloak.daemon.dependencies import workspace_scope
from agentcloak.daemon.middleware.metrics import MetricsState
from agentcloak.daemon.scheduling import Scheduling, scheduling_for

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = ["MetricsState", "install_middlewares"]
logger = structlog.get_logger()
_LOCALHOST_BYPASS_PATHS = frozenset(
    {"/health", "/ext", "/openapi.json", "/docs", "/redoc"}
)
_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _is_localhost(request: Request) -> bool:
    return not request.client or request.client.host in _LOCAL_HOSTS


class SessionMiddleware:
    def __init__(self, app: ASGIApp, *, state: Any) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        metrics: MetricsState | None = getattr(self.state, "metrics", None)
        if metrics:
            metrics.enter()
        try:
            await self._http(scope, receive, send)
        finally:
            if metrics:
                metrics.exit()

    async def _http(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        path = request.url.path
        if path not in _LOCALHOST_BYPASS_PATHS and not _is_localhost(request):
            await self._error(
                scope,
                receive,
                send,
                "forbidden",
                "Only localhost connections are allowed",
                403,
            )
            return
        self.state.last_request_time = time.monotonic()
        manager = getattr(self.state, "session_manager", None)
        policy = scheduling_for(path)
        if manager is None or policy == Scheduling.BYPASS:
            await self.app(scope, receive, send)
            return

        # Own receive once: after buffering the body, only the disconnect watcher
        # reads the socket. Cancelling the actual ASGI task cancels route work too.
        body = await request.body()
        payload: dict[str, Any] = {}
        with contextlib.suppress(ValueError):
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                payload = cast("dict[str, Any]", parsed)
        session_id = request.headers.get("x-agentcloak-session", "default")
        if path == "/session/close" and isinstance(payload.get("session_id"), str):
            session_id = payload["session_id"] or session_id
        slot = manager.slot(session_id, **workspace_scope(request))
        request_logger = logger.bind(workspace=slot.workspace_id)
        slot.label = unquote(
            request.headers.get("x-agentcloak-session-label", slot.label)
        )
        slot.workspace_path = unquote(
            request.headers.get("x-agentcloak-workspace-path", slot.workspace_path)
        )
        if slot.closing:
            await self._error(
                scope,
                receive,
                send,
                "session_busy",
                "Session recovery is in progress",
                409,
            )
            return
        force = path == "/session/close" and payload.get("force") is True
        disconnected = asyncio.Event()
        body_sent = False
        response_started = False

        async def replay() -> Message:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        async def watch_disconnect() -> None:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    disconnected.set()
                    return

        config = manager.config.browser
        timeout = max(config.action_timeout / 1000, 0.001)
        request_timeout = timeout
        explicit_timeout = payload.get("timeout")
        if (
            isinstance(explicit_timeout, int | float)
            and not isinstance(explicit_timeout, bool)
            and explicit_timeout > 0
        ):
            if path in {"/navigate", "/fetch"}:
                request_timeout = max(float(explicit_timeout), timeout)
            elif path in {"/wait", "/cdp/send"}:
                request_timeout = max(float(explicit_timeout) / 1000, timeout)
        elif path in {"/navigate", "/fetch"}:
            request_timeout = max(float(config.navigation_timeout), timeout)
        # Give explicit protocol timeouts room to produce their more specific error.
        if path == "/cdp/send":
            request_timeout += 0.1
        if force:
            request_timeout = 0.95
        task: asyncio.Task[None]

        async def execute() -> None:
            acquired: list[asyncio.Lock] = []
            waiting = False
            try:
                if not force and policy != Scheduling.RELEASE:
                    route_manager = getattr(slot.ctx, "_route_mgr", None)
                    observing_hold = (
                        policy == Scheduling.OBSERVE
                        and route_manager is not None
                        and route_manager.pending()
                    )
                    locks = (
                        [slot.observation_lock]
                        if observing_hold
                        else [slot.lock, slot.observation_lock]
                    )
                    waiting = True
                    slot.queued += 1
                    try:
                        async with asyncio.timeout(timeout):
                            for lock in locks:
                                await lock.acquire()
                                acquired.append(lock)
                    except TimeoutError:
                        await self._error(
                            scope,
                            replay,
                            tracked_send,
                            "session_busy",
                            "Session is occupied by "
                            + (", ".join(slot.active.values()) or "another request"),
                            409,
                        )
                        return
                    finally:
                        slot.queued -= 1
                        waiting = False
                    if not observing_hold and policy == Scheduling.SERIAL:
                        slot.observation_lock.release()
                        acquired.remove(slot.observation_lock)
                request_logger.info(
                    "request_acquired",
                    session=session_id,
                    path=path,
                    time=time.monotonic(),
                )
                slot.active[task] = path
                request_logger.info(
                    "request_started",
                    session=session_id,
                    path=path,
                    time=time.monotonic(),
                )
                try:
                    async with asyncio.timeout(request_timeout):
                        await self.app(scope, replay, tracked_send)
                except TimeoutError:
                    if not response_started:
                        await self._error(
                            scope,
                            replay,
                            tracked_send,
                            "action_timeout",
                            f"{path} exceeded {request_timeout:g}s",
                            408,
                        )
            finally:
                if waiting:
                    slot.queued -= 1
                slot.active.pop(task, None)
                for lock in reversed(acquired):
                    lock.release()

        slot.users += 1
        request_logger.info(
            "request_entered", session=session_id, path=path, time=time.monotonic()
        )
        task = asyncio.create_task(execute(), name=f"session:{session_id}:{path}")
        if not force:
            slot.tasks.add(task)
        watcher = asyncio.create_task(watch_disconnect())
        try:
            done, _ = await asyncio.wait(
                {task, watcher}, return_when=asyncio.FIRST_COMPLETED
            )
            if watcher in done and not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                if not disconnected.is_set() and not response_started:
                    await self._error(
                        scope,
                        replay,
                        tracked_send,
                        "session_cancelled",
                        "Session request cancelled; navigate before retrying",
                        409,
                    )
        finally:
            watcher.cancel()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, watcher, return_exceptions=True)
            slot.tasks.discard(task)
            slot.users -= 1
            slot.last_request_time = time.monotonic()
            request_logger.info(
                "request_finished",
                session=session_id,
                path=path,
                time=slot.last_request_time,
            )

    @staticmethod
    async def _error(
        scope: Scope, receive: Receive, send: Send, code: str, hint: str, status: int
    ) -> None:
        response = JSONResponse(
            status_code=status,
            content={
                "ok": False,
                "error": code,
                "hint": hint,
                "action": "inspect session list; use session close --force to recover",
            },
        )
        await response(scope, receive, send)


def install_middlewares(app: FastAPI) -> None:
    app.add_middleware(SessionMiddleware, state=app.state)
