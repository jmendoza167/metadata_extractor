"""Stripping FLAC: STREAMINFO and every byte of the frames stay, the metadata goes."""

import pytest

from helpers import audiotags as at
from helpers import flac as f
from helpers import id3 as b
from helpers.jpeg import sample_photo
from metaex.core.errors import MalformedFileError
from metaex.formats import flac
from metaex.formats.flac import (checked_layout, kept_warnings, removed_sizes, strip,
                                 strip_warnings)

PHOTO = sample_photo()
KITCHEN = f.kitchen_sink_flac(PHOTO)


def blocks(data: bytes) -> list[str]:
    return [blk.name for blk in flac.layout(data).blocks]


def test_kitchen_sink():
    cleaned = strip(KITCHEN)
    assert blocks(cleaned) == ["STREAMINFO", "SEEKTABLE", "CUESHEET"]
    original, out = flac.layout(KITCHEN), flac.layout(cleaned)
    assert out.blocks[0].data == original.blocks[0].data  # the MD5 included
    assert out.blocks[1].data == original.blocks[1].data
    assert out.blocks[-1].last and not any(blk.last for blk in out.blocks[:-1])
    assert (cleaned[out.audio_start:] == KITCHEN[original.audio_start:original.audio_end])
    assert cleaned.startswith(b"fLaC")
    for marker in (b"Jane", b"ID3", b"riff", b"hidden", b"example.com", b"libFLAC"):
        assert marker not in cleaned


def test_paranoid_removes_the_cue_sheet():
    assert blocks(strip(KITCHEN, paranoid=True)) == ["STREAMINFO", "SEEKTABLE"]


def test_streaminfo_alone_is_the_last_block():
    cleaned = strip(f.flac((f.VORBIS_COMMENT, f.vorbis_comment(fields=[("ARTIST", "x")]))))
    found = flac.layout(cleaned)
    assert [blk.name for blk in found.blocks] == ["STREAMINFO"] and found.blocks[0].last


def test_cue_sheet_reserved_bytes_are_zeroed():
    data = f.flac((f.CUESHEET, f.cuesheet(reserved=b"\x00secret")))
    cleaned = strip(data)
    cue = flac.layout(cleaned).first(f.CUESHEET)
    assert b"secret" not in cleaned and flac.parse_cuesheet(cue.data).reserved_nonzero == 0
    assert cue.data == f.cuesheet()


def test_tags_around_the_stream_are_removed():
    data = f.flac(before=b.tag(b.text("TPE1", "Jane Doe")) + b.tag(b.text("TIT2", "x")),
                  after=at.ape([at.ape_item("Artist", b"Jane Doe")]) + at.lyrics3_v2()
                  + at.id3v1())
    assert strip(data) == f.flac()


@pytest.mark.parametrize("trailing, key", [(bytes(10), "audio_padding"),
                                           (b"secret", "audio_trailing")])
def test_data_after_the_last_frame_is_removed(trailing, key):
    data = f.flac(after=trailing + at.id3v1())
    assert strip(data) == f.flac()
    assert removed_sizes(data) == {key: len(trailing)}


@pytest.mark.parametrize("channels, options", [
    ([f.sine(5000)], dict(kind="lpc", coefs=(1900, -950), precision=13, shift=10)),
    ([f.sine(5000), f.sine(5000, freq=550.0)], dict(kind="fixed", order=2, assignment=10)),
    ([f.sine(5000, bits=24)], dict(kind="fixed", order=3, bits=24)),
])
def test_every_kind_of_stream(channels, options):
    info, frames = f.encode(channels, **options)
    data = f.flac((f.PADDING, bytes(100)), info=info, frames=frames)
    assert strip(data) == f.flac(info=info, frames=frames)


def test_stripping_is_stable():
    once = strip(KITCHEN)
    assert strip(once) == once


def test_warnings():
    assert kept_warnings(strip(KITCHEN))[0].startswith("the cue sheet is kept")
    assert kept_warnings(strip(KITCHEN, paranoid=True)) == []
    assert strip_warnings(KITCHEN, paranoid=True) == [
        "--paranoid removed the cue sheet (track positions)"]
    assert strip_warnings(KITCHEN) == [] and strip_warnings(f.flac(), paranoid=True) == []


# -- refused ---------------------------------------------------------------------------------


@pytest.mark.parametrize("data, message", [
    (f.flac()[:-100], "the file may be cut off"),
    (f.flac((127, b"x")), "invalid type 127"),
    (f.flac((f.SEEKTABLE, bytes(20))), "SEEKTABLE length is not a multiple of 18"),
    (f.flac((f.SEEKTABLE, bytes(18)), (f.SEEKTABLE, bytes(18))), "more than one SEEKTABLE"),
    (f.flac((f.CUESHEET, f.cuesheet()), (f.CUESHEET, f.cuesheet())), "more than one CUESHEET"),
    (f.flac((f.CUESHEET, f.cuesheet()[:420])), "CUESHEET block is truncated"),
    (f.flac((f.STREAMINFO, f.streaminfo())), "more than one STREAMINFO"),
    (f.flac(before=b.tag(b.frame("TPE1", b"\x03ab", 4, flags=0x0009))), "is malformed"),
])
def test_malformed_input_is_refused(data, message):
    with pytest.raises(MalformedFileError, match=message):
        strip(data)


def test_a_bad_crc_in_the_last_frame_is_refused():
    data = bytearray(f.flac())
    data[-3] ^= 1  # inside the last frame
    with pytest.raises(MalformedFileError):
        strip(bytes(data))


def test_the_self_check(monkeypatch):
    real = flac.audio_digest
    calls = []

    def digest(data, found):
        calls.append(1)
        return real(data, found) + ("x" if len(calls) == 1 else "")

    monkeypatch.setattr(flac, "audio_digest", digest)
    with pytest.raises(MalformedFileError, match="internal check failed"):
        strip(KITCHEN)


def test_checked_layout():
    found = checked_layout(KITCHEN)
    assert found.audio_end == len(KITCHEN)
