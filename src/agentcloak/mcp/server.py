"""MCP server — stdio bridge to agentcloak daemon."""

from __future__ import annotations

import atexit
import logging
import os
import sys

__all__ = ["create_server", "main"]


def _configure_logging() -> None:
    from agentcloak.core.config import load_config

    _, cfg = load_config()
    level = getattr(logging, cfg.daemon.log_level.upper(), logging.WARNING)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )


def _emit_environment_precheck() -> None:
    """Surface obvious environment problems on MCP startup.

    The first tool call from an MCP client will trigger daemon auto-start.
    If the CloakBrowser binary is missing we'd download ~200MB at that
    moment, which freezes the agent and times out most clients. Printing a
    warning to stderr up front lets the user pre-install or kick off
    ``doctor --fix`` before the first navigate.

    All output goes to stderr — MCP servers reserve stdout for the JSON-RPC
    transport, so anything we'd send to stdout would corrupt the protocol.
    """
    try:
        import cloakbrowser  # pyright: ignore[reportMissingImports,reportMissingTypeStubs]

        info: dict[str, object] = cloakbrowser.binary_info()  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        if not info.get("installed"):
            sys.stderr.write(
                "[agentcloak-mcp] CloakBrowser binary not found. The first "
                "tool call will trigger a ~200MB download and may exceed "
                "client timeouts.\n"
            )
            sys.stderr.write(
                "[agentcloak-mcp] Pre-install with: agentcloak doctor --fix "
                "(or: uvx agentcloak doctor --fix)\n"
            )
    except ImportError:
        sys.stderr.write(
            "[agentcloak-mcp] CloakBrowser package missing — tools will fail. "
            "Reinstall with: pip install agentcloak\n"
        )
    except Exception as exc:
        # Defensive — binary_info changed shape once already during CloakBrowser
        # development. We don't want a probe failure to block MCP startup.
        sys.stderr.write(
            f"[agentcloak-mcp] Environment precheck skipped ({exc!r}). "
            "Run 'agentcloak doctor' to verify the install.\n"
        )


def _mcp_session_id() -> str:
    """Stable per-process session id for this MCP server.

    Tagged with the OS pid so it stays constant for the server's lifetime yet
    is unique across concurrent MCP processes. Crucially this overrides the
    ambient ``CLAUDE_CODE_SESSION_ID`` auto-detection: when the MCP server runs
    inside a Claude Code session we still want its own isolated browser rather
    than sharing the one the agent's CLI calls land on.
    """
    return f"mcp-{os.getpid()}"


def create_server(session_id: str | None = None) -> object:
    """Create and configure the FastMCP server with all tools.

    ``session_id`` is the ``X-Agentcloak-Session`` value every tool's daemon
    request carries, isolating this server's browser. Defaults to
    :func:`_mcp_session_id` when not supplied (the normal entry-point path
    passes the same id it hands the atexit hook so both agree).
    """
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient
    from agentcloak.mcp.tools import (
        bridge,
        capture,
        clipboard,
        console,
        content,
        debugger,
        dialog,
        download,
        emulation,
        frame,
        graphql,
        interaction,
        management,
        navigation,
        network,
        pdf,
        performance,
        profiler,
        route,
        script,
        serve,
        sourcemap,
        storage,
        streaming,
        upload,
        wait,
    )

    mcp = FastMCP(
        "agentcloak",
        instructions=(
            "agentcloak provides browser automation for AI agents. "
            "Core workflow: agentcloak_navigate → agentcloak_snapshot → "
            "agentcloak_action. The snapshot shows an accessibility tree "
            "with [N] element references — pass those numbers as 'target' "
            "to agentcloak_action. The daemon auto-starts on first use "
            "with CloakBrowser (default stealth backend). "
            "Use agentcloak_launch to explicitly "
            "set tier or profile. For jshookmcp coordination: use "
            "agentcloak_status(query='cdp_endpoint') to get the "
            "WebSocket URL, then call jshookmcp's browser_attach."
        ),
    )

    # Single shared client instance — auto-start state lives on this object,
    # so reusing one prevents redundant subprocess spawns across tools. The
    # explicit session id gives this MCP server its own browser, independent
    # of any other client talking to the same daemon.
    client = DaemonClient(session_id=session_id or _mcp_session_id())

    navigation.register(mcp, client)
    interaction.register(mcp, client)
    content.register(mcp, client)
    network.register(mcp, client)
    capture.register(mcp, client)
    management.register(mcp, client)
    dialog.register(mcp, client)
    wait.register(mcp, client)
    upload.register(mcp, client)
    frame.register(mcp, client)
    bridge.register(mcp, client)
    # 7a R1-R7 capabilities.
    console.register(mcp, client)
    download.register(mcp, client)
    storage.register(mcp, client)
    clipboard.register(mcp, client)
    pdf.register(mcp, client)
    serve.register(mcp, client)
    # 7b T1/T2/T3 reverse-engineering capabilities.
    script.register(mcp, client)
    route.register(mcp, client)
    emulation.register(mcp, client)
    graphql.register(mcp, client)
    streaming.register(mcp, client)
    debugger.register(mcp, client)
    sourcemap.register(mcp, client)
    # 7f profiling / reverse-engineering aids.
    profiler.register(mcp, client)
    performance.register(mcp, client)

    return mcp


def _register_exit_hook(session_id: str) -> None:
    """Release this MCP server's resources when the process exits.

    Two best-effort calls fire on exit, both swallowing every error (the
    interpreter is tearing down — a failed cleanup must never raise):

    * ``POST /session/close`` for *this server's* session — frees the
      per-session browser promptly instead of waiting on the daemon's idle
      timeout. Always attempted, because a named session left around just
      wastes ~300MB until reclamation. Carries the session header so the
      daemon closes the right slot.
    * ``POST /shutdown`` to stop the whole daemon, but only when
      ``stop_on_exit`` is set. In multi-session mode other clients may share
      this daemon, so tearing it down is opt-in; the default leaves it running
      for them.
    """
    import contextlib

    from agentcloak.core.config import load_config

    _, cfg = load_config()
    base = f"http://{cfg.daemon.host}:{cfg.daemon.port}"

    def _stop() -> None:
        import httpx

        with contextlib.suppress(Exception):
            httpx.post(
                f"{base}/session/close",
                json={"session_id": session_id},
                headers={"X-Agentcloak-Session": session_id},
                timeout=2.0,
            )
        if cfg.browser.stop_on_exit:
            with contextlib.suppress(Exception):
                httpx.post(f"{base}/shutdown", timeout=2.0)

    atexit.register(_stop)


def main() -> None:
    """Entry point for agentcloak-mcp and python -m agentcloak.mcp."""
    _configure_logging()
    _emit_environment_precheck()
    # One session id for this process — shared so the exit hook closes exactly
    # the session the tools used.
    session_id = _mcp_session_id()
    _register_exit_hook(session_id)
    mcp = create_server(session_id)
    mcp.run()  # type: ignore[union-attr]
