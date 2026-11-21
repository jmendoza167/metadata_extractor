"""WebP: image variants, the kitchen sink, damaged files and random mutations."""

import random

import pytest

from helpers.webp import chunk, kitchen_sink_webp, riff, variants, vp8l
from metaex.core.errors import MalformedFileError
from metaex.core.report import Report, render_json, render_text
from metaex.core.verify import verify
from metaex.formats import webp

VARIANTS = variants()


# -- the builders make well-formed WebPs -------------------------------------------------


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_variant_structure(name):
    data = VARIANTS[name]
    s = webp.walk_chunks(data)
    assert s.error is None and s.warnings == [] and s.end_of_image == len(data)
    webp.checked_structure(data)  # one image, or frames that each hold one
    canvas = webp.canvas_size(s.chunks[0])
    assert canvas is not None
    for frame in s.all("ANMF"):
        header = frame.data[:16]
        width = 1 + int.from_bytes(header[6:9], "little")
        height = 1 + int.from_bytes(header[9:12], "little")
        image = next(c for c in frame.children if c.fourcc in ("VP8 ", "VP8L"))
        assert webp.canvas_size(image) == (width, height)
        x, y = 2 * int.from_bytes(header[0:3], "little"), 2 * int.from_bytes(header[3:6], "little")
        assert x + width <= canvas[0] and y + height <= canvas[1]


@pytest.mark.parametrize("name", sorted(VARIANTS))
@pytest.mark.parametrize("paranoid", [False, True])
def test_every_variant_strips_and_verifies(name, paranoid):
    original = VARIANTS[name]
    cleaned = webp.strip(original, paranoid=paranoid)
    result = verify(cleaned, original)
    assert result.clean, (result.leftovers, result.problems)
    assert result.image_data_match is True
    assert webp.strip(cleaned, paranoid=paranoid) == cleaned  # idempotent
    assert webp.walk_chunks(cleaned).first("XMP ") is None


# -- kitchen sink ------------------------------------------------------------------------


@pytest.mark.parametrize("order", ["<", ">"])
def test_kitchen_sink_show(order):
    parsed = webp.parse(kitchen_sink_webp(order))
    keys = [s.key for s in parsed["sections"]]
    assert keys == ["exif", "gps", "xmp", "icc", "animation", "unknown", "trailing"]
    values = {s.key: {f.key: f.value for f in s.fields} for s in parsed["sections"]}
    assert values["exif"]["BodySerialNumber"] == "SN-0001"
    assert values["exif"]["Orientation"].startswith("Rotated 90")
    assert values["animation"]["frames"] == 2
    assert [f.label for f in parsed["sections"][5].fields] == ["FRMx", "prVt", "odd!"]
    critical = [w for w in parsed["warnings"] if w.startswith("CRITICAL")]
    assert len(critical) == 3  # EXIF GPS, XMP GPS, data after the RIFF data
    assert parsed["complete"]


@pytest.mark.parametrize("order", ["<", ">"])
@pytest.mark.parametrize("paranoid", [False, True])
def test_kitchen_sink_strip(order, paranoid):
    original = kitchen_sink_webp(order)
    cleaned = webp.strip(original, paranoid=paranoid)
    result = verify(cleaned, original)
    assert result.clean and result.image_data_match
    kept = {f.section for f in result.kept_by_design}
    assert kept == {"EXIF", "Animation"} | (set() if paranoid else {"ICC profile"})
    assert not [w for w in webp.parse(cleaned)["warnings"] if w.startswith("CRITICAL")]
    assert webp.strip(cleaned, paranoid=paranoid) == cleaned
    for secret in (b"SN-0001", b"ExampleCam", b"FRMx", b"prVt", b"secret", b"xmpmeta"):
        assert secret not in cleaned


# -- damaged files -----------------------------------------------------------------------


@pytest.mark.parametrize("cut", [12, 20, 30, 100, -30, -18, -1])
def test_truncated_files(cut):
    data = kitchen_sink_webp()
    end = webp.walk_chunks(data).end_of_image
    truncated = data[:cut] if cut > 0 else data[: end + cut]
    parsed = webp.parse(truncated)
    assert not parsed["complete"]
    with pytest.raises(MalformedFileError):
        webp.strip(truncated)


def test_huge_chunk_size_does_not_allocate():
    data = riff(b"VP8L" + (2**32 - 1).to_bytes(4, "little") + b"abc")
    assert not webp.parse(data)["complete"]


def test_many_tiny_chunks_are_capped(monkeypatch):
    monkeypatch.setattr(webp, "MAX_CHUNKS", 1000)
    data = riff(chunk("VP8L", vp8l(4, 4)) + chunk("zzzz") * 2000)
    parsed = webp.parse(data)
    assert not parsed["complete"]
    assert any("more than 1000 chunks" in w for w in parsed["warnings"])
    with pytest.raises(MalformedFileError):
        webp.strip(data)


# -- mutation tests ----------------------------------------------------------------------


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
    out = bytes(d)
    if rng.random() < 0.5 and len(out) >= 12:  # keep the RIFF size consistent
        out = out[:4] + (len(out) - 8).to_bytes(4, "little") + out[8:]
    return out


ORIGINALS = [kitchen_sink_webp("<"), kitchen_sink_webp(">"), VARIANTS["animated_lossy"],
             VARIANTS["lossy_alpha"]]


def test_random_mutations_only_raise_malformed_file_error():
    """2,000 random mutations: parse (and rendering) succeeds or raises
    MalformedFileError; any other exception is a bug."""
    rng = random.Random(20261120)
    ok = 0
    for _ in range(2000):
        data = mutate(rng.choice(ORIGINALS), rng)
        try:
            result = webp.parse(data)
        except MalformedFileError:
            continue
        report = Report("mutated.webp", result["sections"] + [result["structure"]],
                        result["warnings"])
        render_text(report, width=100)
        render_json(report)
        ok += 1
    assert ok > 1500  # only a damaged RIFF header makes parse() raise


def test_mutations_either_raise_or_produce_clean_output():
    """1,000 random mutations: strip raises MalformedFileError or returns a WebP
    that verifies clean with unchanged image data."""
    rng = random.Random(31337)
    stripped = 0
    for i in range(1000):
        data = mutate(rng.choice(ORIGINALS), rng)
        paranoid = bool(i % 2)
        try:
            cleaned = webp.strip(data, paranoid=paranoid)
        except MalformedFileError:
            continue
        stripped += 1
        result = verify(cleaned, data)
        assert result.clean, (i, result.leftovers, result.problems)
        assert result.image_data_match is True
        assert webp.strip(cleaned, paranoid=paranoid) == cleaned
    assert stripped > 100
