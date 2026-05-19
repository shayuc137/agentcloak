"""Built-in pipeline step handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx

from agentcloak.core.errors import AgentBrowserError
from agentcloak.spells.pipeline.template import render, render_deep

__all__ = ["STEP_REGISTRY", "StepContext"]

StepHandler = Callable[[Any, Any, "StepContext"], Awaitable[Any]]


# Fallback UA used by PUBLIC-strategy spells that don't have a browser session
# to inherit from. ``httpx``'s default UA is ``python-httpx/<ver>`` which trips
# basic anti-bot heuristics on the same sites these spells are designed to
# reach. We synthesise a CloakBrowser-version-aligned Chrome UA so the request
# looks identical to whatever an attached browser would have sent — TLS
# fingerprint matching happens at the proxy layer, but the UA still has to look
# real or servers will diverge before they even check the handshake.
_FALLBACK_CHROME_UA_TEMPLATE = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/{major}.0.0.0 Safari/537.36"
)
_FALLBACK_CHROME_MAJOR_DEFAULT = "131"


def _fallback_chrome_user_agent() -> str:
    """Return a Chrome-shaped UA string matching the installed CloakBrowser.

    ``cloakbrowser`` is an optional dependency for PUBLIC spells (they don't
    need a live browser), so we silently fall back to a recent major version
    when import fails. The exact number isn't security-critical — what matters
    is that the UA *looks* like a real Chrome.
    """
    major = _FALLBACK_CHROME_MAJOR_DEFAULT
    try:
        import cloakbrowser  # pyright: ignore[reportMissingTypeStubs]

        version_str = getattr(cloakbrowser, "CHROMIUM_VERSION", "")
        if version_str:
            head = str(version_str).split(".")[0]
            if head.isdigit():
                major = head
    except ImportError:
        pass
    return _FALLBACK_CHROME_UA_TEMPLATE.format(major=major)


class StepContext:
    """Runtime context available to each pipeline step."""

    def __init__(
        self,
        *,
        args: dict[str, Any],
        browser: Any | None = None,
    ) -> None:
        self.args = args
        self.browser = browser

    def template_context(
        self,
        data: Any = None,
        item: Any = None,
        index: int = 0,
    ) -> dict[str, Any]:
        ctx: dict[str, Any] = {"args": self.args, "data": data}
        if item is not None:
            ctx["item"] = item
            ctx["index"] = index
        return ctx


async def _step_fetch(params: Any, data: Any, ctx: StepContext) -> Any:
    """Fetch a URL, inheriting browser session state when one is available.

    Two paths:

    1. **Browser attached** (``COOKIE``/``HEADER``/``INTERCEPT``/``UI``
       strategies, or a manually-injected context) — delegate to
       ``ctx.browser.fetch()`` which threads through cookies, the live UA,
       and any active proxy. This is what makes a captured session work for
       follow-up API calls.
    2. **No browser** (``PUBLIC`` strategy) — use ``httpx`` directly but with
       a CloakBrowser-version-aligned Chrome UA. Servers that fingerprint on
       UA see the same string a stealth session would send.
    """
    from agentcloak.core.config import load_config

    rendered: Any = render_deep(params, ctx.template_context(data))
    if isinstance(rendered, str):
        rendered = {"url": rendered}
    cfg = cast("dict[str, Any]", rendered)
    url: str = cfg["url"]
    method: str = cfg.get("method", "GET").upper()
    headers: dict[str, str] = cast("dict[str, str]", cfg.get("headers", {}))
    body: str | None = cfg.get("body")

    _, _agent_cfg = load_config()
    _timeout = float(_agent_cfg.browser.navigation_timeout)

    if ctx.browser is not None:
        result = await ctx.browser.fetch(
            url,
            method=method,
            body=body,
            headers=headers,
            timeout=_timeout,
        )
        # ``BrowserContext.fetch`` returns a dict with ``body`` already JSON-
        # parsed when ``content-type`` looked like JSON. Return that directly
        # so downstream ``select`` steps don't have to know which transport
        # ran the request.
        return result.get("body", result)

    # No browser context — apply the Chrome UA fallback unless the spell
    # author overrode it.
    req_headers: dict[str, str] = dict(headers)
    req_headers.setdefault("User-Agent", _fallback_chrome_user_agent())

    async with httpx.AsyncClient(timeout=_timeout) as client:
        resp = await client.request(method, url, headers=req_headers, content=body)
        resp.raise_for_status()
        return resp.json()


async def _step_select(params: Any, data: Any, ctx: StepContext) -> Any:
    path: Any = render(params, ctx.template_context(data))
    if not isinstance(path, str):
        return data
    parts = path.split(".")
    result: Any = data
    for part in parts:
        if isinstance(result, dict):
            result = cast("Any", result[part])
        elif isinstance(result, (list, tuple)) and part.isdigit():
            result = cast("Any", result[int(part)])
        else:
            result = getattr(cast("Any", result), part)
    return result


async def _step_map(params: Any, data: Any, ctx: StepContext) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        msg = "map step requires a list as input"
        raise AgentBrowserError(
            error="pipeline_type_error",
            hint=msg,
            action="check pipeline step order",
        )
    if not isinstance(params, dict):
        msg = "map step params must be a dict of field mappings"
        raise AgentBrowserError(
            error="pipeline_config_error",
            hint=msg,
            action="fix map step definition",
        )
    items = cast("list[Any]", data)
    mapping = cast("dict[str, Any]", params)
    result: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        tctx = ctx.template_context(data, item=item, index=i)
        row: dict[str, Any] = {str(k): render(v, tctx) for k, v in mapping.items()}
        result.append(row)
    return result


async def _step_filter(params: Any, data: Any, ctx: StepContext) -> list[Any]:
    if not isinstance(data, list):
        msg = "filter step requires a list as input"
        raise AgentBrowserError(
            error="pipeline_type_error",
            hint=msg,
            action="check pipeline step order",
        )
    items = cast("list[Any]", data)
    expr: Any = params
    result: list[Any] = []
    for i, item in enumerate(items):
        tctx = ctx.template_context(data, item=item, index=i)
        val: Any = render(expr, tctx)
        if val:
            result.append(item)
    return result


async def _step_limit(params: Any, data: Any, ctx: StepContext) -> list[Any]:
    if not isinstance(data, list):
        msg = "limit step requires a list as input"
        raise AgentBrowserError(
            error="pipeline_type_error",
            hint=msg,
            action="check pipeline step order",
        )
    n: Any = render(params, ctx.template_context(data))
    return list(cast("list[Any]", data)[: int(n)])


async def _step_navigate(params: Any, data: Any, ctx: StepContext) -> Any:
    if ctx.browser is None:
        msg = "navigate step requires a browser context"
        raise AgentBrowserError(
            error="pipeline_no_browser",
            hint=msg,
            action="set strategy to COOKIE, HEADER, INTERCEPT, or UI",
        )
    url: Any = render(params, ctx.template_context(data))
    await ctx.browser.navigate(str(url))
    return data


async def _step_evaluate(params: Any, data: Any, ctx: StepContext) -> Any:
    if ctx.browser is None:
        msg = "evaluate step requires a browser context"
        raise AgentBrowserError(
            error="pipeline_no_browser",
            hint=msg,
            action="set strategy to COOKIE, HEADER, INTERCEPT, or UI",
        )
    js: Any = render(params, ctx.template_context(data))
    return await ctx.browser.evaluate(str(js))


STEP_REGISTRY: dict[str, StepHandler] = {
    "fetch": _step_fetch,
    "select": _step_select,
    "map": _step_map,
    "filter": _step_filter,
    "limit": _step_limit,
    "navigate": _step_navigate,
    "evaluate": _step_evaluate,
}
