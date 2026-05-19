"""Page-interaction routes — cookies, dialog, wait, upload, frame.

These all act on the live page state rather than the broader daemon
lifecycle. Cookie import/export hits both local and remote backends
(remote_ctx via CDP, local via Playwright's BrowserContext API), so the
helpers stay close to the routes that need them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from agentcloak.daemon.dependencies import (  # noqa: TC001
    BrowserCtxDep,
    RemoteCtxDep,
)
from agentcloak.daemon.models import (
    CookiesExportRequest,
    CookiesExportResponse,
    CookiesImportRequest,
    CookiesImportResponse,
    DialogHandleRequest,
    DialogHandleResponse,
    DialogStatusResponse,
    FrameFocusRequest,
    FrameFocusResponse,
    FrameListResponse,
    OkEnvelope,
    UploadRequest,
    UploadResponse,
    WaitRequest,
    WaitResponse,
)
from agentcloak.daemon.routes._helpers import _ok
from agentcloak.daemon.text_renderers import (
    render_cookies_export_text,
    render_cookies_import_text,
    render_dialog_handle_text,
    render_dialog_status_text,
    render_frame_focus_text,
    render_frame_list_text,
    render_upload_text,
    render_wait_text,
    wants_text,
)

__all__ = ["router"]

router = APIRouter()


# --- Cookies ----------------------------------------------------------------


@router.post("/cookies/export", response_model=OkEnvelope[CookiesExportResponse])
async def handle_cookies_export(
    body: CookiesExportRequest,
    ctx: BrowserCtxDep,
    remote_ctx: RemoteCtxDep,
    request: Request,
) -> Response | dict[str, Any]:
    if remote_ctx is not None:
        from agentcloak.browser.remote_ctx import RemoteBridgeContext

        if not isinstance(remote_ctx, RemoteBridgeContext):
            raise RuntimeError("remote_ctx is not a RemoteBridgeContext instance")
        params: dict[str, Any] = {}
        if body.url:
            params["url"] = body.url
        result = await remote_ctx.send_command("cookies", params)
        count = len(result) if isinstance(result, list) else 0
        data = {"cookies": result, "count": count}
        if wants_text(request):
            return PlainTextResponse(render_cookies_export_text(data))
        return _ok({"cookies": result}, seq=0)

    browser_context = ctx._get_browser_context()
    if body.url:
        cookies = await browser_context.cookies(body.url)
    else:
        cookies = await browser_context.cookies()
    # Field names use camelCase (httpOnly, sameSite) because these are passed
    # straight through from the Playwright / CDP Cookie spec — re-serializing
    # to snake_case would force agents to translate twice when feeding cookies
    # back into ``cookies/import`` or generic devtools clients.
    serializable = [
        {
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "expires": c.get("expires", -1),
            "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", False),
            "sameSite": c.get("sameSite", "None"),
        }
        for c in cookies
    ]
    data = {"cookies": serializable, "count": len(serializable)}
    if wants_text(request):
        return PlainTextResponse(render_cookies_export_text(data))
    return _ok(data, seq=ctx.seq)


@router.post("/cookies/import", response_model=OkEnvelope[CookiesImportResponse])
async def handle_cookies_import(
    body: CookiesImportRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    if not body.cookies:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "no_cookies",
                "hint": "No cookies provided",
                "action": "pass cookies as JSON array in 'cookies' field",
            },
        )
    browser_context = ctx._get_browser_context()
    await browser_context.add_cookies(body.cookies)
    data = {"imported": len(body.cookies)}
    if wants_text(request):
        return PlainTextResponse(render_cookies_import_text(data))
    return _ok(data, seq=ctx.seq)


# --- Dialog -----------------------------------------------------------------


@router.get("/dialog/status", response_model=OkEnvelope[DialogStatusResponse])
async def handle_dialog_status(
    ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    dialog = await ctx.dialog_status()
    if dialog is None:
        data = {"pending": False}
        if wants_text(request):
            return PlainTextResponse(render_dialog_status_text(data))
        return _ok(data, seq=ctx.seq)
    data = {
        "pending": True,
        "dialog": {
            "type": dialog.dialog_type,
            "message": dialog.message,
            "default_value": dialog.default_value,
            "url": dialog.url,
        },
    }
    if wants_text(request):
        return PlainTextResponse(render_dialog_status_text(data))
    return _ok(data, seq=ctx.seq)


@router.post("/dialog/handle", response_model=OkEnvelope[DialogHandleResponse])
async def handle_dialog_handle(
    body: DialogHandleRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    result = await ctx.dialog_handle(body.action, text=body.text)
    # Echo the requested action back so the renderer knows what we did.
    result.setdefault("action", body.action)
    if wants_text(request):
        return PlainTextResponse(render_dialog_handle_text(result))
    return _ok(result, seq=ctx.seq)


# --- Wait -------------------------------------------------------------------


@router.post("/wait", response_model=OkEnvelope[WaitResponse])
async def handle_wait(
    body: WaitRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    result = await ctx.wait(
        condition=body.condition,
        value=body.value,
        timeout=body.timeout,
        state=body.state,
    )
    # Surface the condition/value the renderer needs without forcing the
    # browser layer to echo them back.
    result.setdefault("condition", body.condition)
    result.setdefault("value", body.value)
    if wants_text(request):
        return PlainTextResponse(render_wait_text(result))
    return _ok(result, seq=ctx.seq)


# --- Upload -----------------------------------------------------------------


@router.post("/upload", response_model=OkEnvelope[UploadResponse])
async def handle_upload(
    body: UploadRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    if not body.files:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "missing_files",
                "hint": "No files provided for upload",
                "action": "provide 'files' as a list of file paths",
            },
        )
    result = await ctx.upload(body.index, body.files)
    # Backfill the renderer-only fields so the text path can format ``uploaded
    # 2 files to [7]`` without inspecting the original request again.
    result.setdefault("uploaded", len(body.files))
    result.setdefault("index", body.index)
    if wants_text(request):
        return PlainTextResponse(render_upload_text(result))
    return _ok(result, seq=ctx.seq)


# --- Frame ------------------------------------------------------------------


@router.get("/frame/list", response_model=OkEnvelope[FrameListResponse])
async def handle_frame_list(
    ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    frames = await ctx.frame_list()
    data = [{"name": f.name, "url": f.url, "is_current": f.is_current} for f in frames]
    envelope = {"frames": data, "count": len(data)}
    if wants_text(request):
        return PlainTextResponse(render_frame_list_text(envelope))
    return _ok(envelope, seq=ctx.seq)


@router.post("/frame/focus", response_model=OkEnvelope[FrameFocusResponse])
async def handle_frame_focus(
    body: FrameFocusRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    result = await ctx.frame_focus(name=body.name, url=body.url, main=body.main)
    # Backfill the renderer hint fields without changing the JSON envelope.
    result.setdefault("name", body.name)
    result.setdefault("url", body.url)
    result.setdefault("main", body.main)
    if wants_text(request):
        return PlainTextResponse(render_frame_focus_text(result))
    return _ok(result, seq=ctx.seq)
