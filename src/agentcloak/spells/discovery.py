"""Spell discovery — scan built-in and user directories for spells."""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

import structlog

from agentcloak.spells.registry import get_registry

__all__ = ["discover_spells"]

log = structlog.get_logger()

_USER_SPELL_DIR = (
    Path(os.environ["APPDATA"]) / "agentcloak" / "spells"
    if sys.platform == "win32" and "APPDATA" in os.environ
    else Path.home() / ".config" / "agentcloak" / "spells"
)


def _import_module_from_path(name: str, path: Path) -> ModuleType | None:
    """Import a Python module from a file path."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception:
        log.warning("spell.import_failed", path=str(path))
        sys.modules.pop(name, None)
        return None
    return module


def _discover_builtin() -> int:
    """Import built-in spells from spells/sites/."""
    count_before = len(get_registry())
    builtin_module = "agentcloak.spells.sites._example"
    try:
        if builtin_module in sys.modules:
            importlib.reload(sys.modules[builtin_module])
        else:
            importlib.import_module(builtin_module)
    except Exception:
        log.warning("spell.builtin_import_failed")
    return len(get_registry()) - count_before


def _discover_user() -> int:
    """Scan user spell directory for .py files."""
    count_before = len(get_registry())
    if not _USER_SPELL_DIR.is_dir():
        return 0
    for py_file in sorted(_USER_SPELL_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        module_name = f"agentcloak_user_spells.{py_file.stem}"
        _import_module_from_path(module_name, py_file)
    return len(get_registry()) - count_before


def discover_spells() -> dict[str, int]:
    """Run full spell discovery. Returns counts by source."""
    builtin = _discover_builtin()
    user = _discover_user()
    log.debug(
        "spell.discovery_complete",
        builtin=builtin,
        user=user,
        total=len(get_registry()),
    )
    return {"builtin": builtin, "user": user, "total": len(get_registry())}
