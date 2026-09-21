"""Draw snapshot references in image pixels without changing the page."""

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont


def annotate_image(
    data: bytes, boxes: list[dict[str, Any]], *, dpr: float, format: str, quality: int
) -> bytes:
    with Image.open(BytesIO(data)) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=max(10, round(12 * dpr)))
    for item in boxes:
        x, y, width, height = item["box"]
        left, top, right, bottom = (
            round(v * dpr) for v in (x, y, x + width, y + height)
        )
        if right <= 0 or bottom <= 0 or left >= image.width or top >= image.height:
            continue
        draw.rectangle(
            (left, top, right, bottom), outline="#e02020", width=max(1, round(2 * dpr))
        )
        label = f"[{item['ref']}]"
        label_width = round(draw.textlength(label, font=font)) + 4
        label_height = round(16 * dpr)
        left = max(0, min(left, image.width - label_width))
        top = max(0, min(top - label_height, image.height - label_height))
        draw.rectangle(
            (left, top, left + label_width, top + label_height), fill="#e02020"
        )
        draw.text((left + 2, top), label, font=font, fill="white")
    output = BytesIO()
    image.save(
        output,
        format="JPEG" if format == "jpeg" else "PNG",
        **({"quality": quality} if format == "jpeg" else {}),
    )
    return output.getvalue()
