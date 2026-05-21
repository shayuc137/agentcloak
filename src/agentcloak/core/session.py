"""Session identity detection for multi-session daemon routing.

A single daemon serves many callers (Claude Code sessions, MCP clients,
CLI invocations). Each carries a session id in the ``X-Agentcloak-Session``
HTTP header so the daemon can hand it an isolated browser. This module
decides what that id is, with zero configuration in the common case.

Detection precedence (first non-empty wins):

1. ``AGENTCLOAK_SESSION`` — explicit override, highest priority.
2. ``CLAUDE_CODE_SESSION_ID`` — Claude Code propagates a per-session UUID
   into the environment of tools it spawns, so two concurrent Claude Code
   sessions automatically get distinct browsers with no user action.
3. ``"default"`` — the shared session backed by the daemon's primary
   browser (``app.state.browser_ctx``). Any plain CLI run lands here,
   preserving pre-multi-session behaviour exactly.

Adding another AI tool's session variable (Cursor, Windsurf, …) is a
one-line append to :data:`_SESSION_ENV_VARS`.
"""

from __future__ import annotations

import os

__all__ = ["DEFAULT_SESSION_ID", "auto_detect_session_id"]

DEFAULT_SESSION_ID = "default"

# Ordered by precedence — the first one set (and non-empty after stripping)
# determines the session id.
_SESSION_ENV_VARS: tuple[str, ...] = (
    "AGENTCLOAK_SESSION",
    "CLAUDE_CODE_SESSION_ID",
)


def auto_detect_session_id() -> str:
    """Return the session id for this process, or ``"default"``.

    Reads the environment fresh on every call so a caller can set
    ``AGENTCLOAK_SESSION`` between client constructions and have it take
    effect without restarting the process.
    """
    for var in _SESSION_ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return DEFAULT_SESSION_ID
