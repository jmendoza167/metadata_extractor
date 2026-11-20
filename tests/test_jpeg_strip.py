"""Basic tests for JPEG stripping (whitelist)."""

import pytest

from helpers.jpeg import EOI, SOI, app, kitchen_sink_jpeg, make_jpeg, sample_photo, segment
from metaex.core.errors import MalformedFileError
from metaex.formats import jpeg

JFIF_WITH_THUMB = b"JFIF\x00\x01\x02\x01\x00\x48\x00\x48\x02\x02" + bytes(12)
ADOBE = b"Adobe\x00\x64\x00\x00\x00\x00\x02"


def section_keys(data):
    return [s.key for s in jpeg.parse(data)["sections"]]


def test_strip_removes_exif_and_xmp_and_keeps_image_data():
    original = sample_photo()
    cleaned = jpeg.strip(original)
    assert section_keys(cleaned) == []
    assert jpeg.image_data(cleaned) == jpeg.image_data(original)
    assert cleaned.startswith(SOI) and cleaned.endswith(EOI)
    assert jpeg.parse(cleaned)["complete"]


def test_new_minimal_jfif_keeps_version_and_density_without_thumbnail():
    cleaned = jpeg.strip(make_jpeg(app(0, JFIF_WITH_THUMB)))
    (section,) = jpeg.parse(cleaned)["sections"]
    fields = {f.label: f.value for f in section.fields}
    assert fields == {"Version": "1.02", "Density": "72 x 72 dpi"}
    seg = jpeg.walk_segments(cleaned).segments[1]
    assert seg.payload == b"JFIF\x00\x01\x02\x01\x00\x48\x00\x48\x00\x00"


def test_adobe_app14_is_kept_minimal():
    cleaned = jpeg.strip(make_jpeg(app(14, ADOBE + b"hidden extra bytes")))
    segs = [s for s in jpeg.walk_segments(cleaned).segments if s.marker == 0xEE]
    assert [s.payload for s in segs] == [ADOBE]


def test_everything_else_is_dropped():
    data = make_jpeg(app(1, b"Exif\x00\x00garbage"), app(12, b"Ducky\x00"),
                     segment(0xFE, b"comment"), trailing=b"after the end")
    cleaned = jpeg.strip(data)
    markers = [s.name for s in jpeg.walk_segments(cleaned).segments]
    assert markers == ["SOI", "DQT", "SOF0", "DHT", "SOS", "EOI"]


def test_kitchen_sink_loses_every_metadata_section():
    cleaned = jpeg.strip(kitchen_sink_jpeg())
    assert set(section_keys(cleaned)) <= {"jfif", "adobe"}


@pytest.mark.parametrize(
    "data",
    [
        make_jpeg(eoi=False),
        make_jpeg()[:40],
        SOI + EOI,
        SOI + segment(0xDB, bytes(65)) + EOI,  # tables but no frame or scan
        SOI + b"\xff\xde\x00\x04\x00\x00" + make_jpeg()[2:],  # hierarchical DHP
        b"\x89PNG\r\n\x1a\n",
    ],
)
def test_malformed_input_raises(data):
    with pytest.raises(MalformedFileError):
        jpeg.strip(data)


def test_short_jfif_or_adobe_is_refused():
    with pytest.raises(MalformedFileError):
        jpeg.strip(make_jpeg(app(0, b"JFIF\x00\x01")))
    with pytest.raises(MalformedFileError):
        jpeg.strip(make_jpeg(app(14, b"Adobe\x00")))
