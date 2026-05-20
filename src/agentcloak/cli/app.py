"""Root Typer app, global flags, and structlog setup."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import structlog
import typer

from agentcloak import __version__
from agentcloak.cli.output import (
    _detect_env_json_mode,
    is_json_mode,
    set_json_mode,
    set_pretty,
    value,
)
from agentcloak.core.errors import AgentBrowserError

__all__ = ["app", "main"]

# The shortcut commands registered below (``cloak navigate``, ``cloak click``,
# etc.) are intentionally hidden so the Commands panel stays scannable. To
# avoid them being invisible, the epilog spells them out — agents and humans
# scrolling the help find both the short forms and the full ``<group> --help``
# path to deeper command trees.
_SHORTCUTS = (
    "navigate, snapshot, screenshot, resume, click, fill, type, "
    "press, scroll, hover, select, keydown, keyup"
)
_GROUPS = (
    "browser, do, js, tab, profile, spell, capture, frame, daemon, doctor, "
    "launch, network, fetch, bridge, cookies, skill, cdp, dialog, wait, "
    "upload, config, console, download, storage, clipboard, pdf, serve, "
    "script, route, emulation, graphql, ws, sse"
)
_EPILOG = (
    f"Shortcuts (top-level, also documented under their groups):\n  {_SHORTCUTS}\n"
    f"\nRun 'cloak <group> --help' to explore deeper trees:\n  {_GROUPS}"
)

app = typer.Typer(
    name="agentcloak",
    help="Browser CLI toolchain for AI agents.",
    no_args_is_help=True,
    add_completion=False,
    epilog=_EPILOG,
)


def _maybe_emit_first_run_banner() -> None:
    """Nudge new users toward ``doctor`` on the very first invocation.

    The data directory (``~/.agentcloak``) is created on first daemon launch.
    Its absence is therefore a reliable "this is run #1" signal — we don't
    want to add a separate state file just for the banner, and we can't gate
    on the daemon being up because the user might be running ``--version`` or
    ``--help``. The banner only prints to stderr (stdout stays a clean JSON
    envelope for scripts) and never blocks execution.

    Suppress with ``AGENTCLOAK_SKIP_FIRST_RUN_BANNER=1`` for CI / scripted
    environments.
    """
    if os.environ.get("AGENTCLOAK_SKIP_FIRST_RUN_BANNER", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        return
    data_dir = Path.home() / ".agentcloak"
    if data_dir.exists():
        return
    sys.stderr.write(
        "agentcloak: first-run detected — verify your environment with "
        "'agentcloak doctor --fix' (one-time; suppress with "
        "AGENTCLOAK_SKIP_FIRST_RUN_BANNER=1).\n"
    )


def _configure_logging(*, verbosity: int) -> None:
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"agentcloak {__version__}")
        raise typer.Exit


def _extract_global_flags(argv: list[str]) -> tuple[list[str], dict[str, object]]:
    """Strip global flags from ``argv`` and return ``(cleaned, state)``.

    The recognised globals are ``--pretty``, ``--verbose`` / ``-v`` (counted),
    ``--json``, and ``--version``. Adding a new one means appending a branch
    here *and* declaring it on :func:`_root_callback` so ``--help`` documents
    it — keep the two in sync.

    Click parses options per-command — a flag declared on the root callback is
    invisible to subcommand parsers, so ``agentcloak doctor --pretty`` fails
    with "No such option: --pretty". Rather than duplicating ``--pretty`` on
    every subcommand (and every nested group), we lift these globals out of
    ``argv`` before Typer ever sees it, apply their effects up-front, and pass
    the cleaned argument list down. The root callback still declares them so
    ``agentcloak --help`` documents them, but they never need to reach Click.

    ``--version`` is honoured here too because it has to short-circuit
    execution; if we let Typer handle it from the root callback, it would only
    fire when placed before the subcommand.
    """
    cleaned: list[str] = []
    pretty = False
    verbose = 0
    version = False
    json_mode = False
    for arg in argv:
        if arg == "--pretty":
            pretty = True
        elif arg in ("--verbose", "-v"):
            verbose += 1
        elif arg == "--version":
            version = True
        elif arg == "--json":
            json_mode = True
        else:
            cleaned.append(arg)
    state: dict[str, object] = {
        "pretty": pretty,
        "verbose": verbose,
        "version": version,
        "json": json_mode,
    }
    return cleaned, state


@app.callback()
def _root_callback(  # pyright: ignore[reportUnusedFunction]
    verbose: int = typer.Option(
        0, "--verbose", "-v", count=True, help="Increase log verbosity."
    ),
    pretty: bool = typer.Option(
        False, "--pretty", help="Pretty-print JSON output (requires --json)."
    ),
    json: bool = typer.Option(
        False,
        "--json",
        help=(
            "Emit full JSON envelopes on stdout (backwards-compat mode). "
            "Equivalent to AGENTCLOAK_OUTPUT=json."
        ),
    ),
    _version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version.",
    ),
) -> None:
    # ``main()`` strips these globals from argv via ``_extract_global_flags``
    # before Typer dispatches, so during normal CLI use the parameters arrive
    # here as their defaults (False) — the flag has already been consumed and
    # applied to the module-level state.
    #
    # For ``CliRunner`` invocations (tests, programmatic use) ``main()`` never
    # runs and Typer parses ``--json`` itself. We OR-merge so a True coming
    # from either path wins; we never *clear* an already-enabled flag because
    # ``main()`` may have set it from argv or AGENTCLOAK_OUTPUT before us.
    if json or _detect_env_json_mode():
        set_json_mode(enabled=True)
    if pretty:
        set_pretty(enabled=True)


def _register_commands() -> None:
    from agentcloak.cli.commands import (
        action,
        bridge_cmd,
        browser,
        capture_cmd,
        cdp,
        clipboard_cmd,
        config_cmd,
        console_cmd,
        cookies_cmd,
        daemon_cmd,
        dialog,
        doctor,
        download_cmd,
        emulation,
        fetch,
        frame,
        graphql,
        js,
        launch,
        network,
        pdf_cmd,
        profile,
        route,
        script,
        serve_cmd,
        skill_cmd,
        spell_cmd,
        storage_cmd,
        streaming,
        tab,
        upload,
        wait_cmd,
    )

    app.add_typer(
        config_cmd.app,
        name="config",
        help="Read/write configuration (get/set/unset/list/add/remove).",
    )
    app.add_typer(doctor.app, name="doctor", help="Self-check and diagnostics.")
    app.add_typer(daemon_cmd.app, name="daemon", help="Daemon lifecycle management.")
    app.add_typer(
        launch.app,
        name="launch",
        help="Hot-switch the daemon's active browser tier.",
    )
    app.add_typer(
        browser.app, name="browser", help="Browser navigation and inspection."
    )
    app.add_typer(js.app, name="js", help="JavaScript execution.")
    app.add_typer(network.app, name="network", help="Network request monitoring.")
    app.add_typer(
        action.app,
        name="do",
        help="Page actions: click, fill, type, scroll, hover, "
        "select, press, keydown, keyup.",
    )
    app.add_typer(
        profile.app,
        name="profile",
        help="Browser profile management: create, list, delete, launch.",
    )
    app.add_typer(
        fetch.app,
        name="fetch",
        help="HTTP fetch with browser cookies.",
    )
    app.add_typer(
        bridge_cmd.app,
        name="bridge",
        help="Remote bridge: connect Chrome extension to daemon.",
    )
    app.add_typer(
        cookies_cmd.app,
        name="cookies",
        help="Cookie management: export from remote Chrome.",
    )
    app.add_typer(
        capture_cmd.app,
        name="capture",
        help="Network traffic capture: record, export, analyze.",
    )
    app.add_typer(
        skill_cmd.app,
        name="skill",
        help="Skill bundle: install, update, uninstall to agent platforms.",
    )
    app.add_typer(
        spell_cmd.app,
        name="spell",
        help="Spells: list, info, run, scaffold.",
    )
    app.add_typer(
        tab.app,
        name="tab",
        help="Tab management: list, new, close, switch.",
    )
    app.add_typer(
        cdp.app,
        name="cdp",
        help="Chrome DevTools Protocol: endpoint.",
    )
    app.add_typer(
        dialog.app,
        name="dialog",
        help="Dialog handling: status, accept, dismiss.",
    )
    app.add_typer(
        wait_cmd.app,
        name="wait",
        help="Conditional waiting: selector, URL, load state, JS, time.",
    )
    app.add_typer(
        upload.app,
        name="upload",
        help="File upload to input elements.",
    )
    app.add_typer(
        frame.app,
        name="frame",
        help="Frame switching: list, focus.",
    )
    app.add_typer(
        console_cmd.app,
        name="console",
        help="Console logs: show captured messages and page errors.",
    )
    app.add_typer(
        download_cmd.app,
        name="download",
        help="Downloads: direct-URL fetch, wait for click-triggered, list.",
    )
    app.add_typer(
        storage_cmd.app,
        name="storage",
        help="Web storage: get/set/delete/clear localStorage and sessionStorage.",
    )
    app.add_typer(
        clipboard_cmd.app,
        name="clipboard",
        help="Clipboard: read and write the system clipboard.",
    )
    app.add_typer(
        pdf_cmd.app,
        name="pdf",
        help="Export the current page to a PDF file.",
    )
    app.add_typer(
        serve_cmd.app,
        name="serve",
        help="Local static file server for previewing local files over http.",
    )
    app.add_typer(
        script.app,
        name="script",
        help="Init scripts: inject/remove/list pre-page-load JS hooks.",
    )
    app.add_typer(
        route.app,
        name="route",
        help="Network route interception: abort/fulfill/continue requests.",
    )
    app.add_typer(
        emulation.app,
        name="emulation",
        help="Emulation: inject extra HTTP headers on every request.",
    )
    app.add_typer(
        graphql.app,
        name="graphql",
        help="GraphQL: introspect a schema or send a query.",
    )
    app.add_typer(
        streaming.ws_app,
        name="ws",
        help="WebSocket capture: list connections, read frames.",
    )
    app.add_typer(
        streaming.sse_app,
        name="sse",
        help="Server-Sent Events capture: read buffered events.",
    )


_register_commands()


def _register_shortcuts() -> None:
    """Top-level shortcut commands (cloak open, cloak snapshot, cloak click, etc.)."""
    from agentcloak.cli.commands.action import (
        do_click,
        do_fill,
        do_hover,
        do_keydown,
        do_keyup,
        do_press,
        do_scroll,
        do_select,
        do_type,
    )
    from agentcloak.cli.commands.browser import (
        browser_navigate,
        browser_resume,
        browser_screenshot,
        browser_snapshot,
    )

    app.command("navigate", hidden=True)(browser_navigate)
    app.command("snapshot", hidden=True)(browser_snapshot)
    app.command("screenshot", hidden=True)(browser_screenshot)
    app.command("resume", hidden=True)(browser_resume)
    app.command("click", hidden=True)(do_click)
    app.command("fill", hidden=True)(do_fill)
    app.command("type", hidden=True)(do_type)
    app.command("press", hidden=True)(do_press)
    app.command("scroll", hidden=True)(do_scroll)
    app.command("hover", hidden=True)(do_hover)
    app.command("select", hidden=True)(do_select)
    app.command("keydown", hidden=True)(do_keydown)
    app.command("keyup", hidden=True)(do_keyup)


_register_shortcuts()


@app.command("version")
def show_version() -> None:
    """Show agentcloak version."""
    # Mirrors the ``--version`` flag but as a discoverable subcommand so
    # ``cloak version | head`` and ``cloak version`` style probes work the
    # same as every other Unix CLI. Text mode emits the bare version string
    # (pipe-friendly — agents and version-bump scripts can read it
    # directly, no ``agentcloak `` prefix). JSON mode wraps it in the
    # standard envelope so script callers using ``--json`` see the same
    # shape as every other command.
    if is_json_mode():
        from agentcloak.cli._dispatch import emit_envelope

        emit_envelope({"ok": True, "seq": 0, "data": {"version": __version__}})
        return
    value(__version__)


def main() -> None:
    from agentcloak.cli.output import error_from_exception

    _maybe_emit_first_run_banner()
    # Lift ``--pretty``/``--verbose``/``--version``/``--json`` out of argv
    # before Typer sees it (see :func:`_extract_global_flags`). This is what
    # makes these flags work in any position — including after a subcommand
    # name.
    cleaned_argv, state = _extract_global_flags(sys.argv[1:])
    if state["version"]:
        typer.echo(f"agentcloak {__version__}")
        return
    verbose = state["verbose"]
    pretty = state["pretty"]
    json_flag = bool(state["json"])
    # AGENTCLOAK_OUTPUT=json is the escape hatch for scripts that can't pass
    # CLI flags (cron jobs, CI tools that wrap the binary, etc.). Either path
    # sets the same module-level flag.
    json_enabled = json_flag or _detect_env_json_mode()
    set_json_mode(enabled=json_enabled)
    set_pretty(enabled=bool(pretty))
    _configure_logging(verbosity=int(verbose))  # type: ignore[arg-type]

    # ``--pretty`` without ``--json`` is a no-op (text output isn't JSON).
    # Warn so the user doesn't think their formatting is broken.
    if bool(pretty) and not json_enabled:
        sys.stderr.write(
            "warning: --pretty has no effect without --json (text output mode)\n"
        )

    try:
        app(args=cleaned_argv)
    except AgentBrowserError as exc:
        # In JSON mode the envelope was serialised to stdout. In text mode we
        # emit ``Error: <hint>`` to stderr. Either way the call exits with 1.
        # Using ``sys.exit`` rather than ``raise typer.Exit from exc`` keeps
        # Python from dumping the exception chain — agents already have the
        # structured info they need and a traceback would burn ~800 tokens.
        # ``error_from_exception`` will raise SystemExit(1) itself; the catch
        # below just guards against the rare path where it doesn't.
        try:
            error_from_exception(exc)
        except SystemExit:
            raise
        # Reached only if error_from_exception returns normally (it shouldn't).
        sys.exit(1)
    # Surface the post-call json mode to anyone calling main() in-process so
    # they see the flag was honoured (kept silent under normal CLI use).
    _ = is_json_mode()
