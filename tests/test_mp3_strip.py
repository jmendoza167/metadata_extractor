"""Stripping MP3: every tag goes, the audio frames stay byte for byte."""

import random

import pytest

from helpers import audiotags as at
from helpers import id3 as b
from helpers import mp3
from helpers.jpeg import sample_photo
from metaex.core.errors import MalformedFileError, MetaexError, UnsupportedFormatError
from metaex.formats import mp3 as mp3_format
from metaex.formats.mp3 import checked_layout, kept_warnings, removed_sizes, strip, strip_warnings

PHOTO = sample_photo()
AUDIO = mp3.stream(10)  # an Info frame with a LAME tag, then 10 frames
INFO_LENGTH = mp3.frame_size()


def every_tag(audio: bytes = AUDIO) -> bytes:
    return (b.tag(*b.kitchen_sink_frames(PHOTO), version=3, padding=64)
            + b.tag(b.text("TIT2", "Second"), version=4)
            + audio
            + b.tag(b.text("TIT2", "Appended"), footer=True)
            + at.ape_kitchen_sink(PHOTO) + at.lyrics3_v2() + at.tag_plus() + at.id3v1())


def test_every_tag_is_removed():
    cleaned = strip(every_tag())
    assert cleaned == AUDIO
    for marker in (b"ID3", b"TAG", b"APETAGEX", b"LYRICS", b"3DI", b"Jane", b"example.com"):
        assert marker not in cleaned


def test_paranoid_removes_the_information_frame():
    cleaned = strip(every_tag(), paranoid=True)
    assert cleaned == AUDIO[INFO_LENGTH:]
    assert b"LAME" not in cleaned and b"Info" not in cleaned
    _found, walk = checked_layout(cleaned)
    assert walk.info is None and walk.audio_frames == 10


def test_without_an_information_frame():
    audio = mp3.frames(8)
    assert strip(b.tag(b.text("TPE1", "Jane Doe")) + audio) == audio
    assert strip(audio, paranoid=True) == audio


def test_vbri_frame_is_kept_and_removed_with_paranoid():
    audio = mp3.vbri_frame(9, 4000) + mp3.frames(8)
    assert strip(audio) == audio
    assert strip(audio, paranoid=True) == audio[mp3.frame_size():]


def test_junk_between_and_around_the_frames_is_removed():
    a, c = mp3.frames(4), mp3.frames(3)
    data = b.tag(b.text("TPE1", "x")) + bytes(20) + a + b"hidden message" + c + b"trailing"
    assert strip(data) == a + c
    sizes = removed_sizes(data)
    assert sizes == {"audio_padding": 20, "hidden_audio_data": 14 + 8}


def test_data_hidden_in_the_information_frame_is_zeroed():
    info = mp3.xing_frame(4, 0, extra=b"secret")
    data = info + mp3.frames(3)
    cleaned = strip(data)
    assert b"secret" not in cleaned and len(cleaned) == len(data)
    _found, walk = checked_layout(cleaned)
    assert walk.info.lame.crc_ok and walk.info.hidden_bytes(cleaned) == 0
    assert cleaned[INFO_LENGTH:] == data[INFO_LENGTH:]
    assert removed_sizes(data) == {"hidden_audio_data": 6}


@pytest.mark.parametrize("kind", [dict(layer=1, sample_rate=48000, bitrate=384),
                                  dict(layer=2, bitrate=192), dict(version=2, sample_rate=22050,
                                                                   bitrate=64),
                                  dict(version=25, sample_rate=8000, bitrate=8, mode=mp3.MONO),
                                  dict(protected=True)])
def test_every_kind_of_stream(kind):
    audio = mp3.frames(6, **kind)
    assert strip(b.tag(b.text("TPE1", "Jane Doe")) + audio + at.id3v1()) == audio


def test_stripping_is_stable():
    once = strip(every_tag())
    assert strip(once) == once
    assert strip(strip(every_tag(), paranoid=True), paranoid=True) == AUDIO[INFO_LENGTH:]


def test_warnings():
    assert kept_warnings(strip(every_tag())) == [
        "the Info frame with the LAME tag (encoder 'LAME3.100') is kept for seeking and "
        "gapless playback; use --paranoid to remove it"]
    assert kept_warnings(strip(every_tag(), paranoid=True)) == []
    assert strip_warnings(every_tag()) == []
    assert "less accurate duration" in strip_warnings(every_tag(), paranoid=True)[0]
    assert strip_warnings(mp3.frames(3), paranoid=True) == []


# -- refused ----------------------------------------------------------------------------------


@pytest.mark.parametrize("data, error, message", [
    (b.tag(b.text("TPE1", "x")) + AUDIO[:-100], MalformedFileError, "is truncated"),
    (b.tag(b.text("TPE1", "x"), padding=5000)[:300], MalformedFileError, "is truncated"),
    (b.tag(b.frame("TPE1", b"\x03ab", 4, flags=0x0009)) + AUDIO, MalformedFileError,
     "too short for its flags"),
    (AUDIO + at.ape([at.ape_item("Artist", b"x")])[:-20] + b"0" * 20, MalformedFileError,
     "APE"),
    (AUDIO + b"LYRICSBEGININD00009xx000021LYRICS200", MalformedFileError, "truncated"),
    (b"\x00" * 1000, MalformedFileError, "no MPEG audio frames"),
    ((mp3.header(bitrate_index=0) + bytes(400)) * 3, UnsupportedFormatError, "free-format"),
    (b.tag(b.text("TIT2", "x")) + (bytes([0xFF, 0xF1, 0x50, 0x80, 0x02, 0x1F, 0xFC])
                                   + bytes(10)) * 4, UnsupportedFormatError, "AAC"),
])
def test_malformed_input_is_refused(data, error, message):
    with pytest.raises(error, match=message):
        strip(data)


def test_a_tag_with_unreadable_frames_is_refused():
    raw = bytearray(b.text("TIT2", "x"))
    raw[4:8] = b.synchsafe(5000)
    with pytest.raises(MalformedFileError, match="the ID3v2.4 tag at offset 0 is malformed"):
        strip(b.tag(bytes(raw)) + AUDIO)


def test_encrypted_frames_that_can_be_skipped_are_removed_with_the_tag():
    data = b.tag(b.encrypted_frame("TPE1", b"\x8a\x91secret")) + AUDIO
    assert strip(data) == AUDIO


def test_the_self_check(monkeypatch):
    real = mp3_format.audio_frames
    monkeypatch.setattr(mp3_format, "audio_frames",
                        lambda data, walk, keep_info=True: real(data, walk)[:-INFO_LENGTH])
    with pytest.raises(MalformedFileError, match="internal check failed"):
        strip(b"junk" + mp3.frames(5))


def test_random_mutations_strip_completely_or_not_at_all():
    rng = random.Random(99)
    base = every_tag(AUDIO + b"junk" + mp3.frames(2))
    for _ in range(150):
        data = bytearray(base)
        for _ in range(rng.randint(1, 5)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        try:
            cleaned = strip(bytes(data))
        except MetaexError:
            continue
        found, walk = checked_layout(cleaned)
        assert found.tag_ranges() == [] and walk.junk == []
        _orig_found, orig_walk = checked_layout(bytes(data))
        assert walk.audio_frames == orig_walk.audio_frames
