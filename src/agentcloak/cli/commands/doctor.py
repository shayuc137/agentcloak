"""Self-check and diagnostics command."""

from __future__ import annotations

import sys
from typing import Any

import typer

from agentcloak.cli._dispatch import emit_envelope
from agentcloak.cli.output import is_json_mode, value
from agentcloak.core.config import load_config
from agentcloak.core.text_renderers import (
    render_doctor_detail_text,
    render_doctor_text,
)
from agentcloak.daemon.services import DiagnosticService

__all__ = ["app"]

app = typer.Typer()


def _probe_daemon_runtime(
    host: str, port: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Probe ``/health`` non-spawning and return ``(check, runtime)``.

    ``check`` is the legacy doctor row appended to the diagnostic report
    (``level=ok`` when up, ``level=info`` when down — daemon-down is *not* a
    failure because auto-start kicks in on the next real command).

    ``runtime`` carries the fields the new doctor renderer consumes for the
    status line. ``daemon_ok=False`` lets the renderer fall back to "daemon
    not running" instead of building a misleading summary from defaults.
    """
    from agentcloak.client import DaemonClient

    client = DaemonClient(host=host, port=port, auto_start=False)
    try:
        result = client.health_sync()
        if result.get("ok"):
            runtime: dict[str, Any] = {
                "daemon_ok": True,
                "browser_description": result.get("browser_description"),
                "headless": result.get("headless"),
                "humanize": result.get("humanize"),
                "proxy": result.get("proxy"),
                "active_profile": result.get("active_profile"),
            }
            check = {
                "name": "daemon",
                "ok": True,
                "level": "ok",
                "detail": f"{host}:{port}",
                "hint": "",
            }
            return check, runtime
    except Exception:
        pass
    check = {
        "name": "daemon",
        "ok": True,
        "level": "info",
        "detail": f"{host}:{port}",
        "hint": "not running (auto-starts on first command)",
    }
    return check, {"daemon_ok": False}


@app.callback(invoke_without_command=True)
def run_doctor(
    fix: bool = typer.Option(
        False,
        "--fix",
        help=(
            "Run in-process repairs (CloakBrowser binary download, data dir) "
            "and print a one-liner for the rest."
        ),
    ),
    sudo: bool = typer.Option(
        False,
        "--sudo",
        help=(
            "With --fix, execute the synthesised system command via sudo. "
            "Ignored when --fix is not set."
        ),
    ),
    detail: bool = typer.Option(
        False,
        "--detail",
        "-d",
        help=(
            "Show every check (legacy verbose output). Default mode prints a "
            "summary + runtime status; --detail prints one line per probe."
        ),
    ),
) -> None:
    """Run all diagnostic checks and report status."""
    paths, cfg = load_config()
    diagnostic = DiagnosticService()

    if fix:
        report = diagnostic.doctor_fix(data_dir=paths.root, execute_sudo=sudo)
    else:
        report = diagnostic.doctor(data_dir=paths.root)

    daemon_check, runtime = _probe_daemon_runtime(cfg.daemon_host, cfg.daemon_port)
    report["checks"].append(daemon_check)
    report["healthy"] = all(c["ok"] for c in report["checks"])
    # ``runtime`` is layered on for the renderer — it stays out of the
    # ``--json`` envelope unless the user passed ``--detail``-equivalent flags
    # (the runtime block doesn't belong in the legacy doctor schema, but JSON
    # consumers can still introspect it directly via ``cloak status``).
    report["runtime"] = runtime

    if is_json_mode():
        emit_envelope({"ok": True, "seq": 0, "data": report})
    elif detail:
        # Backward-compat path: print every check, same shape as pre-v0.3.x.
        # We intentionally skip the runtime status line here — ``--detail``
        # is for users debugging individual probes, not glanceable summary.
        value(render_doctor_detail_text(report))
    else:
        value(render_doctor_text(report))

    if fix and not report["healthy"] and not sudo:
        # Help text on stderr so the JSON envelope / text on stdout stays
        # parseable for scripts.
        cmd = report.get("fix", {}).get("command", "")
        if cmd:
            sys.stderr.write("\n--- Run this to finish fixing the environment ---\n")
            sys.stderr.write(f"{cmd}\n")
            sys.stderr.write("(or re-run with: agentcloak doctor --fix --sudo)\n\n")

    if not report["healthy"]:
        raise typer.Exit(1)
