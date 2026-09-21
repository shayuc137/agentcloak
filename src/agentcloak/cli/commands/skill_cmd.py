"""Skill bundle management — install/update/uninstall to agent platform dirs.

The skill bundle (``SKILL.md`` + ``references/``) ships inside the wheel
under :mod:`agentcloak._skill_data`. ``cloak skill install`` copies it to a
canonical location at ``~/.agentcloak/skills/agentcloak/`` and then symlinks
each agent platform directory (``~/.claude/skills/agentcloak/``,
``.codex/skills/agentcloak/``, ...) at that canonical source.

Re-running ``install`` is idempotent — the canonical copy is refreshed from
the wheel and existing symlinks pick up the new content for free. ``update``
runs only the refresh step. ``uninstall`` removes symlinks/copies from every
known platform location and, with ``--remove-canonical``, the source too.

Windows fallback: when :func:`os.symlink` raises (Developer Mode disabled),
the command falls back to :func:`shutil.copytree` and reports ``(copy)`` so
the user knows future updates need a manual re-run.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from agentcloak.cli.output import error, info, success, value

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable

__all__ = ["app"]

app = typer.Typer()


# ---------------------------------------------------------------------------
# Platform catalog
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Platform:
    """One agent platform's skill installation slot."""

    alias: str  # CLI value (``--platform <alias>``)
    label: str  # human-readable label for the menu
    base: Path  # the platform's root dir (``.claude/``, ``~/.codex/``, ...)

    @property
    def target(self) -> Path:
        """The exact path the symlink/copy gets created at."""
        return self.base / "skills" / "agentcloak"

    def detected(self) -> bool:
        """True when the base dir exists — i.e. the user uses this agent."""
        return self.base.is_dir()


def _all_platforms(cwd: Path) -> list[Platform]:
    """Catalog every supported platform target relative to ``cwd``.

    Project-scoped entries resolve under ``cwd`` (so they're recreated for
    whichever repo the user is in); user-global entries always sit under
    ``~``. Use ``--path`` to install to an arbitrary location.
    """
    home = Path.home()
    return [
        Platform("claude", "Claude Code", home / ".claude"),
        Platform("codex", "Codex (global)", home / ".codex"),
        Platform("codex-project", "Codex (project)", cwd / ".codex"),
        Platform("cursor", "Cursor (project)", cwd / ".cursor"),
        Platform("opencode", "OpenCode (project)", cwd / ".opencode"),
    ]


# Canonical install location. Every platform symlink points here so updates
# flow through a single rewrite. Living under ``~/.agentcloak/`` keeps the
# state next to the daemon's data dir for easy cleanup.
CANONICAL = Path.home() / ".agentcloak" / "skills" / "agentcloak"


# ---------------------------------------------------------------------------
# Canonical copy + symlink helpers
# ---------------------------------------------------------------------------


def _copy_skill_data_to_canonical() -> None:
    """Refresh ``CANONICAL`` from the wheel's bundled skill data.

    Uses :mod:`importlib.resources` so the read works whether the package
    lives on disk (editable install) or inside a zipped wheel. We delete
    the existing canonical tree first to drop any stale files left over
    from a previous version of the bundle.
    """
    src_root = files("agentcloak._skill_data") / "agentcloak"
    if CANONICAL.exists():
        # Symlinks at CANONICAL itself would be unusual but we still want to
        # land at the same path — unlink first to keep things predictable.
        if CANONICAL.is_symlink():
            CANONICAL.unlink()
        else:
            shutil.rmtree(CANONICAL)
    CANONICAL.mkdir(parents=True, exist_ok=True)
    _copy_resource_tree(src_root, CANONICAL)


def _copy_resource_tree(src: Traversable, dst: Path) -> None:
    """Recursively copy a :mod:`importlib.resources` directory to ``dst``.

    ``Traversable.iterdir`` mirrors ``Path.iterdir`` for both real-filesystem
    and zipfile-backed resources, so this works in any install mode.
    """
    for child in src.iterdir():
        child_target = dst / child.name
        if child.is_dir():
            child_target.mkdir(parents=True, exist_ok=True)
            _copy_resource_tree(child, child_target)
        else:
            with as_file(child) as src_path:
                shutil.copyfile(src_path, child_target)


def _link_to_canonical(target: Path) -> str:
    """Point ``target`` at ``CANONICAL``. Returns ``"symlink"`` or ``"copy"``.

    Removes whatever sits at ``target`` first (file, dir, broken symlink) so
    a re-install always lands cleanly. Falls back to ``copytree`` on Windows
    without Developer Mode where ``os.symlink`` raises ``OSError``.
    """
    target.parent.mkdir(parents=True, exist_ok=True)

    # Detect-and-remove must happen before the symlink call. ``Path.exists``
    # follows symlinks (so a broken link looks absent), hence the explicit
    # ``is_symlink`` probe.
    if target.is_symlink():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()

    try:
        # ``target_is_directory=True`` is required on Windows for directory
        # symlinks; it's a no-op on POSIX. Without it Windows would create a
        # file-style link that resolves wrong.
        os.symlink(CANONICAL, target, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        # Fall back to a full copy. Updates won't propagate automatically;
        # the user has to re-run ``cloak skill install`` after each upgrade.
        shutil.copytree(CANONICAL, target)
        return "copy"


# ---------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------


def _interactive_select(platforms: list[Platform]) -> list[Platform]:
    """Render a numbered menu on stderr and read the user's choice from stdin.

    Returns the selected platform list (possibly multiple for ``a``) or an
    empty list when the user picks nothing valid.
    """
    info("Detected agent platforms:")
    for idx, p in enumerate(platforms, start=1):
        marker = "detected" if p.detected() else "not detected"
        info(f"  {idx}. {p.label:<24} {p.target}  [{marker}]")
    info("  a. All detected")
    info("")

    # Use raw stderr write so the prompt stays inline (info() appends \n).
    sys.stderr.write(f"Choose [1-{len(platforms)}, a]: ")
    sys.stderr.flush()
    choice = sys.stdin.readline().strip().lower()

    if not choice:
        return []
    if choice == "a":
        detected = [p for p in platforms if p.detected()]
        if not detected:
            info("no detected platforms.")
        return detected
    try:
        n = int(choice)
    except ValueError:
        info(f"invalid choice: {choice!r}")
        return []
    if not 1 <= n <= len(platforms):
        info(f"out of range: {n}")
        return []
    return [platforms[n - 1]]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("install")
def skill_install(
    platform: str | None = typer.Option(
        None,
        "--platform",
        help=(
            "Target platform alias (claude, codex, "
            "codex-project, cursor, opencode, all). Omit for interactive "
            "selection."
        ),
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        help="Install to a custom directory instead of a known platform.",
    ),
) -> None:
    """Install the skill bundle to one or more agent platform directories."""
    _copy_skill_data_to_canonical()

    # ``--path`` short-circuits platform handling entirely — it's the escape
    # hatch for non-standard layouts (custom agent forks, network shares).
    if path is not None:
        target = path.expanduser().resolve()
        kind = _link_to_canonical(target)
        success(f"installed skill to {target} ({kind})")
        return

    platforms = _all_platforms(Path.cwd())

    if platform is None:
        chosen = _interactive_select(platforms)
        if not chosen:
            info("aborted: nothing installed.")
            raise typer.Exit(1)
    elif platform == "all":
        chosen = [p for p in platforms if p.detected()]
        if not chosen:
            info("no detected platforms — install nothing.")
            return
    else:
        match = next((p for p in platforms if p.alias == platform), None)
        if match is None:
            valid = ", ".join(p.alias for p in platforms) + ", all"
            error(
                f"unknown platform '{platform}'",
                f"use one of: {valid}",
            )
            return
        chosen = [match]

    lines: list[str] = []
    for p in chosen:
        kind = _link_to_canonical(p.target)
        lines.append(f"installed skill to {p.target} ({kind})")
    if lines:
        value("\n".join(lines))


@app.command("update")
def skill_update() -> None:
    """Refresh the canonical skill copy from the installed wheel.

    Symlink-style installs pick up the new content automatically. Copy-style
    installs (Windows fallback) need a manual ``cloak skill install`` after
    this command.
    """
    _copy_skill_data_to_canonical()
    success(f"updated skill at {CANONICAL}")


@app.command("uninstall")
def skill_uninstall(
    remove_canonical: bool = typer.Option(
        False,
        "--remove-canonical",
        help=("Also delete the canonical install at ~/.agentcloak/skills/agentcloak/."),
    ),
) -> None:
    """Remove skill installations from every known platform directory."""
    removed: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    failures: list[str] = []

    for p in _all_platforms(Path.cwd()):
        target = p.target
        if target.is_symlink():
            # Only remove links that actually point at our canonical source —
            # we don't want to unlink a user-managed pointer to somewhere
            # else that happens to share the path.
            try:
                resolved = target.readlink()
            except OSError as exc:
                skipped.append((target, f"readlink failed: {exc}"))
                failures.append(f"{target}: {exc}")
                continue
            if Path(resolved) != CANONICAL:
                skipped.append((target, f"symlink points to {resolved}"))
                continue
            try:
                target.unlink()
                removed.append(target)
            except OSError as exc:
                skipped.append((target, f"unlink failed: {exc}"))
                failures.append(f"{target}: {exc}")
        elif target.is_dir():
            # Copy-installed (Windows fallback). Use a sentinel file before
            # ``rmtree`` so we never blow away an unrelated directory that
            # someone parked at this path manually.
            sentinel = target / "SKILL.md"
            if not sentinel.is_file():
                skipped.append((target, "no SKILL.md sentinel — not ours"))
                continue
            try:
                shutil.rmtree(target)
                removed.append(target)
            except OSError as exc:
                skipped.append((target, f"rmtree failed: {exc}"))
                failures.append(f"{target}: {exc}")

    if remove_canonical and CANONICAL.exists():
        try:
            shutil.rmtree(CANONICAL)
            removed.append(CANONICAL)
        except OSError as exc:
            skipped.append((CANONICAL, f"rmtree failed: {exc}"))
            failures.append(f"{CANONICAL}: {exc}")

    if failures:
        error(
            "; ".join(failures),
            code="skill_uninstall_failed",
            data={"removed": [str(path) for path in removed]},
        )

    if not removed and not skipped:
        info("no skill installations found.")
        return

    if removed:
        value("removed:\n" + "\n".join(f"  {p}" for p in removed))
    if skipped:
        info("skipped:")
        for p, reason in skipped:
            info(f"  {p}  ({reason})")
