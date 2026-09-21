"""Stable workspace identity without requiring a Git repository."""

from __future__ import annotations

import hashlib
import os
import subprocess
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

cli_workspace: ContextVar[str | None] = ContextVar("cli_workspace", default=None)


@dataclass(frozen=True)
class WorkspaceIdentity:
    workspace_id: str
    session_scope: str
    workspace_path: str = ""
    label: str = ""


def _path_id(path: Path) -> str:
    canonical = os.path.normcase(str(path.resolve()))
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


def resolve_workspace(roots: list[str] | None = None) -> WorkspaceIdentity:
    cwd = Path.cwd().resolve()
    explicit = cli_workspace.get() or os.environ.get("AGENTCLOAK_WORKSPACE", "").strip()
    candidates = [Path(root).expanduser().resolve() for root in roots or []]
    candidates = [root for root in candidates if cwd.is_relative_to(root)]
    root = (
        Path(explicit).expanduser().resolve()
        if explicit
        else max(candidates, key=lambda p: len(p.parts), default=None)
    )
    worktree_root = None
    try:
        result = subprocess.run(
            [
                "git",
                "rev-parse",
                "--path-format=absolute",
                "--show-toplevel",
                "--git-common-dir",
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        worktree, common = result.stdout.strip().splitlines()
        worktree_root = Path(worktree).resolve()
        common_path = Path(common).resolve()
        if root is None:
            root = common_path.parent if common_path.name == ".git" else common_path
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    root = root or cwd
    return WorkspaceIdentity(
        _path_id(root),
        _path_id(worktree_root or root),
        str(root),
        (worktree_root or root).name,
    )


def workspace_state_dir(root: Path, workspace_id: str, profile: str | None) -> Path:
    key = hashlib.sha256((workspace_id + "\0" + (profile or "")).encode()).hexdigest()
    return root / "workspaces" / key
