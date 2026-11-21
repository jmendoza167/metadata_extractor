"""WebP: stripping with a chunk whitelist."""

import pytest

from helpers.jpeg import SAMPLE_XMP, icc_profile
from helpers.tiff import ASCII, RATIONAL, SHORT, build_tiff
from helpers.webp import (ALPHA_1X1, VP8_1X1, anim, anmf, chunk, exif_chunk, extended, iccp,
                          lossless, lossy, riff, vp8l, xmp_chunk)
from metaex.core import exif
from metaex.core.errors import MalformedFileError
from metaex.formats import webp

FRAME_A = chunk("VP8L", vp8l(4, 4))
FRAME_B = chunk("VP8L", vp8l(4, 4, green2=9))


def exif_tiff(orientation=6):
    return build_tiff([(0x010F, ASCII, "ExampleCam"), (0x0112, SHORT, orientation)],
                      gps=[(0x0001, ASCII, "N"), (0x0002, RATIONAL, [(48, 1), (51, 1), (0, 1)])])


def metadata_webp(orientation=6, **kwargs):
    return extended(iccp(icc_profile()), anim(0), anmf(FRAME_A, chunk("abcd", b"in a frame")),
                    anmf(FRAME_B, duration=50), exif_chunk(exif_tiff(orientation)),
                    xmp_chunk(SAMPLE_XMP), chunk("prVt", b"private"),
                    trailing=b"after RIFF", **kwargs)


def still_webp(*extra, orientation=6):
    return extended(iccp(icc_profile()), FRAME_B, exif_chunk(exif_tiff(orientation)),
                    xmp_chunk(SAMPLE_XMP), *extra)


def types(data):
    return [c.fourcc for c in webp.walk_chunks(data).chunks]


def test_strip_keeps_only_whitelisted_chunks():
    cleaned = webp.strip(metadata_webp())
    assert types(cleaned) == ["VP8X", "ICCP", "ANIM", "ANMF", "ANMF", "EXIF"]
    s = webp.walk_chunks(cleaned)
    assert s.end_of_image == len(cleaned) and s.warnings == []
    assert [c.fourcc for c in s.chunks[3].children] == ["VP8L"]
    assert s.flags == webp.ICC_FLAG | webp.EXIF_FLAG | webp.ANIMATION_FLAG
    assert exif.is_orientation_only_tiff(s.first("EXIF").data)
    assert exif.orientation(exif.parse_tiff(s.first("EXIF").data)) == 6


def test_paranoid_and_no_orientation():
    cleaned = webp.strip(metadata_webp(), paranoid=True, keep_orientation=False)
    assert types(cleaned) == ["VP8X", "ANIM", "ANMF", "ANMF"]
    assert webp.walk_chunks(cleaned).flags == webp.ANIMATION_FLAG


def test_orientation_1_is_not_kept():
    assert "EXIF" not in types(webp.strip(metadata_webp(orientation=1)))


def test_image_chunks_are_copied_byte_for_byte():
    original = metadata_webp()
    cleaned = webp.strip(original)
    assert webp.image_data(cleaned) == webp.image_data(original)
    frames = [c for c in webp.walk_chunks(cleaned).chunks if c.fourcc == "ANMF"]
    assert frames[0].children[0].data == FRAME_A[8:-1]  # without the padding byte
    assert frames[1].data == webp.walk_chunks(original).all("ANMF")[1].data


def test_riff_size_and_padding_are_correct():
    original = extended(chunk("VP8L", vp8l(3, 3)), iccp(icc_profile() + b"x"),
                        chunk("abcd", b"odd", pad=b"Z"))
    cleaned = webp.strip(original)
    assert int.from_bytes(cleaned[4:8], "little") == len(cleaned) - 8
    s = webp.walk_chunks(cleaned)
    assert all(c.padding in (b"", b"\x00") for c in s.chunks)
    assert s.first("ICCP").data == icc_profile()  # cut to its declared size


@pytest.mark.parametrize("data", [lossy(), lossless(), lossless(7, 3, green2=1)])
def test_simple_files_stay_simple(data):
    cleaned = webp.strip(data + b"trailing")
    assert cleaned == data


def test_simple_file_becomes_extended_for_orientation():
    data = riff(chunk("VP8L", vp8l(5, 3, alpha_used=True)) + exif_chunk(exif_tiff(8)))
    cleaned = webp.strip(data)
    s = webp.walk_chunks(cleaned)
    assert types(cleaned) == ["VP8X", "VP8L", "EXIF"]
    assert webp.canvas_size(s.chunks[0]) == (5, 3)
    assert s.flags == webp.EXIF_FLAG | webp.ALPHA_FLAG
    assert webp.strip(data, keep_orientation=False) == riff(chunk("VP8L",
                                                                  vp8l(5, 3, alpha_used=True)))


def test_lossy_with_alpha():
    data = extended(chunk("ALPH", ALPHA_1X1[b"ALPH"]), chunk("VP8 ", ALPHA_1X1[b"VP8 "]),
                    xmp_chunk(b"<x/>"))
    cleaned = webp.strip(data)
    assert types(cleaned) == ["VP8X", "ALPH", "VP8 "]
    assert webp.walk_chunks(cleaned).flags == webp.ALPHA_FLAG


def test_stray_anim_chunk_in_a_still_image_is_dropped():
    cleaned = webp.strip(extended(anim(), chunk("VP8 ", VP8_1X1)))
    assert types(cleaned) == ["VP8X", "VP8 "] and webp.walk_chunks(cleaned).flags == 0


def test_strip_is_idempotent():
    for kwargs in ({}, {"paranoid": True}, {"keep_orientation": False}):
        once = webp.strip(metadata_webp(), **kwargs)
        assert webp.strip(once, **kwargs) == once


@pytest.mark.parametrize("data,message", [
    (lossless()[:-2], "truncated"),
    (riff(chunk("VP8L", vp8l(4, 4)), size=4), "no image found"),
    (riff(chunk("abcd", b"xx") + chunk("VP8L", vp8l(4, 4))), "not an image or VP8X"),
    (riff(chunk("VP8L", vp8l(4, 4)) + chunk("VP8L", vp8l(4, 4))), "does not hold exactly one image"),
    (extended(anmf(FRAME_A)), "without a valid ANIM chunk"),
    (extended(anmf(FRAME_A), anim()), "without a valid ANIM chunk"),
    (extended(anim(), anmf(chunk("abcd", b"x"))), "does not hold exactly one image"),
    (extended(anim(), anmf(FRAME_A), FRAME_B), "both animation frames and a still image"),
    (extended(xmp_chunk(b"<x/>")), "does not hold exactly one image"),
    (riff(chunk("VP8X", bytes(4)) + chunk("VP8L", vp8l(4, 4))), "VP8X chunk is too short"),
    (riff(chunk("VP8L", vp8l(4, 4)) + extended()[12:]), "is not the first chunk"),
])
def test_malformed_webp_is_refused(data, message):
    with pytest.raises(MalformedFileError, match=message):
        webp.strip(data)


def test_unreadable_bitstream_size_is_refused_when_vp8x_is_needed():
    data = riff(chunk("VP8 ", b"\x01" + bytes(20)) + exif_chunk(exif_tiff(6)))
    with pytest.raises(MalformedFileError, match="image size can't be read"):
        webp.strip(data)
    assert webp.strip(data, keep_orientation=False) == riff(chunk("VP8 ", b"\x01" + bytes(20)))


# -- verification ------------------------------------------------------------------------

from metaex.core.verify import verify  # noqa: E402


@pytest.mark.parametrize("kwargs", [{}, {"paranoid": True}, {"keep_orientation": False}])
def test_cleaned_webp_verifies_clean_with_matching_image_data(kwargs):
    original = metadata_webp()
    cleaned = webp.strip(original, **kwargs)
    result = verify(cleaned, original)
    assert result.clean, (result.leftovers, result.problems)
    assert result.image_data_match is True


def test_original_with_metadata_is_not_clean():
    result = verify(metadata_webp())
    assert not result.clean
    sections = {f.section for f in result.leftovers}
    assert {"EXIF", "GPS", "XMP", "Unknown chunks", "Trailing data", "WebP structure"} <= sections


def _replace_vp8x_flags(data, flags):
    d = bytearray(data)
    d[20] = flags
    return bytes(d)


def test_vp8x_flags_must_match_the_chunks():
    cleaned = webp.strip(metadata_webp())
    wrong = _replace_vp8x_flags(cleaned, cleaned[20] | webp.XMP_FLAG)
    result = verify(wrong)
    assert any("XMP flag does not match" in p for p in result.problems)
    reserved = _replace_vp8x_flags(cleaned, cleaned[20] | 0x01)
    assert any(f.label == "VP8X" for f in verify(reserved).leftovers)


def test_wrong_riff_size_is_a_problem():
    cleaned = webp.strip(still_webp())
    longer = riff(cleaned[12:] + b"\x00\x00", size=len(cleaned) - 8)  # 2 bytes of padding after
    result = verify(longer)
    assert any("RIFF size" in p for p in result.problems)


@pytest.mark.parametrize("data,problem", [
    (extended(FRAME_A, xmp_chunk(b"<x/>")), "not allowed in a cleaned WebP"),
    (extended(FRAME_A, exif_chunk(exif_tiff(6))), "more than the Orientation tag"),
    (extended(iccp(icc_profile() + b"xx"), FRAME_A), "invalid or has extra bytes"),
    (extended(anim(), anmf(FRAME_A, chunk("abcd", b"x"))), "other than image data"),
    (extended(FRAME_A, chunk("abcd", b"odd", pad=b"Q")), "padding byte is not zero"),
    (riff(FRAME_A + chunk("abcd", b"xy")), "a simple WebP holds only its image"),
])
def test_structure_leftovers(data, problem):
    result = verify(data)
    assert any(f.section == "WebP structure" and problem in f.value for f in result.leftovers), (
        result.leftovers)


def test_changed_image_data_is_detected():
    original = still_webp()
    cleaned = bytearray(webp.strip(original))
    pos = cleaned.find(b"VP8L") + 8
    cleaned[pos + 6] ^= 0x01
    result = verify(bytes(cleaned), original)
    assert result.image_data_match is False and not result.clean


def test_truncated_webp_is_a_problem():
    result = verify(webp.strip(still_webp())[:-4])
    assert any("not a complete, well-formed WebP" in p for p in result.problems)
