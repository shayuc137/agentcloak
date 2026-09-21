"""Integration test fixtures — browser context (dual-backend) + local HTTP server.

The local HTTP server is built on Starlette/uvicorn so the test stack matches
the production toolchain (FastAPI + Starlette + httpx + websockets).
"""

from __future__ import annotations

import asyncio
import os
import socket
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    import pytest

import pytest_asyncio
import uvicorn
from starlette.applications import Starlette
from starlette.requests import (
    Request,  # noqa: TC002  (runtime annotation in async route handler)
)
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

# Headless by default; set AGENTCLOAK_TEST_HEADED=1 to run headed
_HEADLESS = os.environ.get("AGENTCLOAK_TEST_HEADED", "").lower() not in ("1", "true")

# ---------------------------------------------------------------------------
# Local HTTP server serving test HTML pages
# ---------------------------------------------------------------------------

_PAGES_DIR = Path(__file__).parent / "pages"


def _build_static_app() -> Starlette:
    """Starlette app that serves static files from the pages/ directory."""

    async def _serve(request: Request) -> Response:
        name = request.path_params["name"]
        path = _PAGES_DIR / name
        if not path.is_file():
            return Response("Not Found", status_code=404)
        return Response(
            content=path.read_bytes(),
            media_type="text/html",
        )

    async def _root(_: Request) -> Response:
        return RedirectResponse("/index.html")

    return Starlette(
        routes=[
            Route("/", _root, methods=["GET"]),
            Route("/{name:path}", _serve, methods=["GET"]),
        ]
    )


def _pick_free_port() -> int:
    """Reserve a free TCP port by binding to 0 and releasing immediately."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def local_server() -> AsyncGenerator[str, None]:
    """Session-scoped local HTTP server for test pages."""
    app = _build_static_app()
    port = _pick_free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config=config)
    task = asyncio.create_task(server.serve())

    # Wait until uvicorn has started accepting connections.
    for _ in range(50):
        if server.started:
            break
        await asyncio.sleep(0.05)

    base_url = f"http://127.0.0.1:{port}"
    try:
        yield base_url
    finally:
        server.should_exit = True
        with suppress(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------
# Browser context fixture — dual-backend parametrization
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(
    params=["playwright", "cloak"],
    scope="session",
    loop_scope="session",
)
async def browser_context(
    request: pytest.FixtureRequest,
) -> AsyncGenerator[Any, None]:
    """Launch a real headless browser context (playwright or cloak)."""
    backend = request.param

    if backend == "cloak":
        from agentcloak.browser.cloak_ctx import launch_cloak

        ctx = await launch_cloak(
            headless=_HEADLESS,
            viewport_width=1280,
            viewport_height=720,
            # Coordinate assertions must not inherit humanized pointer jitter.
            humanize=False,
        )
    else:
        from agentcloak.browser.playwright_ctx import launch_playwright

        ctx = await launch_playwright(
            headless=_HEADLESS,
            viewport_width=1280,
            viewport_height=720,
        )

    yield ctx
    await ctx.close()


@pytest_asyncio.fixture
async def fresh_context() -> AsyncGenerator[Any, None]:
    """Function-scoped playwright context for tests that need isolation."""
    from agentcloak.browser.playwright_ctx import launch_playwright

    ctx = await launch_playwright(
        headless=_HEADLESS,
        viewport_width=1280,
        viewport_height=720,
    )
    yield ctx
    await ctx.close()
