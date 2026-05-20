#!/usr/bin/env python3
"""Generate ``skills/agentcloak/references/commands-reference.md`` from the
FastAPI OpenAPI spec.

The main ``SKILL.md`` keeps its hand-written quick-reference tables (we
want agents to pay the minimum token cost for the common path), but a
spec-derived ``commands-reference.md`` lives alongside it so agents can
pull the *full* command surface on demand — parameters, defaults, MCP
tool, route — without scraping the source tree.

The script is a one-way generator: load the FastAPI app, walk the OpenAPI
schema, render markdown. There is no template — the layout lives entirely
in this file. Re-running with ``--write`` updates the reference; CI runs
with ``--check`` and fails if the on-disk file drifts from what the spec
would produce.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUTPUT = ROOT / "skills" / "agentcloak" / "references" / "commands-reference.md"

# Path groups for ordering — keeps related commands adjacent in the rendered
# doc. Anything not listed falls into the "Other" bucket at the bottom so new
# routes still appear without script changes.
GROUPS: list[tuple[str, list[str]]] = [
    ("Health & Lifecycle", ["/health", "/shutdown", "/resume"]),
    (
        "Navigation & Observation",
        ["/navigate", "/snapshot", "/screenshot", "/network"],
    ),
    ("Interaction", ["/action", "/action/batch"]),
    ("Dialog & Wait", ["/dialog/status", "/dialog/handle", "/wait"]),
    (
        "Frames & Tabs",
        [
            "/frame/list",
            "/frame/focus",
            "/tabs",
            "/tab/new",
            "/tab/close",
            "/tab/switch",
        ],
    ),
    ("Content & Fetch", ["/evaluate", "/fetch"]),
    ("Upload", ["/upload"]),
    (
        "Capture",
        [
            "/capture/start",
            "/capture/stop",
            "/capture/status",
            "/capture/export",
            "/capture/analyze",
            "/capture/clear",
            "/capture/replay",
        ],
    ),
    (
        "Cookies & CDP",
        [
            "/cookies/export",
            "/cookies/import",
            "/cookies/set",
            "/cookies/clear",
            "/cookies/delete",
            "/cdp/endpoint",
        ],
    ),
    ("Console", ["/console", "/console/clear"]),
    ("Download", ["/download/url", "/download/wait", "/download/list"]),
    (
        "Storage",
        ["/storage/get", "/storage/set", "/storage/delete", "/storage/clear"],
    ),
    ("Clipboard", ["/clipboard/read", "/clipboard/write"]),
    ("PDF", ["/pdf"]),
    ("Serve", ["/serve/start", "/serve/stop", "/serve/status"]),
    ("Init Scripts", ["/script/add", "/script/remove", "/script/list"]),
    ("Route Interception", ["/route/add", "/route/remove", "/route/list"]),
    ("Header Injection", ["/emulation/headers"]),
    ("GraphQL", ["/graphql/introspect", "/graphql/query"]),
    ("Streaming", ["/ws/list", "/ws/messages", "/sse/messages"]),
    (
        "Profiles",
        [
            "/profile/list",
            "/profile/create",
            "/profile/delete",
            "/profile/create-from-current",
        ],
    ),
    ("Spells", ["/spell/list", "/spell/run"]),
    ("Bridge", ["/bridge/claim", "/bridge/finalize", "/bridge/token/reset"]),
]

# Route → CLI command mapping. The CLI command tree uses typer groups
# (``cloak browser navigate``, ``cloak do click``); most routes also have a
# top-level shortcut (``cloak navigate``, ``cloak click``). We list the form
# agents reach for most — the top-level shortcut where one exists, the group
# form otherwise (e.g. ``cloak do batch`` has no top-level alias).
ROUTE_TO_CLI: dict[str, str] = {
    "/health": "cloak doctor",
    "/shutdown": "cloak daemon stop",
    "/launch": "cloak launch",
    "/resume": "cloak resume",
    "/navigate": "cloak navigate URL",
    "/snapshot": "cloak snapshot",
    "/screenshot": "cloak screenshot",
    "/network": "cloak network",
    "/action": "cloak click|fill|type|scroll|hover|select|press|keydown|keyup N",
    "/action/batch": "cloak do batch --calls-file FILE",
    "/dialog/status": "cloak dialog status",
    "/dialog/handle": "cloak dialog accept|dismiss",
    "/wait": "cloak wait --<condition>",
    "/frame/list": "cloak frame list",
    "/frame/focus": "cloak frame focus",
    "/tabs": "cloak tab list",
    "/tab/new": "cloak tab new",
    "/tab/close": "cloak tab close",
    "/tab/switch": "cloak tab switch",
    "/evaluate": "cloak js evaluate",
    "/fetch": "cloak fetch URL",
    "/upload": "cloak upload --index N --file PATH",
    "/capture/start": "cloak capture start",
    "/capture/stop": "cloak capture stop",
    "/capture/status": "cloak capture status",
    "/capture/export": "cloak capture export",
    "/capture/analyze": "cloak capture analyze",
    "/capture/clear": "cloak capture clear",
    "/capture/replay": "cloak capture replay",
    "/cookies/export": "cloak cookies export",
    "/cookies/import": "cloak cookies import",
    "/cookies/set": "cloak cookies set",
    "/cookies/clear": "cloak cookies clear",
    "/cookies/delete": "cloak cookies delete NAME",
    "/console": "cloak console show",
    "/console/clear": "cloak console show --clear",
    "/download/url": "cloak download url URL",
    "/download/wait": "cloak download wait",
    "/download/list": "cloak download list",
    "/storage/get": "cloak storage get",
    "/storage/set": "cloak storage set KEY VALUE",
    "/storage/delete": "cloak storage delete KEY",
    "/storage/clear": "cloak storage clear",
    "/clipboard/read": "cloak clipboard read",
    "/clipboard/write": "cloak clipboard write TEXT",
    "/pdf": "cloak pdf",
    "/serve/start": "cloak serve start DIR",
    "/serve/stop": "cloak serve stop",
    "/serve/status": "cloak serve status",
    "/script/add": "cloak script add JS",
    "/script/remove": "cloak script remove ID",
    "/script/list": "cloak script list",
    "/route/add": "cloak route add PATTERN",
    "/route/remove": "cloak route remove",
    "/route/list": "cloak route list",
    "/ws/list": "cloak ws list",
    "/ws/messages": "cloak ws messages",
    "/sse/messages": "cloak sse messages",
    "/emulation/headers": "cloak emulation headers",
    "/graphql/introspect": "cloak graphql introspect URL",
    "/graphql/query": "cloak graphql query URL QUERY",
    "/cdp/endpoint": "cloak cdp endpoint",
    "/profile/list": "cloak profile list",
    "/profile/create": "cloak profile create NAME",
    "/profile/delete": "cloak profile delete NAME",
    "/profile/create-from-current": "cloak profile create NAME --from-current",
    "/spell/list": "cloak spell list",
    "/spell/run": "cloak spell run NAME",
    "/bridge/claim": "cloak bridge claim",
    "/bridge/finalize": "cloak bridge finalize",
    "/bridge/token/reset": "cloak bridge token --reset",
}

# Route → MCP tool. Several routes share a single tool that branches on an
# ``action`` parameter (cookies, profile, tab, capture); we list the tool so
# agents can find it, and note the dispatch arg in parentheses.
ROUTE_TO_MCP: dict[str, str] = {
    "/health": "agentcloak_status (query=health)",
    "/cdp/endpoint": "agentcloak_status (query=cdp_endpoint)",
    "/navigate": "agentcloak_navigate",
    "/snapshot": "agentcloak_snapshot",
    "/screenshot": "agentcloak_screenshot",
    "/network": "agentcloak_network",
    "/action": "agentcloak_action",
    # ``/action/batch`` is intentionally CLI-only today — MCP exposes
    # single-action ``agentcloak_action`` and lets the orchestrator drive the
    # loop. A batch MCP tool can be added when there's a clear need.
    "/action/batch": "(CLI-only — not exposed via MCP)",
    "/dialog/status": "agentcloak_dialog (action=status)",
    "/dialog/handle": "agentcloak_dialog (action=accept|dismiss)",
    "/wait": "agentcloak_wait",
    "/frame/list": "agentcloak_frame (action=list)",
    "/frame/focus": "agentcloak_frame (action=focus)",
    "/tabs": "agentcloak_tab (action=list)",
    "/tab/new": "agentcloak_tab (action=new)",
    "/tab/close": "agentcloak_tab (action=close)",
    "/tab/switch": "agentcloak_tab (action=switch)",
    "/evaluate": "agentcloak_evaluate",
    "/fetch": "agentcloak_fetch",
    "/upload": "agentcloak_upload",
    "/capture/start": "agentcloak_capture_control (action=start)",
    "/capture/stop": "agentcloak_capture_control (action=stop)",
    "/capture/status": "agentcloak_capture_query (action=status)",
    "/capture/export": "agentcloak_capture_query (action=export)",
    "/capture/analyze": "agentcloak_capture_query (action=analyze)",
    "/capture/clear": "agentcloak_capture_control (action=clear)",
    "/capture/replay": "agentcloak_capture_control (action=replay)",
    "/cookies/export": "agentcloak_cookies (action=export)",
    "/cookies/import": "agentcloak_cookies (action=import)",
    "/cookies/set": "agentcloak_cookies (action=set)",
    "/cookies/clear": "agentcloak_cookies (action=clear)",
    "/cookies/delete": "agentcloak_cookies (action=delete)",
    "/console": "agentcloak_console (action=show)",
    "/console/clear": "agentcloak_console (action=clear)",
    "/download/url": "agentcloak_download (action=url)",
    "/download/wait": "agentcloak_download (action=wait)",
    "/download/list": "agentcloak_download (action=list)",
    "/storage/get": "agentcloak_storage (action=get)",
    "/storage/set": "agentcloak_storage (action=set)",
    "/storage/delete": "agentcloak_storage (action=delete)",
    "/storage/clear": "agentcloak_storage (action=clear)",
    "/clipboard/read": "agentcloak_clipboard (action=read)",
    "/clipboard/write": "agentcloak_clipboard (action=write)",
    "/pdf": "agentcloak_pdf",
    "/serve/start": "agentcloak_serve (action=start)",
    "/serve/stop": "agentcloak_serve (action=stop)",
    "/serve/status": "agentcloak_serve (action=status)",
    "/script/add": "agentcloak_script (action=add)",
    "/script/remove": "agentcloak_script (action=remove)",
    "/script/list": "agentcloak_script (action=list)",
    "/route/add": "agentcloak_route (action=add)",
    "/route/remove": "agentcloak_route (action=remove)",
    "/route/list": "agentcloak_route (action=list)",
    "/ws/list": "agentcloak_streaming (action=ws_list)",
    "/ws/messages": "agentcloak_streaming (action=ws_messages)",
    "/sse/messages": "agentcloak_streaming (action=sse_messages)",
    "/emulation/headers": "agentcloak_headers",
    "/graphql/introspect": "agentcloak_graphql (action=introspect)",
    "/graphql/query": "agentcloak_graphql (action=query)",
    "/profile/list": "agentcloak_profile (action=list)",
    "/profile/create": "agentcloak_profile (action=create)",
    "/profile/delete": "agentcloak_profile (action=delete)",
    "/profile/create-from-current": (
        "agentcloak_profile (action=create, from_current=true)"
    ),
    "/spell/list": "agentcloak_spell_list",
    "/spell/run": "agentcloak_spell_run",
    "/bridge/claim": "agentcloak_bridge (action=claim)",
    "/bridge/finalize": "agentcloak_bridge (action=finalize)",
    "/bridge/token/reset": "agentcloak_bridge (action=token_reset)",
    "/resume": "agentcloak_resume",
    "/shutdown": "(daemon lifecycle — not exposed)",
    "/launch": "agentcloak_launch",
}


def load_spec() -> dict[str, Any]:
    """Build the FastAPI app and return its OpenAPI dict."""
    from agentcloak.daemon.app import create_app

    app = create_app()
    return app.openapi()


def _format_default(default: Any) -> str:
    """Pretty-print a JSON default value for inline use."""
    if default is None:
        return "null"
    if isinstance(default, bool):
        return "true" if default else "false"
    if isinstance(default, str):
        if not default:
            return '""'
        return f'"{default}"'
    return str(default)


def _resolve_schema(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve a single ``$ref`` if present, otherwise return the schema."""
    ref = schema.get("$ref")
    if not ref:
        return schema
    name = ref.split("/")[-1]
    schemas: dict[str, Any] = spec.get("components", {}).get("schemas", {})
    return schemas.get(name, {})


def _format_type(schema: dict[str, Any]) -> str:
    """Render a Pydantic JSON-Schema type as a compact human-readable label."""
    if "anyOf" in schema:
        parts = [_format_type(s) for s in schema["anyOf"]]
        return " | ".join(parts)
    if "enum" in schema:
        values = " | ".join(f'"{v}"' for v in schema["enum"])
        return f"enum({values})"
    t = schema.get("type")
    if t == "array":
        items = schema.get("items", {})
        return f"array<{_format_type(items)}>"
    if t == "object":
        return "object"
    return str(t) if t else "any"


def _params_from_request_body(spec: dict[str, Any], op: dict[str, Any]) -> list[str]:
    """Extract field rows from a POST route's request body schema."""
    body = op.get("requestBody", {})
    content = body.get("content", {}).get("application/json", {})
    schema_ref = content.get("schema", {})
    schema = _resolve_schema(spec, schema_ref)
    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = list(schema.get("required", []))

    rows: list[str] = []
    for name, prop in properties.items():
        prop_schema = _resolve_schema(spec, prop)
        type_str = _format_type(prop_schema)
        if "default" in prop:
            default_str = _format_default(prop["default"])
        elif "default" in prop_schema:
            default_str = _format_default(prop_schema["default"])
        elif name in required:
            default_str = "*required*"
        else:
            default_str = "—"
        desc = (prop.get("description") or prop_schema.get("description") or "").strip()
        rows.append(
            f"- `{name}` ({type_str}, default: {default_str})"
            + (f" — {desc}" if desc else "")
        )
    return rows


def _params_from_query(op: dict[str, Any]) -> list[str]:
    """Extract field rows from a GET route's query parameters."""
    rows: list[str] = []
    raw_params: list[dict[str, Any]] = op.get("parameters", []) or []
    for param in raw_params:
        if param.get("in") != "query":
            continue
        name: str = str(param.get("name", ""))
        schema: dict[str, Any] = param.get("schema", {}) or {}
        type_str = _format_type(schema)
        required: bool = bool(param.get("required", False))
        if "default" in schema:
            default_str = _format_default(schema["default"])
        elif required:
            default_str = "*required*"
        else:
            default_str = "—"
        desc_raw: str = str(param.get("description") or schema.get("description") or "")
        desc = desc_raw.strip()
        rows.append(
            f"- `{name}` ({type_str}, default: {default_str})"
            + (f" — {desc}" if desc else "")
        )
    return rows


def render_route(spec: dict[str, Any], path: str, verb: str, op: dict[str, Any]) -> str:
    """Render a single route entry as a markdown block."""
    lines: list[str] = []
    lines.append(f"### `{verb} {path}`")
    lines.append("")

    summary = (op.get("summary") or "").strip()
    # FastAPI synthesises summaries like "Handle Navigate" from the handler
    # function name when no explicit ``summary=`` is provided. Those add noise
    # without explaining anything, so we suppress them. Real summaries (from
    # docstrings or explicit decorator args) flow through unchanged.
    if summary and not summary.lower().startswith("handle "):
        lines.append(summary)
        lines.append("")

    cli = ROUTE_TO_CLI.get(path, "_(no CLI binding)_")
    mcp = ROUTE_TO_MCP.get(path, "_(no MCP binding)_")
    lines.append(f"- CLI: `{cli}`")
    lines.append(f"- MCP: `{mcp}`")

    if verb == "POST":
        params = _params_from_request_body(spec, op)
        if params:
            lines.append("- Body:")
            lines.extend(f"  {row}" for row in params)
    else:
        params = _params_from_query(op)
        if params:
            lines.append("- Query:")
            lines.extend(f"  {row}" for row in params)

    lines.append("")
    return "\n".join(lines)


def render_document(spec: dict[str, Any]) -> str:
    """Render the complete commands-reference.md content."""
    paths_dict: dict[str, Any] = spec.get("paths", {})

    # Build (path, verb, op) list in stable order driven by GROUPS.
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str, str, dict[str, Any]]] = []
    for group_name, group_paths in GROUPS:
        for p in group_paths:
            methods = paths_dict.get(p, {})
            for verb_lower, op in methods.items():
                if verb_lower not in {"get", "post"}:
                    continue
                key = (p, verb_lower)
                if key in seen:
                    continue
                seen.add(key)
                ordered.append((group_name, p, verb_lower.upper(), op))

    # Trailing "Other" group for any routes not pre-grouped.
    for p, methods in paths_dict.items():
        for verb_lower, op in methods.items():
            if verb_lower not in {"get", "post"}:
                continue
            key = (p, verb_lower)
            if key not in seen:
                seen.add(key)
                ordered.append(("Other", p, verb_lower.upper(), op))

    parts: list[str] = []
    parts.append(
        "<!-- AUTO-GENERATED by scripts/generate_skill.py. Do not edit by hand. -->"
        "\n<!-- Source: FastAPI OpenAPI spec from daemon/models/ + daemon/routes/ -->"
        "\n"
    )
    parts.append("# Command Reference")
    parts.append("")
    parts.append(
        "Complete catalog of every daemon route, with its CLI and MCP bindings. "
        "Generated from the FastAPI OpenAPI spec — the source of truth lives in "
        "`daemon/models/` (Pydantic request schemas) and `daemon/routes/` "
        "(route handlers)."
    )
    parts.append("")
    parts.append(
        "Read this file when you need full parameter detail. For the common path, "
        "the quick-reference tables in `SKILL.md` are usually enough."
    )
    parts.append("")

    current_group: str | None = None
    for group_name, path, verb, op in ordered:
        if group_name != current_group:
            parts.append(f"## {group_name}")
            parts.append("")
            current_group = group_name
        parts.append(render_route(spec, path, verb, op))

    parts.append("")
    parts.append(
        "_End of generated content. Updates flow from"
        " `daemon/models/` + `daemon/routes/`; run"
        " `python scripts/generate_skill.py --write`"
        " after changing routes._"
    )
    parts.append("")
    return "\n".join(parts)


def _auto_sync_skill_data() -> None:
    """Mirror skills/ → src/_skill_data/ after a --write so the wheel stays in sync."""
    import importlib
    import importlib.util

    sync_path = ROOT / "scripts" / "sync_skill_data.py"
    if not sync_path.is_file():
        return
    spec = importlib.util.spec_from_file_location("sync_skill_data", sync_path)
    if spec is None or spec.loader is None:
        return
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._sync(mod.SOURCE, mod.MIRROR)  # type: ignore[attr-defined]
    total = len(mod._iter_tracked_files(mod.SOURCE))  # type: ignore[attr-defined]
    print(f"OK: synced skill data mirror ({total} files).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--write",
        action="store_true",
        help="Overwrite commands-reference.md with the freshly generated content.",
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if on-disk content differs from the generated output (CI mode).",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print rendered content to stdout (debugging).",
    )
    args = parser.parse_args()

    spec = load_spec()
    rendered = render_document(spec)

    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    if args.write:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(rendered, encoding="utf-8")
        print(f"OK: wrote {OUTPUT.relative_to(ROOT)} ({len(rendered)} bytes).")
        _auto_sync_skill_data()
        return 0

    if args.check:
        if not OUTPUT.exists():
            print(f"FAIL: {OUTPUT.relative_to(ROOT)} does not exist.")
            print("Run `python scripts/generate_skill.py --write` to create it.")
            return 1
        on_disk = OUTPUT.read_text(encoding="utf-8")
        if on_disk != rendered:
            print(f"FAIL: {OUTPUT.relative_to(ROOT)} is out of sync with the spec.")
            print("Run `python scripts/generate_skill.py --write` to regenerate.")
            return 1
        print(f"OK: {OUTPUT.relative_to(ROOT)} is in sync with the OpenAPI spec.")
        return 0

    # Default: show diff hint without modifying anything.
    print(
        "Use --write to update the reference, --check for CI mode, --stdout to preview."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
