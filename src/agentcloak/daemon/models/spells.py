"""Pydantic models for spell and profile routes.

Spell registry covers the ``/spell/run`` and ``/spell/list`` endpoints;
profile CRUD covers create/delete/list/create-from-current. They share a
file because both operate on persistent user data (registered spells and
saved Chrome profiles).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ProfileCreateFromCurrentRequest",
    "ProfileCreateFromCurrentResponse",
    "ProfileCreateRequest",
    "ProfileCreateResponse",
    "ProfileDeleteRequest",
    "ProfileListResponse",
    "SpellListResponse",
    "SpellRunRequest",
    "SpellRunResponse",
]


# --- Spells ---


class SpellRunRequest(BaseModel):
    name: str = Field(description="Registered spell name to execute (see spell list).")
    args: dict[str, Any] = Field(
        default_factory=lambda: {},
        description="Keyword arguments passed to the spell's parameters.",
    )


class SpellRunResponse(BaseModel):
    result: Any


class SpellListResponse(BaseModel):
    spells: list[dict[str, Any]]
    count: int


# --- Profile ---


class ProfileCreateRequest(BaseModel):
    name: str = Field(description="Name for the new empty browser profile directory.")


class ProfileCreateResponse(BaseModel):
    """Profile create response.

    Profile routes historically did not use the OkEnvelope wrapper — they
    returned `{ok: true, created: name}` directly. v0.2.0 normalizes them to
    OkEnvelope[ProfileCreateResponse]; the typed fields below are what lives
    in the `data` payload.
    """

    model_config = ConfigDict(extra="allow")

    created: str | None = None
    deleted: str | None = None


class ProfileDeleteRequest(BaseModel):
    name: str = Field(description="Name of the profile directory to delete.")


class ProfileListResponse(BaseModel):
    profiles: list[str]
    count: int


class ProfileCreateFromCurrentRequest(BaseModel):
    name: str = Field(
        description="Name to save the current live session (cookies/storage) under."
    )


class ProfileCreateFromCurrentResponse(BaseModel):
    profile: str
    renamed: bool
    cookie_count: int
