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
