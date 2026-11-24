"""WAV: every chunk type, odd sizes, wrong RIFF sizes, trailing data, RF64,
damaged files and random mutations."""

import random
import subprocess
import sys

import pytest

from helpers import wav as w
from metaex.core.errors import MalformedFileError
from metaex.core.report import Report, render_json, render_text
from metaex.core.verify import verify
from metaex.formats import wav
from metaex.formats.wav import parse, strip, walk_chunks

VARIANTS = w.variants()


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


def test_the_kitchen_sink_has_every_chunk_type():
    s = walk_chunks(VARIANTS["kitchen_sink"])
    labels = [c.label for c in s.chunks]
    assert labels == ["fmt", "bext", "JUNK", "LIST INFO", "iXML", "axml", "_PMX", "id3", "data",
                      "cue", "LIST adtl", "smpl", "PEAK"]
    keys = {sec.key for sec in parse(VARIANTS["kitchen_sink"])["sections"]}
    assert {"wav_format", "wav_info", "wav_bext", "wav_ixml", "wav_axml", "xmp", "id3v2",
            "wav_adtl", "wav_cue", "wav_smpl", "audio_pictures", "gps", "hidden_chunk_data",
            "unknown"} <= keys


@pytest.mark.parametrize("payload", [b"a", b"abc", b"abcde"])
def test_odd_sizes_everywhere(payload):
    data = w.riff(w.fmt(bits=8), w.chunk("zzzz", payload),
                  w.chunk("LIST", b"INFO" + w.chunk("IART", payload)),
                  w.chunk("data", payload))
    s = walk_chunks(data)
    assert s.error is None and s.end == len(data)
    assert all(c.padding == b"\x00" for c in s.chunks if c.size % 2)
    cleaned = strip(data)
    assert walk_chunks(cleaned).first("data").data == payload
    assert len(cleaned) % 2 == 0


@pytest.mark.parametrize("delta", [-2, -100])
def test_riff_size_smaller_than_the_file(delta):
    good = w.wav(w.info(("IART", "Jane Doe")))
    size = int.from_bytes(good[4:8], "little") + delta
    data = good[:4] + size.to_bytes(4, "little") + good[8:]
    parsed = parse(data)
    assert not parsed["complete"]  # the data chunk is cut by the RIFF end
    with pytest.raises(MalformedFileError):
        strip(data)


def test_riff_size_that_leaves_whole_chunks_outside():
    inside = w.wav()
    data = inside + w.info(("IART", "Jane Doe"))
    parsed = parse(data)
    keys = [s.key for s in parsed["sections"]]
    assert "audio_trailing" in keys and "wav_info" not in keys
    assert strip(data) == inside


@pytest.mark.parametrize("cut", [11, 12, 20, 36, 44, 60, -1, -2, -100])
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


def test_huge_sizes_do_not_allocate():
    for data in (b"RIFF\xff\xff\xff\xffWAVE" + w.fmt(),
                 w.riff(w.fmt(), b"data\xff\xff\xff\xff" + bytes(10)),
                 w.riff(w.fmt(), w.chunk("cue ", (2**31).to_bytes(4, "little"))),
                 w.riff(w.fmt(), w.chunk("smpl", bytes(28) + (2**31).to_bytes(4, "little")
                                         + bytes(4)))):
        parsed = parse(data)
        assert not parsed["complete"]


def test_nested_lists_are_not_walked_deeper():
    inner = w.chunk("LIST", b"INFO" + w.chunk("IART", b"x\x00"))
    data = w.wav(w.chunk("LIST", b"INFO" + inner))
    s = walk_chunks(data)
    info = s.lists("INFO")[0]
    assert [c.fourcc for c in info.children] == ["LIST"] and info.children[0].children == []
    assert strip(data) == w.wav()


def test_rf64_and_bw64_are_refused(tmp_path):
    for magic in (b"RF64", b"BW64"):
        path = tmp_path / "big.wav"
        path.write_bytes(magic + b"\xff\xff\xff\xffWAVE" + w.chunk("ds64", bytes(28)) + w.fmt()
                         + w.data(w.sine(10)))
        r = metaex("strip", path)
        assert r.returncode == 1
        assert "RF64 and BW64 files (WAV files over 4 GiB) are not supported" in r.stderr
        assert sorted(p.name for p in tmp_path.iterdir()) == ["big.wav"]
        with pytest.raises(MalformedFileError, match="not supported"):
            parse(path.read_bytes())


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
    out = bytes(d)
    if rng.random() < 0.5 and len(out) >= 12:  # keep the RIFF size consistent
        out = out[:4] + (len(out) - 8).to_bytes(4, "little") + out[8:]
    return out


ORIGINALS = [VARIANTS[n] for n in ("kitchen_sink", "cue_adtl", "smpl", "extensible", "bext_v2",
                                   "pcm8_odd", "id3_chunk")]


def test_random_mutations_only_raise_malformed_file_error():
    """2,000 random mutations: parse (and rendering) succeeds or raises
    MalformedFileError; anything else is a bug."""
    rng = random.Random(20261124)
    ok = 0
    for _ in range(2000):
        data = mutate(rng.choice(ORIGINALS), rng)
        try:
            result = parse(data)
        except MalformedFileError:
            continue
        report = Report("mutated.wav", result["sections"] + [result["structure"]],
                        result["warnings"])
        render_text(report, width=100)
        render_json(report)
        ok += 1
    assert ok > 1500


def test_mutations_either_raise_or_produce_clean_output():
    """1,000 random mutations: strip refuses, or returns a WAV that verifies
    clean with the same fmt and data."""
    rng = random.Random(777)
    stripped = 0
    for i in range(1000):
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
    assert stripped > 100


def test_module_limits_are_reasonable():
    assert wav.MAX_CHUNKS >= 10_000 and wav.MAX_CUE_POINTS >= 10_000
