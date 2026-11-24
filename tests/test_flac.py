"""FLAC: metadata blocks, frames, and the tags around the stream."""

import base64
import json
import subprocess
import sys

import pytest

from helpers import audiotags as at
from helpers import flac as f
from helpers import id3 as b
from helpers.jpeg import sample_photo
from metaex.core import filetype as ft
from metaex.core.errors import MalformedFileError
from metaex.core.risk import CRITICAL, HIGH, assess
from metaex.formats import flac

PHOTO = sample_photo()
KITCHEN = f.kitchen_sink_flac(PHOTO)


def values(data: bytes) -> dict:
    parsed = flac.parse(data)
    return {(s.key, fl.key): fl.value for s in parsed["sections"] for fl in s.fields}


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


# -- detection and layout ---------------------------------------------------------------------


def test_detection_with_tags_before_the_stream():
    big = b.tag(b.apic(PHOTO), padding=10_000)  # far larger than the first 4 KiB
    for before in (b"", b.tag(b.text("TPE1", "x")), big, big + b.tag(b.text("TIT2", "y"))):
        assert ft.detect(f.flac(before=before)) == ft.FLAC
    assert ft.detect(big + b"\xff\xfb\x90\x44" + bytes(413) * 2) == ft.MP3


def test_kitchen_sink_layout():
    found = flac.layout(KITCHEN)
    assert found.error is None and len(found.leading) == 1
    assert [blk.name for blk in found.blocks] == ["STREAMINFO", "SEEKTABLE", "VORBIS_COMMENT",
                                                  "PICTURE", "APPLICATION", "CUESHEET",
                                                  "PADDING"]
    assert found.blocks[-1].last and not any(blk.last for blk in found.blocks[:-1])
    assert found.audio_end == len(KITCHEN) and found.last_frame.number == 1


def test_kitchen_sink_sections():
    parsed = flac.parse(KITCHEN)
    assert parsed["complete"], parsed["warnings"]
    keys = [s.key for s in parsed["sections"]]
    assert keys[:7] == ["flac_streaminfo", "id3v2", "vorbis_comment", "flac_application",
                        "flac_seektable", "flac_cuesheet", "audio_pictures"]
    assert {"gps", "hidden_block_data"} <= set(keys)
    v = values(KITCHEN)
    assert v[("flac_streaminfo", "sample_rate")] == "8000 Hz"
    assert v[("flac_streaminfo", "channels")] == 2 and v[("flac_streaminfo", "samples")] == 8000
    assert v[("vorbis_comment", "VENDOR")] == "reference libFLAC 1.4.3 20230623"
    assert v[("vorbis_comment", "ARTIST")] == "Jane Doe"
    assert v[("flac_application", "riff@" + str(flac.layout(KITCHEN).all(2)[0].offset))] == (
        "24 bytes")
    assert v[("flac_seektable", "points")] == 1 and v[("flac_seektable", "placeholders")] == 1
    assert v[("flac_cuesheet", "catalog")] == "1234567890123"
    assert v[("flac_cuesheet", "track_1")] == "sample 0, ISRC FRXXX2400001"
    assert v[("id3v2", "TPE1")] == "Jane Doe"


def test_kitchen_sink_risk():
    findings = assess(flac.parse(KITCHEN)["sections"])
    levels = {(x.section_key, x.field_key): x.level for x in findings}
    assert levels[("vorbis_comment", "CONTACT")] == CRITICAL
    assert levels[("vorbis_comment", "ARTIST")] == HIGH
    assert any(x.section_key == "gps" and x.level == CRITICAL for x in findings)
    assert any(x.section_key == "hidden_block_data" and x.level == CRITICAL for x in findings)


def test_tags_at_the_end():
    data = f.flac(after=at.ape([at.ape_item("Artist", b"Jane Doe")]) + at.id3v1())
    parsed = flac.parse(data)
    assert parsed["complete"]
    assert {"ape", "id3v1"} <= {s.key for s in parsed["sections"]}
    found = flac.layout(data)
    assert found.audio_end == found.data_end < len(data)


def test_appended_id3v2_at_the_end():
    data = f.flac(after=b.tag(b.text("TPE1", "Jane Doe"), footer=True))
    titles = [s.title for s in flac.parse(data)["sections"] if s.key == "id3v2"]
    assert titles == ["ID3v2.4 tag (appended at the end)"]


# -- blocks -----------------------------------------------------------------------------------


def test_vorbis_picture_field():
    encoded = base64.b64encode(f.picture(PHOTO)).decode("ascii")
    data = f.flac((f.VORBIS_COMMENT, f.vorbis_comment(fields=[("METADATA_BLOCK_PICTURE",
                                                                encoded)])))
    keys = [s.key for s in flac.parse(data)["sections"]]
    assert "audio_pictures" in keys and "gps" in keys
    broken = f.flac((f.VORBIS_COMMENT, f.vorbis_comment(fields=[("METADATA_BLOCK_PICTURE",
                                                                 "!!not base64")])))
    assert any("can't be decoded" in w for w in flac.parse(broken)["warnings"])


def test_vorbis_names_are_upper_case_and_values_utf8():
    data = f.flac((f.VORBIS_COMMENT, f.vorbis_comment(fields=[("artist", "Zoë"),
                                                               ("Album", "a=b")])))
    v = values(data)
    assert v[("vorbis_comment", "ARTIST")] == "Zoë" and v[("vorbis_comment", "ALBUM")] == "a=b"


@pytest.mark.parametrize("payload", [b"\xff\xff\xff\x00", b"\x04\x00\x00\x00abcd\xff\xff\xff\xff",
                                     b"\x00\x00\x00\x00\x01\x00\x00\x00\x50\x00\x00\x00"])
def test_broken_vorbis_comments(payload):
    parsed = flac.parse(f.flac((f.VORBIS_COMMENT, payload)))
    assert not parsed["complete"]
    assert any("VORBIS_COMMENT" in w for w in parsed["warnings"])


def test_many_vorbis_fields_are_capped(monkeypatch):
    monkeypatch.setattr(flac, "MAX_COMMENTS", 5)
    parsed = flac.parse(f.flac((f.VORBIS_COMMENT, f.vorbis_comment(fields=[("A", "b")] * 6))))
    assert any("more than 5 fields" in w for w in parsed["warnings"])


def test_broken_picture_block():
    parsed = flac.parse(f.flac((f.PICTURE, f.picture(PHOTO)[:30])))
    assert not parsed["complete"] and any("PICTURE block is truncated" in w
                                          for w in parsed["warnings"])


def test_padding_blocks():
    zero = values(f.flac((f.PADDING, bytes(100))))
    assert any(k[0] == "audio_padding" for k in zero)
    parsed = flac.parse(f.flac((f.PADDING, bytes(50) + b"x")))
    assert any(w.startswith("CRITICAL: 1 bytes of data hidden in FLAC metadata blocks")
               for w in parsed["warnings"])


def test_cuesheet_reserved_bytes_are_hidden_data():
    parsed = flac.parse(f.flac((f.CUESHEET, f.cuesheet(reserved=b"\x00secret"))))
    hidden = [fl for s in parsed["sections"] if s.key == "hidden_block_data" for fl in s.fields]
    assert [fl.label for fl in hidden] == ["Reserved bytes of the CUESHEET"]


def test_broken_cuesheet():
    parsed = flac.parse(f.flac((f.CUESHEET, f.cuesheet()[:420])))
    assert any("CUESHEET block is truncated" in w for w in parsed["warnings"])


def test_reserved_block_types_are_unknown():
    v = values(f.flac((9, b"mystery")))
    assert any(k[0] == "unknown" and v[k] == f"7 bytes at offset {42}" for k in v)


def test_seektable_with_a_bad_length():
    parsed = flac.parse(f.flac((f.SEEKTABLE, bytes(20))))
    assert any("SEEKTABLE length is not a multiple of 18" in w for w in parsed["warnings"])


# -- broken structure ---------------------------------------------------------------------------


def broken(data: bytes) -> str:
    found = flac.layout(data)
    assert found.error is not None
    parsed = flac.parse(data)
    assert not parsed["complete"]
    return found.error


def test_invalid_block_type():
    assert "invalid type 127" in broken(f.flac((127, b"x")))


def test_block_length_past_the_end():
    data = bytearray(f.flac((f.PADDING, bytes(10))))
    data[42 + 1:42 + 4] = (10**6).to_bytes(3, "big")
    assert "metadata block at offset 42 is truncated" in broken(bytes(data))


def test_missing_last_block_flag():
    data = bytearray(f.flac((f.PADDING, bytes(10))))
    data[42] = f.PADDING  # the last block no longer says it is the last
    error = broken(bytes(data))
    assert "metadata block" in error or "frame" in error


def test_last_block_flag_too_early():
    data = bytearray(f.flac((f.PADDING, bytes(10))))
    data[4] |= 0x80  # STREAMINFO says it is the last block
    assert "no valid audio frame after the metadata blocks" in broken(bytes(data))


def test_streaminfo_first_and_34_bytes():
    assert "first metadata block is not STREAMINFO" in broken(
        b"fLaC" + f.block(f.PADDING, bytes(4), last=True))
    assert "STREAMINFO block has 30 bytes" in broken(
        b"fLaC" + f.block(f.STREAMINFO, bytes(30), last=True))
    assert "invalid block sizes" in broken(b"fLaC" + f.block(f.STREAMINFO, bytes(34), last=True))


def test_not_flac():
    with pytest.raises(MalformedFileError, match="no fLaC marker after the ID3v2 tags"):
        flac.parse(b.tag(b.text("TPE1", "x")) + b"OggS")


def test_bad_first_frame_crc():
    data = bytearray(f.flac())
    start = flac.layout(bytes(data)).audio_start
    data[start + 20] ^= 1  # inside the first frame: its CRC-16 no longer matches
    assert "bad CRC-16" in broken(bytes(data))


def test_cut_off_stream():
    data = f.flac()
    assert "the last frame ends at sample 4096" in broken(data[:-100])


def test_trailing_data_after_the_last_frame():
    good = f.flac()
    parsed = flac.parse(good + b"secret")
    assert parsed["complete"]
    assert any(w.startswith("CRITICAL: 6 bytes after the last audio frame")
               for w in parsed["warnings"])
    assert "audio_trailing" in {s.key for s in parsed["sections"]}
    assert "audio_padding" in {s.key for s in flac.parse(good + bytes(8))["sections"]}


def test_a_valid_frame_appended_after_the_last_is_audio():
    info, frames = f.encode([f.sine(4096)])
    extra = f.frame([f.sine(100)], number=1)
    data = f.flac(info=f.streaminfo(total=4196, min_block=4096, max_block=4096),
                  frames=frames + extra)
    found = flac.layout(data)
    assert found.error is None and found.audio_end == len(data)


# -- frames ---------------------------------------------------------------------------------------


L, R = f.sine(3000), f.sine(3000, freq=550.0)


@pytest.mark.parametrize("channels, options", [
    ([L], dict(kind="verbatim")),
    ([L], dict(kind="fixed", order=0)),
    ([L], dict(kind="fixed", order=4, partition_order=3)),
    ([L], dict(kind="fixed", order=2, escape=True)),
    ([[v * 8 for v in f.sine(3000, amplitude=0.05)]], dict(kind="fixed", order=1, wasted=3)),
    ([[5] * 3000], dict(kind="constant")),
    ([L, R], dict(kind="lpc", coefs=(1900, -950), precision=13, shift=10)),
    ([L, R], dict(kind="lpc", coefs=(3000, -2600, 900, -100), precision=15, shift=12,
                  partition_order=2)),
    ([L, R], dict(kind="fixed", order=2, assignment=8)),
    ([L, R], dict(kind="fixed", order=2, assignment=9)),
    ([L, R], dict(kind="fixed", order=3, assignment=10)),
    ([L, R, L], dict(kind="fixed", order=2)),
    ([L], dict(kind="fixed", order=2, block_size=1152)),
    ([L], dict(kind="fixed", order=2, block_size=1000)),
    ([L], dict(kind="fixed", order=2, block_size=100)),
    ([L], dict(kind="verbatim", rate=11000)),
])
def test_every_kind_of_frame(channels, options):
    info, frames = f.encode(channels, **options)
    data = f.flac(info=info, frames=frames)
    found = flac.layout(data)
    assert found.error is None, found.error
    assert found.audio_end == len(data)


def test_24_bit_and_8_bit_frames():
    for bits in (8, 24):
        tone = f.sine(2000, bits=bits)
        info, frames = f.encode([tone], bits=bits, kind="fixed", order=2)
        assert flac.layout(f.flac(info=info, frames=frames)).error is None


def test_frame_numbers_over_one_byte():
    tone = f.sine(200 * 64)
    info, frames = f.encode([tone], block_size=64, kind="fixed", order=1)
    found = flac.layout(f.flac(info=info, frames=frames))
    assert found.error is None and found.last_frame.number == 199


def test_variable_block_size_frames():
    first = f.frame([f.sine(1000)], number=0, variable=True)
    second = f.frame([f.sine(500)], number=1000, variable=True)
    info = f.streaminfo(total=1500, min_block=500, max_block=1000)
    found = flac.layout(f.flac(info=info, frames=first + second))
    assert found.error is None and found.last_frame.number == 1000


def test_sample_size_from_streaminfo():
    info, _ = f.encode([L])
    frames = f.frame([L], size_code_from_streaminfo=True)
    assert flac.layout(f.flac(info=info.replace(info[10:18], (8000 << 44 | 15 << 36 | 3000)
                                                .to_bytes(8, "big")), frames=frames)).error is None


@pytest.mark.parametrize("change", [
    lambda h: h[:2] + bytes([0x09]) + h[3:],  # block size code 0 (reserved)
    lambda h: h[:2] + bytes([0xCF]) + h[3:],  # sample rate code 15 (invalid)
    lambda h: h[:3] + bytes([0xB8]) + h[4:],  # channel assignment 11 (reserved)
    lambda h: h[:3] + bytes([0x06]) + h[4:],  # sample size code 3 (reserved)
    lambda h: h[:3] + bytes([0x09]) + h[4:],  # reserved bit set
    lambda h: h[:4] + bytes([0x80]) + h[5:],  # a continuation byte first
    lambda h: h[:4] + bytes([0xFF]) + h[5:],  # 0xFF is never a coded number
    lambda h: h[:-1] + bytes([h[-1] ^ 1]),  # wrong CRC-8
    lambda h: b"\xff\xfa" + h[2:],  # the reserved bit after the sync code
])
def test_invalid_frame_headers(change):
    head = f.frame([[0] * 192])[:6]
    assert flac.frame_header(head + bytes(10), 0, None) is not None
    assert flac.frame_header(change(head) + bytes(10), 0, None) is None


def test_bit_reader_errors():
    bits = flac._Bits(b"\x00\x00", 0, 2)
    with pytest.raises(MalformedFileError):
        bits.unary()
    with pytest.raises(MalformedFileError):
        flac._Bits(b"\x01", 0, 1).read(9)
    with pytest.raises(MalformedFileError):
        flac._Bits(b"\x01", 0, 1).skip(9)
    assert flac._Bits(b"\x01", 0, 1).unary() == 7


# -- through the command line ---------------------------------------------------------------------


def test_show_and_risk(tmp_path):
    path = tmp_path / "song.flac"
    path.write_bytes(KITCHEN)
    report = json.loads(metaex("show", "--json", path).stdout)
    assert report["sections"]["file_type"]["fields"]["embedded_metadata"]["value"] == "examined"
    assert report["sections"]["file_type"]["fields"]["name"]["value"] == "FLAC"
    risk = json.loads(metaex("risk", "--json", path).stdout)
    assert risk["summary"]["critical"] >= 5
