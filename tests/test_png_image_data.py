"""PNG: hidden data inside the image data (IDAT / fdAT) streams."""

import struct
import time
import zlib

import pytest

from helpers.png import (ADAM7, GRAY, PALETTE, RGB, RGBA, SIGNATURE, actl, chunk,
                         expected_raw_size, fctl, ihdr, make_png, raw_image, text, variants,
                         zlib_bomb)
from metaex.core import risk
from metaex.core.errors import HiddenDataError, MalformedFileError
from metaex.core.verify import verify
from metaex.formats import png


def png_with_idat(idat: bytes, *, width=4, height=4, depth=8, color=RGB, interlace=0) -> bytes:
    return (SIGNATURE + ihdr(width, height, depth, color, interlace)
            + chunk("IDAT", idat) + chunk("IEND"))


RAW = raw_image()


def hidden(data):
    return [s for s in png.parse(data)["sections"] if s.key == "hidden_image_data"]


def critical(data):
    return [w for w in png.parse(data)["warnings"] if w.startswith("CRITICAL")]


def malformed(data):
    return [w for w in png.parse(data)["warnings"] if w.startswith("malformed PNG")]


@pytest.mark.parametrize("width,height", [(1, 1), (3, 5), (8, 8), (9, 7), (13, 2), (33, 17)])
@pytest.mark.parametrize("depth,color", [(1, GRAY), (2, GRAY), (8, RGB), (16, RGBA),
                                         (4, PALETTE)])
@pytest.mark.parametrize("interlace", [0, 1])
def test_expected_size_matches_the_scanlines(width, height, depth, color, interlace):
    header = png.ImageHeader(width, height, depth, color, interlace)
    assert png.raw_size(header) == expected_raw_size(width, height, depth, color, interlace)


def test_adam7_table_matches_the_test_builder():
    assert png.ADAM7 == ADAM7


@pytest.mark.parametrize("name", sorted(variants()))
def test_real_images_have_no_hidden_data(name):
    data = variants()[name]
    assert hidden(data) == [] and malformed(data) == [] and critical(data) == []
    check = png.check_image_data(png.walk_chunks(data))
    assert check.problems == [] and check.hidden == []
    assert all(s.size == s.expected for s in check.streams)
    if name == "apng":
        assert [s.stream.name for s in check.streams] == [
            "image data (IDAT)", "animation frame 2 (fdAT)", "animation frame 3 (fdAT)"]


def test_extra_decompressed_bytes_are_critical():
    data = png_with_idat(zlib.compress(RAW + b"a hidden message"))
    [section] = hidden(data)
    field = section.fields[0]
    assert field.value == f"16 bytes beyond the {len(RAW)} the image needs"
    assert field.sensitive
    assert any("image data (IDAT) holds 16 bytes more" in w for w in critical(data))
    assert {f.level for f in risk.assess([section])} == {risk.CRITICAL}


def test_bytes_after_the_zlib_stream_are_critical():
    data = png_with_idat(zlib.compress(RAW) + b"hidden!")
    [section] = hidden(data)
    assert section.fields[0].label == "image data (IDAT): after the compressed data"
    assert section.fields[0].value == "7 bytes"
    assert any("7 bytes after the end of the compressed image data" in w
               for w in critical(data))


def test_hidden_data_split_over_several_idat_chunks():
    compressed = zlib.compress(RAW + bytes(100)) + b"tail"
    idats = b"".join(chunk("IDAT", compressed[i : i + 5]) for i in range(0, len(compressed), 5))
    data = SIGNATURE + ihdr() + idats + chunk("IEND")
    labels = [f.label for f in hidden(data)[0].fields]
    assert labels == ["image data (IDAT): extra data",
                      "image data (IDAT): after the compressed data"]


def test_hidden_data_in_an_animation_frame():
    frame = zlib.compress(raw_image(seed=5) + b"secret")
    data = make_png(fctl(1), chunk("fdAT", struct.pack(">I", 2) + frame),
                    before_idat=(actl(2), fctl(0)))
    [section] = hidden(data)
    assert section.fields[0].label == "animation frame 2 (fdAT): extra data"


def test_frame_size_comes_from_fctl():
    small = raw_image(2, 3, seed=5)
    data = make_png(fctl(1, 2, 3), chunk("fdAT", struct.pack(">I", 2) + zlib.compress(small)),
                    before_idat=(actl(2), fctl(0)))
    assert hidden(data) == [] and malformed(data) == []


@pytest.mark.parametrize("idat,message", [
    (zlib.compress(RAW[:-3]), "too short"),
    (zlib.compress(RAW)[:20], "too short"),
    (b"this is not zlib data", "not valid compressed data"),
    (b"", "too short"),
])
def test_malformed_image_data(idat, message):
    data = png_with_idat(idat)
    assert any(message in w for w in malformed(data))
    with pytest.raises(MalformedFileError, match=message):
        png.strip(data)


def test_unterminated_stream_is_malformed():
    c = zlib.compressobj()
    data = png_with_idat(c.compress(RAW) + c.flush(zlib.Z_SYNC_FLUSH))
    assert any("does not end properly" in w for w in malformed(data))
    with pytest.raises(MalformedFileError, match="does not end properly"):
        png.strip(data)


@pytest.mark.parametrize("ihdr_data,message", [
    (struct.pack(">IIBBBBB", 0, 4, 8, 2, 0, 0, 0), "invalid image size"),
    (struct.pack(">IIBBBBB", 4, 4, 3, 2, 0, 0, 0), "invalid bit depth"),
    (struct.pack(">IIBBBBB", 4, 4, 8, 5, 0, 0, 0), "invalid bit depth"),
    (struct.pack(">IIBBBBB", 4, 4, 8, 2, 1, 0, 0), "unknown compression"),
    (struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 2), "unknown compression"),
])
def test_invalid_ihdr(ihdr_data, message):
    data = (SIGNATURE + chunk("IHDR", ihdr_data) + chunk("IDAT", zlib.compress(RAW))
            + chunk("IEND"))
    assert any(message in w for w in malformed(data))
    with pytest.raises(MalformedFileError, match=message):
        png.strip(data)


@pytest.mark.parametrize("chunks,message", [
    ((fctl(1), chunk("fdAT", b"\x00\x00")), "too short"),
    ((chunk("fdAT", bytes(4) + zlib.compress(RAW)),), "without a frame control chunk"),
    ((fctl(1),), "has no image data"),
    ((chunk("fcTL", bytes(10)),), "instead of 26"),
])
def test_broken_animation_layout(chunks, message):
    data = make_png(*chunks)
    assert any(message in w for w in malformed(data))
    with pytest.raises(MalformedFileError, match=message):
        png.strip(data)


def test_idat_chunks_must_be_consecutive():
    compressed = zlib.compress(RAW)
    data = (SIGNATURE + ihdr() + chunk("IDAT", compressed[:10]) + text("a", "b")
            + chunk("IDAT", compressed[10:]) + chunk("IEND"))
    assert any("not next to the other IDAT chunks" in w for w in malformed(data))


def test_strip_refuses_hidden_image_data():
    data = png_with_idat(zlib.compress(RAW) + b"hidden!")
    with pytest.raises(HiddenDataError, match="7 bytes after the compressed data") as exc:
        png.strip(data)
    assert "--rebuild-image-data" in str(exc.value)
    assert "pixels stay identical" in str(exc.value)


# -- rebuilding the image data -----------------------------------------------------------


def idat_stream(data):
    return b"".join(c.data for c in png.walk_chunks(data).all("IDAT"))


@pytest.mark.parametrize("idat", [
    zlib.compress(RAW + b"a hidden message"),
    zlib.compress(RAW) + b"hidden!",
    zlib.compress(RAW + bytes(1000)) + b"both",
])
def test_rebuild_removes_hidden_data_and_keeps_pixels(idat):
    original = png_with_idat(idat)
    cleaned = png.strip(original, rebuild_image_data=True)
    assert zlib.decompress(idat_stream(cleaned)) == RAW
    assert hidden(cleaned) == [] and malformed(cleaned) == []
    result = verify(cleaned, original)
    assert result.clean and result.image_data_match is True
    assert result.image_data_rebuilt
    assert result.image_sha256 == result.original_image_sha256 == png.decoded_image_digest(
        original)
    # stripping again keeps the rebuilt image data as it is
    assert png.strip(cleaned) == cleaned == png.strip(cleaned, rebuild_image_data=True)


def test_rebuild_of_interlaced_multi_chunk_image(monkeypatch):
    monkeypatch.setattr(png, "REBUILT_IDAT_SIZE", 50)
    raw = raw_image(33, 17, interlace=1)
    compressed = zlib.compress(raw + b"x" * 500)
    idats = b"".join(chunk("IDAT", compressed[i : i + 40])
                     for i in range(0, len(compressed), 40))
    original = (SIGNATURE + ihdr(33, 17, interlace=1) + text("Author", "J") + idats
                + chunk("IEND"))
    cleaned = png.strip(original, rebuild_image_data=True)
    chunks = png.walk_chunks(cleaned).chunks
    assert [c.type for c in chunks][:2] == ["IHDR", "IDAT"]
    assert len([c for c in chunks if c.type == "IDAT"]) > 1
    assert all(c.length <= 50 for c in chunks if c.type == "IDAT")
    assert zlib.decompress(idat_stream(cleaned)) == raw
    assert verify(cleaned, original).image_data_rebuilt


def test_rebuild_option_does_nothing_without_hidden_data():
    data = variants()["rgb8"]
    assert png.strip(data, rebuild_image_data=True) == png.strip(data)
    assert idat_stream(png.strip(data, rebuild_image_data=True)) == idat_stream(data)


def test_hidden_data_in_animation_frames_is_refused_even_with_rebuild():
    frame = zlib.compress(raw_image(seed=5)) + b"secret"
    data = make_png(fctl(1), chunk("fdAT", struct.pack(">I", 2) + frame),
                    before_idat=(actl(2), fctl(0)))
    with pytest.raises(HiddenDataError, match="can't rebuild animation frames"):
        png.strip(data, rebuild_image_data=True)


def test_rebuilt_image_with_different_pixels_does_not_match():
    original = png_with_idat(zlib.compress(RAW) + b"hidden!")
    other = png_with_idat(zlib.compress(raw_image(seed=99), 9))
    result = verify(other, original)
    assert result.image_data_match is False and not result.image_data_rebuilt
    assert "image data differs from the original" in result.problems


def test_verify_reports_hidden_image_data_as_leftover():
    result = verify(png_with_idat(zlib.compress(RAW + b"x")))
    assert not result.clean
    assert [f.section for f in result.leftovers] == ["Hidden data in image data"]


def test_idat_bomb_is_bounded():
    data = png_with_idat(zlib_bomb(256 * 1024 * 1024))
    start = time.monotonic()
    [section] = hidden(data)
    assert section.fields[0].value.startswith("at least 16777216 bytes")
    assert time.monotonic() - start < 15


def test_very_large_images_are_skipped_with_a_note(monkeypatch):
    monkeypatch.setattr(png, "MAX_CHECKED_IMAGE_SIZE", 10)
    data = png_with_idat(zlib.compress(RAW) + b"hidden!")
    result = png.parse(data)
    assert not [s for s in result["sections"] if s.key == "hidden_image_data"]
    assert any("was not checked for hidden data" in w for w in result["warnings"])
    assert png.strip(data)  # not checked, so not refused
