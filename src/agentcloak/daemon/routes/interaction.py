"""Page-interaction routes — cookies, dialog, wait, upload, frame.

These all act on the live page state rather than the broader daemon
lifecycle. Cookie import/export hits both local and remote backends
(remote_ctx via CDP, local via Playwright's BrowserContext API), so the
helpers stay close to the routes that need them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from agentcloak.core.cookie_snapshot import normalize_cookies_for_playwright
from agentcloak.daemon.dependencies import (  # noqa: TC001
    BrowserCtxDep,
    RemoteCtxDep,
)
from agentcloak.daemon.models import (
    CookieDeleteRequest,
    CookieDeleteResponse,
    CookiesClearResponse,
    CookieSetRequest,
    CookieSetResponse,
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

__all__ = ["router"]

router = APIRouter()


# --- Cookies ----------------------------------------------------------------


@router.post("/cookies/export", response_model=OkEnvelope[CookiesExportResponse])
async def handle_cookies_export(
    body: CookiesExportRequest,
    ctx: BrowserCtxDep,
    remote_ctx: RemoteCtxDep,
) -> dict[str, Any]:
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
        return _ok(data, seq=0)

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
    return _ok(data, seq=ctx.seq)


@router.post("/cookies/import", response_model=OkEnvelope[CookiesImportResponse])
async def handle_cookies_import(
    body: CookiesImportRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
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
    cookies, skipped = normalize_cookies_for_playwright(body.cookies)
    browser_context = ctx._get_browser_context()
    await browser_context.add_cookies(cookies)
    data = {"imported": len(cookies), "skipped": skipped}
    return _ok(data, seq=ctx.seq)


@router.post("/cookies/set", response_model=OkEnvelope[CookieSetResponse])
async def handle_cookies_set(
    body: CookieSetRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    """Set cookies directly or parse them from a Copy-as-cURL string (7a R3).

    ``cookies`` and ``curl`` may both be supplied; the parsed curl cookies are
    appended to the explicit list. Goes through ``ctx.cookies_set`` so both
    backends (Playwright ``add_cookies`` / CDP ``Network.setCookie``) share
    the same audit-logged path.
    """
    from agentcloak.core.curl_parser import parse_curl_cookies

    cookies: list[dict[str, Any]] = list(body.cookies or [])
    if body.curl:
        cookies.extend(parse_curl_cookies(body.curl))

    if not cookies:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "no_cookies",
                "hint": "No cookies provided (empty list and no curl string)",
                "action": "pass 'cookies' as a JSON array or 'curl' as a "
                "Copy-as-cURL string",
            },
        )

    result = await ctx.cookies_set(cookies)
    return _ok(result, seq=ctx.seq)


@router.post("/cookies/clear", response_model=OkEnvelope[CookiesClearResponse])
async def handle_cookies_clear(ctx: BrowserCtxDep) -> dict[str, Any]:
    """Remove all cookies from the browser context (7a R3)."""
    result = await ctx.cookies_clear()
    return _ok(result, seq=ctx.seq)


@router.post("/cookies/delete", response_model=OkEnvelope[CookieDeleteResponse])
async def handle_cookies_delete(
    body: CookieDeleteRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    """Delete cookies matching ``name`` (optionally scoped to ``domain``) (7a R3)."""
    result = await ctx.cookies_delete(body.name, domain=body.domain)
    return _ok(result, seq=ctx.seq)


# --- Dialog -----------------------------------------------------------------


@router.get("/dialog/status", response_model=OkEnvelope[DialogStatusResponse])
async def handle_dialog_status(ctx: BrowserCtxDep) -> dict[str, Any]:
    dialog = await ctx.dialog_status()
    if dialog is None:
        return _ok({"pending": False}, seq=ctx.seq)
    data = {
        "pending": True,
        "dialog": {
            "type": dialog.dialog_type,
            "message": dialog.message,
            "default_value": dialog.default_value,
            "url": dialog.url,
        },
    }
    return _ok(data, seq=ctx.seq)


@router.post("/dialog/handle", response_model=OkEnvelope[DialogHandleResponse])
async def handle_dialog_handle(
    body: DialogHandleRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    result = await ctx.dialog_handle(body.action, text=body.text)
    # Echo the requested action so the response is self-describing and CLI/MCP
    # renderers don't have to re-read the request body. Belongs in the JSON
    # response too (an API consumer parsing the envelope wants to see ``action``
    # next to ``handled``).
    result.setdefault("action", body.action)
    return _ok(result, seq=ctx.seq)


# --- Wait -------------------------------------------------------------------


@router.post("/wait", response_model=OkEnvelope[WaitResponse])
async def handle_wait(body: WaitRequest, ctx: BrowserCtxDep) -> dict[str, Any]:
    result = await ctx.wait(
        condition=body.condition,
        value=body.value,
        timeout=body.timeout,
        state=body.state,
    )
    # Surface condition/value alongside elapsed_ms so both API callers and
    # CLI/MCP renderers see ``matched selector=#login | 142ms`` without
    # re-reading the request body.
    result.setdefault("condition", body.condition)
    result.setdefault("value", body.value)
    return _ok(result, seq=ctx.seq)


# --- Upload -----------------------------------------------------------------


@router.post("/upload", response_model=OkEnvelope[UploadResponse])
async def handle_upload(body: UploadRequest, ctx: BrowserCtxDep) -> dict[str, Any]:
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
    result = await ctx.upload(body.index, body.files, nth=body.nth)
    # ``uploaded`` + ``index`` are echoed so the response is self-describing
    # for ``uploaded 2 files to [7]`` — both JSON consumers and the CLI/MCP
    # renderer benefit. With auto-find (index omitted) there's no [N] to echo;
    # the base attaches ``candidates_count`` / ``used_nth`` instead.
    result.setdefault("uploaded", len(body.files))
    if body.index is not None:
        result.setdefault("index", body.index)
    return _ok(result, seq=ctx.seq)


# --- Frame ------------------------------------------------------------------


@router.get("/frame/list", response_model=OkEnvelope[FrameListResponse])
async def handle_frame_list(ctx: BrowserCtxDep) -> dict[str, Any]:
    frames = await ctx.frame_list()
    data = [{"name": f.name, "url": f.url, "is_current": f.is_current} for f in frames]
    envelope = {"frames": data, "count": len(data)}
    return _ok(envelope, seq=ctx.seq)


@router.post("/frame/focus", response_model=OkEnvelope[FrameFocusResponse])
async def handle_frame_focus(
    body: FrameFocusRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    result = await ctx.frame_focus(name=body.name, url=body.url, main=body.main)
    # Echo the request fields so the response identifies which frame was
    # focused — useful for JSON callers and the text renderer alike.
    result.setdefault("name", body.name)
    result.setdefault("url", body.url)
    result.setdefault("main", body.main)
    return _ok(result, seq=ctx.seq)
