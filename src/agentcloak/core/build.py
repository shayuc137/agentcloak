"""Identify the installed source, including untagged wheels and editable builds."""

from __future__ import annotations

import hashlib
import subprocess
from functools import cache
from pathlib import Path


@cache
def build_id() -> str:
    package = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    source = digest.hexdigest()[:12]
    repository = package.parent.parent
    if (repository / ".git").exists():
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short=12", "HEAD"],
                cwd=repository,
                stderr=subprocess.DEVNULL,
                timeout=1,
                text=True,
            ).strip()
            return f"{commit}/src-{source}"
        except (OSError, subprocess.SubprocessError):
            pass
    return f"src-{source}"
