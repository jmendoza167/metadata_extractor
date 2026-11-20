"""PNG: walk the chunks and read the metadata they carry.

A PNG file is an 8-byte signature followed by chunks. Each chunk is a
4-byte big-endian data length, a 4-byte type (ASCII letters), the data and
a CRC-32 of type + data. The first letter's case tells whether a chunk is
critical (uppercase: needed to display the image) or ancillary. The file
ends with the IEND chunk; anything after it is ignored by viewers.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from datetime import datetime

from metaex.core import exif, filetype, icc, xmp
from metaex.core.errors import MalformedFileError, MetaexError
from metaex.core.inflate import Budget, DecompressionLimitError, inflate
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

EXIF_PREFIX = b"Exif\x00\x00"


def exif_tiff(chunk: Chunk) -> bytes:
    """The TIFF data of an eXIf chunk.

    The specification says the chunk holds TIFF data directly; some
    writers add the JPEG-style "Exif\\0\\0" prefix, which is accepted too.
    """
    data = chunk.data
    return data[len(EXIF_PREFIX):] if data.startswith(EXIF_PREFIX) else data


def exif_sections(structure: PngStructure, warnings: list[str]) -> list[Section]:
    """EXIF, GPS and Thumbnail sections for the eXIf chunk(s)."""
    out: list[Section] = []
    for i, chunk in enumerate(structure.all("eXIf")):
        suffix = "" if i == 0 else f" (chunk {i + 1})"
        try:
            st = exif.parse_tiff(exif_tiff(chunk))
        except MalformedFileError as exc:
            warnings.append(f"EXIF{suffix} not parsed: {exc}")
            section = Section("exif", "EXIF" + suffix)
            section.add("status", "EXIF", "present but not parsed", sensitive=True,
                        note=str(exc))
            out.append(section)
            continue
        warnings.extend(f"EXIF{suffix}: {w}" for w in st.warnings)
        sections = exif.exif_sections(st, warnings)
        for section in sections:
            section.title += suffix
        out += sections
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
    """Bytes after the IEND chunk, which viewers ignore."""
    if structure.end_of_image is None:
        return []
    trailing = data[structure.end_of_image:]
    if not trailing:
        return []
    section = Section("trailing", "Trailing data")
    section.add("size", "Size", f"{len(trailing)} bytes", sensitive=True)
    section.add("offset", "Starts at offset", structure.end_of_image)
    detected = filetype.detect(trailing)
    content = detected.name if detected.key != filetype.UNKNOWN.key else "unrecognized data"
    if trailing.strip(b"\x00") == b"":
        content = "padding (only 0x00 bytes)"
    section.add("content", "Content", content, sensitive=True,
                note="detected from the first bytes; more may follow")
    warnings.append(f"CRITICAL: {len(trailing)} bytes after the end of the image "
                    f"({content}); this can hide a whole file")
    return [section]


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
    sections += trailing_sections(structure, data, warnings)
    return {
        "warnings": warnings,
        "sections": sections,
        "structure": structure_section(structure),
        "complete": structure.error is None,
    }
