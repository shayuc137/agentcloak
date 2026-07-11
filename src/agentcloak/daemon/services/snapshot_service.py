"""SnapshotService — diff + response shaping for the ``/snapshot`` route.

The browser layer's :class:`PageSnapshot` already covers tree building,
truncation, focus, and offset. This service exists to:

1. apply the ``--diff`` overlay against the previously cached snapshot lines
2. shape the route response (``tree_size``, ``truncated`` flags, etc.)
3. own the per-request cache slot (``prev_snapshot_lines``) so route handlers
   don't poke ``app.state`` directly.

Design note — ``include_selector_map``:
    CLI / MCP both omit ``selector_map`` by default (R7 of the v0.3.0 CLI
    redesign): agents drive the page from ``[N]`` references in ``tree_text``
    and the structured map would burn 1-3kB of tokens per snapshot on
    metadata they never read. CLI callers can opt back in with the
    ``--selector-map`` flag when scripting non-trivial flows; MCP tools never
    set it. Both surfaces hit the same route, the difference is purely about
    who's reading the response.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from agentcloak.browser._snapshot_builder import (
    count_diff,
    diff_snapshots,
    render_diff_tree,
    truncate_diff_lines,
)

if TYPE_CHECKING:
    from agentcloak.browser.state import PageSnapshot

__all__ = ["SnapshotService"]


class SnapshotService:
    """Stateless helper that turns a :class:`PageSnapshot` into a route payload."""

    async def get(
        self,
        ctx: Any,
        *,
        mode: str = "compact",
        max_nodes: int = 0,
        max_chars: int = 0,
        focus: int = 0,
        offset: int = 0,
        include_selector_map: bool = True,
        frames: bool = False,
        selector: str = "",
        diff: bool = False,
        prev_cached_lines: list[tuple[int, str, int | None]] | None = None,
    ) -> tuple[dict[str, Any], list[tuple[int, str, int | None]] | None]:
        """Fetch a snapshot and shape it for the route response.

        Returns ``(payload, current_cached_lines)``. The caller stores
        ``current_cached_lines`` so the next call can diff against it.
        """
        snap = await ctx.snapshot(
            mode=mode,
            max_nodes=max_nodes,
            max_chars=max_chars,
            focus=focus,
            offset=offset,
            frames=frames,
            selector=selector,
        )

        cur_cache = getattr(ctx, "_cached_lines", None)

        diff_applied = False
        diff_counts_payload: dict[str, int] | None = None
        if diff and prev_cached_lines is not None and cur_cache is not None:
            diff_lines = diff_snapshots(prev_cached_lines, cur_cache)
            # Count before truncation so totals stay accurate even when the
            # tree was clipped by max_nodes.
            counts = count_diff(diff_lines)
            # build_snapshot already truncated snap.tree_text, but the diff
            # overlay is rendered from the full cached lines and would blow
            # past that limit. Re-apply the same node-level cap here so
            # `--diff --max-nodes N` actually honours N.
            truncated_diff, diff_truncated_at = truncate_diff_lines(
                diff_lines,
                max_nodes=max_nodes,
                offset=offset,
            )
            new_truncated_at = (
                diff_truncated_at if diff_truncated_at else snap.truncated_at
            )
            snap = replace(
                snap,
                tree_text=render_diff_tree(truncated_diff),
                truncated_at=new_truncated_at,
            )
            diff_applied = True
            diff_counts_payload = {
                "added": counts.added,
                "changed": counts.changed,
                "removed": counts.removed,
            }

        data: dict[str, Any] = {
            "url": snap.url,
            "title": snap.title,
            "mode": snap.mode,
            "tree_text": snap.tree_text,
            "tree_size": len(snap.tree_text),
            "truncated": snap.truncated_at > 0,
            "total_nodes": snap.total_nodes,
            "total_interactive": snap.total_interactive,
        }
        if diff:
            data["diff"] = diff_applied
            if diff_counts_payload is not None:
                data["diff_counts"] = diff_counts_payload
        if snap.truncated_at > 0:
            data["truncated_at"] = snap.truncated_at
        if include_selector_map:
            data["selector_map"] = self._serialize_selector_map(snap)
        if snap.security_warnings:
            data["security_warnings"] = snap.security_warnings

        return data, cur_cache

    @staticmethod
    def _serialize_selector_map(snap: PageSnapshot) -> dict[str, dict[str, Any]]:
        return {
            str(k): {
                "index": v.index,
                "tag": v.tag,
                "role": v.role,
                "text": v.text,
                "attributes": v.attributes,
            }
            for k, v in snap.selector_map.items()
        }

    @staticmethod
    def attach_snapshot_to_result(result: dict[str, Any], snap: PageSnapshot) -> None:
        """Attach a compact snapshot payload to an action/navigate response.

        Includes ``url``/``title``/``seq``/``truncated_at`` so the text
        renderer can build the same ``# title | url | N nodes ...`` header
        line that the standalone ``snapshot`` command produces — keeps
        ``--snap`` output parseable by the same downstream tooling.
        """
        result["snapshot"] = {
            "url": snap.url,
            "title": snap.title,
            "seq": snap.seq,
            "tree_text": snap.tree_text,
            "mode": snap.mode,
            "total_nodes": snap.total_nodes,
            "total_interactive": snap.total_interactive,
            "truncated_at": snap.truncated_at,
        }
