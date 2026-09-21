"""Native profile storage remains authoritative across navigation and restart."""

import httpx
import pytest
from fastapi import FastAPI

from agentcloak.core.storage_snapshot import write_storage_snapshot
from agentcloak.daemon.dependencies import get_browser_ctx
from agentcloak.daemon.routes.storage import router
from agentcloak.daemon.services.profile_service import ProfileService


@pytest.mark.parametrize("backend", ["playwright", "cloak"])
async def test_profile_storage_lifecycle(tmp_path, local_server, backend):
    from agentcloak.browser.cloak_ctx import launch_cloak
    from agentcloak.browser.playwright_ctx import launch_playwright

    profile = tmp_path / "profiles" / "seeded"
    seed = {"token": "initial", "cursor": "1", "removed": "old"}
    service = ProfileService(profile.parent)
    await service.create_from_cookies("seeded", [], local_storage={local_server: seed})
    # A deliberately obsolete backup must never override native state.
    write_storage_snapshot(profile / "localStorage-snapshot.json", local_server, seed)
    launch = launch_cloak if backend == "cloak" else launch_playwright
    options = {"humanize": False} if backend == "cloak" else {}

    async def start():
        return await launch(headless=True, profile_dir=profile, **options)

    ctx = await start()
    sibling = None
    try:
        await ctx.navigate(f"{local_server}/storage-state.html")
        assert await ctx.evaluate("initialStorage") == seed
        await ctx.evaluate(
            "localStorage.setItem('token','rotated');"
            "localStorage.setItem('cursor','99');"
            "localStorage.removeItem('removed')"
        )
        expected = {"token": "rotated", "cursor": "99"}
        await ctx.navigate(f"{local_server}/storage-state.html")
        assert await ctx.evaluate("initialStorage") == expected
        sibling = await ctx.fork_session()
        await sibling.navigate(f"{local_server}/storage-state.html")
        assert await sibling.evaluate("initialStorage") == expected
        # A distinct origin must not receive the original origin's seed.
        other = local_server.replace("127.0.0.1", "localhost")
        await ctx.navigate(f"{other}/storage-state.html")
        assert await ctx.evaluate("initialStorage") == {}
        await ctx.navigate(f"{local_server}/storage-state.html")
        assert await ctx.evaluate("initialStorage") == expected

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_browser_ctx] = lambda: ctx
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path, body in (
                ("set", {"key": "temporary", "value": "new"}),
                ("delete", {"key": "temporary"}),
                ("clear", {}),
            ):
                response = await client.post(
                    f"/storage/{path}", json={"type": "local", **body}
                )
                assert response.is_success, response.text
                await ctx.navigate(f"{local_server}/storage-state.html")
                expected = (
                    {**expected, "temporary": "new"}
                    if path == "set"
                    else (
                        {k: v for k, v in expected.items() if k != "temporary"}
                        if path == "delete"
                        else {}
                    )
                )
                assert await ctx.evaluate("initialStorage") == expected
        await sibling.navigate(f"{local_server}/storage-state.html")
        assert await sibling.evaluate("initialStorage") == {}
    finally:
        if sibling is not None:
            await sibling.close()
        await ctx.close()

    # Even a stale backup left behind after clear cannot resurrect a token.
    write_storage_snapshot(profile / "localStorage-snapshot.json", local_server, seed)
    ctx = await start()
    try:
        await ctx.navigate(f"{local_server}/storage-state.html")
        assert await ctx.evaluate("initialStorage") == {}
    finally:
        await ctx.close()
