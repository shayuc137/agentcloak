"""Pydantic models for bridge / extension routes.

Covers tab claim, session finalize, and the persistent auth token reset.
The bridge WebSocket endpoints themselves (``/bridge/ws``, ``/ext``) don't
have request/response models — they speak a JSON-RPC-style protocol
documented in :mod:`agentcloak.daemon.services.bridge_service`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

__all__ = [
    "BridgeClaimRequest",
    "BridgeFinalizeRequest",
    "BridgeOpResponse",
    "BridgeTokenResetResponse",
]


class BridgeClaimRequest(BaseModel):
    tab_id: int | None = None
    url_pattern: str | None = None


class BridgeFinalizeRequest(BaseModel):
    mode: str = "close"


class BridgeOpResponse(BaseModel):
    """Bridge claim/finalize result — payload comes from the extension verbatim."""

    model_config = ConfigDict(extra="allow")


class BridgeTokenResetResponse(BaseModel):
    """Result of rotating the persistent bridge auth token.

    The new token is returned so the caller can hand it to the extension
    immediately, and ``rotated=true`` confirms the in-memory state on the
    daemon side has been refreshed (so any already-connected extensions
    will be rejected on their next reconnect).
    """

    token: str
    rotated: bool = True
