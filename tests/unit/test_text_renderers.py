"""Tests for daemon text renderers — header layout and token estimate.

The renderers are pure functions so they're cheap to exercise in isolation;
this file focuses on the bits other suites don't already cover transitively
(text-mode header assembly, the chars/4 token hint).
"""

from __future__ import annotations

from agentcloak.core.text_renderers import (
    _format_tok_estimate,  # pyright: ignore[reportPrivateUsage]
    _render_snapshot_header,  # pyright: ignore[reportPrivateUsage]
    render_health_text,
    render_resume_text,
)


class TestFormatTokEstimate:
    """chars/4 token hint formatting."""

    def test_empty_tree(self) -> None:
        assert _format_tok_estimate("") == "~0 tok"

    def test_sub_1k_uses_bare_count(self) -> None:
        # 200 chars → 50 tokens, well under the 1K cutoff
        text = "x" * 200
        assert _format_tok_estimate(text) == "~50 tok"

    def test_at_1k_switches_to_k_format(self) -> None:
        # 4000 chars → 1000 tokens → 1.0K (just crosses the boundary)
        text = "x" * 4000
        assert _format_tok_estimate(text) == "~1.0K tok"

    def test_large_tree_uses_one_decimal(self) -> None:
        # 7200 chars → 1800 tokens → 1.8K
        text = "x" * 7200
        assert _format_tok_estimate(text) == "~1.8K tok"

    def test_no_dependency_on_tokenizer(self) -> None:
        # Sanity check: result is deterministic and depends only on len.
        for length in (0, 1, 100, 3999, 4001, 99999):
            text = "a" * length
            result = _format_tok_estimate(text)
            assert result.startswith("~")
            assert result.endswith(" tok")


class TestRenderSnapshotHeader:
    """Header assembly: title | url | nodes (interactive) | seq | tok."""

    def test_basic_header_includes_tok(self) -> None:
        # Minimal payload — empty tree gives a ``~0 tok`` suffix.
        data = {
            "title": "Hacker News",
            "url": "https://news.ycombinator.com/",
            "total_nodes": 424,
            "total_interactive": 230,
            "seq": 1,
            "tree_text": "",
        }
        header = _render_snapshot_header(data)
        assert header.startswith("# Hacker News | https://news.ycombinator.com/")
        assert "424 nodes (230 interactive)" in header
        assert "seq=1" in header
        assert header.endswith("~0 tok")

    def test_header_with_realistic_tree_size(self) -> None:
        # 7000-char tree → 1750 tokens → 1.8K
        data = {
            "title": "Page",
            "url": "https://example.com/",
            "total_nodes": 100,
            "total_interactive": 50,
            "seq": 3,
            "tree_text": "x" * 7000,
        }
        header = _render_snapshot_header(data)
        assert header.endswith("~1.8K tok")

    def test_truncation_suffix_appears_before_tok(self) -> None:
        # ``showing 1-N`` and ``~NK tok`` both appended at the tail; agents
        # rely on header ending with the tok hint so it stays last.
        data = {
            "title": "Big Page",
            "url": "https://example.com/big",
            "total_nodes": 500,
            "total_interactive": 100,
            "seq": 2,
            "truncated_at": 80,
            "tree_text": "x" * 4000,
        }
        header = _render_snapshot_header(data)
        assert "showing 1-80" in header
        # tok hint must be after the showing hint
        assert header.index("showing 1-80") < header.index("~1.0K tok")
        assert header.endswith("~1.0K tok")

    def test_diff_info_then_showing_then_tok(self) -> None:
        # Composite header — diff + showing + tok in canonical order.
        data = {
            "title": "Diff Test",
            "url": "https://example.com/",
            "total_nodes": 50,
            "total_interactive": 10,
            "seq": 5,
            "diff": True,
            "diff_counts": {"added": 1, "changed": 2, "removed": 3},
            "truncated_at": 40,
            "tree_text": "y" * 800,
        }
        header = _render_snapshot_header(data)
        assert "| diff: +1 ~2 -3" in header
        assert "| showing 1-40" in header
        assert header.endswith("~200 tok")


class TestRenderHealthTextPageValid:
    """``render_health_text`` exposes ``page_valid`` so agents see silent-fail risk."""

    def test_healthy_page_valid_default_omits_invalid_marker(self) -> None:
        # The happy path stays uncluttered — only the False case surfaces.
        data = {
            "stealth_tier": "cloak",
            "browser_ready": True,
            "seq": 1,
            "page_valid": True,
        }
        rendered = render_health_text(data)
        assert "page: invalid" not in rendered

    def test_invalid_page_after_failed_navigate_surfaces_marker(self) -> None:
        data = {
            "stealth_tier": "cloak",
            "browser_ready": True,
            "seq": 5,
            "page_valid": False,
        }
        rendered = render_health_text(data)
        assert "page: invalid" in rendered

    def test_invalid_marker_suppressed_when_browser_not_ready(self) -> None:
        # If the browser isn't ready the page-valid signal has no agent-
        # actionable meaning (no page exists yet) — suppress the noise.
        data = {
            "stealth_tier": "cloak",
            "browser_ready": False,
            "seq": 0,
            "page_valid": False,
        }
        rendered = render_health_text(data)
        assert "page: invalid" not in rendered


class TestRenderResumeTextPageValid:
    """``render_resume_text`` flags ``page_valid=False`` prominently."""

    def test_valid_page_omits_invalid_marker(self) -> None:
        data = {
            "url": "https://example.com",
            "stealth_tier": "cloak",
            "page_valid": True,
        }
        rendered = render_resume_text(data)
        assert "INVALID" not in rendered

    def test_invalid_page_surfaces_explicit_warning(self) -> None:
        data = {
            "url": "https://example.com",
            "stealth_tier": "cloak",
            "page_valid": False,
        }
        rendered = render_resume_text(data)
        # All caps INVALID so the resume output draws attention; agents
        # parsing the text form shouldn't need to know about the field name.
        assert "INVALID" in rendered
        assert "last navigate failed" in rendered.lower()

    def test_missing_page_valid_field_omits_marker(self) -> None:
        # Older daemon snapshots without the field shouldn't trigger the warning.
        data = {"url": "https://example.com", "stealth_tier": "cloak"}
        rendered = render_resume_text(data)
        assert "INVALID" not in rendered
