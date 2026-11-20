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

from metaex.core import filetype
from metaex.core.errors import MalformedFileError
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
