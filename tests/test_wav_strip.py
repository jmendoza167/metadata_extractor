"""Stripping WAV: fmt and data stay byte for byte, the metadata goes."""

import pytest

from helpers import id3 as b
from helpers import wav as w
from metaex.core.errors import HiddenDataError, MalformedFileError
from metaex.formats import wav
from metaex.formats.wav import (checked_structure, kept_warnings, removed_sizes, strip,
                                strip_warnings, walk_chunks)

VARIANTS = w.variants()


def fourccs(data: bytes) -> list[str]:
    return [c.fourcc for c in walk_chunks(data).chunks]


def test_kitchen_sink():
    original = VARIANTS["kitchen_sink"]
    cleaned = strip(original)
    assert fourccs(cleaned) == ["fmt ", "data", "cue ", "smpl"]
    s, o = walk_chunks(cleaned), walk_chunks(original)
    assert s.first("fmt ").data == o.first("fmt ").data
    assert s.first("data").data == o.first("data").data
    assert s.end == len(cleaned) and s.riff_size == len(cleaned) - 8
    for marker in (b"Jane", b"ZOOM", b"example.com", b"LIST", b"bext", b"iXML", b"JUNK",
                   b"vendor", b"PEAK"):
        assert marker not in cleaned


def test_paranoid_removes_cue_points_and_loops():
    cleaned = strip(VARIANTS["kitchen_sink"], paranoid=True)
    assert fourccs(cleaned) == ["fmt ", "data"]


def test_cue_points_are_rebuilt_without_extra_bytes():
    raw = bytearray(w.cue((1, 0), (2, 100)))
    raw[4:8] = (len(raw) - 8 + 6).to_bytes(4, "little")
    data = w.wav(bytes(raw) + b"secret")
    cleaned = strip(data)
    cue = walk_chunks(cleaned).first("cue ")
    assert cue.data == w.cue((1, 0), (2, 100))[8:]
    assert b"secret" not in cleaned


def test_sampler_identity_and_vendor_data_are_removed():
    cleaned = strip(w.wav(w.smpl((1, 0, 100), (2, 200, 300), sampler_data=b"vendor")))
    s = wav.parse_smpl(walk_chunks(cleaned).first("smpl"))
    assert (s.manufacturer, s.product, s.sampler_data) == (0, 0, b"")
    assert len(s.loops) == 2 and s.sample_period == 125000


def test_an_empty_cue_chunk_is_dropped():
    assert fourccs(strip(w.wav(w.chunk("cue ", bytes(4))))) == ["fmt ", "data"]


def test_fact_keeps_its_sample_count():
    data = w.riff(w.fmt(tag=w.FLOAT, bits=32), w.chunk("fact", (500).to_bytes(4, "little")
                                                       + b"more"), w.data(bytes(2000)))
    fact = walk_chunks(strip(data)).first("fact")
    assert fact.data == (500).to_bytes(4, "little")


def test_odd_data_gets_a_zero_padding_byte():
    data = w.riff(w.fmt(bits=8), w.chunk("data", b"\x80\x81\x82", pad=b"\x07"))
    cleaned = strip(data)
    assert cleaned.endswith(b"\x80\x81\x82\x00")
    assert walk_chunks(cleaned).first("data").padding == b"\x00"


def test_data_before_other_chunks_keeps_its_order():
    assert fourccs(strip(VARIANTS["data_first"])) == ["fmt ", "data"]


@pytest.mark.parametrize("trailing, key", [(bytes(10), "audio_padding"),
                                           (b"secret", "audio_trailing")])
def test_data_after_the_riff_end_is_removed(trailing, key):
    data = w.wav() + trailing
    assert strip(data) == w.wav()
    assert removed_sizes(data) == {key: len(trailing)}


def test_extensible_and_float_formats():
    for name in ("extensible", "float", "pcm24_stereo", "pcm8_odd", "pcm_fmt18"):
        original = VARIANTS[name]
        cleaned = strip(original)
        assert walk_chunks(cleaned).first("fmt ").data == walk_chunks(original).first("fmt ").data


def test_stripping_is_stable():
    once = strip(VARIANTS["kitchen_sink"])
    assert strip(once) == once


def test_warnings():
    cleaned = strip(VARIANTS["kitchen_sink"])
    assert kept_warnings(cleaned) == ["cue points and sampler loops are kept (positions only, "
                                      "without labels); use --paranoid to remove them"]
    assert kept_warnings(strip(VARIANTS["kitchen_sink"], paranoid=True)) == []
    assert strip_warnings(VARIANTS["kitchen_sink"], paranoid=True) == [
        "--paranoid removed the cue points (markers) and the sampler loops"]
    assert strip_warnings(VARIANTS["pcm16"], paranoid=True) == []


# -- refused ---------------------------------------------------------------------------------


@pytest.mark.parametrize("data, message", [
    (w.wav()[:-100], "the file is truncated"),
    (w.riff(w.fmt()), "0 data chunks"),
    (w.riff(w.data(b"\x00\x00")), "0 fmt and 1 data"),
    (w.riff(w.fmt(), w.data(b"\x00\x00"), w.data(b"\x00\x00")), "2 data chunks"),
    (w.riff(w.data(b"\x00\x00"), w.fmt()), "comes after the data chunk"),
    (w.riff(w.chunk("fmt ", bytes(10)), w.data(b"\x00\x00")), "fmt chunk is too short"),
    (w.wav(w.chunk("cue ", (5).to_bytes(4, "little"))), "cue chunk says"),
    (w.wav(w.chunk("smpl", bytes(10))), "smpl chunk is too short"),
    (w.riff(w.fmt(tag=w.FLOAT, bits=32), w.chunk("fact", b"x"), w.data(bytes(4))),
     "fact chunk is too short"),
])
def test_malformed_input_is_refused(data, message):
    with pytest.raises(MalformedFileError, match=message):
        strip(data)


def test_data_hidden_in_the_fmt_chunk_is_refused():
    data = w.riff(w.fmt(cb_size=0, extra=b"secret"), w.data(w.sine(10)))
    with pytest.raises(HiddenDataError, match="6 bytes after the format description"):
        strip(data)
    # zeros there are kept as they are
    zeros = w.riff(w.fmt(cb_size=0, extra=bytes(4)), w.data(w.sine(10)))
    assert walk_chunks(strip(zeros)).first("fmt ").size == 22


def test_rf64_is_refused():
    with pytest.raises(MalformedFileError, match="RF64 files"):
        strip(b"RF64" + bytes(4) + b"WAVE" + w.fmt() + w.data(b"\x00\x00"))


def test_checked_structure_is_what_strip_uses():
    with pytest.raises(MalformedFileError):
        checked_structure(w.wav()[:-1])


def test_id3_chunk_is_removed_with_its_picture():
    data = w.wav(w.chunk("id3 ", b.tag(b.text("TPE1", "Jane Doe"))))
    assert fourccs(strip(data)) == ["fmt ", "data"]
