"""Cross-route helpers used by the per-feature router modules.

Keeps the OkEnvelope wrapper, the resume-snapshot update logic, and the
``include_snapshot`` attachment in one place so every router file imports
the same implementation. Anything specific to a single route group lives
next to that group's handlers.
"""

from __future__ import annotations

from typing import Any

import structlog

__all__ = ["_attach_optional_snapshot", "_ok", "_update_resume", "logger"]

logger = structlog.get_logger()


def _ok(data: Any, *, seq: int) -> dict[str, Any]:
    """Wrap a payload in the success envelope shared with the OkEnvelope model."""
    return {"ok": True, "seq": seq, "data": data}


async def _update_resume(
    writer: Any,
    ctx: Any,
    *,
    action_summary: dict[str, Any] | None = None,
) -> None:
    """Mark resume snapshot dirty (non-blocking, background task flushes).

    ``writer`` is the :class:`ResumeWriter` injected via :class:`ResumeWriterDep`
    by the calling route. ``ctx`` exposes the live session data through
    :meth:`BrowserContextBase.resume_snapshot`, so this helper never has to
    introspect backend internals.
    """
    if writer is None:
        return

    snap: dict[str, Any]
    try:
        snap = await ctx.resume_snapshot()
    except Exception:
        logger.debug("resume_state_extraction_failed", exc_info=True)
        snap = {
            "url": "",
            "title": "",
            "tabs": [],
            "capture_active": ctx.capture_store.recording,
            "stealth_tier": ctx.stealth_tier.value,
        }

    writer.mark_dirty(
        url=str(snap.get("url", "")),
        title=str(snap.get("title", "")),
        tabs=list(snap.get("tabs", []) or []),
        action_summary=action_summary,
        capture_active=bool(snap.get("capture_active", False)),
        stealth_tier=str(snap.get("stealth_tier", "")),
    )


async def _attach_optional_snapshot(
    result: dict[str, Any],
    ctx: Any,
    *,
    snapshot_mode: str,
    snapshot_max_nodes: int,
) -> None:
    """Attach an inline snapshot to a navigate/action result.

    Default-cap matches ``/snapshot``: compact mode honours
    ``snapshot_max_nodes``; other modes opt into the full tree. Logged and
    swallowed on failure so a snapshot hiccup never breaks the underlying
    action's response.
    """
    # Local import to avoid a cycle: services depend on models, which depend
    # on this package via re-export.
    from agentcloak.daemon.services import SnapshotService

    try:
        attach_max = snapshot_max_nodes if snapshot_mode == "compact" else 0
        snap = await ctx.snapshot(mode=snapshot_mode, max_nodes=attach_max)
        SnapshotService.attach_snapshot_to_result(result, snap)
    except Exception:
        logger.debug("include_snapshot_failed", exc_info=True)
