"""Observable route state and script injection status on the daemon surface."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from agentcloak.browser.managers.route_manager import RouteManager
from agentcloak.daemon.app import create_app


def _client() -> tuple[TestClient, MagicMock]:
    ctx = MagicMock()
    ctx.seq = 0
    ctx._route_add_impl = AsyncMock()
    ctx._route_remove_impl = AsyncMock()
    ctx.route_manager = RouteManager(ctx)
    app = create_app()
    app.state.browser_ctx = ctx
    return TestClient(app), ctx


def test_route_registration_zero_hit_warning_and_release_not_found() -> None:
    client, _ = _client()
    added = client.post("/route/add", json={"pattern": "/api", "action": "hold"})
    assert added.status_code == 200
    identifier = added.json()["data"]["identifier"]
    data = client.get("/route/list").json()["data"]
    assert data["rules"][0]["identifier"] == identifier
    assert data["rules"][0]["hits"] == 0
    assert len(data["warnings"]) == 1
    assert data["pending"] == []
    result = client.post("/route/release", json={"identifier": identifier})
    assert result.status_code == 404
    assert result.json()["ok"] is False
    assert client.post("/route/release", json={"identifier": ""}).status_code == 422


def test_script_list_reports_current_page_status() -> None:
    client, ctx = _client()
    ctx.script_manager.list_scripts.return_value = {"one": "window.flag=true"}
    ctx.script_manager.statuses = AsyncMock(return_value={"one": "not injected"})
    data = client.get("/script/list").json()["data"]
    assert data["scripts"] == [
        {"identifier": "one", "source": "window.flag=true", "status": "not injected"}
    ]
