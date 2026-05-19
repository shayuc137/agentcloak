"""Browser backend implementations behind a unified ABC base class."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentcloak.browser.base import BrowserContextBase
from agentcloak.browser.state import BrowserState, ElementRef, PageSnapshot, TabInfo
from agentcloak.core.types import StealthTier

if TYPE_CHECKING:
    from pathlib import Path

    from agentcloak.core.config import BrowserConfig

__all__ = [
    "BrowserContextBase",
    "BrowserState",
    "ElementRef",
    "PageSnapshot",
    "StealthTier",
    "TabInfo",
    "create_context",
]


async def create_context(
    *,
    tier: StealthTier = StealthTier.CLOAK,
    headless: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    profile_dir: Path | None = None,
    humanize: bool = False,
    extensions: list[str] | None = None,
    proxy_url: str | None = None,
    browser_proxy: str | None = None,
    extra_args: list[str] | None = None,
    browser_config: BrowserConfig | None = None,
) -> BrowserContextBase:
    """Factory: create a browser context for the given stealth tier.

    ``proxy_url`` and ``browser_proxy`` solve two different problems and
    are intentionally separate:

    * ``proxy_url`` is the httpcloak local TLS-fingerprint proxy used by
      :py:meth:`fetch`. It lives on ``localhost`` and is wired by the
      daemon when stealth is on; callers that don't run httpcloak pass
      ``None``.
    * ``browser_proxy`` is the user-configured upstream proxy
      (``socks5://...``, ``http://...``) handed straight to Chromium so
      every browser request — page loads, XHRs, WebSockets — egresses
      through that hop. Populated from ``[browser] proxy`` /
      ``AGENTCLOAK_PROXY``.

    ``browser_config`` is the live :class:`BrowserConfig` snapshot the
    daemon hands down — the backend stores it so per-call defaults
    (``navigation_timeout``, ``screenshot_quality``, …) do not require a
    fresh ``load_config()`` on every API call. Passing ``None`` falls
    back to dataclass defaults inside the backend.
    """
    if tier == StealthTier.PLAYWRIGHT:
        from agentcloak.browser.playwright_ctx import launch_playwright

        return await launch_playwright(
            headless=headless,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            profile_dir=profile_dir,
            proxy_url=proxy_url,
            browser_proxy=browser_proxy,
            extra_args=extra_args,
            browser_config=browser_config,
        )

    if tier == StealthTier.CLOAK:
        from agentcloak.browser.cloak_ctx import launch_cloak

        return await launch_cloak(
            headless=headless,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            profile_dir=profile_dir,
            humanize=humanize,
            extensions=extensions,
            proxy_url=proxy_url,
            browser_proxy=browser_proxy,
            extra_args=extra_args,
            browser_config=browser_config,
        )

    raise NotImplementedError(f"Backend {tier!r} not yet implemented")
