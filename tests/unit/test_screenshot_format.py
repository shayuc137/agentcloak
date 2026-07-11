from __future__ import annotations

import pytest

from agentcloak.core.errors import BackendError
from agentcloak.core.screenshot_format import resolve_screenshot_format


@pytest.mark.parametrize(
    ("path", "expected"),
    [("page.png", "png"), ("page.JPG", "jpeg"), ("page.jpeg", "jpeg")],
)
def test_recognized_suffix_selects_format(path: str, expected: str) -> None:
    result = resolve_screenshot_format(
        explicit_format=None,
        output_path=path,
        default_format="jpeg",
    )

    assert result.format == expected
    assert result.warning_for(expected) == ""


def test_explicit_format_can_match_suffix() -> None:
    result = resolve_screenshot_format(
        explicit_format="PNG",
        output_path="page.png",
        default_format="jpeg",
    )

    assert result.format == "png"


def test_explicit_format_conflict_is_actionable() -> None:
    with pytest.raises(BackendError) as exc_info:
        resolve_screenshot_format(
            explicit_format="jpeg",
            output_path="page.png",
            default_format="jpeg",
        )

    assert exc_info.value.error == "screenshot_format_suffix_conflict"
    assert "omit --format" in exc_info.value.action


def test_unrecognized_suffix_falls_back_with_warning() -> None:
    result = resolve_screenshot_format(
        explicit_format=None,
        output_path="page.artifact",
        default_format="png",
    )

    assert result.format == "png"
    assert result.warning_for("png") == (
        "unrecognized screenshot suffix '.artifact'; used configured format 'png'"
    )


def test_extensionless_path_falls_back_quietly() -> None:
    result = resolve_screenshot_format(
        explicit_format=None,
        output_path="page",
        default_format="jpeg",
    )

    assert result.format == "jpeg"
    assert result.warning_for("jpeg") == ""


def test_cli_can_defer_unknown_default_to_daemon() -> None:
    result = resolve_screenshot_format(
        explicit_format=None,
        output_path="page.unknown",
        default_format=None,
    )

    assert result.format is None
    assert "configured format 'jpeg'" in result.warning_for("jpeg")
