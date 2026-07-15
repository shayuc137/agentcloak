"""DiagnosticService — unified doctor checks + rich health + resume snapshot.

Today there are three separate places that run "is this thing healthy?"
checks: CLI ``doctor`` command, MCP ``doctor`` tool, and various ad-hoc
``/health`` calls. This service collects all of them so each surface can call
the same code and produce consistent output.

The ``doctor`` API also exposes a fix-mode that auto-installs what the running
process can fix on its own (Python deps, CloakBrowser binary download) and
emits a single combined shell command for the rest (system packages, Xvfb,
Playwright libs). When ``execute_sudo=True`` and the caller is a root/sudo
session, the command is actually run instead of just printed.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from agentcloak.core.config import load_config

__all__ = ["DiagnosticService"]


_REQUIRED_PACKAGES = (
    "typer",
    "orjson",
    "structlog",
    "fastapi",
    "uvicorn",
    "httpx",
    "cloakbrowser",
    "playwright",
    "httpcloak",
    "mcp",
)


_CHROMIUM_BINARIES = (
    "chromium-browser",
    "chromium",
    "google-chrome-stable",
    "google-chrome",
)


# Distro → (display name, package manager argv, xvfb package name).
# Used by the fix-mode command synthesis so we hand the user a one-liner that
# matches their actual OS instead of always saying ``apt-get``.
_DISTRO_PROFILES: dict[str, tuple[str, list[str], str]] = {
    "debian": ("Debian/Ubuntu", ["apt-get", "install", "-y"], "xvfb"),
    "ubuntu": ("Debian/Ubuntu", ["apt-get", "install", "-y"], "xvfb"),
    "linuxmint": ("Linux Mint", ["apt-get", "install", "-y"], "xvfb"),
    "pop": ("Pop!_OS", ["apt-get", "install", "-y"], "xvfb"),
    "fedora": ("Fedora", ["dnf", "install", "-y"], "xorg-x11-server-Xvfb"),
    "rhel": ("RHEL", ["dnf", "install", "-y"], "xorg-x11-server-Xvfb"),
    "centos": ("CentOS", ["dnf", "install", "-y"], "xorg-x11-server-Xvfb"),
    "rocky": ("Rocky Linux", ["dnf", "install", "-y"], "xorg-x11-server-Xvfb"),
    "almalinux": ("AlmaLinux", ["dnf", "install", "-y"], "xorg-x11-server-Xvfb"),
    "arch": ("Arch", ["pacman", "-S", "--noconfirm"], "xorg-server-xvfb"),
    "manjaro": ("Manjaro", ["pacman", "-S", "--noconfirm"], "xorg-server-xvfb"),
    "alpine": ("Alpine", ["apk", "add"], "xvfb"),
    "opensuse": ("openSUSE", ["zypper", "install", "-y"], "xorg-x11-server"),
    "opensuse-leap": ("openSUSE Leap", ["zypper", "install", "-y"], "xorg-x11-server"),
    "opensuse-tumbleweed": (
        "openSUSE Tumbleweed",
        ["zypper", "install", "-y"],
        "xorg-x11-server",
    ),
}


def _detect_linux_distro() -> tuple[str, list[str], str]:
    """Return ``(display_name, package_manager_argv, xvfb_package)`` for Linux.

    Reads ``/etc/os-release`` (systemd-standardised on all modern distros). On
    failure or unrecognised distro we fall back to the Debian profile —
    Debian-likes are the most common server target and giving the user a
    wrong-but-recognisable command is better than crashing the doctor.
    """
    os_release = Path("/etc/os-release")
    if not os_release.is_file():
        return _DISTRO_PROFILES["debian"]

    fields: dict[str, str] = {}
    try:
        for raw_line in os_release.read_text().splitlines():
            if "=" not in raw_line:
                continue
            key, _, val = raw_line.partition("=")
            fields[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        return _DISTRO_PROFILES["debian"]

    # Prefer ID, then ID_LIKE (handles e.g. ``ID=linuxmint`` whose ID_LIKE
    # claims ``ubuntu debian``). Lowercased so user-facing ``ID=Fedora`` still
    # routes correctly.
    candidates: list[str] = []
    if "ID" in fields:
        candidates.append(fields["ID"].lower())
    if "ID_LIKE" in fields:
        candidates.extend(tok.lower() for tok in fields["ID_LIKE"].split())

    for cand in candidates:
        if cand in _DISTRO_PROFILES:
            return _DISTRO_PROFILES[cand]

    return _DISTRO_PROFILES["debian"]


class DiagnosticService:
    """Diagnostics shared by CLI / MCP / daemon."""

    # ------------------------------------------------------------------
    # Doctor — environment checks
    # ------------------------------------------------------------------

    def doctor(self, *, data_dir: Path) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        checks.append(self._check_python())
        checks.append(self._check_path_entry())
        for pkg in _REQUIRED_PACKAGES:
            checks.append(self._check_package(pkg))
        checks.append(self._check_chromium())
        checks.append(self._check_cloakbrowser_binary())
        checks.append(self._check_data_dir(data_dir))
        checks.append(self._check_playwright_libs())

        # Xvfb is only relevant on Linux when there's no display and the user
        # actually plans to run the browser headed. Headless mode bypasses the
        # whole virtual framebuffer dance, so reporting "Xvfb missing" on
        # ``headless=true`` would be a false negative.
        extras: list[dict[str, Any]] = []
        if self._xvfb_relevant():
            extras.append(self._check_xvfb())
        # Stale Chromium versions are informational (disk hygiene), not a
        # health failure — CloakBrowser auto-updates leave old ~700MB builds
        # behind. Lives in ``extras`` so it never flips ``healthy`` to False.
        extras.append(self._check_stale_chromium())

        all_ok = all(c["ok"] for c in checks)
        return {
            "healthy": all_ok,
            "checks": checks,
            "extras": {
                "available": all(c["ok"] for c in extras) if extras else True,
                "checks": extras,
            },
        }

    # ------------------------------------------------------------------
    # Doctor (fix mode) — auto-fix what we can, synthesise a command for
    # the rest, optionally execute it under sudo.
    # ------------------------------------------------------------------

    def doctor_fix(
        self,
        *,
        data_dir: Path,
        execute_sudo: bool = False,
    ) -> dict[str, Any]:
        """Diagnose with side effects.

        Steps:
        1. Run a normal :meth:`doctor` pass to know what's broken.
        2. For each fixable check, attempt an in-process repair (download the
           CloakBrowser binary, create the data dir).
        3. Run :meth:`doctor` again so the response reflects the post-fix
           state and lists only the remaining issues.
        4. Build a single shell command covering whatever still needs
           system-level intervention. When ``execute_sudo=True`` and we have
           sudo/root, run that command via ``subprocess.run`` and re-check.
        """
        before = self.doctor(data_dir=data_dir)
        fix_actions: list[dict[str, Any]] = []

        # ---- in-process fixes ----
        # CloakBrowser binary download. We use ensure_binary so the user
        # doesn't need to remember a separate CLI.
        cb_check = next(
            (c for c in before["checks"] if c["name"] == "cloakbrowser_binary"),
            None,
        )
        if cb_check is not None and not cb_check["ok"]:
            outcome = self._fix_cloakbrowser_binary()
            fix_actions.append(outcome)

        # Data directory creation (cheap, idempotent).
        data_check = next(
            (c for c in before["checks"] if c["name"] == "data_directory"),
            None,
        )
        if data_check is not None and not Path(data_check["detail"]).exists():
            outcome = self._fix_data_dir(data_dir)
            fix_actions.append(outcome)

        # ---- after in-process fixes, re-run checks ----
        after = self.doctor(data_dir=data_dir)
        command = self._synthesise_fix_command(after)

        executed: dict[str, Any] | None = None
        if execute_sudo and command:
            executed = self._execute_fix_command(command)
            # Re-check one more time so the response is authoritative.
            after = self.doctor(data_dir=data_dir)

        return {
            "healthy": after["healthy"],
            "checks": after["checks"],
            "extras": after["extras"],
            "fix": {
                "before_ok": before["healthy"],
                "actions": fix_actions,
                "command": command,
                "executed": executed,
            },
        }

    # ------------------------------------------------------------------
    # Fix helpers (in-process)
    # ------------------------------------------------------------------

    @staticmethod
    def _fix_cloakbrowser_binary() -> dict[str, Any]:
        try:
            import cloakbrowser  # pyright: ignore[reportMissingImports,reportMissingTypeStubs]

            binary = str(cloakbrowser.ensure_binary())  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
            return {
                "name": "cloakbrowser_binary",
                "fixed": True,
                "detail": binary,
                "message": "downloaded patched Chromium binary",
            }
        except ImportError:
            return {
                "name": "cloakbrowser_binary",
                "fixed": False,
                "detail": "cloakbrowser not installed",
                "message": "install agentcloak before running doctor --fix",
            }
        except Exception as exc:
            return {
                "name": "cloakbrowser_binary",
                "fixed": False,
                "detail": str(exc),
                "message": "binary download failed; check network and retry",
            }

    @staticmethod
    def _fix_data_dir(data_dir: Path) -> dict[str, Any]:
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / "profiles").mkdir(exist_ok=True)
            (data_dir / "logs").mkdir(exist_ok=True)
            return {
                "name": "data_directory",
                "fixed": True,
                "detail": str(data_dir),
                "message": "created data directory tree",
            }
        except OSError as exc:
            return {
                "name": "data_directory",
                "fixed": False,
                "detail": str(data_dir),
                "message": f"could not create data dir: {exc}",
            }

    # ------------------------------------------------------------------
    # Fix command synthesis
    # ------------------------------------------------------------------

    def _synthesise_fix_command(self, report: dict[str, Any]) -> str:
        """Build a single ``&&``-chained command for outstanding issues."""
        segments: list[str] = []

        # Playwright libs: only ask for system libs if the user didn't already
        # install them, so doctor doesn't nag on macOS/Windows where the cmd
        # is irrelevant.
        pw_check = next(
            (c for c in report["checks"] if c["name"] == "playwright_libs"),
            None,
        )
        if pw_check is not None and not pw_check["ok"]:
            segments.append("sudo playwright install-deps chromium")

        # Xvfb (only present in ``extras`` when relevant).
        xvfb_check = next(
            (c for c in report["extras"].get("checks", []) if c["name"] == "xvfb"),
            None,
        )
        if xvfb_check is not None and not xvfb_check["ok"]:
            _, mgr_argv, pkg = _detect_linux_distro()
            segments.append("sudo " + " ".join([*mgr_argv, pkg]))

        return " && ".join(segments)

    @staticmethod
    def _execute_fix_command(command: str) -> dict[str, Any]:
        """Run the synthesised fix command.

        ``execute_sudo=True`` only makes sense when the current user can
        actually invoke sudo — we don't try to do anything fancy like
        prompt-passing. If sudo is missing we report the gap instead of
        silently doing nothing.
        """
        if sys.platform == "win32":
            return {
                "ran": False,
                "command": command,
                "reason": "auto-fix is not supported on Windows",
            }
        if os.geteuid() != 0 and shutil.which("sudo") is None:
            return {
                "ran": False,
                "command": command,
                "reason": "no sudo binary and not running as root",
            }
        try:
            # We pipe stderr to stdout so the caller sees the package-manager
            # output in one place — pacman/apt-get like to mix progress and
            # errors freely.
            proc = subprocess.run(
                command,
                shell=True,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=600,
            )
            return {
                "ran": True,
                "command": command,
                "exit_code": proc.returncode,
                "output": proc.stdout[-4000:],  # cap for envelope size
            }
        except subprocess.TimeoutExpired:
            return {
                "ran": True,
                "command": command,
                "exit_code": None,
                "output": "command exceeded 600s timeout",
            }
        except OSError as exc:
            return {
                "ran": False,
                "command": command,
                "reason": str(exc),
            }

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_python() -> dict[str, Any]:
        v = sys.version_info
        ok = (v.major, v.minor) >= (3, 12)
        return {
            "name": "python_version",
            "ok": ok,
            "detail": platform.python_version(),
            "hint": "Python >= 3.12 required" if not ok else "",
        }

    @staticmethod
    def _check_path_entry() -> dict[str, Any]:
        """Warn when ``agentcloak`` isn't reachable through ``$PATH``.

        ``pip install --user`` is a common foot-gun on Windows and freshly
        installed Pythons because the user-scripts dir often isn't on PATH.
        Doctor catches that early so the user doesn't hit ``command not found``
        immediately after install.
        """
        path = shutil.which("agentcloak") or shutil.which("cloak")
        if path:
            return {"name": "path_entry", "ok": True, "detail": path, "hint": ""}
        return {
            "name": "path_entry",
            "ok": False,
            "detail": "agentcloak/cloak not on PATH",
            "hint": (
                "ensure the install scripts dir is on PATH (Windows: "
                "%APPDATA%\\Python\\PythonXY\\Scripts; *nix: ~/.local/bin)"
            ),
        }

    @staticmethod
    def _check_package(name: str) -> dict[str, Any]:
        try:
            ver = version(name)
            return {"name": name, "ok": True, "detail": ver, "hint": ""}
        except PackageNotFoundError:
            return {
                "name": name,
                "ok": False,
                "detail": "not installed",
                "hint": f"pip install {name}",
            }

    @staticmethod
    def _check_chromium() -> dict[str, Any]:
        for name in _CHROMIUM_BINARIES:
            path = shutil.which(name)
            if path:
                return {
                    "name": "chromium",
                    "ok": True,
                    "detail": path,
                    "hint": "",
                }
        return {
            "name": "chromium",
            "ok": False,
            "detail": "not found",
            "hint": "install chromium or run 'playwright install chromium'",
        }

    @staticmethod
    def _check_data_dir(data_dir: Path) -> dict[str, Any]:
        exists = data_dir.is_dir()
        return {
            "name": "data_directory",
            "ok": True,
            "detail": str(data_dir),
            "hint": "" if exists else "will be created on first use",
        }

    @staticmethod
    def _check_playwright_libs() -> dict[str, Any]:
        """Probe for the dynamic libs Playwright/Chromium drags in on Linux.

        Only Linux needs this check — macOS and Windows ship Chromium-ready
        runtimes by default. We sample three well-known dependencies that
        ``playwright install-deps chromium`` typically supplies; if any are
        missing we suggest the standard install-deps command.
        """
        if platform.system() != "Linux":
            return {
                "name": "playwright_libs",
                "ok": True,
                "detail": "not required on this OS",
                "hint": "",
            }

        # ldconfig prints "<libname> (...) => <path>"; matching by name is
        # robust across distros without depending on ldd against the chromium
        # binary itself (which may not be downloaded yet).
        ldconfig = shutil.which("ldconfig")
        if ldconfig is None:
            return {
                "name": "playwright_libs",
                "ok": True,
                "detail": "ldconfig unavailable, skipping deep check",
                "hint": "run 'playwright install-deps chromium' if Chromium fails",
            }
        try:
            output = subprocess.run(
                [ldconfig, "-p"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return {
                "name": "playwright_libs",
                "ok": True,
                "detail": "ldconfig probe failed, skipping deep check",
                "hint": "",
            }

        required = ("libnss3.so", "libnssutil3.so", "libgbm.so", "libasound.so")
        missing = [lib for lib in required if lib not in output]
        if not missing:
            return {
                "name": "playwright_libs",
                "ok": True,
                "detail": "core libs present",
                "hint": "",
            }
        return {
            "name": "playwright_libs",
            "ok": False,
            "detail": "missing: " + ", ".join(missing),
            "hint": "sudo playwright install-deps chromium",
        }

    @staticmethod
    def _xvfb_relevant() -> bool:
        """Return ``True`` when Xvfb is needed for the current configuration.

        Xvfb is only meaningful when (a) we're on Linux, (b) there's no
        X/Wayland display already available, and (c) the user actually plans to
        run the browser headed. ``headless=true`` makes Xvfb irrelevant
        regardless of the display state.
        """
        if platform.system() != "Linux":
            return False
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            return False
        try:
            _, cfg = load_config()
        except Exception:
            # Config-load problems are surfaced via other checks; assume Xvfb
            # might be needed if we can't tell.
            return True
        return not cfg.browser.headless

    @staticmethod
    def _check_xvfb() -> dict[str, Any]:
        path = shutil.which("Xvfb")
        if path:
            return {"name": "xvfb", "ok": True, "detail": path, "hint": ""}
        _, mgr_argv, pkg = _detect_linux_distro()
        return {
            "name": "xvfb",
            "ok": False,
            "detail": "not found",
            "hint": (
                f"install with: sudo {' '.join(mgr_argv)} {pkg} "
                "(needed for headed mode on a Linux box without a display)"
            ),
        }

    @staticmethod
    def _check_cloakbrowser_binary() -> dict[str, Any]:
        try:
            import cloakbrowser  # pyright: ignore[reportMissingImports,reportMissingTypeStubs]

            info: dict[str, Any] = cloakbrowser.binary_info()  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
            installed = bool(info.get("installed"))
            detail = str(info.get("binary_path", "")) or "not downloaded"
            if installed:
                return {
                    "name": "cloakbrowser_binary",
                    "ok": True,
                    "detail": detail,
                    "hint": "",
                }
            return {
                "name": "cloakbrowser_binary",
                "ok": False,
                "detail": detail,
                "hint": "run 'agentcloak doctor --fix' to download (~200MB)",
            }
        except ImportError:
            return {
                "name": "cloakbrowser_binary",
                "ok": False,
                "detail": "cloakbrowser not installed",
                "hint": "pip install agentcloak",
            }
        except Exception as exc:
            return {
                "name": "cloakbrowser_binary",
                "ok": False,
                "detail": f"binary check failed: {exc}",
                "hint": "run 'agentcloak doctor --fix' to download",
            }

    @staticmethod
    def _dir_size_mb(path: Path) -> float:
        """Best-effort recursive directory size in MiB.

        Symlinks are not followed and unreadable entries are skipped so a
        permission hiccup on one file can't blow up the whole doctor run.
        """
        total = 0
        for root, _dirs, files in os.walk(path, followlinks=False):
            for fname in files:
                fp = Path(root) / fname
                try:
                    total += fp.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
        return total / (1024 * 1024)

    @classmethod
    def _check_stale_chromium(cls) -> dict[str, Any]:
        """Detect old CloakBrowser Chromium builds left by auto-update.

        CloakBrowser downloads each Chromium version into
        ``<cache>/chromium-<version>/`` (~700MB each) and never prunes the
        previous one. This check finds every ``chromium-*`` dir that isn't the
        currently-effective version and reports the reclaimable space. It is
        purely informational — ``doctor`` keeps it out of the blocking
        ``checks`` list so a healthy install with leftover binaries still
        reports green.

        The whole thing is ``ok=True`` (nothing to do) when CloakBrowser isn't
        installed, the cache dir is missing, or only the current version is
        present. ``get_cache_dir()`` resolves to ``~/.cloakbrowser`` (or the
        ``CLOAKBROWSER_CACHE_DIR`` override) on every platform — pure ``Path``
        work, no OS branch.
        """
        try:
            from cloakbrowser.config import (  # pyright: ignore[reportMissingImports,reportMissingTypeStubs,reportUnknownVariableType]
                get_cache_dir,
                get_effective_version,
            )
        except ImportError:
            # CloakBrowser not installed → nothing to clean up.
            return {
                "name": "stale_chromium",
                "ok": True,
                "detail": "cloakbrowser not installed",
                "hint": "",
                "stale_dirs": [],
                "reclaimable_mb": 0,
            }

        try:
            cache_dir = Path(get_cache_dir())  # pyright: ignore[reportUnknownArgumentType]
            current = f"chromium-{get_effective_version()}"  # pyright: ignore[reportUnknownArgumentType]
            if not cache_dir.is_dir():
                return {
                    "name": "stale_chromium",
                    "ok": True,
                    "detail": "no chromium cache directory",
                    "hint": "",
                    "stale_dirs": [],
                    "reclaimable_mb": 0,
                }

            stale_dirs = [
                d
                for d in cache_dir.glob("chromium-*")
                if d.is_dir() and d.name != current
            ]
            if not stale_dirs:
                return {
                    "name": "stale_chromium",
                    "ok": True,
                    "detail": f"only current version ({current})",
                    "hint": "",
                    "stale_dirs": [],
                    "reclaimable_mb": 0,
                }

            reclaimable = sum(cls._dir_size_mb(d) for d in stale_dirs)
            paths = [str(d) for d in sorted(stale_dirs)]
            return {
                "name": "stale_chromium",
                "ok": False,
                "detail": (
                    f"{len(stale_dirs)} old chromium "
                    f"version{'s' if len(stale_dirs) != 1 else ''} "
                    f"(~{reclaimable:.0f} MB reclaimable)"
                ),
                "hint": f"reclaim ~{reclaimable:.0f} MB: rm -rf {paths[0]}",
                "stale_dirs": paths,
                "reclaimable_mb": round(reclaimable),
            }
        except Exception as exc:
            # Never let disk-hygiene introspection break doctor.
            return {
                "name": "stale_chromium",
                "ok": True,
                "detail": f"stale check skipped: {exc}",
                "hint": "",
                "stale_dirs": [],
                "reclaimable_mb": 0,
            }

    # ------------------------------------------------------------------
    # Health — runtime liveness with browser introspection
    # ------------------------------------------------------------------

    async def health(
        self,
        ctx: Any,
        *,
        local_proxy: Any = None,
        active_tier: Any = None,
        remote_connected: bool = False,
        config: Any = None,
        active_profile: str | None = None,
        route_count: int = 0,
        started_at: float | None = None,
        metrics: Any = None,
    ) -> dict[str, Any]:
        """Build a health payload — supports the "no browser yet" state.

        When ``ctx`` is ``None`` (typical in ``remote_bridge`` mode while
        we wait for the extension), we still report the configured tier so
        agents know what the daemon is set up for.

        The ``config`` and ``active_profile`` fields populate the runtime
        status block consumed by ``cloak doctor`` — they describe the agent's
        environment (which browser, headless or not, proxy, profile) rather
        than just daemon liveness.

        ``started_at`` (a ``time.monotonic()`` reference) and ``metrics`` (a
        :class:`~agentcloak.daemon.middleware.MetricsState`) feed the daemon
        liveness metrics — uptime, total request count, in-flight requests.
        Both are optional so callers that don't track them (or unit-test apps)
        simply omit those fields from the payload.
        """
        import time

        import agentcloak

        data: dict[str, Any] = {
            "ok": True,
            "service": "agentcloak-daemon",
            "version": agentcloak.__version__,
            "route_count": route_count,
            "browser_ready": ctx is not None,
            "remote_connected": remote_connected,
        }
        if started_at is not None:
            # Clamp at 0 so a monotonic clock quirk never reports negative
            # uptime; round to whole seconds — sub-second precision is noise.
            data["uptime_seconds"] = round(max(0.0, time.monotonic() - started_at), 1)
        if metrics is not None:
            data["request_count"] = int(getattr(metrics, "request_count", 0) or 0)
            data["active_connections"] = int(
                getattr(metrics, "active_connections", 0) or 0
            )
        if active_tier is not None:
            tier_value = getattr(active_tier, "value", active_tier)
            data["active_tier"] = str(tier_value)
            data["stealth_tier"] = str(tier_value)

        if ctx is not None:
            data["stealth_tier"] = ctx.stealth_tier.value
            data["seq"] = ctx.seq
            data["capture_recording"] = ctx.capture_store.recording
            data["capture_entries"] = len(ctx.capture_store)
            # ``page_valid`` exposes the navigate-validity flag so agents
            # can query it directly via ``cloak doctor`` instead of having
            # to trigger an op and parse the error envelope. The flag lives
            # on the inner context — SecureBrowserContext forwards it via
            # ``__getattr__``.
            data["page_valid"] = bool(getattr(ctx, "_page_valid", True))

            # Browser description comes from the backend so we don't hardcode
            # tier → name mapping (and so future backends like Camoufox plug
            # in without touching this service).
            try:
                data["browser_description"] = ctx.browser_description()
            except Exception:
                data["browser_description"] = None

            # Pull the current URL/title best-effort; failures are non-fatal.
            # When ``page_valid`` is False the snapshot call will raise
            # ``NavigationError`` — that's expected, we still want the rest
            # of the health payload to render.
            try:
                snap = await ctx.snapshot(mode="accessible")
                data["current_url"] = snap.url
                data["current_title"] = snap.title
            except Exception:
                data["current_url"] = None
                data["current_title"] = None
        else:
            data["seq"] = 0
            data["capture_recording"] = False
            data["capture_entries"] = 0
            data["current_url"] = None
            data["current_title"] = None
            data["browser_description"] = None
            data["page_valid"] = False

        if config is not None:
            # Runtime status fields the doctor command needs to render its
            # second line. ``headless``/``humanize`` are bool — doctor decides
            # the textual rendering. ``proxy`` is the user-configured upstream
            # proxy (browser-level), not the httpcloak local proxy.
            browser_cfg = getattr(config, "browser", config)
            data["headless"] = bool(getattr(browser_cfg, "headless", True))
            data["humanize"] = bool(getattr(browser_cfg, "humanize", True))
            data["proxy"] = str(getattr(browser_cfg, "proxy", "") or "")
        # ``active_profile`` is None for ephemeral sessions; doctor renders
        # the difference (``profile: name`` vs ``no profile (ephemeral)``).
        data["active_profile"] = active_profile or ""

        if local_proxy is not None:
            try:
                data["local_proxy"] = {
                    "running": local_proxy.is_running,
                    "url": local_proxy.proxy_url,
                }
            except Exception:
                data["local_proxy"] = {"running": False}
        return data
