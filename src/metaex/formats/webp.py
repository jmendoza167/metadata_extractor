"""WebP: walk the RIFF chunks and read the metadata they carry.

A WebP file is a RIFF container: "RIFF", a 4-byte little-endian size (the
file size minus 8), "WEBP", then chunks. Each chunk is a FourCC, a 4-byte
little-endian payload size, the payload and one padding byte if the size is
odd. Simple files hold one image chunk (VP8 = lossy, VP8L = lossless).
Extended files start with VP8X, whose flags announce the optional chunks:
ICCP (color profile), ANIM/ANMF (animation; each ANMF frame holds its own
image sub-chunks), ALPH (alpha for lossy images), EXIF and "XMP ".
Viewers ignore anything after the end the RIFF size gives.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from metaex.core import exif, filetype, icc, xmp
from metaex.core.errors import MalformedFileError
from metaex.core.result import Section

MAX_CHUNKS = 100_000
ANMF_HEADER_SIZE = 16

# VP8X flag bits
ICC_FLAG, ALPHA_FLAG, EXIF_FLAG, XMP_FLAG, ANIMATION_FLAG = 0x20, 0x10, 0x08, 0x04, 0x02
RESERVED_FLAGS = 0xC1
FLAG_CHUNKS = ((ICC_FLAG, "ICCP", "ICC profile"), (EXIF_FLAG, "EXIF", "EXIF"),
               (XMP_FLAG, "XMP ", "XMP"), (ANIMATION_FLAG, "ANIM", "animation"))

IMAGE_CHUNKS = ("VP8 ", "VP8L", "ALPH")
KNOWN_CHUNKS = {"VP8X", "VP8 ", "VP8L", "ALPH", "ANIM", "ANMF", "ICCP", "EXIF", "XMP "}
FRAME_CHUNKS = {"VP8 ", "VP8L", "ALPH"}
"""Sub-chunks an ANMF frame may contain."""


@dataclass
class Chunk:
    fourcc: str
    offset: int
    """Offset of the chunk header in the file."""
    size: int
    """Payload size, as stored."""
    data: bytes
    padding: bytes = b""
    """The padding byte after an odd-sized payload (b"" if none)."""
    children: list[Chunk] = field(default_factory=list)
    """ANMF: the frame's sub-chunks."""

    @property
    def end(self) -> int:
        return self.offset + 8 + self.size + len(self.padding)

    @property
    def label(self) -> str:
        return self.fourcc.strip() or repr(self.fourcc)


@dataclass
class WebpStructure:
    riff_size: int
    chunks: list[Chunk] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    end_of_image: int | None = None
    """Offset just past the RIFF data (8 + RIFF size), if the file is that long."""
    error: str | None = None
    """Set when walking stopped early because the data is malformed."""

    def first(self, fourcc: str) -> Chunk | None:
        return next((c for c in self.chunks if c.fourcc == fourcc), None)

    def all(self, fourcc: str) -> list[Chunk]:
        return [c for c in self.chunks if c.fourcc == fourcc]

    @property
    def flags(self) -> int | None:
        """VP8X flags, or None for a simple file (or a broken VP8X chunk)."""
        vp8x = self.chunks[0] if self.chunks and self.chunks[0].fourcc == "VP8X" else None
        return vp8x.data[0] if vp8x is not None and len(vp8x.data) >= 10 else None


def _fourcc(raw: bytes) -> str:
    return raw.decode("latin-1")


class _Walker:
    def __init__(self, data: bytes, result: WebpStructure) -> None:
        self.data = data
        self.result = result
        self.count = 0

    def chunks(self, start: int, end: int, where: str, out: list[Chunk]) -> None:
        """Append the chunks between ``start`` and ``end`` to ``out`` (so the
        ones found before a problem are kept); raises MalformedFileError."""
        pos = start
        data = self.data
        while pos < end:
            # Every chunk moves pos forward by at least 8 bytes.
            self.count += 1
            if self.count > MAX_CHUNKS:
                raise MalformedFileError(f"more than {MAX_CHUNKS} chunks")
            if pos + 8 > end:
                raise MalformedFileError(f"chunk header at offset {pos} is truncated{where}")
            fourcc = _fourcc(data[pos : pos + 4])
            size = int.from_bytes(data[pos + 4 : pos + 8], "little")
            if size > end - pos - 8:
                raise MalformedFileError(
                    f"{fourcc!r} chunk at offset {pos} is truncated{where}: needs {size} "
                    f"bytes, {end - pos - 8} available")
            payload = data[pos + 8 : pos + 8 + size]
            padding = b""
            if size % 2:
                if pos + 8 + size < end:
                    padding = data[pos + 8 + size : pos + 9 + size]
                    if padding != b"\x00":
                        self.result.warnings.append(
                            f"padding byte of the {fourcc!r} chunk at offset {pos} is not zero")
                else:
                    self.result.warnings.append(
                        f"{fourcc!r} chunk at offset {pos} has no padding byte{where}")
            chunk = Chunk(fourcc, pos, size, payload, padding)
            if fourcc == "ANMF" and not where:
                if size < ANMF_HEADER_SIZE:
                    raise MalformedFileError(f"ANMF chunk at offset {pos} is too short")
                out.append(chunk)
                self.chunks(pos + 8 + ANMF_HEADER_SIZE, pos + 8 + size,
                            f" (in the ANMF frame at offset {pos})", chunk.children)
            else:
                out.append(chunk)
            pos = chunk.end


def walk_chunks(data: bytes | bytearray | memoryview) -> WebpStructure:
    """Walk all chunks inside the RIFF container (and inside ANMF frames).

    Raises MalformedFileError only if the data doesn't start with a RIFF
    WEBP header. Anything malformed later stops the walk; the chunks found
    so far are kept and the problem is recorded in ``error`` and
    ``warnings``.
    """
    data = bytes(data)
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise MalformedFileError("not a WebP file: no RIFF WEBP header")
    riff_size = int.from_bytes(data[4:8], "little")
    result = WebpStructure(riff_size)
    end = 8 + riff_size
    walker = _Walker(data, result)
    try:
        if riff_size < 4:
            raise MalformedFileError(f"RIFF size {riff_size} is too small")
        if riff_size % 2:
            result.warnings.append(f"RIFF size {riff_size} is odd (it should be even)")
        if end > len(data):
            # Walk what is there, then report the truncation.
            result.error = (f"RIFF size says the file has {end} bytes, but it has "
                            f"{len(data)}; the file is truncated")
            walker.chunks(12, len(data), "", result.chunks)
        else:
            result.end_of_image = end
            walker.chunks(12, end, "", result.chunks)
    except MalformedFileError as exc:
        result.error = result.error or str(exc)
    if result.error:
        result.warnings.append(f"malformed WebP: {result.error}; data after this point was "
                               "not examined")
    _check_layout(result)
    return result


def _check_layout(result: WebpStructure) -> None:
    """Warnings for chunks in the wrong place and VP8X flags that don't match."""
    chunks = result.chunks
    for i, chunk in enumerate(chunks):
        if chunk.fourcc == "VP8X" and i != 0:
            result.warnings.append(f"VP8X chunk at offset {chunk.offset} is not the first chunk")
    flags = result.flags
    if flags is None:
        if chunks and chunks[0].fourcc == "VP8X":
            result.warnings.append("VP8X chunk is too short")
        return
    for bit, fourcc, name in FLAG_CHUNKS:
        present = result.first(fourcc) is not None
        if bool(flags & bit) != present:
            state = "set" if flags & bit else "not set"
            result.warnings.append(f"VP8X {name} flag is {state}, but the file "
                                   f"{'has no' if not present else 'has a'} {fourcc.strip()} "
                                   "chunk")


# -- image chunks ----------------------------------------------------------------------


def canvas_size(chunk: Chunk) -> tuple[int, int] | None:
    """Width and height from a VP8X, VP8 (key frame) or VP8L chunk, or None."""
    d = chunk.data
    if chunk.fourcc == "VP8X" and len(d) >= 10:
        return (1 + int.from_bytes(d[4:7], "little"), 1 + int.from_bytes(d[7:10], "little"))
    if chunk.fourcc == "VP8 " and len(d) >= 10 and not d[0] & 1 and d[3:6] == b"\x9d\x01\x2a":
        return (int.from_bytes(d[6:8], "little") & 0x3FFF,
                int.from_bytes(d[8:10], "little") & 0x3FFF)
    if chunk.fourcc == "VP8L" and len(d) >= 5 and d[0] == 0x2F:
        bits = int.from_bytes(d[1:5], "little")
        return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    return None


def _image_value(chunk: Chunk) -> str:
    size = canvas_size(chunk)
    kind = {"VP8 ": "lossy", "VP8L": "lossless", "ALPH": "alpha"}.get(chunk.fourcc, "")
    text = f"offset {chunk.offset}, {chunk.size} bytes"
    if kind:
        text += f", {kind}"
    if size is not None and chunk.fourcc != "VP8X":
        text += f", {size[0]} x {size[1]}"
    return text


# -- sections -------------------------------------------------------------------------


def exif_sections(structure: WebpStructure, warnings: list[str]) -> list[Section]:
    """EXIF, GPS and Thumbnail sections for the EXIF chunk(s), via core/exif."""
    out: list[Section] = []
    for i, chunk in enumerate(structure.all("EXIF")):
        suffix = "" if i == 0 else f" (chunk {i + 1})"
        out += exif.tiff_sections(exif.tiff_from_chunk(chunk.data), warnings, suffix)
    return out


def xmp_sections(structure: WebpStructure, warnings: list[str]) -> list[Section]:
    section = Section("xmp", "XMP")
    for i, chunk in enumerate(structure.all("XMP ")):
        label = "XMP packet" if i == 0 else f"XMP packet {i + 1}"
        xmp.add_to_section(section, chunk.data, label, "", warnings)
    return [section] if section.fields else []


def icc_sections(structure: WebpStructure, warnings: list[str]) -> list[Section]:
    """The embedded ICC color profile (ICCP), read with core/icc."""
    chunk = structure.first("ICCP")
    if chunk is None:
        return []
    try:
        return [icc.icc_section(chunk.data, warnings)]
    except MalformedFileError as exc:
        warnings.append(f"ICC profile not parsed: {exc}")
        section = Section("icc", "ICC profile")
        section.add("status", "ICC profile", "present but not parsed", note=str(exc))
        return [section]


def animation_sections(structure: WebpStructure, warnings: list[str]) -> list[Section]:
    """ANIM (loop count, background color) and the number of ANMF frames."""
    anim = structure.first("ANIM")
    frames = structure.all("ANMF")
    if anim is None and not frames:
        return []
    section = Section("animation", "Animation")
    section.add("frames", "Frames", len(frames))
    if anim is not None and len(anim.data) == 6:
        blue, green, red, alpha = anim.data[0:4]
        loops = int.from_bytes(anim.data[4:6], "little")
        section.add("loop_count", "Loop count", "forever" if loops == 0 else loops,
                    note="ANIM chunk")
        section.add("background", "Background color",
                    f"#{red:02x}{green:02x}{blue:02x}, alpha {alpha}")
    else:
        warnings.append("animation frames without a valid ANIM chunk")
    return [section]


def unknown_sections(structure: WebpStructure) -> list[Section]:
    """Chunks metaex doesn't know, at the top level or inside ANMF frames."""
    section = Section("unknown", "Unknown chunks")
    for chunk in structure.chunks:
        if chunk.fourcc not in KNOWN_CHUNKS:
            section.add(f"{chunk.fourcc}@{chunk.offset}", chunk.label,
                        f"{chunk.size} bytes at offset {chunk.offset}", sensitive=True,
                        note="contents not examined")
        for child in chunk.children:
            if child.fourcc not in FRAME_CHUNKS:
                section.add(f"{child.fourcc}@{child.offset}", child.label,
                            f"{child.size} bytes at offset {child.offset}", sensitive=True,
                            note=f"inside the ANMF frame at offset {chunk.offset}; "
                                 "contents not examined")
    return [section] if section.fields else []


def trailing_sections(structure: WebpStructure, data: bytes,
                      warnings: list[str]) -> list[Section]:
    """Bytes after the end of the RIFF data, which viewers ignore.

    Zero padding is a plain warning (same rule as JPEG, PNG and GIF);
    anything else is CRITICAL, because it can hide a whole file.
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


def structure_section(structure: WebpStructure) -> Section:
    section = Section("structure", "WebP structure")
    section.add("riff_size", "RIFF size", structure.riff_size)
    flags = structure.flags
    if flags is not None:
        names = [name for bit, _fourcc, name in FLAG_CHUNKS if flags & bit]
        if flags & ALPHA_FLAG:
            names.append("alpha")
        size = canvas_size(structure.chunks[0])
        section.add("vp8x", "Canvas and flags", f"{size[0]} x {size[1]}; flags: "
                    + (", ".join(names) or "none"))
    n = 0
    for chunk in structure.chunks:
        section.add(f"chunk_{n}", chunk.label, _image_value(chunk))
        n += 1
        for child in chunk.children:
            section.add(f"chunk_{n}", f"  {child.label}", _image_value(child))
            n += 1
    return section


# -- parse -------------------------------------------------------------------------


def parse(data: bytes | bytearray | memoryview) -> dict:
    """Parse a WebP file and return its warnings, report sections and structure.

    Raises MalformedFileError if the data has no RIFF WEBP header; problems
    later in the file become warnings.
    """
    data = bytes(data)
    structure = walk_chunks(data)
    warnings = list(structure.warnings)
    sections: list[Section] = []
    sections += exif_sections(structure, warnings)
    sections += xmp_sections(structure, warnings)
    sections += icc_sections(structure, warnings)
    sections += animation_sections(structure, warnings)
    sections += unknown_sections(structure)
    sections += trailing_sections(structure, data, warnings)
    return {
        "warnings": warnings,
        "sections": sections,
        "structure": structure_section(structure),
        "complete": structure.error is None,
    }
