"""Local MV3 extension → WebSocket → daemon smoke, without a user profile."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import httpx
import uvicorn

from agentcloak.browser.cloak_ctx import launch_cloak
from agentcloak.core.config import AgentcloakConfig
from agentcloak.core.types import StealthTier
from agentcloak.daemon.app import create_app
from agentcloak.daemon.context_manager import ContextManager
from agentcloak.daemon.services.bridge_service import BridgeService
from agentcloak.daemon.services.session_manager import SessionManager
from tests.integration.conftest import _pick_free_port


async def test_real_extension_browser_controls(tmp_path, local_server):
    app = create_app()
    cfg = AgentcloakConfig()
    app.state.config = cfg
    app.state.context_manager = ContextManager(app.state, cfg)
    app.state.context_manager.seed_initial(
        active_tier=StealthTier.REMOTE_BRIDGE,
        local_ctx=None,
        local_tier=None,
        local_profile=None,
    )
    app.state.session_manager = SessionManager(cfg, app_state=app.state)
    app.state.bridge_service = BridgeService(app.state)
    port = _pick_free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    serving = asyncio.create_task(server.serve())
    extension = tmp_path / "extension"
    source = (
        Path(__file__).resolve().parents[2]
        / "src/agentcloak/bridge/agentcloak-chrome-extension"
    )
    shutil.copytree(source, extension)
    background = extension / "background.js"
    script = background.read_text()
    for name, old in [
        ("DEFAULT_PORT", 18765),
        ("PORT_RANGE_START", 18765),
        ("PORT_RANGE_END", 18774),
    ]:
        script = script.replace(f"const {name} = {old};", f"const {name} = {port};")
    background.write_text(script)
    browser = None
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        browser = await launch_cloak(
            headless=True,
            humanize=False,
            profile_dir=tmp_path / "browser",
            extensions=[str(extension)],
        )
        for _ in range(200):
            if app.state.remote_ctx is not None:
                break
            await asyncio.sleep(0.05)
        assert app.state.remote_ctx is not None, "Real MV3 extension did not connect"
        headers = {"X-Agentcloak-Session": "bridge", "X-Agentcloak-Workspace": "one"}
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers=headers, timeout=20
        ) as client:
            response = await client.post("/launch", json={"tier": "remote_bridge"})
            assert response.is_success, response.text
            response = await client.post(
                "/bridge/claim", json={"url_pattern": "about:blank"}
            )
            assert response.is_success, response.text
            response = await client.post(
                "/navigate", json={"url": f"{local_server}/input-actions.html"}
            )
            assert response.is_success, response.text
            response = await client.get(
                "/snapshot", params={"include_selector_map": "true"}
            )
            assert response.is_success, response.text
            assert "Editor" in response.json()["data"]["tree_text"]
            response = await client.post(
                "/viewport", json={"width": 1100, "height": 700}
            )
            assert response.is_success, response.text
            response = await client.post(
                "/evaluate", json={"js": "[innerWidth, innerHeight]"}
            )
            assert response.is_success, response.text
            assert response.json()["data"]["result"] == [1100, 700]
            response = await client.get("/screenshot")
            assert response.is_success, response.text
            response = await client.post(
                "/bridge/claim",
                json={},
                headers={**headers, "X-Agentcloak-Workspace": "two"},
            )
            assert response.status_code == 409
            for tier in ["remote_bridge", "playwright"]:
                response = await client.post(
                    "/launch",
                    json={"tier": tier},
                    headers={**headers, "X-Agentcloak-Workspace": "two"},
                )
                assert response.status_code == 400
                assert "bridge_session_in_use" in response.text
            assert (
                await client.post("/evaluate", json={"js": "location.pathname"})
            ).json()["data"]["result"] == "/input-actions.html"
            response = await client.post("/session/close", json={})
            assert response.is_success, response.text
    finally:
        if browser is not None:
            await browser.close()
        server.should_exit = True
        await serving
