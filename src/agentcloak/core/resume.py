"""Resume snapshot — daemon auto-maintained session state for agent recovery."""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import orjson
import structlog

if TYPE_CHECKING:
    from agentcloak.core.config import Paths

__all__ = ["ResumeSnapshot", "ResumeWriter"]

logger = structlog.get_logger()

_MAX_RECENT_ACTIONS = 5
_FLUSH_INTERVAL = 2.0


@dataclass
class ResumeSnapshot:
    """Current session state for agent recovery."""

    url: str = ""
    title: str = ""
    tabs: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    recent_actions: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    capture_active: bool = False
    stealth_tier: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "tabs": self.tabs,
            "recent_actions": self.recent_actions,
            "capture_active": self.capture_active,
            "stealth_tier": self.stealth_tier,
            "timestamp": self.timestamp,
        }


class ResumeWriter:
    """Debounced writer for resume.json — marks dirty, flushes periodically."""

    def __init__(self, paths: Paths | None = None) -> None:
        self._paths = paths
        self._dirty = False
        self._snapshot = ResumeSnapshot()
        self._recent_actions: deque[dict[str, Any]] = deque(maxlen=_MAX_RECENT_ACTIONS)
        self._task: asyncio.Task[None] | None = None

    def mark_dirty(
        self,
        *,
        url: str = "",
        title: str = "",
        tabs: list[dict[str, Any]] | None = None,
        action_summary: dict[str, Any] | None = None,
        capture_active: bool = False,
        stealth_tier: str = "",
    ) -> None:
        """Update snapshot data and mark for next flush."""
        if action_summary is not None:
            self._recent_actions.append(action_summary)

        self._snapshot = ResumeSnapshot(
            url=url,
            title=title,
            tabs=tabs or [],
            recent_actions=list(self._recent_actions),
            capture_active=capture_active,
            stealth_tier=stealth_tier,
            timestamp=datetime.now(UTC).isoformat(),
        )
        self._dirty = True

    def flush(self) -> None:
        """Write snapshot to disk if dirty."""
        if not self._dirty or self._paths is None:
            return
        try:
            self._paths.ensure_dirs()
            self._paths.resume_file.write_bytes(orjson.dumps(self._snapshot.to_dict()))
            self._dirty = False
        except Exception:
            logger.warning("resume_flush_failed", path=str(self._paths.resume_file))

    def start_background(self) -> asyncio.Task[None]:
        """Start periodic flush background task."""
        self._task = asyncio.ensure_future(self._periodic_flush())
        return self._task

    async def _periodic_flush(self) -> None:
        """Flush every _FLUSH_INTERVAL seconds until cancelled."""
        try:
            while True:
                await asyncio.sleep(_FLUSH_INTERVAL)
                self.flush()
        except asyncio.CancelledError:
            self.flush()

    def clear(self) -> None:
        """Cancel background task and remove resume.json from disk."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self._paths is not None:
            with contextlib.suppress(FileNotFoundError):
                self._paths.resume_file.unlink()
        self._dirty = False

    @property
    def current_snapshot(self) -> ResumeSnapshot:
        return self._snapshot
