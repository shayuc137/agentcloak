"""Session-aware remote_bridge routing in ``get_browser_ctx`` (06-02 fix).

Background
----------
Two subsystems used to collide: ``ContextManager`` owns the *default* session's
``app.state.browser_ctx`` (and honours tier switches, including remote_bridge),
while ``SessionManager`` owns every *named* ``X-Agentcloak-Session`` and
hard-downgrades remote_bridge to a local ``cloak`` browser. In a Claude Code
environment every request carries a named session header, so
``launch --tier remote_bridge`` switched the default session while the actual
navigate/snapshot landed on a named session pinned to a local browser.

The fix records the launching session id on ``app.state.remote_session_id`` and
routes *only that session* to the shared ``browser_ctx`` (the extension-backed
remote ctx) before the SessionManager fork. These tests exercise that fork
directly against the dependency providers — no TestClient, no real browser —
so each routing combination is asserted in isolation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from agentcloak.core.types import StealthTier
from agentcloak.daemon.dependencies import (
    get_browser_ctx,
    get_optional_browser_ctx,
)


class _FakeSessionManager:
    """Records ``get_or_create`` calls and hands back a sentinel ctx."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.ctx = SimpleNamespace(name="session_local_browser")

    async def get_or_create(self, session_id: str) -> Any:
        self.calls.append(session_id)
        return self.ctx


def _make_request(
    *,
    active_tier: StealthTier | None,
    remote_session_id: str | None,
    browser_ctx: Any,
    session_header: str | None,
    session_manager: Any,
) -> Any:
    """Build a minimal stand-in for a Starlette ``Request``.

    The providers only touch ``request.app.state`` and ``request.headers.get``,
    so a ``SimpleNamespace`` with a dict-backed headers shim is sufficient and
    avoids spinning up the full ASGI machinery.
    """
    state = SimpleNamespace(
        active_tier=active_tier,
        remote_session_id=remote_session_id,
        browser_ctx=browser_ctx,
        session_manager=session_manager,
    )
    headers: dict[str, str] = {}
    if session_header is not None:
        headers["x-agentcloak-session"] = session_header
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        headers=headers,
    )


_REMOTE_CTX = SimpleNamespace(name="shared_remote_ctx")


@pytest.mark.asyncio
async def test_launching_session_routes_to_shared_remote_ctx() -> None:
    """Session A launched remote_bridge → its requests hit the shared ctx."""
    mgr = _FakeSessionManager()
    req = _make_request(
        active_tier=StealthTier.REMOTE_BRIDGE,
        remote_session_id="claude-A",
        browser_ctx=_REMOTE_CTX,
        session_header="claude-A",
        session_manager=mgr,
    )
    ctx = await get_browser_ctx(req)
    assert ctx is _REMOTE_CTX
    # SessionManager must NOT be consulted for the launching session.
    assert mgr.calls == []


@pytest.mark.asyncio
async def test_other_session_keeps_local_browser() -> None:
    """Session B (a different named session) stays on its isolated local ctx."""
    mgr = _FakeSessionManager()
    req = _make_request(
        active_tier=StealthTier.REMOTE_BRIDGE,
        remote_session_id="claude-A",
        browser_ctx=_REMOTE_CTX,
        session_header="claude-B",
        session_manager=mgr,
    )
    ctx = await get_browser_ctx(req)
    assert ctx is mgr.ctx
    assert mgr.calls == ["claude-B"]


@pytest.mark.asyncio
async def test_local_tier_named_session_unaffected() -> None:
    """With a local tier active, named sessions always go through SessionManager."""
    mgr = _FakeSessionManager()
    req = _make_request(
        active_tier=StealthTier.CLOAK,
        remote_session_id=None,
        browser_ctx=_REMOTE_CTX,
        session_header="claude-A",
        session_manager=mgr,
    )
    ctx = await get_browser_ctx(req)
    assert ctx is mgr.ctx
    assert mgr.calls == ["claude-A"]


@pytest.mark.asyncio
async def test_launching_session_no_extension_raises_503() -> None:
    """remote_bridge active but extension not connected → browser_not_ready."""
    mgr = _FakeSessionManager()
    req = _make_request(
        active_tier=StealthTier.REMOTE_BRIDGE,
        remote_session_id="claude-A",
        browser_ctx=None,
        session_header="claude-A",
        session_manager=mgr,
    )
    with pytest.raises(HTTPException) as exc_info:
        await get_browser_ctx(req)
    assert exc_info.value.status_code == 503
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["error"] == "browser_not_ready"
    # The hint must steer the agent toward the extension, not "wait for startup".
    assert "extension" in detail["hint"]
    assert mgr.calls == []


@pytest.mark.asyncio
async def test_default_session_fallback_when_remote_session_unset() -> None:
    """Daemon booted into remote_bridge with no /launch yet.

    ``remote_session_id`` is ``None``, so a header-less / default caller falls
    back to the shared remote ctx rather than getting a local browser.
    """
    mgr = _FakeSessionManager()
    req = _make_request(
        active_tier=StealthTier.REMOTE_BRIDGE,
        remote_session_id=None,
        browser_ctx=_REMOTE_CTX,
        session_header=None,  # → "default"
        session_manager=mgr,
    )
    ctx = await get_browser_ctx(req)
    assert ctx is _REMOTE_CTX
    assert mgr.calls == []


@pytest.mark.asyncio
async def test_optional_ctx_launching_session_routes_remote() -> None:
    """``get_optional_browser_ctx`` honours the same remote override ..."""
    mgr = _FakeSessionManager()
    req = _make_request(
        active_tier=StealthTier.REMOTE_BRIDGE,
        remote_session_id="claude-A",
        browser_ctx=_REMOTE_CTX,
        session_header="claude-A",
        session_manager=mgr,
    )
    ctx = await get_optional_browser_ctx(req)
    assert ctx is _REMOTE_CTX
    assert mgr.calls == []


@pytest.mark.asyncio
async def test_optional_ctx_no_extension_returns_none() -> None:
    """... and returns ``None`` (never raises) when the extension is absent."""
    mgr = _FakeSessionManager()
    req = _make_request(
        active_tier=StealthTier.REMOTE_BRIDGE,
        remote_session_id="claude-A",
        browser_ctx=None,
        session_header="claude-A",
        session_manager=mgr,
    )
    ctx = await get_optional_browser_ctx(req)
    assert ctx is None
    assert mgr.calls == []
