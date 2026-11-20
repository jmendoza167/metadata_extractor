"""Edge cases and malformed input for JPEG and EXIF parsing."""

import random
import struct

import pytest

from helpers.jpeg import (
    EOI, SOI, app, extended_xmp_segments, icc_segments, icc_profile, iim, kitchen_sink_jpeg,
    make_jpeg, make_mpf_jpeg, photoshop_resource, photoshop_segment, sample_photo, segment,
    xmp_segment,
)
from helpers.tiff import (
    ASCII, LONG, RATIONAL, SHORT, build_tiff, exif_payload, ifd_bytes, tiff_header,
)
from metaex.core.errors import MalformedFileError
from metaex.core.report import Report, render_json, render_text
from metaex.formats import jpeg


def parse(data):
    return jpeg.parse(data)


def keys(result):
    return [s.key for s in result["sections"]]


def fields(result, key):
    (section,) = [s for s in result["sections"] if s.key == key]
    return {f.label: f for f in section.fields}


def exif_jpeg(tiff):
    return make_jpeg(app(1, exif_payload(tiff)))


# --- builders produce every section, in both byte orders ---------------------------

@pytest.mark.parametrize("order", ["<", ">"])
def test_kitchen_sink_has_every_section(order):
    result = parse(kitchen_sink_jpeg(order))
    assert keys(result) == ["exif", "gps", "thumbnail", "xmp", "iptc", "photoshop", "icc",
                            "mpf", "comments", "jfif", "adobe", "other_app", "trailing"]
    assert result["complete"]
    assert fields(result, "gps")["Coordinates"].value == "48.858367, 2.294481"
    assert fields(result, "iptc")["City"].value == "Paris"
    assert "(extended) xmpMM:DocumentID" in fields(result, "xmp")


# --- EXIF structure ------------------------------------------------------------------

def test_ifd_loop_in_jpeg_exif():
    tiff = tiff_header("<") + ifd_bytes([(0x010F, ASCII, "Cam")], 8, "<", next_offset=8)
    result = parse(exif_jpeg(tiff))
    assert fields(result, "exif")["Make"].value == "Cam"
    assert any("IFD loop detected" in w for w in result["warnings"])


def test_exif_sub_ifd_loop_between_exif_and_gps():
    # IFD0 -> Exif at 26; Exif's GPS pointer leads back to IFD0's offset.
    ifd0 = ifd_bytes([(0x8769, LONG, 26)], 8, "<")
    exif_ifd = ifd_bytes([(0x8825, LONG, 8)], 26, "<")
    result = parse(exif_jpeg(tiff_header("<") + ifd0 + exif_ifd))
    assert any("IFD loop detected" in w for w in result["warnings"])


def test_ifd0_offset_past_end():
    result = parse(exif_jpeg(tiff_header("<", ifd0_offset=5000) + bytes(10)))
    assert fields(result, "exif")["EXIF"].value == "present but not parsed"
    assert any("EXIF not parsed" in w for w in result["warnings"])


def test_sub_ifd_offset_past_end():
    tiff = build_tiff([(0x010F, ASCII, "Cam"), (0x8825, LONG, 99999)])
    result = parse(exif_jpeg(tiff))
    assert fields(result, "exif")["Make"].value == "Cam"
    assert any("GPS at offset 99999 could not be read" in w for w in result["warnings"])


def test_value_offset_past_end():
    tiff = build_tiff([(0x010F, ASCII, 50, struct.pack("<I", 4000))])
    result = parse(exif_jpeg(tiff))
    assert fields(result, "exif")["Make"].value == "(not readable)"


@pytest.mark.parametrize("count", [0xFFFFFFFF, 0x20000000, 70000])
def test_huge_counts(count):
    tiff = build_tiff([(0x0111, LONG, count, struct.pack("<I", 8)), (0x010F, ASCII, "Cam")])
    result = parse(exif_jpeg(tiff))
    assert fields(result, "exif")["Make"].value == "Cam"
    assert fields(result, "exif")["StripOffsets"].value == "(not readable)"


def test_huge_entry_count_in_ifd():
    tiff = tiff_header("<") + struct.pack("<H", 0xFFFF) + bytes(20)
    result = parse(exif_jpeg(tiff))
    assert any("claims 65535 entries" in w for w in result["warnings"])
    assert any("truncated" in w for w in result["warnings"])


def test_zero_denominator_rationals():
    tiff = build_tiff([], exif=[(0x829A, RATIONAL, (1, 0)), (0x829D, RATIONAL, (0, 0))],
                      gps=[(0x0001, ASCII, "N"), (0x0002, RATIONAL, [(48, 0), (0, 0), (0, 0)]),
                           (0x0003, ASCII, "E"), (0x0004, RATIONAL, [(2, 1), (0, 1), (0, 1)]),
                           (0x0006, RATIONAL, (5, 0)), (0x0007, RATIONAL, [(1, 0), (0, 1), (0, 1)])])
    result = parse(exif_jpeg(tiff))
    exif = fields(result, "exif")
    assert exif["ExposureTime"].value == "1/0 (invalid: zero denominator)"
    assert exif["FNumber"].value == "0/0 (invalid: zero denominator)"
    gps = fields(result, "gps")
    assert "Coordinates" not in gps
    assert "zero denominator" in gps["GPSLatitude"].value
    assert any("GPS latitude is invalid" in w for w in result["warnings"])
    assert any("GPS altitude is invalid" in w for w in result["warnings"])


def test_thumbnail_pointer_past_end():
    tiff = build_tiff([], ifd1=[(0x0201, LONG, 40), (0x0202, LONG, 1_000_000)])
    result = parse(exif_jpeg(tiff))
    assert "Embedded thumbnail" not in fields(result, "thumbnail")
    assert any("past the end of the EXIF data" in w for w in result["warnings"])


# --- segment structure ----------------------------------------------------------------

def test_truncated_app1_segment():
    data = sample_photo()
    result = parse(data[:200])
    assert not result["complete"]
    assert any("truncated" in w for w in result["warnings"])


def test_segment_length_below_two():
    result = parse(SOI + b"\xff\xe1\x00\x01" + EOI)
    assert any("invalid length 1" in w for w in result["warnings"])


def test_garbage_between_segments():
    result = parse(SOI + app(0, b"JFIF\x00") + b"junk" + EOI)
    assert any("expected a marker" in w for w in result["warnings"])


def test_missing_eoi_has_no_trailing_section():
    result = parse(make_jpeg(app(1, exif_payload(build_tiff([(0x010F, ASCII, "C")]))), eoi=False))
    assert "trailing" not in keys(result)
    assert any("missing end-of-image" in w for w in result["warnings"])
    assert result["complete"]


def test_scan_data_ending_in_ff():
    result = parse(make_jpeg(scan=b"\x01\x02\xff", eoi=False))
    assert any("missing end-of-image" in w for w in result["warnings"])


def test_only_soi():
    result = parse(SOI)
    assert keys(result) == []


def test_not_a_jpeg_raises_malformed():
    with pytest.raises(MalformedFileError):
        parse(b"")
    with pytest.raises(MalformedFileError):
        parse(b"\x89PNG\r\n\x1a\n")


# --- XMP, IPTC, ICC, MPF ------------------------------------------------------------

def test_xml_with_doctype_in_extended_xmp_is_refused():
    bomb = '<!DOCTYPE x [<!ENTITY a "' + "a" * 50 + '">]><x>&a;&a;</x>'
    result = parse(make_jpeg(*extended_xmp_segments(bomb, chunk_size=30)))
    assert any("DOCTYPE or ENTITY" in w for w in result["warnings"])


def test_extended_xmp_claiming_huge_length():
    segs = extended_xmp_segments("<x/>", total=2**31)
    result = parse(make_jpeg(*segs))
    assert any("too large" in w for w in result["warnings"])


def test_malformed_xmp_xml():
    result = parse(make_jpeg(xmp_segment(b"<x:xmpmeta><unclosed>")))
    assert any("not well-formed" in w for w in result["warnings"])


def test_photoshop_resource_with_huge_size():
    bad = b"8BIM" + struct.pack(">H", 0x0404) + b"\x00\x00" + struct.pack(">I", 0x7FFFFFFF)
    result = parse(make_jpeg(photoshop_segment(photoshop_resource(0x03ED, bytes(4)) + bad)))
    assert "0x03ED Resolution info" in fields(result, "photoshop")
    assert any("Photoshop resources are truncated" in w for w in result["warnings"])


def test_iptc_with_bad_extended_size():
    data = iim((2, 90, "Paris")) + b"\x1c\x02\x50\x80\x09" + bytes(9)
    result = parse(make_jpeg(photoshop_segment(photoshop_resource(0x0404, data))))
    assert fields(result, "iptc")["City"].value == "Paris"
    assert any("invalid extended IPTC size length 9" in w for w in result["warnings"])


def test_icc_with_zero_total_and_duplicates():
    profile = icc_profile()
    dup = icc_segments(profile, 100)
    result = parse(make_jpeg(dup[0], dup[0], *dup[1:]))
    assert any("duplicate sequence number" in w for w in result["warnings"])
    zero = app(2, b"ICC_PROFILE\x00\x00\x00" + profile)
    result = parse(make_jpeg(zero))
    assert "icc" not in keys(result)


def test_icc_profile_too_short():
    result = parse(make_jpeg(*icc_segments(b"tiny")))
    assert any("ICC profile not parsed" in w for w in result["warnings"])


def test_mpf_claiming_more_images_than_listed():
    data = bytearray(make_mpf_jpeg([(0x010001, make_jpeg())]))
    pos = data.find(b"\x01\xb0\x04\x00\x01\x00\x00\x00")  # NumberOfImages, LONG, count 1
    assert pos > 0
    data[pos + 8 : pos + 12] = struct.pack("<I", 1000)
    result = parse(bytes(data))
    assert any("MPF index claims 1000 images" in w for w in result["warnings"])


def test_mpf_image_offset_outside_file():
    result = parse(make_mpf_jpeg([(0x010001, make_jpeg())])[:-10])
    assert fields(result, "mpf")["Image 2"].note == "outside the file"


# --- mutation test ------------------------------------------------------------------

def mutate(data: bytes, rng: random.Random) -> bytes:
    d = bytearray(data)
    op = rng.random()
    if op < 0.6:
        for _ in range(rng.randint(1, 8)):
            d[rng.randrange(len(d))] = rng.randrange(256)
    elif op < 0.8:
        del d[rng.randrange(len(d)):]
    else:
        pos = rng.randrange(len(d))
        d[pos:pos] = rng.randbytes(rng.randint(1, 16))
    return bytes(d)


def test_random_mutations_only_raise_malformed_file_error():
    """~2,000 random mutations of a JPEG with every kind of metadata.

    Parsing (and rendering the result) must either succeed or raise
    MalformedFileError; any other exception is a bug.
    """
    rng = random.Random(20261120)
    originals = [kitchen_sink_jpeg("<"), kitchen_sink_jpeg(">")]
    outcomes = {"ok": 0, "malformed": 0}
    for _ in range(2000):
        data = mutate(rng.choice(originals), rng)
        try:
            result = jpeg.parse(data)
        except MalformedFileError:
            outcomes["malformed"] += 1
            continue
        report = Report("mutated.jpg", result["sections"] + [result["structure"]],
                        result["warnings"])
        render_text(report, width=100)
        render_json(report)
        outcomes["ok"] += 1
    assert outcomes["ok"] > 1000  # most mutations still leave a parseable JPEG
