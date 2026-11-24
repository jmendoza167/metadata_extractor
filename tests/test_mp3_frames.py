"""Walking MPEG audio frames: headers, resynchronization, junk, information frames."""

import random

import pytest

from helpers import mp3
from metaex.core.errors import MalformedFileError, MetaexError, UnsupportedFormatError
from metaex.formats import mp3 as mp3_format
from metaex.formats.mp3 import (FrameWalk, audio_section, clean_info_frame, frame_length,
                                info_frame, info_section, is_free_format, junk_sections,
                                parse_header, walk_frames)

ALL_KINDS = [(v, layer, rate, br)
             for v in (1, 2, 25) for layer in (1, 2, 3)
             for rate in mp3.SAMPLE_RATES[v]
             for br in mp3.BITRATES[(1 if v == 1 else 2, layer)][1:]]


def walk(data: bytes) -> FrameWalk:
    return walk_frames(data, 0, len(data))


# -- headers --------------------------------------------------------------------------------


@pytest.mark.parametrize("padding", [False, True])
def test_frame_lengths_of_every_version_layer_rate_and_bitrate(padding):
    for version, layer, rate, bitrate in ALL_KINDS:
        kw = dict(version=version, layer=layer, sample_rate=rate, bitrate=bitrate,
                  padding=padding)
        head = mp3.header(**kw)
        assert frame_length(head, 0) == mp3.frame_size(**kw), kw
        h = parse_header(head, 0)
        assert (h.sample_rate, h.bitrate, h.layer_number, h.padding) == (
            rate, bitrate * 1000, layer, padding)
        assert h.samples == mp3.samples_per_frame(version, layer)


def test_header_fields():
    h = parse_header(mp3.header(mode=mp3.MONO, protected=True, private=True, copyright=True,
                                original=False, emphasis=1), 0)
    assert (h.version_name, h.layer_name, h.channels, h.protected, h.private, h.copyright,
            h.original, h.emphasis) == ("MPEG-1", "Layer III", 1, True, True, True, False, 1)
    assert h.side_info_size == 17


@pytest.mark.parametrize("bad", [
    b"\xff\xeb\x90\x44",  # version bits 01 (reserved)
    b"\xff\xf9\x90\x44",  # layer bits 00 (reserved)
    b"\xff\xfb\xf0\x44",  # bitrate index 15
    b"\xff\xfb\x9c\x44",  # sample rate index 3
    b"\xff\xfb\x90\x46",  # emphasis 2 (reserved)
    b"\xfe\xfb\x90\x44",  # no sync
    b"\xff\xfb\x90",      # too short
])
def test_invalid_headers(bad):
    assert frame_length(bad, 0) == 0 and parse_header(bad, 0) is None


def test_free_format_header():
    head = mp3.header(bitrate_index=0)
    assert frame_length(head, 0) == 0 and is_free_format(head, 0)
    assert not is_free_format(mp3.header(), 0)


# -- walking --------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [(1, 3, 44100, 128), (1, 2, 48000, 192), (1, 1, 32000, 384),
                                  (2, 3, 22050, 64), (2, 2, 16000, 8), (25, 3, 8000, 8),
                                  (25, 1, 11025, 32)])
def test_streams_of_every_kind(kind):
    version, layer, rate, bitrate = kind
    data = mp3.frames(30, version=version, layer=layer, sample_rate=rate, bitrate=bitrate)
    w = walk(data)
    assert w.frames == w.audio_frames == 30 and w.runs == [(0, len(data))] and w.junk == []
    assert w.info is None
    assert w.duration == pytest.approx(30 * mp3.samples_per_frame(version, layer) / rate)
    assert w.bitrates == {bitrate * 1000: 30}


def test_info_frame_with_lame_tag():
    data = mp3.stream(20)
    w = walk(data)
    assert w.frames == 21 and w.audio_frames == 20
    info = w.info
    assert (info.kind, info.frames, info.bytes, info.toc, info.offset) == (
        "Info", 21, len(data), True, 0)
    lame = info.lame
    assert (lame.encoder, lame.encoder_delay, lame.encoder_padding, lame.crc_ok) == (
        "LAME3.100", 576, 1152, True)
    assert info.hidden_bytes(data) == 0
    fields = {f.key: f.value for f in info_section(info).fields}
    assert fields["encoder"] == "LAME3.100" and fields["tag_crc"] == "valid"


def test_mono_and_mpeg2_info_frames():
    for kw in (dict(mode=mp3.MONO), dict(version=2, sample_rate=24000, bitrate=64),
               dict(version=2, sample_rate=24000, bitrate=64, mode=mp3.MONO)):
        w = walk(mp3.stream(5, **kw))
        assert w.info is not None and w.info.lame.crc_ok, kw


def test_xing_frame_of_a_variable_bitrate_stream():
    audio = mp3.frames(5, bitrate=128) + mp3.frames(5, bitrate=192) + mp3.frames(2, bitrate=64)
    data = mp3.xing_frame(13, 0, tag=b"Xing") + audio
    w = walk(data)
    assert w.info.kind == "Xing" and w.audio_frames == 12
    assert w.bitrates == {128000: 5, 192000: 5, 64000: 2}
    assert "variable, 64 to 192" in {f.key: f.value for f in audio_section(w).fields}["bitrate"]


def test_vbri_frame():
    data = mp3.vbri_frame(11, 5000) + mp3.frames(10)
    info = walk(data).info
    assert (info.kind, info.frames, info.bytes, info.toc, info.vbri_delay) == (
        "VBRI", 11, 5000, True, 1105)


def test_a_broken_lame_crc_is_reported():
    data = bytearray(mp3.stream(3))
    data[mp3.side_info_size() + 4 + 8 + 4 + 4 + 100 + 4 + 10] ^= 0xFF  # the lowpass byte
    info = walk(bytes(data)).info
    assert info.lame is not None and info.lame.crc_ok is False


def test_hidden_bytes_in_the_info_frame():
    data = mp3.xing_frame(3, 0, extra=b"secret") + mp3.frames(2)
    w = walk(data)
    assert w.info.hidden_bytes(data) == 6
    warnings = []
    sections = junk_sections(w, data, warnings)
    assert [s.key for s in sections] == ["hidden_audio_data"]
    assert warnings[0].startswith("CRITICAL: 6 bytes")
    cleaned = clean_info_frame(data, w.info)
    assert b"secret" not in cleaned and len(cleaned) == w.info.length
    assert info_frame(cleaned + mp3.frames(2), 0).lame.crc_ok  # the tag is untouched


def test_side_information_of_the_info_frame_must_be_zero():
    data = bytearray(mp3.stream(2))
    data[10] = 0x55
    w = walk(bytes(data))
    assert w.info.hidden_bytes(bytes(data)) == 1


def test_protected_frames():
    data = mp3.frames(10, protected=True)
    assert walk(data).frames == 10


# -- junk and resynchronization ---------------------------------------------------------------


def test_junk_between_frames():
    a, b = mp3.frames(5), mp3.frames(5)
    data = a + b"hidden message" + b
    w = walk(data)
    assert w.runs == [(0, len(a)), (len(a) + 14, len(data))]
    assert w.junk == [(len(a), len(a) + 14)] and w.frames == 10
    warnings = []
    sections = junk_sections(w, data, warnings)
    assert sections[0].key == "hidden_audio_data"
    assert sections[0].fields[0].note == "between frames"


def test_junk_before_the_first_frame_and_after_the_last():
    audio = mp3.frames(4)
    data = b"\x00" * 100 + audio + b"trailing secret"
    w = walk(data)
    assert w.junk == [(0, 100), (100 + len(audio), len(data))]
    warnings = []
    keys = [s.key for s in junk_sections(w, data, warnings)]
    assert keys == ["hidden_audio_data", "audio_padding"]
    assert any("CRITICAL" in x for x in warnings) and any("zero padding" in x for x in warnings)


def test_a_fake_header_inside_junk_is_not_a_frame():
    audio = mp3.frames(3)
    fake = mp3.header() + b"junk"  # a valid header not followed by another frame
    data = audio + fake + bytes(50) + audio
    w = walk(data)
    assert w.frames == 6 and w.junk == [(len(audio), len(audio) + len(fake) + 50)]


def test_a_frame_of_another_stream_is_junk():
    audio = mp3.frames(3)
    other = mp3.frames(1, sample_rate=48000)
    data = audio + other + audio
    w = walk(data)
    assert w.frames == 6 and w.junk == [(len(audio), len(audio) + len(other))]


def test_a_truncated_last_frame_is_refused():
    data = mp3.frames(5)
    with pytest.raises(MalformedFileError, match="is truncated: needs 418 bytes, 100 available"):
        walk(data[:-318])


def test_one_frame_that_ends_the_data():
    assert walk(mp3.frame()).frames == 1


def test_no_frames():
    for data in (b"", b"\x00" * 1000, b"not audio at all", mp3.header() + bytes(10)):
        with pytest.raises(MalformedFileError, match="no MPEG audio frames"):
            walk(data)


def test_free_format_is_refused():
    data = (mp3.header(bitrate_index=0) + bytes(400)) * 3
    with pytest.raises(UnsupportedFormatError, match="free-format"):
        walk(data)


def test_a_switch_to_free_format_is_refused():
    data = mp3.frames(3) + (mp3.header(bitrate_index=0) + bytes(400)) * 3
    with pytest.raises(UnsupportedFormatError, match="free-format MPEG audio at offset"):
        walk(data)


def test_aac_adts_is_refused():
    adts = bytes([0xFF, 0xF1, 0x50, 0x80, 0x02, 0x1F, 0xFC]) + bytes(10)
    with pytest.raises(UnsupportedFormatError, match="AAC"):
        walk(adts * 4)


def test_too_many_gaps(monkeypatch):
    monkeypatch.setattr(mp3_format, "MAX_JUNK_REGIONS", 5)
    data = (mp3.frames(2) + b"x") * 10
    with pytest.raises(MalformedFileError, match="more than 5 gaps"):
        walk(data)


def test_too_many_false_headers(monkeypatch):
    monkeypatch.setattr(mp3_format, "MAX_SYNC_CANDIDATES", 100)
    data = mp3.frames(2) + (mp3.header() + b"x") * 200 + mp3.frames(2)
    with pytest.raises(MalformedFileError, match="false MPEG frame headers"):
        walk(data)


def test_a_long_run_of_ff_bytes_is_fast():
    data = mp3.frames(3) + b"\xff" * 2_000_000 + mp3.frames(3)
    w = walk(data)
    assert w.frames == 6 and w.candidates == 0


def test_the_walk_stays_inside_its_range():
    tag = b"ID3-like prefix " * 4
    audio = mp3.frames(5)
    data = tag + audio + tag
    w = walk_frames(data, len(tag), len(tag) + len(audio))
    assert w.runs == [(len(tag), len(tag) + len(audio))] and w.junk == []
    with pytest.raises(ValueError):
        walk_frames(data, 10, 5)


def test_random_mutations_never_crash():
    rng = random.Random(1234)
    base = mp3.stream(10)
    for _ in range(400):
        data = bytearray(base)
        for _ in range(rng.randint(1, 8)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        if rng.random() < 0.3:
            del data[rng.randrange(len(data)):]
        try:
            w = walk(bytes(data))
        except MetaexError:
            continue
        covered = sum(b - a for a, b in w.runs) + sum(b - a for a, b in w.junk)
        assert covered == len(data)
        junk_sections(w, bytes(data), [])
        audio_section(w)
