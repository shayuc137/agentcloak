"""Extra-header injection — base ``set_extra_headers`` (7b T1.2).

Exercised through ``RemoteBridgeContext`` (cheapest concrete subclass) with the
``_set_extra_headers_impl`` atom patched, mirroring the T0 CDP-base tests.
Covers set / dump / clear and the closed-browser guard.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcloak.browser.remote_ctx import RemoteBridgeContext
from agentcloak.core.errors import BackendError


def _make_ctx() -> RemoteBridgeContext:
    ws = MagicMock()
    ws.closed = False
    return RemoteBridgeContext(bridge_ws=ws)


class TestSetHeaders:
    @pytest.mark.asyncio
    async def test_set_delegates_to_impl_and_tracks(self) -> None:
        ctx = _make_ctx()
        ctx._set_extra_headers_impl = AsyncMock()  # type: ignore[method-assign]

        result = await ctx.set_extra_headers({"Authorization": "Bearer t"})

        ctx._set_extra_headers_impl.assert_awaited_once_with(
            {"Authorization": "Bearer t"}
        )
        assert result["count"] == 1
        assert ctx.list_extra_headers() == {"Authorization": "Bearer t"}

    @pytest.mark.asyncio
    async def test_set_replaces_previous(self) -> None:
        ctx = _make_ctx()
        ctx._set_extra_headers_impl = AsyncMock()  # type: ignore[method-assign]

        await ctx.set_extra_headers({"A": "1", "B": "2"})
        await ctx.set_extra_headers({"C": "3"})

        # Replace semantics, not merge.
        assert ctx.list_extra_headers() == {"C": "3"}

    @pytest.mark.asyncio
    async def test_empty_dict_clears(self) -> None:
        ctx = _make_ctx()
        ctx._set_extra_headers_impl = AsyncMock()  # type: ignore[method-assign]

        await ctx.set_extra_headers({"A": "1"})
        result = await ctx.set_extra_headers({})

        assert result["count"] == 0
        assert ctx.list_extra_headers() == {}

    @pytest.mark.asyncio
    async def test_returned_headers_are_a_copy(self) -> None:
        ctx = _make_ctx()
        ctx._set_extra_headers_impl = AsyncMock()  # type: ignore[method-assign]

        result = await ctx.set_extra_headers({"A": "1"})
        # Mutating the returned dict must not corrupt internal state.
        result["headers"]["A"] = "tampered"
        assert ctx.list_extra_headers() == {"A": "1"}


class TestGuards:
    @pytest.mark.asyncio
    async def test_blocked_when_browser_closed(self) -> None:
        ctx = _make_ctx()
        ctx._set_extra_headers_impl = AsyncMock()  # type: ignore[method-assign]
        ctx._browser_closed = True

        with pytest.raises(BackendError) as excinfo:
            await ctx.set_extra_headers({"A": "1"})
        assert excinfo.value.error == "browser_closed"
        ctx._set_extra_headers_impl.assert_not_awaited()
