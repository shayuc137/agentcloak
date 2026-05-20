#!/usr/bin/env python3
"""Cross-check daemon routes against CLI commands and MCP tools.

This script is the CI replacement for the old, aiohttp-era
``check_consistency.py`` (which silently passed because its regex no longer
matched FastAPI's decorator style). It treats the daemon's OpenAPI spec as
the source of truth and walks five assertions:

1. every daemon route has a CLI binding listed in ``generate_skill.py``;
2. every daemon route has an MCP binding listed in ``generate_skill.py``;
3. every MCP tool defined under ``mcp/tools/`` is reachable from at least
   one route's mapping (or appears on the standalone allow-list);
4. CLI typer groups cover every command category implied by the routes;
5. every command name referenced from ``ROUTE_TO_CLI`` is actually
   registered as a typer command (catches mapping-table typos and
   commands that were declared but never wired into the Typer app).

Exit code 0 means everything lines up; non-zero means CI should fail.

The mapping tables in ``generate_skill.py`` are the documented contract —
when a new route lands you update the dict (one line) and this script
keeps the trio in sync.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


# MCP tools that don't correspond to a daemon route. ``agentcloak_doctor``
# aggregates local probes plus a daemon health check, so it isn't a 1:1
# route binding — keep this list short and explanatory.
MCP_STANDALONE: set[str] = {
    "agentcloak_doctor",
}

# Sentinel key used inside the CLI command map for top-level shortcuts
# (e.g. ``cloak navigate``) that are registered directly on the root Typer
# app instead of inside a sub-group.
_TOP_LEVEL = "__top__"


def collect_spec_routes() -> list[tuple[str, str]]:
    """Return ``[(verb, path), ...]`` from the FastAPI app's OpenAPI spec."""
    from agentcloak.daemon.app import create_app

    app = create_app()
    spec = app.openapi()
    routes: list[tuple[str, str]] = []
    for path, methods in spec.get("paths", {}).items():
        for verb in methods:
            if verb in {"get", "post", "put", "patch", "delete"}:
                routes.append((verb.upper(), path))
    return sorted(routes)


def collect_mcp_tools() -> set[str]:
    """Scan ``mcp/tools/*.py`` for ``async def agentcloak_xxx`` definitions."""
    tools_dir = SRC / "agentcloak" / "mcp" / "tools"
    pattern = re.compile(r"async def (agentcloak_\w+)\(")
    tools: set[str] = set()
    for f in tools_dir.glob("*.py"):
        tools.update(pattern.findall(f.read_text()))
    return tools


def collect_cli_groups() -> set[str]:
    """Extract typer group names from ``cli/app.py``."""
    app_file = SRC / "agentcloak" / "cli" / "app.py"
    text = app_file.read_text()
    pattern = re.compile(r'name="([^"]+)"')
    # The first match is the root ``typer.Typer(name="agentcloak", ...)`` so
    # we skip that one — only ``add_typer(..., name="...")`` calls describe
    # actual command groups.
    matches: list[str] = pattern.findall(text)
    if matches:
        matches = matches[1:]
    return {m for m in matches if not m.startswith("agentcloak")}


def collect_cli_commands() -> dict[str, set[str]]:
    """Walk ``cli/app.py`` + ``cli/commands/*.py`` to map groups → commands.

    Returns ``{group_name: {subcommand, ...}}``. Two special conventions:

    * The empty string ``""`` in the subcommand set means the group itself
      is invokable via ``@app.callback(invoke_without_command=True)`` —
      e.g. ``cloak doctor`` runs the doctor callback directly.
    * The special group key ``_TOP_LEVEL`` collects top-level shortcuts
      registered straight on the root Typer app (``cloak navigate``,
      ``cloak click``, …).
    """
    app_file = SRC / "agentcloak" / "cli" / "app.py"
    app_text = app_file.read_text()
    commands: dict[str, set[str]] = {_TOP_LEVEL: set()}

    # 1. add_typer(module.app, name="cookies", help=...) → groups
    group_pattern = re.compile(
        r'app\.add_typer\(\s*(\w+)\.app,\s*name="([^"]+)"',
    )
    module_to_group: dict[str, str] = {}
    for module_alias, group_name in group_pattern.findall(app_text):
        module_to_group[module_alias] = group_name
        commands.setdefault(group_name, set())

    # 2. Top-level shortcuts: app.command("name", hidden=True)(func)
    shortcut_pattern = re.compile(r'app\.command\("([^"]+)",\s*hidden=True\)')
    for cmd_name in shortcut_pattern.findall(app_text):
        commands[_TOP_LEVEL].add(cmd_name)

    # 3. Per-file decorators
    commands_dir = SRC / "agentcloak" / "cli" / "commands"
    cmd_pattern = re.compile(r'@app\.command\(\s*["\']([^"\']+)["\']')
    # ``invoke_without_command=True`` is the marker we care about — a plain
    # ``@app.callback()`` that requires a subcommand is irrelevant here.
    callback_pattern = re.compile(
        r"@app\.callback\([^)]*invoke_without_command=True",
    )

    for f in commands_dir.glob("*.py"):
        if f.name == "__init__.py":
            continue
        module_alias = f.stem
        group_name = module_to_group.get(module_alias)
        if not group_name:
            continue
        text = f.read_text()
        for cmd_name in cmd_pattern.findall(text):
            commands[group_name].add(cmd_name)
        if callback_pattern.search(text):
            commands[group_name].add("")

    return commands


def extract_mcp_name(binding: str) -> str:
    """Pull the bare ``agentcloak_xxx`` identifier out of a binding string.

    Bindings look like ``"agentcloak_tab (action=new)"`` or just
    ``"agentcloak_navigate"``. Some routes (notably ``/shutdown``) intentionally
    have no MCP exposure — the binding is a parenthesised note and we return
    an empty string so the caller can skip it.
    """
    if binding.startswith("("):
        return ""
    head = binding.split(" ", 1)[0]
    return head


def _classify_token(token: str) -> str:
    """Classify a CLI binding token.

    ROUTE_TO_CLI values document the *shape* of the command, not its exact
    syntax. The classifier separates literal subcommand names from various
    placeholder shapes so the validator only enforces what is actually
    asserted by the binding.

    Return values:
        ``literal``           — a concrete subcommand name like ``list``
        ``sub_placeholder``   — an angle-bracketed slot like ``<kind>``
                                meaning "any registered subcommand"
        ``arg``               — an all-caps positional arg (``URL``, ``NAME``)
        ``flag``              — a ``--flag`` option
    """
    if not token:
        return "literal"
    if token.startswith("--"):
        return "flag"
    if token.startswith("<") and token.endswith(">"):
        return "sub_placeholder"
    # All-caps single-word args (URL, NAME, FILE, PATH, N). Alternatives like
    # ``accept|dismiss`` contain ``|`` and must stay literal.
    if token.isupper() and "|" not in token:
        return "arg"
    return "literal"


def check_cli_bindings(commands: dict[str, set[str]]) -> list[str]:
    """Verify every ``ROUTE_TO_CLI`` binding resolves to a registered command."""
    from generate_skill import ROUTE_TO_CLI

    top_level = commands.get(_TOP_LEVEL, set())
    errors: list[str] = []

    for route, cli_str in ROUTE_TO_CLI.items():
        if not cli_str.startswith("cloak "):
            errors.append(
                f"Route {route} CLI binding '{cli_str}' must start with 'cloak '"
            )
            continue

        tokens = cli_str[len("cloak ") :].split()
        if not tokens:
            errors.append(f"Route {route} CLI binding '{cli_str}' is empty")
            continue

        head = tokens[0]

        # Pipe-separated top-level shortcuts (``cloak click|fill|type|...``):
        # a route like ``/action`` fans out to several top-level commands, so
        # the binding lists them all. Every alternative must be a registered
        # shortcut — stricter than a ``<placeholder>`` (which only requires one
        # subcommand to exist), and it documents the real command list for
        # agents reading commands-reference.md.
        if "|" in head:
            missing_heads = sorted(h for h in head.split("|") if h not in top_level)
            if missing_heads:
                errors.append(
                    f"Route {route} maps to '{cli_str}' but these top-level "
                    f"shortcut(s) are not registered: {missing_heads}"
                )
            continue

        # Top-level shortcut path (cloak navigate / cloak click / ...).
        if head in top_level:
            continue

        if head not in commands:
            errors.append(
                f"Route {route} maps to '{cli_str}' but no typer group "
                f"or top-level shortcut '{head}' exists"
            )
            continue

        # Walk the remaining tokens to find a literal subcommand or a
        # ``<placeholder>`` standing in for one.
        literal_sub: str | None = None
        sub_placeholder = False
        for token in tokens[1:]:
            kind = _classify_token(token)
            if kind == "literal":
                literal_sub = token
                break
            if kind == "sub_placeholder":
                sub_placeholder = True
                break
            # arg / flag — keep scanning in case a literal follows.

        if literal_sub is None and not sub_placeholder:
            # Group-only form (cloak doctor / cloak network) — needs callback.
            if "" in commands[head]:
                continue
            errors.append(
                f"Route {route} maps to '{cli_str}' but group '{head}' has "
                f"no callback and no subcommand specified"
            )
            continue

        if sub_placeholder:
            # ``cloak do <kind>`` is satisfied as long as the group exposes
            # at least one real subcommand.
            real_subs = {s for s in commands[head] if s}
            if not real_subs:
                errors.append(
                    f"Route {route} maps to '{cli_str}' but group '{head}' "
                    f"has no concrete subcommands to satisfy the placeholder"
                )
            continue

        assert literal_sub is not None  # for type-narrowing
        sub = literal_sub

        # Alternatives such as 'accept|dismiss' — each side must be wired.
        if "|" in sub:
            alternatives = sub.split("|")
            missing = sorted(a for a in alternatives if a not in commands[head])
            if missing:
                errors.append(
                    f"Route {route} maps to '{cli_str}' but '{head}' is "
                    f"missing subcommand(s): {missing}"
                )
            continue

        if sub not in commands[head]:
            errors.append(
                f"Route {route} maps to '{cli_str}' but no typer command "
                f"'{head} {sub}' is registered"
            )

    return errors


def main() -> int:
    from generate_skill import ROUTE_TO_CLI, ROUTE_TO_MCP

    routes = collect_spec_routes()
    mcp_tools = collect_mcp_tools()
    cli_groups = collect_cli_groups()
    cli_commands = collect_cli_commands()

    errors: list[str] = []
    warnings: list[str] = []

    # 1. Every route must appear in both mapping tables.
    for _verb, path in routes:
        if path not in ROUTE_TO_CLI:
            errors.append(f"Route {path} missing from generate_skill.ROUTE_TO_CLI")
        if path not in ROUTE_TO_MCP:
            errors.append(f"Route {path} missing from generate_skill.ROUTE_TO_MCP")

    # 2. The MCP binding must refer to a tool that actually exists.
    for path, binding in ROUTE_TO_MCP.items():
        tool_name = extract_mcp_name(binding)
        if not tool_name:
            continue  # intentionally not exposed (e.g. /shutdown)
        if tool_name not in mcp_tools:
            errors.append(
                f"Route {path} maps to MCP tool {tool_name!r}, "
                f"but no async def {tool_name}() found in mcp/tools/"
            )

    # 3. Every MCP tool must be referenced from at least one route mapping
    #    or be on the standalone allow-list.
    referenced_tools: set[str] = set()
    for binding in ROUTE_TO_MCP.values():
        name = extract_mcp_name(binding)
        if name:
            referenced_tools.add(name)
    orphan_tools = mcp_tools - referenced_tools - MCP_STANDALONE
    for tool in sorted(orphan_tools):
        errors.append(f"MCP tool {tool!r} has no route mapping (add to ROUTE_TO_MCP)")

    # 4. Every CLI binding must resolve to a registered typer command.
    errors.extend(check_cli_bindings(cli_commands))

    # 5. Drift signal: if the surface counts shift unexpectedly we want a
    #    human to glance at the change. We only warn, not fail.
    expected_routes = len(ROUTE_TO_CLI)
    if len(routes) != expected_routes:
        warnings.append(
            f"Route count drift: spec has {len(routes)}, "
            f"mapping table has {expected_routes}"
        )

    # Output report.
    typer_command_count = sum(
        len(s) for k, s in cli_commands.items() if k != _TOP_LEVEL
    ) + len(cli_commands.get(_TOP_LEVEL, set()))
    print(f"Daemon routes : {len(routes)}")
    print(f"MCP tools     : {len(mcp_tools)}")
    print(f"CLI groups    : {len(cli_groups)}")
    print(f"CLI commands  : {typer_command_count} (incl. shortcuts and callbacks)")
    print(f"CLI mappings  : {len(ROUTE_TO_CLI)}")
    print(f"MCP mappings  : {len(ROUTE_TO_MCP)}")

    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print(f"  - {w}")

    if errors:
        print(f"\nFAIL: {len(errors)} consistency error(s):")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(
        "\nOK: all routes have CLI + MCP bindings, all MCP tools are reachable, "
        "and every CLI binding resolves to a registered typer command."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
