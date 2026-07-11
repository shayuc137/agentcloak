"""Tests for deterministic local screenshot comparison."""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from agentcloak.core.errors import ImageDiffError
from agentcloak.core.image_diff import compare_images

if TYPE_CHECKING:
    from pathlib import Path


def _write_image(
    path: Path,
    pixels: list[tuple[int, int, int, int]],
    *,
    size: tuple[int, int] = (2, 2),
) -> None:
    image = Image.new("RGBA", size)
    image.putdata(pixels)
    image.save(path)


def _png_bytes(
    pixels: list[tuple[int, int, int, int]], *, size: tuple[int, int] = (2, 2)
) -> bytes:
    image = Image.new("RGBA", size)
    image.putdata(pixels)
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


class TestCompareImages:
    def test_identical_images_report_zero(self, tmp_path: Path) -> None:
        pixels = [(10, 20, 30, 255)] * 4
        baseline = tmp_path / "baseline.png"
        current = tmp_path / "current.png"
        _write_image(baseline, pixels)
        _write_image(current, pixels)

        result = compare_images(baseline, current)

        assert result.changed_pixels == 0
        assert result.total_pixels == 4
        assert result.difference_ratio == 0
        assert result.difference_percent == 0
        assert result.max_channel_delta == 0

    def test_one_pixel_change_reports_exact_metrics(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.png"
        current = tmp_path / "current.png"
        _write_image(baseline, [(0, 0, 0, 255)] * 4)
        _write_image(
            current,
            [(0, 0, 40, 255), (0, 0, 0, 255), (0, 0, 0, 255), (0, 0, 0, 255)],
        )

        result = compare_images(baseline, current)

        assert result.changed_pixels == 1
        assert result.difference_ratio == 0.25
        assert result.difference_percent == 25
        assert result.max_channel_delta == 40
        assert (result.width, result.height) == (2, 2)

    def test_normalizes_rgb_and_rgba_inputs(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.bmp"
        current = tmp_path / "current.png"
        Image.new("RGB", (2, 2), (10, 20, 30)).save(baseline)
        Image.new("RGBA", (2, 2), (10, 20, 30, 255)).save(current)

        result = compare_images(baseline, current)

        assert result.changed_pixels == 0
        assert result.max_channel_delta == 0

    def test_threshold_ignores_boundary_value(self) -> None:
        baseline = _png_bytes([(0, 0, 0, 255)])
        current = _png_bytes([(4, 0, 0, 255)])

        assert compare_images(baseline, current, threshold=4).changed_pixels == 0
        assert compare_images(baseline, current, threshold=3).changed_pixels == 1

    def test_dimension_mismatch_reports_both_sizes(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.png"
        current = tmp_path / "current.png"
        _write_image(baseline, [(0, 0, 0, 255)] * 4)
        _write_image(current, [(0, 0, 0, 255)] * 3, size=(3, 1))

        with pytest.raises(ImageDiffError) as exc_info:
            compare_images(baseline, current)

        assert exc_info.value.error == "image_diff_dimension_mismatch"
        assert "baseline=2x2" in exc_info.value.hint
        assert "current=3x1" in exc_info.value.hint

    def test_diff_output_highlights_changed_pixels_red(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.png"
        current = tmp_path / "current.png"
        output = tmp_path / "artifacts" / "diff.png"
        base_pixels = [(100, 80, 60, 255)] * 4
        _write_image(baseline, base_pixels)
        _write_image(
            current,
            [(110, 80, 60, 255), *base_pixels[1:]],
        )

        result = compare_images(baseline, current, output=output)

        assert result.changed_pixels == 1
        with Image.open(output) as image:
            rgba = image.convert("RGBA")
            assert rgba.size == (2, 2)
            assert rgba.getpixel((0, 0)) == (255, 0, 0, 255)
            assert rgba.getpixel((1, 0)) == (25, 20, 15, 255)

    def test_invalid_threshold_is_structured(self) -> None:
        with pytest.raises(ImageDiffError) as exc_info:
            compare_images(b"unused", b"unused", threshold=256)

        assert exc_info.value.error == "invalid_image_diff_threshold"
