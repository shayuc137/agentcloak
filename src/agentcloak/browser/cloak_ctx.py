"""CloakBrowser stealth backend — high-stealth launch via cloakbrowser package."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentcloak.browser.playwright_ctx import PlaywrightContext, find_free_port
from agentcloak.core.errors import BackendError
from agentcloak.core.seq import RingBuffer, SeqCounter
from agentcloak.core.types import StealthTier

if TYPE_CHECKING:
    from agentcloak.core.config import BrowserConfig

__all__ = ["launch_cloak"]

_EXTENSIONS_DIR = Path(__file__).parent / "extensions"
TURNSTILE_PATCH_DIR = _EXTENSIONS_DIR / "turnstile_patch"


def _ensure_cloakbrowser() -> Any:
    try:
        import cloakbrowser  # pyright: ignore[reportMissingImports]

        return cloakbrowser
    except ImportError as exc:
        raise BackendError(
            error="stealth_not_installed",
            hint="CloakBrowser package is required for stealth mode",
            action="pip install agentcloak[stealth]",
        ) from exc


def _build_extension_args(extensions: list[str] | None) -> list[str]:
    if not extensions:
        return []
    paths = ",".join(extensions)
    return [
        f"--disable-extensions-except={paths}",
        f"--load-extension={paths}",
    ]


class CloakContext(PlaywrightContext):
    """PlaywrightContext subclass that reports CLOAK stealth tier."""

    @property
    def stealth_tier(self) -> StealthTier:
        return StealthTier.CLOAK

    def browser_description(self) -> str:
        """CloakBrowser + bundled patched Chromium version.

        The ``cloakbrowser`` package exposes ``__version__`` and ``binary_info()``
        — we prefer the package version since it's stable across runs (the
        bundled Chromium binary tracks the patch set, not the package). If the
        package isn't importable we fall back to a bare label so doctor stays
        usable on a half-configured machine.
        """
        try:
            import cloakbrowser  # pyright: ignore[reportMissingImports,reportMissingTypeStubs]

            ver = getattr(cloakbrowser, "__version__", "")
            if ver:
                return f"CloakBrowser {ver}"
            return "CloakBrowser"
        except ImportError:
            return "CloakBrowser"


async def launch_cloak(
    *,
    headless: bool = False,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    profile_dir: Path | None = None,
    humanize: bool = True,
    extensions: list[str] | None = None,
    proxy_url: str | None = None,
    browser_proxy: str | None = None,
    extra_args: list[str] | None = None,
    browser_config: BrowserConfig | None = None,
) -> CloakContext:
    """Launch a CloakBrowser instance and return a CloakContext.

    ``proxy_url`` is the httpcloak local TLS proxy (used by ``fetch``);
    ``browser_proxy`` is the user-configured upstream proxy passed to
    Chromium so every browser request egresses through it. See
    :func:`agentcloak.browser.create_context` for the rationale behind
    the split.
    """
    cb = _ensure_cloakbrowser()

    ext_args = _build_extension_args(extensions)

    # Allocate a free port for CDP; Chrome 90+ supports pipe+port coexistence.
    cdp_port = find_free_port()
    # ``extra_args`` lands at the end so user-supplied flags can override
    # any defaults agentcloak set (Chromium honours the last occurrence).
    all_args = [
        *ext_args,
        f"--remote-debugging-port={cdp_port}",
        *(extra_args or []),
    ]

    # CloakBrowser piggy-backs on Playwright's launch API, which expects
    # ``proxy={"server": "..."}``. Empty / ``None`` means "direct".
    launch_extras: dict[str, Any] = {}
    if browser_proxy:
        launch_extras["proxy"] = {"server": browser_proxy}

    seq_counter = SeqCounter()
    ring_buffer = RingBuffer()

    if profile_dir is not None:
        profile_dir.mkdir(parents=True, exist_ok=True)
        browser_context = await cb.launch_persistent_context_async(
            user_data_dir=str(profile_dir),
            headless=headless,
            args=all_args,
            humanize=humanize,
            viewport={"width": viewport_width, "height": viewport_height},
            **launch_extras,
        )

        pages = browser_context.pages
        page = pages[0] if pages else await browser_context.new_page()

        return CloakContext(
            page=page,
            browser=None,
            playwright=None,
            seq_counter=seq_counter,
            ring_buffer=ring_buffer,
            browser_context=browser_context,
            proxy_url=proxy_url,
            cdp_port=cdp_port,
            browser_config=browser_config,
            profile_dir=profile_dir,
        )

    browser = await cb.launch_async(
        headless=headless,
        args=all_args,
        humanize=humanize,
        **launch_extras,
    )

    ctx = await browser.new_context(
        viewport={"width": viewport_width, "height": viewport_height},
    )
    page = await ctx.new_page()

    return CloakContext(
        page=page,
        browser=browser,
        playwright=None,
        seq_counter=seq_counter,
        ring_buffer=ring_buffer,
        proxy_url=proxy_url,
        cdp_port=cdp_port,
        browser_config=browser_config,
    )
