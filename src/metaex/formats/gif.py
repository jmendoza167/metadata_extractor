"""GIF: walk the blocks and read the metadata they carry.

A GIF file is a 6-byte header ("GIF87a" or "GIF89a"), a 7-byte logical
screen descriptor, an optional global color table, then a sequence of
blocks: images (0x2C), extensions (0x21) and finally the trailer (0x3B).
Image data and extension contents are stored as "data sub-blocks": a
length byte (1-255) followed by that many bytes, ended by a zero length.
Viewers ignore anything after the trailer.

Metadata lives in extensions: comments, plain text, and application
extensions (XMP, ICC color profiles, animation loop count, and anything
else a program chose to store).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from metaex.core import filetype, icc, xmp
from metaex.core.errors import MalformedFileError
from metaex.core.result import Section

SIGNATURES = (b"GIF87a", b"GIF89a")
MAX_BLOCKS = 100_000
MAX_SHOWN_CHARS = 256 * 1024
"""Longer comments are cut in the report (the full text is still removed)."""

IMAGE, EXTENSION, TRAILER = 0x2C, 0x21, 0x3B
GRAPHIC_CONTROL, COMMENT, PLAIN_TEXT, APPLICATION = 0xF9, 0xFE, 0x01, 0xFF
EXTENSION_NAMES = {GRAPHIC_CONTROL: "Graphic Control", COMMENT: "Comment",
                   PLAIN_TEXT: "Plain Text", APPLICATION: "Application"}

LOOP_IDS = ("NETSCAPE2.0", "ANIMEXTS1.0")
XMP_ID = "XMP DataXMP"
ICC_ID = "ICCRGBG1012"
XMP_MAGIC_TRAILER = b"\x01" + bytes(range(255, -1, -1)) + b"\x00"
"""The 258 bytes that end an XMP application extension: a descending ramp
that makes any reader walking the raw XMP as sub-blocks land on the block
terminator (the last 0x00)."""


@dataclass
class Block:
    kind: str
    """"image", "extension" or "trailer"."""
    offset: int
    end: int
    """Offset just past the block (after its sub-block terminator)."""
    label: int | None = None
    """Extension label (0xF9, 0xFE, 0x01, 0xFF or another value)."""
    sub_blocks: list[bytes] = field(default_factory=list)
    """Data sub-blocks without their length bytes (image: the LZW data)."""
    data_start: int = 0
    """Offset of the first sub-block length byte."""
    descriptor: bytes = b""
    """Image: the 10-byte image descriptor (starting with 0x2C)."""
    color_table: bytes = b""
    """Image: the local color table, if any."""
    min_code_size: int = 0
    """Image: the LZW minimum code size."""

    @property
    def name(self) -> str:
        if self.kind == "image":
            return "Image"
        if self.kind == "trailer":
            return "Trailer"
        return EXTENSION_NAMES.get(self.label, f"Extension 0x{self.label:02X}") + " extension"

    @property
    def app_id(self) -> str | None:
        """Application extension: identifier + authentication code, e.g. "NETSCAPE2.0"."""
        if self.kind != "extension" or self.label != APPLICATION or not self.sub_blocks:
            return None
        first = self.sub_blocks[0]
        if len(first) != 11:
            return f"(invalid identifier 0x{first.hex()})"
        return first.decode("latin-1")


@dataclass
class GifStructure:
    version: str
    width: int
    height: int
    flags: int
    background: int
    aspect: int
    global_color_table: bytes
    screen_end: int
    """Offset just past the screen descriptor and global color table."""
    blocks: list[Block] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    end_of_image: int | None = None
    """Offset just past the trailer, or None if no trailer was found."""
    error: str | None = None
    """Set when walking stopped early because the data is malformed."""

    @property
    def images(self) -> list[Block]:
        return [b for b in self.blocks if b.kind == "image"]

    def extensions(self, label: int) -> list[Block]:
        return [b for b in self.blocks if b.kind == "extension" and b.label == label]

    def applications(self) -> list[Block]:
        return self.extensions(APPLICATION)


def color_table_size(flags: int) -> int:
    """Bytes in a color table whose flag is set in ``flags`` (bit 7) with
    the size in bits 0-2; 0 if there is none."""
    return 3 * (2 << (flags & 0x07)) if flags & 0x80 else 0


def read_sub_blocks(data: bytes, pos: int, what: str) -> tuple[list[bytes], int]:
    """Data sub-blocks starting at ``pos``; returns them and the offset just
    past the terminator. Raises MalformedFileError if they run past the end."""
    n = len(data)
    blocks = []
    while True:
        # Each iteration consumes at least one byte, so this ends.
        if pos >= n:
            raise MalformedFileError(f"{what} is truncated (no sub-block terminator)")
        size = data[pos]
        pos += 1
        if size == 0:
            return blocks, pos
        if pos + size > n:
            raise MalformedFileError(f"{what} is truncated: a sub-block needs {size} bytes, "
                                     f"{n - pos} available")
        blocks.append(data[pos : pos + size])
        pos += size


def walk_blocks(data: bytes | bytearray | memoryview) -> GifStructure:
    """Walk the header, screen descriptor and all blocks up to the trailer.

    Raises MalformedFileError only if the header or screen descriptor is
    missing or broken. Anything malformed later stops the walk; the blocks
    found so far are kept and the problem is recorded in ``error`` and
    ``warnings``.
    """
    data = bytes(data)
    if not data.startswith(SIGNATURES):
        raise MalformedFileError("not a GIF file: no GIF87a/GIF89a header")
    if len(data) < 13:
        raise MalformedFileError("GIF screen descriptor is truncated")
    flags = data[10]
    gct_end = 13 + color_table_size(flags)
    if gct_end > len(data):
        raise MalformedFileError("GIF global color table is truncated")
    result = GifStructure(
        version=data[3:6].decode("ascii"),
        width=int.from_bytes(data[6:8], "little"), height=int.from_bytes(data[8:10], "little"),
        flags=flags, background=data[11], aspect=data[12],
        global_color_table=data[13:gct_end], screen_end=gct_end)
    n = len(data)
    pos = gct_end
    try:
        while True:
            if pos >= n:
                result.warnings.append("missing GIF trailer; the file may be truncated")
                break
            if len(result.blocks) >= MAX_BLOCKS:
                raise MalformedFileError(f"more than {MAX_BLOCKS} blocks")
            introducer = data[pos]
            if introducer == TRAILER:
                result.blocks.append(Block("trailer", pos, pos + 1))
                result.end_of_image = pos + 1
                break
            if introducer == IMAGE:
                result.blocks.append(_image_block(data, pos))
            elif introducer == EXTENSION:
                if pos + 2 > n:
                    raise MalformedFileError(f"extension at offset {pos} is truncated")
                label = data[pos + 1]
                what = f"extension 0x{label:02X} at offset {pos}"
                subs, end = read_sub_blocks(data, pos + 2, what)
                result.blocks.append(Block("extension", pos, end, label, subs, pos + 2))
            else:
                raise MalformedFileError(f"unknown block type 0x{introducer:02X} at offset {pos}")
            pos = result.blocks[-1].end
    except MalformedFileError as exc:
        result.error = str(exc)
        result.warnings.append(f"malformed GIF: {exc}; data after this point was not examined")
    return result


def _image_block(data: bytes, pos: int) -> Block:
    if pos + 10 > len(data):
        raise MalformedFileError(f"image descriptor at offset {pos} is truncated")
    descriptor = data[pos : pos + 10]
    table_end = pos + 10 + color_table_size(descriptor[9])
    if table_end + 1 > len(data):
        raise MalformedFileError(f"image at offset {pos} is truncated")
    min_code_size = data[table_end]
    if not 1 <= min_code_size <= 11:
        raise MalformedFileError(f"image at offset {pos} has an invalid LZW code size "
                                 f"{min_code_size}")
    subs, end = read_sub_blocks(data, table_end + 1, f"image data at offset {pos}")
    return Block("image", pos, end, None, subs, table_end + 1, descriptor,
                 data[pos + 10 : table_end], min_code_size)


# -- sections ---------------------------------------------------------------------------


def _text(raw: bytes) -> str:
    """GIF text is meant to be 7-bit ASCII; real files use UTF-8 or Latin-1."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    if len(text) > MAX_SHOWN_CHARS:
        text = text[:MAX_SHOWN_CHARS] + f" ... ({len(text) - MAX_SHOWN_CHARS} more characters)"
    return text


def comment_sections(structure: GifStructure) -> list[Section]:
    """Comment extensions: free text, often the creating program or a person."""
    section = Section("comments", "Comments")
    for i, block in enumerate(structure.extensions(COMMENT), 1):
        section.add(f"comment_{i}", "Comment", _text(b"".join(block.sub_blocks)),
                    sensitive=True, note=f"Comment extension at offset {block.offset}")
    return [section] if section.fields else []


def plain_text_sections(structure: GifStructure, warnings: list[str]) -> list[Section]:
    """Plain Text extensions: text meant to be drawn over the image (almost
    no viewer does), which can carry any message."""
    section = Section("plain_text", "Plain text")
    for i, block in enumerate(structure.extensions(PLAIN_TEXT), 1):
        note = f"Plain Text extension at offset {block.offset}"
        if not block.sub_blocks or len(block.sub_blocks[0]) != 12:
            warnings.append(f"Plain Text extension at offset {block.offset} has an invalid "
                            "header")
            text = b"".join(block.sub_blocks)
        else:
            head = block.sub_blocks[0]
            x, y = int.from_bytes(head[0:2], "little"), int.from_bytes(head[2:4], "little")
            note += f"; drawn at {x},{y}"
            text = b"".join(block.sub_blocks[1:])
        section.add(f"plain_text_{i}", "Plain text", _text(text), sensitive=True, note=note)
    return [section] if section.fields else []


def xmp_packet(data: bytes, block: Block, warnings: list[str]) -> bytes:
    """The XMP packet of an "XMP DataXMP" application extension.

    The XMP specification stores the packet as raw bytes (not split into
    sub-blocks) followed by the 258-byte magic trailer; some writers use
    ordinary sub-blocks instead, which are accepted too.
    """
    raw = data[block.data_start + 12 : block.end]  # after the 11-byte identifier sub-block
    if raw.endswith(XMP_MAGIC_TRAILER):
        return raw[: -len(XMP_MAGIC_TRAILER)]
    warnings.append(f"XMP at offset {block.offset} has no magic trailer; read as data "
                    "sub-blocks")
    return b"".join(block.sub_blocks[1:])


def xmp_sections(structure: GifStructure, data: bytes, warnings: list[str]) -> list[Section]:
    section = Section("xmp", "XMP")
    for block in structure.applications():
        if block.app_id == XMP_ID:
            n = sum(1 for f in section.fields if f.key.endswith(".size"))
            label = "XMP packet" if n == 0 else f"XMP packet {n + 1}"
            xmp.add_to_section(section, xmp_packet(data, block, warnings), label, "", warnings)
    return [section] if section.fields else []


def icc_profile(block: Block) -> bytes:
    """The ICC profile stored in an "ICCRGBG1012" application extension."""
    return b"".join(block.sub_blocks[1:])


def icc_sections(structure: GifStructure, warnings: list[str]) -> list[Section]:
    """The embedded ICC color profile, read with core/icc."""
    block = next((b for b in structure.applications() if b.app_id == ICC_ID), None)
    if block is None:
        return []
    try:
        return [icc.icc_section(icc_profile(block), warnings)]
    except MalformedFileError as exc:
        warnings.append(f"ICC profile not parsed: {exc}")
        section = Section("icc", "ICC profile")
        section.add("status", "ICC profile", "present but not parsed", note=str(exc))
        return [section]


def loop_count(block: Block) -> int | None:
    """Loop count of a NETSCAPE2.0 / ANIMEXTS1.0 extension (0 = forever)."""
    for sub in block.sub_blocks[1:]:
        if len(sub) == 3 and sub[0] == 1:
            return int.from_bytes(sub[1:3], "little")
    return None


def animation_sections(structure: GifStructure, warnings: list[str]) -> list[Section]:
    """Frame count and loop count of an animated GIF."""
    frames = len(structure.images)
    loops = [b for b in structure.applications() if b.app_id in LOOP_IDS]
    if frames < 2 and not loops:
        return []
    section = Section("animation", "Animation")
    section.add("frames", "Frames", frames)
    if loops:
        count = loop_count(loops[0])
        if count is None:
            warnings.append(f"{loops[0].app_id} extension at offset {loops[0].offset} has no "
                            "valid loop count")
            section.add("loop_count", "Loop count", "(invalid)", note=loops[0].app_id)
        else:
            section.add("loop_count", "Loop count", "forever" if count == 0 else count,
                        note=loops[0].app_id)
    return [section]


KNOWN_APPLICATIONS = (XMP_ID, ICC_ID) + LOOP_IDS


def app_extension_sections(structure: GifStructure) -> list[Section]:
    """Application extensions metaex doesn't read, and extensions with an
    unknown label: their contents could be anything."""
    section = Section("app_extensions", "Application extensions")
    for block in structure.blocks:
        if block.kind != "extension":
            continue
        size = sum(len(s) for s in block.sub_blocks)
        if block.label == APPLICATION and block.app_id not in KNOWN_APPLICATIONS:
            name = block.app_id or "(no identifier)"
            section.add(f"{name}@{block.offset}", name, f"{size} bytes at offset {block.offset}",
                        sensitive=True, note="application extension; contents not examined")
        elif block.label not in EXTENSION_NAMES:
            name = f"Extension 0x{block.label:02X}"
            section.add(f"{name}@{block.offset}", name, f"{size} bytes at offset {block.offset}",
                        sensitive=True, note="unknown extension type; contents not examined")
    return [section] if section.fields else []


def trailing_sections(structure: GifStructure, data: bytes,
                      warnings: list[str]) -> list[Section]:
    """Bytes after the trailer, which viewers ignore.

    Zero padding is a plain warning (same rule as JPEG and PNG); anything
    else is CRITICAL, because it can hide a whole file.
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


def structure_section(structure: GifStructure) -> Section:
    section = Section("structure", "GIF structure")
    section.add("version", "Version", structure.version)
    section.add("screen", "Logical screen", f"{structure.width} x {structure.height}")
    if structure.global_color_table:
        section.add("global_color_table", "Global color table",
                    f"{len(structure.global_color_table) // 3} colors")
    for i, block in enumerate(structure.blocks):
        value = f"offset {block.offset}, {block.end - block.offset} bytes"
        if block.kind == "image":
            left, top, w, h = (int.from_bytes(block.descriptor[j : j + 2], "little")
                               for j in (1, 3, 5, 7))
            value += f", {w} x {h} at {left},{top}"
            if block.descriptor[9] & 0x40:
                value += ", interlaced"
        elif block.app_id is not None:
            value += f", {block.app_id}"
        section.add(f"block_{i}", block.name, value)
    return section


# -- parse -------------------------------------------------------------------------


def parse(data: bytes | bytearray | memoryview) -> dict:
    """Parse a GIF and return its warnings, report sections and structure.

    Raises MalformedFileError if the header or screen descriptor is broken;
    problems later in the file become warnings.
    """
    data = bytes(data)
    structure = walk_blocks(data)
    warnings = list(structure.warnings)
    sections: list[Section] = []
    sections += comment_sections(structure)
    sections += plain_text_sections(structure, warnings)
    sections += xmp_sections(structure, data, warnings)
    sections += icc_sections(structure, warnings)
    sections += animation_sections(structure, warnings)
    sections += app_extension_sections(structure)
    sections += trailing_sections(structure, data, warnings)
    return {
        "warnings": warnings,
        "sections": sections,
        "structure": structure_section(structure),
        "complete": structure.error is None,
    }
