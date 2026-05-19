"""Success and error envelope models shared by every daemon route.

These two models live in their own file so route-group modules can import
them without dragging in the full model surface. Every route returns either
``OkEnvelope[XxxResponse]`` or — via the global exception handler — an
``ErrorResponse`` payload.
"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

__all__ = ["ErrorResponse", "OkEnvelope"]


T = TypeVar("T")


class OkEnvelope(BaseModel, Generic[T]):  # noqa: UP046
    """Success envelope `{ok: true, seq: N, data: T}`.

    We keep ``Generic[T]`` (instead of the PEP 695 ``class Foo[T]`` form)
    because Pydantic v2 still wires its generic plumbing through the legacy
    ``typing.Generic`` mechanism — switching to PEP 695 silently breaks
    Pydantic's model validation for parameterised envelopes.
    """

    ok: Literal[True] = True
    seq: int = 0
    data: T


class ErrorResponse(BaseModel):
    """Three-field error envelope. Status code carries the HTTP semantics."""

    ok: Literal[False] = False
    error: str
    hint: str = ""
    action: str = ""
