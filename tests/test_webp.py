"""WebP: RIFF chunk walking and the metadata sections."""

import pytest

from helpers.jpeg import SAMPLE_XMP, icc_profile
from helpers.tiff import ASCII, RATIONAL, SHORT, build_tiff
from helpers.webp import (ALPHA_1X1, VP8_1X1, anim, anmf, chunk, exif_chunk, extended, iccp,
                          lossless, lossy, riff, vp8l, vp8l_size, xmp_chunk)
from metaex.core.errors import MalformedFileError
from metaex.formats import webp


def values(sections):
    return {s.key: {f.key: f.value for f in s.fields} for s in sections}


def tiff(orientation=6):
    return build_tiff([(0x010F, ASCII, "ExampleCam"), (0x0112, SHORT, orientation)],
                      gps=[(0x0001, ASCII, "N"), (0x0002, RATIONAL, [(48, 1), (51, 1), (0, 1)])])


# -- the test builders -------------------------------------------------------------------


@pytest.mark.parametrize("width,height", [(1, 1), (4, 4), (13, 7), (300, 2)])
def test_vp8l_header(width, height):
    bitstream = vp8l(width, height, green2=10)
    assert bitstream[0] == 0x2F and vp8l_size(bitstream) == (width, height)
    assert webp.canvas_size(webp.Chunk("VP8L", 0, len(bitstream), bitstream)) == (width, height)


def test_sample_bitstreams():
    assert VP8_1X1[3:6] == b"\x9d\x01\x2a"
    assert webp.canvas_size(webp.Chunk("VP8 ", 0, len(VP8_1X1), VP8_1X1)) == (1, 1)
    assert {b"ALPH", b"VP8 "} <= set(ALPHA_1X1)


# -- walking -----------------------------------------------------------------------------


def test_simple_files():
    for data, fourcc in ((lossy(), "VP8 "), (lossless(), "VP8L")):
        s = webp.walk_chunks(data)
        assert [c.fourcc for c in s.chunks] == [fourcc]
        assert s.end_of_image == len(data) and s.error is None and s.warnings == []
        assert s.flags is None


def test_extended_file_with_everything():
    data = extended(iccp(icc_profile()), anim(3), anmf(chunk("VP8L", vp8l(4, 4))),
                    anmf(chunk("ALPH", ALPHA_1X1[b"ALPH"]), chunk("VP8 ", ALPHA_1X1[b"VP8 "]),
                         width=1, height=1),
                    exif_chunk(tiff()), xmp_chunk(SAMPLE_XMP), chunk("abcd", b"xyz"))
    s = webp.walk_chunks(data)
    assert s.error is None and s.warnings == []
    assert [c.fourcc for c in s.chunks] == ["VP8X", "ICCP", "ANIM", "ANMF", "ANMF", "EXIF",
                                            "XMP ", "abcd"]
    assert [c.fourcc for c in s.chunks[4].children] == ["ALPH", "VP8 "]
    assert s.flags == (webp.ICC_FLAG | webp.ANIMATION_FLAG | webp.ALPHA_FLAG | webp.EXIF_FLAG
                       | webp.XMP_FLAG)


def test_odd_sizes_are_padded():
    data = extended(chunk("VP8L", vp8l(4, 4)), chunk("abcd", b"odd"), xmp_chunk(b"<x/>"))
    s = webp.walk_chunks(data)
    odd = s.chunks[2]
    assert odd.size == 3 and odd.padding == b"\x00" and odd.end == odd.offset + 12
    assert s.warnings == []


def test_nonzero_padding_byte_is_a_warning():
    s = webp.walk_chunks(extended(chunk("VP8L", vp8l(4, 4)), chunk("abcd", b"odd", pad=b"X")))
    assert any("padding byte" in w and "is not zero" in w for w in s.warnings)


def test_missing_final_padding_byte_is_a_warning():
    body = chunk("VP8L", vp8l(4, 4)) + chunk("abcd", b"odd")[:-1]
    s = webp.walk_chunks(riff(body))
    assert s.error is None and any("has no padding byte" in w for w in s.warnings)


@pytest.mark.parametrize("data,message", [
    (b"RIFF\x00\x00\x00\x00WEBX", "no RIFF WEBP header"),
    (b"RIFX" + bytes(8), "no RIFF WEBP header"),
    (b"RIFF", "no RIFF WEBP header"),
])
def test_not_webp(data, message):
    with pytest.raises(MalformedFileError, match=message):
        webp.walk_chunks(data)


def test_riff_size_too_small():
    s = webp.walk_chunks(riff(chunk("VP8L", vp8l(4, 4)), size=2))
    assert "too small" in s.error


def test_riff_size_larger_than_file():
    data = lossless()
    s = webp.walk_chunks(data[:-3])
    assert "truncated" in s.error and s.end_of_image is None
    s = webp.walk_chunks(riff(chunk("VP8L", vp8l(4, 4)), size=1000))
    assert "RIFF size says the file has 1008 bytes" in s.error
    assert [c.fourcc for c in s.chunks] == ["VP8L"]  # what was found is kept


def test_odd_riff_size_is_a_warning():
    body = chunk("VP8L", vp8l(4, 4))
    s = webp.walk_chunks(riff(body + b"\x00", size=4 + len(body) + 1))
    assert any("is odd" in w for w in s.warnings)


def test_chunk_larger_than_the_riff_data():
    s = webp.walk_chunks(riff(b"VP8L" + (100).to_bytes(4, "little") + b"abc"))
    assert "truncated" in s.error


def test_short_anmf_frame():
    s = webp.walk_chunks(extended(anim(), chunk("ANMF", bytes(8))))
    assert "ANMF chunk at offset" in s.error and "too short" in s.error


def test_chunk_limit(monkeypatch):
    monkeypatch.setattr(webp, "MAX_CHUNKS", 50)
    s = webp.walk_chunks(extended(chunk("VP8L", vp8l(4, 4)), *[chunk("abcd")] * 60))
    assert "more than 50 chunks" in s.error


def test_vp8x_flag_mismatches():
    s = webp.walk_chunks(extended(chunk("VP8L", vp8l(4, 4)), xmp_chunk(b"<x/>"),
                                  flags=webp.EXIF_FLAG))
    assert any("EXIF flag is set, but the file has no EXIF chunk" in w for w in s.warnings)
    assert any("XMP flag is not set, but the file has a XMP chunk" in w for w in s.warnings)


def test_vp8x_not_first():
    body = chunk("VP8L", vp8l(4, 4)) + extended()[12:]
    assert any("is not the first chunk" in w for w in webp.walk_chunks(riff(body)).warnings)


# -- sections ----------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", [False, True])
def test_exif(prefix):
    warnings = []
    s = webp.walk_chunks(extended(chunk("VP8L", vp8l(4, 4)), exif_chunk(tiff(), prefix)))
    v = values(webp.exif_sections(s, warnings))
    assert v["exif"]["Make"] == "ExampleCam" and "Orientation" in v["exif"]
    assert "gps" in v


def test_broken_exif():
    warnings = []
    s = webp.walk_chunks(extended(chunk("VP8L", vp8l(4, 4)), exif_chunk(b"XX\x00\x00")))
    [section] = webp.exif_sections(s, warnings)
    assert section.get("status").value == "present but not parsed"


def test_xmp():
    warnings = []
    s = webp.walk_chunks(extended(chunk("VP8L", vp8l(4, 4)), xmp_chunk(SAMPLE_XMP)))
    v = values(webp.xmp_sections(s, warnings))["xmp"]
    assert any(k.startswith("exif:GPS") for k in v)
    assert any(w.startswith("CRITICAL: XMP contains GPS") for w in warnings)


def test_icc():
    s = webp.walk_chunks(extended(iccp(icc_profile()), chunk("VP8L", vp8l(4, 4))))
    [section] = webp.icc_sections(s, [])
    assert section.get("device_manufacturer") is not None
    s = webp.walk_chunks(extended(iccp(b"short"), chunk("VP8L", vp8l(4, 4))))
    warnings = []
    assert webp.icc_sections(s, warnings)[0].get("status") is not None and warnings


def test_animation():
    s = webp.walk_chunks(extended(anim(2, b"\x10\x20\x30\x40"), anmf(chunk("VP8L", vp8l(4, 4))),
                                  anmf(chunk("VP8L", vp8l(4, 4)))))
    assert values(webp.animation_sections(s, []))["animation"] == {
        "frames": 2, "loop_count": 2, "background": "#302010, alpha 64"}


def test_unknown_chunks_top_level_and_in_frames():
    s = webp.walk_chunks(extended(anim(), anmf(chunk("VP8L", vp8l(4, 4)), chunk("xyzw", b"1")),
                                  chunk("abcd", b"hello")))
    [section] = webp.unknown_sections(s)
    assert [f.label for f in section.fields] == ["xyzw", "abcd"]
    assert "inside the ANMF frame" in section.fields[0].note
    assert all(f.sensitive for f in section.fields)


@pytest.mark.parametrize("trailing,key,critical", [
    (b"PK\x03\x04" + bytes(30), "trailing", True),
    (b"secret", "trailing", True),
    (bytes(10), "padding", False),
])
def test_data_after_the_riff_end(trailing, key, critical):
    data = riff(chunk("VP8L", vp8l(4, 4)), trailing)
    warnings = []
    [section] = webp.trailing_sections(webp.walk_chunks(data), data, warnings)
    assert section.key == key and warnings[0].startswith("CRITICAL") == critical


def test_structure_section():
    s = webp.walk_chunks(extended(anim(), anmf(chunk("VP8L", vp8l(5, 3)), width=5, height=3)))
    fields = {f.label: f.value for f in webp.structure_section(s).fields}
    assert fields["Canvas and flags"] == "5 x 3; flags: animation"
    assert fields["  VP8L"].endswith("lossless, 5 x 3")
