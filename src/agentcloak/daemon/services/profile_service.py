"""ProfileService — unified profile CRUD across CLI / MCP / daemon.

Profile lifecycle used to live in three places (CLI helpers, MCP tools,
daemon routes) with slight differences in name validation, error envelopes,
and the "create from current session" subprocess invocation. This service is
the single source of truth.
"""

from __future__ import annotations

import asyncio
import contextlib
import json as _json_mod
import os as _os
import shutil
import sys
import tempfile as _tempfile
from typing import TYPE_CHECKING, Any, cast

from agentcloak.core.errors import ProfileError
from agentcloak.core.types import PROFILE_NAME_RE

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["ProfileService"]


class ProfileService:
    """Stateless helper around the ``profiles/`` directory on disk."""

    def __init__(self, profiles_dir: Path) -> None:
        self._profiles_dir = profiles_dir

    @property
    def profiles_dir(self) -> Path:
        return self._profiles_dir

    def ensure_dir(self) -> None:
        self._profiles_dir.mkdir(parents=True, exist_ok=True)

    def read_hide_selectors(self, name: str) -> list[str]:
        """Read profile-scoped hide selectors from ``hide.json``."""
        self.validate_name(name)
        path = self._profiles_dir / name / "hide.json"
        if not path.exists():
            return []
        try:
            payload = _json_mod.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, _json_mod.JSONDecodeError) as exc:
            raise ProfileError(
                error="profile_hide_invalid",
                hint=f"Could not read hide selectors for profile '{name}': {exc}",
                action="fix or remove the profile hide.json file",
            ) from exc
        selectors_obj = (
            cast("dict[str, object]", payload).get("selectors")
            if isinstance(payload, dict)
            else None
        )
        if not isinstance(selectors_obj, list):
            raise ProfileError(
                error="profile_hide_invalid",
                hint=f"Profile '{name}' hide.json must contain a selectors string list",
                action='replace hide.json with {"selectors": [".toolbar"]}',
            )
        selector_values = cast("list[object]", selectors_obj)
        if not all(isinstance(selector, str) for selector in selector_values):
            raise ProfileError(
                error="profile_hide_invalid",
                hint=f"Profile '{name}' hide.json must contain a selectors string list",
                action='replace hide.json with {"selectors": [".toolbar"]}',
            )
        selectors = cast("list[str]", selector_values)
        return list(
            dict.fromkeys(
                selector.strip() for selector in selectors if selector.strip()
            )
        )

    def write_hide_selectors(self, name: str, selectors: list[str]) -> None:
        """Atomically write profile-scoped hide selectors."""
        self.validate_name(name)
        profile_dir = self._profiles_dir / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        path = profile_dir / "hide.json"
        temp_path = profile_dir / ".hide.json.tmp"
        normalized = list(
            dict.fromkeys(
                selector.strip() for selector in selectors if selector.strip()
            )
        )
        try:
            temp_path.write_text(
                _json_mod.dumps({"selectors": normalized}, indent=2) + "\n",
                encoding="utf-8",
            )
            temp_path.replace(path)
        except OSError as exc:
            raise ProfileError(
                error="profile_hide_write_failed",
                hint=f"Could not write hide selectors for profile '{name}': {exc}",
                action="check that the profile directory is writable",
            ) from exc

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_profiles(self) -> list[str]:
        self.ensure_dir()
        return sorted(d.name for d in self._profiles_dir.iterdir() if d.is_dir())

    # ------------------------------------------------------------------
    # Create / Delete
    # ------------------------------------------------------------------

    def validate_name(self, name: str) -> None:
        if not name:
            raise ProfileError(
                error="missing_name",
                hint="Profile name is required",
                action="provide 'name' parameter",
            )
        if not PROFILE_NAME_RE.match(name):
            raise ProfileError(
                error="invalid_profile_name",
                hint=f"Profile name '{name}' is not valid",
                action="use lowercase alphanumeric and hyphens",
            )

    def create(self, name: str) -> str:
        self.validate_name(name)
        self.ensure_dir()
        profile_path = self._profiles_dir / name
        if profile_path.exists():
            raise ProfileError(
                error="profile_exists",
                hint=f"Profile '{name}' already exists",
                action="use a different name or delete first",
            )
        profile_path.mkdir(parents=True)
        return name

    def delete(self, name: str) -> str:
        self.validate_name(name)
        profile_path = self._profiles_dir / name
        # Guard against path traversal — resolve and check containment.
        if not profile_path.resolve().is_relative_to(self._profiles_dir.resolve()):
            raise ProfileError(
                error="invalid_profile_path",
                hint="Profile path escapes profiles directory",
                action="use a simple profile name without path separators",
            )
        if not profile_path.exists():
            raise ProfileError(
                error="profile_not_found",
                hint=f"Profile '{name}' does not exist",
                action="use profile list to see available",
            )
        shutil.rmtree(profile_path)
        return name

    # ------------------------------------------------------------------
    # Create from current session (cookies → on-disk profile)
    # ------------------------------------------------------------------

    async def create_from_cookies(
        self,
        name: str,
        cookies: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Persist a fresh profile populated from the given cookie list.

        Allocates a non-colliding name (appending ``-2``, ``-3``, etc.) and
        shells out to the ``_profile_writer`` subprocess which knows how to
        write CloakBrowser's user-data-dir layout.
        """
        self.validate_name(name)
        self.ensure_dir()

        actual_name = name
        renamed = False
        if (self._profiles_dir / actual_name).exists():
            counter = 2
            while (self._profiles_dir / f"{name}-{counter}").exists():
                counter += 1
            actual_name = f"{name}-{counter}"
            renamed = True

        profile_dir = self._profiles_dir / actual_name
        profile_dir.mkdir(parents=True, exist_ok=True)

        exec_path = self._maybe_cloakbrowser_binary()
        await self._run_profile_writer(profile_dir, cookies, exec_path)

        return {
            "profile": actual_name,
            "renamed": renamed,
            "cookie_count": len(cookies),
        }

    @staticmethod
    def _maybe_cloakbrowser_binary() -> str | None:
        try:
            import cloakbrowser as _cb  # pyright: ignore[reportMissingImports,reportMissingTypeStubs]

            info = _cb.binary_info()  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
            if info.get("installed"):  # pyright: ignore[reportUnknownMemberType]
                return str(info["binary_path"])  # pyright: ignore[reportUnknownArgumentType]
        except ImportError:
            return None
        return None

    @staticmethod
    async def _run_profile_writer(
        profile_dir: Path,
        cookies: list[dict[str, Any]],
        exec_path: str | None,
    ) -> None:
        fd, cookies_file = _tempfile.mkstemp(suffix=".json", prefix="cloak-cookies-")
        try:
            with _os.fdopen(fd, "w") as f:
                _json_mod.dump(cookies, f)
            _os.chmod(cookies_file, 0o600)

            cmd = [
                sys.executable,
                "-m",
                "agentcloak.browser._profile_writer",
                "--profile-dir",
                str(profile_dir),
                "--cookies-file",
                cookies_file,
            ]
            if exec_path:
                cmd.extend(["--executable-path", exec_path])

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr_bytes = await proc.communicate()
        finally:
            with contextlib.suppress(OSError):
                _os.unlink(cookies_file)

        if proc.returncode != 0:
            err_msg = stderr_bytes.decode(errors="replace")[:300]
            raise ProfileError(
                error="profile_writer_failed",
                hint=err_msg,
                action="check daemon logs for the profile writer subprocess",
            )
