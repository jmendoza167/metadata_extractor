"""PNG parsing: chunks, CRCs, text, ancillary chunks."""

import struct

import pytest

from helpers.png import SIGNATURE, chunk, ihdr, make_png, text
from metaex.core.errors import MalformedFileError
from metaex.formats import png


# -- chunk walking -------------------------------------------------------------------


def test_walk_simple_png():
    data = make_png(text("Software", "Paint"))
    s = png.walk_chunks(data)
    assert [c.type for c in s.chunks] == ["IHDR", "IDAT", "tEXt", "IEND"]
    assert s.error is None and s.warnings == [] and s.crc_errors == []
    assert s.end_of_image == len(data)
    assert s.chunks[0].offset == 8 and s.chunks[0].length == 13
    assert s.chunks[0].critical and not s.chunks[2].critical
    assert all(c.crc_ok for c in s.chunks)


def test_not_a_png():
    with pytest.raises(MalformedFileError, match="no PNG signature"):
        png.walk_chunks(b"\xff\xd8\xff\xe0")


def test_bad_crc_is_a_warning_and_walking_continues():
    bad = chunk("tEXt", b"Comment\x00hi", crc=0x12345678)
    data = make_png(bad, text("Author", "Jane"))
    s = png.walk_chunks(data)
    assert [c.type for c in s.chunks] == ["IHDR", "IDAT", "tEXt", "tEXt", "IEND"]
    assert not s.chunks[2].crc_ok and s.chunks[3].crc_ok
    assert len(s.crc_errors) == 1 and "bad CRC" in s.crc_errors[0]
    assert s.error is None


def test_missing_iend_is_a_warning():
    s = png.walk_chunks(make_png(iend=False))
    assert s.end_of_image is None and s.error is None
    assert any("missing IEND" in w for w in s.warnings)


@pytest.mark.parametrize(
    "tail,message",
    [
        (b"\x00\x00", "truncated"),                                   # partial header
        (struct.pack(">I", 100) + b"tEXt" + b"abc", "truncated"),     # data too short
        (struct.pack(">I", 2**31) + b"tEXt", "invalid length"),
        (struct.pack(">I", 0) + b"t3Xt" + bytes(4), "invalid chunk type"),
    ],
)
def test_malformed_chunks_stop_the_walk(tail, message):
    data = SIGNATURE + ihdr() + tail
    s = png.walk_chunks(data)
    assert [c.type for c in s.chunks] == ["IHDR"]
    assert message in s.error
    assert any(w.startswith("malformed PNG") for w in s.warnings)


def test_chunk_limit(monkeypatch):
    monkeypatch.setattr(png, "MAX_CHUNKS", 5)
    data = make_png(*[text("k", "v")] * 10)
    s = png.walk_chunks(data)
    assert len(s.chunks) == 5
    assert "more than 5 chunks" in s.error


def test_first_chunk_must_be_ihdr():
    data = SIGNATURE + text("Comment", "x") + ihdr() + chunk("IEND")
    s = png.walk_chunks(data)
    assert any("not IHDR" in w for w in s.warnings)


def test_structure_section_lists_chunks():
    data = make_png(chunk("tEXt", b"a\x00b", crc=1))
    section = png.structure_section(png.walk_chunks(data))
    values = [(f.label, f.value) for f in section.fields]
    assert values[0] == ("IHDR", "offset 8, 13 bytes, critical")
    assert values[2][0] == "tEXt" and values[2][1].endswith("ancillary, BAD CRC")


@pytest.mark.parametrize(
    "trailing,content",
    [
        (b"PK\x03\x04" + bytes(30), "ZIP"),
        (b"secret notes", "unrecognized data"),
        (bytes(15) + b"\x01", "unrecognized data"),  # not only zeros
        (b"\xff" * 16, "unrecognized data"),  # JPEG pads with 0xFF; PNG writers don't
    ],
)
def test_data_after_iend_is_critical(trailing, content):
    data = make_png(trailing=trailing)
    s = png.walk_chunks(data)
    warnings = []
    sections = png.trailing_sections(s, data, warnings)
    fields = {f.key: f.value for f in sections[0].fields}
    assert fields["size"] == f"{len(trailing)} bytes"
    assert fields["offset"] == s.end_of_image
    assert fields["content"] == content
    assert warnings[0].startswith("CRITICAL:")


def test_zero_padding_after_iend_is_a_warning_not_critical():
    from metaex.core import risk

    result = png.parse(make_png(trailing=bytes(16)))
    padding = next(s for s in result["sections"] if s.title == "Trailing data")
    assert padding.key == "padding"
    fields = {f.key: f.value for f in padding.fields}
    assert fields["content"] == "padding (only 0x00 bytes)"
    assert "16 bytes of padding after the end of the image" in result["warnings"]
    assert not any(w.startswith("CRITICAL") for w in result["warnings"])
    assert all(f.level == risk.LOW for f in risk.assess([padding]))
    # strip still drops it
    cleaned = png.strip(make_png(trailing=bytes(16)))
    assert png.walk_chunks(cleaned).end_of_image == len(cleaned)
    assert png.removed_sizes(make_png(trailing=bytes(16))) == {"trailing": 16}


def test_no_trailing_section_without_trailing_data():
    data = make_png()
    assert png.trailing_sections(png.walk_chunks(data), data, []) == []


# -- text chunks ---------------------------------------------------------------------

from helpers.png import itxt, zlib_bomb, ztxt  # noqa: E402
from metaex.core.inflate import Budget  # noqa: E402


def sections_of(*chunks, budget=None):
    warnings = []
    s = png.walk_chunks(make_png(*chunks))
    out = png.text_sections(s, budget or Budget(), warnings)
    return {sec.key: sec for sec in out}, warnings


def values(section):
    return {f.key: f.value for f in section.fields}


def test_text_ztxt_itxt():
    title = "Caf" + chr(0xE9) + " " + chr(0x2615)  # needs UTF-8
    secs, warnings = sections_of(
        text("Author", "Jane Doe"),
        ztxt("Comment", "compressed comment"),
        itxt("Title", title, language="fr", translated="Titre"),
        itxt("Description", "long text " * 50, compress=True),
    )
    assert warnings == []
    v = values(secs["text"])
    assert v["Author"] == "Jane Doe"
    assert v["Comment"] == "compressed comment"
    assert v["Title"] == title
    assert v["Description"] == "long text " * 50
    notes = {f.key: f.note for f in secs["text"].fields}
    assert notes["Comment"] == "zTXt, compressed"
    assert notes["Title"] == "iTXt; language fr; translated keyword: Titre"
    assert all(f.sensitive for f in secs["text"].fields)


def test_unknown_keywords_are_shown():
    secs, _ = sections_of(text("My Secret Key", "value"), text("Author", "a"),
                          text("Author", "b"))
    v = values(secs["text"])
    assert v["My Secret Key"] == "value"
    assert v["Author"] == "a" and v["Author (2)"] == "b"
    assert secs["text"].get("My Secret Key").note == "tEXt; non-standard keyword"


@pytest.mark.parametrize("keyword", ["parameters", "prompt", "workflow"])
def test_ai_generation_data(keyword):
    secs, _ = sections_of(text(keyword, "a cat, Steps: 20, Seed: 42"), text("Software", "x"))
    assert "text" in secs and "Software" in values(secs["text"])
    ai = secs["ai"]
    assert ai.title == "AI generation data"
    assert values(ai)[keyword] == "a cat, Steps: 20, Seed: 42"


def test_xmp_in_itxt_is_parsed():
    from helpers.jpeg import SAMPLE_XMP

    secs, warnings = sections_of(itxt("XML:com.adobe.xmp", SAMPLE_XMP))
    v = values(secs["xmp"])
    assert v["dc:creator"] == "Jane Doe"
    assert v["xmpMM:DocumentID"] == "xmp.did:1234"
    assert any(w.startswith("CRITICAL: XMP contains GPS") for w in warnings)


def test_xmp_with_doctype_is_refused():
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
    secs, warnings = sections_of(itxt("XML:com.adobe.xmp", evil))
    assert values(secs["xmp"])["XMP packet.status"] == "present but not parsed"
    assert any("DOCTYPE" in w for w in warnings)


def test_ztxt_bomb_is_capped():
    bomb = ztxt("Comment", "", compressed=zlib_bomb(64 * 1024 * 1024))
    secs, warnings = sections_of(bomb, text("Author", "still read"))
    v = values(secs["text"])
    assert v["Comment"].startswith("(compressed text not shown")
    assert v["Author"] == "still read"
    assert any("16 MiB limit" in w for w in warnings)


def test_budget_is_shared_between_chunks():
    big = "x" * 600
    secs, warnings = sections_of(ztxt("Comment", big), ztxt("Title", big), budget=Budget(1000))
    v = values(secs["text"])
    assert v["Comment"] == big
    assert v["Title"].startswith("(compressed text not shown")


def test_text_without_separator():
    from helpers.png import chunk

    secs, warnings = sections_of(chunk("tEXt", b"no separator"))
    assert "no keyword separator" in values(secs["text"])["no separator"]
    assert any("no keyword separator" in w for w in warnings)


def test_bad_compression_is_reported():
    from helpers.png import chunk

    secs, warnings = sections_of(ztxt("Comment", "", compressed=b"garbage"),
                                 ztxt("Title", "x", method=1),
                                 chunk("iTXt", b"Author\x00\x01"))
    v = values(secs["text"])
    assert "could not be read" in v["Comment"]
    assert "unknown compression method" in v["Title"]
    assert "too short" in v["Author"]
    assert len(warnings) == 3


def test_extra_data_after_compressed_text_is_warned():
    import zlib

    secs, warnings = sections_of(ztxt("Comment", "", compressed=zlib.compress(b"hi") + b"XX"))
    assert values(secs["text"])["Comment"] == "hi"
    assert any("2 bytes of extra data" in w for w in warnings)


def test_long_text_is_cut_in_the_report():
    secs, _ = sections_of(text("Comment", "a" * (png.MAX_SHOWN_CHARS + 10)))
    assert values(secs["text"])["Comment"].endswith("(10 more characters)")


# -- tIME, eXIf, iCCP, color, caBX, APNG, unknown chunks ---------------------------

import struct as _struct  # noqa: E402

from helpers.jpeg import icc_profile  # noqa: E402
from helpers.png import (actl, exif_chunk, fctl, fdat, iccp, phys,  # noqa: E402
                         time_chunk)
from helpers.png import chunk as png_chunk  # noqa: E402
from helpers.tiff import ASCII, RATIONAL, SHORT, build_tiff  # noqa: E402


def structure_of(*chunks, **kwargs):
    return png.walk_chunks(make_png(*chunks, **kwargs))


def test_time():
    warnings = []
    [section] = png.time_sections(structure_of(time_chunk()), warnings)
    assert values(section) == {"tIME": "2024-05-01T12:30:00Z"}
    assert section.fields[0].sensitive and warnings == []


@pytest.mark.parametrize("raw,expected", [
    (_struct.pack(">HBBBBB", 2024, 13, 1, 0, 0, 0), "invalid date 2024-13-01T00:00:00Z"),
    (_struct.pack(">HBBBBB", 2016, 12, 31, 23, 59, 60), "2016-12-31T23:59:60Z"),
    (b"\x07\xe8\x05", "(malformed: 3 bytes)"),
])
def test_time_invalid(raw, expected):
    [section] = png.time_sections(structure_of(png_chunk("tIME", raw)), [])
    assert values(section)["tIME"] == expected


GPS_ENTRIES = [(0x0001, ASCII, "N"), (0x0002, RATIONAL, [(48, 1), (51, 1), (3012, 100)]),
               (0x0003, ASCII, "E"), (0x0004, RATIONAL, [(2, 1), (17, 1), (4013, 100)])]


@pytest.mark.parametrize("prefix", [False, True])
@pytest.mark.parametrize("order", ["<", ">"])
def test_exif_with_gps(prefix, order):
    tiff = build_tiff([(0x010F, ASCII, "ExampleCam"), (0x0112, SHORT, 6)], order=order,
                      gps=GPS_ENTRIES)
    warnings = []
    secs = {s.key: s for s in png.exif_sections(structure_of(exif_chunk(tiff, prefix)),
                                                warnings)}
    assert values(secs["exif"])["Make"] == "ExampleCam"
    assert "Orientation" in values(secs["exif"])
    assert "48.858" in str(values(secs["gps"])["coordinates"])


def test_broken_exif_is_reported():
    warnings = []
    [section] = png.exif_sections(structure_of(exif_chunk(b"XX\x00\x00")), warnings)
    assert values(section)["status"] == "present but not parsed"
    assert any("EXIF not parsed" in w for w in warnings)


def test_iccp():
    profile = icc_profile()
    [section] = png.icc_sections(structure_of(iccp(profile, "Jane display")), Budget(), [])
    v = values(section)
    assert v["profile_name"] == "Jane display"
    assert section.fields[0].sensitive
    assert v["device_manufacturer"] == "ACME" and v["description"] == "Test RGB profile"


def test_iccp_generic_name_is_not_sensitive():
    [section] = png.icc_sections(structure_of(iccp(icc_profile())), Budget(), [])
    assert not section.get("profile_name").sensitive


@pytest.mark.parametrize("bad", [b"no separator", b"name\x00\x01" + b"x" * 10,
                                 b"name\x00\x00not zlib"])
def test_broken_iccp(bad):
    warnings = []
    [section] = png.icc_sections(structure_of(png_chunk("iCCP", bad)), Budget(), warnings)
    assert values(section)["status"] == "present but not parsed"
    assert warnings


def test_iccp_bomb_is_capped():
    warnings = []
    [section] = png.icc_sections(
        structure_of(iccp(b"", compressed=zlib_bomb(64 * 1024 * 1024))), Budget(), warnings)
    assert values(section)["status"] == "present but not parsed"
    assert any("16 MiB limit" in w for w in warnings)


def test_color_chunks():
    chunks = [
        png_chunk("sRGB", b"\x00"),
        png_chunk("gAMA", _struct.pack(">I", 45455)),
        png_chunk("cHRM", _struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000,
                                       15000, 6000)),
        png_chunk("cICP", bytes([12, 13, 0, 1])),
        png_chunk("mDCv", _struct.pack(">8H2I", *([10000] * 8), 10000000, 50)),
        png_chunk("cLLI", _struct.pack(">II", 10000000, 4000000)),
        png_chunk("sBIT", bytes([5, 6, 5])),
        png_chunk("bKGD", b"\x00\xff\x00\xff\x00\xff"),
        phys(),
        png_chunk("oFFs", _struct.pack(">iiB", -5, 10, 0)),
        png_chunk("pCAL", b"temperature\x00rest"),
        png_chunk("sCAL", b"\x011.5\x002.5"),
    ]
    warnings = []
    [section] = png.color_sections(structure_of(before_idat=tuple(chunks)), warnings)
    v = values(section)
    assert warnings == []
    assert v["sRGB"] == "sRGB color space, perceptual intent"
    assert v["gAMA"] == "gamma 0.45455"
    assert v["cHRM"].startswith("white 0.3127,0.329; red 0.64,0.33")
    assert v["cICP"] == "Display P3, sRGB, matrix 0, full range"
    assert v["mDCv"].endswith("luminance 0.005 to 1000 cd/m2")
    assert v["cLLI"] == "max 1000 cd/m2, frame average max 400 cd/m2"
    assert v["sBIT"] == "significant bits 5, 6, 5"
    assert v["pHYs"] == "72 x 72 dpi (2835 x 2835 pixels per meter)"
    assert v["oFFs"] == "-5, 10 pixels"
    assert v["pCAL"] == '"temperature"'
    assert v["sCAL"] == "1.5 x 2.5 meters per pixel"


def test_palette_chunks():
    from helpers.png import PALETTE

    plte = png_chunk("PLTE", bytes(range(12)))
    chunks = (plte, png_chunk("hIST", bytes(8)),
              png_chunk("sPLT", b"My palette\x00\x08" + bytes(12)))
    [section] = png.color_sections(structure_of(before_idat=chunks, color=PALETTE), [])
    v = values(section)
    assert v["hIST"] == "4 entries"
    assert v["sPLT"] == '"My palette", 2 entries, 8-bit'


@pytest.mark.parametrize("chunk_type,data", [("gAMA", b"\x00"), ("pHYs", bytes(3)),
                                             ("sPLT", b"noname"), ("sCAL", b"\x01")])
def test_malformed_color_chunks(chunk_type, data):
    warnings = []
    [section] = png.color_sections(structure_of(png_chunk(chunk_type, data)), warnings)
    assert values(section)[chunk_type].startswith("(malformed")
    assert len(warnings) == 1


def test_content_credentials():
    [section] = png.content_credentials_sections(
        structure_of(png_chunk("caBX", b"jumb" + bytes(100))))
    assert section.title == "Content Credentials"
    assert values(section)["caBX"].startswith("104 bytes at offset")
    assert section.fields[0].sensitive


def test_animation():
    raw = bytes(4 * 13)
    chunks = (actl(3, 0), fctl(0))
    data = make_png(fctl(1), fdat(2, raw), fctl(3), fdat(4, raw), before_idat=chunks)
    [section] = png.animation_sections(png.walk_chunks(data), [])
    assert values(section) == {"frames": 3, "plays": "forever", "frame_controls": 3,
                               "frame_data": 2}


def test_unknown_chunks_are_listed():
    s = structure_of(png_chunk("prVt", b"secret"), png_chunk("zzZZ", b"x"))
    [section] = png.unknown_sections(s)
    labels = [(f.label, f.value.split(" at ")[0], f.note) for f in section.fields]
    assert labels == [("prVt", "6 bytes", "ancillary; contents not examined"),
                      ("zzZZ", "1 bytes", "ancillary; contents not examined")]


def test_known_chunks_are_not_unknown():
    assert png.unknown_sections(structure_of(time_chunk(), text("a", "b"))) == []
