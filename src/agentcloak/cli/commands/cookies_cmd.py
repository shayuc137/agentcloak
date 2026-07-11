"""Cookie commands — export and import browser cookies."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — Typer needs runtime access

import orjson
import typer

from agentcloak.cli._dispatch import dispatch_text_or_json, emit_envelope
from agentcloak.cli.output import error_from_exception, is_json_mode, value
from agentcloak.client import DaemonClient
from agentcloak.core.config import load_config
from agentcloak.core.cookie_snapshot import (
    read_cookie_snapshot,
    resolve_cookie_snapshot_path,
    write_cookie_snapshot,
)
from agentcloak.core.errors import AgentBrowserError
from agentcloak.core.text_renderers import (
    render_cookie_delete_text,
    render_cookie_set_text,
    render_cookies_clear_text,
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

    try:
        result = client.cookies_export_sync(url=url)
        data = result.get("data", result)
        health = client.health_sync()
        paths, _ = load_config()
        snapshot = resolve_cookie_snapshot_path(
            paths, str(health.get("active_profile") or "") or None
        )
        write_cookie_snapshot(snapshot, data)
    except AgentBrowserError as exc:
        error_from_exception(exc)
        return

    if is_json_mode():
        emit_envelope(result)
        return
    value(render_cookies_export_text(data))


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


@app.command("restore")
def cookies_restore(
    file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        help="Restore from this snapshot instead of the active profile default.",
    ),
) -> None:
    """Restore cookies from the latest client-side snapshot."""
    client = DaemonClient()
    try:
        snapshot = file.expanduser() if file is not None else None
        if snapshot is None:
            health = client.health_sync()
            paths, _ = load_config()
            snapshot = resolve_cookie_snapshot_path(
                paths, str(health.get("active_profile") or "") or None
            )
        cookies = read_cookie_snapshot(snapshot)
    except AgentBrowserError as exc:
        error_from_exception(exc)
        return

    dispatch_text_or_json(
        client,
        "POST",
        "/cookies/import",
        json_body={"cookies": cookies},
        renderer=render_cookies_import_text,
    )


@app.command("set")
def cookies_set(
    name: str | None = typer.Argument(
        None, help="Cookie name (omit when using --curl)."
    ),
    value: str | None = typer.Argument(
        None, help="Cookie value (omit when using --curl)."
    ),
    domain: str = typer.Option(
        "", "--domain", "-d", help="Cookie domain, e.g. .example.com."
    ),
    path: str = typer.Option("/", "--path", help="Cookie path."),
    curl: str = typer.Option(
        "",
        "--curl",
        help="Parse cookies from a DevTools 'Copy as cURL' command string.",
    ),
) -> None:
    """Set a cookie directly, or import cookies from a Copy-as-cURL string."""
    body: dict[str, object] = {}
    if curl:
        body["curl"] = curl
    if name is not None and value is not None:
        cookie: dict[str, object] = {"name": name, "value": value, "path": path}
        if domain:
            cookie["domain"] = domain
        body["cookies"] = [cookie]
    if not body:
        from agentcloak.cli.output import error

        error(
            "no cookie provided",
            "pass NAME VALUE (with optional --domain) or --curl '<string>'",
        )
        return
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/cookies/set",
        json_body=body,
        renderer=render_cookie_set_text,
    )


@app.command("clear")
def cookies_clear() -> None:
    """Remove all cookies from the browser context."""
    dispatch_text_or_json(
        DaemonClient(), "POST", "/cookies/clear", renderer=render_cookies_clear_text
    )


@app.command("delete")
def cookies_delete(
    name: str = typer.Argument(..., help="Cookie name to delete."),
    domain: str = typer.Option(
        "", "--domain", "-d", help="Restrict deletion to this domain."
    ),
) -> None:
    """Delete cookies matching a name (optionally scoped to a domain)."""
    body: dict[str, object] = {"name": name}
    if domain:
        body["domain"] = domain
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/cookies/delete",
        json_body=body,
        renderer=render_cookie_delete_text,
    )
