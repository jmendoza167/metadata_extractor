"""ID3v2 tags (versions 2.2, 2.3 and 2.4), as found in MP3, WAV and FLAC files.

A tag is a 10-byte header ("ID3", version, flags, a 28-bit "synchsafe"
size), an optional extended header, frames, zero padding and (2.4 only)
an optional 10-byte footer ("3DI"), which lets a tag be appended at the
end of a file. Each frame has an ID (3 characters in 2.2, 4 later), a size
and (2.3, 2.4) two flag bytes; a frame may be compressed (zlib), encrypted
or "unsynchronised" (a 0x00 inserted after every 0xFF, so no false MPEG
sync appears; 2.2 and 2.3 apply it to the whole tag, 2.4 to each frame).

Text frames start with an encoding byte: 0 = ISO-8859-1, 1 = UTF-16 with
a byte order mark, 2 = UTF-16BE, 3 = UTF-8.

Everything is bounded: the tag can't extend past the data, frames can't
extend past the tag, and there are caps on the number of frames, the
decompressed size and the nesting of chapter frames.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from metaex.core.embedded import Picture
from metaex.core.errors import MalformedFileError
from metaex.core.inflate import Budget, DecompressionLimitError, inflate
from metaex.core.result import Section

HEADER_SIZE = 10
MAX_FRAMES = 10_000
MAX_DEPTH = 4
"""Chapter (CHAP) and table of contents (CTOC) frames nest at most this deep."""
MAX_SHOWN_CHARS = 256 * 1024
DECOMPRESS_LIMIT = 64 * 1024 * 1024

FLAG_UNSYNC, FLAG_EXTENDED, FLAG_EXPERIMENTAL, FLAG_FOOTER = 0x80, 0x40, 0x20, 0x10
V22_COMPRESSION = 0x40
KNOWN_FLAGS = {2: FLAG_UNSYNC | V22_COMPRESSION,
               3: FLAG_UNSYNC | FLAG_EXTENDED | FLAG_EXPERIMENTAL,
               4: FLAG_UNSYNC | FLAG_EXTENDED | FLAG_EXPERIMENTAL | FLAG_FOOTER}

_FRAME_ID = {3: re.compile(rb"[A-Z0-9]{3}"), 4: re.compile(rb"[A-Z0-9]{4}")}

# ID3v2.2 frame IDs -> their 2.3/2.4 equivalents, so they are named and
# classified the same way.
V22_IDS = {
    "BUF": "RBUF", "CNT": "PCNT", "COM": "COMM", "CRA": "AENC", "EQU": "EQUA", "ETC": "ETCO",
    "GEO": "GEOB", "IPL": "IPLS", "LNK": "LINK", "MCI": "MCDI", "MLL": "MLLT", "PIC": "APIC",
    "POP": "POPM", "REV": "RVRB", "RVA": "RVAD", "SLT": "SYLT", "STC": "SYTC", "TAL": "TALB",
    "TBP": "TBPM", "TCM": "TCOM", "TCO": "TCON", "TCR": "TCOP", "TDA": "TDAT", "TDY": "TDLY",
    "TEN": "TENC", "TFT": "TFLT", "TIM": "TIME", "TKE": "TKEY", "TLA": "TLAN", "TLE": "TLEN",
    "TMT": "TMED", "TOA": "TOPE", "TOF": "TOFN", "TOL": "TOLY", "TOR": "TORY", "TOT": "TOAL",
    "TP1": "TPE1", "TP2": "TPE2", "TP3": "TPE3", "TP4": "TPE4", "TPA": "TPOS", "TPB": "TPUB",
    "TRC": "TSRC", "TRD": "TRDA", "TRK": "TRCK", "TSI": "TSIZ", "TSS": "TSSE", "TT1": "TIT1",
    "TT2": "TIT2", "TT3": "TIT3", "TXT": "TEXT", "TXX": "TXXX", "TYE": "TYER", "UFI": "UFID",
    "ULT": "USLT", "WAF": "WOAF", "WAR": "WOAR", "WAS": "WOAS", "WCM": "WCOM", "WCP": "WCOP",
    "WPB": "WPUB", "WXX": "WXXX", "TCP": "TCMP", "TS2": "TSO2", "TSA": "TSOA", "TSC": "TSOC",
    "TSP": "TSOP", "TST": "TSOT", "CRM": "CRM",
}

FRAME_NAMES = {
    "TALB": "Album", "TBPM": "BPM", "TCOM": "Composer", "TCON": "Genre", "TCOP": "Copyright",
    "TDAT": "Date (DDMM)", "TDEN": "Encoding time", "TDLY": "Playlist delay",
    "TDOR": "Original release time", "TDRC": "Recording time", "TDRL": "Release time",
    "TDTG": "Tagging time", "TENC": "Encoded by", "TEXT": "Lyricist", "TFLT": "File type",
    "TIME": "Time (HHMM)", "TIPL": "Involved people", "TIT1": "Content group",
    "TIT2": "Title", "TIT3": "Subtitle", "TKEY": "Initial key", "TLAN": "Language",
    "TLEN": "Length (ms)", "TMCL": "Musician credits", "TMED": "Media type", "TMOO": "Mood",
    "TOAL": "Original album", "TOFN": "Original file name", "TOLY": "Original lyricist",
    "TOPE": "Original artist", "TORY": "Original release year", "TOWN": "File owner",
    "TPE1": "Artist", "TPE2": "Album artist", "TPE3": "Conductor", "TPE4": "Remixed by",
    "TPOS": "Disc number", "TPRO": "Produced notice", "TPUB": "Publisher",
    "TRCK": "Track number", "TRDA": "Recording dates", "TRSN": "Internet radio station",
    "TRSO": "Radio station owner", "TSIZ": "Size", "TSO2": "Album artist sort order",
    "TSOA": "Album sort order", "TSOC": "Composer sort order", "TSOP": "Artist sort order",
    "TSOT": "Title sort order", "TSRC": "ISRC", "TSSE": "Encoder software and settings",
    "TSST": "Set subtitle", "TYER": "Year", "TCMP": "Compilation", "TDES": "Podcast description",
    "TGID": "Podcast ID", "TKWD": "Podcast keywords", "TCAT": "Podcast category",
    "GRP1": "Grouping", "MVNM": "Movement name", "MVIN": "Movement number",
    "IPLS": "Involved people", "TXXX": "User text",
    "WCOM": "Commercial information URL", "WCOP": "Copyright URL", "WOAF": "Audio file URL",
    "WOAR": "Artist URL", "WOAS": "Audio source URL", "WORS": "Radio station URL",
    "WPAY": "Payment URL", "WPUB": "Publisher URL", "WFED": "Podcast feed URL",
    "WXXX": "User URL",
    "COMM": "Comment", "USLT": "Lyrics", "SYLT": "Synchronized lyrics", "APIC": "Picture",
    "GEOB": "Embedded object", "PRIV": "Private data", "UFID": "Unique file identifier",
    "OWNE": "Ownership (purchase)", "POPM": "Popularimeter (rating by e-mail)",
    "PCNT": "Play counter", "MCDI": "Music CD identifier", "USER": "Terms of use",
    "COMR": "Commercial frame", "ENCR": "Encryption method registration",
    "GRID": "Group registration", "LINK": "Linked information", "AENC": "Audio encryption",
    "RVA2": "Relative volume adjustment", "RVAD": "Relative volume adjustment",
    "EQU2": "Equalisation", "EQUA": "Equalisation", "ETCO": "Event timing codes",
    "MLLT": "MPEG location lookup table", "SYTC": "Synchronised tempo codes",
    "POSS": "Position synchronisation", "RBUF": "Recommended buffer size",
    "ASPI": "Audio seek point index", "SIGN": "Signature", "SEEK": "Seek frame",
    "CHAP": "Chapter", "CTOC": "Table of contents", "PCST": "Podcast", "RVRB": "Reverb",
    "CRM": "Encrypted meta frame",
}

PICTURE_TYPES = (
    "other", "file icon", "other file icon", "front cover", "back cover", "leaflet page",
    "media", "lead artist", "artist", "conductor", "band", "composer", "lyricist",
    "recording location", "during recording", "during performance", "video screen capture",
    "a bright coloured fish", "illustration", "band logo", "publisher logo")


def picture_type_name(n: int) -> str:
    return PICTURE_TYPES[n] if 0 <= n < len(PICTURE_TYPES) else f"type {n}"


def synchsafe(raw: bytes) -> int:
    """A big-endian integer stored 7 bits per byte."""
    value = 0
    for b in raw:
        value = (value << 7) | (b & 0x7F)
    return value


def remove_unsync(data: bytes) -> bytes:
    """Undo unsynchronisation: every 0xFF 0x00 becomes 0xFF."""
    return data.replace(b"\xff\x00", b"\xff")


@dataclass
class Frame:
    id: str
    """As stored (3 characters in ID3v2.2)."""
    data: bytes
    """The frame's content: without the extra header bytes the flags add,
    unsynchronisation undone and decompressed (when possible)."""
    size: int
    """Size as stored."""
    flags: int = 0
    compressed: bool = False
    encrypted: bool = False
    method: int | None = None
    """Encryption method (encrypted frames)."""
    problem: str | None = None
    """Why the content can't be shown (encrypted, decompression failed)."""
    children: list[Frame] = field(default_factory=list)
    """Sub-frames of a CHAP or CTOC frame."""

    @property
    def key(self) -> str:
        """The 2.3/2.4 ID (2.2 IDs are translated)."""
        return V22_IDS.get(self.id, self.id) if len(self.id) == 3 else self.id


@dataclass
class Tag:
    offset: int
    """Offset of the tag (its header) in the data it was read from."""
    major: int
    revision: int
    flags: int
    size: int
    """The size in the header: everything after the header, without a footer."""
    frames: list[Frame] = field(default_factory=list)
    extended: bytes | None = None
    padding: int = 0
    """Bytes after the last frame (should be zeros)."""
    padding_nonzero: int = 0
    """Non-zero bytes among them: data hidden in the padding."""
    error: str | None = None
    """Why the frames couldn't all be read (the tag is malformed)."""
    warnings: list[str] = field(default_factory=list)
    appended: bool = False
    """Found at the end of the file through its footer."""
    frame_count: int = 0
    """Frames read so far, sub-frames included (capped at MAX_FRAMES)."""

    @property
    def total_size(self) -> int:
        return HEADER_SIZE + self.size + (HEADER_SIZE if self.has_footer else 0)

    @property
    def end(self) -> int:
        return self.offset + self.total_size

    @property
    def has_footer(self) -> bool:
        return self.major == 4 and bool(self.flags & FLAG_FOOTER)

    @property
    def version(self) -> str:
        return f"ID3v2.{self.major}"

    def all_frames(self):
        """Every frame, including the sub-frames of chapters, depth first."""
        stack = list(reversed(self.frames))
        while stack:
            frame = stack.pop()
            yield frame
            stack.extend(reversed(frame.children))


def header_size(data: bytes, pos: int) -> int | None:
    """Total size (header, body and footer) of an ID3v2 tag whose valid header
    starts at ``pos``, or None if there is no valid header there."""
    head = data[pos:pos + HEADER_SIZE]
    if (len(head) < HEADER_SIZE or head[:3] != b"ID3" or head[3] not in (2, 3, 4)
            or head[4] == 0xFF or any(b & 0x80 for b in head[6:10])):
        return None
    size = synchsafe(head[6:10])
    footer = HEADER_SIZE if head[3] == 4 and head[5] & FLAG_FOOTER else 0
    return HEADER_SIZE + size + footer


def footer_start(data: bytes, end: int) -> int | None:
    """Start of an ID3v2.4 tag that ends at ``end`` with a footer ("3DI"),
    if its header matches the footer; else None."""
    if end < 2 * HEADER_SIZE:
        return None
    foot = data[end - HEADER_SIZE:end]
    if (foot[:3] != b"3DI" or foot[3] != 4 or foot[4] == 0xFF or not foot[5] & FLAG_FOOTER
            or any(b & 0x80 for b in foot[6:10])):
        return None
    start = end - 2 * HEADER_SIZE - synchsafe(foot[6:10])
    if start < 0 or data[start:start + 3] != b"ID3" or data[start + 3:start + 10] != foot[3:]:
        return None
    return start


def parse_tag(data: bytes, pos: int, budget: Budget | None = None) -> Tag:
    """The ID3v2 tag at ``pos``. Raises MalformedFileError if the header is
    invalid or the tag extends past the data; problems with its frames are
    recorded in ``error`` (the tag is still delimited by its size)."""
    total = header_size(data, pos)
    if total is None:
        raise MalformedFileError(f"invalid ID3v2 tag header at offset {pos}")
    if pos + total > len(data):
        raise MalformedFileError(f"the ID3v2 tag at offset {pos} is truncated: it needs "
                                 f"{total} bytes, {len(data) - pos} available")
    major, revision, flags = data[pos + 3], data[pos + 4], data[pos + 5]
    tag = Tag(pos, major, revision, flags, synchsafe(data[pos + 6:pos + 10]))
    if tag.has_footer and data[tag.end - HEADER_SIZE:tag.end - 7] != b"3DI":
        tag.warnings.append(f"the {tag.version} tag at offset {pos} says it has a footer, but "
                            "the footer is missing")
    if flags & ~KNOWN_FLAGS[major] & 0xFF:
        tag.warnings.append(f"the {tag.version} tag at offset {pos} has unknown flags "
                            f"0x{flags:02x}")
    body = data[pos + HEADER_SIZE:pos + HEADER_SIZE + tag.size]
    if major == 2 and flags & V22_COMPRESSION:
        tag.error = "compressed ID3v2.2 tag (no compression method is defined)"
        return tag
    if major < 4 and flags & FLAG_UNSYNC:
        body = remove_unsync(body)
    budget = budget or Budget(DECOMPRESS_LIMIT)
    try:
        start = _extended_header(tag, body)
        end = _frames(tag, body, start, len(body), tag.frames, budget, 0)
        rest = body[end:]
        tag.padding = len(rest)
        tag.padding_nonzero = len(rest) - rest.count(0)
    except MalformedFileError as exc:
        tag.error = str(exc)
    return tag


def _extended_header(tag: Tag, body: bytes) -> int:
    if tag.major == 2 or not tag.flags & FLAG_EXTENDED:
        return 0
    if len(body) < 6:
        raise MalformedFileError("the extended header is truncated")
    if tag.major == 3:
        size = int.from_bytes(body[0:4], "big") + 4
    else:
        size = synchsafe(body[0:4])
    if size < 6 or size > len(body):
        raise MalformedFileError(f"the extended header has an invalid size ({size})")
    tag.extended = body[:size]
    return size


def _frame_size(tag: Tag, raw: bytes) -> int:
    if tag.major == 2:
        return int.from_bytes(raw[3:6], "big")
    if tag.major == 3:
        return int.from_bytes(raw[4:8], "big")
    return synchsafe(raw[4:8])


def _frames(tag: Tag, body: bytes, start: int, end: int, out: list[Frame], budget: Budget,
            depth: int) -> int:
    """Read frames from body[start:end] into ``out``; return where they end
    (the start of the padding). Raises MalformedFileError."""
    id_len, head_len = (3, 6) if tag.major == 2 else (4, 10)
    pattern = _FRAME_ID[id_len]
    pos = start
    while pos + head_len <= end:
        raw = body[pos:pos + head_len]
        if not pattern.fullmatch(raw[:id_len]):
            return pos  # padding (or garbage, reported as data in the padding)
        size = _frame_size(tag, raw)
        not_synchsafe = any(b & 0x80 for b in raw[4:8])
        if tag.major == 4 and (not_synchsafe or pos + head_len + size > end):
            # iTunes wrote ID3v2.4 tags with plain (not synchsafe) sizes.
            plain = int.from_bytes(raw[4:8], "big")
            if pos + head_len + plain <= end:
                size = plain
                warning = (f"the {tag.version} tag at offset {tag.offset} has frame sizes "
                           "that are not synchsafe (as old iTunes versions wrote them)")
                if warning not in tag.warnings:
                    tag.warnings.append(warning)
        if pos + head_len + size > end:
            raise MalformedFileError(
                f"frame {raw[:id_len].decode('ascii')} at byte {pos} of the {tag.version} tag "
                f"needs {size} bytes, {end - pos - head_len} available")
        tag.frame_count += 1
        if tag.frame_count > MAX_FRAMES:
            raise MalformedFileError(f"more than {MAX_FRAMES} frames")
        flags = int.from_bytes(raw[8:10], "big") if tag.major > 2 else 0
        frame = _decode_frame(tag, raw[:id_len].decode("ascii"),
                              body[pos + head_len:pos + head_len + size], size, flags, budget)
        out.append(frame)
        if frame.key in ("CHAP", "CTOC") and frame.problem is None:
            _chapter_children(tag, frame, budget, depth)
        pos += head_len + size
    return pos


def _decode_frame(tag: Tag, frame_id: str, payload: bytes, size: int, flags: int,
                  budget: Budget) -> Frame:
    """A frame's content with the flags' extra bytes removed, unsynchronisation
    undone and zlib compression undone (when possible)."""
    frame = Frame(frame_id, payload, size, flags)
    if tag.major == 3:
        compressed, encrypted, grouped = flags & 0x80, flags & 0x40, flags & 0x20
        extra = (4 if compressed else 0) + (1 if encrypted else 0) + (1 if grouped else 0)
        if len(payload) < extra:
            raise MalformedFileError(f"frame {frame_id} is too short for its flags")
        if encrypted:
            frame.method = payload[4 if compressed else 0]
        unsync, data = False, payload[extra:]
    elif tag.major == 4:
        grouped, compressed, encrypted = flags & 0x40, flags & 0x08, flags & 0x04
        unsync, has_length = bool(flags & 0x02 or tag.flags & FLAG_UNSYNC), flags & 0x01
        extra = (1 if grouped else 0) + (1 if encrypted else 0) + (4 if has_length else 0)
        if len(payload) < extra:
            raise MalformedFileError(f"frame {frame_id} is too short for its flags")
        if encrypted:
            frame.method = payload[1 if grouped else 0]
        data = payload[extra:]
    else:
        compressed = encrypted = 0
        unsync, data = False, payload
    frame.compressed, frame.encrypted = bool(compressed), bool(encrypted)
    if encrypted:
        frame.data = data
        frame.problem = f"encrypted (method {frame.method}); its contents can't be read"
        return frame
    if unsync:
        data = remove_unsync(data)
    if compressed:
        try:
            data = inflate(data, budget).data
        except DecompressionLimitError as exc:
            frame.problem = f"compressed: {exc}"
        except MalformedFileError as exc:
            frame.problem = f"compressed, but {exc}"
    frame.data = data
    return frame


def _chapter_children(tag: Tag, frame: Frame, budget: Budget, depth: int) -> None:
    """Sub-frames of a CHAP (element ID, 4 times) or CTOC (element ID,
    flags, child IDs) frame."""
    data = frame.data
    end_id = data.find(b"\x00")
    if end_id < 0:
        raise MalformedFileError(f"{frame.key} frame without an element ID")
    pos = end_id + 1
    if frame.key == "CHAP":
        pos += 16
    else:
        if pos + 2 > len(data):
            raise MalformedFileError("CTOC frame is truncated")
        count = data[pos + 1]
        pos += 2
        for _ in range(count):
            nul = data.find(b"\x00", pos)
            if nul < 0:
                raise MalformedFileError("CTOC frame is truncated")
            pos = nul + 1
    if pos > len(data):
        raise MalformedFileError(f"{frame.key} frame is truncated")
    if depth + 1 > MAX_DEPTH:
        raise MalformedFileError(f"chapter frames nested more than {MAX_DEPTH} deep")
    end = _frames(tag, data, pos, len(data), frame.children, budget, depth + 1)
    if data[end:].strip(b"\x00"):
        raise MalformedFileError(f"{frame.key} frame has data after its sub-frames")


# -- decoding frame contents ------------------------------------------------------------------

ENCODINGS = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}


def _decode(raw: bytes, encoding: int) -> str:
    if encoding == 1:
        if raw[:2] == b"\xfe\xff":
            return raw[2:].decode("utf-16-be", errors="replace")
        if raw[:2] == b"\xff\xfe":
            return raw[2:].decode("utf-16-le", errors="replace")
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode(ENCODINGS.get(encoding, "latin-1"), errors="replace")


def _split(raw: bytes, encoding: int, count: int | None = None) -> list[bytes]:
    """Split at string terminators (one 0x00, or two at an even offset for
    UTF-16); at most ``count`` pieces, the last holding the rest."""
    wide = encoding in (1, 2)
    parts: list[bytes] = []
    pos = 0
    while count is None or len(parts) < count - 1:
        if wide:
            i = pos
            while True:
                i = raw.find(b"\x00\x00", i)
                if i < 0 or (i - pos) % 2 == 0:
                    break
                i += 1
            step = 2
        else:
            i, step = raw.find(b"\x00", pos), 1
        if i < 0:
            break
        parts.append(raw[pos:i])
        pos = i + step
    parts.append(raw[pos:])
    return parts


def _strings(raw: bytes, encoding: int, count: int | None = None) -> list[str]:
    return [_decode(p, encoding) for p in _split(raw, encoding, count)]


def _shown(text: str) -> str:
    if len(text) > MAX_SHOWN_CHARS:
        return text[:MAX_SHOWN_CHARS] + f" [shortened: {len(text)} characters in total]"
    return text


def _latin1_until_nul(data: bytes, pos: int) -> tuple[str, int]:
    nul = data.find(b"\x00", pos)
    if nul < 0:
        raise MalformedFileError("a string is not terminated")
    return data[pos:nul].decode("latin-1"), nul + 1


def _encoding_of(data: bytes) -> int:
    if not data:
        raise MalformedFileError("empty frame")
    return data[0]


def _printable_or_bytes(raw: bytes) -> str | bytes:
    if raw and all(0x20 <= b < 0x7F for b in raw):
        return raw.decode("ascii")
    return raw


@dataclass
class Item:
    """One reported value of a frame."""
    key: str
    label: str
    value: object
    note: str | None = None
    picture: Picture | None = None
    """An embedded picture (APIC) to examine with the image parsers."""


def frame_items(frame: Frame, name: str = "") -> list[Item]:
    """The values of a frame, decoded for display. ``name`` (e.g. "APIC 2")
    names an embedded picture. Raises MalformedFileError for a frame that
    can't be decoded."""
    key, data = frame.key, frame.data
    label = FRAME_NAMES.get(key, f"Frame {frame.id}")
    flags = ", ".join(w for w, on in (("compressed", frame.compressed),
                                      ("encrypted", frame.encrypted)) if on) or None
    if frame.problem is not None:
        return [Item(key, label, f"{len(data)} bytes", note=frame.problem)]
    if key == "TXXX":
        enc = _encoding_of(data)
        desc, value = (_strings(data[1:], enc, 2) + [""])[:2]
        return [Item(f"TXXX:{desc}", f"User text: {desc}", _shown(value.rstrip("\x00")),
                     note=flags)]
    if key.startswith("T") and key != "TXXX":
        enc = _encoding_of(data)
        values = [v for v in _strings(data[1:], enc) if v]
        if key in ("TIPL", "TMCL", "IPLS") and len(values) > 1:
            pairs = [f"{values[i]}: {values[i + 1]}" if i + 1 < len(values) else values[i]
                     for i in range(0, len(values), 2)]
            return [Item(key, label, pairs, note=flags)]
        value = _shown(values[0]) if len(values) == 1 else [_shown(v) for v in values]
        return [Item(key, label, value if values else "", note=flags)]
    if key == "WXXX":
        enc = _encoding_of(data)
        desc, url = (_split(data[1:], enc, 2) + [b""])[:2]
        desc_text = _decode(desc, enc)
        return [Item(f"WXXX:{desc_text}", f"User URL: {desc_text}" if desc_text else label,
                     url.rstrip(b"\x00").decode("latin-1"), note=flags)]
    if key.startswith("W"):
        return [Item(key, label, data.split(b"\x00", 1)[0].decode("latin-1"), note=flags)]
    if key in ("COMM", "USLT", "USER"):
        enc = _encoding_of(data)
        if len(data) < 4:
            raise MalformedFileError(f"{key} frame is truncated")
        lang = data[1:4].decode("latin-1", errors="replace")
        if key == "USER":
            return [Item(key, label, _shown(_decode(data[4:], enc)), note=f"language {lang}")]
        desc, text = (_strings(data[4:], enc, 2) + [""])[:2]
        full = label + (f" ({desc})" if desc else "")
        return [Item(f"{key}:{lang}:{desc}", full, _shown(text.rstrip("\x00")),
                     note=f"language {lang}")]
    if key == "SYLT":
        return [_sylt(frame, label)]
    if key == "APIC":
        return [_picture(frame, label, name)]
    if key == "GEOB":
        enc = _encoding_of(data)
        mime, pos = _latin1_until_nul(data, 1)
        parts = _split(data[pos:], enc, 3)
        if len(parts) < 3:
            raise MalformedFileError("GEOB frame is truncated")
        filename, desc = _decode(parts[0], enc), _decode(parts[1], enc)
        text = f"{len(parts[2])} bytes, {mime or 'no MIME type'}"
        if filename:
            text += f", file name {filename!r}"
        if desc:
            text += f", description {desc!r}"
        return [Item(f"GEOB:{desc}", label, text, picture=Picture(name or "GEOB", parts[2],
                                                                  f"embedded object {mime}"))]
    if key in ("PRIV", "UFID"):
        owner, pos = _latin1_until_nul(data, 0)
        return [Item(f"{key}:{owner}", f"{label}: {owner}", _printable_or_bytes(data[pos:]))]
    if key == "POPM":
        email, pos = _latin1_until_nul(data, 0)
        rating = data[pos] if pos < len(data) else 0
        counter = int.from_bytes(data[pos + 1:pos + 9], "big") if pos + 1 < len(data) else None
        text = f"rating {rating}/255" + (f", play count {counter}" if counter is not None else "")
        return [Item(f"POPM:{email}", f"{label}: {email}" if email else label, text)]
    if key == "OWNE":
        enc = _encoding_of(data)
        price, pos = _latin1_until_nul(data, 1)
        date = data[pos:pos + 8].decode("latin-1", errors="replace")
        seller = _decode(data[pos + 8:], enc).rstrip("\x00")
        return [Item(key, label, f"price paid {price!r}, purchased {date}, seller {seller!r}")]
    if key == "PCNT":
        return [Item(key, label, int.from_bytes(data[:8], "big"))]
    if key == "CHAP":
        element, pos = _latin1_until_nul(data, 0)
        start = int.from_bytes(data[pos:pos + 4], "big")
        end = int.from_bytes(data[pos + 4:pos + 8], "big")
        return [Item(f"CHAP:{element}", f"{label} {element}", f"{start} to {end} ms")]
    if key == "CTOC":
        element, _pos = _latin1_until_nul(data, 0)
        return [Item(f"CTOC:{element}", f"{label} {element}",
                     f"{len(frame.children)} sub-frames")]
    if key in FRAME_NAMES:
        return [Item(key, label, f"{len(data)} bytes", note=flags)]
    return [Item(key, f"Frame {frame.id}", f"{len(data)} bytes",
                 note="unknown frame; contents not examined")]


def _sylt(frame: Frame, label: str) -> Item:
    data = frame.data
    enc = _encoding_of(data)
    if len(data) < 6:
        raise MalformedFileError("SYLT frame is truncated")
    lang = data[1:4].decode("latin-1", errors="replace")
    parts = _split(data[6:], enc, 2)
    desc = _decode(parts[0], enc)
    rest = parts[1] if len(parts) > 1 else b""
    lines: list[str] = []
    total = 0
    while rest and total < MAX_SHOWN_CHARS:
        pieces = _split(rest, enc, 2)
        if len(pieces) < 2 or len(pieces[1]) < 4:
            break
        text = _decode(pieces[0], enc)
        lines.append(text)
        total += len(text)
        rest = pieces[1][4:]
    full = label + (f" ({desc})" if desc else "")
    return Item(f"SYLT:{lang}:{desc}", full, _shown(" / ".join(lines)),
                note=f"language {lang}, {len(lines)} lines")


def _picture(frame: Frame, label: str, name: str) -> Item:
    data = frame.data
    enc = _encoding_of(data)
    if len(frame.id) == 3:  # ID3v2.2 PIC: a 3-character image format
        if len(data) < 5:
            raise MalformedFileError("PIC frame is truncated")
        mime, pos = data[1:4].decode("latin-1"), 4
    else:
        mime, pos = _latin1_until_nul(data, 1)
    if pos >= len(data):
        raise MalformedFileError("APIC frame is truncated")
    kind = picture_type_name(data[pos])
    parts = _split(data[pos + 1:], enc, 2)
    if len(parts) < 2:
        raise MalformedFileError("APIC frame has no picture data")
    desc, image = _decode(parts[0], enc), parts[1]
    text = f"{kind}, {mime or 'no MIME type'}, {len(image)} bytes"
    if desc:
        text += f", description {desc!r}"
    picture = Picture(name or "APIC", image, f"{kind}" + (f", {desc!r}" if desc else ""))
    return Item(f"APIC:{kind}", f"{label} ({kind})", text, picture=picture)


def tag_section(tag: Tag, key: str, title: str, warnings: list[str],
                pictures: list[Picture]) -> Section:
    """One section with every frame of the tag; embedded pictures are added
    to ``pictures`` for the image parsers."""
    section = Section(key, title)
    if tag.extended is not None:
        section.add("extended_header", "Extended header", f"{len(tag.extended)} bytes")
    counts: dict[str, int] = {}
    for frame in tag.all_frames():
        name = ""
        if frame.key in ("APIC", "GEOB"):
            counts[frame.key] = counts.get(frame.key, 0) + 1
            name = f"{tag.version} {frame.key} {counts[frame.key]}"
        try:
            items = frame_items(frame, name)
        except MalformedFileError as exc:
            items = [Item(frame.key, FRAME_NAMES.get(frame.key, f"Frame {frame.id}"),
                          f"{len(frame.data)} bytes", note=f"could not be decoded: {exc}")]
            warnings.append(f"{tag.version} frame {frame.id} could not be decoded: {exc}")
        for item in items:
            section.add(item.key, item.label, item.value, sensitive=True, note=item.note)
            if item.picture is not None:
                pictures.append(item.picture)
    for w in tag.warnings:
        warnings.append(w)
    if tag.error is not None:
        warnings.append(f"malformed {tag.version} tag at offset {tag.offset}: {tag.error}")
    return section


def padding_section(tags: list[Tag], warnings: list[str]) -> Section | None:
    """Data hidden in the padding of ID3v2 tags (non-zero bytes after the last frame)."""
    section = Section("hidden_tag_data", "Hidden data in tag padding")
    for tag in tags:
        if tag.padding_nonzero:
            section.add(f"id3v2_{tag.offset}", f"{tag.version} tag at offset {tag.offset}",
                        f"{tag.padding_nonzero} non-zero bytes in {tag.padding} bytes of "
                        "padding", sensitive=True)
    if not section.fields:
        return None
    total = sum(t.padding_nonzero for t in tags)
    warnings.append(f"CRITICAL: {total} bytes of data hidden in the padding of ID3v2 tags "
                    "(players ignore them); this can hide anything")
    return section
