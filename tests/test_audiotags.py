"""Tags at the end of audio files: ID3v1 (and TAG+), APE, Lyrics3, appended
ID3v2, in any order; and the MP3 layout around the audio."""

import random

import pytest

from helpers import audiotags as at
from helpers import id3 as b
from helpers import mp3
from helpers.jpeg import sample_photo
from metaex.core import audiotags, id3
from metaex.core.audiotags import find_end_tags
from metaex.core.errors import MalformedFileError, MetaexError
from metaex.formats.mp3 import layout, tag_sections

AUDIO = mp3.frames(5)
PHOTO = sample_photo()


def kinds(data: bytes, limit: int = 0) -> list[str]:
    return [k for k, _s, _e in find_end_tags(data, limit).order]


# -- ID3v1 ----------------------------------------------------------------------------------


def test_id3v1_1():
    tag = id3.v1_tag_at(at.id3v1(), 128)
    assert tag.fields == [("title", "Title", "Floor plan blues"), ("artist", "Artist", "Jane Doe"),
                          ("album", "Album", "Demo tapes"), ("year", "Year", "2024"),
                          ("comment", "Comment", "jane.doe@example.com"),
                          ("track", "Track number", "3"), ("genre", "Genre", "13 (Pop)")]
    assert tag.hidden == 0 and not tag.extended


def test_id3v1_0_and_space_padding():
    raw = at.id3v1(title="Title" + " " * 25, comment="x" * 30, track=None, genre=255)
    tag = id3.v1_tag_at(raw, 128)
    assert ("title", "Title", "Title") in tag.fields
    assert ("comment", "Comment", "x" * 30) in tag.fields
    assert not any(k in ("track", "genre") for k, _l, _v in tag.fields)


def test_data_hidden_after_an_id3v1_field():
    raw = at.id3v1(comment=b"hi\x00secret")
    data = AUDIO + raw
    tags = find_end_tags(data)
    assert tags.id3v1[0].hidden == 6
    warnings = []
    sections = audiotags.end_tag_sections(tags, warnings, [])
    assert sections[-1].key == "hidden_tag_data" and warnings[0].startswith("CRITICAL: 6")


def test_tag_plus():
    data = AUDIO + at.tag_plus(title=" continued") + at.id3v1(title="T" * 30)
    tags = find_end_tags(data)
    tag = tags.id3v1[0]
    assert tag.extended and tag.start == len(AUDIO) and tags.audio_end == len(AUDIO)
    found = {k: v for k, _l, v in tag.fields}
    assert found["title"] == "T" * 30 + " continued"
    assert found["genre_text"] == "Bedroom pop" and found["end_time"] == "003:10"
    assert id3.v1_section(tag).title == f"ID3v1 tag with TAG+ (at offset {len(AUDIO)})"


# -- APE ------------------------------------------------------------------------------------


def test_apev2_with_header_and_cover():
    data = AUDIO + at.ape_kitchen_sink(PHOTO)
    tags = find_end_tags(data)
    tag = tags.ape[0]
    assert (tag.name, tag.start, tag.end) == ("APEv2", len(AUDIO), len(data))
    pictures = []
    section = audiotags.ape_section(tag, pictures)
    values = {f.key: f.value for f in section.fields}
    assert values["Comment"] == "Write to jane.doe@example.com"
    assert values["Cover Art (Front)"].startswith("binary, ") and "cover.jpg" in values[
        "Cover Art (Front)"]
    assert [p.data for p in pictures] == [PHOTO]
    assert section.get("Related").note == "link"


def test_apev2_without_header_and_apev1():
    items = [at.ape_item("Artist", b"Jane Doe"), at.ape_item("Genre", b"Pop\x00Rock")]
    for raw in (at.ape(items, header=False), at.ape(items, version=1000)):
        tag = find_end_tags(AUDIO + raw).ape[0]
        assert tag.start == len(AUDIO)
        values = {f.key: f.value for f in audiotags.ape_section(tag, []).fields}
        assert values == {"Artist": "Jane Doe", "Genre": ["Pop", "Rock"]}


@pytest.mark.parametrize("change, message", [
    (lambda t: t[:-24] + (9999).to_bytes(4, "little") + t[-20:], "unknown version"),
    (lambda t: t[:-20] + (999999).to_bytes(4, "little") + t[-16:], "invalid size"),
    (lambda t: t[:-16] + (5).to_bytes(4, "little") + t[-12:], "item is truncated"),
    (lambda t: t[:-16] + (0).to_bytes(4, "little") + t[-12:], "bytes after its items"),
    (lambda t: t[:-12] + (1 << 29).to_bytes(4, "little") + t[-8:], "ends with a header"),
    (lambda t: b"XXXXXXXX" + t[8:], "no valid header"),
])
def test_broken_ape_tags(change, message):
    raw = at.ape([at.ape_item("Artist", b"Jane Doe")])
    with pytest.raises(MalformedFileError, match=message):
        find_end_tags(AUDIO + change(raw))


def test_ape_item_larger_than_the_tag():
    raw = bytearray(at.ape([at.ape_item("Artist", b"Jane Doe")], header=False))
    raw[0:4] = (500).to_bytes(4, "little")
    with pytest.raises(MalformedFileError, match="needs 500 bytes"):
        find_end_tags(AUDIO + bytes(raw))


# -- Lyrics3 --------------------------------------------------------------------------------


def test_lyrics3_v2():
    data = AUDIO + at.lyrics3_v2() + at.id3v1()
    tags = find_end_tags(data)
    assert kinds(data) == ["Lyrics3", "ID3v1"] and tags.audio_end == len(AUDIO)
    section = audiotags.lyrics3_section(tags.lyrics3[0])
    values = {f.key: f.value for f in section.fields}
    assert values["AUT"] == "Jane Doe" and values["LYR"].startswith("[00:01]La la la")


def test_lyrics3_v1():
    data = AUDIO + at.lyrics3_v1() + at.id3v1()
    tags = find_end_tags(data)
    assert tags.lyrics3[0].version == 1 and tags.lyrics3[0].fields == [
        ("LYR", "La la la, the kitchen is small")]
    assert tags.audio_end == len(AUDIO)


@pytest.mark.parametrize("raw, message", [
    (b"LYRICSBEGININD00002xx" + b"00abcd" + b"LYRICS200", "invalid size"),
    (b"LYRICSBEGXNIND00002xx000021LYRICS200", "does not start with LYRICSBEGIN"),
    (b"LYRICSBEGININD00009xx000021LYRICS200", "IND is truncated"),
    (b"LYRICSBEGININD0x002xx000021LYRICS200", "field is malformed"),
    (b"no begin marker hereLYRICSEND", "does not start with LYRICSBEGIN"),
])
def test_broken_lyrics3(raw, message):
    with pytest.raises(MalformedFileError, match=message):
        find_end_tags(AUDIO + raw)


# -- appended ID3v2, order, limits ------------------------------------------------------------


def test_every_tag_in_one_order():
    tail = (b.tag(b.text("TIT2", "Appended"), footer=True) + at.ape_kitchen_sink(PHOTO)
            + at.lyrics3_v2() + at.id3v1())
    data = AUDIO + tail
    tags = find_end_tags(data)
    assert kinds(data) == ["ID3v2", "APE", "Lyrics3", "ID3v1"]
    assert tags.audio_end == len(AUDIO) and tags.id3v2[0].appended


def test_another_order():
    tail = (at.lyrics3_v2() + at.ape(([at.ape_item("Artist", b"Jane Doe")]))
            + b.tag(b.text("TIT2", "Appended"), footer=True) + at.id3v1())
    assert kinds(AUDIO + tail) == ["Lyrics3", "APE", "ID3v2", "ID3v1"]


def test_no_tags_and_the_limit():
    assert kinds(AUDIO) == [] and find_end_tags(AUDIO).audio_end == len(AUDIO)
    raw = at.id3v1()
    assert kinds(raw, limit=1) == []  # would start before the limit
    assert kinds(raw, limit=0) == ["ID3v1"]


def test_too_many_tags(monkeypatch):
    monkeypatch.setattr(audiotags, "MAX_END_TAGS", 3)
    with pytest.raises(MalformedFileError, match="more than 3 tags"):
        find_end_tags(AUDIO + at.id3v1() * 4)


# -- the MP3 layout -------------------------------------------------------------------------


def test_mp3_layout_and_sections():
    head = b.tag(*b.kitchen_sink_frames(PHOTO), version=3, padding=32)
    tail = b.tag(b.text("TIT2", "Appended"), footer=True) + at.ape_kitchen_sink(PHOTO) + at.id3v1()
    data = head + AUDIO + tail
    found = layout(data)
    assert (found.audio_start, found.audio_end) == (len(head), len(head) + len(AUDIO))
    assert [k for k, _s, _e in found.tag_ranges()] == ["ID3v2", "ID3v2", "APE", "ID3v1"]
    warnings = []
    sections = tag_sections(found.id3v2, warnings, found.end_tags)
    keys = [s.key for s in sections]
    assert keys[:4] == ["id3v2", "id3v2", "ape", "id3v1"] and "audio_pictures" in keys
    titles = [s.title for s in sections[:2]]
    assert titles == ["ID3v2.3 tag 1 (at offset 0)", "ID3v2.4 tag 2 (appended at the end)"]
    pictures = next(s for s in sections if s.key == "audio_pictures")
    assert [f.label for f in pictures.fields] == ["ID3v2.3 APIC 1", "ID3v2.3 GEOB 1",
                                                  "APEv2 Cover Art (Front)"]
    assert sum(1 for s in sections if s.key == "gps") == 2


def test_random_mutations_of_the_end_never_crash():
    rng = random.Random(7)
    tail = (b.tag(b.text("TIT2", "Appended"), footer=True) + at.ape_kitchen_sink(PHOTO[:200])
            + at.lyrics3_v2() + at.tag_plus() + at.id3v1())
    base = AUDIO + tail
    for _ in range(400):
        data = bytearray(base)
        for _ in range(rng.randint(1, 4)):
            data[len(AUDIO) + rng.randrange(len(tail))] = rng.randrange(256)
        try:
            tags = find_end_tags(bytes(data))
        except MetaexError:
            continue
        audiotags.end_tag_sections(tags, [], [])
        assert 0 <= tags.audio_end <= len(data)
