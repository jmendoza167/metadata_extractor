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
    assert set(section_keys(cleaned)) == {"jfif", "adobe", "icc"}  # kept by design
    assert set(section_keys(jpeg.strip(kitchen_sink_jpeg(), paranoid=True))) == {"jfif", "adobe"}


def photo_with_orientation(value, order="<"):
    from helpers.tiff import ASCII, SHORT, build_tiff, exif_payload

    entries = [(0x010F, ASCII, "Cam")]
    if value is not None:
        entries.append((0x0112, SHORT, value))
    return make_jpeg(app(1, exif_payload(build_tiff(entries, exif=[(0x9003, ASCII, "2024:01:01 00:00:00")],
                                                    order=order))))


@pytest.mark.parametrize("value", [2, 3, 4, 5, 6, 7, 8])
@pytest.mark.parametrize("order", ["<", ">"])
def test_orientation_2_to_8_is_kept_alone(value, order):
    cleaned = jpeg.strip(photo_with_orientation(value, order))
    (section,) = jpeg.parse(cleaned)["sections"]
    assert section.key == "exif"
    assert [f.label for f in section.fields] == ["Byte order", "Orientation"]
    exif_seg = [s for s in jpeg.walk_segments(cleaned).segments if s.marker == 0xE1]
    assert len(exif_seg) == 1
    assert exif_seg[0].payload == jpeg.minimal_orientation_exif(value)[4:]
    assert len(exif_seg[0].payload) == 6 + 26


@pytest.mark.parametrize("value", [1, None, 0, 9, 300])
def test_no_exif_when_orientation_is_normal_missing_or_invalid(value):
    assert section_keys(jpeg.strip(photo_with_orientation(value))) == []


def test_keep_orientation_false_drops_it():
    assert section_keys(jpeg.strip(photo_with_orientation(6), keep_orientation=False)) == []


def test_orientation_follows_jfif():
    original = photo_with_orientation(6)
    with_jfif = original[:2] + app(0, JFIF_WITH_THUMB) + original[2:]
    names = [s.name for s in jpeg.walk_segments(jpeg.strip(with_jfif)).segments]
    assert names[:3] == ["SOI", "APP0", "APP1"]


def icc_bytes(data):
    return jpeg.assemble_icc_profile(jpeg.walk_segments(data), [])


@pytest.mark.parametrize("chunk_size", [65519, 100])
def test_icc_profile_is_kept_by_default(chunk_size):
    from helpers.jpeg import icc_profile, icc_segments

    profile = icc_profile()
    cleaned = jpeg.strip(make_jpeg(*icc_segments(profile, chunk_size)))
    assert icc_bytes(cleaned) == profile
    assert section_keys(cleaned) == ["icc"]


def test_paranoid_removes_icc_and_jfif_density():
    from helpers.jpeg import icc_profile, icc_segments

    data = make_jpeg(app(0, JFIF_WITH_THUMB), *icc_segments(icc_profile(), 100))
    cleaned = jpeg.strip(data, paranoid=True)
    assert icc_bytes(cleaned) is None
    (section,) = jpeg.parse(cleaned)["sections"]
    assert {f.label: f.value for f in section.fields}["Density"] == "1 x 1 (aspect ratio only)"


def test_broken_icc_profile_is_dropped():
    from helpers.jpeg import icc_profile, icc_segments

    segs = icc_segments(icc_profile(), 100)
    cleaned = jpeg.strip(make_jpeg(*segs[:-1]))
    assert icc_bytes(cleaned) is None
    not_icc = make_jpeg(*icc_segments(b"x" * 200))
    assert icc_bytes(jpeg.strip(not_icc)) is None


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
