"""Management tools — launch, status, profile, spell, doctor, resume."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import orjson
from mcp.types import ToolAnnotations

from agentcloak.core.errors import AgentBrowserError
from agentcloak.core.text_renderers import (
    render_cdp_endpoint_text,
    render_cookies_export_text,
    render_cookies_import_text,
    render_doctor_detail_text,
    render_doctor_text,
    render_health_text,
    render_launch_text,
    render_profile_create_from_current_text,
    render_profile_create_text,
    render_profile_delete_text,
    render_profile_list_text,
    render_resume_text,
    render_spell_list_text,
    render_spell_run_text,
    render_tab_list_text,
    render_tab_op_text,
)
from agentcloak.mcp._format import error_json, format_call, render_envelope

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def _error_envelope(error: str, hint: str, action: str) -> str:
    """Return a local validation error in the same shape as a daemon error."""
    return orjson.dumps({"error": error, "hint": hint, "action": action}).decode()


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def agentcloak_status(
        query: Literal["health", "cdp_endpoint"] = "health",
    ) -> str:
        """Query daemon and browser status.

        Queries:
          health       — daemon status, stealth tier, current URL, capture state
          cdp_endpoint — CDP WebSocket URL (for jshookmcp browser_attach)

        Args:
            query: What to check — health or cdp_endpoint

        Returns:
            health: stealth_tier, current_url, current_title, seq.
            cdp_endpoint: ws_endpoint URL for CDP tools.
        """
        if query == "cdp_endpoint":
            return await format_call(client.cdp_endpoint(), render_cdp_endpoint_text)
        return await format_call(client.health(), render_health_text)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_cookies(
        action: Literal["export", "import"] = "export",
        url: str = "",
        cookies_json: str = "",
    ) -> str:
        """Manage browser cookies — export or import.

        Actions:
          export — get all cookies from the browser (local or bridge)
          import — inject cookies into the browser (supports httpOnly)

        Args:
            action: 'export' to get cookies, 'import' to inject cookies
            url: Filter exported cookies by URL (only for export)
            cookies_json: JSON array of cookie objects to import. Each cookie
                needs at least 'name', 'value', 'domain', 'path'. Example:
                '[{"name":"token","value":"abc","domain":".example.com","path":"/"}]'

        Returns:
            export: array of cookies with name, value, domain, path, httpOnly, etc.
            import: count of imported cookies.
        """
        if action == "export":
            return await format_call(
                client.cookies_export(url=url or None), render_cookies_export_text
            )

        if action == "import":
            if not cookies_json:
                return _error_envelope(
                    error="missing_cookies",
                    hint="cookies_json is required for import",
                    action="pass cookies as JSON array string",
                )
            # ``cookies_json`` is typed as ``str``; decode it once so the
            # daemon client always receives a list of dicts.
            cookies = orjson.loads(cookies_json)
            return await format_call(
                client.cookies_import(cookies=cookies), render_cookies_import_text
            )

        return _error_envelope(
            error="unknown_action",
            hint=f"Unknown action: {action}",
            action="use export or import",
        )

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=False, readOnlyHint=False))
    async def agentcloak_launch(
        tier: str = "",
        profile: str = "",
    ) -> str:
        """Hot-switch the daemon's active browser tier.

        Without restarting the daemon process, swap the browser context to
        another backend. Local tiers ('cloak', 'playwright') create or
        reuse a local browser; 'remote_bridge' waits for the Chrome
        extension to connect.

        Args:
            tier: Browser tier — 'auto' (default: cloak), 'playwright', 'cloak',
                  or 'remote_bridge'. Empty = use AGENTCLOAK_DEFAULT_TIER env.
            profile: Named browser profile for persistent cookies/state
                  (only meaningful for local tiers).

        Returns:
            JSON with active_tier, browser_ready, remote_connected,
            local_cached, profile.
        """
        from agentcloak.core.config import load_config

        _, cfg = load_config()
        actual_tier = tier or cfg.default_tier

        try:
            envelope = await client.launch(tier=actual_tier, profile=profile or None)
        except AgentBrowserError as exc:
            return error_json(exc)
        return render_envelope(envelope, render_launch_text)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_spell_run(
        name: str,
        args_json: str = "{}",
    ) -> str:
        """Run a registered spell by name.

        Spells are reusable automation commands for specific websites.
        Execution happens inside the daemon so the spell has full browser
        context (cookies, session, etc.).
        Use agentcloak_spell_list to see available spells.

        Args:
            name: Spell name as 'site/command' (e.g. 'httpbin/headers')
            args_json: Arguments as JSON object (e.g. '{"limit": 10}')

        Returns:
            JSON with the spell execution result.
        """
        parsed_args: dict[str, Any] = orjson.loads(args_json)
        return await format_call(
            client.spell_run(name=name, args=parsed_args), render_spell_run_text
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def agentcloak_spell_list() -> str:
        """List all registered spells.

        Returns:
            Plain text: one ``name | strategy | description`` per line, or
            ``no spells registered`` when none are loaded.
        """
        return await format_call(client.spell_list(), render_spell_list_text)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_profile(
        action: Literal["create", "list", "delete"] = "list",
        name: str = "",
        from_current: bool = False,
    ) -> str:
        """Manage browser profiles for persistent cookies and login state.

        Actions:
          list   — show all available profiles
          create — create a new named profile
          delete — delete an existing profile

        Args:
            action: Profile action — create, list, or delete
            name: Profile name (required for create/delete)
            from_current: For 'create' — copy cookies from the current browser
                session into the new profile. Useful to save login state.
                If name already exists, auto-appends suffix (-2, -3, ...).

        Returns:
            list: array of profile names.
            create/delete: confirmation.
            create with from_current: {profile, renamed, cookie_count}.
        """
        # Profile CRUD goes through the daemon API so name validation, path
        # traversal guards, and the from-current cookie writer all live in
        # ``ProfileService`` (one implementation, not three).
        if action == "list":
            return await format_call(client.profile_list(), render_profile_list_text)

        if not name:
            return _error_envelope(
                error="missing_name",
                hint="Profile name is required for create/delete",
                action="provide a name parameter",
            )

        if action == "create":
            if from_current:
                return await format_call(
                    client.profile_create_from_current(name=name),
                    render_profile_create_from_current_text,
                )
            return await format_call(
                client.profile_create(name=name), render_profile_create_text
            )

        if action == "delete":
            return await format_call(
                client.profile_delete(name=name), render_profile_delete_text
            )

        return _error_envelope(
            error="unknown_action",
            hint=f"Unknown: {action}",
            action="use create, list, or delete",
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_tab(
        action: Literal["list", "new", "close", "switch"] = "list",
        tab_id: int = -1,
        url: str = "",
    ) -> str:
        """Manage browser tabs — list, create, close, switch.

        Actions:
          list   — show all open tabs with id, url, title, active status
          new    — create a new tab (optionally navigate to url)
          close  — close tab by tab_id
          switch — switch active tab to tab_id

        Args:
            action: Tab action — list, new, close, or switch
            tab_id: Tab ID (required for close/switch, ignored for list)
            url: URL to navigate new tab to (only for 'new' action)

        Returns:
            list: array of tabs.
            new: created tab_id and url.
            close: confirmation of closed tab.
            switch: new active tab info.
        """
        if action == "list":
            return await format_call(client.tab_list(), render_tab_list_text)

        if action == "new":
            return await format_call(
                client.tab_new(url=url or None),
                lambda d: render_tab_op_text("new", d),
            )

        if action == "close":
            if tab_id < 0:
                return _error_envelope(
                    error="missing_tab_id",
                    hint="tab_id is required for close action",
                    action="provide a valid tab_id",
                )
            return await format_call(
                client.tab_close(tab_id),
                lambda d: render_tab_op_text("closed", d),
            )

        if action == "switch":
            if tab_id < 0:
                return _error_envelope(
                    error="missing_tab_id",
                    hint="tab_id is required for switch action",
                    action="provide a valid tab_id",
                )
            return await format_call(
                client.tab_switch(tab_id),
                lambda d: render_tab_op_text("switched to", d),
            )

        return _error_envelope(
            error="unknown_action",
            hint=f"Unknown tab action: {action}",
            action="use list, new, close, or switch",
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def agentcloak_doctor(fix: bool = False, detail: bool = False) -> str:
        """Run diagnostic checks on agentcloak installation.

        Runs the same probes the CLI ``doctor`` command runs (Python version,
        required packages, CloakBrowser binary, daemon connectivity) so both
        surfaces stay in sync. The shared logic lives in
        :class:`DiagnosticService`.

        The diagnostic runs in-process so it works even when the daemon is
        down — agents can call ``agentcloak_doctor`` to find out *why* the
        daemon won't start instead of getting a generic ``daemon_unreachable``
        error. We do still try the daemon probe (with ``auto_start=False`` so
        we don't accidentally launch one mid-diagnosis) and report what we
        find.

        Default output is the concise agent-friendly summary (summary line +
        runtime status). Pass ``detail=True`` for the legacy per-check list.

        Args:
            fix: When True, attempt in-process repairs (download CloakBrowser
                 binary, create data dir) and include a shell command for any
                 remaining system-level work. The shell command is *not*
                 executed — MCP doesn't have a way to escalate to sudo, so
                 the user runs it themselves.
            detail: When True, return the full per-check list instead of the
                 summary view.

        Returns:
            Plain text — concise summary by default, or the legacy list under
            ``detail=True``. On error, the standard three-field JSON envelope.
        """
        from agentcloak.client import DaemonClient
        from agentcloak.core.config import load_config
        from agentcloak.daemon.services import DiagnosticService

        paths, cfg = load_config()
        diagnostic = DiagnosticService()

        # Run the local checks directly — no HTTP round-trip, so this still
        # works when the daemon is down. Fix mode never executes the system
        # command from MCP because we can't elevate.
        if fix:
            report = diagnostic.doctor_fix(data_dir=paths.root, execute_sudo=False)
        else:
            report = diagnostic.doctor(data_dir=paths.root)

        # Daemon probe via a non-auto-start client. Using the shared
        # ``client`` instance would happily spawn a daemon mid-diagnosis,
        # masking the very problem the user asked about.
        probe = DaemonClient(
            host=cfg.daemon_host, port=cfg.daemon_port, auto_start=False
        )
        runtime: dict[str, Any] = {"daemon_ok": False}
        try:
            health = await probe.health()
            report["checks"].append(
                {
                    "name": "daemon",
                    "ok": True,
                    "level": "ok",
                    "detail": f"{cfg.daemon_host}:{cfg.daemon_port}",
                    "hint": "",
                }
            )
            runtime = {
                "daemon_ok": True,
                "browser_description": health.get("browser_description"),
                "headless": health.get("headless"),
                "humanize": health.get("humanize"),
                "proxy": health.get("proxy"),
                "active_profile": health.get("active_profile"),
            }
        except AgentBrowserError:
            # Daemon-down during a one-off ``doctor`` invocation is the
            # expected fresh-install state — auto-start spins it up on the
            # next real command. Report ``level="info"`` so the CLI / agent
            # render doesn't treat it as a broken environment.
            report["checks"].append(
                {
                    "name": "daemon",
                    "ok": True,
                    "level": "info",
                    "detail": f"{cfg.daemon_host}:{cfg.daemon_port}",
                    "hint": "not running (auto-starts on first command)",
                }
            )
        report["healthy"] = all(c["ok"] for c in report["checks"])
        report["runtime"] = runtime

        if detail:
            return render_doctor_detail_text(report)
        return render_doctor_text(report)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def agentcloak_resume() -> str:
        """Get session resume snapshot for recovering context after restart.

        Returns current URL, open tabs, last 5 actions, capture state,
        and stealth tier. Use this at the start of a new session to
        quickly restore working context.

        Returns:
            Plain text key:value block (``url:``, ``title:``, ``tier:``, tab
            count, last action). Use the CLI ``--json`` flag or await
            :meth:`DaemonClient.resume` directly for the structured payload.
        """
        return await format_call(client.resume(), render_resume_text)
