import pytest

from helpers.jpeg import (
    EOI, PHOTOSHOP_ID, SAMPLE_XMP, SOI, app, dht, extended_xmp_segments, icc_profile,
    icc_segments, iim, make_jpeg, make_mpf_jpeg, photoshop_resource, segment, sos, xmp_segment,
)
from metaex.core.errors import MalformedFileError
from metaex.formats import jpeg
from metaex.formats.jpeg import walk_segments


def names(structure):
    return [s.name for s in structure.segments]


def test_walks_minimal_jpeg():
    data = make_jpeg()
    s = walk_segments(data)
    assert names(s) == ["SOI", "DQT", "SOF0", "DHT", "SOS", "EOI"]
    assert s.end_of_image == len(data)
    assert s.warnings == []
    assert s.error is None


def test_segment_offsets_and_lengths():
    data = make_jpeg(app(0, b"JFIF\x00\x01\x02"))
    seg = walk_segments(data).segments[1]
    assert seg.name == "APP0"
    assert seg.offset == 2
    assert seg.length == 2 + 7
    assert seg.payload == b"JFIF\x00\x01\x02"
    assert seg.identifier == "JFIF"


@pytest.mark.parametrize(
    "payload,identifier",
    [
        (b"Exif\x00\x00II*\x00", "Exif"),
        (b"http://ns.adobe.com/xap/1.0/\x00<x/>", "http://ns.adobe.com/xap/1.0/"),
        (b"Adobe\x00\x64\x00\x00", "Adobe"),
        (b"\x01\x02", ""),
    ],
)
def test_app_identifiers(payload, identifier):
    assert walk_segments(make_jpeg(app(1, payload))).segments[1].identifier == identifier


def test_fill_bytes_before_markers():
    data = SOI + b"\xff\xff\xff" + app(0, b"JFIF\x00") + b"\xff" + EOI
    assert names(walk_segments(data)) == ["SOI", "APP0", "EOI"]


def test_entropy_data_with_stuffing_and_restart_markers():
    scan = b"\x01\xff\x00\x02\xff\xd0\x03\xff\xd7\x04\xff\xff\xff"
    s = walk_segments(make_jpeg(scan=scan[:-2]))
    assert names(s)[-2:] == ["SOS", "EOI"]
    assert s.segments[-2].scan_length == len(scan) - 2


def test_progressive_jpeg_with_multiple_scans():
    data = SOI + dht() + sos() + b"\x11\xff\x00" + dht() + sos() + b"\x22" + sos() + b"\x33" + EOI
    s = walk_segments(data)
    assert names(s).count("SOS") == 3
    assert names(s)[-1] == "EOI"


def test_standalone_tem_marker():
    data = SOI + b"\xff\x01" + EOI
    assert names(walk_segments(data)) == ["SOI", "TEM", "EOI"]


def test_missing_eoi_is_a_warning():
    s = walk_segments(make_jpeg(eoi=False))
    assert s.end_of_image is None
    assert s.error is None
    assert any("missing end-of-image" in w for w in s.warnings)


def test_truncated_segment_keeps_earlier_segments():
    data = make_jpeg(app(1, b"Exif\x00\x00" + bytes(100)))[:60]
    s = walk_segments(data)
    assert names(s) == ["SOI"]
    assert "truncated" in s.error


def test_too_many_segments():
    data = SOI + segment(0xFE, b"x") * (jpeg.MAX_SEGMENTS + 5) + EOI
    s = walk_segments(data)
    assert len(s.segments) == jpeg.MAX_SEGMENTS
    assert "more than" in s.error


def test_not_a_jpeg_raises():
    with pytest.raises(MalformedFileError):
        walk_segments(b"\x89PNG")


def test_image_size():
    assert jpeg.image_size(make_jpeg(width=640, height=480)) == (640, 480)
    assert jpeg.image_size(SOI + EOI) is None
    assert jpeg.image_size(b"not a jpeg") is None


def section_fields(result, key):
    (section,) = [s for s in result["sections"] if s.key == key]
    return {f.label: f for f in section.fields}


def test_parse_reads_xmp():
    result = jpeg.parse(make_jpeg(xmp_segment(SAMPLE_XMP)))
    f = section_fields(result, "xmp")
    assert f["xmpMM:DocumentID"].value == "xmp.did:1234"
    assert any("XMP contains GPS" in w for w in result["warnings"])


def test_parse_reassembles_extended_xmp():
    ext = extended_xmp_segments(SAMPLE_XMP, chunk_size=200)
    assert len(ext) > 3
    result = jpeg.parse(make_jpeg(xmp_segment(SAMPLE_XMP), *reversed(ext)))
    f = section_fields(result, "xmp")
    assert f["(extended) xmpMM:OriginalDocumentID"].value == "xmp.did:abcd"


def test_incomplete_extended_xmp_is_a_warning():
    ext = extended_xmp_segments(SAMPLE_XMP, chunk_size=200)
    result = jpeg.parse(make_jpeg(*ext[:-1]))
    assert any("incomplete" in w for w in result["warnings"])


def test_unsafe_xmp_is_refused_in_jpeg():
    bomb = b'<!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
    result = jpeg.parse(make_jpeg(xmp_segment(bomb)))
    f = section_fields(result, "xmp")
    assert f["XMP packet status"].value == "present but not parsed"
    assert any("DOCTYPE or ENTITY" in w for w in result["warnings"])


def test_parse_reads_iptc_across_two_app13_segments():
    resource = photoshop_resource(0x0404, iim((2, 90, "Paris"), (2, 80, "Jane")))
    first, second = resource[:15], resource[15:]
    data = make_jpeg(app(13, PHOTOSHOP_ID + first), app(13, PHOTOSHOP_ID + second))
    result = jpeg.parse(data)
    f = section_fields(result, "iptc")
    assert f["City"].value == "Paris"
    assert f["By-line"].value == "Jane"
    assert "0x0404 IPTC-NAA record" in section_fields(result, "photoshop")


def test_comments_are_sensitive_text():
    result = jpeg.parse(make_jpeg(segment(0xFE, b"Shot by Jane at home"), segment(0xFE, b"\xe9t\xe9")))
    f = section_fields(result, "comments")
    assert f["Comment 1"].value == "Shot by Jane at home"
    assert f["Comment 2"].value == chr(0xE9) + "t" + chr(0xE9)
    assert f["Comment 1"].sensitive


@pytest.mark.parametrize("chunk_size", [65519, 100])
def test_icc_profile_single_and_multi_chunk(chunk_size):
    segments = icc_segments(icc_profile(), chunk_size)
    result = jpeg.parse(make_jpeg(*segments))
    f = section_fields(result, "icc")
    assert f["Description"].value == "Test RGB profile"
    assert f["Copyright"].value == "Copyright Test"
    assert f["Device manufacturer"].value == "ACME"
    assert f["Device model"].value == "X100"
    assert f["Profile creator"].value == "meta"
    assert f["Device class"].value == "Display device"
    assert f["Profile created"].value == "2024-05-01T12:30:00"
    assert f["Version"].value == "4.3.0"


def test_icc_missing_chunk_is_a_warning():
    segments = icc_segments(icc_profile(), 100)
    result = jpeg.parse(make_jpeg(*segments[:1], *segments[2:]))
    assert not [s for s in result["sections"] if s.key == "icc"]
    assert any("ICC profile chunks are missing" in w for w in result["warnings"])


def test_mpf_extra_images_are_critical():
    extras = [(0x010001, make_jpeg(width=640, height=480)), (0x020002, make_jpeg())]
    data = make_mpf_jpeg(extras)
    result = jpeg.parse(data)
    f = section_fields(result, "mpf")
    assert f["Number of images"].value == 3
    assert f["Image 2"].value.startswith("Large thumbnail (VGA equivalent), ")
    assert f["Image 2"].note == "JPEG"
    assert f["Image 3"].value.startswith("Multi-frame image (disparity), ")
    assert f["Image 2"].sensitive
    assert any(w.startswith("CRITICAL: 2 extra image(s) hidden in the file (MPF)")
               for w in result["warnings"])


def test_jfif_density_and_thumbnail():
    jfif = b"JFIF\x00\x01\x02\x01\x00\x48\x00\x48\x02\x02" + bytes(12)
    result = jpeg.parse(make_jpeg(app(0, jfif), app(0, b"JFXX\x00\x10" + make_jpeg())))
    f = section_fields(result, "jfif")
    assert f["Version"].value == "1.02"
    assert f["Density"].value == "72 x 72 dpi"
    assert f["JFIF thumbnail"].value == "2 x 2 pixels, uncompressed RGB"
    assert f["JFXX thumbnail"].value.startswith("JPEG, ")
    assert f["JFXX thumbnail"].sensitive
    assert sum("thumbnail" in w for w in result["warnings"]) == 2


def test_adobe_segment_is_not_sensitive():
    result = jpeg.parse(make_jpeg(app(14, b"Adobe\x00\x64\x00\x00\x00\x00\x01")))
    f = section_fields(result, "adobe")
    assert f["Color transform"].value == "YCbCr"
    assert not any(x.sensitive for x in f.values())


def test_other_app_segments_are_listed():
    result = jpeg.parse(make_jpeg(app(12, b"Ducky\x00\x01\x02"), app(9, b"\x00\x01")))
    f = section_fields(result, "other_app")
    assert f['APP12 "Ducky"'].value.startswith("8 bytes at offset")
    assert "APP9 (no identifier)" in f


def test_parse_returns_structure():
    result = jpeg.parse(make_jpeg())
    assert result["structure"].title == "JPEG structure"
    assert [f.label for f in result["structure"].fields][0] == "SOI"
