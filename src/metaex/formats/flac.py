"""FLAC: metadata blocks, the audio frames after them and the tags around them.

A FLAC file is "fLaC", metadata blocks, then audio frames. Each block has a
4-byte header (a last-block flag, a 7-bit type, a 24-bit length): 0
STREAMINFO (sample rate, channels, bits, total samples, the MD5 of the
decoded audio), 1 PADDING, 2 APPLICATION, 3 SEEKTABLE, 4 VORBIS_COMMENT (a
vendor string and NAME=value fields, little-endian lengths), 5 CUESHEET,
6 PICTURE; 7-126 are reserved and 127 is invalid. Some taggers also put
an ID3v2 tag before "fLaC" and ID3v1 or APE tags after the audio.

Each audio frame starts with a sync code (0xFFF8 or 0xFFF9), a header
ending with a CRC-8, then one subframe per channel, and ends with a
CRC-16 of the whole frame. The frames are not walked one by one (that
would take a pass over every bit of the audio): the first frame must
follow the metadata, and the last frame is found from the end and parsed
subframe by subframe to know exactly where it ends, confirmed by its
CRC-16. Anything after it is not audio.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass, field

from metaex.core import id3
from metaex.core.audiotags import EndTags, end_tag_sections, find_end_tags
from metaex.core.crc import crc8, crc16
from metaex.core.embedded import Picture, picture_sections
from metaex.core.errors import MalformedFileError
from metaex.core.inflate import Budget
from metaex.core.result import Section

MAGIC = b"fLaC"
MAX_BLOCKS = 10_000
MAX_COMMENTS = 100_000
MAX_SHOWN_CHARS = 256 * 1024
MAX_FRAME_ATTEMPTS = 1000
"""Frame headers fully parsed while looking for the last frame."""
MAX_SYNC_CANDIDATES = 10_000_000

STREAMINFO, PADDING, APPLICATION, SEEKTABLE, VORBIS_COMMENT, CUESHEET, PICTURE = range(7)
BLOCK_NAMES = {STREAMINFO: "STREAMINFO", PADDING: "PADDING", APPLICATION: "APPLICATION",
               SEEKTABLE: "SEEKTABLE", VORBIS_COMMENT: "VORBIS_COMMENT", CUESHEET: "CUESHEET",
               PICTURE: "PICTURE"}
STREAMINFO_SIZE = 34
SEEKPOINT_SIZE = 18
CUESHEET_HEADER = 396
CUESHEET_TRACK = 36
CUESHEET_INDEX = 12
APPLICATION_NAMES = {
    b"riff": "RIFF chunks of the original WAV file (may include its metadata)",
    b"aiff": "AIFF chunks of the original AIFF file (may include its metadata)",
    b"ATCH": "FlacFile (attached files)", b"BSOL": "beSolo", b"BUGS": "Bugs Player",
    b"Cues": "GoldWave cue points", b"Fica": "CUE Splitter", b"Ftol": "flac-tools",
    b"MOTB": "MOTB MetaCzar", b"MPSE": "MP3 Stream Editor", b"MuML": "MusicML",
    b"RIFF": "Sound Devices RIFF chunk storage", b"SFFL": "Sound Font FLAC",
    b"SONY": "Sony Creative Software", b"SQEZ": "flacsqueeze", b"TtWv": "TwistedWave",
    b"UITS": "UITS embedding tools", b"aifC": "FLAC AIFF chunk storage",
    b"imag": "flac-image (an image file)", b"peem": "Parseable Embedded Extensible Metadata",
    b"qfst": "QFLAC Studio", b"tune": "TagTuner", b"xbat": "XBAT", b"xmcd": "xmcd",
}


@dataclass
class Block:
    type: int
    offset: int
    """Offset of the block header in the file."""
    data: bytes
    last: bool

    @property
    def name(self) -> str:
        if self.type in BLOCK_NAMES:
            return BLOCK_NAMES[self.type]
        return "invalid" if self.type == 127 else f"reserved type {self.type}"

    @property
    def end(self) -> int:
        return self.offset + 4 + len(self.data)


@dataclass
class StreamInfo:
    min_block: int
    max_block: int
    min_frame: int
    max_frame: int
    sample_rate: int
    channels: int
    bits: int
    total_samples: int
    md5: bytes


def parse_streaminfo(data: bytes) -> StreamInfo:
    if len(data) != STREAMINFO_SIZE:
        raise MalformedFileError(f"STREAMINFO block has {len(data)} bytes instead of 34")
    v = int.from_bytes(data[10:18], "big")
    info = StreamInfo(int.from_bytes(data[0:2], "big"), int.from_bytes(data[2:4], "big"),
                      int.from_bytes(data[4:7], "big"), int.from_bytes(data[7:10], "big"),
                      v >> 44, ((v >> 41) & 7) + 1, ((v >> 36) & 0x1F) + 1, v & 0xFFFFFFFFF,
                      data[18:34])
    if info.min_block < 16 or info.max_block < info.min_block:
        raise MalformedFileError(f"STREAMINFO has invalid block sizes ({info.min_block} to "
                                 f"{info.max_block})")
    if info.sample_rate == 0:
        raise MalformedFileError("STREAMINFO has sample rate 0")
    return info


# -- frames ----------------------------------------------------------------------------------

_BLOCK_SIZES = {1: 192, 2: 576, 3: 1152, 4: 2304, 5: 4608, **{n: 256 << (n - 8)
                                                                 for n in range(8, 16)}}
_SAMPLE_RATES = {1: 88200, 2: 176400, 3: 192000, 4: 8000, 5: 16000, 6: 22050, 7: 24000,
                 8: 32000, 9: 44100, 10: 48000, 11: 96000}
_SAMPLE_SIZES = {1: 8, 2: 12, 4: 16, 5: 20, 6: 24, 7: 32}


@dataclass
class FrameHeader:
    offset: int
    length: int
    """Bytes of the header, CRC-8 included."""
    variable: bool
    """Variable block size: ``number`` is the first sample, else the frame."""
    block_size: int
    channels: int
    assignment: int
    bits: int
    number: int


def frame_header(data: bytes, pos: int, info: StreamInfo | None) -> FrameHeader | None:
    """The frame header at ``pos`` if it is valid (sync code, no reserved
    values, a matching CRC-8), else None."""
    if pos + 6 > len(data) or data[pos] != 0xFF or data[pos + 1] & 0xFE != 0xF8:
        return None
    b2, b3 = data[pos + 2], data[pos + 3]
    bs_code, rate_code = b2 >> 4, b2 & 0xF
    assignment, size_code = b3 >> 4, (b3 >> 1) & 7
    if bs_code == 0 or rate_code == 15 or assignment > 10 or size_code == 3 or b3 & 1:
        return None
    p = pos + 4
    first = data[p]
    if first < 0x80:
        n_extra, number = 0, first
    elif first >= 0xC0 and first != 0xFF:
        n_extra = 8 - (first ^ 0xFF).bit_length() - 1
        number = first & (0x7F >> (n_extra + 1))
    else:
        return None
    if p + 1 + n_extra > len(data):
        return None
    for i in range(n_extra):
        cont = data[p + 1 + i]
        if cont & 0xC0 != 0x80:
            return None
        number = (number << 6) | (cont & 0x3F)
    p += 1 + n_extra
    if bs_code in (6, 7):
        width = 1 if bs_code == 6 else 2
        if p + width > len(data):
            return None
        block_size = int.from_bytes(data[p:p + width], "big") + 1
        p += width
    else:
        block_size = _BLOCK_SIZES[bs_code]
    if rate_code in (12, 13, 14):
        p += 1 if rate_code == 12 else 2
    if p >= len(data) or crc8(data[pos:p]) != data[p]:
        return None
    if size_code == 0:
        if info is None:
            return None
        bits = info.bits
    else:
        bits = _SAMPLE_SIZES[size_code]
    channels = assignment + 1 if assignment < 8 else 2
    return FrameHeader(pos, p + 1 - pos, bool(data[pos + 1] & 1), block_size, channels,
                       assignment, bits, number)


class _Bits:
    """Big-endian bit reader over data[start:end]; every read is checked."""

    def __init__(self, data: bytes, start: int, end: int) -> None:
        self.data, self.pos, self.end = data, start * 8, end * 8

    def read(self, n: int) -> int:
        if n == 0:
            return 0
        if self.pos + n > self.end:
            raise MalformedFileError("the frame is truncated")
        byte, off = self.pos >> 3, self.pos & 7
        count = (off + n + 7) >> 3
        value = int.from_bytes(self.data[byte:byte + count], "big") >> (count * 8 - off - n)
        self.pos += n
        return value & ((1 << n) - 1)

    def skip(self, n: int) -> None:
        if n < 0 or self.pos + n > self.end:
            raise MalformedFileError("the frame is truncated")
        self.pos += n

    def unary(self) -> int:
        """Count 0 bits up to the next 1 bit (which is consumed)."""
        count = 0
        data = self.data
        while self.pos < self.end:
            byte, off = self.pos >> 3, self.pos & 7
            b = data[byte] & (0xFF >> off)
            if b:
                zeros = 8 - b.bit_length() - off
                if self.pos + zeros >= self.end:
                    break
                self.pos += zeros + 1
                return count + zeros
            count += 8 - off
            self.pos = (byte + 1) * 8
        raise MalformedFileError("the frame is truncated")


def _residual(bits: _Bits, block_size: int, order: int) -> None:
    method = bits.read(2)
    if method > 1:
        raise MalformedFileError("reserved residual coding method")
    param_bits, escape = (4, 15) if method == 0 else (5, 31)
    partition_order = bits.read(4)
    partitions = 1 << partition_order
    if block_size % partitions or (block_size >> partition_order) < order:
        raise MalformedFileError("invalid residual partition order")
    for i in range(partitions):
        samples = (block_size >> partition_order) - (order if i == 0 else 0)
        param = bits.read(param_bits)
        if param == escape:
            bits.skip(samples * bits.read(5))
        else:
            for _ in range(samples):
                bits.unary()
                bits.skip(param)


def _subframe(bits: _Bits, block_size: int, sample_bits: int) -> None:
    if bits.read(1):
        raise MalformedFileError("subframe padding bit is set")
    kind = bits.read(6)
    if bits.read(1):  # wasted bits per sample
        sample_bits -= bits.unary() + 1
        if sample_bits <= 0:
            raise MalformedFileError("invalid wasted bits")
    if kind == 0:  # constant
        bits.skip(sample_bits)
    elif kind == 1:  # verbatim
        bits.skip(block_size * sample_bits)
    elif 8 <= kind <= 12:  # fixed predictor
        order = kind - 8
        bits.skip(order * sample_bits)
        _residual(bits, block_size, order)
    elif kind >= 32:  # linear predictor
        order = kind - 31
        bits.skip(order * sample_bits)
        precision = bits.read(4)
        if precision == 15:
            raise MalformedFileError("invalid LPC precision")
        bits.skip(5 + order * (precision + 1))
        _residual(bits, block_size, order)
    else:
        raise MalformedFileError(f"reserved subframe type {kind}")


def frame_end(data: bytes, header: FrameHeader, limit: int) -> int:
    """Where the frame ends (just past its CRC-16), parsing its subframes;
    raises MalformedFileError if it can't be parsed or its CRC-16 is wrong."""
    bits = _Bits(data, header.offset + header.length, limit)
    for channel in range(header.channels):
        side = ((header.assignment == 8 and channel == 1) or (header.assignment == 9
                                                              and channel == 0)
                or (header.assignment == 10 and channel == 1))
        _subframe(bits, header.block_size, header.bits + (1 if side else 0))
    end = (bits.pos + 7) // 8 + 2
    if end > limit:
        raise MalformedFileError("the frame is truncated")
    if crc16(data[header.offset:end - 2]) != int.from_bytes(data[end - 2:end], "big"):
        raise MalformedFileError(f"the frame at offset {header.offset} has a bad CRC-16")
    return end


# -- the layout ------------------------------------------------------------------------------


@dataclass
class FlacLayout:
    leading: list[id3.Tag]
    """ID3v2 tags before "fLaC"."""
    start: int
    """Offset of "fLaC"."""
    blocks: list[Block] = field(default_factory=list)
    info: StreamInfo | None = None
    audio_start: int | None = None
    """Offset of the first frame (just after the last metadata block)."""
    first_frame: FrameHeader | None = None
    end_tags: EndTags | None = None
    last_frame: FrameHeader | None = None
    audio_end: int | None = None
    """Just past the last frame."""
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def data_end(self) -> int:
        """Where the data before the tags at the end ends."""
        return self.end_tags.audio_end if self.end_tags is not None else 0

    def first(self, block_type: int) -> Block | None:
        return next((b for b in self.blocks if b.type == block_type), None)

    def all(self, block_type: int) -> list[Block]:
        return [b for b in self.blocks if b.type == block_type]


def _blocks(data: bytes, found: FlacLayout) -> None:
    pos = found.start + 4
    while True:
        if len(found.blocks) >= MAX_BLOCKS:
            raise MalformedFileError(f"more than {MAX_BLOCKS} metadata blocks")
        if pos + 4 > len(data):
            raise MalformedFileError(f"metadata block header at offset {pos} is truncated")
        head = data[pos]
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        if pos + 4 + length > len(data):
            raise MalformedFileError(f"metadata block at offset {pos} is truncated: needs "
                                     f"{length} bytes, {len(data) - pos - 4} available")
        block = Block(head & 0x7F, pos, data[pos + 4:pos + 4 + length], bool(head & 0x80))
        if block.type == 127:
            raise MalformedFileError(f"metadata block at offset {pos} has the invalid type 127")
        found.blocks.append(block)
        pos = block.end
        if block.last:
            break
    found.audio_start = pos


def _last_frame(data: bytes, found: FlacLayout) -> None:
    """Find the last frame: the frame header closest to the end that parses,
    with a matching CRC-16, ending at or before the tags at the end."""
    info, first = found.info, found.first_frame
    limit = found.data_end
    pos = limit
    attempts = candidates = 0
    while True:
        pos = data.rfind(b"\xff", found.audio_start, pos)
        if pos < 0:
            raise MalformedFileError("the end of the audio frames could not be found")
        candidates += 1
        if candidates > MAX_SYNC_CANDIDATES:
            raise MalformedFileError("the end of the audio frames could not be found")
        header = frame_header(data, pos, info)
        if (header is not None and header.variable == first.variable
                and header.channels == first.channels):
            attempts += 1
            if attempts > MAX_FRAME_ATTEMPTS:
                raise MalformedFileError("the end of the audio frames could not be found")
            try:
                end = frame_end(data, header, limit)
            except MalformedFileError:
                pass
            else:
                found.last_frame, found.audio_end = header, end
                return


def layout(data: bytes) -> FlacLayout:
    """Find the tags, metadata blocks and frames of a FLAC file.

    Raises MalformedFileError if there is no "fLaC" (after any ID3v2 tags)
    or an ID3v2 tag before it is broken; later problems are recorded in
    ``error`` and ``warnings``.
    """
    budget = Budget(id3.DECOMPRESS_LIMIT)
    leading, start = id3.leading_tags(data, budget)
    if data[start:start + 4] != MAGIC:
        raise MalformedFileError("not a FLAC file: no fLaC marker"
                                 + (" after the ID3v2 tags" if leading else ""))
    found = FlacLayout(leading, start)
    try:
        _blocks(data, found)
        if not found.blocks or found.blocks[0].type != STREAMINFO:
            raise MalformedFileError("the first metadata block is not STREAMINFO")
        found.info = parse_streaminfo(found.blocks[0].data)
        found.end_tags = find_end_tags(data, found.audio_start, budget)
        found.first_frame = frame_header(data, found.audio_start, found.info)
        if found.first_frame is None:
            raise MalformedFileError(f"no valid audio frame after the metadata blocks (at "
                                     f"offset {found.audio_start})")
        frame_end(data, found.first_frame, found.data_end)
        _last_frame(data, found)
        _check_samples(found)
    except MalformedFileError as exc:
        found.error = str(exc)
        found.warnings.append(f"malformed FLAC: {exc}")
    return found


def _check_samples(found: FlacLayout) -> None:
    """The last frame must end the stream STREAMINFO describes."""
    info, last = found.info, found.last_frame
    if last.variable:
        end_sample = last.number + last.block_size
    else:
        end_sample = last.number * info.max_block + last.block_size
    if info.total_samples and end_sample != info.total_samples:
        raise MalformedFileError(f"STREAMINFO says the audio has {info.total_samples} samples, "
                                 f"but the last frame ends at sample {end_sample} (the file "
                                 "may be cut off)")


# -- report sections ----------------------------------------------------------------------------


def streaminfo_section(info: StreamInfo) -> Section:
    section = Section("flac_streaminfo", "Audio")
    section.add("format", "Format", "FLAC")
    section.add("sample_rate", "Sample rate", f"{info.sample_rate} Hz")
    section.add("channels", "Channels", info.channels)
    section.add("bits", "Bits per sample", info.bits)
    if info.total_samples:
        section.add("duration", "Duration", f"{info.total_samples / info.sample_rate:.3f} s")
        section.add("samples", "Samples", info.total_samples)
    section.add("block_size", "Block size", info.max_block if info.min_block == info.max_block
                else f"{info.min_block} to {info.max_block}")
    section.add("md5", "MD5 of the audio", info.md5.hex() if any(info.md5) else "(not set)")
    return section


def _text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    if len(text) > MAX_SHOWN_CHARS:
        text = text[:MAX_SHOWN_CHARS] + f" [shortened: {len(text)} characters in total]"
    return text


@dataclass
class PictureBlock:
    type: int
    mime: str
    description: str
    width: int
    height: int
    data: bytes


def parse_picture(raw: bytes) -> PictureBlock:
    pos = 0

    def u32() -> int:
        nonlocal pos
        if pos + 4 > len(raw):
            raise MalformedFileError("PICTURE block is truncated")
        value = int.from_bytes(raw[pos:pos + 4], "big")
        pos += 4
        return value

    def chunk(n: int) -> bytes:
        nonlocal pos
        if n > len(raw) - pos:
            raise MalformedFileError("PICTURE block is truncated")
        pos += n
        return raw[pos - n:pos]

    kind = u32()
    mime = chunk(u32()).decode("latin-1")
    desc = chunk(u32()).decode("utf-8", errors="replace")
    width, height, _depth, _colors = u32(), u32(), u32(), u32()
    image = chunk(u32())
    return PictureBlock(kind, mime, desc, width, height, image)


def vorbis_comments(raw: bytes) -> tuple[str, list[tuple[str, bytes]]]:
    """(vendor string, [(NAME, value as UTF-8 bytes)]); raises MalformedFileError."""
    pos = 0

    def string() -> bytes:
        nonlocal pos
        if pos + 4 > len(raw):
            raise MalformedFileError("VORBIS_COMMENT block is truncated")
        n = int.from_bytes(raw[pos:pos + 4], "little")
        if n > len(raw) - pos - 4:
            raise MalformedFileError("VORBIS_COMMENT block is truncated")
        pos += 4 + n
        return raw[pos - n:pos]

    vendor = string().decode("utf-8", errors="replace")
    if pos + 4 > len(raw):
        raise MalformedFileError("VORBIS_COMMENT block is truncated")
    count = int.from_bytes(raw[pos:pos + 4], "little")
    pos += 4
    if count > MAX_COMMENTS:
        raise MalformedFileError(f"VORBIS_COMMENT block has more than {MAX_COMMENTS} fields")
    fields = []
    for _ in range(count):
        entry = string()
        name, _sep, value = entry.partition(b"=")
        fields.append((name.decode("ascii", errors="replace").upper(), value))
    return vendor, fields


def vorbis_sections(found: FlacLayout, warnings: list[str],
                    pictures: list[Picture]) -> list[Section]:
    out = []
    for block in found.all(VORBIS_COMMENT):
        section = Section("vorbis_comment", "Vorbis comments")
        try:
            vendor, fields = vorbis_comments(block.data)
        except MalformedFileError as exc:
            warnings.append(f"malformed FLAC: {exc}")
            section.add("raw", "VORBIS_COMMENT block", f"{len(block.data)} bytes",
                        sensitive=True, note=f"not examined: {exc}")
            out.append(section)
            continue
        section.add("VENDOR", "Vendor (encoder)", vendor)
        for n, (name, value) in enumerate(fields):
            if name == "METADATA_BLOCK_PICTURE":
                section.add(name, name, f"{len(value)} characters (base64 picture)",
                            sensitive=True)
                try:
                    picture = parse_picture(base64.b64decode(value.strip(), validate=True))
                except (MalformedFileError, binascii.Error, ValueError) as exc:
                    warnings.append(f"a METADATA_BLOCK_PICTURE field can't be decoded: {exc}")
                    continue
                pictures.append(Picture(f"Vorbis picture {n + 1}", picture.data,
                                        id3.picture_type_name(picture.type)))
                continue
            section.add(name, name, _text(value), sensitive=True)
        out.append(section)
    return out


def picture_block_sections(found: FlacLayout, warnings: list[str],
                           pictures: list[Picture]) -> None:
    for n, block in enumerate(found.all(PICTURE)):
        try:
            p = parse_picture(block.data)
        except MalformedFileError as exc:
            warnings.append(f"malformed FLAC: {exc}")
            continue
        desc = id3.picture_type_name(p.type) + f", {p.mime or 'no MIME type'}"
        if p.description:
            desc += f", {p.description!r}"
        pictures.append(Picture(f"PICTURE block {n + 1}", p.data, desc))


def application_sections(found: FlacLayout) -> list[Section]:
    section = Section("flac_application", "Application blocks")
    for block in found.all(APPLICATION):
        app_id = block.data[:4]
        name = APPLICATION_NAMES.get(app_id, "unknown application")
        label = app_id.decode("latin-1") if app_id.isascii() else app_id.hex()
        section.add(f"{label}@{block.offset}", f"APPLICATION {label!r}",
                    f"{len(block.data) - 4} bytes", sensitive=True, note=name)
    return [section] if section.fields else []


def seektable_sections(found: FlacLayout, warnings: list[str]) -> list[Section]:
    out = []
    for block in found.all(SEEKTABLE):
        section = Section("flac_seektable", "Seek table")
        if len(block.data) % SEEKPOINT_SIZE:
            warnings.append("malformed FLAC: SEEKTABLE length is not a multiple of 18")
        points = len(block.data) // SEEKPOINT_SIZE
        placeholders = sum(1 for i in range(points)
                           if block.data[i * 18:i * 18 + 8] == b"\xff" * 8)
        section.add("points", "Seek points", points - placeholders)
        if placeholders:
            section.add("placeholders", "Placeholders", placeholders)
        out.append(section)
    return out


@dataclass
class CueSheet:
    catalog: str
    lead_in: int
    is_cd: bool
    tracks: list[tuple[int, int, str, int]]
    """(number, offset, ISRC, index points)."""
    reserved_nonzero: int


def parse_cuesheet(raw: bytes) -> CueSheet:
    if len(raw) < CUESHEET_HEADER:
        raise MalformedFileError("CUESHEET block is truncated")
    catalog = raw[0:128].split(b"\x00", 1)[0].decode("latin-1")
    lead_in = int.from_bytes(raw[128:136], "big")
    is_cd = bool(raw[136] & 0x80)
    reserved = (raw[136] & 0x7F) + sum(1 for b in raw[137:395] if b)
    count = raw[395]
    pos = CUESHEET_HEADER
    tracks = []
    for _ in range(count):
        if pos + CUESHEET_TRACK > len(raw):
            raise MalformedFileError("CUESHEET block is truncated")
        t = raw[pos:pos + CUESHEET_TRACK]
        indexes = t[35]
        reserved += (t[21] & 0x3F) + sum(1 for b in t[22:35] if b)
        pos += CUESHEET_TRACK
        if pos + CUESHEET_INDEX * indexes > len(raw):
            raise MalformedFileError("CUESHEET block is truncated")
        for i in range(indexes):
            reserved += sum(1 for b in raw[pos + 9 + 12 * i:pos + 12 + 12 * i] if b)
        pos += CUESHEET_INDEX * indexes
        tracks.append((t[8], int.from_bytes(t[0:8], "big"),
                       t[9:21].split(b"\x00", 1)[0].decode("latin-1"), indexes))
    if pos != len(raw):
        reserved += len(raw) - pos
    return CueSheet(catalog, lead_in, is_cd, tracks, reserved)


def clean_cuesheet(raw: bytes) -> bytes:
    """The CUESHEET with its reserved bits and bytes set to zero (and nothing
    after its last track)."""
    sheet = parse_cuesheet(raw)
    out = bytearray(raw[:CUESHEET_HEADER])
    out[136] &= 0x80
    out[137:395] = bytes(258)
    pos = CUESHEET_HEADER
    for _number, _offset, _isrc, indexes in sheet.tracks:
        track = bytearray(raw[pos:pos + CUESHEET_TRACK])
        track[21] &= 0xC0
        track[22:35] = bytes(13)
        out += track
        pos += CUESHEET_TRACK
        for i in range(indexes):
            index = bytearray(raw[pos:pos + CUESHEET_INDEX])
            index[9:12] = bytes(3)
            out += index
            pos += CUESHEET_INDEX
    return bytes(out)


def cuesheet_sections(found: FlacLayout, warnings: list[str]) -> list[Section]:
    out = []
    for block in found.all(CUESHEET):
        section = Section("flac_cuesheet", "Cue sheet")
        try:
            sheet = parse_cuesheet(block.data)
        except MalformedFileError as exc:
            warnings.append(f"malformed FLAC: {exc}")
            continue
        if sheet.catalog:
            section.add("catalog", "Media catalog number", sheet.catalog)
        section.add("is_cd", "CD-DA", sheet.is_cd)
        section.add("tracks", "Tracks", len(sheet.tracks))
        for number, offset, isrc, _indexes in sheet.tracks[:200]:
            section.add(f"track_{number}", f"Track {number}",
                        f"sample {offset}" + (f", ISRC {isrc}" if isrc else ""))
        out.append(section)
    return out


def padding_sections(found: FlacLayout, warnings: list[str]) -> list[Section]:
    hidden = Section("hidden_block_data", "Hidden data in metadata blocks")
    padding = Section("audio_padding", "Padding blocks")
    total = 0
    for block in found.all(PADDING):
        nonzero = len(block.data) - block.data.count(0)
        if nonzero:
            total += nonzero
            hidden.add(f"padding_{block.offset}", f"PADDING block at offset {block.offset}",
                       f"{nonzero} non-zero bytes in {len(block.data)} bytes", sensitive=True)
        else:
            padding.add(f"padding_{block.offset}", f"PADDING block at offset {block.offset}",
                        f"{len(block.data)} zero bytes")
    for block in found.all(CUESHEET):
        try:
            reserved = parse_cuesheet(block.data).reserved_nonzero
        except MalformedFileError:
            continue
        if reserved:
            total += reserved
            hidden.add(f"cuesheet_{block.offset}", "Reserved bytes of the CUESHEET",
                       f"{reserved} non-zero bytes", sensitive=True)
    out = []
    if hidden.fields:
        warnings.append(f"CRITICAL: {total} bytes of data hidden in FLAC metadata blocks")
        out.append(hidden)
    if padding.fields:
        out.append(padding)
    return out


def unknown_sections(found: FlacLayout) -> list[Section]:
    section = Section("unknown", "Other metadata blocks")
    for block in found.blocks:
        if block.type not in BLOCK_NAMES:
            section.add(f"{block.type}@{block.offset}", f"Block of {block.name}",
                        f"{len(block.data)} bytes at offset {block.offset}", sensitive=True,
                        note="contents not examined")
    return [section] if section.fields else []


def trailing_sections(found: FlacLayout, data: bytes, warnings: list[str]) -> list[Section]:
    """Bytes between the last frame and the tags at the end (or the file end)."""
    if found.audio_end is None or found.audio_end >= found.data_end:
        return []
    trailing = data[found.audio_end:found.data_end]
    if trailing.count(0) == len(trailing):
        section = Section("audio_padding", "Data after the last frame")
        section.add("trailing", "After the last frame", f"{len(trailing)} zero bytes",
                    note=f"from offset {found.audio_end}")
        warnings.append(f"{len(trailing)} bytes of padding after the last audio frame")
        return [section]
    section = Section("audio_trailing", "Data after the end of the audio")
    section.add("size", "Size", f"{len(trailing)} bytes", sensitive=True)
    section.add("offset", "Starts at offset", found.audio_end)
    warnings.append(f"CRITICAL: {len(trailing)} bytes after the last audio frame; players "
                    "ignore them, so they can hide anything")
    return [section]


def structure_section(found: FlacLayout) -> Section:
    section = Section("structure", "FLAC structure")
    parts = [(t.offset, "ID3v2 tag", f"offset {t.offset}, {t.total_size} bytes")
             for t in found.leading]
    parts += [(b.offset, f"{b.name} block", f"offset {b.offset}, {len(b.data)} bytes"
               + (" (last)" if b.last else "")) for b in found.blocks]
    if found.audio_start is not None and found.audio_end is not None:
        parts.append((found.audio_start, "Audio frames",
                      f"offset {found.audio_start}, {found.audio_end - found.audio_start} bytes"))
    if found.end_tags is not None:
        parts += [(start, f"{kind} tag", f"offset {start}, {end - start} bytes")
                  for kind, start, end in found.end_tags.order]
    for n, (_offset, label, value) in enumerate(sorted(parts, key=lambda p: p[0])):
        section.add(f"part_{n}", label, value)
    return section


# -- parse ------------------------------------------------------------------------------------


def parse(data: bytes | bytearray | memoryview) -> dict:
    """Parse a FLAC file: tags, metadata blocks, and where the audio ends.

    Raises MalformedFileError if there is no "fLaC" marker; later problems
    become warnings, with ``complete`` False.
    """
    data = bytes(data)
    found = layout(data)
    warnings = list(found.warnings)
    sections: list[Section] = []
    pictures: list[Picture] = []
    if found.info is not None:
        sections.append(streaminfo_section(found.info))
    for n, tag in enumerate(found.leading):
        title = f"{tag.version} tag" + (f" {n + 1}" if len(found.leading) > 1 else "") + (
            f" (at offset {tag.offset})")
        sections.append(id3.tag_section(tag, "id3v2", title, warnings, pictures))
    sections += vorbis_sections(found, warnings, pictures)
    picture_block_sections(found, warnings, pictures)
    sections += application_sections(found)
    sections += seektable_sections(found, warnings)
    sections += cuesheet_sections(found, warnings)
    end_tags = found.end_tags
    if end_tags is not None:
        for tag in end_tags.id3v2:
            sections.append(id3.tag_section(tag, "id3v2", f"{tag.version} tag (appended at "
                                            "the end)", warnings, pictures))
        sections += end_tag_sections(end_tags, warnings, pictures)
    sections += picture_sections(pictures, warnings, "audio_pictures",
                                 "Embedded pictures and objects")
    all_id3 = found.leading + (end_tags.id3v2 if end_tags is not None else [])
    hidden = id3.padding_section(all_id3, warnings)
    if hidden is not None:
        sections.append(hidden)
    sections += padding_sections(found, warnings)
    sections += unknown_sections(found)
    sections += trailing_sections(found, data, warnings)
    complete = (found.error is None and all(t.error is None for t in all_id3)
                and not any(w.startswith("malformed FLAC") for w in warnings))
    return {"warnings": warnings, "sections": sections, "structure": structure_section(found),
            "complete": complete}


def audio_digest(data: bytes, found: FlacLayout) -> str:
    """SHA-256 of STREAMINFO and every byte from the first frame to the end of the last."""
    h = hashlib.sha256(found.blocks[0].data)
    h.update(data[found.audio_start:found.audio_end])
    return h.hexdigest()
