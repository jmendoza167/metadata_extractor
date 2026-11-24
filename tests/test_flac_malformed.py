"""FLAC: every block type and tag position, last-block flag errors, wrong
lengths, damaged files and random mutations."""

import random

import pytest

from helpers import flac as f
from metaex.core.errors import MalformedFileError
from metaex.core.report import Report, render_json, render_text
from metaex.core.verify import verify
from metaex.formats import flac
from metaex.formats.flac import layout, parse, strip

VARIANTS = f.variants()


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_every_variant_parses_completely(name):
    parsed = parse(VARIANTS[name])
    assert parsed["complete"], parsed["warnings"]


@pytest.mark.parametrize("name", sorted(VARIANTS))
@pytest.mark.parametrize("paranoid", [False, True])
def test_every_variant_strips_and_verifies(name, paranoid):
    original = VARIANTS[name]
    cleaned = strip(original, paranoid=paranoid)
    result = verify(cleaned, original)
    assert result.clean, (result.leftovers, result.problems)
    assert result.image_data_match is True
    assert strip(cleaned, paranoid=paranoid) == cleaned
    found = layout(cleaned)
    assert {blk.name for blk in found.blocks} <= {"STREAMINFO", "SEEKTABLE", "CUESHEET"}
    assert found.audio_end == len(cleaned) and not found.leading


# -- last-block flags and lengths ----------------------------------------------------------------


def blocks_with(*heads: tuple[int, int, bytes]) -> bytes:
    """fLaC, STREAMINFO (not last), then (type, flag byte, payload) blocks with
    the given header flags, then the frames."""
    info, frames = f.encode([f.sine()])
    out = b"fLaC" + f.block(f.STREAMINFO, info)
    for block_type, last, payload in heads:
        out += bytes([block_type | last]) + len(payload).to_bytes(3, "big") + payload
    return out + frames


def test_no_block_says_it_is_the_last():
    error = layout(blocks_with((f.PADDING, 0, bytes(8)))).error
    assert error is not None  # the frames are read as block headers


def test_two_blocks_say_they_are_the_last():
    error = layout(blocks_with((f.PADDING, 0x80, bytes(8)), (f.PADDING, 0x80, bytes(8)))).error
    assert "no valid audio frame after the metadata blocks" in error


@pytest.mark.parametrize("delta", [-3, -1, 1, 3, 100])
def test_wrong_block_lengths(delta):
    data = bytearray(f.flac((f.VORBIS_COMMENT, f.vorbis_comment(fields=[("A", "b")]))))
    offset = 42
    length = int.from_bytes(data[offset + 1:offset + 4], "big") + delta
    data[offset + 1:offset + 4] = length.to_bytes(3, "big")
    with pytest.raises(MalformedFileError):
        strip(bytes(data))
    assert not parse(bytes(data))["complete"]


def test_huge_lengths_do_not_allocate():
    data = b"fLaC" + bytes([0x80]) + b"\xff\xff\xff" + b"x" * 10
    assert not parse(data)["complete"]


def test_many_blocks_are_capped(monkeypatch):
    monkeypatch.setattr(flac, "MAX_BLOCKS", 20)
    data = f.flac(*[(f.PADDING, b"")] * 25)
    assert "more than 20 metadata blocks" in layout(data).error


def test_frame_search_limits(monkeypatch):
    monkeypatch.setattr(flac, "MAX_FRAME_ATTEMPTS", 0)
    assert "could not be found" in layout(f.flac()).error


# -- tags around the stream ------------------------------------------------------------------


def test_broken_id3_before_the_stream():
    from helpers import id3 as b

    data = f.flac(before=b.tag(b.text("TPE1", "x"), padding=100))
    with pytest.raises(MalformedFileError):
        parse(data[:30] + data[200:])


def test_tags_after_a_cut_off_stream():
    from helpers import audiotags as at

    good = f.flac()
    data = good[:-50] + at.id3v1()
    assert "may be cut off" in layout(data).error


# -- damaged files ---------------------------------------------------------------------------


@pytest.mark.parametrize("cut", [3, 4, 8, 30, 42, 100, -1, -2, -100, -5000])
def test_truncated_files(cut):
    data = VARIANTS["kitchen_sink"]
    truncated = data[:cut]
    try:
        parsed = parse(truncated)
    except MalformedFileError:
        return
    assert not parsed["complete"]
    with pytest.raises(MalformedFileError):
        strip(truncated)


# -- mutation tests --------------------------------------------------------------------------


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


ORIGINALS = [VARIANTS[n] for n in ("kitchen_sink", "all_blocks", "stereo_mid_side", "lpc",
                                   "tags_after", "two_id3v2_before", "small_blocks")]


def test_random_mutations_only_raise_malformed_file_error():
    """1,500 random mutations: parse (and rendering) succeeds or raises
    MalformedFileError; anything else is a bug."""
    rng = random.Random(20261125)
    ok = 0
    for _ in range(1500):
        data = mutate(rng.choice(ORIGINALS), rng)
        try:
            result = parse(data)
        except MalformedFileError:
            continue
        report = Report("mutated.flac", result["sections"] + [result["structure"]],
                        result["warnings"])
        render_text(report, width=100)
        render_json(report)
        ok += 1
    assert ok > 1000


def test_mutations_either_raise_or_produce_clean_output():
    """800 random mutations: strip refuses, or returns a FLAC that verifies
    clean with the same STREAMINFO and frames."""
    rng = random.Random(8080)
    stripped = 0
    for i in range(800):
        data = mutate(rng.choice(ORIGINALS), rng)
        paranoid = bool(i % 2)
        try:
            cleaned = strip(data, paranoid=paranoid)
        except MalformedFileError:
            continue
        stripped += 1
        result = verify(cleaned, data)
        assert result.clean, (i, result.leftovers, result.problems)
        assert result.image_data_match is True
        assert strip(cleaned, paranoid=paranoid) == cleaned
    # Most mutations land in the (small) first or last frame, whose CRCs then
    # fail: those files are refused, which is right.
    assert stripped > 40
