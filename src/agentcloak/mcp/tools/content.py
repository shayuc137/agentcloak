"""Content tools — evaluate JS, fetch with cookies."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, cast

import orjson
from mcp.types import ToolAnnotations

from agentcloak.core.errors import AgentBrowserError
from agentcloak.core.text_renderers import render_evaluate_text, render_fetch_text
from agentcloak.mcp._format import error_json, format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    cfg = client.config  # single shared client snapshot.

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=False, readOnlyHint=False))
    async def agentcloak_evaluate(
        js: str = "",
        world: str = "main",
        max_return_size: int = cfg.browser.max_return_size,
        preset: str = "",
    ) -> str:
        """Execute JavaScript in the browser page context. Can modify page state.

        By default runs in the page's main world, so page globals (jQuery, Vue,
        React, etc.) are accessible. Use world='isolated' for an isolated context.

        Note: if evaluate triggers async requests (AJAX/fetch), those requests
        are captured asynchronously. Use agentcloak_network or capture tools
        to inspect them after a short delay.
        Supports async/await via IIFE: `(async () => { return await fn(); })()`

        Reverse-engineering presets (set ``preset``, leave ``js`` empty) run a
        canned snippet in the main world and return parsed JSON:
          vue_inspect   — Vue 2/3 component data/method/computed key names
          react_inspect — React component tree (names + props/state keys)
          jwt_decode    — decode JWTs found in cookies/local/sessionStorage
          cookie_parse  — structured document.cookie
          storage_dump  — full localStorage + sessionStorage dump

        Args:
            js: JavaScript code to evaluate (runs in page context with full DOM access)
            world: Execution context — 'main' (page globals visible)
                or 'isolated' (isolated)
            max_return_size: Max bytes of serialized result to return
                (default from config.browser.max_return_size). Large objects are
                truncated with a [truncated] marker.
            preset: Reverse-engineering preset name; takes precedence over 'js'
                and forces the main world.

        Returns:
            JSON with the evaluation result. Complex objects are serialized.
        """
        try:
            envelope = await client.evaluate(
                js, world=world, max_return_size=max_return_size, preset=preset
            )
        except AgentBrowserError as exc:
            return error_json(exc)

        # Daemon envelopes are untyped JSON; cast to ``dict[str, Any]`` once
        # so pyright stops widening every ``.get`` into ``Unknown | None``.
        data: dict[str, Any] = cast(
            "dict[str, Any]", envelope.get("data", envelope) or {}
        )
        # Design decision (audit #10): MCP-specific auto-unwrap. The CLI
        # surface returns the raw daemon payload verbatim, but agents on the
        # MCP side often pass JSON.stringify(...) at the end of their snippet
        # (so the page's response is captured as text instead of a host
        # object). Decoding the string here gives them the parsed object in
        # one tool call instead of forcing a second ``orjson.loads(result)``
        # round-trip through their reasoning loop. The CLI does NOT do this —
        # CLI callers either consume JSON via ``jq`` (where escaping is fine)
        # or pipe through their own parser.
        actual = data.get("result")
        if isinstance(actual, str) and len(actual) > 1 and actual[0] in ("{", "["):
            with contextlib.suppress(orjson.JSONDecodeError, ValueError):
                parsed = orjson.loads(actual)
                data = {**data, "result": parsed}
        # Hand off to the shared renderer so the formatting (scalar passthrough
        # vs pretty JSON for objects) is identical to the CLI text-mode path.
        return render_evaluate_text(data)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def agentcloak_fetch(
        url: str,
        method: str = "GET",
        body: str | None = None,
        headers_json: str | None = None,
        timeout: float = float(cfg.browser.navigation_timeout),
    ) -> str:
        """HTTP fetch using the browser's cookies and user agent.

        Makes a request as if the browser sent it — same cookies, same UA.
        For APIs that require browser authentication without full page interaction.

        Args:
            url: Request URL
            method: HTTP method (GET, POST, PUT, DELETE)
            body: Request body for POST/PUT
            headers_json: Extra headers as JSON object (e.g. '{"X-Custom": "value"}')
            timeout: Request timeout in seconds

        Returns:
            JSON with status, headers, and response body text.
        """
        headers: dict[str, Any] | None = None
        if headers_json is not None:
            # ``headers_json`` is typed as ``str | None``; we already filtered
            # ``None`` above. ``orjson.loads`` handles malformed JSON via the
            # surrounding try/except in ``format_call``.
            headers = orjson.loads(headers_json)
        return await format_call(
            client.fetch(
                url,
                method=method,
                body=body,
                headers=headers,
                timeout=timeout,
            ),
            render_fetch_text,
        )
