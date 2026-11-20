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
        (bytes(16), "padding (only 0x00 bytes)"),
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
