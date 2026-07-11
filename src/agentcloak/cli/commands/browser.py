"""Browser commands — navigate, screenshot, snapshot, resume.

Text-mode rendering lives in :mod:`agentcloak.core.text_renderers` and is
shared with the MCP surface. The CLI is responsible for choosing the
mode (``--json`` flag), assembling the request, and routing the daemon's
JSON payload through the right renderer. Anything that mutates local
state — e.g. ``screenshot`` decoding base64 and saving a file — also
lives here because the daemon has no access to the user's filesystem.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from tempfile import gettempdir

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json, emit_envelope
from agentcloak.cli.output import error, error_from_exception, info, is_json_mode, value
from agentcloak.client import DaemonClient
from agentcloak.core.errors import AgentBrowserError
from agentcloak.core.screenshot_format import resolve_screenshot_format
from agentcloak.core.text_renderers import (
    render_navigate_text,
    render_resume_text,
    render_snapshot_text,
)

__all__ = ["app"]

app = typer.Typer()


@app.command("navigate")
def browser_navigate(
    url: str = typer.Argument(help="URL to navigate to."),
    timeout: float | None = typer.Option(
        None,
        "--timeout",
        help=(
            "Navigation timeout in seconds (default: "
            "config.browser.navigation_timeout)."
        ),
    ),
    snap: bool = typer.Option(
        False,
        "--snap",
        "--snapshot",
        help="Attach compact snapshot to the navigate result (one round-trip).",
    ),
    snapshot_mode: str = typer.Option(
        "compact",
        "--snapshot-mode",
        help="Snapshot mode when --snap is used: compact, accessible.",
    ),
) -> None:
    """Navigate to a URL."""
    client = DaemonClient()
    body: dict[str, object] = {"url": url}
    if timeout is not None:
        body["timeout"] = timeout
    if snap:
        body["include_snapshot"] = True
        body["snapshot_mode"] = snapshot_mode
    dispatch_text_or_json(
        client, "POST", "/navigate", json_body=body, renderer=render_navigate_text
    )


@app.command("screenshot")
def browser_screenshot(
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Save to a specific file. Default: <system-temp>/agentcloak-<ts>.<ext>.",
    ),
    full_page: bool = typer.Option(
        False, "--full-page", help="Capture full scrollable page."
    ),
    format: str | None = typer.Option(
        None,
        "--format",
        "-f",
        help="Image format override: jpeg or png (default: browser config).",
    ),
    quality: int | None = typer.Option(
        None,
        "--quality",
        "-q",
        help=(
            "JPEG quality 0-100 (default: config.browser.screenshot_quality, "
            "ignored for png)."
        ),
    ),
    wait_selector: str = typer.Option(
        "",
        "--wait-selector",
        help="Wait for this selector to be visible before capture.",
    ),
    wait_timeout: int | None = typer.Option(
        None,
        "--wait-timeout",
        help="Selector wait timeout in ms (default: browser.action_timeout).",
    ),
    wait_for: str = typer.Option(
        "",
        "--wait-for",
        help="Wait for this CSS selector via the wait command before capture.",
    ),
) -> None:
    """Take a screenshot. Defaults to a file in the system temp dir; prints the path."""
    client = DaemonClient()
    try:
        resolution = resolve_screenshot_format(
            explicit_format=format,
            output_path=output,
            default_format=None,
        )
    except AgentBrowserError as exc:
        error_from_exception(exc)
        return
    # Always pull the JSON envelope so we get the base64 payload — text mode
    # would only give us a metadata line.
    try:
        if wait_for:
            wait_body: dict[str, object] = {
                "condition": "selector",
                "value": wait_for,
                "state": "visible",
            }
            if wait_timeout is not None:
                wait_body["timeout"] = wait_timeout
            client._send_sync("POST", "/wait", json_body=wait_body)  # pyright: ignore[reportPrivateUsage]
        result = client.screenshot_sync(
            full_page=full_page,
            format=resolution.format,
            quality=quality,
            wait_selector=wait_selector,
            wait_timeout=wait_timeout,
        )
    except AgentBrowserError as exc:
        error_from_exception(exc)
        return
    data = result.get("data", result)
    seq = int(result.get("seq", 0) or 0)
    resolved_format = str(data.get("format", "jpeg") or "jpeg")
    warning = resolution.warning_for(resolved_format)

    b64_str: str = data.get("base64", "")
    if not b64_str:
        if is_json_mode():
            emit_envelope(result)
            return
        error("screenshot returned empty payload", "retry, or check daemon logs")
        return

    if output is None:
        ts = int(time.time() * 1000)
        ext = "png" if resolved_format == "png" else "jpg"
        output = Path(gettempdir()) / f"agentcloak-{ts}.{ext}"

    output.write_bytes(base64.b64decode(b64_str))

    if is_json_mode():
        emit_envelope(
            {
                "ok": True,
                "seq": seq,
                "data": {
                    "saved": str(output),
                    "size": data.get("size", 0),
                    "format": resolved_format,
                    **({"warning": warning} if warning else {}),
                },
            }
        )
        return
    if warning:
        info(f"Warning: {warning}")
    value(str(output))


@app.command("snapshot")
def browser_snapshot(
    mode: str = typer.Option(
        "compact",
        "--mode",
        "-m",
        help="Snapshot mode: compact (default), accessible, dom, content.",
    ),
    max_chars: int = typer.Option(
        0,
        "--max-chars",
        help="Truncate tree_text to this many characters (0 = no limit).",
    ),
    limit: int = typer.Option(
        -1,
        "--limit",
        "--max-nodes",
        help=(
            "Truncate after N nodes. Default applies "
            "config.browser.snapshot_max_nodes (80) in compact mode; pass "
            "--limit 0 to opt back into the full tree. --max-nodes "
            "is the legacy alias."
        ),
    ),
    focus: int = typer.Option(
        0,
        "--focus",
        help="Expand subtree around element [N] from cached snapshot.",
    ),
    offset: int = typer.Option(
        0,
        "--offset",
        help="Start output from Nth element (pagination).",
    ),
    frames: bool = typer.Option(
        False,
        "--frames",
        help="Include iframe content in the snapshot (merges child frame AX trees).",
    ),
    selector: str = typer.Option(
        "",
        "--selector",
        "--within",
        "-s",
        help="Scope the snapshot to a main-document CSS selector.",
    ),
    diff: bool = typer.Option(
        False,
        "--diff",
        help="Compare with previous snapshot, mark [+] added and [~] changed.",
    ),
    selector_map: bool = typer.Option(
        False,
        "--selector-map",
        help="Include selector_map (off by default — agents don't need it).",
    ),
) -> None:
    """Get page snapshot (accessibility tree by default)."""
    client = DaemonClient()
    params: dict[str, str] = {"mode": mode}
    if max_chars:
        params["max_chars"] = str(max_chars)
    # ``limit == -1`` is the "user didn't pass --limit" sentinel — leave
    # ``max_nodes`` out so the daemon applies its compact-mode default
    # (``config.browser.snapshot_max_nodes``). ``--limit 0`` opts back into the full
    # tree and we must forward the literal ``0`` to override the default.
    if limit != -1:
        params["max_nodes"] = str(limit)
    if focus:
        params["focus"] = str(focus)
    if offset:
        params["offset"] = str(offset)
    if frames:
        params["frames"] = "true"
    if selector:
        params["selector"] = selector
    if diff:
        params["diff"] = "true"
    if selector_map:
        params["include_selector_map"] = "true"
    else:
        params["include_selector_map"] = "false"
    # ``promote_seq`` copies envelope.seq → data.seq so the snapshot header
    # line ``... | seq=N`` matches the pre-refactor daemon output without
    # leaking seq into the JSON payload.
    dispatch_text_or_json(
        client,
        "GET",
        "/snapshot",
        params=params,
        renderer=render_snapshot_text,
        promote_seq=True,
    )


@app.command("resume")
def browser_resume() -> None:
    """Get the resume snapshot for session recovery."""
    client = DaemonClient()
    dispatch_text_or_json(client, "GET", "/resume", renderer=render_resume_text)


# Suppress unused-import warning for the ``info`` helper — it's reserved for
# commands like ``screenshot`` that might add stderr breadcrumbs later.
_ = info
