#!/usr/bin/env python3
"""Drift detection for the shared :class:`DaemonClient`.

For v0.2.0 we keep ``daemon_client.py`` hand-written — it's already typed,
tested, and carefully tuned for the sync + async dual surface. This script
is a *verification tool*: it builds the FastAPI app, reads the OpenAPI spec,
and confirms that every daemon route has a matching async method on the
client. The richer "generate the client from the spec" workflow can land in
Phase 6 once the surface stabilises.

What it checks
--------------
* every route has a typed ``<name>`` async method on :class:`DaemonClient`
  (used by MCP tools and exercised by tests)
* the curated sync allow-list — CLI commands that need bespoke handling
  (base64 decode, custom export formats, etc.) — is present
* every public client method covers a route or sits in the allow-list
  (otherwise the client is growing dead code)

Sync vs async surface
---------------------
CLI commands dispatch most routes through ``DaemonClient._send_sync(method,
path, body)`` and render JSON locally, so they don't need a typed sync
wrapper per route. Only the nine commands in :data:`KEEP_SYNC_METHODS`
genuinely need their own sync entry point — usually because they reshape
the daemon's response (base64 decode, custom file output, etc.) before the
renderer sees it.

Usage
-----
    python scripts/generate_client.py            # human-readable report
    python scripts/generate_client.py --check    # exit 1 on drift (CI mode)

The script imports the FastAPI app rather than hitting a live daemon, so it
works in CI without a running browser.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Routes the spec exposes but the typed client deliberately does not surface.
# WebSockets and special-purpose endpoints (launch lifecycle, daemon-only
# diagnostics) live outside the typed wrapper, so we exclude them from the
# coverage check.
EXCLUDED_ROUTES: set[str] = set()

# Routes whose typed-method name doesn't follow the default ``path → snake``
# convention. ``/tabs`` is the only oddball today — the client method is
# ``tab_list`` to stay parallel with ``tab_new``/``tab_close``/``tab_switch``.
ROUTE_METHOD_OVERRIDES: dict[str, str] = {
    "/tabs": "tab_list",
}

# CLI commands that need a typed sync wrapper. Everything else goes through
# the generic ``DaemonClient._send_sync`` dispatch — see the module docstring
# for the rationale. Keep this list in sync with the CLI commands that touch
# bespoke sync methods (preflight will fail loudly if a sync method is added
# or removed without updating the allow-list).
KEEP_SYNC_METHODS: set[str] = {
    "screenshot_sync",
    "health_sync",
    "shutdown_sync",
    "bridge_token_reset_sync",
    "capture_export_sync",
    "capture_analyze_sync",
    "cookies_export_sync",
    "fetch_sync",
    "profile_create_from_current_sync",
}


def route_to_method(path: str) -> str:
    """Translate a route path to the canonical client method name."""
    if path in ROUTE_METHOD_OVERRIDES:
        return ROUTE_METHOD_OVERRIDES[path]
    # Drop leading ``/`` and replace remaining ``/`` and ``-`` with ``_``.
    return path.lstrip("/").replace("/", "_").replace("-", "_")


def collect_spec_routes() -> list[tuple[str, str]]:
    """Return ``[(method, path), ...]`` for every HTTP route in the FastAPI app."""
    from agentcloak.daemon.app import create_app

    app = create_app()
    spec = app.openapi()
    routes: list[tuple[str, str]] = []
    for path, methods in spec.get("paths", {}).items():
        for verb in methods:
            if verb in {"get", "post", "put", "patch", "delete"}:
                routes.append((verb.upper(), path))
    return sorted(routes)


def collect_client_methods() -> set[str]:
    """Return the set of public method names defined on :class:`DaemonClient`."""
    from agentcloak.client import DaemonClient

    return {
        name
        for name in vars(DaemonClient)
        if not name.startswith("_") and callable(getattr(DaemonClient, name))
    }


def find_drift() -> tuple[list[str], list[str]]:
    """Identify route → method mismatches and orphan client methods.

    Returns ``(missing, orphans)``:

    * ``missing`` — gaps in coverage. Every route must expose an async
      method; sync wrappers are only required for the curated CLI commands
      in :data:`KEEP_SYNC_METHODS`.
    * ``orphans`` — public client methods that don't correspond to any route
      (excluding the explicitly allow-listed ones like ``launch_daemon``).
    """
    routes = [r for r in collect_spec_routes() if r[1] not in EXCLUDED_ROUTES]
    methods = collect_client_methods()

    # Stand-alone client APIs that intentionally don't map 1:1 with a route —
    # they delegate to subprocess management or aggregate multiple routes.
    standalone = {
        "launch_daemon",
        "spawn_background",
        "config",  # property, exposed for downstream access
    }

    route_bases: set[str] = {route_to_method(path) for _, path in routes}

    missing: list[str] = []
    for _verb, path in routes:
        base = route_to_method(path)
        if base not in methods:
            missing.append(f"{path:40s} -> missing async {base}")

    # Every entry in the sync allow-list must correspond to a real route and
    # be present on the client; otherwise the CLI is calling a method that
    # silently no longer exists.
    for sync_name in sorted(KEEP_SYNC_METHODS):
        base = sync_name.removesuffix("_sync")
        if base not in route_bases:
            missing.append(
                f"{sync_name:40s} -> kept in KEEP_SYNC_METHODS but no matching route"
            )
        elif sync_name not in methods:
            missing.append(f"{sync_name:40s} -> kept in allow-list but not implemented")

    # Build the set of method names every route is expected to claim plus the
    # bespoke sync wrappers we deliberately retained.
    expected: set[str] = set(route_bases)
    expected.update(KEEP_SYNC_METHODS)

    orphans = sorted(methods - expected - standalone)
    return missing, orphans


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with code 1 when drift is found (CI-friendly).",
    )
    args = parser.parse_args()

    missing, orphans = find_drift()
    spec_count = len(collect_spec_routes())

    if missing:
        print(f"FAIL: {len(missing)} routes have incomplete client coverage:")
        for line in missing:
            print(f"  - {line}")
    else:
        kept = len(KEEP_SYNC_METHODS)
        print(
            f"OK: all {spec_count} routes have a typed async method; "
            f"{kept} CLI-bespoke sync wrappers in allow-list."
        )

    if orphans:
        print(f"\nWARN: {len(orphans)} client methods have no corresponding route:")
        for name in orphans:
            print(f"  - {name}")

    drift = bool(missing) or bool(orphans)
    if args.check and drift:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
