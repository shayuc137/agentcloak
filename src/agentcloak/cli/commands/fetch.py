"""Fetch command — HTTP request with browser cookies.

Exposed as a flat ``agentcloak fetch <URL>`` (no nested ``fetch fetch``) via
``@app.callback(invoke_without_command=True)``.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — Typer needs runtime access

import typer

from agentcloak.cli._dispatch import emit_envelope
from agentcloak.cli.output import info, is_json_mode, value
from agentcloak.client import DaemonClient

__all__ = ["app"]

# See the original module-level comment in v0.2.0 for why we need
# ``allow_extra_args=False`` here (B4 from dogfood v0.2.0).
app = typer.Typer(
    invoke_without_command=True,
    context_settings={
        "allow_extra_args": False,
        "allow_interspersed_args": True,
    },
)


def _parse_header(raw: str) -> tuple[str, str]:
    """Parse 'Key: Value' into a (key, value) pair."""
    if ":" not in raw:
        msg = f"Invalid header format: '{raw}' (expected 'Key: Value')"
        raise typer.BadParameter(msg)
    key, _, val = raw.partition(":")
    return key.strip(), val.strip()


@app.callback(invoke_without_command=True)
def fetch_url(
    ctx: typer.Context,
    url: str | None = typer.Argument(None, help="URL to fetch."),
    method: str = typer.Option("GET", "--method", "-m", help="HTTP method."),
    body: str | None = typer.Option(None, "--body", "-b", help="Request body."),
    header: list[str] | None = typer.Option(
        None, "--header", "-H", help="Header in 'Key: Value' format (repeatable)."
    ),
    timeout: float | None = typer.Option(
        None,
        "--timeout",
        help="Request timeout in seconds (default: config.browser.navigation_timeout).",
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Save response body to file."
    ),
) -> None:
    """HTTP fetch using browser cookies and user agent."""
    if ctx.invoked_subcommand is not None:
        return
    if url is None:
        raise typer.BadParameter("URL argument is required")

    headers: dict[str, str] | None = None
    if header:
        headers = {}
        for h in header:
            k, v = _parse_header(h)
            headers[k] = v

    client = DaemonClient()
    # Fetch always goes through the JSON path because the body and metadata
    # (status, content_type, truncated) live in separate fields. Text mode
    # extracts the body afterward.
    result = client.fetch_sync(
        url,
        method=method,
        body=body,
        headers=headers,
        timeout=timeout,
    )
    data = result.get("data", result)
    seq = int(result.get("seq", 0) or 0)

    if output:
        resp_body: str = data.get("body", "")
        output.write_text(resp_body, encoding="utf-8")
        if is_json_mode():
            emit_envelope(
                {
                    "ok": True,
                    "seq": seq,
                    "data": {
                        "saved": str(output),
                        "status": data.get("status"),
                        "content_type": data.get("content_type", ""),
                        "truncated": data.get("truncated", False),
                    },
                }
            )
            return
        value(f"saved {output} ({data.get('status')} {data.get('content_type', '')})")
        return

    if is_json_mode():
        emit_envelope({"ok": True, "seq": seq, "data": data})
        return
    # Text mode: print body to stdout, status to stderr so pipes get clean
    # data.
    status = data.get("status")
    ctype = data.get("content_type", "")
    if status is not None:
        info(f"status={status} content_type={ctype}")
    value(data.get("body", "") or "")
