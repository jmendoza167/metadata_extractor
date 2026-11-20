"""PNG stripping: chunk whitelist, kept color chunks, orientation, paranoid mode."""

import struct
import zlib

import pytest

from helpers.jpeg import SAMPLE_XMP, icc_profile
from helpers.png import (PALETTE, SIGNATURE, actl, chunk, exif_chunk, fctl, fdat, iccp, ihdr,
                         itxt, make_png, phys, text, time_chunk, ztxt)
from helpers.tiff import ASCII, RATIONAL, SHORT, build_tiff
from metaex.core.errors import MalformedFileError
from metaex.formats import png

GAMA = chunk("gAMA", struct.pack(">I", 45455))
SRGB = chunk("sRGB", b"\x00")
CHRM = chunk("cHRM", struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000))
CICP = chunk("cICP", bytes([12, 13, 0, 1]))
MDCV = chunk("mDCv", bytes(24))
CLLI = chunk("cLLI", bytes(8))


def exif_tiff(orientation=6):
    return build_tiff([(0x010F, ASCII, "ExampleCam"), (0x0112, SHORT, orientation)],
                      gps=[(0x0001, ASCII, "N"), (0x0002, RATIONAL, [(48, 1), (51, 1), (0, 1)])])


def metadata_png(orientation=6, **kwargs):
    before = (exif_chunk(exif_tiff(orientation)), iccp(icc_profile(), "Jane's display"),
              SRGB, GAMA, CHRM, CICP, MDCV, CLLI, phys(), chunk("bKGD", bytes(6)),
              chunk("sBIT", b"\x08\x08\x08"), time_chunk(), text("Author", "Jane"))
    after = (text("parameters", "a cat"), ztxt("Comment", "hello"),
             itxt("XML:com.adobe.xmp", SAMPLE_XMP), chunk("caBX", bytes(40)),
             chunk("prVt", b"private"))
    return make_png(*after, before_idat=before, trailing=b"after IEND", **kwargs)


def types(data):
    return [c.type for c in png.walk_chunks(data).chunks]


def test_strip_keeps_only_whitelisted_chunks():
    cleaned = png.strip(metadata_png())
    assert types(cleaned) == ["IHDR", "eXIf", "iCCP", "sRGB", "gAMA", "cHRM", "cICP",
                              "mDCv", "cLLI", "pHYs", "IDAT", "IEND"]
    s = png.walk_chunks(cleaned)
    assert s.end_of_image == len(cleaned)  # nothing after IEND
    assert s.crc_errors == []


def test_paranoid_keeps_only_srgb():
    cleaned = png.strip(metadata_png(), paranoid=True)
    assert types(cleaned) == ["IHDR", "eXIf", "sRGB", "IDAT", "IEND"]


def test_image_chunks_are_copied_byte_for_byte():
    original = metadata_png(idat_parts=3)
    cleaned = png.strip(original)
    def raw(data):
        s = png.walk_chunks(data)
        return [data[c.offset:c.end] for c in s.chunks if c.type in png.IMAGE_CHUNKS]
    assert raw(cleaned) == raw(original)
    assert png.image_data(cleaned) == png.image_data(original)


def test_kept_color_chunks_are_unchanged():
    original = metadata_png()
    cleaned = png.strip(original)
    for c in (SRGB, GAMA, CHRM, CICP, MDCV, CLLI, phys()):
        assert c in cleaned


@pytest.mark.parametrize("orientation,expected", [(6, True), (1, False), (8, True)])
def test_orientation(orientation, expected):
    cleaned = png.strip(metadata_png(orientation))
    s = png.walk_chunks(cleaned)
    exif_chunks = s.all("eXIf")
    if not expected:
        assert exif_chunks == []
        return
    [c] = exif_chunks
    assert c.data == png.exif.minimal_orientation_tiff(orientation)
    assert len(c.data) == 26 and c.crc_ok
    assert png.walk_chunks(cleaned).chunks[1].type == "eXIf"  # right after IHDR


def test_no_orientation():
    cleaned = png.strip(metadata_png(6), keep_orientation=False)
    assert "eXIf" not in types(cleaned)


def test_iccp_is_renamed_with_same_compressed_profile():
    profile = icc_profile()
    compressed = zlib.compress(profile, 6)
    original = make_png(before_idat=(iccp(profile, "Jane's display", compressed=compressed),))
    cleaned = png.strip(original)
    [c] = png.walk_chunks(cleaned).all("iCCP")
    assert c.data == b"ICC profile\x00\x00" + compressed
    assert c.crc_ok


def test_iccp_with_extra_bytes_is_recompressed():
    profile = icc_profile()
    bad = zlib.compress(profile) + b"hidden"
    cleaned = png.strip(make_png(before_idat=(iccp(profile, compressed=bad),)))
    [c] = png.walk_chunks(cleaned).all("iCCP")
    assert b"hidden" not in c.data
    assert zlib.decompress(c.data[len(b"ICC profile\x00\x00"):]) == profile


@pytest.mark.parametrize("bad", [iccp(b"not a profile"), chunk("iCCP", b"x\x00\x00garbage"),
                                 chunk("iCCP", b"no separator")])
def test_broken_iccp_is_dropped(bad):
    assert "iCCP" not in types(png.strip(make_png(before_idat=(bad,))))


def test_wrong_size_color_chunks_are_dropped():
    original = make_png(before_idat=(chunk("gAMA", bytes(40)), chunk("sRGB", b"\x00hidden")))
    assert types(png.strip(original)) == ["IHDR", "IDAT", "IEND"]


def test_only_first_of_each_color_chunk_is_kept():
    original = make_png(before_idat=(GAMA, chunk("gAMA", struct.pack(">I", 100000))))
    assert types(png.strip(original)).count("gAMA") == 1


def test_palette_and_transparency_are_kept():
    plte = chunk("PLTE", bytes(range(12)))
    trns = chunk("tRNS", b"\x00\xff")
    original = make_png(text("Author", "x"), before_idat=(plte, trns), color=PALETTE)
    assert types(png.strip(original)) == ["IHDR", "PLTE", "tRNS", "IDAT", "IEND"]


def test_apng_chunks_are_kept():
    raw = bytes(4 * 13)
    original = make_png(fctl(1), fdat(2, raw), text("Software", "x"),
                        before_idat=(actl(2), fctl(0)))
    assert types(png.strip(original)) == ["IHDR", "acTL", "fcTL", "IDAT", "fcTL", "fdAT",
                                          "IEND"]


def test_strip_is_idempotent():
    once = png.strip(metadata_png())
    assert png.strip(once) == once


def test_output_has_no_metadata():
    parsed = png.parse(png.strip(metadata_png()))
    keys = {s.key for s in parsed["sections"]}
    assert keys == {"exif", "icc", "color"}
    exif_fields = {f.key for s in parsed["sections"] if s.key == "exif" for f in s.fields}
    assert exif_fields == {"byte_order", "Orientation"}
    assert not any(w.startswith("CRITICAL") for w in parsed["warnings"])


def test_icc_device_warning_applies_to_png():
    from metaex.commands.strip import kept_warnings

    warnings = kept_warnings(png.parse(png.strip(metadata_png())))
    assert warnings == ["ICC profile names a device (ACME X100); use --paranoid to remove "
                        "it (colors may shift)"]
    assert kept_warnings(png.parse(png.strip(metadata_png(), paranoid=True))) == []


@pytest.mark.parametrize(
    "data,message",
    [
        (make_png(chunk("tEXt", b"a\x00b", crc=1)), "bad CRC"),
        (make_png(iend=False), "missing IEND"),
        (make_png()[:-20], "truncated"),
        (SIGNATURE + chunk("IEND"), "IHDR"),
        (SIGNATURE + ihdr() + chunk("IEND"), "no image data"),
        (make_png(chunk("ABCD", b"x")), "unsupported critical chunk ABCD"),
        (make_png(before_idat=(ihdr(),)), "second IHDR"),
    ],
)
def test_malformed_input_is_refused(data, message):
    with pytest.raises(MalformedFileError, match=message):
        png.strip(data)


def test_not_a_png_is_refused():
    with pytest.raises(MalformedFileError):
        png.strip(b"\xff\xd8\xff")


def test_png_is_not_offered_for_stripping_yet():
    from metaex.core.registry import can_strip

    assert not can_strip(png)


# -- verification ---------------------------------------------------------------------


def test_verify_details_clean_output():
    original = metadata_png(idat_parts=2)
    cleaned = png.strip(original)
    d = png.verify_details(cleaned, original)
    assert d["leftovers"] == [] and d["problems"] == []
    assert d["image_data_match"] is True
    assert d["image_sha256"] == d["original_image_sha256"]
    assert len(d["image_sha256"]) == 64


def test_verify_details_paranoid_output():
    original = metadata_png()
    d = png.verify_details(png.strip(original, paranoid=True), original)
    assert d["leftovers"] == [] and d["image_data_match"] is True


def test_image_hash_ignores_how_idat_is_split():
    assert png.image_data(make_png(idat_parts=1)) == png.image_data(make_png(idat_parts=4))


def test_image_hash_changes_with_pixels_palette_or_frames():
    base = png.image_data(make_png())
    assert png.image_data(make_png(seed=2)) != base
    assert png.image_data(make_png(width=5)) != base
    plte = chunk("PLTE", bytes(range(12)))
    assert png.image_data(make_png(before_idat=(plte,), color=PALETTE)) != png.image_data(
        make_png(before_idat=(chunk("PLTE", bytes(12)),), color=PALETTE))
    raw = bytes(4 * 13)
    a = make_png(fctl(1), fdat(2, raw), before_idat=(actl(2), fctl(0)))
    b = make_png(fctl(1), fdat(2, b"\x01" * 52), before_idat=(actl(2), fctl(0)))
    assert png.image_data(a) != png.image_data(b)


def test_different_image_is_reported():
    d = png.verify_details(png.strip(make_png()), make_png(seed=9))
    assert d["image_data_match"] is False
    assert "image data differs from the original" in d["problems"]


@pytest.mark.parametrize(
    "extra,problem",
    [
        (text("Author", "Jane"), "tEXt chunk is not allowed"),
        (time_chunk(), "tIME chunk is not allowed"),
        (chunk("caBX", bytes(10)), "caBX chunk is not allowed"),
        (chunk("prVt", b"x"), "prVt chunk is not allowed"),
        (chunk("gAMA", bytes(8)), "gAMA should have 4 bytes"),
        (exif_chunk(exif_tiff(6)), "eXIf contains more than the Orientation tag"),
        (iccp(icc_profile(), "Jane's display"), "non-generic name"),
        (iccp(icc_profile(), "ICC profile", compressed=zlib.compress(icc_profile()) + b"xx"),
         "2 extra bytes"),
        (iccp(b"short", "ICC profile"), "not a valid ICC profile"),
    ],
)
def test_leftovers_are_found(extra, problem):
    d = png.verify_details(make_png(extra))
    [(section, label, value)] = d["leftovers"]
    assert section == "PNG structure"
    assert problem in value


def test_duplicate_kept_chunks_are_leftovers():
    d = png.verify_details(make_png(before_idat=(GAMA, GAMA)))
    assert len(d["leftovers"]) == 1


def test_bad_crc_or_truncation_is_a_problem():
    for data in (make_png(chunk("gAMA", bytes(4), crc=7)), make_png(iend=False)):
        d = png.verify_details(data)
        assert d["problems"] and d["problems"][0].startswith("not a complete, undamaged PNG")
        assert d["image_sha256"] is None


def test_damaged_original_is_reported():
    d = png.verify_details(png.strip(make_png()), make_png(iend=False))
    assert d["image_data_match"] is None
    assert "original is not a complete, undamaged PNG" in d["problems"][0]
