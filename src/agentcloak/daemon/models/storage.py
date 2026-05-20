"""Pydantic models for localStorage / sessionStorage routes (7a R4).

Storage operations run through ``page.evaluate()`` (see
:mod:`agentcloak.core.storage_helpers`) rather than a backend ``_impl`` method,
so these models only carry the request shape and a permissive response — the
value can be a single string (single-key get) or an object (full dump).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "StorageClearRequest",
    "StorageDeleteRequest",
    "StorageGetRequest",
    "StorageResponse",
    "StorageSetRequest",
]


class StorageGetRequest(BaseModel):
    """Read one key (when ``key`` given) or the whole store."""

    type: str = Field("local", description="Storage area: 'local' or 'session'.")
    key: str | None = Field(
        None, description="Single key to read; omit to dump all entries."
    )


class StorageSetRequest(BaseModel):
    """Write ``key=value`` into a storage area."""

    type: str = Field("local", description="Storage area: 'local' or 'session'.")
    key: str = Field(description="Key to write.")
    value: str = Field(description="Value to store (strings only).")


class StorageDeleteRequest(BaseModel):
    """Remove a single key from a storage area."""

    type: str = Field("local", description="Storage area: 'local' or 'session'.")
    key: str = Field(description="Key to remove.")


class StorageClearRequest(BaseModel):
    """Empty an entire storage area."""

    type: str = Field("local", description="Storage area: 'local' or 'session'.")


class StorageResponse(BaseModel):
    """Storage operation result.

    ``value`` carries the read payload (string for single-key get, object for
    a full dump, ``null`` for a missing key). Mutating ops echo back the type
    and key so the renderer can confirm what changed without re-reading the
    request.
    """

    model_config = ConfigDict(extra="allow")

    type: str = Field(description="Storage area the op ran against.")
    key: str | None = Field(None, description="Key the op targeted, if single-key.")
    value: Any = Field(None, description="Read result; omitted for writes/clears.")
