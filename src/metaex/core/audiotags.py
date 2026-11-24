"""Tags at the end of an audio file: ID3v1 (with "TAG+"), APEv1/APEv2,
Lyrics3 v1/v2 and appended ID3v2.4 tags (with a footer), in any order.

They are found from the end of the file backwards: each one is recognized
by what it ends with (ID3v1: "TAG" 128 bytes from the end; APE: an
"APETAGEX" footer; Lyrics3 v2: a 6-digit size and "LYRICS200"; Lyrics3 v1:
"LYRICSEND"; ID3v2.4: a "3DI" footer), then removed, and the search goes
on before it. Every step moves the end back, so the search is bounded.

APE tags (Monkey's Audio, also common in MP3 files) are a 32-byte header
(APEv2 only, optional) and footer ("APETAGEX", version 1000 or 2000, the
size of the items plus the footer, the item count, flags) around items:
value size, flags, a key in ASCII ending with 0x00, the value. Lyrics3 v2
is "LYRICSBEGIN", fields (a 3-letter ID, a 5-digit size, the text), the
6-digit size of all that and "LYRICS200".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from metaex.core import id3
from metaex.core.embedded import Picture
from metaex.core.errors import MalformedFileError
from metaex.core.inflate import Budget
from metaex.core.result import Section

MAX_END_TAGS = 32
MAX_APE_ITEMS = 10_000
APE_HEADER_SIZE = 32
APE_IS_HEADER, APE_HAS_HEADER, APE_NO_FOOTER = 1 << 29, 1 << 31, 1 << 30
LYRICS3_V1_MAX = 5100
MAX_SHOWN_CHARS = 256 * 1024


@dataclass
class ApeItem:
    key: str
    kind: int
    """0 = UTF-8 text, 1 = binary, 2 = external locator (a link)."""
    value: bytes


@dataclass
class ApeTag:
    start: int
    end: int
    version: int
    items: list[ApeItem] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "APEv2" if self.version >= 2000 else "APEv1"


@dataclass
class Lyrics3Tag:
    start: int
    end: int
    version: int
    fields: list[tuple[str, str]] = field(default_factory=list)
    """(field ID, text); a v1 tag has one "LYR" field."""


@dataclass
class EndTags:
    """The tags at the end, in file order, and where the data before them ends."""
    audio_end: int
    ape: list[ApeTag] = field(default_factory=list)
    lyrics3: list[Lyrics3Tag] = field(default_factory=list)
    id3v1: list[id3.V1Tag] = field(default_factory=list)
    id3v2: list[id3.Tag] = field(default_factory=list)
    order: list[tuple[str, int, int]] = field(default_factory=list)
    """(kind, start, end) of every tag, in file order."""


def find_end_tags(data: bytes, limit: int = 0, budget: Budget | None = None) -> EndTags:
    """The tags that end the data, found backwards from the end but never
    before ``limit`` (where the audio starts). Raises MalformedFileError if a
    tag is recognized by its end but is broken."""
    budget = budget or Budget(id3.DECOMPRESS_LIMIT)
    found = EndTags(len(data))
    end = len(data)
    for _ in range(MAX_END_TAGS + 1):
        tag = _tag_ending_at(data, end, limit, budget)
        if tag is None:
            break
        if len(found.order) >= MAX_END_TAGS:
            raise MalformedFileError(f"more than {MAX_END_TAGS} tags at the end of the file")
        kind, start, obj = tag
        if not limit <= start < end:
            raise MalformedFileError(f"the {kind} tag ending at offset {end} starts outside "
                                     "the file")
        found.order.insert(0, (kind, start, end))
        {"APE": found.ape, "Lyrics3": found.lyrics3, "ID3v1": found.id3v1,
         "ID3v2": found.id3v2}[kind].insert(0, obj)
        end = start
    found.audio_end = end
    return found


def _tag_ending_at(data: bytes, end: int, limit: int, budget: Budget):
    """(kind, start, tag) of the tag that ends at ``end``, or None."""
    if end - limit >= id3.V1_SIZE and data[end - id3.V1_SIZE:end - id3.V1_SIZE + 3] == b"TAG":
        tag = id3.v1_tag_at(data, end)
        if tag.start < limit:
            tag.extended = False
        return "ID3v1", tag.start, tag
    if end - limit >= APE_HEADER_SIZE and data[end - 32:end - 24] == b"APETAGEX":
        tag = parse_ape(data, end, limit)
        return "APE", tag.start, tag
    if end - limit >= 26 and data[end - 9:end] == b"LYRICS200":
        tag = parse_lyrics3_v2(data, end, limit)
        return "Lyrics3", tag.start, tag
    if end - limit >= 20 and data[end - 9:end] == b"LYRICSEND":
        tag = parse_lyrics3_v1(data, end, limit)
        return "Lyrics3", tag.start, tag
    start = id3.footer_start(data, end)
    if start is not None and start >= limit:
        tag = id3.parse_tag(data, start, budget)  # footer_start checked that it ends at end
        tag.appended = True
        return "ID3v2", start, tag
    return None


# -- APE ----------------------------------------------------------------------------------


def parse_ape(data: bytes, end: int, limit: int = 0) -> ApeTag:
    """The APE tag whose footer ends at ``end``."""
    footer = data[end - APE_HEADER_SIZE:end]
    version = int.from_bytes(footer[8:12], "little")
    size = int.from_bytes(footer[12:16], "little")
    count = int.from_bytes(footer[16:20], "little")
    flags = int.from_bytes(footer[20:24], "little")
    if version not in (1000, 2000):
        raise MalformedFileError(f"APE tag ending at offset {end} has unknown version {version}")
    if flags & APE_IS_HEADER:
        raise MalformedFileError(f"APE tag ending at offset {end} ends with a header")
    if size < APE_HEADER_SIZE or size > end - limit:
        raise MalformedFileError(f"APE tag ending at offset {end} has an invalid size ({size})")
    if count > MAX_APE_ITEMS:
        raise MalformedFileError(f"APE tag has more than {MAX_APE_ITEMS} items")
    items_start = end - size
    start = items_start
    if version >= 2000 and flags & APE_HAS_HEADER:
        start -= APE_HEADER_SIZE
        if start < limit or data[start:start + 8] != b"APETAGEX":
            raise MalformedFileError(f"APE tag ending at offset {end} has no valid header")
    tag = ApeTag(start, end, version)
    pos, items_end = items_start, end - APE_HEADER_SIZE
    for _ in range(count):
        if pos + 9 > items_end:
            raise MalformedFileError("an APE item is truncated")
        value_size = int.from_bytes(data[pos:pos + 4], "little")
        item_flags = int.from_bytes(data[pos + 4:pos + 8], "little")
        nul = data.find(b"\x00", pos + 8, items_end)
        if nul < 0:
            raise MalformedFileError("an APE item key is not terminated")
        key = data[pos + 8:nul].decode("ascii", errors="replace")
        value_start = nul + 1
        if value_size > items_end - value_start:
            raise MalformedFileError(f"APE item {key!r} needs {value_size} bytes, "
                                     f"{items_end - value_start} available")
        kind = 0 if version < 2000 else (item_flags >> 1) & 3
        tag.items.append(ApeItem(key, kind, data[value_start:value_start + value_size]))
        pos = value_start + value_size
    if pos != items_end:
        raise MalformedFileError(f"APE tag has {items_end - pos} bytes after its items")
    return tag


def ape_section(tag: ApeTag, pictures: list[Picture]) -> Section:
    section = Section("ape", f"{tag.name} tag (at offset {tag.start})")
    for n, item in enumerate(tag.items):
        key = item.key
        if item.kind == 1:
            value = f"binary, {len(item.value)} bytes"
            if key.lower().startswith("cover art"):
                # "Cover Art (Front)" and the like: a file name, 0x00, the image
                name, nul, image = item.value.partition(b"\x00")
                if not nul:
                    name, image = b"", item.value
                if name:
                    value += f", file name {name.decode('utf-8', errors='replace')!r}"
                pictures.append(Picture(f"{tag.name} {key}", image, key))
            section.add(key, key, value, sensitive=True)
            continue
        text = item.value.decode("utf-8", errors="replace")
        values = [v for v in text.split("\x00") if v] if "\x00" in text else [text]
        values = [v[:MAX_SHOWN_CHARS] for v in values]
        note = "link" if item.kind == 2 else None
        section.add(key, key, values[0] if len(values) == 1 else values, sensitive=True,
                    note=note)
    if not section.fields:
        section.add("empty", "Items", "(none)")
    return section


# -- Lyrics3 ------------------------------------------------------------------------------

LYRICS3_FIELDS = {"IND": "Indications", "LYR": "Lyrics", "INF": "Information",
                  "AUT": "Lyrics author", "EAL": "Album", "EAR": "Artist", "ETT": "Title",
                  "IMG": "Image links"}


def parse_lyrics3_v2(data: bytes, end: int, limit: int = 0) -> Lyrics3Tag:
    """The Lyrics3 v2 tag that ends at ``end`` (with "LYRICS200")."""
    digits = data[end - 15:end - 9]
    if not digits.isdigit():
        raise MalformedFileError(f"Lyrics3 v2 tag ending at offset {end} has an invalid size")
    size = int(digits)
    start = end - 15 - size
    if start < limit or size < 11 or data[start:start + 11] != b"LYRICSBEGIN":
        raise MalformedFileError(f"Lyrics3 v2 tag ending at offset {end} does not start with "
                                 "LYRICSBEGIN")
    tag = Lyrics3Tag(start, end, 2)
    pos, fields_end = start + 11, end - 15
    while pos < fields_end:
        if pos + 8 > fields_end or not data[pos + 3:pos + 8].isdigit():
            raise MalformedFileError("a Lyrics3 v2 field is malformed")
        field_id = data[pos:pos + 3].decode("latin-1")
        length = int(data[pos + 3:pos + 8])
        if pos + 8 + length > fields_end:
            raise MalformedFileError(f"Lyrics3 v2 field {field_id} is truncated")
        tag.fields.append((field_id, data[pos + 8:pos + 8 + length].decode("latin-1")))
        pos += 8 + length
    return tag


def parse_lyrics3_v1(data: bytes, end: int, limit: int = 0) -> Lyrics3Tag:
    """The Lyrics3 v1 tag that ends at ``end`` (with "LYRICSEND")."""
    window_start = max(limit, end - 9 - LYRICS3_V1_MAX - 11)
    start = data.rfind(b"LYRICSBEGIN", window_start, end - 9)
    if start < 0:
        raise MalformedFileError(f"Lyrics3 tag ending at offset {end} does not start with "
                                 "LYRICSBEGIN")
    return Lyrics3Tag(start, end, 1, [("LYR", data[start + 11:end - 9].decode("latin-1"))])


def lyrics3_section(tag: Lyrics3Tag) -> Section:
    section = Section("lyrics3", f"Lyrics3 v{tag.version} tag (at offset {tag.start})")
    for field_id, text in tag.fields:
        section.add(field_id, LYRICS3_FIELDS.get(field_id, f"Field {field_id}"),
                    text[:MAX_SHOWN_CHARS], sensitive=field_id != "IND")
    if not section.fields:
        section.add("empty", "Fields", "(none)")
    return section


# -- report ---------------------------------------------------------------------------------


def end_tag_sections(tags: EndTags, warnings: list[str],
                     pictures: list[Picture]) -> list[Section]:
    """A section per tag at the end (appended ID3v2 tags are reported with
    the other ID3v2 tags by the caller), in file order, and the data hidden
    inside ID3v1 fields."""
    sections: list[Section] = []
    ape, lyrics, v1 = iter(tags.ape), iter(tags.lyrics3), iter(tags.id3v1)
    for kind, _start, _end in tags.order:
        if kind == "APE":
            sections.append(ape_section(next(ape), pictures))
        elif kind == "Lyrics3":
            sections.append(lyrics3_section(next(lyrics)))
        elif kind == "ID3v1":
            sections.append(id3.v1_section(next(v1)))
    hidden = sum(t.hidden for t in tags.id3v1)
    if hidden:
        section = Section("hidden_tag_data", "Hidden data in ID3v1 fields")
        for t in tags.id3v1:
            if t.hidden:
                section.add(f"id3v1_{t.offset}", f"ID3v1 tag at offset {t.offset}",
                            f"{t.hidden} non-zero bytes after the end of its text fields",
                            sensitive=True)
        warnings.append(f"CRITICAL: {hidden} bytes of data hidden in ID3v1 fields (after "
                        "the end of their text)")
        sections.append(section)
    return sections
