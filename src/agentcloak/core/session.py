"""Stable caller identity within a workspace."""

from __future__ import annotations

import os
from contextvars import ContextVar

from agentcloak.core.workspace import resolve_workspace

__all__ = ["DEFAULT_SESSION_ID", "auto_detect_session_id", "cli_session_id"]

DEFAULT_SESSION_ID = "default"
cli_session_id: ContextVar[str | None] = ContextVar("cli_session_id", default=None)


def auto_detect_session_id(*, session_scope: str | None = None) -> str:
    explicit = cli_session_id.get() or os.environ.get("AGENTCLOAK_SESSION", "").strip()
    return explicit or "session-" + (session_scope or resolve_workspace().session_scope)
