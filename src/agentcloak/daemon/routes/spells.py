"""Spell registry and profile CRUD routes.

Spells are user-discoverable adapters built on top of ``@spell``; profiles
are persistent Chrome profile directories. Both touch the user filesystem
through service-layer code so the HTTP handlers remain thin.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from agentcloak.core.errors import ProfileError
from agentcloak.daemon.dependencies import (  # noqa: TC001
    BrowserCtxDep,
    RemoteCtxDep,
)
from agentcloak.daemon.models import (
    OkEnvelope,
    ProfileCreateFromCurrentRequest,
    ProfileCreateFromCurrentResponse,
    ProfileCreateRequest,
    ProfileCreateResponse,
    ProfileDeleteRequest,
    ProfileListResponse,
    SpellListResponse,
    SpellRunRequest,
    SpellRunResponse,
)
from agentcloak.daemon.routes._helpers import _ok
from agentcloak.daemon.services import ProfileService

__all__ = ["router"]

router = APIRouter()


def _profile_error_to_http(exc: ProfileError) -> HTTPException:
    """Translate a ProfileError into a FastAPI HTTPException with the right status."""
    status_map = {
        "missing_name": 400,
        "invalid_profile_name": 400,
        "invalid_profile_path": 400,
        "profile_exists": 409,
        "profile_not_found": 404,
        "profile_writer_failed": 500,
    }
    return HTTPException(
        status_code=status_map.get(exc.error, 400),
        detail=exc.to_dict(),
    )


def _profiles_dir():  # type: ignore[no-untyped-def]
    """Load the profiles directory from the daemon config snapshot."""
    from agentcloak.core.config import load_config

    paths, _ = load_config()
    return paths.profiles_dir


# --- Spells -----------------------------------------------------------------


@router.post("/spell/run", response_model=OkEnvelope[SpellRunResponse])
async def handle_spell_run(body: SpellRunRequest, ctx: BrowserCtxDep) -> dict[str, Any]:
    """Run a registered spell with the daemon's live browser context."""
    from agentcloak.spells.discovery import discover_spells
    from agentcloak.spells.executor import execute_spell
    from agentcloak.spells.registry import get_registry

    parts = body.name.split("/", 1)
    if len(parts) != 2:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "invalid_spell_name",
                "hint": f"Expected 'site/command', got '{body.name}'",
                "action": "use format like 'httpbin/headers'",
            },
        )

    discover_spells()
    registry = get_registry()
    entry = registry.get(parts[0], parts[1])
    if entry is None:
        available = [e.meta.full_name for e in registry.list_all()]
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error": "spell_not_found",
                "hint": f"No spell '{body.name}'",
                "action": f"available: {', '.join(available[:10])}",
            },
        )

    result = await execute_spell(entry, args=body.args, browser=ctx)
    data = {"result": result}
    return _ok(data, seq=ctx.seq)


@router.get("/spell/list", response_model=OkEnvelope[SpellListResponse])
async def handle_spell_list(ctx: BrowserCtxDep) -> dict[str, Any]:
    """List all registered spells."""
    from agentcloak.spells.discovery import discover_spells
    from agentcloak.spells.registry import get_registry

    discover_spells()
    registry = get_registry()
    spells = [
        {
            "full_name": e.meta.full_name,
            "strategy": e.meta.strategy.value,
            "access": e.meta.access,
            "description": e.meta.description,
        }
        for e in registry.list_all()
    ]
    data = {"spells": spells, "count": len(spells)}
    return _ok(data, seq=ctx.seq)


# --- Profile ----------------------------------------------------------------


@router.post(
    "/profile/create-from-current",
    response_model=OkEnvelope[ProfileCreateFromCurrentResponse],
)
async def handle_profile_create_from_current(
    body: ProfileCreateFromCurrentRequest,
    ctx: BrowserCtxDep,
    remote_ctx: RemoteCtxDep,
) -> dict[str, Any]:
    """Create a profile from the current browser session's cookies."""
    service = ProfileService(_profiles_dir())

    try:
        service.validate_name(body.name)
    except ProfileError as exc:
        raise _profile_error_to_http(exc) from exc

    cookies: list[dict[str, Any]]
    if remote_ctx is not None:
        from agentcloak.browser.remote_ctx import RemoteBridgeContext

        if not isinstance(remote_ctx, RemoteBridgeContext):
            raise RuntimeError("remote_ctx is not a RemoteBridgeContext instance")
        # The bridge ``cookies`` command returns either a list of cookie dicts
        # directly or a ``{"cookies": [...]}`` envelope depending on extension
        # version. Normalise to a list either way.
        raw_response: Any = await remote_ctx.send_command("cookies", {})
        cookies = []
        if isinstance(raw_response, list):
            cookies = list(raw_response)  # type: ignore[arg-type]
        elif isinstance(raw_response, dict):
            inner = raw_response.get("cookies", [])  # type: ignore[arg-type]
            if isinstance(inner, list):
                cookies = list(inner)  # type: ignore[arg-type]
    else:
        browser_context = ctx._get_browser_context()
        cookies = await browser_context.cookies()

    try:
        result = await service.create_from_cookies(body.name, cookies)
    except ProfileError as exc:
        raise _profile_error_to_http(exc) from exc
    return _ok(result, seq=ctx.seq)


@router.get("/profile/list", response_model=OkEnvelope[ProfileListResponse])
async def handle_profile_list(ctx: BrowserCtxDep) -> dict[str, Any]:
    service = ProfileService(_profiles_dir())
    names = service.list_profiles()
    data = {"profiles": names, "count": len(names)}
    return _ok(data, seq=ctx.seq)


@router.post("/profile/create", response_model=OkEnvelope[ProfileCreateResponse])
async def handle_profile_create(
    body: ProfileCreateRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    service = ProfileService(_profiles_dir())
    try:
        name = service.create(body.name)
    except ProfileError as exc:
        raise _profile_error_to_http(exc) from exc
    return _ok({"created": name}, seq=ctx.seq)


@router.post("/profile/delete", response_model=OkEnvelope[ProfileCreateResponse])
async def handle_profile_delete(
    body: ProfileDeleteRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    service = ProfileService(_profiles_dir())
    try:
        name = service.delete(body.name)
    except ProfileError as exc:
        raise _profile_error_to_http(exc) from exc
    return _ok({"deleted": name}, seq=ctx.seq)
