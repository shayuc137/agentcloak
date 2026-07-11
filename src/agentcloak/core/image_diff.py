"""Deterministic RGBA screenshot comparison."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import reduce
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageEnhance, ImageOps, UnidentifiedImageError

from agentcloak.core.errors import ImageDiffError

__all__ = ["ImageDiffResult", "ImageSource", "compare_images"]

type ImageSource = Path | bytes


@dataclass(frozen=True)
class ImageDiffResult:
    """Numeric result of one exact-dimension RGBA comparison."""

    width: int
    height: int
    changed_pixels: int
    total_pixels: int
    difference_ratio: float
    difference_percent: float
    max_channel_delta: int
    threshold: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_rgba(source: ImageSource, *, label: str) -> Image.Image:
    try:
        stream: Path | BytesIO = source if isinstance(source, Path) else BytesIO(source)
        with Image.open(stream) as image:
            return ImageOps.exif_transpose(image).convert("RGBA")
    except (OSError, UnidentifiedImageError) as exc:
        raise ImageDiffError(
            error="image_diff_unreadable",
            hint=f"Cannot read {label} image: {exc}",
            action="provide a supported local image file",
        ) from exc


def _save_diff_image(
    baseline: Image.Image,
    max_channel: Image.Image,
    *,
    threshold: int,
    output: Path,
) -> None:
    mask = Image.frombytes(
        "L",
        max_channel.size,
        bytes(255 if value > threshold else 0 for value in max_channel.tobytes()),
    )
    dimmed = ImageEnhance.Brightness(baseline).enhance(0.25)
    highlighted = Image.composite(
        Image.new("RGBA", baseline.size, (255, 0, 0, 255)), dimmed, mask
    )
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        highlighted.save(output)
    except OSError as exc:
        raise ImageDiffError(
            error="image_diff_output_failed",
            hint=f"Cannot write difference image '{output}': {exc}",
            action="choose a writable --output path",
        ) from exc


def compare_images(
    baseline_source: ImageSource,
    current_source: ImageSource,
    *,
    threshold: int = 0,
    output: Path | None = None,
) -> ImageDiffResult:
    """Compare two images after EXIF transpose and RGBA normalization."""
    if not 0 <= threshold <= 255:
        raise ImageDiffError(
            error="invalid_image_diff_threshold",
            hint=f"Image difference threshold must be 0-255, got {threshold}",
            action="pass --threshold with an integer from 0 through 255",
        )

    baseline = _load_rgba(baseline_source, label="baseline")
    current = _load_rgba(current_source, label="current")
    if baseline.size != current.size:
        raise ImageDiffError(
            error="image_diff_dimension_mismatch",
            hint=(
                f"Image dimensions differ: baseline={baseline.size[0]}x"
                f"{baseline.size[1]}, current={current.size[0]}x{current.size[1]}"
            ),
            action="capture both images at the same viewport and dimensions",
        )

    channel_diff = ImageChops.difference(baseline, current)
    max_channel = reduce(ImageChops.lighter, channel_diff.split())
    histogram = max_channel.histogram()
    changed_pixels = sum(histogram[threshold + 1 :])
    max_delta = next((value for value in range(255, -1, -1) if histogram[value]), 0)
    width, height = baseline.size
    total_pixels = width * height
    difference_ratio = changed_pixels / total_pixels if total_pixels else 0.0

    if output is not None:
        _save_diff_image(
            baseline, max_channel, threshold=threshold, output=output.expanduser()
        )

    return ImageDiffResult(
        width=width,
        height=height,
        changed_pixels=changed_pixels,
        total_pixels=total_pixels,
        difference_ratio=difference_ratio,
        difference_percent=difference_ratio * 100,
        max_channel_delta=max_delta,
        threshold=threshold,
    )
