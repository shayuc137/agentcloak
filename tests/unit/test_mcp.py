"""Tests for MCP server — tool registration, response formatting, tool count."""

from __future__ import annotations

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

    def test_tool_count_is_34(self) -> None:
        try:
            from agentcloak.mcp.server import create_server

            mcp = create_server()
            tools = mcp._tool_manager._tools  # type: ignore[union-attr]
            # 23 pre-7a tools + 6 from the 7a batch (console, download,
            # storage, clipboard, pdf, serve) + 4 from the 7b T1 batch
            # (script, route, headers, graphql) + 1 from 7b T2 (streaming).
            assert len(tools) == 34, (
                f"Expected 34 tools, got {len(tools)}: {sorted(tools.keys())}"
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
