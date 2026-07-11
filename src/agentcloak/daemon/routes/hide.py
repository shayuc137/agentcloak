"""Persistent page-overlay hiding routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from agentcloak.core.errors import AgentBrowserError
from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import (
    HideAddRequest,
    HideAddResponse,
    HideListResponse,
    HideRemoveRequest,
    HideRemoveResponse,
    OkEnvelope,
)
from agentcloak.daemon.routes._helpers import _ok
from agentcloak.daemon.services import ProfileService

__all__ = ["router"]

router = APIRouter()


def _profile_scope(request: Request) -> str | None:
    profile = getattr(request.app.state, "local_profile", None)
    return str(profile) if profile else None


def _persist(profile: str, selectors: list[str]) -> None:
    from agentcloak.core.config import load_config

    paths, _ = load_config()
    ProfileService(paths.profiles_dir).write_hide_selectors(profile, selectors)


def _required(value: str) -> str:
    normalized = value.strip()
    if normalized:
        return normalized
    raise AgentBrowserError(
        error="invalid_argument",
        hint="Hide selector or identifier cannot be blank",
        action="pass a non-empty CSS selector or identifier from 'hide list'",
    )


@router.post("/hide/add", response_model=OkEnvelope[HideAddResponse])
async def handle_hide_add(
    body: HideAddRequest, ctx: BrowserCtxDep, request: Request
) -> dict[str, Any]:
    selector = _required(body.selector)
    identifier = await ctx.hide_manager.add(selector)
    profile = _profile_scope(request)
    if profile:
        _persist(profile, ctx.hide_manager.persistent_selectors())
    return _ok(
        {
            "identifier": identifier,
            "selector": selector,
            "scope": profile or "session-only",
        },
        seq=ctx.seq,
    )


@router.post("/hide/remove", response_model=OkEnvelope[HideRemoveResponse])
async def handle_hide_remove(
    body: HideRemoveRequest, ctx: BrowserCtxDep, request: Request
) -> dict[str, Any]:
    identifier_or_selector = _required(body.identifier_or_selector)
    removed = await ctx.hide_manager.remove(identifier_or_selector)
    profile = _profile_scope(request)
    if profile and removed:
        _persist(profile, ctx.hide_manager.persistent_selectors())
    return _ok({"removed": removed, "scope": profile or "session-only"}, seq=ctx.seq)


@router.get("/hide/list", response_model=OkEnvelope[HideListResponse])
async def handle_hide_list(ctx: BrowserCtxDep, request: Request) -> dict[str, Any]:
    selectors = ctx.hide_manager.list_selectors()
    profile = _profile_scope(request)
    return _ok(
        {
            "selectors": selectors,
            "count": len(selectors),
            "scope": profile or "session-only",
        },
        seq=ctx.seq,
    )
