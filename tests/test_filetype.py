import io
import tarfile
import zipfile

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


# --- documents, audio, archives -----------------------------------------------


def make_zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def mpeg_frames(count: int = 2) -> bytes:
    # MPEG-1 Layer III, 128 kbit/s, 44.1 kHz, no padding: 417-byte frames.
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    return frame * count


def make_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        info = tarfile.TarInfo("hello.txt")
        info.size = 2
        tf.addfile(info, io.BytesIO(b"hi"))
    return buf.getvalue()


OOXML_CT = b'<?xml version="1.0"?><Types/>'

OTHER_FORMATS = [
    (b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n", ft.PDF),
    (make_zip({"a.txt": "hello"}), ft.ZIP),
    (make_zip({"[Content_Types].xml": OOXML_CT, "word/document.xml": "<w/>"}), ft.DOCX),
    (make_zip({"[Content_Types].xml": OOXML_CT, "xl/workbook.xml": "<x/>"}), ft.XLSX),
    (make_zip({"[Content_Types].xml": OOXML_CT, "ppt/presentation.xml": "<p/>"}), ft.PPTX),
    (make_zip({"mimetype": "application/vnd.oasis.opendocument.text", "content.xml": "<c/>"}), ft.ODT),
    (make_zip({"mimetype": "application/vnd.oasis.opendocument.spreadsheet"}), ft.ODS),
    (make_zip({"mimetype": "application/vnd.oasis.opendocument.presentation"}), ft.ODP),
    (make_zip({"mimetype": "application/epub+zip", "META-INF/container.xml": "<c/>"}), ft.EPUB),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504, ft.OLE2),
    (b"ID3\x04\x00\x00\x00\x00\x00\x00" + mpeg_frames(1), ft.MP3),
    (mpeg_frames(2), ft.MP3),
    (b"RIFF\x24\x00\x00\x00WAVEfmt ", ft.WAV),
    (b"fLaC\x00\x00\x00\x22", ft.FLAC),
    (b"ID3\x03\x00\x00\x00\x00\x00\x00fLaC\x00\x00\x00\x22", ft.FLAC),
    (b"OggS\x00\x02" + b"\x00" * 22, ft.OGG),
    (ftyp(b"isom", b"isom", b"iso2", b"mp41"), ft.MP4),
    (ftyp(b"qt  ", b"qt  "), ft.MOV),
    (ftyp(b"M4A ", b"M4A ", b"mp42", b"isom"), ft.M4A),
    (make_tar(), ft.TAR),
    (b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03", ft.GZIP),
    (b"7z\xbc\xaf\x27\x1c\x00\x04", ft.SEVEN_ZIP),
    (b"Rar!\x1a\x07\x00\xcf\x90", ft.RAR),
    (b"Rar!\x1a\x07\x01\x00\x33\x92", ft.RAR),
]


@pytest.mark.parametrize("data,expected", OTHER_FORMATS, ids=lambda v: getattr(v, "key", None))
def test_detects_other_formats(data, expected):
    assert detect(data) == expected


def test_in_scope_formats_are_marked_supported():
    for t in (ft.DOCX, ft.XLSX, ft.PPTX, ft.ODT, ft.MP3, ft.WAV, ft.FLAC, ft.ZIP, ft.TAR):
        assert t.supported is True, t.key
    for t in (ft.PDF, ft.OLE2, ft.ODS, ft.ODP, ft.EPUB, ft.OGG, ft.MP4, ft.MOV,
              ft.M4A, ft.GZIP, ft.SEVEN_ZIP, ft.RAR, ft.UNKNOWN):
        assert t.supported is False, t.key


def test_single_mpeg_frame_header_is_not_enough():
    assert detect(mpeg_frames(1)) == ft.UNKNOWN


def test_unknown_never_raises():
    assert detect(b"\x00" * 1000) == ft.UNKNOWN
    assert detect(b"PK\x03\x04garbage") .key == "zip"
