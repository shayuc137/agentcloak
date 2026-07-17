"""Tests for daemon/context_manager.py and the bridge-token + /launch wiring."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agentcloak.cli.app import app as cli_app
from agentcloak.core.config import (
    AgentcloakConfig,
    BridgeConfig,
    Paths,
    ensure_bridge_token,
    load_config,
    regenerate_bridge_token,
)
from agentcloak.core.types import StealthTier
from agentcloak.daemon.app import create_app
from agentcloak.daemon.context_manager import ContextManager

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Bridge token persistence
# ---------------------------------------------------------------------------


class TestBridgeTokenPersistence:
    def test_ensure_generates_when_missing(self, tmp_path: Path) -> None:
        paths = Paths(root=tmp_path)
        cfg = AgentcloakConfig()
        token = ensure_bridge_token(paths, cfg)
        assert len(token) >= 32
        assert cfg.bridge.token == token
        # File written and contains the token.
        assert paths.config_file.exists()
        contents = paths.config_file.read_text(encoding="utf-8")
        assert "[bridge]" in contents
        assert token in contents

    def test_ensure_returns_existing(self, tmp_path: Path) -> None:
        paths = Paths(root=tmp_path)
        cfg = AgentcloakConfig(bridge=BridgeConfig(token="preset-token-value"))
        token = ensure_bridge_token(paths, cfg)
        assert token == "preset-token-value"
        # No file should have been written when one already existed in cfg.
        assert not paths.config_file.exists()

    def test_load_round_trip(self, tmp_path: Path) -> None:
        paths = Paths(root=tmp_path)
        cfg = AgentcloakConfig()
        token = ensure_bridge_token(paths, cfg)
        # Reload from disk and confirm we get the same value back.
        _, reloaded = load_config(root=tmp_path)
        assert reloaded.bridge.token == token

    def test_regenerate_rotates(self, tmp_path: Path) -> None:
        paths = Paths(root=tmp_path)
        cfg = AgentcloakConfig()
        first = ensure_bridge_token(paths, cfg)
        second = regenerate_bridge_token(paths, cfg)
        assert second != first
        assert cfg.bridge.token == second

    def test_preserves_other_sections(self, tmp_path: Path) -> None:
        paths = Paths(root=tmp_path)
        paths.ensure_dirs()
        paths.config_file.write_text(
            "[daemon]\nport = 9001\n\n[browser]\nheadless = false\n",
            encoding="utf-8",
        )
        cfg = AgentcloakConfig()
        ensure_bridge_token(paths, cfg)
        # Reload to confirm daemon/browser sections survived.
        _, reloaded = load_config(root=tmp_path)
        assert reloaded.daemon.port == 9001
        assert reloaded.browser.headless is False
        assert reloaded.bridge.token


# ---------------------------------------------------------------------------
# CLI command: agentcloak bridge token
# ---------------------------------------------------------------------------


class TestBridgeTokenCLI:
    def test_show_token(self, tmp_path: Path) -> None:
        runner = CliRunner()
        paths = Paths(root=tmp_path)
        cfg = AgentcloakConfig()
        ensure_bridge_token(paths, cfg)

        # ``load_config`` is imported inside the command function, so we
        # patch it at the source module (``core.config``) instead.
        with patch(
            "agentcloak.core.config.load_config",
            return_value=(paths, cfg),
        ):
            result = runner.invoke(cli_app, ["bridge", "token"])
        assert result.exit_code == 0
        assert cfg.bridge.token in result.stdout

    def test_reset_rotates(self, tmp_path: Path) -> None:
        runner = CliRunner()
        paths = Paths(root=tmp_path)
        cfg = AgentcloakConfig()
        first = ensure_bridge_token(paths, cfg)

        # The CLI talks to a daemon over HTTP when one is available. The test
        # cares about the *fallback* path that updates the local cfg, so force
        # the daemon call to look unreachable. Without this guard the test is
        # racy: a dev who already has a real daemon listening on the default
        # port watches the daemon-side reset succeed (updating *its* cfg) and
        # the test's cfg stays untouched.
        from agentcloak.core.errors import DaemonConnectionError

        with (
            patch(
                "agentcloak.core.config.load_config",
                return_value=(paths, cfg),
            ),
            patch(
                "agentcloak.client.DaemonClient.bridge_token_reset_sync",
                side_effect=DaemonConnectionError(
                    error="daemon_unreachable",
                    hint="forced for test",
                    action="n/a",
                ),
            ),
        ):
            result = runner.invoke(cli_app, ["bridge", "token", "--reset"])
        assert result.exit_code == 0
        assert cfg.bridge.token != first


# ---------------------------------------------------------------------------
# ContextManager state transitions
# ---------------------------------------------------------------------------


def _fake_app_state() -> Any:
    """Minimal stand-in for FastAPI's app.state for ContextManager."""
    state = MagicMock()
    state.browser_ctx = None
    state.local_ctx = None
    state.local_tier = None
    state.local_profile = None
    state.remote_ctx = None
    state.active_tier = None
    return state


def _make_fake_remote_ctx() -> Any:
    remote = MagicMock()
    remote.stealth_tier = MagicMock(value="remote_bridge")
    remote.close = AsyncMock()
    return remote


def _make_fake_local_ctx() -> Any:
    local = MagicMock()
    local.stealth_tier = MagicMock(value="cloak")
    local.close = AsyncMock()
    local.hide_manager.load = AsyncMock()
    return local


@pytest.mark.asyncio
async def test_extension_connect_while_remote_tier_activates() -> None:
    state = _fake_app_state()
    cfg = AgentcloakConfig()
    mgr = ContextManager(state, cfg)
    mgr.seed_initial(
        active_tier=StealthTier.REMOTE_BRIDGE,
        local_ctx=None,
        local_tier=None,
        local_profile=None,
    )

    remote = _make_fake_remote_ctx()
    mgr.on_extension_connected(remote)
    assert state.browser_ctx is not None
    assert state.remote_ctx is remote


@pytest.mark.asyncio
async def test_extension_connect_while_local_tier_holds_only() -> None:
    state = _fake_app_state()
    cfg = AgentcloakConfig()
    mgr = ContextManager(state, cfg)
    mgr.seed_initial(
        active_tier=StealthTier.CLOAK,
        local_ctx=_make_fake_local_ctx(),
        local_tier=StealthTier.CLOAK,
        local_profile=None,
    )
    # Active context isn't set up by seed_initial (server.start does that),
    # so we simulate the post-startup state where ctx is wired.
    state.browser_ctx = MagicMock()
    previous_browser = state.browser_ctx

    remote = _make_fake_remote_ctx()
    mgr.on_extension_connected(remote)
    # Remote slot populated but browser_ctx untouched.
    assert state.remote_ctx is remote
    assert state.browser_ctx is previous_browser


@pytest.mark.asyncio
async def test_extension_disconnect_clears_when_remote_active() -> None:
    state = _fake_app_state()
    cfg = AgentcloakConfig()
    mgr = ContextManager(state, cfg)
    mgr.seed_initial(
        active_tier=StealthTier.REMOTE_BRIDGE,
        local_ctx=None,
        local_tier=None,
        local_profile=None,
    )
    remote = _make_fake_remote_ctx()
    mgr.on_extension_connected(remote)
    assert state.browser_ctx is not None

    mgr.on_extension_disconnected()
    assert state.browser_ctx is None
    assert state.remote_ctx is None


@pytest.mark.asyncio
async def test_switch_to_remote_without_extension_clears_browser_ctx() -> None:
    state = _fake_app_state()
    cfg = AgentcloakConfig(bridge=BridgeConfig(local_idle_timeout=0))  # disable timer
    mgr = ContextManager(state, cfg)
    mgr.seed_initial(
        active_tier=StealthTier.CLOAK,
        local_ctx=_make_fake_local_ctx(),
        local_tier=StealthTier.CLOAK,
        local_profile=None,
    )
    state.browser_ctx = MagicMock()

    result = await mgr.switch_tier(StealthTier.REMOTE_BRIDGE)
    assert result["active_tier"] == "remote_bridge"
    assert result["browser_ready"] is False
    assert state.browser_ctx is None
    # Local cache preserved (warm) for fast switch-back.
    assert state.local_ctx is not None


@pytest.mark.asyncio
async def test_switch_to_remote_with_existing_extension_activates() -> None:
    state = _fake_app_state()
    cfg = AgentcloakConfig(bridge=BridgeConfig(local_idle_timeout=0))
    mgr = ContextManager(state, cfg)
    mgr.seed_initial(
        active_tier=StealthTier.CLOAK,
        local_ctx=_make_fake_local_ctx(),
        local_tier=StealthTier.CLOAK,
        local_profile=None,
    )
    state.remote_ctx = _make_fake_remote_ctx()

    result = await mgr.switch_tier(StealthTier.REMOTE_BRIDGE)
    assert result["browser_ready"] is True
    assert state.browser_ctx is not None


@pytest.mark.asyncio
async def test_switch_back_to_local_reuses_cache() -> None:
    state = _fake_app_state()
    cfg = AgentcloakConfig(bridge=BridgeConfig(local_idle_timeout=0))
    mgr = ContextManager(state, cfg)
    cached_local = _make_fake_local_ctx()
    mgr.seed_initial(
        active_tier=StealthTier.REMOTE_BRIDGE,
        local_ctx=cached_local,
        local_tier=StealthTier.CLOAK,
        local_profile=None,
    )

    result = await mgr.switch_tier(StealthTier.CLOAK)
    assert result["active_tier"] == "cloak"
    assert result["browser_ready"] is True
    # Cache reused — close() never called.
    assert cached_local.close.await_count == 0


@pytest.mark.asyncio
async def test_switch_to_profile_loads_profile_hide_selectors(tmp_path: Path) -> None:
    state = _fake_app_state()
    cfg = AgentcloakConfig(bridge=BridgeConfig(local_idle_timeout=0))
    mgr = ContextManager(state, cfg)
    mgr.seed_initial(
        active_tier=StealthTier.CLOAK,
        local_ctx=None,
        local_tier=None,
        local_profile=None,
    )
    new_local = _make_fake_local_ctx()
    paths = Paths(root=tmp_path)
    paths.profiles_dir.joinpath("dos").mkdir(parents=True)
    paths.profiles_dir.joinpath("dos", "hide.json").write_text(
        '{"selectors": [".toolbar"]}', encoding="utf-8"
    )

    with (
        patch(
            "agentcloak.daemon.context_manager.create_context",
            new=AsyncMock(return_value=new_local),
        ),
        patch(
            "agentcloak.core.config.load_config",
            return_value=(paths, cfg),
        ),
    ):
        await mgr.switch_tier(StealthTier.CLOAK, profile="dos")

    new_local.hide_manager.load.assert_awaited_once_with([".toolbar"])
    assert state.local_profile == "dos"


@pytest.mark.asyncio
async def test_switch_to_different_local_tier_closes_cache() -> None:
    state = _fake_app_state()
    cfg = AgentcloakConfig(bridge=BridgeConfig(local_idle_timeout=0))
    mgr = ContextManager(state, cfg)
    cached_local = _make_fake_local_ctx()
    mgr.seed_initial(
        active_tier=StealthTier.CLOAK,
        local_ctx=cached_local,
        local_tier=StealthTier.CLOAK,
        local_profile=None,
    )
    new_local = _make_fake_local_ctx()
    with patch(
        "agentcloak.daemon.context_manager.create_context",
        new=AsyncMock(return_value=new_local),
    ):
        result = await mgr.switch_tier(StealthTier.PLAYWRIGHT)
    assert result["active_tier"] == "playwright"
    # Old cache closed, new launched.
    assert cached_local.close.await_count == 1
    assert state.local_ctx is new_local


@pytest.mark.asyncio
async def test_shutdown_closes_local_only() -> None:
    state = _fake_app_state()
    cfg = AgentcloakConfig(bridge=BridgeConfig(local_idle_timeout=0))
    mgr = ContextManager(state, cfg)
    cached_local = _make_fake_local_ctx()
    remote = _make_fake_remote_ctx()
    mgr.seed_initial(
        active_tier=StealthTier.REMOTE_BRIDGE,
        local_ctx=cached_local,
        local_tier=StealthTier.CLOAK,
        local_profile=None,
    )
    state.remote_ctx = remote

    await mgr.shutdown()
    assert cached_local.close.await_count == 1
    # Remote context owned by WS handler — only references cleared.
    assert remote.close.await_count == 0
    assert state.remote_ctx is None
    assert state.browser_ctx is None


# ---------------------------------------------------------------------------
# /launch endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def launch_client() -> Any:
    """FastAPI TestClient wired with a real ContextManager and stub locals."""
    app = create_app()
    cfg = AgentcloakConfig(bridge=BridgeConfig(local_idle_timeout=0))
    mgr = ContextManager(app.state, cfg)
    cached_local = _make_fake_local_ctx()
    mgr.seed_initial(
        active_tier=StealthTier.CLOAK,
        local_ctx=cached_local,
        local_tier=StealthTier.CLOAK,
        local_profile=None,
    )
    app.state.context_manager = mgr
    app.state.config = cfg
    app.state.browser_ctx = MagicMock()
    app.state.shutdown_event = asyncio.Event()
    with TestClient(app) as c:
        yield c


class TestLaunchRoute:
    def test_launch_remote_bridge_without_extension(
        self, launch_client: TestClient
    ) -> None:
        resp = launch_client.post("/launch", json={"tier": "remote_bridge"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["active_tier"] == "remote_bridge"
        assert data["data"]["browser_ready"] is False

    def test_launch_back_to_local_reuses_cache(self, launch_client: TestClient) -> None:
        # First go remote, then come back; cached local should be reused.
        launch_client.post("/launch", json={"tier": "remote_bridge"})
        resp = launch_client.post("/launch", json={"tier": "cloak"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["active_tier"] == "cloak"
        assert data["data"]["browser_ready"] is True

    def test_launch_invalid_tier(self, launch_client: TestClient) -> None:
        resp = launch_client.post("/launch", json={"tier": "bogus_tier"})
        # FastAPI validates against the Literal first → 422.
        assert resp.status_code in (400, 422)


class TestLaunchProfileStickiness:
    """Profile persists across tier switches unless explicitly cleared."""

    def test_launch_without_profile_keeps_current(
        self, launch_client: TestClient
    ) -> None:
        launch_client.app.state.local_profile = "dos"  # type: ignore[union-attr]
        resp = launch_client.post("/launch", json={"tier": "cloak"})
        assert resp.status_code == 200
        assert resp.json()["data"]["profile"] == "dos"

    def test_launch_no_profile_flag_clears(self, launch_client: TestClient) -> None:
        launch_client.app.state.local_profile = "dos"  # type: ignore[union-attr]
        new_local = _make_fake_local_ctx()
        with patch(
            "agentcloak.daemon.context_manager.create_context",
            new=AsyncMock(return_value=new_local),
        ):
            resp = launch_client.post(
                "/launch", json={"tier": "cloak", "no_profile": True}
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["profile"] is None


class TestHealthInRemoteBridgeWaiting:
    """`/health` should answer even when no browser is wired up."""

    def test_health_with_no_browser(self) -> None:
        app = create_app()
        cfg = AgentcloakConfig()
        mgr = ContextManager(app.state, cfg)
        mgr.seed_initial(
            active_tier=StealthTier.REMOTE_BRIDGE,
            local_ctx=None,
            local_tier=None,
            local_profile=None,
        )
        app.state.context_manager = mgr
        app.state.config = cfg
        app.state.browser_ctx = None
        app.state.shutdown_event = asyncio.Event()
        with TestClient(app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["active_tier"] == "remote_bridge"
        assert data["browser_ready"] is False
