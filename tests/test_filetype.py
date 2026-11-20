import pytest

from metaex.core import filetype as ft
from metaex.core.filetype import detect


def ftyp(major: bytes, *compatible: bytes) -> bytes:
    body = major + b"\x00\x00\x00\x00" + b"".join(compatible)
    return (8 + len(body)).to_bytes(4, "big") + b"ftyp" + body + b"\x00" * 16


IMAGES = [
    (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00", ft.JPEG),
    (b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR", ft.PNG),
    (b"GIF87a\x01\x00\x01\x00", ft.GIF),
    (b"GIF89a\x01\x00\x01\x00", ft.GIF),
    (b"RIFF\x24\x00\x00\x00WEBPVP8 ", ft.WEBP),
    (b"II*\x00\x08\x00\x00\x00", ft.TIFF),
    (b"MM\x00*\x00\x00\x00\x08", ft.TIFF),
    (ftyp(b"heic", b"mif1", b"heic"), ft.HEIC),
    (ftyp(b"mif1", b"mif1", b"heic"), ft.HEIC),
    (ftyp(b"mif1", b"mif1", b"miaf"), ft.HEIF),
    (ftyp(b"avif", b"avif", b"mif1", b"miaf"), ft.AVIF),
    (ftyp(b"mif1", b"mif1", b"avif"), ft.AVIF),
    (b'<svg xmlns="http://www.w3.org/2000/svg"/>', ft.SVG),
]


@pytest.mark.parametrize("data,expected", IMAGES, ids=lambda v: getattr(v, "key", None))
def test_detects_images(data, expected):
    assert detect(data) == expected


def test_svg_after_bom_declaration_and_comments():
    data = (
        b"\xef\xbb\xbf"
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b"<!-- Generator: Some Editor 1.0 -->\n"
        b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
        b'<svg version="1.1">\n</svg>'
    )
    assert detect(data) == ft.SVG


def test_heic_tiff_avif_are_detected_but_not_supported():
    for t in (ft.HEIC, ft.HEIF, ft.TIFF, ft.AVIF):
        assert t.supported is False
    for t in (ft.JPEG, ft.PNG, ft.GIF, ft.WEBP, ft.SVG):
        assert t.supported is True


def test_filetype_fields():
    assert ft.JPEG.name == "JPEG"
    assert ft.JPEG.mime == "image/jpeg"
    assert ".jpg" in ft.JPEG.extensions


def test_unknown_data():
    assert detect(b"hello world") == ft.UNKNOWN
    assert detect(b"") == ft.UNKNOWN
