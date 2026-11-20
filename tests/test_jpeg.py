import pytest

from helpers.jpeg import EOI, SOI, app, dht, make_jpeg, sos, segment
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


def test_parse_returns_structure():
    result = jpeg.parse(make_jpeg())
    assert result["structure"].title == "JPEG structure"
    assert [f.label for f in result["structure"].fields][0] == "SOI"
