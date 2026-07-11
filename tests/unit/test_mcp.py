"""Tests for MCP server — tool registration, response formatting, tool count."""

from __future__ import annotations

from typing import Any

import orjson
import pytest

from agentcloak.core.errors import AgentBrowserError
from agentcloak.mcp._format import error_json, render_envelope


class TestFormatHelpers:
    """The MCP format helpers route the daemon envelope through shared renderers."""

    def test_render_envelope_unwraps_data_and_calls_renderer(self) -> None:
        envelope = {
            "ok": True,
            "seq": 1,
            "data": {"url": "https://example.com", "title": "Example"},
        }
        rendered = render_envelope(envelope, lambda d: f"{d['url']} | {d['title']}")
        assert rendered == "https://example.com | Example"

    def test_render_envelope_promote_seq_copies_envelope_seq(self) -> None:
        # ``/snapshot`` keeps ``seq`` in the envelope only; ``promote_seq=True``
        # injects it into the data dict so the header renderer can see it.
        envelope = {"ok": True, "seq": 42, "data": {"tree_text": "ok"}}
        rendered = render_envelope(
            envelope, lambda d: f"seq={d['seq']}", promote_seq=True
        )
        assert rendered == "seq=42"

    def test_error_json_renders_three_field_envelope(self) -> None:
        exc = AgentBrowserError(
            error="navigation_failed",
            hint="Page not found",
            action="check URL",
        )
        rendered = error_json(exc)
        assert orjson.loads(rendered) == {
            "error": "navigation_failed",
            "hint": "Page not found",
            "action": "check URL",
        }


class TestMCPServerCreation:
    def test_create_server_returns_fastmcp(self) -> None:
        try:
            from mcp.server.fastmcp import FastMCP

            from agentcloak.mcp.server import create_server

            mcp = create_server()
            assert isinstance(mcp, FastMCP)
        except ImportError:
            pytest.skip("mcp package not installed")

    @pytest.mark.asyncio
    async def test_screenshot_uses_response_format_for_mime_and_forwards_wait(
        self,
    ) -> None:
        try:
            from mcp.server.fastmcp import FastMCP
        except ImportError:
            pytest.skip("mcp package not installed")
        from unittest.mock import AsyncMock

        from agentcloak.core.config import AgentcloakConfig
        from agentcloak.mcp.tools.navigation import register

        client = AsyncMock()
        client.config = AgentcloakConfig()
        client.screenshot.return_value = {
            "ok": True,
            "seq": 1,
            "data": {"base64": "cG5n", "size": 3, "format": "png"},
        }
        mcp = FastMCP("test")
        register(mcp, client)
        tool = mcp._tool_manager._tools["agentcloak_screenshot"]  # type: ignore[union-attr]

        result = await tool.fn(
            format=None,
            wait_selector="#ready",
            wait_timeout=1500,
        )

        assert result[0].mimeType == "image/png"
        assert "format=png" in result[1].text
        client.screenshot.assert_awaited_once_with(
            full_page=False,
            format=None,
            quality=50,
            wait_selector="#ready",
            wait_timeout=1500,
        )

    def test_tool_count_is_35(self) -> None:
        try:
            from agentcloak.mcp.server import create_server

            mcp = create_server()
            tools = mcp._tool_manager._tools  # type: ignore[union-attr]
            # 23 pre-7a tools + 6 from the 7a batch (console, download,
            # storage, clipboard, pdf, serve) + 4 from the 7b T1 batch
            # (script, route, headers, graphql) + 1 from 7b T2 (streaming)
            # + 1 from 7b T3 (debugger) + 1 from 7b T4 (sourcemap).
            assert len(tools) == 38, (
                f"Expected 38 tools, got {len(tools)}: {sorted(tools.keys())}"
            )
        except ImportError:
            pytest.skip("mcp package not installed")

    def test_tool_names_have_prefix(self) -> None:
        try:
            from agentcloak.mcp.server import create_server

            mcp = create_server()
            tools = mcp._tool_manager._tools  # type: ignore[union-attr]
            for name in tools:
                assert name.startswith("agentcloak_"), (
                    f"Tool '{name}' missing agentcloak_ prefix"
                )
        except ImportError:
            pytest.skip("mcp package not installed")

    def test_expected_tools_present(self) -> None:
        try:
            from agentcloak.mcp.server import create_server

            mcp = create_server()
            tools = mcp._tool_manager._tools  # type: ignore[union-attr]
            expected = {
                "agentcloak_navigate",
                "agentcloak_snapshot",
                "agentcloak_screenshot",
                "agentcloak_action",
                "agentcloak_evaluate",
                "agentcloak_fetch",
                "agentcloak_network",
                "agentcloak_capture_control",
                "agentcloak_capture_query",
                "agentcloak_status",
                "agentcloak_launch",
                "agentcloak_spell_run",
                "agentcloak_spell_list",
                "agentcloak_profile",
                "agentcloak_cookies",
                "agentcloak_doctor",
                "agentcloak_tab",
                "agentcloak_resume",
                "agentcloak_dialog",
                "agentcloak_wait",
                "agentcloak_upload",
                "agentcloak_frame",
                "agentcloak_bridge",
                "agentcloak_console",
                "agentcloak_download",
                "agentcloak_storage",
                "agentcloak_clipboard",
                "agentcloak_pdf",
                "agentcloak_serve",
                "agentcloak_script",
                "agentcloak_route",
                "agentcloak_headers",
                "agentcloak_graphql",
                "agentcloak_streaming",
                "agentcloak_debugger",
                "agentcloak_sourcemap",
                "agentcloak_profiler",
                "agentcloak_performance",
            }
            assert set(tools.keys()) == expected
        except ImportError:
            pytest.skip("mcp package not installed")


class TestResolveTier:
    def test_playwright_passthrough(self) -> None:
        from agentcloak.core.config import resolve_tier

        assert resolve_tier("playwright") == "playwright"

    def test_cloak_passthrough(self) -> None:
        from agentcloak.core.config import resolve_tier

        assert resolve_tier("cloak") == "cloak"

    def test_auto_resolves_to_cloak(self) -> None:
        from agentcloak.core.config import resolve_tier

        assert resolve_tier("auto") == "cloak"


class TestMCPSessionIsolation:
    """The MCP server uses its own per-process session, not the ambient one."""

    def test_session_id_is_pid_tagged(self) -> None:
        import os

        from agentcloak.mcp.server import _mcp_session_id

        assert _mcp_session_id() == f"mcp-{os.getpid()}"

    def test_session_id_overrides_claude_env(self, monkeypatch: Any) -> None:
        # Even inside a Claude Code session the MCP server must NOT inherit the
        # agent's session id — it gets its own browser.
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "agent-uuid-123")
        from agentcloak.mcp.server import _mcp_session_id

        assert _mcp_session_id() != "agent-uuid-123"
        assert _mcp_session_id().startswith("mcp-")

    def test_client_carries_mcp_session(self, monkeypatch: Any) -> None:
        try:
            from agentcloak.mcp.server import _mcp_session_id, create_server
        except ImportError:
            pytest.skip("mcp package not installed")
        from agentcloak.core.config import AgentcloakConfig

        captured: dict[str, str] = {}

        class _StubClient:
            def __init__(self, *, session_id: str | None = None, **_kw: Any) -> None:
                captured["session_id"] = session_id or ""

            @property
            def config(self) -> AgentcloakConfig:
                # Tool registration reads defaults off the client's config.
                return AgentcloakConfig()

        monkeypatch.setattr("agentcloak.client.DaemonClient", _StubClient)
        create_server()
        assert captured["session_id"] == _mcp_session_id()


class TestMCPExitHook:
    """On exit the server closes its own session; daemon shutdown is opt-in."""

    @staticmethod
    def _patch_config(monkeypatch: Any, *, stop_on_exit: bool) -> None:
        # ``_register_exit_hook`` does ``from agentcloak.core.config import
        # load_config`` at call time, so patching the function on the config
        # module is what the hook actually sees.
        from agentcloak.core import config as cfg_mod

        paths, cfg = cfg_mod.load_config()
        cfg.browser.stop_on_exit = stop_on_exit
        monkeypatch.setattr(cfg_mod, "load_config", lambda *_a, **_k: (paths, cfg))

    def test_exit_hook_closes_session_not_daemon_by_default(
        self, monkeypatch: Any
    ) -> None:
        import httpx

        from agentcloak.mcp import server as mcp_server

        self._patch_config(monkeypatch, stop_on_exit=False)
        calls: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            httpx, "post", lambda url, **kw: calls.append((url, kw)) or None
        )
        registered: list[Any] = []
        monkeypatch.setattr(
            mcp_server.atexit, "register", lambda fn: registered.append(fn)
        )
        mcp_server._register_exit_hook("mcp-test")
        assert len(registered) == 1
        registered[0]()  # run the hook

        paths = [c[0] for c in calls]
        assert any(p.endswith("/session/close") for p in paths)
        assert not any(p.endswith("/shutdown") for p in paths)
        # The close call targets the right session.
        close_call = next(c for c in calls if c[0].endswith("/session/close"))
        assert close_call[1]["json"]["session_id"] == "mcp-test"
        assert close_call[1]["headers"]["X-Agentcloak-Session"] == "mcp-test"

    def test_exit_hook_also_stops_daemon_when_configured(
        self, monkeypatch: Any
    ) -> None:
        import httpx

        from agentcloak.mcp import server as mcp_server

        self._patch_config(monkeypatch, stop_on_exit=True)
        calls: list[str] = []
        monkeypatch.setattr(httpx, "post", lambda url, **_kw: calls.append(url) or None)
        registered: list[Any] = []
        monkeypatch.setattr(
            mcp_server.atexit, "register", lambda fn: registered.append(fn)
        )
        mcp_server._register_exit_hook("mcp-test")
        registered[0]()

        assert any(p.endswith("/session/close") for p in calls)
        assert any(p.endswith("/shutdown") for p in calls)
