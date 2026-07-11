"""Spell commands — list, info, run, and scaffold spells."""

from __future__ import annotations

import asyncio
from typing import Any

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json, emit_envelope
from agentcloak.cli.output import error_from_exception, is_json_mode, value
from agentcloak.client import DaemonClient
from agentcloak.core.errors import AgentBrowserError
from agentcloak.core.text_renderers import render_spell_run_text
from agentcloak.spells.discovery import discover_spells
from agentcloak.spells.registry import get_registry

__all__ = ["app"]

app = typer.Typer()


def _ensure_discovered() -> None:
    if len(get_registry()) == 0:
        discover_spells()


@app.command("list")
def spell_list() -> None:
    """List all registered spells."""
    _ensure_discovered()
    registry = get_registry()
    if is_json_mode():
        spells: list[dict[str, Any]] = []
        for entry in registry.list_all():
            m = entry.meta
            spells.append(
                {
                    "site": m.site,
                    "name": m.name,
                    "full_name": m.full_name,
                    "strategy": m.strategy.value,
                    "access": m.access,
                    "description": m.description,
                    "domain": m.domain,
                    "needs_browser": m.needs_browser,
                    "mode": "pipeline" if entry.is_pipeline else "function",
                }
            )
        emit_envelope(
            {
                "ok": True,
                "seq": 0,
                "data": {"spells": spells, "count": len(spells)},
            }
        )
        return

    entries = list(registry.list_all())
    if not entries:
        value("no spells registered")
        return
    lines: list[str] = []
    for entry in entries:
        m = entry.meta
        lines.append(f"{m.full_name} | {m.strategy.value} | {m.description}")
    value("\n".join(lines))


@app.command("info")
def spell_info(
    name: str = typer.Argument(help="Spell name as site/command."),
) -> None:
    """Show detailed info for a spell."""
    _ensure_discovered()
    parts = name.split("/", 1)
    if len(parts) != 2:
        raise AgentBrowserError(
            error="invalid_spell_name",
            hint=f"Expected 'site/name' format, got '{name}'",
            action="use format like 'httpbin/headers'",
        )
    registry = get_registry()
    entry = registry.get(parts[0], parts[1])
    if entry is None:
        raise AgentBrowserError(
            error="spell_not_found",
            hint=f"No spell registered as '{name}'",
            action="run 'agentcloak spell list' to see available spells",
        )
    m = entry.meta
    info_data: dict[str, Any] = {
        "site": m.site,
        "name": m.name,
        "full_name": m.full_name,
        "strategy": m.strategy.value,
        "access": m.access,
        "description": m.description,
        "domain": m.domain,
        "needs_browser": m.needs_browser,
        "navigate_before": m.navigate_before,
        "mode": "pipeline" if entry.is_pipeline else "function",
        "args": [
            {
                "name": a.name,
                "type": a.type.__name__,
                "default": a.default,
                "required": a.required,
                "help": a.help,
            }
            for a in m.args
        ],
        "columns": list(m.columns) if m.columns else None,
    }
    if is_json_mode():
        emit_envelope({"ok": True, "seq": 0, "data": info_data})
        return
    lines = [
        f"# {m.full_name}",
        f"description: {m.description}",
        f"strategy: {m.strategy.value}",
        f"domain: {m.domain}",
        f"needs_browser: {m.needs_browser}",
    ]
    if m.args:
        lines.append("args:")
        for arg in m.args:
            req = "required" if arg.required else f"default={arg.default!r}"
            lines.append(f"  {arg.name}: {arg.type.__name__} ({req}) — {arg.help}")
    value("\n".join(lines))


@app.command("run")
def spell_run(
    name: str = typer.Argument(help="Spell name as site/command."),
    args: list[str] = typer.Argument(
        default=None, help="Spell arguments as key=value pairs."
    ),
) -> None:
    """Execute a spell."""
    _ensure_discovered()
    parts = name.split("/", 1)
    if len(parts) != 2:
        raise AgentBrowserError(
            error="invalid_spell_name",
            hint=f"Expected 'site/name' format, got '{name}'",
            action="use format like 'httpbin/headers'",
        )
    registry = get_registry()
    entry = registry.get(parts[0], parts[1])
    if entry is None:
        raise AgentBrowserError(
            error="spell_not_found",
            hint=f"No spell registered as '{name}'",
            action="run 'agentcloak spell list' to see available spells",
        )

    parsed_args = _parse_args(args or [])
    if entry.meta.needs_browser:
        try:
            dispatch_text_or_json(
                DaemonClient(),
                "POST",
                "/spell/run",
                json_body={"name": name, "args": parsed_args},
                renderer=render_spell_run_text,
            )
        except AgentBrowserError as exc:
            error_from_exception(exc)
        return

    # Local spell execution stays async (executor + pipeline are async); we
    # only need asyncio.run here, not a daemon round-trip.
    result = asyncio.run(_execute(entry, parsed_args))
    data = {"result": result}
    if is_json_mode():
        emit_envelope({"ok": True, "seq": 0, "data": data})
        return
    value(render_spell_run_text(data))


async def _execute(entry: Any, args: dict[str, Any]) -> Any:
    from agentcloak.spells.executor import execute_spell

    if entry.meta.needs_browser:
        raise AgentBrowserError(
            error="browser_required",
            hint=f"Spell '{entry.meta.full_name}' requires a browser "
            f"(strategy={entry.meta.strategy})",
            action="route browser-required spells through daemon /spell/run",
        )

    return await execute_spell(entry, args=args)


def _parse_args(raw: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in raw:
        if "=" in item:
            key, val = item.split("=", 1)
            result[key] = val
        else:
            result[item] = True
    return result


@app.command("scaffold")
def spell_scaffold(
    site: str = typer.Argument(help="Site name for generated spells."),
    domain: str = typer.Option("", help="Filter patterns by domain."),
) -> None:
    """Generate spell code from captured traffic analysis."""
    client = DaemonClient()
    analyze_result = client.capture_analyze_sync(domain=domain)

    patterns_data: list[dict[str, Any]] = analyze_result.get("data", {}).get(
        "patterns", []
    )

    if not patterns_data:
        if is_json_mode():
            emit_envelope(
                {
                    "ok": True,
                    "seq": 0,
                    "data": {"code": "", "message": "No API patterns found."},
                }
            )
            return
        value("no API patterns found in captured traffic")
        return

    from agentcloak.core.types import Strategy
    from agentcloak.spells.analyzer import EndpointPattern
    from agentcloak.spells.generator import generate_spells

    patterns: list[EndpointPattern] = []
    for p in patterns_data:
        patterns.append(
            EndpointPattern(
                method=p.get("method", "GET"),
                path=p.get("path", "/"),
                domain=p.get("domain", ""),
                call_count=p.get("call_count", 0),
                query_params=p.get("query_params", []),
                status_codes={int(k): v for k, v in p.get("status_codes", {}).items()},
                auth_headers=p.get("auth_headers", []),
                content_type=p.get("content_type", ""),
                category=p.get("category", "read"),
                strategy=Strategy(p.get("strategy", "public")),
            )
        )

    code = generate_spells(site, patterns)
    if is_json_mode():
        emit_envelope(
            {
                "ok": True,
                "seq": 0,
                "data": {"code": code, "pattern_count": len(patterns)},
            }
        )
        return
    value(code)
