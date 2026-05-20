"""Pydantic models for init-script injection routes (7b T1.1).

Init scripts run before any page script on every navigation — the standard
hook point for reverse engineering (patch ``fetch``/``XHR``/``JSON.parse``
before the page uses them). ``/script/add`` injects either raw JS or a named
preset; ``/script/remove`` drops one by identifier; ``/script/list`` reports
what's active.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "ScriptAddRequest",
    "ScriptAddResponse",
    "ScriptListResponse",
    "ScriptRemoveRequest",
    "ScriptRemoveResponse",
]


class ScriptAddRequest(BaseModel):
    """Inject an init script: raw ``js`` or a named ``preset`` (one required)."""

    js: str = Field("", description="Raw JavaScript to run before page scripts.")
    preset: str = Field(
        "",
        description=(
            "Built-in hook preset: fetch, xhr, json_parse, crypto, or timing. "
            "Overrides 'js' when set."
        ),
    )


class ScriptAddResponse(BaseModel):
    identifier: str = Field(description="CDP script identifier (use to remove).")
    preset: str | None = Field(None, description="Preset name, if one was used.")


class ScriptRemoveRequest(BaseModel):
    identifier: str = Field(description="Identifier returned by /script/add.")


class ScriptRemoveResponse(BaseModel):
    removed: bool = Field(description="True if the identifier was known and removed.")


class ScriptListResponse(BaseModel):
    scripts: list[dict[str, str]] = Field(
        description="Active init scripts: [{identifier, source}], source truncated."
    )
    count: int = Field(description="Number of active init scripts.")
