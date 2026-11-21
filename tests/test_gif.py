"""GIF: block walking and the metadata sections."""

import struct

import pytest

from helpers.gif import (XMP_MAGIC_TRAILER, application, comment, graphic_control,
                         icc_extension, image, loop, lzw_decode, lzw_encode, make_gif, palette,
                         pixels, plain_text, sub_blocks, xmp_extension)
from helpers.jpeg import SAMPLE_XMP, icc_profile
from metaex.core.errors import MalformedFileError
from metaex.formats import gif


def fields(sections):
    return {f.key: f.value for s in sections for f in s.fields}


# -- the test builders ---------------------------------------------------------------------


@pytest.mark.parametrize("min_code_size,colors", [(2, 2), (2, 4), (3, 8), (8, 256)])
@pytest.mark.parametrize("n", [1, 2, 50, 3000])
def test_lzw_roundtrip(min_code_size, colors, n):
    data = bytes((i * 7 + i // 3) % colors for i in range(n))
    assert lzw_decode(lzw_encode(data, min_code_size), min_code_size) == data


def test_image_block_holds_real_lzw_data():
    s = gif.walk_blocks(make_gif(image(7, 5, colors=4, seed=3)))
    [img] = s.images
    assert lzw_decode(b"".join(img.sub_blocks), img.min_code_size) == pixels(7, 5, 4, 3)


# -- walking -----------------------------------------------------------------------------


def test_walk_minimal_gif():
    data = make_gif()
    s = gif.walk_blocks(data)
    assert (s.version, s.width, s.height) == ("89a", 4, 4)
    assert s.global_color_table == palette(4)
    assert [b.kind for b in s.blocks] == ["image", "trailer"]
    assert s.end_of_image == len(data) and s.error is None and s.warnings == []


def test_walk_all_block_kinds():
    data = make_gif(comment("hi"), loop(), graphic_control(), image(), plain_text("x"),
                    image(2, 2, 1, 1, local_table=palette(4, 9)), application(b"MYAPPv01.00", b"z"))
    s = gif.walk_blocks(data)
    assert [b.name for b in s.blocks] == [
        "Comment extension", "Application extension", "Graphic Control extension", "Image",
        "Plain Text extension", "Image", "Application extension", "Trailer"]
    assert s.images[1].color_table == palette(4, 9)
    assert [b.app_id for b in s.applications()] == ["NETSCAPE2.0", "MYAPPv01.00"]


def test_gif87a_and_no_global_table():
    s = gif.walk_blocks(make_gif(version=b"87a", global_table=False))
    assert s.version == "87a" and s.global_color_table == b""


@pytest.mark.parametrize("data,message", [
    (b"GIF89", "no GIF87a/GIF89a header"),
    (b"GIF90a" + bytes(10), "no GIF87a/GIF89a header"),
    (b"GIF89a" + bytes(5), "screen descriptor is truncated"),
    (b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0x87, 0, 0) + bytes(10),
     "global color table is truncated"),
])
def test_broken_header_raises(data, message):
    with pytest.raises(MalformedFileError, match=message):
        gif.walk_blocks(data)


@pytest.mark.parametrize("cut", [2, 5, 12, 20])  # 1 would only remove the trailer
def test_truncated_blocks_stop_the_walk(cut):
    data = make_gif(comment("a comment"), image())
    s = gif.walk_blocks(data[: len(data) - cut])
    assert s.error and any(w.startswith("malformed GIF") for w in s.warnings)
    assert s.end_of_image is None


def test_missing_trailer_is_a_warning():
    s = gif.walk_blocks(make_gif(trailer=False))
    assert s.error is None and s.end_of_image is None
    assert any("missing GIF trailer" in w for w in s.warnings)


def test_unknown_block_type_stops_the_walk():
    data = make_gif(image())[:-1] + b"\x99\x00\x3b"
    s = gif.walk_blocks(data)
    assert "unknown block type 0x99" in s.error


@pytest.mark.parametrize("code_size", [0, 12, 255])
def test_invalid_lzw_code_size(code_size):
    s = gif.walk_blocks(make_gif(image(min_code_size=code_size, lzw=b"\x00")))
    assert "invalid LZW code size" in s.error


def test_block_limit(monkeypatch):
    monkeypatch.setattr(gif, "MAX_BLOCKS", 100)
    s = gif.walk_blocks(make_gif(*[comment("x")] * 200, image()))
    assert "more than 100 blocks" in s.error


def test_sub_block_reading_is_bounds_checked():
    with pytest.raises(MalformedFileError, match="truncated"):
        gif.read_sub_blocks(b"\x05abc", 0, "test")
    with pytest.raises(MalformedFileError, match="no sub-block terminator"):
        gif.read_sub_blocks(b"\x01a", 0, "test")
    assert gif.read_sub_blocks(b"\x01a\x02bc\x00rest", 0, "test") == ([b"a", b"bc"], 6)


# -- sections ----------------------------------------------------------------------------


def test_comments():
    s = gif.walk_blocks(make_gif(comment("Made by Jane"), comment("x" * 600),
                                 comment(b"caf\xe9")))
    [section] = gif.comment_sections(s)
    values = [f.value for f in section.fields]
    assert values == ["Made by Jane", "x" * 600, "caf" + chr(0xE9)]
    assert all(f.sensitive for f in section.fields)


def test_plain_text():
    warnings = []
    s = gif.walk_blocks(make_gif(plain_text("Hello there", 5, 6), image()))
    [section] = gif.plain_text_sections(s, warnings)
    assert section.fields[0].value == "Hello there"
    assert "drawn at 5,6" in section.fields[0].note and warnings == []


def test_xmp_with_magic_trailer():
    warnings = []
    data = make_gif(xmp_extension(SAMPLE_XMP), image())
    s = gif.walk_blocks(data)
    assert s.error is None  # the magic trailer makes the raw packet walkable
    assert gif.xmp_packet(data, s.applications()[0], warnings) == SAMPLE_XMP.encode()
    values = fields(gif.xmp_sections(s, data, warnings))
    assert values["XMP packet.size"] == f"{len(SAMPLE_XMP.encode())} bytes"
    assert any(k.startswith("exif:GPS") for k in values)
    assert any(w.startswith("CRITICAL: XMP contains GPS") for w in warnings)


def test_xmp_in_ordinary_sub_blocks():
    warnings = []
    data = make_gif(application(b"XMP DataXMP", SAMPLE_XMP), image())
    s = gif.walk_blocks(data)
    assert gif.xmp_packet(data, s.applications()[0], warnings) == SAMPLE_XMP.encode()
    assert any("no magic trailer" in w for w in warnings)


def test_magic_trailer_shape():
    assert len(XMP_MAGIC_TRAILER) == 258 == len(gif.XMP_MAGIC_TRAILER)
    assert XMP_MAGIC_TRAILER == gif.XMP_MAGIC_TRAILER


def test_icc_profile():
    warnings = []
    s = gif.walk_blocks(make_gif(icc_extension(icc_profile()), image()))
    [section] = gif.icc_sections(s, warnings)
    assert section.key == "icc"
    assert section.get("device_manufacturer") is not None


def test_broken_icc_profile_is_reported():
    warnings = []
    s = gif.walk_blocks(make_gif(icc_extension(b"too short"), image()))
    [section] = gif.icc_sections(s, warnings)
    assert section.get("status").value == "present but not parsed"
    assert any("ICC profile not parsed" in w for w in warnings)


@pytest.mark.parametrize("ext,expected", [
    (loop(0), "forever"),
    (loop(3), 3),
    (loop(5, b"ANIMEXTS1.0"), 5),
])
def test_animation(ext, expected):
    s = gif.walk_blocks(make_gif(ext, graphic_control(), image(), graphic_control(), image()))
    values = fields(gif.animation_sections(s, []))
    assert values == {"frames": 2, "loop_count": expected}


def test_still_image_has_no_animation_section():
    assert gif.animation_sections(gif.walk_blocks(make_gif()), []) == []


def test_unknown_application_and_extension_types():
    data = make_gif(application(b"MGK8BIM0000", b"photoshop stuff"),
                    b"\x21\x42" + sub_blocks(b"odd"), image())
    [section] = gif.app_extension_sections(gif.walk_blocks(data))
    labels = [f.label for f in section.fields]
    assert labels == ["MGK8BIM0000", "Extension 0x42"]
    assert all(f.sensitive for f in section.fields)


def test_known_applications_are_not_listed_as_unknown():
    data = make_gif(loop(), icc_extension(icc_profile()), xmp_extension(SAMPLE_XMP), image())
    assert gif.app_extension_sections(gif.walk_blocks(data)) == []


@pytest.mark.parametrize("trailing,key,critical", [
    (b"PK\x03\x04" + bytes(30), "trailing", True),
    (b"secret", "trailing", True),
    (bytes(20), "padding", False),
])
def test_data_after_the_trailer(trailing, key, critical):
    data = make_gif(trailing=trailing)
    warnings = []
    [section] = gif.trailing_sections(gif.walk_blocks(data), data, warnings)
    assert section.key == key
    assert warnings[0].startswith("CRITICAL") == critical


def test_structure_section():
    s = gif.walk_blocks(make_gif(loop(), image(3, 2, 1, 1, interlace=True)))
    values = [f.value for f in gif.structure_section(s).fields]
    assert values[:3] == ["89a", "4 x 4", "4 colors"]
    assert "NETSCAPE2.0" in values[3] and "3 x 2 at 1,1, interlaced" in values[4]
