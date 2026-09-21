"""Real browser storage boundaries and lifecycle across both local backends."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from agentcloak.core.config import AgentcloakConfig
from agentcloak.daemon.services.session_manager import SessionManager


@pytest.mark.parametrize("backend", ["playwright", "cloak"])
@pytest.mark.parametrize("mode", ["shared", "workspace"])
async def test_workspace_storage_and_restart(tmp_path, local_server, backend, mode):
    from agentcloak.browser.cloak_ctx import launch_cloak
    from agentcloak.browser.playwright_ctx import launch_playwright

    cfg = AgentcloakConfig()
    cfg.browser.isolation = mode
    cfg.browser.humanize = False
    launch = launch_cloak if backend == "cloak" else launch_playwright
    state = SimpleNamespace(local_profile="test", config_root=tmp_path)
    owner = None

    async def start():
        nonlocal owner
        options = {"humanize": False} if backend == "cloak" else {}
        owner = await launch(
            headless=True,
            profile_dir=tmp_path / "profile",
            browser_config=cfg.browser,
            **options,
        )

        async def ensure_local():
            return owner

        state.context_manager = SimpleNamespace(ensure_local=ensure_local)
        return SessionManager(cfg, app_state=state)

    async def open_session(manager, workspace, session="same"):
        ctx = await manager.get_or_create(session, workspace_id=workspace)
        await ctx.navigate(f"{local_server}/index.html")
        return ctx

    async def write(ctx, value):
        await ctx.evaluate(
            f"localStorage.setItem('workspace-test','{value}');"
            f"document.cookie='workspace-test={value}; Max-Age=3600; Path=/'"
        )

        await ctx.evaluate(
            """new Promise((resolve, reject) => {
            const request = indexedDB.open('workspace-test', 1);
            request.onupgradeneeded = () => request.result.createObjectStore('values');
            request.onerror = () => reject(request.error);
            request.onsuccess = () => {
                const db = request.result;
                const transaction = db.transaction('values', 'readwrite');
                transaction.objectStore('values').put('"""
            + value
            + """', 'identity');
                transaction.oncomplete = () => { db.close(); resolve(true); };
                transaction.onerror = () => { db.close(); reject(transaction.error); };
            };
        })"""
        )

    async def read_database(ctx):
        return await ctx.evaluate("""new Promise((resolve, reject) => {
            const request = indexedDB.open('workspace-test', 1);
            request.onerror = () => reject(request.error);
            request.onsuccess = () => {
                const db = request.result;
                const store = db.transaction('values').objectStore('values');
                const read = store.get('identity');
                read.onsuccess = () => { db.close(); resolve(read.result); };
                read.onerror = () => { db.close(); reject(read.error); };
            };
        })""")

    async def read(ctx):
        return await ctx.evaluate(
            "[localStorage.getItem('workspace-test'), document.cookie]"
        )

    manager = await start()
    try:
        a = await open_session(manager, "a")
        await write(a, "alpha")
        sibling = await open_session(manager, "a", "sibling")
        assert (await read(sibling))[0] == "alpha"
        await a.evaluate("document.title='only-a'")
        assert await sibling.evaluate("document.title") != "only-a"
        b = await open_session(manager, "b")
        before = await read(b)
        assert before[0] == (None if mode == "workspace" else "alpha")
        assert ("workspace-test=alpha" in before[1]) == (mode == "shared")
        await write(b, "beta")
        expected_a = "alpha" if mode == "workspace" else "beta"
        assert (await read(a))[0] == expected_a
        assert f"workspace-test={expected_a}" in (await read(a))[1]
        assert await manager.close_session("same", workspace_id="a")
        assert (await read(sibling))[0] == expected_a
        assert (await read(b))[0] == "beta"
        assert await manager.close_session("sibling", workspace_id="a")
        a = await open_session(manager, "a")
        assert (await read(a))[0] == expected_a

        # A closed page must recover without replacing a different workspace.
        await a.close()
        a = await open_session(manager, "a")
        assert (await read(a))[0] == expected_a
        assert (await read(b))[0] == "beta"
        await manager.close_all()
        await owner.close()
        manager = await start()
        a = await open_session(manager, "a")
        b = await open_session(manager, "b")
        assert (await read(a))[0] == expected_a
        assert f"workspace-test={expected_a}" in (await read(a))[1]
        assert (await read(b))[0] == "beta"
        assert await read_database(a) == expected_a
        assert await read_database(b) == "beta"
        if mode == "workspace":
            files = list((tmp_path / "workspaces").glob("*/storage.json"))
            assert len(files) == 2
            if os.name != "nt":
                assert all(path.stat().st_mode & 0o777 == 0o600 for path in files)
    finally:
        await manager.close_all()
        if owner is not None:
            await owner.close()
