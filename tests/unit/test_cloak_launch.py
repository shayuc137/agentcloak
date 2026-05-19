"""CloakContext launch parameter assembly tests.

Until now :file:`test_stealth.py` only patched ``_ensure_cloakbrowser`` to
verify the import-error path. The actual parameter assembly inside
:func:`launch_cloak` — proxy routing, ``extra_args`` ordering, persistent
profile launching — had no regression net.

These tests stub the entire ``cloakbrowser`` module via ``sys.modules``,
trigger :func:`launch_cloak`, and assert on the kwargs passed to the
launcher. We never actually start a browser.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _install_cloak_mock(launch_kwargs: list[dict[str, Any]]) -> Any:
    """Install a ``cloakbrowser`` stub that records launch_async kwargs.

    Returns the mock module so the test can inspect / extend it. The
    captured kwargs go into the shared ``launch_kwargs`` list so each
    test reads the most recent call without juggling closures.
    """
    cb = MagicMock()

    async def fake_launch_async(**kwargs: Any) -> Any:
        launch_kwargs.append(kwargs)
        # Return a mock browser whose new_context() yields a mock with
        # new_page() — enough for launch_cloak to finish without errors.
        page = MagicMock()
        page.on = MagicMock()
        new_ctx = MagicMock()
        new_ctx.new_page = AsyncMock(return_value=page)
        browser = MagicMock()
        browser.new_context = AsyncMock(return_value=new_ctx)
        return browser

    cb.launch_async = fake_launch_async
    sys.modules["cloakbrowser"] = cb
    return cb


@pytest.fixture
def cloak_stub() -> Any:
    """Provide a fresh cloakbrowser stub per test and restore on teardown."""
    saved = sys.modules.get("cloakbrowser")
    kwargs_log: list[dict[str, Any]] = []
    _install_cloak_mock(kwargs_log)
    yield kwargs_log
    if saved is None:
        sys.modules.pop("cloakbrowser", None)
    else:
        sys.modules["cloakbrowser"] = saved


# ---------------------------------------------------------------------------
# B6.1: proxy → browser_proxy → launch.proxy
# ---------------------------------------------------------------------------


class TestProxyParameter:
    @pytest.mark.asyncio
    async def test_browser_proxy_translated_to_launch_proxy_dict(
        self, cloak_stub: list[dict[str, Any]]
    ) -> None:
        """``browser_proxy=...`` → CloakBrowser ``proxy={"server": ...}``."""
        from agentcloak.browser.cloak_ctx import launch_cloak

        await launch_cloak(
            headless=True,
            browser_proxy="socks5://127.0.0.1:1080",
        )

        assert len(cloak_stub) == 1
        kwargs = cloak_stub[0]
        # Playwright's launch API accepts the proxy as a dict.
        assert kwargs.get("proxy") == {"server": "socks5://127.0.0.1:1080"}

    @pytest.mark.asyncio
    async def test_no_proxy_omits_proxy_kwarg(
        self, cloak_stub: list[dict[str, Any]]
    ) -> None:
        """No ``browser_proxy`` → no ``proxy`` key in launch kwargs."""
        from agentcloak.browser.cloak_ctx import launch_cloak

        await launch_cloak(headless=True)

        assert len(cloak_stub) == 1
        # Empty / None should *not* materialise a proxy entry — otherwise
        # CloakBrowser would try to route through an empty server string.
        assert "proxy" not in cloak_stub[0]


# ---------------------------------------------------------------------------
# B6.2: extra_args → appended to launch args (after agentcloak defaults)
# ---------------------------------------------------------------------------


class TestExtraArgs:
    @pytest.mark.asyncio
    async def test_extra_args_appended_after_defaults(
        self, cloak_stub: list[dict[str, Any]]
    ) -> None:
        """User flags must land after the agentcloak-managed defaults.

        The contract is documented in the launch_cloak docstring:
        "``extra_args`` lands at the end so user-supplied flags can
        override any defaults agentcloak set". Chromium honours the last
        occurrence, so position matters.
        """
        from agentcloak.browser.cloak_ctx import launch_cloak

        await launch_cloak(
            headless=True,
            extra_args=["--lang=ja-JP", "--disable-features=DnsOverHttps"],
        )

        assert len(cloak_stub) == 1
        args = cloak_stub[0].get("args", [])
        # The CDP debugging-port flag is always set; user args must come
        # after it in the list.
        cdp_idx = next(
            (i for i, a in enumerate(args) if a.startswith("--remote-debugging-port=")),
            None,
        )
        assert cdp_idx is not None, "CDP port flag missing from launch args"
        lang_idx = args.index("--lang=ja-JP")
        doh_idx = args.index("--disable-features=DnsOverHttps")
        assert lang_idx > cdp_idx
        assert doh_idx > cdp_idx


# ---------------------------------------------------------------------------
# B6.3: humanize flag forwarded
# ---------------------------------------------------------------------------


class TestHumanize:
    @pytest.mark.asyncio
    async def test_humanize_forwarded_to_launcher(
        self, cloak_stub: list[dict[str, Any]]
    ) -> None:
        """``humanize=False`` reaches the CloakBrowser launcher untouched."""
        from agentcloak.browser.cloak_ctx import launch_cloak

        await launch_cloak(headless=True, humanize=False)
        assert cloak_stub[0].get("humanize") is False

    @pytest.mark.asyncio
    async def test_headless_flag_forwarded(
        self, cloak_stub: list[dict[str, Any]]
    ) -> None:
        from agentcloak.browser.cloak_ctx import launch_cloak

        await launch_cloak(headless=False)
        assert cloak_stub[0].get("headless") is False


# ---------------------------------------------------------------------------
# B6.4: DoH flag passes through via extra_args
# ---------------------------------------------------------------------------


class TestDnsOverHttps:
    """The DoH flag is owned by the daemon layer; ``launch_cloak`` only
    forwards whatever is in ``extra_args``. Document that contract here
    so a future refactor that moves the flag down can't silently break it.
    """

    @pytest.mark.asyncio
    async def test_disable_doh_arg_passes_through(
        self, cloak_stub: list[dict[str, Any]]
    ) -> None:
        from agentcloak.browser.cloak_ctx import launch_cloak

        await launch_cloak(
            headless=True,
            extra_args=["--disable-features=DnsOverHttps"],
        )

        args = cloak_stub[0].get("args", [])
        assert "--disable-features=DnsOverHttps" in args
