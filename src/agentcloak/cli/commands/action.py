"""Action commands — click, fill, type, scroll, hover, select, press, batch.

All action commands share the same dispatch pattern: assemble a body, call
``/action`` (or ``/action/batch``), and emit either JSON (``--json``) or the
:func:`agentcloak.core.text_renderers.render_action_text` one-liner. The
``--snap`` combo flag asks the daemon to attach a compact snapshot to the
action result so the agent can observe-and-act in one round-trip.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — Typer needs runtime access
from typing import TYPE_CHECKING, Any, cast

import orjson
import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.cli.output import error
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import render_action_text, render_batch_text

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["app"]

app = typer.Typer()


def _build_action_body(
    kind: str,
    *,
    index: int | None = None,
    target: str | None = None,
    snap: bool = False,
    snapshot_mode: str = "compact",
    **extras: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {"kind": kind}
    if index is not None:
        body["index"] = index
    if target is not None:
        body["target"] = target
    if snap:
        body["include_snapshot"] = True
        body["snapshot_mode"] = snapshot_mode
    for k, v in extras.items():
        if v is not None:
            body[k] = v
    return body


def _action_renderer(body: dict[str, Any]) -> Callable[[dict[str, Any]], str]:
    """Build a render closure that captures kind/target/text/key from ``body``.

    The CLI knows what action it dispatched; the daemon JSON payload no
    longer carries that context, so the renderer needs it injected here. We
    also forward ``text`` / ``key`` extras so ``filled [N] | value: '...'``
    and ``press Enter`` keep their pre-refactor shape.
    """
    kind = str(body.get("kind", ""))
    if body.get("index") is not None:
        target = str(body["index"])
    else:
        target = str(body.get("target", "") or "")
    text_extra = body.get("text", "")
    key_extra = body.get("key", "")

    def render(data: dict[str, Any]) -> str:
        # ``render_action_text`` reads ``data.text`` for fill output and
        # ``data.key`` for keyless press output — promote the body extras
        # into the data dict so the closure produces byte-identical text to
        # the pre-refactor daemon path.
        enriched = dict(data)
        if "text" not in enriched and text_extra:
            enriched["text"] = text_extra
        if "key" not in enriched and key_extra:
            enriched["key"] = key_extra
        return render_action_text(kind, target, enriched)

    return render


@app.command("click")
def do_click(
    target: int | None = typer.Argument(
        None,
        help="Element index [N] from snapshot. Equivalent to --index.",
    ),
    index: int | None = typer.Option(None, "--index", "-i", help="Element index [N]."),
    x: float | None = typer.Option(None, "--x", help="X coordinate (fallback)."),
    y: float | None = typer.Option(None, "--y", help="Y coordinate (fallback)."),
    button: str = typer.Option("left", "--button", help="Mouse button."),
    click_count: int = typer.Option(1, "--click-count", help="Number of clicks."),
    force: bool = typer.Option(
        False, "--force", help="Bypass overlays with a DOM click for element refs."
    ),
    snap: bool = typer.Option(
        False,
        "--snap",
        "--include-snapshot",
        help="Attach compact snapshot after the action (one round-trip).",
    ),
) -> None:
    """Click an element. Accept index as positional ``[N]`` or via ``--index``."""
    resolved = index if index is not None else target
    body = _build_action_body(
        "click",
        index=resolved,
        snap=snap,
        button=button,
        click_count=click_count,
        force=force,
        x=x,
        y=y,
    )
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/action",
        json_body=body,
        renderer=_action_renderer(body),
    )


@app.command("fill")
def do_fill(
    target: int | None = typer.Argument(None, help="Element index [N]."),
    text_pos: str | None = typer.Argument(None, help="Text to fill."),
    index: int | None = typer.Option(None, "--index", "-i", help="Element index [N]."),
    text: str | None = typer.Option(None, "--text", "-t", help="Text to fill."),
    snap: bool = typer.Option(
        False,
        "--snap",
        "--include-snapshot",
        help="Attach compact snapshot after the action.",
    ),
) -> None:
    """Fill an input element (clear then set value)."""
    resolved = index if index is not None else target
    if resolved is None:
        error("missing element index", "pass it as the first positional or --index N")
    if text is None:
        text = text_pos
    if text is None:
        error("missing text to fill", "pass it as the second positional or --text")
    body = _build_action_body("fill", index=resolved, snap=snap, text=text)
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/action",
        json_body=body,
        renderer=_action_renderer(body),
    )


@app.command("type")
def do_type(
    target: int | None = typer.Argument(None, help="Element index [N]."),
    text_pos: str | None = typer.Argument(None, help="Text to type."),
    index: int | None = typer.Option(None, "--index", "-i", help="Element index [N]."),
    text: str | None = typer.Option(None, "--text", "-t", help="Text to type."),
    delay: float = typer.Option(0, "--delay", help="Delay between keystrokes in ms."),
    snap: bool = typer.Option(
        False,
        "--snap",
        "--include-snapshot",
        help="Attach compact snapshot after the action.",
    ),
) -> None:
    """Type text character by character (per-key events)."""
    resolved = index if index is not None else target
    if resolved is None:
        error("missing element index", "pass it as the first positional or --index N")
    if text is None:
        text = text_pos
    if text is None:
        error("missing text to type", "pass it as the second positional or --text")
    body = _build_action_body("type", index=resolved, snap=snap, text=text, delay=delay)
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/action",
        json_body=body,
        renderer=_action_renderer(body),
    )


@app.command("scroll")
def do_scroll(
    direction_pos: str | None = typer.Argument(
        None, help="Direction: up/down/left/right. Defaults to 'down'."
    ),
    index: int | None = typer.Option(None, "--index", "-i", help="Element to scroll."),
    target: int | None = typer.Option(None, "--target", help="Alias for --index."),
    direction: str | None = typer.Option(
        None, "--direction", "-d", help="up/down/left/right."
    ),
    amount: int = typer.Option(300, "--amount", help="Scroll amount in pixels."),
    snap: bool = typer.Option(
        False,
        "--snap",
        "--include-snapshot",
        help="Attach compact snapshot after the action.",
    ),
) -> None:
    """Scroll the page or an element into view."""
    resolved = target if index is None else index
    final_dir = direction or direction_pos or "down"
    body = _build_action_body(
        "scroll", index=resolved, snap=snap, direction=final_dir, amount=amount
    )
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/action",
        json_body=body,
        renderer=_action_renderer(body),
    )


@app.command("hover")
def do_hover(
    target: int | None = typer.Argument(None, help="Element index [N]."),
    index: int | None = typer.Option(None, "--index", "-i", help="Element index [N]."),
    x: float | None = typer.Option(None, "--x", help="X coordinate (fallback)."),
    y: float | None = typer.Option(None, "--y", help="Y coordinate (fallback)."),
    snap: bool = typer.Option(
        False,
        "--snap",
        "--include-snapshot",
        help="Attach compact snapshot after the action.",
    ),
) -> None:
    """Hover over an element or coordinates."""
    resolved = index if index is not None else target
    body = _build_action_body("hover", index=resolved, snap=snap, x=x, y=y)
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/action",
        json_body=body,
        renderer=_action_renderer(body),
    )


@app.command("select")
def do_select(
    target: int | None = typer.Argument(None, help="Element index [N]."),
    index: int | None = typer.Option(None, "--index", "-i", help="Element index [N]."),
    value_opt: str | None = typer.Option(None, "--value", help="Option value."),
    label: str | None = typer.Option(None, "--label", help="Option display text."),
    snap: bool = typer.Option(
        False,
        "--snap",
        "--include-snapshot",
        help="Attach compact snapshot after the action.",
    ),
) -> None:
    """Select a dropdown option."""
    resolved = index if index is not None else target
    if resolved is None:
        error("missing element index", "pass it as the first positional or --index N")
    body = _build_action_body(
        "select", index=resolved, snap=snap, value=value_opt, label=label
    )
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/action",
        json_body=body,
        renderer=_action_renderer(body),
    )


@app.command("press")
def do_press(
    key_pos: str | None = typer.Argument(
        None, help="Key to press (Enter, Tab, Control+a, etc.)."
    ),
    key: str | None = typer.Option(None, "--key", "-k", help="Key to press."),
    target: int | None = typer.Option(
        None, "--target", help="Element index [N] to focus before pressing."
    ),
    snap: bool = typer.Option(
        False,
        "--snap",
        "--include-snapshot",
        help="Attach compact snapshot after the action.",
    ),
) -> None:
    """Press a keyboard key (supports modifiers like Control+a)."""
    final_key = key or key_pos
    if not final_key:
        error("missing key", "pass it as a positional arg or --key Enter")
    body = _build_action_body("press", index=target, snap=snap, key=final_key)
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/action",
        json_body=body,
        renderer=_action_renderer(body),
    )


@app.command("keydown")
def do_keydown(
    key_pos: str | None = typer.Argument(None, help="Key to hold down."),
    key: str | None = typer.Option(None, "--key", "-k", help="Key to hold down."),
    snap: bool = typer.Option(
        False,
        "--snap",
        "--include-snapshot",
        help="Attach compact snapshot after the action.",
    ),
) -> None:
    """Hold a key down (e.g. Shift, Control)."""
    final_key = key or key_pos
    if not final_key:
        error("missing key", "pass it as a positional arg or --key Shift")
    body = _build_action_body("keydown", snap=snap, key=final_key)
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/action",
        json_body=body,
        renderer=_action_renderer(body),
    )


@app.command("keyup")
def do_keyup(
    key_pos: str | None = typer.Argument(None, help="Key to release."),
    key: str | None = typer.Option(None, "--key", "-k", help="Key to release."),
    snap: bool = typer.Option(
        False,
        "--snap",
        "--include-snapshot",
        help="Attach compact snapshot after the action.",
    ),
) -> None:
    """Release a held key."""
    final_key = key or key_pos
    if not final_key:
        error("missing key", "pass it as a positional arg or --key Shift")
    body = _build_action_body("keyup", snap=snap, key=final_key)
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/action",
        json_body=body,
        renderer=_action_renderer(body),
    )


@app.command("batch")
def do_batch(
    calls_file: Path = typer.Option(
        ..., "--calls-file", help="JSONL file with actions."
    ),
    sleep: float = typer.Option(0.15, "--sleep", help="Seconds between actions."),
) -> None:
    """Execute a batch of actions from a JSONL or JSON array file.

    Two formats are accepted:

    * **JSONL** (one JSON object per line) — historical default.
    * **JSON array** (e.g. ``[{...}, {...}]``) — convenient when authoring
      by hand or piping the output of `jq`. Detected by a leading ``[``.

    Actions in either format can reference prior results with ``$N.path``
    syntax; e.g. ``$0.data.url`` references the URL from the first
    action's result.
    """
    if not calls_file.exists():
        error(f"file not found: {calls_file}", "pass an existing JSONL file")

    raw = calls_file.read_text(encoding="utf-8").strip()
    if not raw:
        error(
            f"file is empty: {calls_file}",
            "provide at least one action (JSONL line or JSON array element)",
        )

    actions: list[dict[str, Any]] = []
    try:
        if raw.startswith("["):
            # JSON array form. orjson.loads is typed as ``Any`` so we narrow
            # explicitly before populating ``actions`` to keep strict pyright
            # happy.
            parsed: object = orjson.loads(raw)
            if not isinstance(parsed, list):
                error(
                    f"invalid batch file: {calls_file}",
                    "top-level JSON must be an array of action objects",
                )
            else:
                actions = [
                    cast("dict[str, Any]", item) for item in cast("list[Any]", parsed)
                ]
        else:
            # JSONL form — one JSON object per non-blank line.
            for raw_line in raw.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                actions.append(cast("dict[str, Any]", orjson.loads(line)))
    except orjson.JSONDecodeError as exc:
        error(
            f"could not parse {calls_file}: {exc}",
            "expected JSONL (one JSON object per line) or a JSON array",
        )

    body = {"actions": actions, "sleep": sleep}
    client = DaemonClient()
    dispatch_text_or_json(
        client, "POST", "/action/batch", json_body=body, renderer=render_batch_text
    )
