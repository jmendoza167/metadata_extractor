"""PNG: real image variants, the kitchen sink, decompression bombs, damaged
files and random mutations."""

import random
import time
import zlib

import pytest

from helpers.jpeg import icc_profile
from helpers.png import (ADAM7, SIGNATURE, chunk, expected_raw_size, iccp, itxt,
                         kitchen_sink_png, make_png, text, variants, zlib_bomb, ztxt)
from metaex.core.errors import CannotCleanError, MalformedFileError
from metaex.core.report import Report, render_json, render_text
from metaex.core.verify import verify
from metaex.formats import png

VARIANTS = variants()


# -- the builders make real PNGs -------------------------------------------------------


def _ihdr(data):
    d = png.walk_chunks(data).chunks[0].data
    return (int.from_bytes(d[0:4], "big"), int.from_bytes(d[4:8], "big"), d[8], d[9], d[12])


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_variant_pixel_data_matches_its_header(name):
    """Decompressed IDAT data has exactly the size the IHDR implies, and every
    scanline starts with a valid filter byte (what a decoder checks first)."""
    data = VARIANTS[name]
    width, height, depth, color, interlace = _ihdr(data)
    s = png.walk_chunks(data)
    raw = zlib.decompress(b"".join(c.data for c in s.all("IDAT")))
    assert len(raw) == expected_raw_size(width, height, depth, color, interlace)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    pos = 0
    passes = ADAM7 if interlace else ((0, 0, 1, 1),)
    for x0, y0, dx, dy in passes:
        w = max(0, -(-(width - x0) // dx))
        h = max(0, -(-(height - y0) // dy))
        if not (w and h):
            continue
        row = (w * channels * depth + 7) // 8
        for _ in range(h):
            assert raw[pos] <= 4  # filter type 0-4
            pos += 1 + row
    assert pos == len(raw)


@pytest.mark.parametrize("name", sorted(VARIANTS))
@pytest.mark.parametrize("paranoid", [False, True])
def test_every_variant_strips_and_verifies(name, paranoid):
    original = VARIANTS[name]
    cleaned = png.strip(original, paranoid=paranoid)
    result = verify(cleaned, original)
    assert result.clean, result.leftovers + result.problems
    assert result.image_data_match is True
    assert png.strip(cleaned, paranoid=paranoid) == cleaned  # idempotent
    assert "tEXt" not in {c.type for c in png.walk_chunks(cleaned).chunks}


# -- kitchen sink ------------------------------------------------------------------------


@pytest.mark.parametrize("order", ["<", ">"])
def test_kitchen_sink_show(order):
    parsed = png.parse(kitchen_sink_png(order))
    keys = [s.key for s in parsed["sections"]]
    assert keys == ["text", "ai", "xmp", "exif", "gps", "time", "icc", "color",
                    "content_credentials", "animation", "unknown", "trailing"]
    sections = {s.key: {f.key: f.value for f in s.fields} for s in parsed["sections"]}
    assert set(sections["ai"]) == {"parameters", "prompt", "workflow"}
    assert sections["text"]["Private Note"] == "meet at the cafe at 5"
    assert sections["text"]["Title (2)"] == "Vacances"
    assert sections["exif"]["Orientation"].startswith("Rotated 90")
    assert sections["icc"]["profile_name"] == "Jane's MacBook display"
    assert sections["animation"]["frames"] == 2
    critical = [w for w in parsed["warnings"] if w.startswith("CRITICAL")]
    assert len(critical) == 3  # XMP GPS, EXIF GPS, data after IEND
    assert parsed["complete"]


@pytest.mark.parametrize("order", ["<", ">"])
@pytest.mark.parametrize("paranoid", [False, True])
def test_kitchen_sink_strip(order, paranoid):
    original = kitchen_sink_png(order)
    cleaned = png.strip(original, paranoid=paranoid)
    result = verify(cleaned, original)
    assert result.clean and result.image_data_match
    kept = {f.section for f in result.kept_by_design}
    expected = {"EXIF", "Color and display", "Animation"} | (set() if paranoid else
                                                             {"ICC profile"})
    assert kept == expected
    parsed = png.parse(cleaned)
    assert not [w for w in parsed["warnings"] if w.startswith("CRITICAL")]
    assert png.strip(cleaned, paranoid=paranoid) == cleaned


# -- decompression bombs -----------------------------------------------------------------

BOMB = zlib_bomb(256 * 1024 * 1024)  # 256 MiB of zeros in about 250 KB


@pytest.mark.parametrize("bomb_chunk", [
    ztxt("Comment", "", compressed=BOMB),
    itxt("Description", "", compressed=BOMB),
    iccp(b"", compressed=BOMB),
])
def test_bombs_are_capped_in_show_and_dropped_by_strip(bomb_chunk):
    data = make_png(*[bomb_chunk] * 3, text("Author", "after the bombs"))
    start = time.monotonic()
    parsed = png.parse(data)
    assert any("16 MiB limit" in w for w in parsed["warnings"])
    values = {f.key: f.value for s in parsed["sections"] for f in s.fields}
    if bomb_chunk[4:8] != b"iCCP":
        assert values["Author"] == "after the bombs"
    cleaned = png.strip(data)
    assert {c.type for c in png.walk_chunks(cleaned).chunks} == {"IHDR", "IDAT", "IEND"}
    assert time.monotonic() - start < 20


# -- damaged files -----------------------------------------------------------------------


def test_bad_crc_show_warns_strip_refuses():
    data = bytearray(kitchen_sink_png())
    s = png.walk_chunks(bytes(data))
    author = next(c for c in s.chunks if c.type == "tEXt")
    data[author.offset + 8] ^= 0xFF  # change one byte of its data
    parsed = png.parse(bytes(data))
    assert any("bad CRC" in w for w in parsed["warnings"])
    assert parsed["complete"]
    with pytest.raises(MalformedFileError, match="bad CRC"):
        png.strip(bytes(data))


def test_missing_iend():
    data = make_png(text("Author", "x"), iend=False)
    parsed = png.parse(data)
    assert any("missing IEND" in w for w in parsed["warnings"])
    with pytest.raises(MalformedFileError, match="missing IEND"):
        png.strip(data)


@pytest.mark.parametrize("cut", [9, 20, 33, -13, -1])
def test_truncated_files(cut):
    data = kitchen_sink_png()
    truncated = data[:cut] if cut > 0 else data[: png.walk_chunks(data).end_of_image + cut]
    parsed = png.parse(truncated)
    assert not parsed["complete"] or any("missing IEND" in w for w in parsed["warnings"])
    with pytest.raises(MalformedFileError):
        png.strip(truncated)


def test_huge_declared_length_does_not_allocate():
    data = SIGNATURE + make_png()[8:41] + (2**31 - 1).to_bytes(4, "big") + b"tEXt"
    parsed = png.parse(data)
    assert not parsed["complete"]


def test_many_tiny_chunks_are_capped(monkeypatch):
    monkeypatch.setattr(png, "MAX_CHUNKS", 1000)
    data = make_png(*[chunk("zzZZ")] * 2000)
    parsed = png.parse(data)
    assert not parsed["complete"]
    assert any("more than 1000 chunks" in w for w in parsed["warnings"])
    with pytest.raises(MalformedFileError):
        png.strip(data)


# -- mutation tests ----------------------------------------------------------------------


def fix_crcs(data: bytes) -> bytes:
    """Recompute the CRC of every complete chunk, so mutations reach chunk contents."""
    d = bytearray(data)
    pos = 8
    while pos + 12 <= len(d):
        length = int.from_bytes(d[pos : pos + 4], "big")
        end = pos + 12 + length
        if end > len(d):
            break
        crc = zlib.crc32(bytes(d[pos + 4 : pos + 8 + length]))
        d[pos + 8 + length : end] = crc.to_bytes(4, "big")
        if d[pos + 4 : pos + 8] == b"IEND":
            break
        pos = end
    return bytes(d)


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
    return fix_crcs(out) if rng.random() < 0.7 else out


ORIGINALS = [kitchen_sink_png("<"), kitchen_sink_png(">"), VARIANTS["apng"],
             VARIANTS["palette_trns"]]


def test_random_mutations_only_raise_malformed_file_error():
    """2,000 random mutations: parse (and rendering) succeeds or raises
    MalformedFileError; any other exception is a bug."""
    rng = random.Random(20261120)
    ok = 0
    for _ in range(2000):
        data = mutate(rng.choice(ORIGINALS), rng)
        try:
            result = png.parse(data)
        except MalformedFileError:
            continue
        report = Report("mutated.png", result["sections"] + [result["structure"]],
                        result["warnings"])
        render_text(report, width=100)
        render_json(report)
        ok += 1
    assert ok > 1500  # only a damaged signature makes parse() raise


def test_mutations_either_raise_or_produce_clean_output():
    """1,000 random mutations: strip raises MalformedFileError or returns a PNG
    that verifies clean with unchanged image data."""
    rng = random.Random(31337)
    stripped = 0
    for i in range(1000):
        data = mutate(rng.choice(ORIGINALS), rng)
        paranoid = bool(i % 2)
        try:
            cleaned = png.strip(data, paranoid=paranoid)
        except (MalformedFileError, CannotCleanError):
            continue  # damaged, or image data too large to check for hidden data
        stripped += 1
        result = verify(cleaned, data)
        assert result.clean, (i, result.leftovers, result.problems)
        assert result.image_data_match is True
        assert png.strip(cleaned, paranoid=paranoid) == cleaned
    # With CRCs fixed, many mutations stay strippable (damaged image data is
    # refused, since the image data stream is checked).
    assert stripped > 100


def test_unknown_critical_chunk_and_icc_profile_are_handled():
    data = make_png(chunk("ABCD", b"x"), before_idat=(iccp(icc_profile()),))
    assert any(f.label == "ABCD" for s in png.parse(data)["sections"] for f in s.fields)
    with pytest.raises(MalformedFileError, match="unsupported critical chunk"):
        png.strip(data)
