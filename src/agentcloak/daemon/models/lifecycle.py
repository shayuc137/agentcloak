"""Pydantic models for daemon-lifecycle routes.

Covers daemon health probing, shutdown, tier hot-switch (``launch``),
session resume, and the CDP debugger endpoint discovery used by
jshookmcp coordination.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "CDPEndpointResponse",
    "HealthResponse",
    "LaunchRequest",
    "LaunchResponse",
    "ResumeResponse",
    "ShutdownResponse",
]


# --- Health ---


class HealthResponse(BaseModel):
    """Daemon liveness + rich state introspection."""

    model_config = ConfigDict(extra="allow")

    ok: Literal[True] = True
    service: str = "agentcloak-daemon"
    # ``stealth_tier`` is the tier of the *currently active* backend; for a
    # remote_bridge session awaiting the extension this is ``remote_bridge``
    # even though no browser exists yet.
    stealth_tier: str | None = None
    active_tier: str | None = None
    browser_ready: bool | None = None
    remote_connected: bool | None = None
    seq: int | None = None
    capture_recording: bool | None = None
    capture_entries: int | None = None
    current_url: str | None = None
    current_title: str | None = None
    # ``page_valid`` mirrors :attr:`BrowserContextBase._page_valid`. False
    # means the last ``navigate()`` failed and subsequent page-bound ops
    # will raise ``no_valid_page`` until a successful navigate runs again.
    page_valid: bool | None = None
    local_proxy: dict[str, Any] | None = None
    # Backend self-describes via ``BrowserContextBase.browser_description()``.
    # Doctor renders this verbatim so we don't hardcode tier → name here.
    browser_description: str | None = None
    # Runtime configuration mirrors — doctor's status line needs these without
    # making each surface re-read config.toml. ``proxy`` is the upstream
    # browser-level proxy (Chromium ``--proxy-server``), not the httpcloak
    # local TLS proxy used by fetch.
    headless: bool | None = None
    humanize: bool | None = None
    proxy: str | None = None
    # Empty string when no profile is attached (ephemeral mode).
    active_profile: str | None = None


# --- Shutdown ---


class ShutdownResponse(BaseModel):
    """Empty payload — the shutdown route signals success via the envelope only."""

    model_config = ConfigDict(extra="allow")


# --- Launch ---


class LaunchRequest(BaseModel):
    """Hot-switch the active browser context.

    ``tier`` selects the backend (``cloak`` / ``playwright`` /
    ``remote_bridge``). ``profile`` only applies to local tiers — for
    ``remote_bridge`` the profile is whatever the user's Chrome already
    has loaded, so the field is ignored.
    """

    tier: Literal["auto", "cloak", "playwright", "remote_bridge"] = "auto"
    profile: str | None = None


class LaunchResponse(BaseModel):
    """Result of a tier switch."""

    model_config = ConfigDict(extra="allow")

    active_tier: str
    browser_ready: bool
    remote_connected: bool
    local_cached: bool
    profile: str | None = None


# --- Resume ---


class ResumeResponse(BaseModel):
    """Session resume snapshot — open-ended to match the writer's payload."""

    model_config = ConfigDict(extra="allow")


# --- CDP ---


class CDPEndpointResponse(BaseModel):
    ws_endpoint: str
    http_url: str
    port: int
