#!/usr/bin/env python3
"""Drift detection for the shared :class:`DaemonClient`.

For v0.2.0 we keep ``daemon_client.py`` hand-written — it's already typed,
tested, and carefully tuned for the sync + async dual surface. This script
is a *verification tool*: it builds the FastAPI app, reads the OpenAPI spec,
and confirms that every daemon route has a matching pair of typed methods
on the client. The richer "generate the client from the spec" workflow can
land in Phase 6 once the surface stabilises.

What it checks
--------------
* every route maps to ``<name>`` (async) and ``<name>_sync`` on
  :class:`DaemonClient`
* both surfaces actually exist on the class
* every public client method covers a route (otherwise the client is
  growing dead code)

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

    * ``missing`` — route paths whose ``<name>``/``<name>_sync`` pair is
      incomplete on the client.
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

    missing: list[str] = []
    for _verb, path in routes:
        base = route_to_method(path)
        async_name = base
        sync_name = f"{base}_sync"
        gaps: list[str] = []
        if async_name not in methods:
            gaps.append(async_name)
        if sync_name not in methods:
            gaps.append(sync_name)
        if gaps:
            missing.append(f"{path:40s} -> missing {', '.join(gaps)}")

    # Build the set of method names every route is expected to claim.
    expected: set[str] = set()
    for _verb, path in routes:
        base = route_to_method(path)
        expected.add(base)
        expected.add(f"{base}_sync")

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
        print(f"OK: all {spec_count} routes have matching async + sync methods.")

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
