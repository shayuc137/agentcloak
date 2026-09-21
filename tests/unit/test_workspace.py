"""Workspace identity, configuration and HTTP namespace boundaries."""

from __future__ import annotations

import subprocess
from pathlib import Path  # noqa: TC003
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from agentcloak.cli.app import _extract_global_flags
from agentcloak.core.config import AgentcloakConfig, ConfigError, load_config
from agentcloak.core.workspace import cli_workspace, resolve_workspace
from agentcloak.daemon.app import create_app
from agentcloak.daemon.scheduling import PATHS, POLICY
from agentcloak.daemon.services.session_manager import SessionManager


@pytest.fixture(autouse=True)
def identity_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AGENTCLOAK_WORKSPACE", raising=False)
    monkeypatch.delenv("AGENTCLOAK_ISOLATION", raising=False)
    token = cli_workspace.set(None)
    yield
    cli_workspace.reset(token)


def test_non_git_names_and_root_inheritance(tmp_path: Path, monkeypatch):
    first = tmp_path / "one" / "assistant"
    second = tmp_path / "two" / "assistant"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    monkeypatch.chdir(first)
    a = resolve_workspace()
    assert a == resolve_workspace()
    monkeypatch.chdir(second)
    assert a != resolve_workspace()
    outer = resolve_workspace([str(tmp_path)])
    assert resolve_workspace([str(tmp_path), str(second)]) != outer
    monkeypatch.chdir(first)
    assert resolve_workspace([str(tmp_path)]) == outer
    monkeypatch.setenv("AGENTCLOAK_WORKSPACE", str(second))
    b = resolve_workspace()
    cli_workspace.set(str(first))
    assert resolve_workspace() == a
    cli_workspace.set(None)
    assert resolve_workspace() == b


def test_git_worktrees_share_storage_not_default_session(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True
        )

    git("init")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "Initial",
    )
    tree = tmp_path / "tree"
    git("worktree", "add", "-b", "parallel", str(tree))
    monkeypatch.chdir(repo)
    a = resolve_workspace()
    monkeypatch.chdir(tree)
    b = resolve_workspace()
    assert a.workspace_id == b.workspace_id
    assert a.session_scope != b.session_scope
    child = tree / "nested"
    child.mkdir()
    monkeypatch.chdir(child)
    assert resolve_workspace() == b
    configured_tree = resolve_workspace([str(tmp_path)])
    monkeypatch.chdir(repo)
    configured_repo = resolve_workspace([str(tmp_path)])
    assert configured_tree.workspace_id == configured_repo.workspace_id
    assert configured_tree.session_scope != configured_repo.session_scope
    cli_workspace.set(str(tmp_path))
    explicit_repo = resolve_workspace()
    monkeypatch.chdir(tree)
    explicit_tree = resolve_workspace()
    assert explicit_repo.workspace_id == explicit_tree.workspace_id
    assert explicit_repo.session_scope != explicit_tree.session_scope


@pytest.mark.parametrize(
    "argv",
    [
        ["--workspace", "/workspace", "snapshot"],
        ["snapshot", "--workspace", "/workspace"],
        ["snapshot", "--workspace=/workspace"],
    ],
)
def test_workspace_flag_position(argv):
    args, state = _extract_global_flags(argv)
    assert args == ["snapshot"]
    assert state["workspace"] == "/workspace"


def test_default_shared_and_config_override(tmp_path, monkeypatch):
    assert load_config(root=tmp_path)[1].browser.isolation == "shared"
    (tmp_path / "config.toml").write_text(
        '[browser]\nisolation="workspace"\nworkspace_roots=["/work"]\n'
    )
    cfg = load_config(root=tmp_path)[1]
    assert cfg.browser.isolation == "workspace"
    assert cfg.browser.workspace_roots == ["/work"]
    monkeypatch.setenv("AGENTCLOAK_ISOLATION", "shared")
    assert load_config(root=tmp_path)[1].browser.isolation == "shared"


@pytest.mark.parametrize(
    "value",
    [
        'isolation="typo"',
        'workspace_roots="/work"',
        "workspace_roots=[1]",
        'workspace_roots=[""]',
    ],
)
def test_invalid_workspace_config(tmp_path, value):
    (tmp_path / "config.toml").write_text(f"[browser]\n{value}\n")
    with pytest.raises(ConfigError):
        load_config(root=tmp_path)


def test_every_http_route_has_one_scheduling_policy():
    paths = set(create_app().openapi()["paths"])
    assert not paths - POLICY.keys(), "Classify new routes in daemon/scheduling.py"
    assert sum(map(len, PATHS.values())) == len(POLICY)
    assert (
        not POLICY.keys()
        - paths
        - {"/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}
    )


async def test_close_and_list_are_scoped_even_for_identical_session_names():
    app = create_app()
    manager = SessionManager(AgentcloakConfig(), app_state=app.state)
    app.state.session_manager = manager
    app.state.context_manager = SimpleNamespace(
        close_remote_session=AsyncMock(return_value=False)
    )
    a = SimpleNamespace(close=AsyncMock())
    b = SimpleNamespace(close=AsyncMock())
    manager.slot("same", workspace_id="a").ctx = a
    manager.slot("same", workspace_id="b").ctx = b
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://localhost"
    ) as client:
        headers = {"X-Agentcloak-Session": "same", "X-Agentcloak-Workspace": "a"}
        result = await client.get("/session/list", headers=headers)
        assert [x["workspace_id"] for x in result.json()["data"]["sessions"]] == ["a"]
        assert (
            await client.post("/session/close", json={}, headers=headers)
        ).is_success
    a.close.assert_awaited_once()
    b.close.assert_not_awaited()


async def test_workspace_bridge_rejected_before_remote_command():
    app = create_app()
    cfg = AgentcloakConfig()
    cfg.browser.isolation = "workspace"
    app.state.config = cfg
    app.state.remote_ctx = SimpleNamespace(send_command=AsyncMock())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://localhost"
    ) as client:
        result = await client.post("/bridge/claim", json={})
    assert result.status_code == 409
    assert "workspace_isolation_unavailable" in result.text
    app.state.remote_ctx.send_command.assert_not_awaited()
