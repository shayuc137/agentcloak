"""DOS #5: preserve lost-page identity across idle cleanup and owner replacement."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from agentcloak.core.config import AgentcloakConfig
from agentcloak.daemon.app import create_app
from agentcloak.daemon.services.session_manager import SessionManager


@pytest.mark.parametrize("reason", ["idle", "browser-rebuilt", "page-closed"])
async def test_page_loss_reports_identity_and_reason(
    browser_context: Any, local_server: str, reason: str
) -> None:
    config = AgentcloakConfig()
    app = create_app()
    app.state.config = config
    app.state.active_tier = browser_context.stealth_tier
    app.state.local_profile = None
    app.state.browser_ctx = browser_context
    owner = browser_context

    async def ensure_local() -> Any:
        return owner

    app.state.context_manager = SimpleNamespace(ensure_local=ensure_local)
    manager = SessionManager(config, app_state=app.state)
    app.state.session_manager = manager
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={
            "X-Agentcloak-Session": "lost-page",
            "X-Agentcloak-Session-Label": "sales-check",
        },
    ) as client:
        try:
            url = f"{local_server}/form.html"
            response = await client.post("/navigate", json={"url": url})
            assert response.is_success, response.text
            slot = manager.slot("lost-page")
            if reason == "idle":
                slot.last_request_time = time.monotonic() - 301
                assert await manager.cleanup_idle(300) == ["lost-page"]
            elif reason == "browser-rebuilt":
                owner = await browser_context.fork_session()
            else:
                await slot.ctx.close()
            response = await client.get("/snapshot")
            assert response.status_code == 409
            error = response.json()
            assert error["error"] == "page_recreated"
            assert url in error["hint"]
            assert "Form" in error["hint"]
            assert "lost-page" in error["hint"]
            assert "sales-check" in error["hint"]
            assert "+08:00" in error["hint"]
            assert "daemon.session_idle_timeout" in error["hint"]
            assert {
                "idle": "300",
                "browser-rebuilt": "browser",
                "page-closed": "closed",
            }[reason] in error["hint"]
            assert (await client.post("/navigate", json={"url": url})).is_success
            assert (await client.get("/snapshot")).is_success
        finally:
            await manager.close_all()
            if owner is not browser_context:
                await owner.close()
