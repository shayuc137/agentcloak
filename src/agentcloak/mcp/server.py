"""MCP server — stdio bridge to agentcloak daemon."""

from __future__ import annotations

import atexit
import logging
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


def create_server() -> object:
    """Create and configure the FastMCP server with all tools."""
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient
    from agentcloak.mcp.tools import (
        bridge,
        capture,
        clipboard,
        console,
        content,
        dialog,
        download,
        frame,
        interaction,
        management,
        navigation,
        network,
        pdf,
        serve,
        storage,
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
    # so reusing one prevents redundant subprocess spawns across tools.
    client = DaemonClient()

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

    return mcp


def _register_exit_hook() -> None:
    """Stop daemon on MCP server exit if configured."""
    from agentcloak.core.config import load_config

    _, cfg = load_config()
    if not cfg.browser.stop_on_exit:
        return

    def _stop() -> None:
        import httpx

        base = f"http://{cfg.daemon.host}:{cfg.daemon.port}"
        import contextlib

        with contextlib.suppress(Exception):
            httpx.post(f"{base}/shutdown", timeout=2.0)

    atexit.register(_stop)


def main() -> None:
    """Entry point for agentcloak-mcp and python -m agentcloak.mcp."""
    _configure_logging()
    _emit_environment_precheck()
    _register_exit_hook()
    mcp = create_server()
    mcp.run()  # type: ignore[union-attr]
