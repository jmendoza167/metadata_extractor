"""metaex verify on WAV: fmt and data unchanged, only whitelisted chunks left."""

import json
import subprocess
import sys

import pytest

from helpers import wav as w
from metaex.core.verify import verify
from metaex.formats import wav
from metaex.formats.wav import strip

VARIANTS = w.variants()


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


def replace_chunk(data: bytes, fourcc: str, payload: bytes) -> bytes:
    """``data`` with one chunk's payload replaced (same position, new size)."""
    s = wav.walk_chunks(data)
    c = s.first(fourcc)
    chunks = [data[x.offset:x.end] if x is not c else wav.make_chunk(fourcc, payload)
              for x in s.chunks]
    return wav.riff(chunks)


@pytest.mark.parametrize("name", sorted(VARIANTS))
@pytest.mark.parametrize("paranoid", [False, True])
def test_every_variant_strips_and_verifies(name, paranoid):
    original = VARIANTS[name]
    result = verify(strip(original, paranoid=paranoid), original)
    assert result.clean, (result.leftovers, result.problems)
    assert result.image_data_match is True
    assert result.image_label == "Audio (fmt and data chunks)"


def test_kept_by_design():
    result = verify(strip(VARIANTS["kitchen_sink"]), VARIANTS["kitchen_sink"])
    kept = {(f.section, f.label) for f in result.kept_by_design}
    assert ("Cue points", "Cue point 2") in kept and ("Sampler loops (smpl)", "Loops") in kept
    assert ("Audio", "Format") in kept


def test_an_unstripped_file_has_leftovers():
    result = verify(VARIANTS["kitchen_sink"])
    assert not result.clean
    labels = {f.label for f in result.leftovers if f.section == "WAV structure"}
    assert "bext at offset 36" in labels
    assert any(label.startswith("LIST INFO at offset") for label in labels)
    assert any(label.startswith("JUNK at offset") for label in labels)
    sections = {f.section for f in result.leftovers}
    assert {"RIFF INFO", "Broadcast WAV (bext)", "iXML", "Cue point labels"} <= sections


@pytest.mark.parametrize("extra, label", [
    (w.chunk("LIST", b"INFO"), "LIST INFO"), (w.chunk("JUNK", bytes(4)), "JUNK"),
    (w.chunk("zzzz", b"x"), "zzzz"),
])
def test_any_other_chunk_is_a_leftover(extra, label):
    result = verify(w.wav(extra))
    assert not result.clean
    assert any(f.label.startswith(label + " at offset") for f in result.leftovers)


def test_data_after_the_riff_end_is_a_leftover():
    result = verify(w.wav() + bytes(4))
    assert any("after the RIFF data" in f.value for f in result.leftovers)


def test_non_canonical_cue_smpl_and_fact_chunks_are_leftovers():
    cue = w.cue((1, 0))[8:] + b"xx"
    smpl = w.smpl((1, 0, 10))[8:]  # manufacturer and product are set
    fact = (5).to_bytes(4, "little") + b"more"
    for data in (w.wav(w.chunk("cue ", cue)), w.wav(w.chunk("smpl", smpl)),
                 w.riff(w.fmt(tag=w.FLOAT, bits=32), w.chunk("fact", fact),
                        w.data(bytes(20)))):
        result = verify(data)
        assert not result.clean, data[:60]


def test_two_cue_chunks_and_a_bad_padding_byte():
    assert not verify(w.wav(w.cue((1, 0)), w.cue((2, 0)))).clean
    result = verify(w.riff(w.fmt(bits=8), w.chunk("data", b"\x80", pad=b"\x01")))
    assert any("padding byte is not zero" in f.value for f in result.leftovers)


def test_changed_audio_is_detected():
    original = VARIANTS["pcm16"]
    cleaned = bytearray(strip(original))
    cleaned[-1] ^= 1
    result = verify(bytes(cleaned), original)
    assert result.image_data_match is False
    assert "the fmt or data chunk differs from the original" in result.problems
    assert any("wave module differ" in p for p in result.problems)


def test_changed_format_is_detected():
    original = VARIANTS["pcm16"]
    cleaned = replace_chunk(strip(original), "fmt ", w.fmt(rate=16000)[8:])
    result = verify(cleaned, original)
    assert result.image_data_match is False


def test_the_wave_module_check_alone(monkeypatch):
    """If only the wave module saw a difference, the files still don't match."""
    original = VARIANTS["pcm16"]
    cleaned = strip(original)
    calls = []

    def fake(data):
        calls.append(len(data))
        return (1, 2, 8000, 1), b"x" if len(calls) == 2 else b"y"

    monkeypatch.setattr(wav, "_wave_frames", fake)
    result = verify(cleaned, original)
    assert result.image_data_match is False and len(calls) == 2


def test_formats_the_wave_module_does_not_read_are_noted():
    original = VARIANTS["float"]
    result = verify(strip(original), original)
    assert result.clean and result.image_data_match is True  # not PCM: not compared
    original = VARIANTS["extensible"]
    result = verify(strip(original), original)
    assert result.clean and result.image_data_match is True
    if sys.version_info < (3, 12):  # wave reads WAVE_FORMAT_EXTENSIBLE since 3.12
        assert any("wave module can't read the original" in n for n in result.notes)


def test_malformed_files():
    result = verify(w.wav()[:-10])
    assert any("not a complete, well-formed WAV" in p for p in result.problems)
    result = verify(strip(w.wav()), w.wav()[:-10])
    assert any("original is not a complete, well-formed WAV" in p for p in result.problems)


# -- through the command line -------------------------------------------------------------------


@pytest.mark.parametrize("paranoid", [False, True])
def test_strip_then_verify(tmp_path, paranoid):
    path = tmp_path / "take3.wav"
    path.write_bytes(VARIANTS["kitchen_sink"])
    r = metaex("strip", path, *(["--paranoid"] if paranoid else []))
    assert r.returncode == 0, r.stderr
    assert "Audio (fmt and data chunks) unchanged (SHA-256" in r.stdout
    if paranoid:
        assert "--paranoid removed the cue points (markers) and the sampler loops" in r.stdout
    else:
        assert "cue points and sampler loops are kept" in r.stdout
    v = metaex("verify", tmp_path / "take3_clean.wav", "--original", path, "--json")
    assert v.returncode == 0, v.stdout
    assert json.loads(v.stdout)["image_data_match"] is True


def test_strip_summary_groups(tmp_path):
    path = tmp_path / "take3.wav"
    path.write_bytes(VARIANTS["kitchen_sink"] + b"after the end")
    summary = json.loads(metaex("strip", path, "--json").stdout)
    assert {"RIFF INFO", "Broadcast WAV (bext)", "iXML", "axml", "Cue point labels",
            "Sampler loops", "Hidden data in chunks", "GPS location", "ID3v2 tags",
            "XMP", "Data after the end of the audio"} <= set(summary["removed"])
    assert summary["removed_sizes"]["Data after the end of the audio"] == len(b"after the end")
