import io

import pytest
from PIL import Image

from gemini_delegate.imaging import ImagingError, chroma_key, decode, parse_color, save_image, validate_key


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


def _magenta_with_square():
    img = Image.new("RGB", (40, 40), (255, 0, 255))
    for x in range(15, 25):
        for y in range(15, 25):
            img.putpixel((x, y), (0, 200, 0))
    return img


def test_chroma_key_removes_background_keeps_subject():
    keyed, stats = chroma_key(_magenta_with_square(), (255, 0, 255), 60)
    assert keyed.mode == "RGBA"
    assert keyed.getpixel((0, 0))[3] == 0          # corner (bg) transparent
    assert keyed.getpixel((20, 20))[3] == 255      # subject opaque
    assert keyed.getpixel((20, 20))[:3] == (0, 200, 0)
    assert 0.9 < stats["removed_fraction"] < 0.95   # (1600-100)/1600 = 0.9375
    assert stats["corners_transparent"] is True


def test_chroma_key_tolerance_band():
    # a near-magenta pixel (compression-ish) within tolerance keys out
    img = Image.new("RGB", (10, 10), (245, 8, 250))
    keyed, stats = chroma_key(img, (255, 0, 255), 60)
    assert stats["removed_fraction"] == 1.0
    keyed2, stats2 = chroma_key(img, (255, 0, 255), 2)  # tight tolerance: nothing
    assert stats2["removed_fraction"] == 0.0


def test_validate_key_low_removed_warns():
    assert any("key" in w for w in validate_key({"removed_fraction": 0.01, "corners_transparent": False}))


def test_validate_key_high_removed_warns():
    msgs = validate_key({"removed_fraction": 0.99, "corners_transparent": True})
    assert any("subject" in w for w in msgs)


def test_validate_key_clean_result_no_warnings():
    assert validate_key({"removed_fraction": 0.5, "corners_transparent": True}) == []
