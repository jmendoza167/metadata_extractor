"""MP3: MPEG audio frames, and the tags around them.

An MP3 file is a sequence of MPEG audio frames (MPEG-1, MPEG-2 or MPEG-2.5,
Layer I, II or III), usually with tags before (ID3v2) and after them
(ID3v1, APE, Lyrics3, an appended ID3v2). Frames have no container: each
starts with a 4-byte header (11 sync bits, then version, layer, bitrate,
sample rate, padding, channel mode, ...) from which its length follows;
the next frame starts right after it.

Walking the frames:
- The first frame must have a valid header and be followed by another
  frame of the same version, layer and sample rate (or end exactly at the
  end of the audio). That fixes the stream's parameters ("lock").
- After that, every header with the locked parameters is a frame, and the
  next one is expected right after it.
- Where none is found, the walk resynchronizes: it looks for the next
  position where a locked header is followed by another one (or ends the
  audio). The bytes skipped are junk: decoders ignore them, so they can
  hide anything, and they are reported and removed.
- The first frame may be an information frame instead of audio (Xing or
  Info, often with a LAME tag; or VBRI): it gives the frame count and a
  seek table, and the LAME tag holds the encoder name, the encoder delay
  and padding (for gapless playback) and a CRC.

Free-format streams (bitrate index 0: the frame length isn't in the
header) are refused, and so is AAC in ADTS frames (same sync bits, layer 0).
Every loop is bounded by the data: each step moves forward by at least
one byte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from metaex.core import id3
from metaex.core.audiotags import EndTags, end_tag_sections, find_end_tags
from metaex.core.crc import crc16_arc
from metaex.core.embedded import Picture, picture_sections
from metaex.core.errors import MalformedFileError, UnsupportedFormatError
from metaex.core.inflate import Budget
from metaex.core.result import Section

MAX_JUNK_REGIONS = 10_000
"""More gaps between frames than this: not MPEG audio."""
MAX_SYNC_CANDIDATES = 1_000_000
"""More would-be frame headers than this that turn out not to be frames:
not MPEG audio (and the search stays fast on hostile data)."""

# Version bits: 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5 (1 is reserved).
# Layer bits: 3 = Layer I, 2 = Layer II, 1 = Layer III (0 is reserved).
VERSION_NAMES = {3: "MPEG-1", 2: "MPEG-2", 0: "MPEG-2.5"}
LAYER_NAMES = {3: "Layer I", 2: "Layer II", 1: "Layer III"}
SAMPLE_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
CHANNEL_MODES = ("stereo", "joint stereo", "dual channel", "mono")
EMPHASIS = ("none", "50/15 ms", "reserved", "CCITT J.17")

_BITRATES_KBPS = {
    (3, 3): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (3, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (3, 1): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 3): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 1): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}


def bitrate_kbps(version: int, layer: int, index: int) -> int:
    """Bitrate in kbit/s for a header's version, layer and bitrate index (1-14)."""
    return _BITRATES_KBPS[(3 if version == 3 else 2, layer)][index]


def samples_per_frame(version: int, layer: int) -> int:
    if layer == 3:
        return 384
    if layer == 2 or version == 3:
        return 1152
    return 576  # Layer III, MPEG-2 and 2.5


def _build_length_table() -> list[int]:
    """Frame length (without the padding slot) for every value of header
    bytes 1 and 2 (as b1 << 8 | b2); 0 if the header is invalid or free-format.
    The padding bit adds one slot: 4 bytes in Layer I, 1 byte otherwise."""
    table = [0] * 65536
    for b1 in range(0xE0, 0x100):
        version, layer = (b1 >> 3) & 3, (b1 >> 1) & 3
        if version == 1 or layer == 0:
            continue
        for b2 in range(256):
            index, rate_index = b2 >> 4, (b2 >> 2) & 3
            if index in (0, 15) or rate_index == 3:
                continue
            bitrate = bitrate_kbps(version, layer, index) * 1000
            rate = SAMPLE_RATES[version][rate_index]
            if layer == 3:
                length = (12 * bitrate // rate) * 4
            else:
                length = samples_per_frame(version, layer) // 8 * bitrate // rate
            table[(b1 << 8) | b2] = length
    return table


_LENGTHS = _build_length_table()
LOCK_MASK_B1 = 0xFE  # version and layer (not the CRC bit)
LOCK_MASK_B2 = 0x0C  # sample rate


def _byte_class(values: list[int]) -> bytes:
    return b"[" + b"".join(re.escape(bytes([v])) for v in values) + b"]"


_VALID_B1 = [b for b in range(0xE0, 0x100) if (b >> 3) & 3 != 1 and (b >> 1) & 3 != 0]
_VALID_B2 = [b for b in range(256) if b >> 4 not in (0, 15) and (b >> 2) & 3 != 3]
_CANDIDATE = re.compile(b"\xff(?=" + _byte_class(_VALID_B1) + _byte_class(_VALID_B2) + b")")
"""A byte 0xFF followed by a plausible header (searched in C, not byte by byte)."""


@dataclass(frozen=True)
class FrameHeader:
    version: int
    """Version bits: 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5."""
    layer: int
    """Layer bits: 3 = Layer I, 2 = Layer II, 1 = Layer III."""
    protected: bool
    """A 16-bit CRC follows the header."""
    bitrate: int
    """Bits per second."""
    sample_rate: int
    padding: bool
    private: bool
    channel_mode: int
    mode_extension: int
    copyright: bool
    original: bool
    emphasis: int
    length: int

    @property
    def version_name(self) -> str:
        return VERSION_NAMES[self.version]

    @property
    def layer_name(self) -> str:
        return LAYER_NAMES[self.layer]

    @property
    def layer_number(self) -> int:
        return 4 - self.layer

    @property
    def channels(self) -> int:
        return 1 if self.channel_mode == 3 else 2

    @property
    def samples(self) -> int:
        return samples_per_frame(self.version, self.layer)

    @property
    def side_info_size(self) -> int:
        """Layer III side information after the header (and its CRC)."""
        if self.layer != 1:
            return 0
        if self.version == 3:
            return 17 if self.channel_mode == 3 else 32
        return 9 if self.channel_mode == 3 else 17


def frame_length(data: bytes, pos: int) -> int:
    """Length of the frame whose valid header starts at ``pos``, else 0."""
    if pos < 0 or pos + 4 > len(data) or data[pos] != 0xFF:
        return 0
    b2 = data[pos + 2]
    length = _LENGTHS[(data[pos + 1] << 8) | b2]
    if not length or data[pos + 3] & 3 == 2:  # reserved emphasis
        return 0
    if b2 & 2:
        length += 4 if data[pos + 1] & 6 == 6 else 1
    return length


def parse_header(data: bytes, pos: int) -> FrameHeader | None:
    """The frame header at ``pos``, or None if there is no valid one."""
    length = frame_length(data, pos)
    if not length:
        return None
    b1, b2, b3 = data[pos + 1], data[pos + 2], data[pos + 3]
    version, layer = (b1 >> 3) & 3, (b1 >> 1) & 3
    return FrameHeader(
        version=version, layer=layer, protected=not b1 & 1,
        bitrate=bitrate_kbps(version, layer, b2 >> 4) * 1000,
        sample_rate=SAMPLE_RATES[version][(b2 >> 2) & 3], padding=bool(b2 & 2),
        private=bool(b2 & 1), channel_mode=b3 >> 6, mode_extension=(b3 >> 4) & 3,
        copyright=bool(b3 & 8), original=bool(b3 & 4), emphasis=b3 & 3, length=length)


def is_free_format(data: bytes, pos: int) -> bool:
    """A header that would be valid, but with bitrate index 0 (free format)."""
    if pos < 0 or pos + 4 > len(data) or data[pos] != 0xFF:
        return False
    b1, b2 = data[pos + 1], data[pos + 2]
    return (b1 & 0xE0 == 0xE0 and (b1 >> 3) & 3 != 1 and (b1 >> 1) & 3 != 0
            and b2 >> 4 == 0 and (b2 >> 2) & 3 != 3 and data[pos + 3] & 3 != 2)


def is_adts(data: bytes, pos: int) -> bool:
    """An AAC ADTS header (MPEG sync bits with layer 0)."""
    return (pos >= 0 and pos + 7 <= len(data) and data[pos] == 0xFF
            and data[pos + 1] & 0xF6 == 0xF0 and (data[pos + 2] >> 2) & 0xF < 13)


def _locked(data: bytes, pos: int, lock: tuple[int, int]) -> int:
    """frame_length() of a header at ``pos`` with the locked version, layer
    and sample rate; 0 otherwise."""
    length = frame_length(data, pos)
    if (length and data[pos + 1] & LOCK_MASK_B1 == lock[0]
            and data[pos + 2] & LOCK_MASK_B2 == lock[1]):
        return length
    return 0


def _lock_of(data: bytes, pos: int) -> tuple[int, int]:
    return data[pos + 1] & LOCK_MASK_B1, data[pos + 2] & LOCK_MASK_B2


# -- information frames (Xing, Info, LAME, VBRI) ------------------------------------------

XING_FRAMES, XING_BYTES, XING_TOC, XING_QUALITY = 1, 2, 4, 8
LAME_TAG_SIZE = 36
VBRI_OFFSET = 36
VBRI_HEADER_SIZE = 26


@dataclass
class LameTag:
    offset: int
    """Offset of the tag in the file."""
    encoder: str
    revision: int
    vbr_method: int
    lowpass: int
    """Hz."""
    encoder_delay: int
    encoder_padding: int
    music_length: int
    music_crc: int
    tag_crc: int
    crc_ok: bool


@dataclass
class InfoFrame:
    kind: str
    """"Xing", "Info" or "VBRI"."""
    offset: int
    """Offset of the frame in the file."""
    length: int
    frames: int | None = None
    bytes: int | None = None
    toc: bool = False
    quality: int | None = None
    vbri_version: int | None = None
    vbri_delay: int | None = None
    lame: LameTag | None = None
    unknown_ranges: list[tuple[int, int]] = field(default_factory=list)
    """Ranges of the frame (offsets in the file) outside the known fields:
    the side information and everything after the last field. Encoders
    write zeros there."""

    def hidden_bytes(self, data: bytes) -> int:
        """Non-zero bytes in the unknown ranges (data hidden in the frame)."""
        return sum(len(data[a:b]) - data[a:b].count(0) for a, b in self.unknown_ranges)


def _printable(raw: bytes) -> bool:
    return bool(raw) and all(0x20 <= b < 0x7F for b in raw)


def _lame_tag(data: bytes, frame_start: int, pos: int, frame_end: int) -> LameTag | None:
    """The LAME tag at ``pos`` if one is there (an encoder name in printable
    ASCII, all 36 bytes inside the frame)."""
    if pos + LAME_TAG_SIZE > frame_end or not _printable(data[pos:pos + 4]):
        return None
    raw = data[pos:pos + LAME_TAG_SIZE]
    delay_padding = int.from_bytes(raw[21:24], "big")
    tag_crc = int.from_bytes(raw[34:36], "big")
    return LameTag(
        offset=pos,
        encoder=raw[0:9].rstrip(b"\x00 ").decode("ascii", errors="replace"),
        revision=raw[9] >> 4, vbr_method=raw[9] & 0xF, lowpass=raw[10] * 100,
        encoder_delay=delay_padding >> 12, encoder_padding=delay_padding & 0xFFF,
        music_length=int.from_bytes(raw[28:32], "big"),
        music_crc=int.from_bytes(raw[32:34], "big"), tag_crc=tag_crc,
        crc_ok=crc16_arc(data[frame_start:pos + 34]) == tag_crc)


def info_frame(data: bytes, pos: int) -> InfoFrame | None:
    """The Xing/Info or VBRI information in the frame at ``pos``, or None if
    it is an ordinary audio frame."""
    header = parse_header(data, pos)
    if header is None or pos + header.length > len(data):
        return None
    end = pos + header.length
    side_start = pos + 4 + (2 if header.protected else 0)
    xing = side_start + header.side_info_size
    if header.layer == 1 and data[xing:xing + 4] in (b"Xing", b"Info"):
        info = InfoFrame(data[xing:xing + 4].decode("ascii"), pos, header.length)
        p = xing + 8
        if p > end:
            return None
        flags = int.from_bytes(data[xing + 4:xing + 8], "big")
        sizes = ((XING_FRAMES, 4), (XING_BYTES, 4), (XING_TOC, 100), (XING_QUALITY, 4))
        if p + sum(size for flag, size in sizes if flags & flag) > end:
            return None
        if flags & XING_FRAMES:
            info.frames = int.from_bytes(data[p:p + 4], "big")
            p += 4
        if flags & XING_BYTES:
            info.bytes = int.from_bytes(data[p:p + 4], "big")
            p += 4
        if flags & XING_TOC:
            info.toc = True
            p += 100
        if flags & XING_QUALITY:
            info.quality = int.from_bytes(data[p:p + 4], "big")
            p += 4
        info.lame = _lame_tag(data, pos, p, end)
        if info.lame is not None:
            p += LAME_TAG_SIZE
        info.unknown_ranges = [(side_start, xing), (p, end)]
        return info
    vbri = pos + VBRI_OFFSET
    if data[vbri:vbri + 4] == b"VBRI" and vbri + VBRI_HEADER_SIZE <= end:
        r = data[vbri:vbri + VBRI_HEADER_SIZE]
        entries, entry_size = int.from_bytes(r[18:20], "big"), int.from_bytes(r[22:24], "big")
        p = vbri + VBRI_HEADER_SIZE + entries * entry_size
        if entry_size > 4 or p > end:
            return None
        info = InfoFrame("VBRI", pos, header.length, frames=int.from_bytes(r[14:18], "big"),
                         bytes=int.from_bytes(r[10:14], "big"), toc=entries > 0,
                         quality=int.from_bytes(r[8:10], "big"),
                         vbri_version=int.from_bytes(r[4:6], "big"),
                         vbri_delay=int.from_bytes(r[6:8], "big"))
        info.unknown_ranges = [(side_start, vbri), (p, end)]
        return info
    return None


def clean_info_frame(data: bytes, info: InfoFrame) -> bytes:
    """The information frame with its unknown ranges set to zero."""
    frame = bytearray(data[info.offset:info.offset + info.length])
    for a, b in info.unknown_ranges:
        frame[a - info.offset:b - info.offset] = bytes(b - a)
    return bytes(frame)


# -- walking the frames --------------------------------------------------------------------


@dataclass
class FrameWalk:
    start: int
    """Where the audio begins (after any ID3v2 tag)."""
    end: int
    """Where the audio ends (before any tags at the end)."""
    runs: list[tuple[int, int]] = field(default_factory=list)
    """Ranges of back-to-back frames, in file order."""
    junk: list[tuple[int, int]] = field(default_factory=list)
    """Ranges between start and end that are not frames, in file order."""
    frames: int = 0
    """Every frame, including an information frame."""
    first: FrameHeader | None = None
    info: InfoFrame | None = None
    bitrates: dict[int, int] = field(default_factory=dict)
    """Bitrate (bit/s) -> number of audio frames."""
    channel_modes: set[int] = field(default_factory=set)
    audio_bytes: int = 0
    """Bytes in audio frames (not the information frame)."""
    private_bits: int = 0
    """Frames with the private bit set."""
    candidates: int = 0
    """Would-be headers rejected while searching for frames."""

    @property
    def audio_frames(self) -> int:
        return self.frames - (1 if self.info is not None else 0)

    @property
    def duration(self) -> float:
        """Seconds of audio in the frames (without encoder delay and padding)."""
        if self.first is None:
            return 0.0
        return self.audio_frames * self.first.samples / self.first.sample_rate


def _find_sync(walk: FrameWalk, data: bytes, pos: int, end: int,
               lock: tuple[int, int] | None, limit: int | None = None) -> int | None:
    """The first offset >= pos (and < ``limit``, if given) where a frame
    starts that is confirmed by the next one (same version, layer and
    sample rate) or that ends exactly at ``end``; None if there is none."""
    i = pos
    while True:
        m = _CANDIDATE.search(data, i, end)
        if m is None or m.start() + 4 > end or (limit is not None and m.start() >= limit):
            return None
        j = m.start()
        here = lock if lock is not None else (
            _lock_of(data, j) if frame_length(data, j) else None)
        length = _locked(data, j, here) if here is not None else 0
        if length:
            k = j + length
            if k == end or (k + 4 <= end and _locked(data, k, here)):
                return j
        walk.candidates += 1
        if walk.candidates > MAX_SYNC_CANDIDATES:
            raise MalformedFileError(f"more than {MAX_SYNC_CANDIDATES} false MPEG frame "
                                     "headers; this does not look like MPEG audio")
        i = j + 1


def walk_frames(data: bytes, start: int, end: int) -> FrameWalk:
    """Walk the MPEG audio frames in data[start:end].

    Raises UnsupportedFormatError for free-format MPEG audio and AAC (ADTS),
    and MalformedFileError if there are no frames, too many gaps between
    them, or the last frame is cut off.
    """
    if not 0 <= start <= end <= len(data):
        raise ValueError("bad audio range")
    walk = FrameWalk(start, end)
    lock: tuple[int, int] | None = None
    pos = start
    while pos < end:
        sync = _find_sync(walk, data, pos, end, lock)
        if lock is None and (sync is None or sync > start):
            _refuse_other_streams(data, start, end, sync)
        if sync is None:
            _add_junk(walk, pos, end)
            break
        if sync > pos:
            _add_junk(walk, pos, sync)
        if lock is None:
            lock = _lock_of(data, sync)
            walk.first = parse_header(data, sync)
            walk.info = info_frame(data, sync)
        p = sync
        while p + 4 <= end:
            length = _locked(data, p, lock)
            if not length:
                if is_free_format(data, p) and _lock_of(data, p) == lock:
                    raise UnsupportedFormatError(
                        f"free-format MPEG audio at offset {p} is not supported (the frame "
                        "length is not in the header)")
                break
            if p + length > end:
                if _find_sync(walk, data, p + 1, end, lock) is not None:
                    break  # a false header in junk: real frames follow
                raise MalformedFileError(
                    f"the MPEG frame at offset {p} is truncated: needs {length} bytes, "
                    f"{end - p} available (the file may be cut off)")
            if (p + length != end and not _locked(data, p + length, lock)
                    and _find_sync(walk, data, p + 1, end, lock, limit=p + length) is not None):
                break  # a false header: a real frame starts inside it
            walk.frames += 1
            if walk.info is None or p != walk.info.offset:
                b2, b3 = data[p + 2], data[p + 3]
                bitrate = bitrate_kbps(lock[0] >> 3 & 3, lock[0] >> 1 & 3, b2 >> 4) * 1000
                walk.bitrates[bitrate] = walk.bitrates.get(bitrate, 0) + 1
                walk.channel_modes.add(b3 >> 6)
                walk.audio_bytes += length
                walk.private_bits += b2 & 1
            p += length
        if p == sync:  # can't happen (sync is confirmed), but never loop forever
            raise MalformedFileError(f"the MPEG frame at offset {sync} could not be walked")
        walk.runs.append((sync, p))
        pos = p
    if not walk.runs:
        raise MalformedFileError("no MPEG audio frames found")
    return walk


def _add_junk(walk: FrameWalk, start: int, end: int) -> None:
    if len(walk.junk) >= MAX_JUNK_REGIONS:
        raise MalformedFileError(f"more than {MAX_JUNK_REGIONS} gaps between MPEG frames; "
                                 "this does not look like MPEG audio")
    walk.junk.append((start, end))


def _refuse_other_streams(data: bytes, start: int, end: int, sync: int | None) -> None:
    """At the start of the audio there is no MPEG frame: refuse free-format
    MPEG audio and AAC clearly instead of calling them junk."""
    if is_free_format(data, start):
        raise UnsupportedFormatError("free-format MPEG audio is not supported (the frame "
                                     "length is not in the header)")
    if is_adts(data, start):
        raise UnsupportedFormatError("this is AAC audio (ADTS), not MP3; AAC is not supported")
    if sync is None:
        raise MalformedFileError("no MPEG audio frames found")


# -- ID3v2 tags before the audio ----------------------------------------------------------

MAX_LEADING_TAGS = 16


def leading_tags(data: bytes, budget: Budget | None = None) -> tuple[list[id3.Tag], int]:
    """The ID3v2 tags at the start of the file (some files have several, one
    after the other) and the offset where the audio starts. Raises
    MalformedFileError for a tag that extends past the end of the file."""
    budget = budget or Budget(id3.DECOMPRESS_LIMIT)
    tags: list[id3.Tag] = []
    pos = 0
    while id3.header_size(data, pos) is not None:
        if len(tags) >= MAX_LEADING_TAGS:
            raise MalformedFileError(f"more than {MAX_LEADING_TAGS} ID3v2 tags")
        tag = id3.parse_tag(data, pos, budget)
        tags.append(tag)
        pos = tag.end
    return tags, pos


@dataclass
class Layout:
    """Where the tags and the audio are."""
    leading: list[id3.Tag]
    """ID3v2 tags at the start."""
    audio_start: int
    end_tags: EndTags
    """ID3v1, APE, Lyrics3 and appended ID3v2 tags at the end."""

    @property
    def audio_end(self) -> int:
        return self.end_tags.audio_end

    @property
    def id3v2(self) -> list[id3.Tag]:
        return self.leading + self.end_tags.id3v2

    def tag_ranges(self) -> list[tuple[str, int, int]]:
        """(kind, start, end) of every tag, in file order."""
        return ([("ID3v2", t.offset, t.end) for t in self.leading]
                + list(self.end_tags.order))


def layout(data: bytes) -> Layout:
    """Find the tags before and after the audio. Raises MalformedFileError
    for a tag that is broken or extends past the file."""
    budget = Budget(id3.DECOMPRESS_LIMIT)
    leading, start = leading_tags(data, budget)
    return Layout(leading, start, find_end_tags(data, start, budget))


def tag_sections(tags: list[id3.Tag], warnings: list[str],
                 end_tags: EndTags | None = None) -> list[Section]:
    """A section per tag (ID3v2 first, then those at the end in file
    order), the pictures in them (through the image parsers) and any data
    hidden in tag padding or ID3v1 fields."""
    sections: list[Section] = []
    pictures: list[Picture] = []
    for n, tag in enumerate(tags):
        where = "appended at the end" if tag.appended else f"at offset {tag.offset}"
        title = f"{tag.version} tag" + (f" {n + 1}" if len(tags) > 1 else "") + f" ({where})"
        sections.append(id3.tag_section(tag, "id3v2", title, warnings, pictures))
    if end_tags is not None:
        sections += end_tag_sections(end_tags, warnings, pictures)
    sections += picture_sections(pictures, warnings, "audio_pictures", "Embedded pictures")
    hidden = id3.padding_section(tags, warnings)
    if hidden is not None:
        sections.append(hidden)
    return sections


# -- report sections ----------------------------------------------------------------------


def _bitrate_text(walk: FrameWalk) -> str:
    if not walk.bitrates:
        return "(no audio frames)"
    if len(walk.bitrates) == 1:
        return f"{next(iter(walk.bitrates)) // 1000} kbit/s (constant)"
    average = walk.audio_bytes * 8 / walk.duration / 1000 if walk.duration else 0
    return (f"{average:.0f} kbit/s average (variable, {min(walk.bitrates) // 1000} to "
            f"{max(walk.bitrates) // 1000})")


def audio_section(walk: FrameWalk) -> Section:
    """Technical facts about the audio stream."""
    first = walk.first
    section = Section("audio", "Audio")
    section.add("format", "Format", f"{first.version_name} {first.layer_name}")
    section.add("sample_rate", "Sample rate", f"{first.sample_rate} Hz")
    modes = sorted(walk.channel_modes) or [first.channel_mode]
    section.add("channels", "Channels", ", ".join(CHANNEL_MODES[m] for m in modes))
    section.add("bitrate", "Bitrate", _bitrate_text(walk))
    section.add("duration", "Duration", f"{walk.duration:.3f} s")
    section.add("frames", "Audio frames", walk.audio_frames)
    if first.copyright or first.original or first.emphasis:
        section.add("flags", "Header flags",
                    ", ".join(n for n, on in (("copyright", first.copyright),
                                              ("original", first.original)) if on)
                    + (f"; emphasis {EMPHASIS[first.emphasis]}" if first.emphasis else ""))
    return section


def info_section(info: InfoFrame) -> Section:
    """The Xing/Info or VBRI frame and its LAME tag."""
    section = Section("encoder_info", f"{info.kind} frame")
    section.add("kind", "Information frame",
                {"Xing": "Xing (variable bitrate)", "Info": "Info (constant bitrate)",
                 "VBRI": "VBRI (Fraunhofer, variable bitrate)"}[info.kind],
                note=f"offset {info.offset}, {info.length} bytes")
    if info.frames is not None:
        section.add("frames", "Frame count", info.frames)
    if info.bytes is not None:
        section.add("bytes", "Byte count", info.bytes)
    section.add("toc", "Seek table", "yes" if info.toc else "no")
    if info.quality is not None:
        section.add("quality", "Quality", info.quality)
    lame = info.lame
    if lame is not None:
        section.add("encoder", "Encoder", lame.encoder, note="LAME tag")
        section.add("encoder_delay", "Encoder delay", f"{lame.encoder_delay} samples")
        section.add("encoder_padding", "Encoder padding", f"{lame.encoder_padding} samples")
        if lame.lowpass:
            section.add("lowpass", "Lowpass filter", f"{lame.lowpass} Hz")
        section.add("tag_crc", "LAME tag CRC", "valid" if lame.crc_ok else "INVALID")
    return section


def junk_sections(walk: FrameWalk, data: bytes, warnings: list[str]) -> list[Section]:
    """Bytes between or after the frames that are not frames: hidden data if
    any byte is non-zero, padding otherwise. Decoders skip both."""
    hidden = Section("hidden_audio_data", "Hidden data in the audio stream")
    padding = Section("padding", "Padding in the audio stream")
    hidden_total = padding_total = 0
    for start, end in walk.junk:
        chunk = data[start:end]
        where = "after the last frame" if end == walk.end else (
            "before the first frame" if start == walk.start else "between frames")
        if chunk.count(0) == len(chunk):
            padding_total += len(chunk)
            if len(padding.fields) < 100:
                padding.add(f"at_{start}", f"At offset {start}", f"{len(chunk)} zero bytes",
                            note=where)
        else:
            hidden_total += len(chunk)
            if len(hidden.fields) < 100:
                hidden.add(f"at_{start}", f"At offset {start}", f"{len(chunk)} bytes",
                           sensitive=True, note=where)
    if walk.info is not None:
        hidden_info = walk.info.hidden_bytes(data)
        if hidden_info:
            hidden_total += hidden_info
            hidden.add("info_frame", f"In the {walk.info.kind} frame",
                       f"{hidden_info} non-zero bytes outside its fields", sensitive=True,
                       note=f"frame at offset {walk.info.offset}")
    out = []
    if hidden.fields:
        warnings.append(f"CRITICAL: {hidden_total} bytes of data hidden in the audio stream "
                        "(not audio; players skip them); this can hide anything")
        out.append(hidden)
    if padding.fields:
        warnings.append(f"{padding_total} bytes of zero padding in the audio stream")
        out.append(padding)
    return out
