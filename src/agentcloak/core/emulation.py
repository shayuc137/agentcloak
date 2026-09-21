"""Page environment overrides shared by the browser and API surfaces."""

from enum import StrEnum

from pydantic import BaseModel, Field


class ColorScheme(StrEnum):
    light = "light"
    dark = "dark"


class Pointer(StrEnum):
    fine = "fine"
    coarse = "coarse"


class PageEmulation(BaseModel):
    color_scheme: ColorScheme | None = Field(
        default=None,
        description="Color scheme override; omitted retains the current value.",
    )
    reduced_motion: bool | None = Field(
        default=None, description="True reduces motion; false requests no preference."
    )
    pointer: Pointer | None = Field(
        default=None,
        description="Touch/pointer override; requires a headed local browser.",
    )
