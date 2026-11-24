"""ID3v2 tags: versions 2.2-2.4, unsynchronisation, extended headers, footers,
frame flags, text encodings, every frame type, limits and malformed tags."""

import random

import pytest

from helpers import id3 as b
from helpers import mp3
from helpers.jpeg import sample_photo
from metaex.core import id3
from metaex.core.errors import MalformedFileError, MetaexError
from metaex.formats.mp3 import leading_tags, tag_sections

PHOTO = sample_photo()


def items(tag: id3.Tag) -> dict:
    out = {}
    for frame in tag.all_frames():
        for item in id3.frame_items(frame):
            out[item.key] = item.value
    return out


def parse(data: bytes) -> id3.Tag:
    tag = id3.parse_tag(data, 0)
    assert tag.error is None, tag.error
    return tag


# -- versions and headers ---------------------------------------------------------------------


@pytest.mark.parametrize("version", [3, 4])
def test_text_frames_in_every_encoding(version):
    frames = [b.text("TIT2", "Title é", encoding=b.LATIN1, version=version),
              b.text("TPE1", "Jane Doe ☺", encoding=b.UTF16, version=version),
              b.text("TALB", "Album ☺", encoding=b.UTF16BE, version=version),
              b.text("TCOM", "Composer ☺", encoding=b.UTF8, version=version)]
    found = items(parse(b.tag(*frames, version=version)))
    assert found == {"TIT2": "Title é", "TPE1": "Jane Doe ☺", "TALB": "Album ☺",
                     "TCOM": "Composer ☺"}


def test_utf16_without_bom_and_big_endian_bom():
    big = b.frame("TIT2", b"\x01\xfe\xff" + "Hi".encode("utf-16-be"))
    no_bom = b.frame("TPE1", b"\x01" + "Jo".encode("utf-16-le"))
    assert items(parse(b.tag(big, no_bom))) == {"TIT2": "Hi", "TPE1": "Jo"}


def test_multiple_values_in_a_24_text_frame():
    tag = parse(b.tag(b.text("TPE1", "Jane Doe", "John Example")))
    assert items(tag) == {"TPE1": ["Jane Doe", "John Example"]}


def test_involved_people_pairs():
    tag = parse(b.tag(b.text("TIPL", "producer", "Jane Doe", "engineer", "John Example")))
    assert items(tag) == {"TIPL": ["producer: Jane Doe", "engineer: John Example"]}


def test_version_22_frames_are_translated():
    tag = parse(b.tag(b.frame("TT2", b"\x00Title", version=2),
                      b.frame("TP1", b"\x00Jane Doe", version=2),
                      b.comm("note", version=2), b.apic(PHOTO, version=2),
                      b.popm("jane.doe@example.com", version=2), version=2))
    keys = [f.key for f in tag.frames]
    assert keys == ["TIT2", "TPE1", "COMM", "APIC", "POPM"]
    found = items(tag)
    assert found["TPE1"] == "Jane Doe" and found["COMM:eng:"] == "note"
    assert found["APIC:front cover"].startswith("front cover, JPG, ")
    assert found["POPM:jane.doe@example.com"] == "rating 196/255, play count 42"


def test_22_compression_is_reported():
    tag = id3.parse_tag(b.tag(b.frame("TT2", b"\x00x", version=2), version=2, flags=0x40), 0)
    assert tag.error.startswith("compressed ID3v2.2 tag")


@pytest.mark.parametrize("version", [2, 3])
def test_whole_tag_unsynchronisation(version):
    frame_id = "PIC" if version == 2 else "APIC"
    data = b.tag(b.apic(PHOTO, version=version), version=version, unsynchronise=True)
    assert b"\xff\xd8\xff" not in data  # the JPEG's markers were broken up
    tag = parse(data)
    assert tag.frames[0].id == frame_id
    assert PHOTO in tag.frames[0].data


def test_24_frame_unsynchronisation():
    payload = b"\x00image/jpeg\x00\x03\x00" + PHOTO
    tag = parse(b.tag(b.unsync_frame_v24("APIC", payload)))
    assert tag.frames[0].data == payload


def test_24_tag_flag_unsynchronises_every_frame():
    payload = b"\x00image/jpeg\x00\x03\x00" + PHOTO
    tag = parse(b.tag(b.frame("APIC", b.unsync(payload)), unsynchronise=True))
    assert tag.frames[0].data == payload


@pytest.mark.parametrize("version, extended", [(3, b.extended_v3(100)), (4, b.extended_v4())])
def test_extended_header(version, extended):
    tag = parse(b.tag(b.text("TIT2", "x", version=version), version=version, extended=extended,
                      padding=100))
    assert tag.extended == extended and items(tag) == {"TIT2": "x"}


def test_bad_extended_header():
    data = b.tag(b.text("TIT2", "x"), extended=b.synchsafe(9999) + b"\x01\x00")
    assert "extended header has an invalid size" in id3.parse_tag(data, 0).error


def test_footer_and_appended_tag():
    tag_bytes = b.tag(b.text("TIT2", "Appended"), footer=True)
    data = mp3.frames(3) + tag_bytes
    start = id3.footer_start(data, len(data))
    assert start == len(data) - len(tag_bytes)
    tag = parse(data[start:])
    assert tag.has_footer and tag.total_size == len(tag_bytes)
    assert id3.footer_start(data[:-1], len(data) - 1) is None
    assert id3.footer_start(b"3DI" + bytes(7), 10) is None


def test_footer_that_does_not_match_its_header():
    tag_bytes = bytearray(b.tag(b.text("TIT2", "x"), footer=True))
    tag_bytes[4] = 1  # header revision differs from the footer's
    data = mp3.frames(1) + bytes(tag_bytes)
    assert id3.footer_start(data, len(data)) is None


def test_missing_footer_is_a_warning():
    data = b.tag(b.text("TIT2", "x"), padding=10, flags=0x10)
    # the header says "footer", but the 10 bytes after the body are audio
    data += bytes(10)
    tag = id3.parse_tag(data, 0)
    assert any("footer is missing" in w for w in tag.warnings)


def test_unknown_header_flags_are_a_warning():
    tag = id3.parse_tag(b.tag(b.text("TIT2", "x"), version=3, flags=0x01), 0)
    assert any("unknown flags" in w for w in tag.warnings)


@pytest.mark.parametrize("head", [b"ID3\x05\x00\x00\x00\x00\x00\x00",   # version 2.5
                                  b"ID3\x04\xff\x00\x00\x00\x00\x00",   # revision 0xFF
                                  b"ID3\x04\x00\x00\x80\x00\x00\x00",   # not synchsafe
                                  b"ID3\x04\x00\x00", b"TAG"])
def test_invalid_headers(head):
    assert id3.header_size(head, 0) is None
    with pytest.raises(MalformedFileError, match="invalid ID3v2 tag header"):
        id3.parse_tag(head, 0)


def test_tag_larger_than_the_file():
    data = b.tag(b.text("TIT2", "x"), padding=100)[:-50]
    with pytest.raises(MalformedFileError, match="is truncated"):
        id3.parse_tag(data, 0)


# -- frame flags ------------------------------------------------------------------------------


@pytest.mark.parametrize("version", [3, 4])
def test_compressed_frames_are_decompressed(version):
    payload = b"\x03" + "Jane Doe".encode() * 20
    tag = parse(b.tag(b.compressed_frame("TPE1", payload, version), version=version))
    frame = tag.frames[0]
    assert frame.compressed and frame.data == payload
    assert items(tag)["TPE1"] == "Jane Doe" * 20


def test_a_compression_bomb_is_not_expanded(monkeypatch):
    monkeypatch.setattr(id3, "DECOMPRESS_LIMIT", 1000)
    tag = parse(b.tag(b.compressed_frame("TPE1", b"\x03" + b"A" * 100_000)))
    assert "limit" in tag.frames[0].problem


def test_broken_compressed_data():
    raw = b.frame("TPE1", b.synchsafe(10) + b"not zlib", 4, flags=0x0009)
    frame = parse(b.tag(raw)).frames[0]
    assert frame.problem.startswith("compressed, but")


@pytest.mark.parametrize("version", [3, 4])
def test_encrypted_frames_are_reported_not_read(version):
    tag = parse(b.tag(b.encrypted_frame("TPE1", b"\x8a\x91secret", version=version),
                      b.text("TIT2", "x", version=version), version=version))
    frame = tag.frames[0]
    assert frame.encrypted and frame.method == 0x80
    item = id3.frame_items(frame)[0]
    assert item.value == "8 bytes" and "encrypted (method 128)" in item.note
    assert items(tag)["TIT2"] == "x"  # the frames after it are read


def test_grouping_byte_is_skipped():
    raw = b.frame("TIT2", b"\x07" + b"\x03Grouped", 4, flags=0x0040)
    assert items(parse(b.tag(raw))) == {"TIT2": "Grouped"}


def test_frame_too_short_for_its_flags():
    raw = b.frame("TIT2", b"\x01", 4, flags=0x0009)
    assert "too short for its flags" in id3.parse_tag(b.tag(raw), 0).error


def test_itunes_plain_sizes_in_24():
    big = "x" * 200  # a size >= 128 differs between plain and synchsafe
    raw = b.frame("TIT2", b"\x03" + big.encode(), 4, plain_size=True)
    tag = parse(b.tag(raw, b.text("TPE1", "Jane Doe")))
    assert items(tag) == {"TIT2": big, "TPE1": "Jane Doe"}
    assert any("not synchsafe" in w for w in tag.warnings)


# -- every kind of frame ----------------------------------------------------------------------


def test_kitchen_sink_frames():
    tag = parse(b.tag(*b.kitchen_sink_frames(PHOTO), version=3))
    found = items(tag)
    assert found["TXXX:Contact"] == "jane.doe@example.com"
    assert found["TXXX:Recorded at"] == "Home studio, 12 Example Street"
    assert found["WXXX:Homepage"] == "https://example.com/jane"
    assert found["WOAR"] == "https://example.com/jane/music"
    assert found["COMM:eng:"] == "Recorded on my phone, first take"
    assert found["USLT:eng:"] == "La la la, the kitchen is small"
    assert found["SYLT:eng:"] == "La la / the kitchen"
    assert found["APIC:front cover"].startswith("front cover, image/jpeg, ")
    assert "file name 'notes.txt'" in found["GEOB:notes"]
    assert found["PRIV:WM/UniqueFileIdentifier"] == "AMGa_id=R 123456;AMGp_id=P 7890"
    assert found["UFID:http://musicbrainz.org"] == "0f9b6c4e-0000-1111-2222-333333333333"
    assert found["POPM:jane.doe@example.com"] == "rating 196/255, play count 42"
    assert found["OWNE"] == ("price paid 'EUR0.99', purchased 20240501, "
                             "seller 'Example Music Store'")
    assert found["PCNT"] == 7
    assert found["TENC"] == "Jane Doe" and found["TSSE"] == "LAME 3.100 -V2"


def test_binary_private_data_stays_bytes():
    tag = parse(b.tag(b.priv("com.example", b"\x00\x01\xfe")))
    assert items(tag)["PRIV:com.example"] == b"\x00\x01\xfe"


def test_chapters_and_their_subframes():
    tag = parse(b.tag(
        b.ctoc("toc", ["ch1", "ch2"], b.text("TIT2", "Chapters")),
        b.chap("ch1", 0, 1000, b.text("TIT2", "Intro"), b.apic(PHOTO)),
        b.chap("ch2", 1000, 2000, b.text("TIT2", "Verse"))))
    keys = [f.key for f in tag.all_frames()]
    assert keys == ["CTOC", "TIT2", "CHAP", "TIT2", "APIC", "CHAP", "TIT2"]
    found = items(tag)
    assert found["CHAP:ch1"] == "0 to 1000 ms" and found["CTOC:toc"] == "1 sub-frames"


def test_chapters_nested_too_deep():
    inner = b.text("TIT2", "x")
    for n in range(id3.MAX_DEPTH + 1):
        inner = b.chap(f"c{n}", 0, 1, inner)
    assert "nested more than" in id3.parse_tag(b.tag(inner), 0).error


def test_unknown_frames_are_listed():
    tag = parse(b.tag(b.frame("XYZ1", b"whatever")))
    item = id3.frame_items(tag.frames[0])[0]
    assert (item.label, item.value) == ("Frame XYZ1", "8 bytes")
    assert "unknown frame" in item.note


def test_undecodable_frames_become_warnings():
    data = b.tag(b.frame("APIC", b"\x03image/jpeg"), b.frame("TIT2", b""))
    warnings, pictures = [], []
    section = id3.tag_section(parse(data), "id3v2", "ID3v2.4 tag", warnings, pictures)
    assert len(section.fields) == 2
    assert any("APIC could not be decoded" in w for w in warnings)
    assert pictures == []


# -- padding, multiple tags, limits -----------------------------------------------------------


def test_zero_padding():
    tag = parse(b.tag(b.text("TIT2", "x"), padding=500))
    assert (tag.padding, tag.padding_nonzero) == (500, 0)
    assert id3.padding_section([tag], []) is None


def test_data_hidden_in_the_padding():
    tag = parse(b.tag(b.text("TIT2", "x"), padding=500, padding_bytes=b"\x00\x00secret"))
    assert tag.padding_nonzero == 6
    warnings = []
    section = id3.padding_section([tag], warnings)
    assert section.key == "hidden_tag_data" and warnings[0].startswith("CRITICAL: 6 bytes")


def test_garbage_after_the_frames_is_data_in_the_padding():
    tag = parse(b.tag(b.text("TIT2", "x"), padding_bytes=b"not a frame header"))
    assert tag.padding_nonzero == tag.padding == len(b"not a frame header")


def test_frame_larger_than_the_tag():
    raw = bytearray(b.text("TIT2", "x"))
    raw[4:8] = b.synchsafe(5000)
    assert "needs 5000 bytes" in id3.parse_tag(b.tag(bytes(raw)), 0).error


def test_too_many_frames(monkeypatch):
    monkeypatch.setattr(id3, "MAX_FRAMES", 10)
    tag = id3.parse_tag(b.tag(*[b.text("TIT2", "x")] * 11), 0)
    assert tag.error == "more than 10 frames"


def test_several_tags_before_the_audio():
    first, second = b.tag(b.text("TIT2", "One"), version=3), b.tag(b.text("TIT2", "Two"))
    data = first + second + mp3.frames(3)
    tags, start = leading_tags(data)
    assert [t.version for t in tags] == ["ID3v2.3", "ID3v2.4"]
    assert start == len(first) + len(second)
    warnings = []
    sections = tag_sections(tags, warnings)
    assert [s.title for s in sections] == ["ID3v2.3 tag 1 (at offset 0)",
                                           f"ID3v2.4 tag 2 (at offset {len(first)})"]


def test_too_many_leading_tags():
    data = b.tag(b.text("TIT2", "x")) * 17 + mp3.frames(2)
    with pytest.raises(MalformedFileError, match="more than 16 ID3v2 tags"):
        leading_tags(data)


def test_pictures_go_through_the_image_parsers():
    data = b.tag(b.apic(PHOTO), b.apic(b"not an image", mime="image/png", picture_type=4))
    warnings = []
    sections = tag_sections(leading_tags(data + mp3.frames(2))[0], warnings)
    keys = [s.key for s in sections]
    assert keys[:2] == ["id3v2", "audio_pictures"] and "gps" in keys
    gps = next(s for s in sections if s.key == "gps")
    assert gps.title.startswith("ID3v2.4 APIC 1 (JPEG): ")
    listing = sections[1]
    assert "not examined" in listing.fields[1].value


def test_random_mutations_never_crash():
    rng = random.Random(42)
    base = b.tag(*b.kitchen_sink_frames(PHOTO), version=3, padding=64)
    base4 = b.tag(*b.kitchen_sink_frames(PHOTO, version=4), b.chap("c", 0, 1, b.text("TIT2", "x")),
                  footer=True)
    for _ in range(300):
        data = bytearray(rng.choice((base, base4)))
        for _ in range(rng.randint(1, 6)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        try:
            tags, _start = leading_tags(bytes(data) + mp3.frames(2))
        except MetaexError:
            continue
        tag_sections(tags, [])
