"""Storage tool — localStorage / sessionStorage CRUD (7a R4)."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import render_storage_text
from agentcloak.mcp._format import error_json, format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_storage(
        action: Literal["get", "set", "delete", "clear"] = "get",
        type: Literal["local", "session"] = "local",
        key: str = "",
        value: str = "",
    ) -> str:
        """Read or write the page's localStorage / sessionStorage.

        Actions:
          get    — read one key (pass 'key') or dump the whole area (omit 'key')
          set    — write 'key'='value'
          delete — remove a single 'key'
          clear  — empty the entire storage area

        Args:
            action: 'get', 'set', 'delete', or 'clear'
            type: Storage area — 'local' (persistent) or 'session' (per-tab)
            key: Storage key (required for set/delete; optional for get)
            value: Value to store (required for set)

        Returns:
            get: bare value (single key), 'key=value' lines (full dump), or
                empty when the key is missing.
            set/delete/clear: a short confirmation.
        """
        from agentcloak.core.errors import AgentBrowserError

        try:
            if action == "set":
                if not key:
                    raise AgentBrowserError(
                        error="missing_key",
                        hint="key is required for set",
                        action="pass a key parameter",
                    )
                coro = client.storage_set(type=type, key=key, value=value)
            elif action == "delete":
                if not key:
                    raise AgentBrowserError(
                        error="missing_key",
                        hint="key is required for delete",
                        action="pass a key parameter",
                    )
                coro = client.storage_delete(type=type, key=key)
            elif action == "clear":
                coro = client.storage_clear(type=type)
            else:
                coro = client.storage_get(type=type, key=key or None)
        except AgentBrowserError as exc:
            return error_json(exc)
        return await format_call(coro, render_storage_text)
