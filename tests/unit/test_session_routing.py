"""Caller identity and the concurrency contract at the daemon HTTP boundary."""

from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI, Request

from agentcloak.cli.app import _extract_global_flags
from agentcloak.core.config import AgentcloakConfig
from agentcloak.core.session import auto_detect_session_id, cli_session_id
from agentcloak.daemon.middleware import install_middlewares
from agentcloak.daemon.services.session_manager import SessionManager

if TYPE_CHECKING:
    from pathlib import Path


def test_identity_priority_and_worktree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENTCLOAK_SESSION", raising=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        MagicMock(
            return_value=SimpleNamespace(stdout="/code/feature-ui\n/code/main/.git\n")
        ),
    )
    token = cli_session_id.set(None)
    try:
        assert auto_detect_session_id().startswith("session-")
        monkeypatch.setenv("AGENTCLOAK_SESSION", "explicit-env")
        assert auto_detect_session_id() == "explicit-env"
        cli_session_id.set("explicit-cli")
        assert auto_detect_session_id() == "explicit-cli"
    finally:
        cli_session_id.reset(token)


def test_non_git_cwd_is_stable_and_distinct(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("AGENTCLOAK_SESSION", raising=False)
    monkeypatch.setattr(subprocess, "run", MagicMock(side_effect=FileNotFoundError))
    token = cli_session_id.set(None)
    try:
        monkeypatch.chdir(tmp_path)
        first = auto_detect_session_id()
        assert first == auto_detect_session_id()
        child = tmp_path / "other"
        child.mkdir()
        monkeypatch.chdir(child)
        assert auto_detect_session_id() != first
    finally:
        cli_session_id.reset(token)


@pytest.mark.parametrize(
    "argv",
    [
        ["--session", "alpha", "navigate", "https://example.com"],
        ["navigate", "https://example.com", "--session", "alpha"],
        ["navigate", "https://example.com", "--session=alpha"],
    ],
)
def test_session_flag_position(argv: list[str]) -> None:
    args, state = _extract_global_flags(argv)
    assert args == ["navigate", "https://example.com"]
    assert state["session"] == "alpha"


@pytest.mark.asyncio
async def test_queue_is_per_session_and_hold_escape_remains_reachable() -> None:
    app = FastAPI()
    manager = SessionManager(AgentcloakConfig(), app_state=app.state)
    app.state.session_manager = manager
    install_middlewares(app)
    manager.slot("alpha").ctx = SimpleNamespace(
        _route_mgr=SimpleNamespace(pending=lambda: [{"identifier": "held"}])
    )
    started = asyncio.Event()
    release = asyncio.Event()
    visited: list[str] = []

    @app.post("/navigate")
    async def navigate(request: Request) -> dict[str, bool]:
        name = request.headers["x-agentcloak-session"]
        visited.append(name)
        if name == "alpha" and visited.count(name) == 1:
            started.set()
            await release.wait()
        return {"ok": True}

    @app.get("/screenshot")
    async def screenshot() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/route/release")
    async def release_route() -> dict[str, bool]:
        release.set()
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        first = asyncio.create_task(
            client.post("/navigate", headers={"x-agentcloak-session": "alpha"})
        )
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(
            client.post("/navigate", headers={"x-agentcloak-session": "alpha"})
        )
        await asyncio.wait_for(
            client.post("/navigate", headers={"x-agentcloak-session": "beta"}), 1
        )
        assert visited == ["alpha", "beta"]
        assert not second.done()
        await asyncio.wait_for(
            client.get("/screenshot", headers={"x-agentcloak-session": "alpha"}), 1
        )
        assert await manager.cleanup_idle(0.000001) == []
        await asyncio.wait_for(
            client.post("/route/release", headers={"x-agentcloak-session": "alpha"}), 1
        )
        await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert visited == ["alpha", "beta", "alpha"]


@pytest.mark.asyncio
async def test_health_does_not_allocate_session() -> None:
    from agentcloak.daemon.dependencies import get_optional_browser_ctx

    manager = MagicMock()
    manager.peek.return_value = None
    manager.get_or_create = AsyncMock()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(session_manager=manager, active_tier=None)
        ),
        headers={},
    )
    assert await get_optional_browser_ctx(request) is None
    manager.get_or_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_launch_cannot_switch_profile_under_a_sibling() -> None:
    from agentcloak.core.errors import AgentBrowserError
    from agentcloak.core.types import StealthTier

    state = SimpleNamespace(
        local_profile="shared-profile",
        active_tier=StealthTier.CLOAK,
        context_manager=SimpleNamespace(switch_tier=AsyncMock(return_value={})),
    )
    manager = SessionManager(AgentcloakConfig(), app_state=state)
    sibling = manager.slot("beta")
    sibling.ctx = SimpleNamespace(close=AsyncMock())
    with pytest.raises(AgentBrowserError, match="another session"):
        await manager.launch_session("alpha", StealthTier.CLOAK, profile="other")
    state.context_manager.switch_tier.assert_not_awaited()
    sibling.ctx.close.assert_not_awaited()
    await manager.launch_session("alpha", StealthTier.CLOAK)
    state.context_manager.switch_tier.assert_awaited_once_with(StealthTier.CLOAK)


@pytest.mark.asyncio
async def test_launch_switch_rejects_a_sibling_still_creating_its_tab() -> None:
    from agentcloak.core.errors import AgentBrowserError
    from agentcloak.core.types import StealthTier

    state = SimpleNamespace(local_profile=None, active_tier=StealthTier.CLOAK)
    manager = SessionManager(AgentcloakConfig(), app_state=state)
    manager.slot("beta").users = 1
    with pytest.raises(AgentBrowserError, match="another session"):
        await manager.launch_session("alpha", StealthTier.PLAYWRIGHT)


@pytest.mark.asyncio
async def test_pending_hold_screenshot_serializes_temporary_viewport_with_set() -> None:
    app = FastAPI()
    manager = SessionManager(AgentcloakConfig(), app_state=app.state)
    app.state.session_manager = manager
    manager.slot("alpha").ctx = SimpleNamespace(
        _route_mgr=SimpleNamespace(pending=lambda: [{"identifier": "held"}])
    )
    install_middlewares(app)
    started = asyncio.Event()
    finish_screenshot = asyncio.Event()
    viewport = {"width": 800}

    @app.get("/screenshot")
    async def screenshot() -> dict[str, bool]:
        original = viewport["width"]
        viewport["width"] = 400
        started.set()
        await finish_screenshot.wait()
        viewport["width"] = original
        return {"ok": True}

    @app.post("/viewport")
    async def set_viewport() -> dict[str, bool]:
        viewport["width"] = 1200
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
        headers={"x-agentcloak-session": "alpha"},
    ) as client:
        shot = asyncio.create_task(client.get("/screenshot"))
        await asyncio.wait_for(started.wait(), 1)
        resize = asyncio.create_task(client.post("/viewport"))
        await asyncio.sleep(0)
        assert not resize.done()
        finish_screenshot.set()
        await asyncio.wait_for(asyncio.gather(shot, resize), 1)
    assert viewport["width"] == 1200


def test_resume_history_is_scoped_to_caller() -> None:
    from agentcloak.daemon.dependencies import get_resume_writer

    state = SimpleNamespace()
    state.session_manager = SessionManager(AgentcloakConfig(), app_state=state)
    request_a = SimpleNamespace(
        app=SimpleNamespace(state=state),
        headers={"x-agentcloak-session": "alpha"},
    )
    request_b = SimpleNamespace(
        app=SimpleNamespace(state=state),
        headers={"x-agentcloak-session": "beta"},
    )
    alpha = get_resume_writer(request_a)
    beta = get_resume_writer(request_b)
    assert alpha is not None and beta is not None
    alpha.mark_dirty(url="https://example.com/a", action_summary={"kind": "alpha"})
    beta.mark_dirty(url="https://example.com/b", action_summary={"kind": "beta"})
    assert alpha.current_snapshot.url == "https://example.com/a"
    assert alpha.current_snapshot.recent_actions == [{"kind": "alpha"}]
    assert beta.current_snapshot.url == "https://example.com/b"
    assert beta.current_snapshot.recent_actions == [{"kind": "beta"}]
    alpha.flush()
    beta.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/session/close",
        "/launch",
        "/tab/switch",
        "/tab/close",
        "/navigate",
        "/action",
        "/tab/new",
    ],
)
async def test_mutation_waits_for_pending_hold_screenshot(path: str) -> None:
    app = FastAPI()
    manager = SessionManager(AgentcloakConfig(), app_state=app.state)
    app.state.session_manager = manager
    slot = manager.slot("alpha")
    slot.ctx = SimpleNamespace(
        _route_mgr=SimpleNamespace(pending=lambda: [{"identifier": "held"}])
    )
    install_middlewares(app)
    shot_started = asyncio.Event()
    finish_shot = asyncio.Event()
    mutation_started = asyncio.Event()
    finish_mutation = asyncio.Event()
    events: list[str] = []

    @app.get("/screenshot")
    async def screenshot() -> dict[str, bool]:
        shot_started.set()
        await finish_shot.wait()
        events.append("shot-done")
        return {"ok": True}

    @app.post(path)
    async def mutate() -> dict[str, bool]:
        events.append("mutate")
        mutation_started.set()
        await finish_mutation.wait()
        return {"ok": True}

    async def wait_for_admission() -> None:
        while not slot.lock.locked():
            await asyncio.sleep(0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
        headers={"x-agentcloak-session": "alpha"},
    ) as client:
        shot = asyncio.create_task(client.get("/screenshot"))
        await asyncio.wait_for(shot_started.wait(), 1)
        mutation = asyncio.create_task(client.post(path, json={}))
        try:
            await asyncio.wait_for(wait_for_admission(), 1)
            assert not mutation_started.is_set()
            finish_shot.set()
            await asyncio.wait_for(mutation_started.wait(), 1)
            assert events == ["shot-done", "mutate"]
        finally:
            finish_shot.set()
            finish_mutation.set()
            await asyncio.wait_for(asyncio.gather(shot, mutation), 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["", "bridge-owner"])
async def test_bridge_owner_close_detaches_only_its_remote_session(target: str) -> None:
    from agentcloak.core.types import StealthTier
    from agentcloak.daemon.app import create_app
    from agentcloak.daemon.context_manager import ContextManager

    app = create_app()
    cfg = AgentcloakConfig()
    state = app.state
    state.config = cfg
    state.context_manager = ContextManager(state, cfg)
    manager = SessionManager(cfg, app_state=state)
    state.session_manager = manager
    local = MagicMock()
    local.close = AsyncMock()
    state.local_ctx = local
    state.local_tier = StealthTier.CLOAK
    state.active_tier = StealthTier.REMOTE_BRIDGE
    state.remote_ctx = object()
    state.browser_ctx = state.remote_ctx
    state.remote_session_id = "bridge-owner"
    sibling = SimpleNamespace(close=AsyncMock())
    manager.slot("local-sibling").ctx = sibling
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
        headers={"x-agentcloak-session": "bridge-owner"},
    ) as client:
        response = await client.post("/session/close", json={"session_id": target})
    assert response.status_code == 200
    assert response.json()["data"]["closed"] is True
    assert state.remote_ctx is None
    assert state.remote_session_id is None
    assert state.local_ctx is local
    assert state.active_tier == StealthTier.CLOAK
    local.close.assert_not_awaited()
    sibling.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_another_caller_cannot_detach_bridge_owner_by_explicit_id() -> None:
    from agentcloak.core.types import StealthTier
    from agentcloak.daemon.app import create_app
    from agentcloak.daemon.context_manager import ContextManager

    app = create_app()
    cfg = AgentcloakConfig()
    state = app.state
    state.config = cfg
    state.context_manager = ContextManager(state, cfg)
    state.session_manager = SessionManager(cfg, app_state=state)
    state.active_tier = StealthTier.REMOTE_BRIDGE
    remote = object()
    state.remote_ctx = remote
    state.browser_ctx = remote
    state.remote_session_id = "bridge-owner"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
        headers={"x-agentcloak-session": "outsider"},
    ) as client:
        response = await client.post(
            "/session/close", json={"session_id": "bridge-owner"}
        )
    assert response.status_code == 200
    assert response.json()["data"]["closed"] is False
    assert state.browser_ctx is remote
    assert state.remote_ctx is remote
    assert state.remote_session_id == "bridge-owner"


async def test_force_close_bypasses_remote_lock_and_interrupts_cleanup() -> None:
    from agentcloak.daemon.routes.session import router

    app = FastAPI()
    manager = SessionManager(AgentcloakConfig(), app_state=app.state)
    app.state.session_manager = manager
    remote_close = AsyncMock(side_effect=AssertionError("remote lock must not be used"))
    app.state.context_manager = SimpleNamespace(close_remote_session=remote_close)
    app.include_router(router)
    install_middlewares(app)
    closed = AsyncMock()
    manager.slot("alpha").ctx = SimpleNamespace(force_close=closed)
    started = asyncio.Event()

    @app.post("/evaluate")
    async def evaluate() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.Event().wait()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
        headers={"x-agentcloak-session": "alpha"},
    ) as client:
        pending = asyncio.create_task(client.post("/evaluate"))
        await asyncio.wait_for(started.wait(), 1)
        response = await asyncio.wait_for(
            client.post("/session/close", json={"force": True}), 1
        )
        assert response.json()["data"]["closed"] is True
        assert (await pending).json()["error"] == "session_cancelled"
        closed.assert_awaited_once()
        remote_close.assert_not_awaited()
