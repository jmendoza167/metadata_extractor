"""GIF: real image variants, the kitchen sink, damaged files and random mutations."""

import random

import pytest

from helpers.gif import kitchen_sink_gif, lzw_decode, make_gif, variants
from metaex.core.errors import CannotCleanError, MalformedFileError
from metaex.core.report import Report, render_json, render_text
from metaex.core.verify import verify
from metaex.formats import gif

VARIANTS = variants()


# -- the builders make real GIFs ---------------------------------------------------------


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_variant_lzw_data_decodes_to_the_frame_size(name):
    """Every frame's LZW data decodes to exactly width x height color indices,
    each within the color table in use (what a decoder checks)."""
    s = gif.walk_blocks(VARIANTS[name])
    assert s.error is None and s.end_of_image == len(VARIANTS[name])
    for img in s.images:
        width = int.from_bytes(img.descriptor[5:7], "little")
        height = int.from_bytes(img.descriptor[7:9], "little")
        indices = lzw_decode(b"".join(img.sub_blocks), img.min_code_size)
        assert len(indices) == width * height
        colors = len(img.color_table or s.global_color_table) // 3
        assert max(indices) < colors


@pytest.mark.parametrize("name", sorted(VARIANTS))
@pytest.mark.parametrize("paranoid", [False, True])
def test_every_variant_strips_and_verifies(name, paranoid):
    original = VARIANTS[name]
    cleaned = gif.strip(original, paranoid=paranoid)
    result = verify(cleaned, original)
    assert result.clean, (result.leftovers, result.problems)
    assert result.image_data_match is True
    assert gif.strip(cleaned, paranoid=paranoid) == cleaned  # idempotent
    assert gif.walk_blocks(cleaned).extensions(gif.COMMENT) == []


# -- kitchen sink ------------------------------------------------------------------------


def test_kitchen_sink_show():
    parsed = gif.parse(kitchen_sink_gif())
    keys = [s.key for s in parsed["sections"]]
    assert keys == ["comments", "plain_text", "xmp", "icc", "animation", "app_extensions",
                    "trailing"]
    values = {s.key: {f.key: f.value for f in s.fields} for s in parsed["sections"]}
    assert values["comments"]["comment_2"] == "caf" + chr(0xE9) + " at 5"
    assert values["plain_text"]["plain_text_1"] == "meet at the cafe at 5"
    assert values["animation"] == {"frames": 3, "loop_count": "forever"}
    assert [f.label for f in parsed["sections"][5].fields] == [
        "MGK8BIM0000", "Extension 0x42", "ImageMagick"]
    critical = [w for w in parsed["warnings"] if w.startswith("CRITICAL")]
    assert len(critical) == 2  # XMP GPS, data after the trailer
    assert parsed["complete"]


@pytest.mark.parametrize("paranoid", [False, True])
def test_kitchen_sink_strip(paranoid):
    original = kitchen_sink_gif()
    cleaned = gif.strip(original, paranoid=paranoid)
    result = verify(cleaned, original)
    assert result.clean and result.image_data_match
    kept = {f.section for f in result.kept_by_design}
    assert kept == ({"Animation"} if paranoid else {"Animation", "ICC profile"})
    assert not [w for w in gif.parse(cleaned)["warnings"] if w.startswith("CRITICAL")]
    assert gif.strip(cleaned, paranoid=paranoid) == cleaned
    for secret in (b"Jane", b"cafe", b"MGK8BIM", b"ImageMagick", b"secret", b"XMP"):
        assert secret not in cleaned


# -- damaged files -----------------------------------------------------------------------


@pytest.mark.parametrize("cut", [7, 13, 30, 100, -30, -21, -2])
def test_truncated_files(cut):
    data = kitchen_sink_gif()
    end = gif.walk_blocks(data).end_of_image
    truncated = data[:cut] if cut > 0 else data[: end + cut]
    try:
        parsed = gif.parse(truncated)
    except MalformedFileError:
        pass  # header or screen descriptor cut off
    else:
        assert not parsed["complete"] or any("missing GIF trailer" in w
                                              for w in parsed["warnings"])
    with pytest.raises(MalformedFileError):
        gif.strip(truncated)


def test_sub_block_length_past_the_end():
    data = make_gif()[:-1]  # without the trailer
    data = data[:-1] + b"\xff\x00"  # the LZW terminator becomes a 255-byte sub-block
    parsed = gif.parse(data)
    assert not parsed["complete"]
    with pytest.raises(MalformedFileError):
        gif.strip(data)


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
    return bytes(d)


ORIGINALS = [kitchen_sink_gif(), VARIANTS["animated"], VARIANTS["interlaced"],
             VARIANTS["256_colors"]]


def test_random_mutations_only_raise_malformed_file_error():
    """2,000 random mutations: parse (and rendering) succeeds or raises
    MalformedFileError; any other exception is a bug."""
    rng = random.Random(20261120)
    ok = 0
    for _ in range(2000):
        data = mutate(rng.choice(ORIGINALS), rng)
        try:
            result = gif.parse(data)
        except MalformedFileError:
            continue
        report = Report("mutated.gif", result["sections"] + [result["structure"]],
                        result["warnings"])
        render_text(report, width=100)
        render_json(report)
        ok += 1
    assert ok > 1500  # only a damaged header or screen descriptor makes parse() raise


def test_mutations_either_raise_or_produce_clean_output():
    """1,000 random mutations: strip raises MalformedFileError or returns a GIF
    that verifies clean with unchanged image data."""
    rng = random.Random(31337)
    stripped = 0
    for i in range(1000):
        data = mutate(rng.choice(ORIGINALS), rng)
        paranoid = bool(i % 2)
        try:
            cleaned = gif.strip(data, paranoid=paranoid)
        except (MalformedFileError, CannotCleanError):
            continue  # damaged, or the image data holds more than the image
        stripped += 1
        result = verify(cleaned, data)
        assert result.clean, (i, result.leftovers, result.problems)
        assert result.image_data_match is True
        assert gif.strip(cleaned, paranoid=paranoid) == cleaned
    assert stripped > 200  # no checksums, so many mutations stay strippable
