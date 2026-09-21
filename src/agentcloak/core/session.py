"""Stable caller identity for shared-profile browser sessions."""

from __future__ import annotations

import hashlib
import os
import subprocess
from contextvars import ContextVar
from pathlib import Path

__all__ = ["DEFAULT_SESSION_ID", "auto_detect_session_id", "cli_session_id"]

DEFAULT_SESSION_ID = "default"
cli_session_id: ContextVar[str | None] = ContextVar("cli_session_id", default=None)


def auto_detect_session_id() -> str:
    explicit = cli_session_id.get() or os.environ.get("AGENTCLOAK_SESSION", "").strip()
    if explicit:
        return explicit
    cwd = Path.cwd().resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        return Path(result.stdout.strip()).name
    except (OSError, subprocess.SubprocessError):
        return "cwd-" + hashlib.sha256(str(cwd).encode()).hexdigest()[:12]
