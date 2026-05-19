"""Cookie commands — export and import browser cookies."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — Typer needs runtime access

import orjson
import typer

from agentcloak.cli._dispatch import dispatch_text_or_json, emit_envelope
from agentcloak.cli.output import is_json_mode, value
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import (
    render_cookies_export_text,
    render_cookies_import_text,
)

__all__ = ["app"]

app = typer.Typer()


@app.command("export")
def cookies_export(
    url: str | None = typer.Option(None, "--url", "-u", help="URL to get cookies for."),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Save cookies to file."
    ),
) -> None:
    """Export cookies from the active browser session."""
    body: dict[str, object] = {}
    if url:
        body["url"] = url

    client = DaemonClient()
    if output is not None:
        # File output needs the structured envelope to serialize the cookie
        # list verbatim — text mode would only give us ``name=value`` lines.
        result = client.cookies_export_sync(url=url)
        data = result.get("data", result)
        output.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
        if is_json_mode():
            seq = int(result.get("seq", 0) or 0)
            emit_envelope(
                {
                    "ok": True,
                    "seq": seq,
                    "data": {
                        "saved": str(output),
                        "count": len(data.get("cookies", [])),
                    },
                }
            )
            return
        value(f"saved {output} ({len(data.get('cookies', []))} cookies)")
        return

    dispatch_text_or_json(
        client,
        "POST",
        "/cookies/export",
        json_body=body,
        renderer=render_cookies_export_text,
    )


@app.command("import")
def cookies_import(
    cookies_json: str = typer.Option(
        ...,
        "--cookies",
        "-c",
        help=(
            "JSON array of cookie objects, e.g. "
            '\'[{"name":"k","value":"v",'
            '"domain":".example.com","path":"/"}]\'.'
        ),
    ),
) -> None:
    """Import cookies into the browser (supports httpOnly)."""
    cookies = orjson.loads(cookies_json)
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/cookies/import",
        json_body={"cookies": cookies},
        renderer=render_cookies_import_text,
    )
