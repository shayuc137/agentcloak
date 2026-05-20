"""RouteManager + RouteRule — network route interception (7b T1.3).

Two layers under test:

* :class:`RouteRule` matching — pure logic (URL glob/substring + optional
  resource-type / method filters), no ctx needed.
* :class:`RouteManager` bookkeeping — add/remove/list/match plus the
  delegation to the backend ``_route_*_impl`` atoms (mocked).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest

from agentcloak.browser.managers.route_manager import RouteManager, RouteRule

if TYPE_CHECKING:
    from agentcloak.browser.base import BrowserContextBase


def _make_mgr() -> tuple[RouteManager, AsyncMock, AsyncMock]:
    ctx = AsyncMock()
    ctx._route_add_impl = AsyncMock()
    ctx._route_remove_impl = AsyncMock()
    mgr = RouteManager(cast("BrowserContextBase", ctx))
    return mgr, ctx._route_add_impl, ctx._route_remove_impl


class TestRuleMatching:
    def test_substring_match_without_star(self) -> None:
        rule = RouteRule(pattern="api/login", action="abort")
        assert rule.matches(
            "https://x.com/api/login?next=1", resource_type=None, method=None
        )
        assert not rule.matches(
            "https://x.com/api/logout", resource_type=None, method=None
        )

    def test_glob_star_match(self) -> None:
        rule = RouteRule(pattern="https://*/track", action="abort")
        assert rule.matches(
            "https://ads.example/track", resource_type=None, method=None
        )
        assert not rule.matches(
            "https://ads.example/track/extra", resource_type=None, method=None
        )

    def test_resource_type_filter(self) -> None:
        rule = RouteRule(pattern="*", action="abort", resource_type="image")
        assert rule.matches("https://x/a.png", resource_type="image", method=None)
        assert not rule.matches("https://x/a.js", resource_type="script", method=None)

    def test_method_filter_case_insensitive(self) -> None:
        rule = RouteRule(pattern="*", action="abort", method="post")
        assert rule.matches("https://x/api", resource_type=None, method="POST")
        assert not rule.matches("https://x/api", resource_type=None, method="GET")

    def test_filter_ignored_when_request_field_missing(self) -> None:
        # A rule with a method filter still matches when the caller can't supply
        # the request method (None) — we don't reject on unknown.
        rule = RouteRule(pattern="*", action="abort", method="POST")
        assert rule.matches("https://x/api", resource_type=None, method=None)


class TestRuleSerialisation:
    def test_to_dict_omits_unset_optionals(self) -> None:
        rule = RouteRule(pattern="*", action="continue")
        assert rule.to_dict() == {"pattern": "*", "action": "continue"}

    def test_to_dict_includes_set_fields(self) -> None:
        rule = RouteRule(
            pattern="*/api",
            action="fulfill",
            method="GET",
            status=503,
            content_type="application/json",
            body="{}",
        )
        d = rule.to_dict()
        assert d["status"] == 503
        assert d["content_type"] == "application/json"
        assert d["body"] == "{}"
        assert d["method"] == "GET"


class TestManagerCrud:
    @pytest.mark.asyncio
    async def test_add_appends_and_calls_impl(self) -> None:
        mgr, add_impl, _ = _make_mgr()
        rule = RouteRule(pattern="*/x", action="abort")

        await mgr.add(rule)

        assert mgr.list_rules() == [rule]
        add_impl.assert_awaited_once_with(rule)

    @pytest.mark.asyncio
    async def test_remove_by_pattern(self) -> None:
        mgr, _, remove_impl = _make_mgr()
        await mgr.add(RouteRule(pattern="a", action="abort"))
        await mgr.add(RouteRule(pattern="b", action="abort"))

        removed = await mgr.remove("a")

        assert removed == 1
        assert [r.pattern for r in mgr.list_rules()] == ["b"]
        remove_impl.assert_awaited_once_with("a")

    @pytest.mark.asyncio
    async def test_remove_all_with_none(self) -> None:
        mgr, _, remove_impl = _make_mgr()
        await mgr.add(RouteRule(pattern="a", action="abort"))
        await mgr.add(RouteRule(pattern="b", action="abort"))

        removed = await mgr.remove(None)

        assert removed == 2
        assert mgr.list_rules() == []
        remove_impl.assert_awaited_once_with(None)

    @pytest.mark.asyncio
    async def test_remove_updates_list_before_impl(self) -> None:
        """The backend teardown must observe the post-removal rule set.

        RemoteBridge decides whether to fully disable ``Fetch`` based on the
        live rule count, so the list has to be updated first.
        """
        mgr, _, remove_impl = _make_mgr()
        await mgr.add(RouteRule(pattern="only", action="abort"))

        seen_count: list[int] = []
        remove_impl.side_effect = lambda _p: seen_count.append(len(mgr.list_rules()))

        await mgr.remove("only")

        assert seen_count == [0]  # list already empty when impl runs


class TestManagerMatch:
    @pytest.mark.asyncio
    async def test_first_match_wins(self) -> None:
        mgr, _, _ = _make_mgr()
        await mgr.add(RouteRule(pattern="*/api", action="abort"))
        await mgr.add(RouteRule(pattern="*/api", action="fulfill"))

        match = mgr.match("https://x/api")

        assert match is not None
        assert match.action == "abort"  # insertion order

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self) -> None:
        mgr, _, _ = _make_mgr()
        await mgr.add(RouteRule(pattern="*/api", action="abort"))
        assert mgr.match("https://x/static.css") is None


class TestTabSwitchReplay:
    @pytest.mark.asyncio
    async def test_replay_reissues_all_rules(self) -> None:
        mgr, add_impl, _ = _make_mgr()
        await mgr.add(RouteRule(pattern="a", action="abort"))
        await mgr.add(RouteRule(pattern="b", action="continue"))
        add_impl.reset_mock()

        await mgr.on_tab_switched()

        assert add_impl.await_count == 2
