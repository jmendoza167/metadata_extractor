"""PNG: walk the chunks and read the metadata they carry.

A PNG file is an 8-byte signature followed by chunks. Each chunk is a
4-byte big-endian data length, a 4-byte type (ASCII letters), the data and
a CRC-32 of type + data. The first letter's case tells whether a chunk is
critical (uppercase: needed to display the image) or ancillary. The file
ends with the IEND chunk; anything after it is ignored by viewers.
"""

from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass, field
from datetime import datetime

from metaex.core import exif, filetype, icc, xmp
from metaex.core.errors import CannotCleanError, HiddenDataError, MalformedFileError, MetaexError
from metaex.core.inflate import Budget, DecompressionLimitError, inflate, inflate_stream
from metaex.core.result import Field, Section

SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_CHUNKS = 100_000
MAX_CHUNK_LENGTH = 2**31 - 1  # the PNG specification's limit


@dataclass
class Chunk:
    type: str
    """Four ASCII letters, e.g. "IHDR"."""
    offset: int
    """Offset of the chunk's length field in the file."""
    length: int
    data: bytes
    crc: int
    """CRC stored in the file."""
    crc_ok: bool

    @property
    def critical(self) -> bool:
        """Uppercase first letter: the image can't be displayed without it."""
        return self.type[0].isupper()

    @property
    def end(self) -> int:
        """Offset just past the chunk's CRC."""
        return self.offset + 12 + self.length


@dataclass
class PngStructure:
    chunks: list[Chunk] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    end_of_image: int | None = None
    """Offset just past the IEND chunk, or None if no IEND was found."""
    error: str | None = None
    """Set when walking stopped early because the data is malformed."""
    crc_errors: list[str] = field(default_factory=list)
    """One message per chunk whose CRC doesn't match its contents."""

    def first(self, chunk_type: str) -> Chunk | None:
        for chunk in self.chunks:
            if chunk.type == chunk_type:
                return chunk
        return None

    def all(self, chunk_type: str) -> list[Chunk]:
        return [chunk for chunk in self.chunks if chunk.type == chunk_type]


def _valid_type(raw: bytes) -> bool:
    return all(0x41 <= b <= 0x5A or 0x61 <= b <= 0x7A for b in raw)


def walk_chunks(data: bytes | bytearray | memoryview) -> PngStructure:
    """Walk all chunks from the signature to IEND.

    Raises MalformedFileError only if the data doesn't start with the PNG
    signature. Anything malformed later stops the walk; the chunks found so
    far are kept and the problem is recorded in ``error`` and ``warnings``.
    A CRC mismatch is recorded (``crc_errors`` and a warning) and the walk
    continues.
    """
    data = bytes(data)
    if not data.startswith(SIGNATURE):
        raise MalformedFileError("not a PNG file: no PNG signature at offset 0")
    result = PngStructure()
    n = len(data)
    pos = len(SIGNATURE)
    try:
        while True:
            if pos == n:
                result.warnings.append("missing IEND chunk; the file may be truncated")
                break
            if len(result.chunks) >= MAX_CHUNKS:
                raise MalformedFileError(f"more than {MAX_CHUNKS} chunks")
            if pos + 8 > n:
                raise MalformedFileError(f"chunk header at offset {pos} is truncated")
            length = int.from_bytes(data[pos : pos + 4], "big")
            raw_type = data[pos + 4 : pos + 8]
            if not _valid_type(raw_type):
                raise MalformedFileError(f"invalid chunk type {raw_type!r} at offset {pos}")
            chunk_type = raw_type.decode("ascii")
            if length > MAX_CHUNK_LENGTH:
                raise MalformedFileError(
                    f"{chunk_type} chunk at offset {pos} has invalid length {length}")
            if length + 12 > n - pos:
                raise MalformedFileError(
                    f"{chunk_type} chunk at offset {pos} is truncated: needs "
                    f"{length + 12} bytes, {n - pos} available")
            body = data[pos + 8 : pos + 8 + length]
            crc = int.from_bytes(data[pos + 8 + length : pos + 12 + length], "big")
            chunk = Chunk(chunk_type, pos, length, body, crc,
                          zlib.crc32(raw_type + body) == crc)
            if not chunk.crc_ok:
                message = (f"{chunk_type} chunk at offset {pos} has a bad CRC "
                           "(the file is damaged or was edited)")
                result.crc_errors.append(message)
                result.warnings.append(message)
            result.chunks.append(chunk)
            pos = chunk.end
            if chunk_type == "IEND":
                result.end_of_image = pos
                break
    except MalformedFileError as exc:
        result.error = str(exc)
        result.warnings.append(f"malformed PNG: {exc}; data after this point was not examined")
    if result.chunks and result.chunks[0].type != "IHDR":
        result.warnings.append(f"the first chunk is {result.chunks[0].type}, not IHDR")
    return result


# -- text chunks: tEXt, zTXt, iTXt ----------------------------------------------------

TEXT_CHUNKS = {"tEXt", "zTXt", "iTXt"}
MAX_SHOWN_CHARS = 256 * 1024
"""Longer text values are cut in the report (the full text is still removed)."""

# Keywords defined by the PNG specification.
STANDARD_KEYWORDS = {
    "Title", "Author", "Description", "Copyright", "Creation Time", "Software",
    "Disclaimer", "Warning", "Source", "Comment",
}
XMP_KEYWORD = "XML:com.adobe.xmp"
# Keywords AI image generators use for the prompt and generation settings.
AI_KEYWORDS = {
    "parameters": "Stable Diffusion (AUTOMATIC1111 / Forge): prompt and settings",
    "prompt": "ComfyUI: prompt graph (JSON)",
    "workflow": "ComfyUI: workflow (JSON)",
    "invokeai_metadata": "InvokeAI: prompt and settings (JSON)",
    "invokeai_graph": "InvokeAI: generation graph (JSON)",
    "sd-metadata": "InvokeAI (older versions): prompt and settings",
    "Dream": "InvokeAI (older versions): prompt",
}


@dataclass
class TextEntry:
    chunk_type: str
    offset: int
    keyword: str
    text: str | None
    """None if the text could not be read (see ``problem``)."""
    language: str = ""
    translated_keyword: str = ""
    compressed: bool = False
    problem: str | None = None


def _inflate_text(raw: bytes, entry: TextEntry, budget: Budget,
                  warnings: list[str]) -> bytes | None:
    where = f"{entry.chunk_type} chunk {entry.keyword!r} at offset {entry.offset}"
    try:
        result = inflate(raw, budget)
    except DecompressionLimitError as exc:
        entry.problem = f"compressed text not shown: {exc}"
        warnings.append(f"{where}: {exc}; its text is not shown")
        return None
    except MalformedFileError as exc:
        entry.problem = f"compressed text could not be read: {exc}"
        warnings.append(f"{where}: {exc}")
        return None
    if result.extra:
        warnings.append(f"{where}: {result.extra} bytes of extra data after the "
                        "compressed text")
    return result.data


def decode_text_chunk(chunk: Chunk, budget: Budget, warnings: list[str]) -> TextEntry:
    """Keyword and text of a tEXt, zTXt or iTXt chunk.

    Problems (missing separators, bad compression, the decompression limit)
    are reported in the entry's ``problem`` and in ``warnings``; they never
    raise.
    """
    keyword_raw, sep, rest = chunk.data.partition(b"\x00")
    entry = TextEntry(chunk.type, chunk.offset, keyword_raw.decode("latin-1"), None)
    if not sep:
        entry.problem = "malformed chunk: no keyword separator"
        warnings.append(f"{chunk.type} chunk at offset {chunk.offset} is malformed "
                        "(no keyword separator)")
        return entry
    if not 1 <= len(keyword_raw) <= 79:
        warnings.append(f"{chunk.type} chunk at offset {chunk.offset} has an invalid "
                        f"keyword length ({len(keyword_raw)})")
    if chunk.type == "tEXt":
        entry.text = rest.decode("latin-1")
    elif chunk.type == "zTXt":
        entry.compressed = True
        if not rest or rest[0] != 0:
            entry.problem = "unknown compression method"
            warnings.append(f"zTXt chunk at offset {chunk.offset} uses an unknown "
                            "compression method")
            return entry
        raw = _inflate_text(rest[1:], entry, budget, warnings)
        if raw is not None:
            entry.text = raw.decode("latin-1")
    else:  # iTXt
        if len(rest) < 2:
            entry.problem = "malformed chunk: too short"
            warnings.append(f"iTXt chunk at offset {chunk.offset} is too short")
            return entry
        flag, method = rest[0], rest[1]
        language, sep1, rest = rest[2:].partition(b"\x00")
        translated, sep2, body = rest.partition(b"\x00")
        if not (sep1 and sep2):
            entry.problem = "malformed chunk: missing separators"
            warnings.append(f"iTXt chunk at offset {chunk.offset} is malformed "
                            "(missing separators)")
            return entry
        entry.language = language.decode("latin-1")
        entry.translated_keyword = translated.decode("utf-8", errors="replace")
        if flag:
            entry.compressed = True
            if method != 0:
                entry.problem = "unknown compression method"
                warnings.append(f"iTXt chunk at offset {chunk.offset} uses an unknown "
                                "compression method")
                return entry
            body = _inflate_text(body, entry, budget, warnings)
            if body is None:
                return entry
        entry.text = body.decode("utf-8", errors="replace")
    return entry


def _shown(text: str) -> str:
    if len(text) <= MAX_SHOWN_CHARS:
        return text
    return text[:MAX_SHOWN_CHARS] + f" ... ({len(text) - MAX_SHOWN_CHARS} more characters)"


def _unique(section: Section, key: str) -> str:
    if section.get(key) is None:
        return key
    n = 2
    while section.get(f"{key} ({n})") is not None:
        n += 1
    return f"{key} ({n})"


def text_sections(structure: PngStructure, budget: Budget,
                  warnings: list[str]) -> list[Section]:
    """Text, AI generation data and XMP sections from the text chunks.

    Every keyword is shown, including unknown ones.
    """
    text = Section("text", "Text")
    ai = Section("ai", "AI generation data")
    xmp_section = Section("xmp", "XMP")
    for chunk in structure.chunks:
        if chunk.type not in TEXT_CHUNKS:
            continue
        entry = decode_text_chunk(chunk, budget, warnings)
        if entry.keyword == XMP_KEYWORD and entry.text is not None:
            n = sum(1 for f in xmp_section.fields if f.key.endswith(".size"))
            label = "XMP packet" if n == 0 else f"XMP packet {n + 1}"
            xmp.add_to_section(xmp_section, entry.text.encode("utf-8"), label, "", warnings)
            continue
        target = ai if entry.keyword in AI_KEYWORDS else text
        notes = [entry.chunk_type + (", compressed" if entry.compressed else "")]
        if entry.keyword in AI_KEYWORDS:
            notes.append(AI_KEYWORDS[entry.keyword])
        elif entry.keyword not in STANDARD_KEYWORDS and entry.keyword != XMP_KEYWORD:
            notes.append("non-standard keyword")
        if entry.language:
            notes.append(f"language {entry.language}")
        if entry.translated_keyword:
            notes.append(f"translated keyword: {entry.translated_keyword}")
        value = _shown(entry.text) if entry.text is not None else f"({entry.problem})"
        label = entry.keyword or "(empty keyword)"
        target.add(_unique(target, label), label, value, sensitive=True,
                   note="; ".join(notes))
    return [s for s in (text, ai, xmp_section) if s.fields]


# -- tIME ------------------------------------------------------------------------------


def time_sections(structure: PngStructure, warnings: list[str]) -> list[Section]:
    """tIME: when the image was last modified (UTC)."""
    chunks = structure.all("tIME")
    if not chunks:
        return []
    section = Section("time", "Time")
    for i, chunk in enumerate(chunks):
        key = "tIME" if i == 0 else f"tIME ({i + 1})"
        section.add(key, "Last modified", _time_value(chunk, warnings), sensitive=True,
                    note="tIME chunk, UTC")
    return [section]


def _time_value(chunk: Chunk, warnings: list[str]) -> str:
    if len(chunk.data) != 7:
        warnings.append(f"tIME chunk at offset {chunk.offset} has {len(chunk.data)} bytes "
                        "instead of 7")
        return f"(malformed: {len(chunk.data)} bytes)"
    year = int.from_bytes(chunk.data[:2], "big")
    month, day, hour, minute, second = chunk.data[2:]
    text = f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}Z"
    try:
        datetime(year, month, day, hour, minute, min(second, 59))  # 60 = leap second
        if second > 60:
            raise ValueError
    except ValueError:
        warnings.append(f"tIME chunk at offset {chunk.offset} holds an invalid date")
        return f"invalid date {text}"
    return text


# -- eXIf ------------------------------------------------------------------------------

def exif_tiff(chunk: Chunk) -> bytes:
    """The TIFF data of an eXIf chunk (with or without an "Exif\\0\\0" prefix)."""
    return exif.tiff_from_chunk(chunk.data)


def exif_sections(structure: PngStructure, warnings: list[str]) -> list[Section]:
    """EXIF, GPS and Thumbnail sections for the eXIf chunk(s)."""
    out: list[Section] = []
    for i, chunk in enumerate(structure.all("eXIf")):
        suffix = "" if i == 0 else f" (chunk {i + 1})"
        out += exif.tiff_sections(exif_tiff(chunk), warnings, suffix)
    return out


# -- iCCP ------------------------------------------------------------------------------

GENERIC_ICC_NAME = "ICC profile"


@dataclass
class IccpChunk:
    name: str
    compressed: bytes
    """The zlib stream as stored (after the name and compression method)."""


def split_iccp(chunk: Chunk) -> IccpChunk:
    """Profile name and compressed profile of an iCCP chunk; raises if malformed."""
    name, sep, rest = chunk.data.partition(b"\x00")
    if not sep or not rest:
        raise MalformedFileError(f"iCCP chunk at offset {chunk.offset} is malformed")
    if rest[0] != 0:
        raise MalformedFileError(f"iCCP chunk at offset {chunk.offset} uses an unknown "
                                 "compression method")
    return IccpChunk(name.decode("latin-1"), rest[1:])


def icc_sections(structure: PngStructure, budget: Budget,
                 warnings: list[str]) -> list[Section]:
    """The embedded ICC color profile (iCCP), read with core/icc."""
    chunk = structure.first("iCCP")
    if chunk is None:
        return []
    try:
        iccp = split_iccp(chunk)
    except MalformedFileError as exc:
        warnings.append(str(exc))
        section = Section("icc", "ICC profile")
        section.add("status", "ICC profile", "present but not parsed", note=str(exc))
        return [section]
    try:
        inflated = inflate(iccp.compressed, budget)
        if inflated.extra:
            warnings.append(f"iCCP chunk: {inflated.extra} bytes of extra data after the "
                            "compressed profile")
        section = icc.icc_section(inflated.data, warnings)
    except MetaexError as exc:
        warnings.append(f"ICC profile not parsed: {exc}")
        section = Section("icc", "ICC profile")
        section.add("status", "ICC profile", "present but not parsed", note=str(exc))
    section.fields.insert(0, Field("profile_name", "Profile name", iccp.name,
                                   sensitive=iccp.name != GENERIC_ICC_NAME))
    return [section]


# -- color and display chunks ----------------------------------------------------------

RENDERING_INTENTS = {0: "perceptual", 1: "relative colorimetric", 2: "saturation",
                     3: "absolute colorimetric"}
CICP_PRIMARIES = {1: "BT.709 / sRGB", 9: "BT.2020", 11: "DCI-P3", 12: "Display P3"}
CICP_TRANSFER = {1: "BT.709", 8: "linear", 13: "sRGB", 16: "PQ (HDR)", 18: "HLG (HDR)"}
COLOR_CHUNKS = ("sRGB", "gAMA", "cHRM", "cICP", "mDCv", "cLLI", "sBIT", "bKGD", "hIST",
                "sPLT", "pHYs", "oFFs", "pCAL", "sCAL")
_COLOR_LABELS = {
    "sRGB": "sRGB", "gAMA": "Gamma", "cHRM": "Chromaticities",
    "cICP": "Color coding (cICP)", "mDCv": "Mastering display", "cLLI": "Content light level",
    "sBIT": "Significant bits", "bKGD": "Background color", "hIST": "Palette histogram",
    "sPLT": "Suggested palette", "pHYs": "Pixel density", "oFFs": "Image offset",
    "pCAL": "Pixel calibration", "sCAL": "Physical scale",
}
_FIXED_SIZES = {"sRGB": 1, "gAMA": 4, "cHRM": 32, "cICP": 4, "mDCv": 24, "cLLI": 8,
                "pHYs": 9, "oFFs": 9}


def _u32s(data: bytes) -> list[int]:
    return [int.from_bytes(data[i : i + 4], "big") for i in range(0, len(data) - 3, 4)]


def _color_value(chunk: Chunk) -> str:
    """Readable value of a color/display chunk; raises MalformedFileError if malformed."""
    t, d = chunk.type, chunk.data
    if t in _FIXED_SIZES and len(d) != _FIXED_SIZES[t]:
        raise MalformedFileError(f"{len(d)} bytes instead of {_FIXED_SIZES[t]}")
    if t == "sRGB":
        return f"sRGB color space, {RENDERING_INTENTS.get(d[0], f'intent {d[0]}')} intent"
    if t == "gAMA":
        return f"gamma {_u32s(d)[0] / 100000:g}"
    if t == "cHRM":
        v = [x / 100000 for x in _u32s(d)]
        return (f"white {v[0]:g},{v[1]:g}; red {v[2]:g},{v[3]:g}; "
                f"green {v[4]:g},{v[5]:g}; blue {v[6]:g},{v[7]:g}")
    if t == "cICP":
        primaries = CICP_PRIMARIES.get(d[0], f"primaries {d[0]}")
        transfer = CICP_TRANSFER.get(d[1], f"transfer {d[1]}")
        return (f"{primaries}, {transfer}, matrix {d[2]}, "
                f"{'full' if d[3] else 'narrow'} range")
    if t == "mDCv":
        xy = [int.from_bytes(d[i : i + 2], "big") * 0.00002 for i in range(0, 16, 2)]
        top, low = (int.from_bytes(d[i : i + 4], "big") * 0.0001 for i in (16, 20))
        return (f"primaries {xy[0]:.4f},{xy[1]:.4f} {xy[2]:.4f},{xy[3]:.4f} "
                f"{xy[4]:.4f},{xy[5]:.4f}; white {xy[6]:.4f},{xy[7]:.4f}; "
                f"luminance {low:g} to {top:g} cd/m2")
    if t == "cLLI":
        max_cll, max_fall = (x * 0.0001 for x in _u32s(d))
        return f"max {max_cll:g} cd/m2, frame average max {max_fall:g} cd/m2"
    if t == "pHYs":
        x, y = _u32s(d[:8])
        if d[8] == 1:
            return f"{x * 0.0254:.0f} x {y * 0.0254:.0f} dpi ({x} x {y} pixels per meter)"
        return f"aspect ratio {x}:{y} (no unit)"
    if t == "sBIT":
        return "significant bits " + ", ".join(str(b) for b in d)
    if t == "bKGD":
        return "0x" + d.hex()
    if t == "hIST":
        return f"{len(d) // 2} entries"
    if t == "sPLT":
        name, sep, rest = d.partition(b"\x00")
        if not sep or not rest:
            raise MalformedFileError("no palette name separator")
        entry_size = 6 if rest[0] == 8 else 10
        return (f'"{name.decode("latin-1")}", {(len(rest) - 1) // entry_size} entries, '
                f"{rest[0]}-bit")
    if t == "oFFs":
        x = int.from_bytes(d[0:4], "big", signed=True)
        y = int.from_bytes(d[4:8], "big", signed=True)
        unit = {0: "pixels", 1: "micrometers"}.get(d[8], f"unit {d[8]}")
        return f"{x}, {y} {unit}"
    if t == "pCAL":
        name, sep, _rest = d.partition(b"\x00")
        if not sep:
            raise MalformedFileError("no calibration name separator")
        return f'"{name.decode("latin-1")}"'
    if t == "sCAL":
        if len(d) < 4:
            raise MalformedFileError("too short")
        unit = {1: "meters", 2: "radians"}.get(d[0], f"unit {d[0]}")
        width, _sep, height = d[1:].partition(b"\x00")
        return f"{width.decode('latin-1')} x {height.decode('latin-1')} {unit} per pixel"
    raise AssertionError(t)


def color_sections(structure: PngStructure, warnings: list[str]) -> list[Section]:
    """Color space, display and physical-size chunks."""
    section = Section("color", "Color and display")
    for chunk in structure.chunks:
        if chunk.type not in COLOR_CHUNKS:
            continue
        try:
            value = _color_value(chunk)
        except MalformedFileError as exc:
            warnings.append(f"{chunk.type} chunk at offset {chunk.offset} is malformed: {exc}")
            value = f"(malformed: {exc})"
        section.add(_unique(section, chunk.type), _COLOR_LABELS[chunk.type], value,
                    note=f"{chunk.type} chunk")
    return [section] if section.fields else []


# -- caBX, APNG, unknown chunks ---------------------------------------------------------

ANIMATION_CHUNKS = ("acTL", "fcTL", "fdAT")


def content_credentials_sections(structure: PngStructure) -> list[Section]:
    """caBX: C2PA Content Credentials, reported but not parsed."""
    chunks = structure.all("caBX")
    if not chunks:
        return []
    section = Section("content_credentials", "Content Credentials")
    for i, chunk in enumerate(chunks):
        key = "caBX" if i == 0 else f"caBX ({i + 1})"
        section.add(key, "C2PA manifest", f"{chunk.length} bytes at offset {chunk.offset}",
                    sensitive=True, note="not parsed; can contain edit history, "
                                         "software, account or identity information")
    return [section]


def animation_sections(structure: PngStructure, warnings: list[str]) -> list[Section]:
    """APNG: the animation control chunk and frame counts."""
    actl = structure.first("acTL")
    fctl = structure.all("fcTL")
    if actl is None and not fctl and structure.first("fdAT") is None:
        return []
    section = Section("animation", "Animation")
    if actl is not None and len(actl.data) == 8:
        frames, plays = _u32s(actl.data)
        section.add("frames", "Frames", frames, note="acTL chunk")
        section.add("plays", "Plays", "forever" if plays == 0 else plays)
    else:
        warnings.append("APNG animation chunks without a valid acTL chunk")
    section.add("frame_controls", "Frame control chunks (fcTL)", len(fctl))
    section.add("frame_data", "Frame data chunks (fdAT)", len(structure.all("fdAT")))
    return [section]


# -- image data: hidden bytes inside the compressed stream ---------------------------

MAX_CHECKED_IMAGE_SIZE = 1024**3
"""Image data is only checked while the decompressed sizes add up to at most 1 GiB."""
EXTRA_COUNT_LIMIT = 16 * 1024 * 1024
"""Extra decompressed bytes are counted up to this many ("at least ...")."""
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
VALID_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2),
         (0, 1, 1, 2))
"""Adam7 interlace passes: first column, first row, column step, row step."""


@dataclass(frozen=True)
class ImageHeader:
    width: int
    height: int
    depth: int
    color: int
    interlace: int


def image_header(chunk: Chunk) -> ImageHeader:
    """The IHDR fields; raises MalformedFileError if they are invalid."""
    d = chunk.data
    if len(d) != 13:
        raise MalformedFileError(f"IHDR chunk has {len(d)} bytes instead of 13")
    width, height = int.from_bytes(d[0:4], "big"), int.from_bytes(d[4:8], "big")
    depth, color, compression, filter_method, interlace = d[8:13]
    if not (1 <= width <= MAX_CHUNK_LENGTH and 1 <= height <= MAX_CHUNK_LENGTH):
        raise MalformedFileError(f"IHDR has an invalid image size {width} x {height}")
    if depth not in VALID_DEPTHS.get(color, ()):
        raise MalformedFileError(f"IHDR has an invalid bit depth {depth} for color type {color}")
    if compression != 0 or filter_method != 0 or interlace not in (0, 1):
        raise MalformedFileError("IHDR uses an unknown compression, filter or interlace method")
    return ImageHeader(width, height, depth, color, interlace)


def raw_size(header: ImageHeader, width: int | None = None, height: int | None = None) -> int:
    """Exact size of the decompressed image data: per scanline one filter byte
    plus the pixels, for each Adam7 pass if interlaced (empty passes have no
    scanlines). ``width``/``height`` override the IHDR size (APNG frames)."""
    width = header.width if width is None else width
    height = header.height if height is None else height
    bits = CHANNELS[header.color] * header.depth

    def rows(w: int, h: int) -> int:
        return h * (1 + (w * bits + 7) // 8) if w > 0 and h > 0 else 0

    if not header.interlace:
        return rows(width, height)
    return sum(rows(-(-(width - x0) // dx), -(-(height - y0) // dy))
               for x0, y0, dx, dy in ADAM7)


@dataclass
class ImageStream:
    """One zlib stream of image data: the default image (all IDAT chunks
    together) or one APNG frame (its fdAT chunks, without sequence numbers)."""
    name: str
    chunk_type: str
    width: int
    height: int
    offset: int
    """Offset of its first chunk."""
    parts: list[bytes] = field(default_factory=list)


def image_streams(structure: PngStructure, header: ImageHeader) -> list[ImageStream]:
    """The image data streams in file order; raises MalformedFileError if the
    IDAT/fdAT/fcTL layout is broken."""
    streams: list[ImageStream] = []
    idat: ImageStream | None = None
    frame: tuple[int, int, int] | None = None  # (frame number, width, height) from fcTL
    current: ImageStream | None = None  # the APNG frame being collected
    frames = 0
    previous = ""
    for chunk in structure.chunks:
        previous, before = chunk.type, previous
        if chunk.type == "fcTL":
            if len(chunk.data) != 26:
                raise MalformedFileError(f"fcTL chunk at offset {chunk.offset} has "
                                         f"{len(chunk.data)} bytes instead of 26")
            if frame is not None and idat is not None and current is None:
                raise MalformedFileError(f"animation frame {frame[0]} has no image data")
            frames += 1
            width, height = _u32s(chunk.data[4:12])
            if width == 0 or height == 0:
                raise MalformedFileError(f"fcTL chunk at offset {chunk.offset} has an "
                                         "empty frame")
            frame, current = (frames, width, height), None
        elif chunk.type == "IDAT":
            if idat is None:
                if streams and streams[-1].chunk_type == "fdAT":
                    raise MalformedFileError(f"IDAT chunk at offset {chunk.offset} after "
                                             "animation frames")
                idat = ImageStream("image data (IDAT)", "IDAT", header.width, header.height,
                                   chunk.offset)
                streams.append(idat)
                frame = None  # an fcTL just before IDAT describes the default image
            elif before != "IDAT":
                raise MalformedFileError(f"IDAT chunk at offset {chunk.offset} is not next "
                                         "to the other IDAT chunks")
            idat.parts.append(chunk.data)
        elif chunk.type == "fdAT":
            if len(chunk.data) < 4:
                raise MalformedFileError(f"fdAT chunk at offset {chunk.offset} is too short")
            if current is None:
                if frame is None:
                    raise MalformedFileError(f"fdAT chunk at offset {chunk.offset} without "
                                             "a frame control chunk (fcTL)")
                current = ImageStream(f"animation frame {frame[0]} (fdAT)", "fdAT",
                                      frame[1], frame[2], chunk.offset)
                streams.append(current)
            current.parts.append(chunk.data[4:])
    if frame is not None and current is None and idat is not None:
        raise MalformedFileError(f"animation frame {frame[0]} has no image data")
    return streams


@dataclass
class StreamCheck:
    stream: ImageStream
    expected: int
    """Decompressed size the image (or frame) needs."""
    size: int = 0
    """Decompressed bytes found (counted up to expected + EXTRA_COUNT_LIMIT)."""
    after_end: int = 0
    """Bytes after the end of the zlib stream."""
    problem: str | None = None
    """Why the stream is malformed (too short, invalid, not terminated)."""
    skipped: bool = False
    """Not decompressed: over the MAX_CHECKED_IMAGE_SIZE limit."""

    @property
    def extra(self) -> int:
        """Decompressed bytes beyond what the image needs."""
        return max(0, self.size - self.expected)

    @property
    def extra_capped(self) -> bool:
        return self.size >= self.expected + EXTRA_COUNT_LIMIT

    @property
    def hidden(self) -> bool:
        return bool(self.extra or self.after_end)


@dataclass
class ImageDataCheck:
    streams: list[StreamCheck] = field(default_factory=list)
    error: str | None = None
    """Set when the image data could not be checked at all (bad IHDR, layout)."""

    @property
    def hidden(self) -> list[StreamCheck]:
        return [s for s in self.streams if s.hidden]

    @property
    def problems(self) -> list[str]:
        out = [self.error] if self.error else []
        return out + [s.problem for s in self.streams if s.problem]

    @property
    def skipped(self) -> list[StreamCheck]:
        return [s for s in self.streams if s.skipped]


_CHECK_CACHE: dict[bytes, ImageDataCheck] = {}
CHECK_CACHE_SIZE = 8
"""Results of check_image_data() kept, so the image data is decompressed
once per command (show, strip and verify each read the file several times)."""


def _check_key(structure: PngStructure) -> bytes:
    """A digest of everything check_image_data() looks at: the first chunk,
    the image data chunks (IHDR, IDAT, fcTL, fdAT) in order and whether each
    IDAT follows another one (other chunks, which strip removes, don't
    matter otherwise)."""
    h = hashlib.sha256(f"{MAX_CHECKED_IMAGE_SIZE},{EXTRA_COUNT_LIMIT}".encode("ascii"))
    previous = ""
    for n, chunk in enumerate(structure.chunks):
        if n == 0 or chunk.type in ("IHDR", "IDAT", "fcTL", "fdAT"):
            joined = b"+" if chunk.type == "IDAT" and previous == "IDAT" else b"-"
            h.update(chunk.type.encode("ascii") + joined
                     + len(chunk.data).to_bytes(8, "big") + chunk.data)
        previous = chunk.type
    return h.digest()


def check_image_data(structure: PngStructure) -> ImageDataCheck:
    """Decompress each image data stream (discarding the output) and compare
    its size with the exact size the IHDR (or fcTL) implies.

    Extra decompressed bytes and bytes after the end of the zlib stream are
    ignored by viewers, so they can hide data. A stream that is too short or
    invalid is malformed. Streams are checked while their sizes add up to at
    most MAX_CHECKED_IMAGE_SIZE; later ones are marked ``skipped`` (strip
    refuses such files and verify reports them: hidden data can't be ruled
    out). The result is cached by content (see _CHECK_CACHE); treat it as
    read-only.
    """
    key = _check_key(structure)
    cached = _CHECK_CACHE.pop(key, None)
    if cached is None:
        cached = _check_image_data(structure)
    _CHECK_CACHE[key] = cached  # most recently used last
    while len(_CHECK_CACHE) > CHECK_CACHE_SIZE:
        del _CHECK_CACHE[next(iter(_CHECK_CACHE))]
    return cached


def _skipped_text(s: StreamCheck) -> str:
    return (f"{s.stream.name} was not checked for hidden data: the image needs "
            f"{s.expected} bytes uncompressed, more than the "
            f"{MAX_CHECKED_IMAGE_SIZE // 1024**3} GiB limit")


def _check_image_data(structure: PngStructure) -> ImageDataCheck:
    result = ImageDataCheck()
    first = structure.chunks[0] if structure.chunks else None
    if first is None or first.type != "IHDR":
        result.error = "no IHDR chunk at the start of the file"
        return result
    try:
        header = image_header(first)
        streams = image_streams(structure, header)
    except MalformedFileError as exc:
        result.error = str(exc)
        return result
    if not any(s.chunk_type == "IDAT" for s in streams):
        result.error = "no image data (IDAT) found"
        return result
    total = 0
    for stream in streams:
        check = StreamCheck(stream, raw_size(header, stream.width, stream.height))
        result.streams.append(check)
        total += check.expected
        if total > MAX_CHECKED_IMAGE_SIZE:
            check.skipped = True
            continue
        try:
            r = inflate_stream(stream.parts, check.expected + EXTRA_COUNT_LIMIT)
        except MalformedFileError as exc:
            check.problem = f"{stream.name} is not valid compressed data: {exc}"
            continue
        check.size, check.after_end = r.size, r.unused
        if r.size < check.expected:
            check.problem = (f"{stream.name} is too short: {r.size} of the "
                             f"{check.expected} bytes the image needs")
        elif not r.complete and not r.more:
            check.problem = f"{stream.name}: the compressed stream does not end properly"
    return result


def image_data_sections(check: ImageDataCheck, warnings: list[str]) -> list[Section]:
    """Hidden data found inside the image data streams, plus warnings for
    malformed and unchecked streams."""
    for problem in check.problems:
        warnings.append(f"malformed PNG: {problem}")
    for s in check.skipped:
        warnings.append(_skipped_text(s))
    section = Section("hidden_image_data", "Hidden data in image data")
    for i, s in enumerate(check.streams):
        name = s.stream.name
        if s.extra:
            amount = f"at least {s.extra}" if s.extra_capped else str(s.extra)
            section.add(f"{i}.extra", f"{name}: extra data",
                        f"{amount} bytes beyond the {s.expected} the image needs",
                        sensitive=True, note="decompressed, but not part of the picture")
            warnings.append(f"CRITICAL: {name} holds {amount} bytes more than the image "
                            "needs; viewers don't show them, so they can hide a whole file")
        if s.after_end:
            section.add(f"{i}.after_end", f"{name}: after the compressed data",
                        f"{s.after_end} bytes", sensitive=True,
                        note="after the end of the zlib stream")
            warnings.append(f"CRITICAL: {s.after_end} bytes after the end of the "
                            f"compressed {name}; viewers ignore them, so they can hide data")
    return [section] if section.fields else []


KNOWN_CHUNKS = ({"IHDR", "PLTE", "IDAT", "IEND", "tRNS", "tIME", "eXIf", "iCCP", "caBX"}
                | TEXT_CHUNKS | set(COLOR_CHUNKS) | set(ANIMATION_CHUNKS))


def unknown_sections(structure: PngStructure) -> list[Section]:
    section = Section("unknown", "Unknown chunks")
    for chunk in structure.chunks:
        if chunk.type not in KNOWN_CHUNKS:
            kind = "critical" if chunk.critical else "ancillary"
            section.add(f"{chunk.type}@{chunk.offset}", chunk.type,
                        f"{chunk.length} bytes at offset {chunk.offset}", sensitive=True,
                        note=f"{kind}; contents not examined")
    return [section] if section.fields else []


# -- report ------------------------------------------------------------------------


def structure_section(structure: PngStructure) -> Section:
    section = Section("structure", "PNG structure")
    for i, chunk in enumerate(structure.chunks):
        kind = "critical" if chunk.critical else "ancillary"
        value = f"offset {chunk.offset}, {chunk.length} bytes, {kind}"
        if not chunk.crc_ok:
            value += ", BAD CRC"
        section.add(f"chunk_{i}", chunk.type, value)
    return section


def trailing_sections(structure: PngStructure, data: bytes,
                      warnings: list[str]) -> list[Section]:
    """Bytes after the IEND chunk, which viewers ignore.

    Zero padding is a plain warning (same rule as JPEG); anything else is
    CRITICAL, because it can hide a whole file.
    """
    if structure.end_of_image is None:
        return []
    trailing = data[structure.end_of_image:]
    if not trailing:
        return []
    if trailing.strip(b"\x00") == b"":
        section = Section("padding", "Trailing data")
        section.add("size", "Size", f"{len(trailing)} bytes")
        section.add("offset", "Starts at offset", structure.end_of_image)
        section.add("content", "Content", "padding (only 0x00 bytes)")
        warnings.append(f"{len(trailing)} bytes of padding after the end of the image")
        return [section]
    section = Section("trailing", "Trailing data")
    section.add("size", "Size", f"{len(trailing)} bytes", sensitive=True)
    section.add("offset", "Starts at offset", structure.end_of_image)
    detected = filetype.detect(trailing)
    content = detected.name if detected.key != filetype.UNKNOWN.key else "unrecognized data"
    section.add("content", "Content", content, sensitive=True,
                note="detected from the first bytes; more may follow")
    warnings.append(f"CRITICAL: {len(trailing)} bytes after the end of the image "
                    f"({content}); this can hide a whole file")
    return [section]


# -- Stripping ---------------------------------------------------------------------

# Chunks that define the image: copied byte-for-byte, never re-encoded.
IMAGE_CHUNKS = ("IHDR", "PLTE", "tRNS", "IDAT", "IEND", "acTL", "fcTL", "fdAT")
# Color and display chunks kept by default, with the exact size each must have
# (a chunk of any other size is dropped, so nothing can hide in it).
KEPT_COLOR_SIZES = {"sRGB": 1, "gAMA": 4, "cHRM": 32, "cICP": 4, "mDCv": 24, "cLLI": 8,
                    "pHYs": 9}
PARANOID_COLOR = ("sRGB",)

# Report sections (and field keys; None = every field) that a stripped PNG may
# still contain. Anything else found by verify is a leftover.
KEPT_BY_DESIGN: dict[str, set[str] | None] = {
    "exif": {"byte_order", "Orientation"},
    "icc": None,
    "color": set(KEPT_COLOR_SIZES),
    "animation": None,
}


def make_chunk(chunk_type: str, data: bytes) -> bytes:
    """A new chunk with a freshly computed CRC."""
    raw_type = chunk_type.encode("ascii")
    return (len(data).to_bytes(4, "big") + raw_type + data
            + zlib.crc32(raw_type + data).to_bytes(4, "big"))


def _chunk_bytes(data: bytes, chunk: Chunk) -> bytes:
    """The chunk as stored: length, type, data and CRC."""
    return data[chunk.offset : chunk.end]


def checked_structure(data: bytes) -> PngStructure:
    """Walk a PNG for stripping or verification: anything malformed raises.

    Unlike parse(), which reports what it can of a damaged file, this
    refuses truncated files, bad CRCs, files without IHDR, IDAT or IEND, and
    critical chunks metaex doesn't know (dropping those could break the
    image).
    """
    structure = walk_chunks(data)
    if structure.error:
        raise MalformedFileError(structure.error)
    if structure.crc_errors:
        raise MalformedFileError(structure.crc_errors[0])
    if structure.end_of_image is None:
        raise MalformedFileError("missing IEND chunk; the file may be truncated")
    first = structure.chunks[0]
    if first.type != "IHDR" or first.length != 13:
        raise MalformedFileError("the file does not start with a valid IHDR chunk")
    for chunk in structure.chunks:
        if chunk.critical and chunk.type not in KNOWN_CHUNKS:
            raise MalformedFileError(
                f"unsupported critical chunk {chunk.type} at offset {chunk.offset}")
        if chunk.type == "IHDR" and chunk is not first:
            raise MalformedFileError(f"second IHDR chunk at offset {chunk.offset}")
    if structure.first("IDAT") is None:
        raise MalformedFileError("no image data (IDAT) found")
    return structure


def _record(chunk_type: str, payload: bytes) -> bytes:
    return chunk_type.encode("ascii") + len(payload).to_bytes(8, "big") + payload


def image_data(data: bytes | bytearray | memoryview) -> bytes:
    """The image-defining data, in file order, for comparing two files.

    IHDR, PLTE, tRNS and the APNG chunks (acTL, fcTL, fdAT) each contribute
    their type and data; consecutive IDAT chunks contribute their data joined
    together, so how it is split into chunks doesn't matter. Two files with
    the same image data decode to the same picture.
    Raises MalformedFileError like checked_structure().
    """
    structure = checked_structure(bytes(data))
    parts: list[bytes] = []
    idat: list[bytes] = []
    for chunk in structure.chunks:
        if chunk.type not in IMAGE_CHUNKS or chunk.type == "IEND":
            continue
        if chunk.type == "IDAT":
            idat.append(chunk.data)
            continue
        if idat:
            parts.append(_record("IDAT", b"".join(idat)))
            idat = []
        parts.append(_record(chunk.type, chunk.data))
    if idat:
        parts.append(_record("IDAT", b"".join(idat)))
    return b"".join(parts)


def original_orientation(structure: PngStructure) -> int | None:
    """EXIF Orientation (1-8) from the first eXIf chunk, or None."""
    chunk = structure.first("eXIf")
    if chunk is None:
        return None
    try:
        return exif.orientation(exif.parse_tiff(exif_tiff(chunk)))
    except MalformedFileError:
        return None


def minimal_orientation_exif(orientation: int) -> bytes:
    """A new eXIf chunk holding only the Orientation tag."""
    return make_chunk("eXIf", exif.minimal_orientation_tiff(orientation))


def usable_icc_profile(chunk: Chunk) -> bytes | None:
    """The compressed profile to keep from an iCCP chunk, or None to drop it.

    The original compressed data is kept as it is when it decompresses
    cleanly to something that looks like a real ICC profile. If extra bytes
    follow the compressed stream, the profile is compressed again so they
    are not carried over. Broken profiles are dropped.
    """
    try:
        iccp = split_iccp(chunk)
        inflated = inflate(iccp.compressed, Budget())
    except MetaexError:
        return None
    profile = inflated.data
    if len(profile) < 128 or profile[36:40] != b"acsp":
        return None
    return iccp.compressed if inflated.extra == 0 else zlib.compress(profile, 9)


def iccp_chunk(compressed: bytes) -> bytes:
    """A new iCCP chunk with the generic profile name."""
    return make_chunk("iCCP", GENERIC_ICC_NAME.encode("latin-1") + b"\x00\x00" + compressed)


CAN_REBUILD_IMAGE_DATA = True
"""strip() accepts rebuild_image_data=True (the --rebuild-image-data option)."""
REBUILT_IDAT_SIZE = 64 * 1024
"""Size of the IDAT chunks written when the image data is rebuilt."""


def _hidden_description(s: StreamCheck) -> str:
    parts = []
    if s.extra:
        parts.append(f"{'at least ' if s.extra_capped else ''}{s.extra} extra bytes")
    if s.after_end:
        parts.append(f"{s.after_end} bytes after the compressed data")
    return " and ".join(parts)


def _refuse_hidden(check: ImageDataCheck, rebuild: bool) -> StreamCheck | None:
    """The IDAT stream to rebuild, or None if nothing is hidden; raises
    HiddenDataError if the hidden data can't (or may not) be removed."""
    if not check.hidden:
        return None
    for s in check.hidden:
        if s.stream.chunk_type != "IDAT":
            raise HiddenDataError(
                f"the {s.stream.name} hides {_hidden_description(s)} that viewers don't "
                "show; metaex can't rebuild animation frames, so this file can't be cleaned")
    s = check.hidden[0]
    if not rebuild:
        raise HiddenDataError(
            f"the {s.stream.name} hides {_hidden_description(s)} that viewers don't show. "
            "Copying the image data would keep them. To remove them, run again with "
            "--rebuild-image-data: the image data is then decompressed and compressed "
            "again without them (PNG compression is lossless, so the pixels stay identical)")
    return s


def rebuilt_idat_chunks(s: StreamCheck) -> bytes:
    """New IDAT chunks holding exactly the bytes the image needs, compressed
    again with zlib level 9. Works piece by piece; the decompressed image is
    never held in memory at once."""
    compressor = zlib.compressobj(9)
    compressed: list[bytes] = []
    result = inflate_stream(s.stream.parts, s.expected,
                            lambda piece: compressed.append(compressor.compress(piece)))
    if result.size != s.expected:
        raise MalformedFileError(f"{s.stream.name} is too short to rebuild")
    compressed.append(compressor.flush())
    stream = b"".join(compressed)
    return b"".join(make_chunk("IDAT", stream[i : i + REBUILT_IDAT_SIZE])
                    for i in range(0, len(stream), REBUILT_IDAT_SIZE))


def decoded_image_digest(data: bytes | bytearray | memoryview) -> str | None:
    """SHA-256 of the image data with the IDAT stream decompressed.

    Like image_data(), but the default image counts as the exact bytes the
    image needs after decompression, so a file whose image data was
    compressed again (see --rebuild-image-data) has the same digest as the
    original: same digest = identical pixels. None if the image data can't
    be decompressed and checked (malformed, or over MAX_CHECKED_IMAGE_SIZE).
    """
    structure = checked_structure(bytes(data))
    check = check_image_data(structure)
    idat = next((s for s in check.streams if s.stream.chunk_type == "IDAT"), None)
    if check.problems or idat is None or idat.skipped:
        return None
    digest = hashlib.sha256()
    pixels = hashlib.sha256()
    inflate_stream(idat.stream.parts, idat.expected, pixels.update)
    in_idat = False
    for chunk in structure.chunks:
        if chunk.type == "IDAT":
            if not in_idat:
                digest.update(_record("IDAT", pixels.digest()))
            in_idat = True
            continue
        in_idat = False
        if chunk.type in IMAGE_CHUNKS and chunk.type != "IEND":
            digest.update(_record(chunk.type, chunk.data))
    return digest.hexdigest()


def strip(data: bytes | bytearray | memoryview, *, keep_orientation: bool = True,
          paranoid: bool = False, rebuild_image_data: bool = False) -> bytes:
    """Return a copy of the PNG with all metadata removed (whitelist).

    Kept byte-for-byte and in their original order: IHDR, PLTE, tRNS, every
    IDAT, IEND and the APNG chunks (acTL, fcTL, fdAT). Kept by default: the
    color chunks sRGB, gAMA, cHRM, cICP, mDCv and cLLI, the pixel density
    (pHYs), and the ICC profile (iCCP), rewritten with the generic name "ICC
    profile" and the same compressed profile. If the original EXIF
    Orientation is 2-8, a new eXIf chunk with only that tag is written unless
    ``keep_orientation`` is False. ``paranoid`` also drops iCCP, gAMA, cHRM,
    cICP, mDCv, cLLI and pHYs (sRGB stays). Everything else is dropped: all
    text, tIME, eXIf, caBX, bKGD, sPLT, hIST, sBIT, unknown chunks and any
    data after IEND. Only the chunks metaex writes itself get a new CRC.

    If the IDAT image data hides extra bytes (see check_image_data()), the
    file is refused with HiddenDataError, unless ``rebuild_image_data`` is
    set: then the IDAT chunks are replaced by new ones holding exactly the
    bytes the image needs, compressed again with zlib level 9 (the pixels
    are identical; only their compressed form changes). Hidden data in APNG
    frames (fdAT) is always refused.

    Raises MalformedFileError for malformed, damaged (bad CRC), truncated or
    unsupported input, and for image data that is too short or invalid;
    nothing is ever partly stripped.
    """
    data = bytes(data)
    structure = checked_structure(data)
    check = check_image_data(structure)
    if check.problems:
        raise MalformedFileError(check.problems[0])
    if check.skipped:
        raise CannotCleanError(f"{_skipped_text(check.skipped[0])}, so data hidden in it "
                               "can't be ruled out")
    rebuild = _refuse_hidden(check, rebuild_image_data)
    new_idat = rebuilt_idat_chunks(rebuild) if rebuild is not None else None
    keep_color = set(PARANOID_COLOR) if paranoid else set(KEPT_COLOR_SIZES)
    orientation = original_orientation(structure)
    new_exif = b""
    if keep_orientation and orientation is not None and orientation != 1:
        new_exif = minimal_orientation_exif(orientation)

    out = bytearray(SIGNATURE)
    written: set[str] = set()
    for chunk in structure.chunks:
        if chunk.type == "IDAT" and new_idat is not None:
            if "IDAT" not in written:  # the rebuilt image data replaces all IDAT chunks
                out += new_idat
                written.add("IDAT")
        elif chunk.type in IMAGE_CHUNKS:
            out += _chunk_bytes(data, chunk)
            if chunk.type == "IHDR":
                out += new_exif
        elif chunk.type in keep_color and chunk.type not in written:
            if chunk.length == KEPT_COLOR_SIZES[chunk.type]:
                out += _chunk_bytes(data, chunk)
                written.add(chunk.type)
        elif chunk.type == "iCCP" and not paranoid and "iCCP" not in written:
            written.add("iCCP")
            compressed = usable_icc_profile(chunk)
            if compressed is not None:
                out += iccp_chunk(compressed)
        # Anything else is metadata (or unknown) and is dropped.
    cleaned = bytes(out)
    # Self-check: the output must be a well-formed PNG with identical image
    # data (identical pixels, if the image data was rebuilt) and nothing hidden.
    if new_idat is None:
        same = image_data(cleaned) == image_data(data)
    else:
        after = check_image_data(checked_structure(cleaned))
        same = (not after.hidden and not after.problems
                and decoded_image_digest(cleaned) == decoded_image_digest(data))
    if not same:
        raise MalformedFileError("internal check failed: image data changed while stripping")
    return cleaned


# -- Verification -------------------------------------------------------------------


def _iccp_problem(chunk: Chunk) -> str | None:
    """Why an iCCP chunk is not one strip() would write; None if it is."""
    try:
        iccp = split_iccp(chunk)
        inflated = inflate(iccp.compressed, Budget())
    except MetaexError as exc:
        return f"ICC profile can't be read ({exc})"
    if iccp.name != GENERIC_ICC_NAME:
        return "ICC profile has a non-generic name"
    if inflated.extra:
        return f"{inflated.extra} extra bytes after the compressed ICC profile"
    profile = inflated.data
    if len(profile) < 128 or profile[36:40] != b"acsp":
        return "not a valid ICC profile"
    return None


def verify_details(cleaned: bytes, original: bytes | None = None) -> dict:
    """PNG-specific verification, beyond the field-level whitelist.

    - The file must be complete, with a valid CRC on every chunk.
    - Chunk whitelist: only image chunks, one of each kept color chunk (with
      its exact size), one Orientation-only eXIf and one iCCP with the
      generic name and a cleanly compressed profile may remain. Anything
      else is a leftover.
    - SHA-256 of the image data (IHDR, PLTE, tRNS, IDAT data and APNG
      chunks); compared with the original's if one is given. If they differ
      because the image data was rebuilt (--rebuild-image-data), the
      decompressed image data is compared instead (decoded_image_digest());
      equal means identical pixels, reported as ``image_data_rebuilt``.
    """
    out: dict = {"leftovers": [], "problems": [], "image_sha256": None,
                 "original_image_sha256": None, "image_data_match": None,
                 "image_data_rebuilt": False}
    try:
        image = image_data(cleaned)
    except MalformedFileError as exc:
        out["problems"].append(f"not a complete, undamaged PNG: {exc}")
        return out
    out["image_sha256"] = hashlib.sha256(image).hexdigest()
    # Hidden data inside the image data is reported by parse() as a leftover
    # section; malformed image data is a problem.
    check = check_image_data(walk_chunks(cleaned))
    out["problems"] += [f"image data is malformed: {p}" for p in check.problems]
    out["problems"] += [_skipped_text(s) for s in check.skipped]

    seen: set[str] = set()
    for chunk in walk_chunks(cleaned).chunks:
        if chunk.type in IMAGE_CHUNKS:
            continue
        first = chunk.type not in seen
        seen.add(chunk.type)
        if chunk.type in KEPT_COLOR_SIZES and first:
            expected = KEPT_COLOR_SIZES[chunk.type]
            if chunk.length == expected:
                continue
            problem = f"{chunk.type} should have {expected} bytes"
        elif chunk.type == "eXIf" and first:
            if exif.is_orientation_only_tiff(chunk.data):
                continue
            problem = "eXIf contains more than the Orientation tag, or unused bytes"
        elif chunk.type == "iCCP" and first:
            problem = _iccp_problem(chunk)
            if problem is None:
                continue
        else:
            problem = f"{chunk.type} chunk is not allowed in a cleaned PNG"
        out["leftovers"].append(("PNG structure", f"{chunk.type} at offset {chunk.offset}",
                                 f"{problem} ({chunk.length} bytes)"))

    if original is not None:
        try:
            original_image = image_data(original)
        except MalformedFileError as exc:
            out["problems"].append(f"original is not a complete, undamaged PNG ({exc}); "
                                   "image data not compared")
        else:
            out["original_image_sha256"] = hashlib.sha256(original_image).hexdigest()
            out["image_data_match"] = original_image == image
            if original_image != image:
                # Rebuilt image data (compressed again): compare the pixels.
                ours, theirs = decoded_image_digest(cleaned), decoded_image_digest(original)
                if ours is not None and ours == theirs:
                    out["image_data_match"] = True
                    out["image_data_rebuilt"] = True
                    out["image_sha256"] = out["original_image_sha256"] = ours
                else:
                    out["problems"].append("image data differs from the original")
    return out


# -- parse -------------------------------------------------------------------------


def parse(data: bytes | bytearray | memoryview) -> dict:
    """Parse a PNG and return its warnings, report sections and structure.

    Raises MalformedFileError if the data is not a PNG at all; problems
    inside the file (bad CRCs, truncated chunks, decompression over the
    limit) become warnings.
    """
    data = bytes(data)
    structure = walk_chunks(data)
    warnings = list(structure.warnings)
    budget = Budget()  # shared by all compressed text and the ICC profile
    sections: list[Section] = []
    sections += text_sections(structure, budget, warnings)
    sections += exif_sections(structure, warnings)
    sections += time_sections(structure, warnings)
    sections += icc_sections(structure, budget, warnings)
    sections += color_sections(structure, warnings)
    sections += content_credentials_sections(structure)
    sections += animation_sections(structure, warnings)
    sections += unknown_sections(structure)
    if structure.error is None:
        sections += image_data_sections(check_image_data(structure), warnings)
    sections += trailing_sections(structure, data, warnings)
    return {
        "warnings": warnings,
        "sections": sections,
        "structure": structure_section(structure),
        "complete": structure.error is None,
    }


def removed_sizes(data: bytes | bytearray | memoryview) -> dict[str, int]:
    """Byte sizes for the strip summary: data after IEND ("trailing") and
    bytes hidden inside the image data ("hidden_image_data")."""
    data = bytes(data)
    structure = walk_chunks(data)
    sizes: dict[str, int] = {}
    if structure.end_of_image is not None and len(data) > structure.end_of_image:
        sizes["trailing"] = len(data) - structure.end_of_image
    if structure.error is None:
        hidden = sum(s.extra + s.after_end for s in check_image_data(structure).hidden)
        if hidden:
            sizes["hidden_image_data"] = hidden
    return sizes
