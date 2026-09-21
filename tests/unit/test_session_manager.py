"""Session tab facade acquisition, teardown and idle reclamation."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcloak.core.config import AgentcloakConfig
from agentcloak.daemon.services.session_manager import SessionManager, SessionSlot

_MODULE = "agentcloak.daemon.services.session_manager"


class _FakeCtx:
    """Stand-in for a SecureBrowserContext-wrapped backend.

    Only needs an awaitable ``close`` for the manager's teardown path; the
    ``closed`` flag lets tests confirm the browser was actually torn down.
    """

    def __init__(self) -> None:
        self.closed = False
        self.hide_manager = MagicMock()
        self.hide_manager.load = AsyncMock()

    def is_alive(self) -> bool:
        return not self.closed

    async def close(self) -> None:
        self.closed = True


def _make() -> tuple[SessionManager, list[_FakeCtx]]:
    """Build a SessionManager whose shared-browser tab factory is mocked.

    Returns the manager plus the list of fake ctxs handed out (in creation
    order) so a test can assert which browsers were closed.
    """
    created: list[_FakeCtx] = []

    owner = MagicMock()
    raw = MagicMock()
    from agentcloak.core.types import StealthTier

    raw.stealth_tier = StealthTier.CLOAK
    owner.fork_session = AsyncMock(return_value=raw)
    state = MagicMock()
    state.context_manager.ensure_local = AsyncMock(return_value=owner)

    def _fake_secure(_raw: Any, _cfg: Any) -> _FakeCtx:
        ctx = _FakeCtx()
        created.append(ctx)
        return ctx

    mgr = SessionManager(AgentcloakConfig(), app_state=state)
    patcher_secure = patch(f"{_MODULE}.SecureBrowserContext", new=_fake_secure)
    patcher_secure.start()
    mgr._test_patchers = (patcher_secure,)  # type: ignore[attr-defined]
    return mgr, created


def _teardown(mgr: SessionManager) -> None:
    for p in getattr(mgr, "_test_patchers", ()):  # type: ignore[attr-defined]
        p.stop()


class TestAcquisition:
    @pytest.mark.asyncio
    async def test_get_or_create_launches_and_marks_active(self) -> None:
        mgr, created = _make()
        try:
            ctx = await mgr.get_or_create("alpha")
            assert ctx is created[0]
            assert mgr.active_count == 1
            sessions = mgr.list_sessions()
            assert len(sessions) == 1
            assert sessions[0]["session_id"] == "alpha"
            assert sessions[0]["state"] == "active"
        finally:
            _teardown(mgr)

    @pytest.mark.asyncio
    async def test_session_browser_inherits_profile_hide_selectors(self) -> None:
        mgr, created = _make()
        mgr._hide_selectors_provider = lambda: [".toolbar", "#dev-banner"]
        try:
            await mgr.get_or_create("alpha")
            created[0].hide_manager.load.assert_awaited_once_with(
                [".toolbar", "#dev-banner"]
            )
        finally:
            _teardown(mgr)

    @pytest.mark.asyncio
    async def test_session_browser_without_provider_loads_builtin_only(self) -> None:
        mgr, created = _make()
        try:
            await mgr.get_or_create("alpha")
            created[0].hide_manager.load.assert_awaited_once_with([])
        finally:
            _teardown(mgr)

    @pytest.mark.asyncio
    async def test_second_call_returns_same_ctx(self) -> None:
        mgr, created = _make()
        try:
            first = await mgr.get_or_create("alpha")
            second = await mgr.get_or_create("alpha")
            assert first is second
            # Only one browser launched for repeated touches of one session.
            assert len(created) == 1
            assert mgr.active_count == 1
        finally:
            _teardown(mgr)

    @pytest.mark.asyncio
    async def test_distinct_sessions_get_distinct_tab_facades(self) -> None:
        mgr, created = _make()
        try:
            a = await mgr.get_or_create("alpha")
            b = await mgr.get_or_create("beta")
            assert a is not b
            assert len(created) == 2
            assert mgr.active_count == 2
            ids = {s["session_id"] for s in mgr.list_sessions()}
            assert ids == {"alpha", "beta"}
        finally:
            _teardown(mgr)


class TestTeardown:
    @pytest.mark.asyncio
    async def test_close_session_closes_tabs_and_suspends_slot(self) -> None:
        mgr, created = _make()
        try:
            await mgr.get_or_create("alpha")
            removed = await mgr.close_session("alpha")
            assert removed is True
            assert created[0].closed is True
            assert mgr.list_sessions()[0]["state"] == "suspended"
            assert mgr.active_count == 0
        finally:
            _teardown(mgr)

    @pytest.mark.asyncio
    async def test_close_unknown_session_returns_false(self) -> None:
        mgr, _created = _make()
        try:
            assert await mgr.close_session("ghost") is False
        finally:
            _teardown(mgr)

    @pytest.mark.asyncio
    async def test_close_all_closes_every_browser(self) -> None:
        mgr, created = _make()
        try:
            await mgr.get_or_create("alpha")
            await mgr.get_or_create("beta")
            await mgr.close_all()
            assert all(c.closed for c in created)
            assert mgr.list_sessions() == []
            assert mgr.active_count == 0
        finally:
            _teardown(mgr)


class TestIdleReclamation:
    @pytest.mark.asyncio
    async def test_cleanup_idle_suspends_stale_session(self) -> None:
        mgr, created = _make()
        try:
            await mgr.get_or_create("alpha")
            # Backdate the last-request time so the slot is well past timeout.
            mgr.slot("alpha").last_request_time = time.monotonic() - 1000
            suspended = await mgr.cleanup_idle(timeout=300.0)
            assert suspended == ["alpha"]
            # Browser closed (RAM freed) but the slot survives as suspended.
            assert created[0].closed is True
            assert mgr.active_count == 0
            sessions = mgr.list_sessions()
            assert len(sessions) == 1
            assert sessions[0]["state"] == "suspended"
        finally:
            _teardown(mgr)

    @pytest.mark.asyncio
    async def test_cleanup_idle_keeps_fresh_session(self) -> None:
        mgr, created = _make()
        try:
            await mgr.get_or_create("alpha")
            suspended = await mgr.cleanup_idle(timeout=300.0)
            assert suspended == []
            assert created[0].closed is False
            assert mgr.active_count == 1
        finally:
            _teardown(mgr)

    @pytest.mark.asyncio
    async def test_cleanup_idle_zero_timeout_is_noop(self) -> None:
        mgr, created = _make()
        try:
            await mgr.get_or_create("alpha")
            mgr.slot("alpha").last_request_time = time.monotonic() - 1000
            assert await mgr.cleanup_idle(timeout=0.0) == []
            assert created[0].closed is False
        finally:
            _teardown(mgr)

    @pytest.mark.asyncio
    async def test_suspended_session_rebuilds_on_next_request(self) -> None:
        mgr, created = _make()
        try:
            await mgr.get_or_create("alpha")
            mgr.slot("alpha").last_request_time = time.monotonic() - 1000
            await mgr.cleanup_idle(timeout=300.0)
            # Next request transparently relaunches a fresh browser.
            ctx = await mgr.get_or_create("alpha")
            assert ctx is created[1]
            assert len(created) == 2
            assert mgr.active_count == 1
            assert mgr.list_sessions()[0]["state"] == "active"
        finally:
            _teardown(mgr)

    def test_all_idle_true_when_empty(self) -> None:
        mgr, _created = _make()
        try:
            assert mgr.all_idle(timeout=300.0) is True
        finally:
            _teardown(mgr)

    @pytest.mark.asyncio
    async def test_all_idle_reflects_active_session(self) -> None:
        mgr, _created = _make()
        try:
            await mgr.get_or_create("alpha")
            # Fresh session → not idle.
            assert mgr.all_idle(timeout=300.0) is False
            # Backdate → idle again.
            mgr.slot("alpha").last_request_time = time.monotonic() - 1000
            assert mgr.all_idle(timeout=300.0) is True
        finally:
            _teardown(mgr)


class TestSessionSlot:
    def test_state_reflects_ctx_presence(self) -> None:
        slot = SessionSlot(session_id="x")
        assert slot.state == "suspended"  # ctx is None
        slot.ctx = object()
        assert slot.state == "active"


class TestCloseRobustness:
    @pytest.mark.asyncio
    async def test_close_all_survives_wedged_browser(self) -> None:
        """A browser whose close() raises must not block reclaiming the rest."""
        mgr, _created = _make()
        try:
            await mgr.get_or_create("alpha")
            await mgr.get_or_create("beta")
            # Make alpha's close blow up; close_all must still clear everything.
            bad = mgr.slot("alpha").ctx
            bad.close = AsyncMock(side_effect=RuntimeError("wedged"))  # type: ignore[union-attr]
            await mgr.close_all()
            assert mgr.list_sessions() == []
        finally:
            _teardown(mgr)
