"""Embedded images in Office files are cleaned with their own format's stripper."""

from __future__ import annotations

import pytest

from helpers import gif, jpeg, png, svg, webp
from helpers.ooxml import add_media, docx, pptx, xlsx
from helpers.pixels import assert_same_image
from metaex.core import filetype
from metaex.core.errors import CannotCleanError
from metaex.core.ooxml import open_package
from metaex.core.registry import format_module
from metaex.formats import docx as docx_format
from metaex.formats import pptx as pptx_format
from metaex.formats import xlsx as xlsx_format

IMAGES = {
    "image1.jpeg": (jpeg.kitchen_sink_jpeg(), "jpeg", "image/jpeg"),
    "image2.png": (png.kitchen_sink_png(), "png", "image/png"),
    "image3.gif": (gif.kitchen_sink_gif(), "gif", "image/gif"),
    "image4.webp": (webp.kitchen_sink_webp(), "webp", "image/webp"),
    "image5.svg": (svg.kitchen_sink_svg(), "svg", "image/svg+xml"),
}
PIXEL_TYPES = ("jpeg", "png", "gif", "webp")


def with_images(builder, source, folder, images=IMAGES):
    pkg = builder()
    for name, (data, ext, ctype) in images.items():
        add_media(pkg, source, f"{folder}/media/{name}", data, ext, ctype)
    return pkg


def metadata_sections(data: bytes) -> list[str]:
    module = format_module(filetype.detect(data))
    kept = getattr(module, "KEPT_BY_DESIGN", {})
    out = []
    for section in module.parse(data)["sections"]:
        allowed = kept.get(section.key, set())
        if allowed is None:
            continue
        out += [f"{section.key}.{f.key}" for f in section.fields if f.key not in allowed]
    return out


@pytest.mark.parametrize("builder, module, source, folder", [
    (docx, docx_format, "word/document.xml", "word"),
    (xlsx, xlsx_format, "xl/workbook.xml", "xl"),
    (pptx, pptx_format, "ppt/slides/slide1.xml", "ppt"),
])
def test_embedded_images_are_cleaned(builder, module, source, folder):
    cleaned = open_package(module.strip(with_images(builder, source, folder).build()))
    for name, (original, ext, _ctype) in IMAGES.items():
        data = cleaned.read(f"{folder}/media/{name}")
        assert metadata_sections(original), name  # the test images do carry metadata
        assert metadata_sections(data) == [], name
        if ext in PIXEL_TYPES:
            assert_same_image(original, data, primary_only=ext == "jpeg")
    assert module.kept_warnings(module.strip(with_images(builder, source, folder).build())) == []


def test_paranoid_and_orientation_options_reach_the_images():
    photo = jpeg.sample_photo_rotated()
    pkg = with_images(docx, "word/document.xml", "word",
                      {"image1.jpeg": (photo, "jpeg", "image/jpeg")})
    kept = open_package(docx_format.strip(pkg.build())).read("word/media/image1.jpeg")
    dropped = open_package(docx_format.strip(pkg.build(), keep_orientation=False)).read(
        "word/media/image1.jpeg")
    module = format_module(filetype.JPEG)
    assert any(f.key.endswith("Orientation") for s in module.parse(kept)["sections"]
               for f in s.fields)
    assert not any(f.key.endswith("Orientation") for s in module.parse(dropped)["sections"]
                   for f in s.fields)
    icc_jpeg = jpeg.kitchen_sink_jpeg()
    pkg = with_images(docx, "word/document.xml", "word",
                      {"image1.jpeg": (icc_jpeg, "jpeg", "image/jpeg")})
    normal = open_package(docx_format.strip(pkg.build())).read("word/media/image1.jpeg")
    paranoid = open_package(docx_format.strip(pkg.build(), paranoid=True)).read(
        "word/media/image1.jpeg")
    assert any(s.key == "icc" for s in module.parse(normal)["sections"])
    assert not any(s.key == "icc" for s in module.parse(paranoid)["sections"])


def test_images_metaex_cannot_clean_are_kept_with_a_warning():
    emf = b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 40
    pkg = with_images(docx, "word/document.xml", "word", {
        "image1.emf": (emf, "emf", "image/x-emf"),
        "image2.tiff": (b"II*\x00" + b"\x00" * 20, "tiff", "image/tiff"),
    })
    cleaned = docx_format.strip(pkg.build())
    assert open_package(cleaned).read("word/media/image1.emf") == emf
    warnings = docx_format.kept_warnings(cleaned)
    assert any("kept 2 embedded image(s)" in w and "TIFF" in w and "image/x-emf" in w
               for w in warnings)


def test_damaged_image_is_refused():
    pkg = with_images(docx, "word/document.xml", "word",
                      {"image1.png": (png.make_png()[:-20], "png", "image/png")})
    with pytest.raises(CannotCleanError, match="embedded image word/media/image1.png"):
        docx_format.strip(pkg.build())


def test_image_without_metadata_is_copied_unchanged():
    from helpers.jpeg_encoder import jpeg_file
    from helpers.zip import DEFLATED
    from metaex.core.ziparchive import open_archive

    clean = png.make_png()
    pkg = with_images(docx, "word/document.xml", "word", {"image1.png": (clean, "png",
                                                                         "image/png")})
    original = open_archive(pkg.build(method=DEFLATED))
    cleaned = open_archive(docx_format.strip(pkg.build(method=DEFLATED)))
    assert cleaned.raw(cleaned.get("word/media/image1.png")) == \
        original.raw(original.get("word/media/image1.png"))
    assert jpeg_file
