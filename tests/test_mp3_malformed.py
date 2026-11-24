"""MP3: every tag version and position, every kind of stream, damaged files
and random mutations."""

import random

import pytest

from helpers import audiotags as at
from helpers import id3 as b
from helpers import mp3
from metaex.core import id3
from metaex.core.errors import MalformedFileError, UnsupportedFormatError
from metaex.core.report import Report, render_json, render_text
from metaex.core.verify import verify
from metaex.formats import mp3 as mp3_format
from metaex.formats.mp3 import checked_layout, parse, strip

VARIANTS = mp3.variants()
REFUSED = (MalformedFileError, UnsupportedFormatError)


# -- every variant ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_every_variant_parses_completely(name):
    parsed = parse(VARIANTS[name])
    assert parsed["complete"], parsed["warnings"]
    assert not any("malformed" in w for w in parsed["warnings"])


@pytest.mark.parametrize("name", sorted(VARIANTS))
@pytest.mark.parametrize("paranoid", [False, True])
def test_every_variant_strips_and_verifies(name, paranoid):
    original = VARIANTS[name]
    cleaned = strip(original, paranoid=paranoid)
    result = verify(cleaned, original)
    assert result.clean, (result.leftovers, result.problems)
    assert result.image_data_match is True
    assert strip(cleaned, paranoid=paranoid) == cleaned  # stable
    found, walk = checked_layout(cleaned)
    assert found.tag_ranges() == [] and walk.junk == []
    if paranoid:
        assert walk.info is None


@pytest.mark.parametrize("name", ["id3v22", "id3v23", "id3v24", "id3v23_unsync",
                                  "id3v24_unsync_frames", "apev2"])
def test_pictures_in_every_tag_version_are_examined(name):
    parsed = parse(VARIANTS[name])
    keys = [s.key for s in parsed["sections"]]
    assert "audio_pictures" in keys and "gps" in keys, keys


def test_kitchen_sink_show():
    parsed = parse(VARIANTS["kitchen_sink"])
    keys = [s.key for s in parsed["sections"]]
    for key in ("audio", "encoder_info", "id3v2", "ape", "lyrics3", "id3v1", "audio_pictures",
                "gps", "hidden_tag_data", "hidden_audio_data"):
        assert key in keys, key
    assert keys.count("id3v2") == 2
    values = {(s.key, f.key): f.value for s in parsed["sections"] for f in s.fields}
    assert values[("id3v2", "POPM:jane.doe@example.com")] == "rating 196/255, play count 42"
    assert values[("id3v1", "comment")] == "jane.doe@example.com"
    assert values[("encoder_info", "encoder")] == "LAME3.100"


# -- damaged files ---------------------------------------------------------------------------


@pytest.mark.parametrize("cut", [5, 9, 50, 3000, -1, -100, -129, -300])
def test_truncated_files(cut):
    data = VARIANTS["kitchen_sink"]
    truncated = data[:cut]
    try:
        parsed = parse(truncated)
    except REFUSED:
        pass
    else:
        render_text(Report("x.mp3", parsed["sections"], parsed["warnings"]))
    if cut > 0 and cut < 4000:
        with pytest.raises(REFUSED):
            strip(truncated)


def test_truncated_audio_is_refused_and_reported():
    data = b.tag(b.text("TPE1", "Jane Doe")) + mp3.stream(5)[:-10]
    parsed = parse(data)
    assert not parsed["complete"]
    assert any("is truncated" in w for w in parsed["warnings"])
    with pytest.raises(MalformedFileError, match="is truncated"):
        strip(data)


@pytest.mark.parametrize("size", [2**28 - 1, 5000])
def test_a_tag_larger_than_the_file(size):
    data = bytearray(b.tag(b.text("TPE1", "Jane Doe")) + mp3.frames(3))
    data[6:10] = b.synchsafe(size)
    with pytest.raises(MalformedFileError, match="is truncated"):
        parse(bytes(data))
    with pytest.raises(MalformedFileError):
        strip(bytes(data))


def test_a_frame_larger_than_its_tag():
    raw = bytearray(b.text("TPE1", "Jane Doe", version=3))
    raw[4:8] = (2**31).to_bytes(4, "big")
    data = b.tag(bytes(raw), version=3) + mp3.frames(3)
    parsed = parse(data)
    assert not parsed["complete"]
    with pytest.raises(MalformedFileError, match="is malformed"):
        strip(data)


@pytest.mark.parametrize("head", [b"ID3\x05\x00\x00\x00\x00\x00\x10",
                                  b"ID3\x03\xff\x00\x00\x00\x00\x10",
                                  b"ID3\x03\x00\x00\x00\x00\x80\x10"])
def test_invalid_tag_headers_are_hidden_data(head):
    data = head + bytes(16) + mp3.frames(4)
    parsed = parse(data)
    keys = [s.key for s in parsed["sections"]]
    assert "hidden_audio_data" in keys and "id3v2" not in keys
    assert strip(data) == mp3.frames(4)


def test_many_frames_are_capped(monkeypatch):
    monkeypatch.setattr(id3, "MAX_FRAMES", 50)
    data = b.tag(*[b.text("TIT2", "x")] * 60) + mp3.frames(3)
    parsed = parse(data)
    assert not parsed["complete"]
    assert any("more than 50 frames" in w for w in parsed["warnings"])
    with pytest.raises(MalformedFileError):
        strip(data)


def test_huge_ape_and_lyrics_sizes_do_not_allocate():
    ape = bytearray(at.ape([at.ape_item("Artist", b"x")], header=False))
    ape[-20:-16] = (2**32 - 1).to_bytes(4, "little")
    lyrics = b"LYRICSBEGIN" + b"999999LYRICS200"
    for tail in (bytes(ape), lyrics):
        parsed = parse(mp3.frames(3) + tail)
        assert not parsed["complete"]


def test_huge_frame_counts_in_the_information_frame_are_only_reported():
    data = mp3.xing_frame(2**32 - 1, 2**32 - 1) + mp3.frames(3)
    parsed = parse(data)
    values = {(s.key, f.key): f.value for s in parsed["sections"] for f in s.fields}
    assert values[("encoder_info", "frames")] == 2**32 - 1
    assert values[("audio", "frames")] == 3


def test_many_gaps_are_capped(monkeypatch):
    monkeypatch.setattr(mp3_format, "MAX_JUNK_REGIONS", 20)
    data = (mp3.frames(2) + b"x") * 30
    parsed = parse(data)
    assert not parsed["complete"]
    with pytest.raises(MalformedFileError, match="gaps"):
        strip(data)


def test_not_mp3_at_all():
    with pytest.raises(MalformedFileError, match="no MPEG audio frames"):
        strip(b.tag(b.text("TPE1", "x")) + b"plain text, not audio" * 10)


# -- mutation tests ------------------------------------------------------------------------------


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


ORIGINALS = [VARIANTS[n] for n in ("kitchen_sink", "id3v24_unsync_frames", "end_order_2",
                                   "chapters", "xing_vbr", "vbri", "id3v22", "layer1")]


def test_random_mutations_only_raise_metaex_errors():
    """2,000 random mutations: parse (and rendering) succeeds or raises
    MalformedFileError / UnsupportedFormatError; anything else is a bug."""
    rng = random.Random(20261123)
    ok = 0
    for _ in range(2000):
        data = mutate(rng.choice(ORIGINALS), rng)
        try:
            result = parse(data)
        except REFUSED:
            continue
        report = Report("mutated.mp3", result["sections"] + [result["structure"]],
                        result["warnings"])
        render_text(report, width=100)
        render_json(report)
        ok += 1
    assert ok > 1500


def test_mutations_either_raise_or_produce_clean_output():
    """1,000 random mutations: strip refuses, or returns frames only that
    verify clean with the same audio frames."""
    rng = random.Random(4242)
    stripped = 0
    for i in range(1000):
        data = mutate(rng.choice(ORIGINALS), rng)
        paranoid = bool(i % 2)
        try:
            cleaned = strip(data, paranoid=paranoid)
        except REFUSED:
            continue
        stripped += 1
        result = verify(cleaned, data)
        assert result.clean, (i, result.leftovers, result.problems)
        assert result.image_data_match is True
        assert strip(cleaned, paranoid=paranoid) == cleaned
    assert stripped > 100
