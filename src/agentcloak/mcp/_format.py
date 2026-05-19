"""Shared response formatting for MCP tools.

The shared :class:`~agentcloak.client.DaemonClient` raises
:class:`AgentBrowserError` on any non-2xx response. MCP tools need to return
strings, so this module centralises:

* exception → three-field JSON envelope (matches CLI ``--json`` error shape)
* envelope → human-friendly text via :mod:`agentcloak.core.text_renderers`,
  the same renderers the CLI uses in text mode. Sharing the renderer keeps
  the CLI and MCP surfaces byte-identical for any given daemon payload.

Why text and not JSON?
----------------------
Pre-v0.3.x ``format_envelope`` emitted ``orjson.dumps(data)``. Agents on
the MCP side were paying tokens for every key name, quote, and brace.
Switching to the shared text renderers cuts an averaged ~40% off the
response size for snapshot/tab/network responses and keeps both surfaces
aligned (no more "MCP says ``{"url": "..."}``, CLI says ``url | title``"
drift).

Error responses still use the three-field JSON envelope —
``{"error", "hint", "action"}`` is the schema MCP clients already parse
for failure handling, and switching that to bare text would break the
existing CLI ``--json`` contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import orjson

from agentcloak.core.errors import AgentBrowserError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = [
    "error_json",
    "format_call",
    "format_call_with",
    "render_envelope",
]


def render_envelope(
    envelope: dict[str, Any],
    renderer: Callable[[dict[str, Any]], str],
    *,
    promote_seq: bool = False,
) -> str:
    """Run ``renderer`` over the envelope's inner ``data`` dict and return the string.

    ``promote_seq`` copies ``envelope["seq"]`` into ``data["seq"]`` first.
    Used by snapshot-shaped routes where the renderer needs ``seq`` to
    build the header line but the daemon keeps ``seq`` envelope-only.
    """
    data: dict[str, Any] = envelope.get("data", envelope) or {}
    if promote_seq:
        seq = int(envelope.get("seq", 0) or 0)
        data = {**data, "seq": seq}
    return renderer(data)


def error_json(exc: AgentBrowserError) -> str:
    """Render an :class:`AgentBrowserError` as the standard three-field envelope.

    Kept as JSON because MCP clients parse this shape for failure handling.
    Switching to bare text would break the existing CLI ``--json`` contract.
    """
    return orjson.dumps(
        {
            "error": exc.error,
            "hint": exc.hint,
            "action": exc.action,
        }
    ).decode()


async def format_call(
    coro: Awaitable[dict[str, Any]],
    renderer: Callable[[dict[str, Any]], str],
    *,
    promote_seq: bool = False,
) -> str:
    """Run a daemon coroutine and render the result via ``renderer``.

    Errors are translated to the JSON three-field envelope.
    Success payloads pass through ``render_envelope`` so MCP and CLI emit
    byte-identical text for the same daemon response.
    """
    try:
        result = await coro
    except AgentBrowserError as exc:
        return error_json(exc)
    return render_envelope(result, renderer, promote_seq=promote_seq)


async def format_call_with(
    coro: Awaitable[dict[str, Any]],
    extract: Callable[[dict[str, Any]], str],
) -> str:
    """Variant of :func:`format_call` for renderers that need the full envelope.

    Some MCP tools want access to envelope-level metadata (``seq``) *and*
    custom post-processing (e.g. screenshot returning ``ImageContent``).
    Those tools pass an ``extract`` callable that receives the raw envelope
    and returns the final string.
    """
    try:
        result = await coro
    except AgentBrowserError as exc:
        return error_json(exc)
    return extract(result)
