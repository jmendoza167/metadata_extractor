"""Basic tests for JPEG stripping (whitelist)."""

import random
import struct

import pytest

from helpers.jpeg import EOI, SOI, app, kitchen_sink_jpeg, make_jpeg, sample_photo, segment, sof0
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


# --- coverage: kitchen sink, idempotence, modes, CMYK, mutations ---------------------

# Sections (and fields) that may remain after stripping, by design.
KEPT_BY_DESIGN = {
    "jfif": {"version", "density"},
    "adobe": None,  # all fields
    "icc": None,
    "exif": {"byte_order", "Orientation"},
}


def leftovers(data):
    found = []
    for s in jpeg.parse(data)["sections"]:
        allowed = KEPT_BY_DESIGN.get(s.key, set())
        if allowed is not None:
            found += [(s.key, f.key) for f in s.fields if f.key not in allowed]
    return found


def image_segments(data):
    """(marker name, exact bytes) of every image-defining segment, scan data included."""
    structure = jpeg.walk_segments(data)
    out = []
    for seg in structure.segments:
        if seg.marker in jpeg.IMAGE_MARKERS:
            chunk = data[seg.offset : seg.offset + 2 + seg.length]
            if seg.marker == 0xDA:
                start = seg.offset + 2 + seg.length
                chunk += data[start : start + seg.scan_length]
            out.append((seg.name, chunk))
    return out


@pytest.mark.parametrize("order", ["<", ">"])
@pytest.mark.parametrize("paranoid", [False, True])
def test_kitchen_sink_is_fully_cleaned(order, paranoid):
    original = kitchen_sink_jpeg(order)
    cleaned = jpeg.strip(original, paranoid=paranoid)
    assert leftovers(cleaned) == []
    result = jpeg.parse(cleaned)
    assert result["complete"]
    assert not [w for w in result["warnings"] if w.startswith("CRITICAL")]
    keys = {s.key for s in result["sections"]}
    assert keys == ({"jfif", "adobe"} if paranoid else {"jfif", "adobe", "icc"})
    # Tables, frame header, scan headers and scan data are byte-identical.
    assert image_segments(cleaned) == image_segments(original)
    assert [n for n, _ in image_segments(cleaned)] == ["DQT", "SOF0", "DHT", "SOS"]
    assert cleaned.endswith(b"\xff\xd9")
    assert b"trailing secret" not in cleaned


@pytest.mark.parametrize("make", [
    lambda: kitchen_sink_jpeg("<"),
    lambda: kitchen_sink_jpeg(">"),
    lambda: photo_with_orientation(6),
    lambda: make_jpeg(app(0, JFIF_WITH_THUMB), app(14, ADOBE)),
])
@pytest.mark.parametrize("options", [{}, {"paranoid": True}, {"keep_orientation": False}])
def test_strip_is_idempotent(make, options):
    once = jpeg.strip(make(), **options)
    assert jpeg.strip(once, **options) == once


def test_orientation_1_vs_6_on_a_full_photo():
    from helpers.tiff import SHORT

    def photo(orientation):
        data = bytearray(sample_photo())
        # sample_photo has Orientation=1 (SHORT, little-endian) in IFD0: patch it.
        pos = data.find(struct.pack("<HHIH", 0x0112, SHORT, 1, 1))
        assert pos > 0
        data[pos + 8 : pos + 10] = struct.pack("<H", orientation)
        return bytes(data)

    assert section_keys(jpeg.strip(photo(1))) == []
    (section,) = jpeg.parse(jpeg.strip(photo(6)))["sections"]
    assert {f.key: f.value for f in section.fields}["Orientation"] == "Rotated 90 degrees clockwise"


def cmyk_jpeg(transform):
    sof = struct.pack(">BHHB", 8, 8, 8, 4) + b"".join(bytes([i, 0x11, 0]) for i in range(1, 5))
    data = make_jpeg(app(14, b"Adobe\x00\x64\x00\x00\x00\x00" + bytes([transform])),
                     app(1, b"Exif\x00\x00junk"))
    return data.replace(sof0(), segment(0xC0, sof))


@pytest.mark.parametrize("transform", [0, 2])
@pytest.mark.parametrize("paranoid", [False, True])
def test_cmyk_file_keeps_adobe_app14(transform, paranoid):
    original = cmyk_jpeg(transform)
    cleaned = jpeg.strip(original, paranoid=paranoid)
    adobe = [s for s in jpeg.walk_segments(cleaned).segments if s.marker == 0xEE]
    assert len(adobe) == 1 and adobe[0].payload[-1] == transform
    assert image_segments(cleaned) == image_segments(original)
    assert section_keys(cleaned) == ["adobe"]


@pytest.mark.parametrize(
    "data",
    [
        kitchen_sink_jpeg()[:-200],  # cut inside the trailing JPEG: EOI of the first still there
        kitchen_sink_jpeg()[:3000],
        make_jpeg(scan=b"\x01\x02\xff"),
        SOI + app(1, b"Exif\x00\x00") + b"\xff\xe1\x00\x01" + EOI,
        SOI + b"\xff\xf0\x00\x02" + make_jpeg()[2:],  # reserved JPG0 marker
        SOI + b"\xff\x02\x00\x02" + make_jpeg()[2:],  # reserved low marker
    ],
)
def test_malformed_or_unsupported_input(data):
    try:
        cleaned = jpeg.strip(data)
    except MalformedFileError:
        return
    # Cutting after the first EOI leaves a valid JPEG; it must then be clean.
    assert leftovers(cleaned) == []


def test_malformed_input_produces_nothing(tmp_path):
    with pytest.raises(MalformedFileError):
        jpeg.strip(kitchen_sink_jpeg()[:3000])
    assert list(tmp_path.iterdir()) == []  # strip() itself never writes files


def mutate(data, rng):
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


def test_mutations_either_raise_or_produce_clean_output():
    """1,000 random mutations: strip raises MalformedFileError or returns a clean JPEG."""
    rng = random.Random(31337)
    originals = [kitchen_sink_jpeg("<"), kitchen_sink_jpeg(">")]
    stripped = 0
    for i in range(1000):
        data = mutate(rng.choice(originals), rng)
        paranoid = bool(i % 2)
        try:
            cleaned = jpeg.strip(data, paranoid=paranoid)
        except MalformedFileError:
            continue
        stripped += 1
        result = jpeg.parse(cleaned)
        assert result["complete"]
        assert leftovers(cleaned) == []
        assert "trailing" not in {s.key for s in result["sections"]}
        assert jpeg.image_data(cleaned) == jpeg.image_data(data)
    assert stripped > 300  # most mutations leave a strippable JPEG


def test_short_jfif_or_adobe_is_refused():
    with pytest.raises(MalformedFileError):
        jpeg.strip(make_jpeg(app(0, b"JFIF\x00\x01")))
    with pytest.raises(MalformedFileError):
        jpeg.strip(make_jpeg(app(14, b"Adobe\x00")))
