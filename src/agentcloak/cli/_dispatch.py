"""CLI dispatch helper — text-mode by default, JSON envelope on opt-in.

CLI commands all share the same shape:

* When ``--json`` (or ``AGENTCLOAK_OUTPUT=json``) is active, call the typed
  ``*_sync`` method on :class:`DaemonClient`, unwrap ``{ok, seq, data}``,
  and emit the full envelope.
* Otherwise: issue the same JSON request, hand the inner ``data`` dict to
  a render function from :mod:`agentcloak.core.text_renderers`, and print
  the rendered string.

Centralising the branch in one helper keeps every command file a thin
parameter-binding shell. The alternative — wiring two code paths into 20+
commands — is exactly the kind of duplication v0.3.x set out to remove.

Why pass ``renderer`` from the call site?
-----------------------------------------
The mapping from a daemon route to its text renderer isn't a simple
``path → callable`` lookup: ``/action`` renders based on ``kind`` and
``target`` (request-side context), while ``/snapshot`` needs the envelope's
``seq`` promoted into the data dict before the header line can be built.
Letting the command supply a closure that closes over its specific context
keeps the dispatcher dumb and the per-command logic explicit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcloak.cli.output import is_json_mode, json_out, value

if TYPE_CHECKING:
    from collections.abc import Callable

    from agentcloak.client import DaemonClient

__all__ = ["dispatch_text_or_json", "emit_envelope", "run_with_renderer"]


def emit_envelope(result: dict[str, Any]) -> None:
    """Emit a daemon envelope as JSON to stdout.

    Helper for commands whose CLI flow doesn't fit the simple ``method+path``
    dispatch (e.g. local-only spell run or commands that combine multiple
    daemon calls). ``data`` and ``seq`` are extracted from the standard
    envelope shape — fall back to the raw dict when the caller already
    unwrapped it.
    """
    data = result.get("data", result)
    seq = int(result.get("seq", 0) or 0)
    json_out(data, seq=seq)


def run_with_renderer(
    client: DaemonClient,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    renderer: Callable[[dict[str, Any]], str],
    promote_seq: bool = False,
) -> None:
    """Send a JSON request and emit via ``renderer`` in text mode, envelope in JSON.

    Args:
        client: Shared :class:`DaemonClient` (handles auto-start).
        method: HTTP verb.
        path: Daemon route.
        json_body: Optional JSON body.
        params: Optional query params.
        renderer: Callable that turns the inner ``data`` dict into a string.
            Closures are encouraged when extra request-side context is needed
            (e.g. ``lambda d: render_action_text(kind, target, d)``).
        promote_seq: When ``True``, copy ``envelope.seq`` into ``data["seq"]``
            before calling the renderer. Used by ``/snapshot`` so the header
            line ``... | seq=N`` matches the pre-refactor daemon output even
            though ``seq`` only lives in the envelope.
    """
    result = client._send_sync(  # pyright: ignore[reportPrivateUsage]
        method, path, json_body=json_body, params=params
    )
    if is_json_mode():
        emit_envelope(result)
        return
    data: dict[str, Any] = result.get("data", result) or {}
    if promote_seq:
        seq = int(result.get("seq", 0) or 0)
        data = {**data, "seq": seq}
    value(renderer(data))


def dispatch_text_or_json(
    client: DaemonClient,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    renderer: Callable[[dict[str, Any]], str] | None = None,
    promote_seq: bool = False,
) -> None:
    """Issue a daemon request and emit honouring ``--json`` mode.

    Thin wrapper over :func:`run_with_renderer` — kept for call-site
    compatibility with commands that haven't been switched to passing
    a renderer explicitly. When ``renderer`` is omitted the dispatcher
    falls back to ``orjson.dumps(data)`` so we never silently drop the
    response, but every active route should supply one.
    """
    if renderer is None:
        # Fallback path — used only by routes that haven't been wired with a
        # dedicated renderer (none expected after the migration). Emits the
        # raw JSON payload so callers still see *something*.
        import orjson

        def _default(data: dict[str, Any]) -> str:
            return orjson.dumps(data, option=orjson.OPT_INDENT_2).decode()

        renderer = _default
    run_with_renderer(
        client,
        method,
        path,
        json_body=json_body,
        params=params,
        renderer=renderer,
        promote_seq=promote_seq,
    )
