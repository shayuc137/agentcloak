"""Interaction tool — unified page actions."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import render_action_text
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]

ActionKind = Literal[
    "click",
    "fill",
    "type",
    "scroll",
    "hover",
    "drag",
    "select",
    "press",
    "keydown",
    "keyup",
]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(destructiveHint=False, readOnlyHint=False))
    async def agentcloak_action(
        kind: ActionKind,
        target: str = "",
        text: str = "",
        key: str = "",
        value: str = "",
        direction: str = "down",
        include_snapshot: bool = False,
        at: str | None = None,
        offset: str | None = None,
        destination: str | None = None,
        from_point: str | None = None,
        to_point: str | None = None,
        steps: int = 20,
    ) -> str:
        """Interact with the page. Use [N] refs from agentcloak_snapshot as target.

        Actions:
          click   — click element [N]
          fill    — clear input [N] and set text (use 'text' param)
          type    — type into [N] character by character (use 'text' param)
          scroll  — scroll page (use 'direction': up/down)
          hover   — hover over element [N], at x,y, or offset dx,dy from its center
          drag    — drag target to destination refs, or from_point/to_point x,y
          select  — pick dropdown option [N] (use 'value' param)
          press   — press keyboard key (use 'key': Enter/Tab/Control+a)
          keydown — hold a key down (use 'key': Shift/Control/Alt)
          keyup   — release a held key (use 'key')

        Returns include proactive state feedback:
          pending_requests — count of in-flight network requests (if > 0)
          dialog — pending dialog info (if a dialog appeared)
          navigation — new URL if page navigated
          current_value — current value after fill/select

        If the target [N] ref is stale (element_not_found), the daemon
        automatically re-snapshots and retries once. The result will
        include retried=true when this happens.

        If a dialog is blocking, returns error='blocked_by_dialog'.
        Handle it with agentcloak_dialog before retrying.

        Args:
            kind: Action type
            target: Element [N] ref from snapshot (empty for scroll/press/key*)
            text: Text for fill/type actions
            key: Key name for press/keydown/keyup (e.g. 'Enter', 'Control+a', 'Shift')
            value: Option value for select action
            direction: Scroll direction — 'up' or 'down'
            at: Hover absolute x,y coordinates, without target
            offset: Hover dx,dy from target element center
            destination: Drag destination N or '[N]', paired with target
            from_point: Drag source x,y, without target/destination refs
            to_point: Drag destination x,y, paired with from_point
            steps: Drag mouse movement steps, 1 to 1000
            include_snapshot: If true, attach a compact snapshot to the
                action result. Saves a round-trip when you need to see
                the page state after an action.

        Returns:
            JSON with action result, seq number, and state feedback fields.
            When include_snapshot=true, includes a 'snapshot' object with
            tree_text, mode, total_nodes, and total_interactive.
        """
        extras: dict[str, Any] = {}
        if kind == "hover":
            if at is not None:
                extras["at"] = at
            if offset is not None:
                extras["offset"] = offset
        if kind == "drag":
            extras.update(
                destination=destination,
                from_point=from_point,
                to_point=to_point,
                steps=steps,
            )
        if kind in ("fill", "type") and text:
            extras["text"] = text
        if kind in ("press", "keydown", "keyup") and key:
            extras["key"] = key
        if kind == "select" and value:
            extras["value"] = value
        if kind == "scroll":
            extras["direction"] = direction

        # ``render_action_text`` needs the action kind/target plus the
        # ``text``/``key`` extras we supplied to format ``filled [3] | value:
        # 'hello'`` / ``press Enter``. Close over them so the daemon JSON
        # payload (which no longer carries that context) can be rendered
        # byte-identically to the CLI text output.
        def render(data: dict[str, Any]) -> str:
            enriched = dict(data)
            if "text" not in enriched and "text" in extras:
                enriched["text"] = extras["text"]
            if "key" not in enriched and "key" in extras:
                enriched["key"] = extras["key"]
            return render_action_text(kind, target, enriched)

        return await format_call(
            client.action(
                kind,
                target=target,
                include_snapshot=include_snapshot,
                **extras,
            ),
            render,
        )
