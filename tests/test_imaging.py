import io

import pytest
from PIL import Image

from gemini_delegate.imaging import ImagingError, decode, parse_color, save_image


def _jpeg_bytes(color=(10, 20, 200)):
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buf, format="JPEG")
    return buf.getvalue()


def test_decode_returns_image():
    img = decode(_jpeg_bytes())
    assert img.size == (16, 16)


def test_decode_bad_bytes_raises():
    with pytest.raises(ImagingError):
        decode(b"not an image")


def test_save_image_uses_extension_format(tmp_path):
    img = decode(_jpeg_bytes())
    png = save_image(img, str(tmp_path / "o.png"))
    assert Image.open(png).format == "PNG"
    webp = save_image(img, str(tmp_path / "o.webp"))
    assert Image.open(webp).format == "WEBP"
    jpg = save_image(img, str(tmp_path / "o.jpg"))
    assert Image.open(jpg).format == "JPEG"


def test_save_image_flattens_rgba_to_jpeg(tmp_path):
    rgba = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    out = save_image(rgba, str(tmp_path / "o.jpg"))
    assert Image.open(out).mode == "RGB"


def test_save_image_returns_absolute_path(tmp_path):
    out = save_image(decode(_jpeg_bytes()), str(tmp_path / "o.png"))
    assert out == str((tmp_path / "o.png").resolve())


def test_parse_color_hex_and_names():
    assert parse_color("#FF00FF") == (255, 0, 255)
    assert parse_color("ff00ff") == (255, 0, 255)
    assert parse_color("magenta") == (255, 0, 255)
    assert parse_color("green") == (0, 255, 0)


def test_parse_color_bad_raises():
    with pytest.raises(ImagingError):
        parse_color("not-a-color")
