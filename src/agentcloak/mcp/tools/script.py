"""Script tool (7b T1.1) — inject/remove/list init scripts."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import (
    render_script_add_text,
    render_script_list_text,
    render_script_remove_text,
)
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_script(
        action: Literal["add", "remove", "list"] = "list",
        js: str = "",
        preset: str = "",
        identifier: str = "",
    ) -> str:
        """Inject JavaScript that runs before page scripts on every navigation.

        Init scripts are the standard hook point for reverse engineering — they
        run before any page code, so you can patch fetch/XHR/JSON.parse before
        the page uses them (unlike evaluate, which runs after load).

        Actions:
          add    — inject 'js' (or a 'preset' hook); returns an identifier
          remove — drop a script by 'identifier'
          list   — show active scripts (identifier + source preview)

        Presets: fetch, xhr, json_parse, crypto, timing — each logs the
        intercepted calls to the console (read with agentcloak_console).

        Args:
            action: 'add', 'remove', or 'list'
            js: Raw JavaScript to inject (for add)
            preset: Built-in hook preset name (for add; overrides js)
            identifier: Script identifier to remove (for remove)

        Returns:
            add: the script identifier (pass to remove).
            remove: confirmation.
            list: one 'identifier: source' per line.
        """
        if action == "add":
            return await format_call(
                client.script_add(js=js, preset=preset), render_script_add_text
            )
        if action == "remove":
            return await format_call(
                client.script_remove(identifier=identifier), render_script_remove_text
            )
        return await format_call(client.script_list(), render_script_list_text)
