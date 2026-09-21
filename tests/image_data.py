"""Small valid image for transport and encoding tests."""

from io import BytesIO

from PIL import Image

_buffer = BytesIO()
Image.new("RGB", (2, 3), "white").save(_buffer, format="PNG")
PNG = _buffer.getvalue()
