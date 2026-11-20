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

from metaex.core import filetype, xmp
from metaex.core.errors import MalformedFileError
from metaex.core.inflate import Budget, DecompressionLimitError, inflate
from metaex.core.result import Section

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
