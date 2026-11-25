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

import hashlib
from dataclasses import dataclass, field

from metaex.core import filetype, icc, xmp
from metaex.core.errors import CannotCleanError, HiddenDataError, MalformedFileError
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


# -- the LZW image data ------------------------------------------------------------------

MAX_LZW_CODES = 32_000_000
"""LZW codes decoded per file at most (a few seconds); the image data of
larger files is not checked for hidden data, and such files are refused."""
LZW_CACHE_SIZE = 8


@dataclass(frozen=True)
class LzwCheck:
    pixels: int
    """Pixels the codes stand for, up to the end-of-information code."""
    after_end: bytes
    """LZW data after the byte that holds the end-of-information code."""
    ended: bool
    """True if an end-of-information code was found."""
    codes: int
    """Codes decoded."""
    problem: str | None = None
    """Why the data is not valid LZW (an undefined code)."""


def decode_lzw(min_code_size: int, lzw: bytes, max_codes: int) -> LzwCheck | None:
    """Walk the LZW codes of one image without building the pixels: only the
    length of each table entry is tracked, which is all it takes to know the
    code sizes, the number of pixels and where the end-of-information code
    is. None if more than ``max_codes`` codes would be needed."""
    clear = 1 << min_code_size
    end = clear + 1
    size = min_code_size + 1
    next_code = end + 1
    lengths = [1] * clear + [0] * (4096 - clear)
    previous = 0  # length of the previous code's string (0: right after a clear code)
    acc = nbits = pos = pixels = codes = 0
    n = len(lzw)
    while True:
        while nbits < size:
            if pos >= n:
                return LzwCheck(pixels, b"", False, codes)
            acc |= lzw[pos] << nbits
            pos += 1
            nbits += 8
        code = acc & ((1 << size) - 1)
        acc >>= size
        nbits -= size
        codes += 1
        if codes > max_codes:
            return None
        if code == clear:
            size, next_code, previous = min_code_size + 1, end + 1, 0
            continue
        if code == end:
            return LzwCheck(pixels, lzw[pos:], True, codes)
        if previous == 0:
            if code > clear:
                return LzwCheck(pixels, b"", False, codes,
                                f"code {code} after a clear code is not a color")
            length = 1
        else:
            if code < next_code:
                length = lengths[code]
            elif code == next_code:
                length = previous + 1
            else:
                return LzwCheck(pixels, b"", False, codes, f"undefined LZW code {code}")
            if next_code < 4096:
                lengths[next_code] = previous + 1
                next_code += 1
                if next_code == 1 << size and size < 12:
                    size += 1
        pixels += length
        previous = length


@dataclass
class ImageCheck:
    block: Block
    number: int
    """1 for the first image."""
    expected: int
    """Pixels the image descriptor needs (width x height)."""
    lzw: LzwCheck | None
    """None: not checked (over MAX_LZW_CODES)."""

    @property
    def extra_pixels(self) -> int:
        return max(0, self.lzw.pixels - self.expected) if self.lzw is not None else 0

    @property
    def hidden_bytes(self) -> int:
        """Bytes after the end code; zeros don't count (they carry nothing)."""
        if self.lzw is None or self.lzw.after_end.count(0) == len(self.lzw.after_end):
            return 0
        return len(self.lzw.after_end)

    @property
    def hidden(self) -> bool:
        return bool(self.extra_pixels or self.hidden_bytes)


_LZW_CACHE: dict[bytes, list[ImageCheck]] = {}


def check_images(structure: GifStructure) -> list[ImageCheck]:
    """Decode the LZW codes of every image (without the pixels) and compare
    what they hold with what the image needs. Viewers stop at the image's
    last pixel and at the end-of-information code, so extra pixels and data
    after the end code can hide data. Cached by content (decoding is done
    once per command); treat the result as read-only."""
    h = hashlib.sha256(str(MAX_LZW_CODES).encode("ascii"))
    for block in structure.images:
        lzw = b"".join(block.sub_blocks)
        h.update(block.descriptor + bytes([block.min_code_size]) + len(lzw).to_bytes(8, "big")
                 + lzw)
    key = h.digest()
    cached = _LZW_CACHE.pop(key, None)
    if cached is None:
        cached = []
        budget = MAX_LZW_CODES
        for n, block in enumerate(structure.images, 1):
            width = int.from_bytes(block.descriptor[5:7], "little")
            height = int.from_bytes(block.descriptor[7:9], "little")
            result = None
            if budget > 0:
                result = decode_lzw(block.min_code_size, b"".join(block.sub_blocks), budget)
            if result is not None:
                budget -= result.codes
            else:
                budget = 0
            cached.append(ImageCheck(block, n, width * height, result))
    _LZW_CACHE[key] = cached
    while len(_LZW_CACHE) > LZW_CACHE_SIZE:
        del _LZW_CACHE[next(iter(_LZW_CACHE))]
    return cached


def _hidden_text(c: ImageCheck) -> str:
    parts = []
    if c.extra_pixels:
        parts.append(f"{c.extra_pixels} pixels more than the image needs")
    if c.hidden_bytes:
        parts.append(f"{c.hidden_bytes} bytes after the end of the image data")
    return " and ".join(parts)


def image_data_problems(checks: list[ImageCheck]) -> list[str]:
    """Image data that is not valid LZW, or that was too large to check."""
    out = []
    for c in checks:
        where = f"image {c.number} (offset {c.block.offset})"
        if c.lzw is None:
            out.append(f"{where} was not checked for hidden data: the file has more than "
                       f"{MAX_LZW_CODES} LZW codes")
        elif c.lzw.problem is not None:
            out.append(f"{where} has invalid image data: {c.lzw.problem}")
    return out


def hidden_image_data_sections(structure: GifStructure, warnings: list[str]) -> list[Section]:
    section = Section("hidden_image_data", "Hidden data in image data")
    checks = check_images(structure)
    for c in checks:
        if c.hidden:
            section.add(f"image_{c.number}", f"image {c.number} (offset {c.block.offset})",
                        _hidden_text(c), sensitive=True)
    if section.fields:
        warnings.append(f"CRITICAL: the image data hides data that viewers don't show "
                        f"({section.fields[0].label}: {section.fields[0].value})")
    warnings += image_data_problems(checks)
    return [section] if section.fields else []


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


# -- Stripping ---------------------------------------------------------------------

GCE_RESERVED_BITS = 0xE0
"""Bits 5-7 of the Graphic Control packed byte are reserved (always 0)."""
ICC_SUB_BLOCK = 255


def checked_structure(data: bytes) -> GifStructure:
    """Walk a GIF for stripping or verification: anything malformed raises.

    Unlike parse(), which reports what it can of a damaged file, this refuses
    truncated files, files without a trailer or an image, and Graphic
    Control extensions with an invalid size.
    """
    structure = walk_blocks(data)
    if structure.error:
        raise MalformedFileError(structure.error)
    if structure.end_of_image is None:
        raise MalformedFileError("missing GIF trailer; the file may be truncated")
    if not structure.images:
        raise MalformedFileError("no image found")
    for block in structure.extensions(GRAPHIC_CONTROL):
        if not block.sub_blocks or len(block.sub_blocks[0]) != 4:
            raise MalformedFileError(f"Graphic Control extension at offset {block.offset} "
                                     "is malformed")
    return structure


def graphic_control_extension(fields: bytes) -> bytes:
    """A Graphic Control extension with these 4 bytes (packed, delay, transparent
    index), reserved bits cleared, and nothing else."""
    return (b"\x21\xf9\x04" + bytes([fields[0] & ~GCE_RESERVED_BITS & 0xFF]) + fields[1:4]
            + b"\x00")


def loop_extension(app_id: str, count: int) -> bytes:
    """A NETSCAPE2.0 / ANIMEXTS1.0 extension with only the loop count."""
    return (b"\x21\xff\x0b" + app_id.encode("latin-1") + b"\x03\x01"
            + count.to_bytes(2, "little") + b"\x00")


def usable_icc_profile(block: Block) -> bytes | None:
    """The ICC profile to keep, cut to the size its header declares, or None
    if it doesn't look like a real profile (it is then dropped)."""
    profile = icc_profile(block)
    if len(profile) < 128 or profile[36:40] != b"acsp":
        return None
    declared = int.from_bytes(profile[0:4], "big")
    if not 128 <= declared <= len(profile):
        return None
    profile = profile[:declared]
    return None if icc.profile_problem(profile) else profile


def icc_extension(profile: bytes) -> bytes:
    """An ICCRGBG1012 application extension holding exactly ``profile``."""
    subs = b"".join(bytes([len(profile[i : i + ICC_SUB_BLOCK])]) + profile[i : i + ICC_SUB_BLOCK]
                    for i in range(0, len(profile), ICC_SUB_BLOCK))
    return b"\x21\xff\x0b" + ICC_ID.encode("latin-1") + subs + b"\x00"


@dataclass
class Rendered:
    """What a viewer uses from a GIF, in order: the screen descriptor and
    global color table, then each image with the Graphic Control extension
    that applies to it, and the animation loop count."""
    screen: bytes
    items: list[tuple[str, Block, bytes | None]] = field(default_factory=list)
    """("image", block, its graphic control fields or None) and ("loop", block, None)."""
    loop: tuple[str, int] | None = None
    """(identifier, count) of the first valid loop extension."""


def rendered(structure: GifStructure, data: bytes) -> Rendered:
    """The blocks that affect how the GIF looks, in file order.

    A Graphic Control extension applies to the next graphic block (an image
    or a Plain Text extension); one that precedes a Plain Text extension, or
    no graphic block at all, is not used. If several precede one image, the
    last one counts.
    """
    out = Rendered(data[6 : structure.screen_end])
    pending: bytes | None = None
    for block in structure.blocks:
        if block.kind == "image":
            out.items.append(("image", block, pending))
            pending = None
        elif block.kind == "extension" and block.label == GRAPHIC_CONTROL:
            pending = block.sub_blocks[0] if block.sub_blocks else None
        elif block.kind == "extension" and block.label == PLAIN_TEXT:
            pending = None  # its Graphic Control extension belongs to the text
        elif block.app_id in LOOP_IDS and out.loop is None:
            count = loop_count(block)
            if count is not None:
                out.loop = (block.app_id, count)
                out.items.append(("loop", block, None))
    return out


def image_data(data: bytes | bytearray | memoryview) -> bytes:
    """The image-defining data, for comparing two files: the screen
    descriptor and global color table, then for each image its Graphic
    Control fields (reserved bits cleared), image descriptor, local color
    table, LZW code size and LZW data (without the sub-block framing), and
    the loop count. Raises MalformedFileError like checked_structure()."""
    data = bytes(data)
    r = rendered(checked_structure(data), data)
    parts = [b"screen", len(r.screen).to_bytes(4, "big"), r.screen]
    for kind, block, gce in r.items:
        if kind == "loop":
            parts.append(b"loop" + r.loop[1].to_bytes(2, "big"))
            continue
        if gce is not None:
            parts.append(b"gce" + graphic_control_extension(gce)[3:7])
        lzw = b"".join(block.sub_blocks)
        parts += [b"image", block.descriptor, len(block.color_table).to_bytes(4, "big"),
                  block.color_table, bytes([block.min_code_size]),
                  len(lzw).to_bytes(8, "big"), lzw]
    return b"".join(parts)


def strip(data: bytes | bytearray | memoryview, *, keep_orientation: bool = True,
          paranoid: bool = False) -> bytes:
    """Return a copy of the GIF with all metadata removed (whitelist).

    Kept byte-for-byte: the header, screen descriptor, global color table,
    every image (descriptor, local color table, LZW data) and the trailer.
    Kept in a minimal rewritten form: the Graphic Control extension that
    applies to each image (timing, transparency; reserved bits cleared),
    the animation loop count (NETSCAPE2.0 / ANIMEXTS1.0, first one only)
    and, unless ``paranoid``, the ICC color profile (ICCRGBG1012, the
    profile bytes unchanged). Dropped: comments, Plain Text extensions (and
    their Graphic Control extensions), XMP, all other application and
    unknown extensions, and any data after the trailer. GIF has no
    orientation, so ``keep_orientation`` has no effect.

    Raises MalformedFileError for malformed, truncated or unsupported input
    (including image data that is not valid LZW), HiddenDataError if the
    image data holds more than the image (extra pixels, data after the end
    code), CannotCleanError if it is too large to check; nothing is ever
    partly stripped.
    """
    data = bytes(data)
    structure = checked_structure(data)
    checks = check_images(structure)
    for c in checks:
        if c.lzw is not None and c.lzw.problem is not None:
            raise MalformedFileError(image_data_problems([c])[0])
        if c.lzw is None:
            raise CannotCleanError(f"{image_data_problems([c])[0]}, so data hidden in it "
                                   "can't be ruled out")
        if c.hidden:
            raise HiddenDataError(f"image {c.number} hides {_hidden_text(c)}, which viewers "
                                  "don't show; the image data is copied byte for byte, so "
                                  "this file can't be cleaned")
    r = rendered(structure, data)
    out = bytearray(data[: structure.screen_end])
    icc_written = False
    for kind, block, gce in r.items:
        if kind == "loop":
            out += loop_extension(*r.loop)
            continue
        if not icc_written and not paranoid:
            # The ICC profile goes before the first image, where it applies to all.
            icc_written = True
            block_icc = next((b for b in structure.applications() if b.app_id == ICC_ID),
                             None)
            profile = usable_icc_profile(block_icc) if block_icc is not None else None
            if profile is not None:
                out += icc_extension(profile)
        if gce is not None:
            out += graphic_control_extension(gce)
        out += data[block.offset : block.end]
    out += bytes([TRAILER])
    cleaned = bytes(out)
    # Self-check: the output must be a well-formed GIF with identical image data.
    if image_data(cleaned) != image_data(data):
        raise MalformedFileError("internal check failed: image data changed while stripping")
    return cleaned


# -- Verification -------------------------------------------------------------------

# Report sections (and field keys; None = every field) that a stripped GIF may
# still contain. Anything else found by verify is a leftover.
KEPT_BY_DESIGN: dict[str, set[str] | None] = {
    "icc": None,
    "animation": None,
}


def _leftover_problem(structure: GifStructure, i: int, data: bytes,
                      seen: set[str]) -> str | None:
    """Why block ``i`` is not one strip() would write; None if it is."""
    block = structure.blocks[i]
    if block.kind in ("image", "trailer"):
        return None
    if block.label == GRAPHIC_CONTROL:
        if block.end - block.offset != 8 or block.sub_blocks[0][0] & GCE_RESERVED_BITS:
            return "Graphic Control extension is not in its minimal form"
        following = next((b for b in structure.blocks[i + 1 :]
                          if b.kind != "extension" or b.label in (GRAPHIC_CONTROL, PLAIN_TEXT)),
                         None)
        if following is None or following.kind != "image":
            return "Graphic Control extension does not apply to an image"
        return None
    app_id = block.app_id
    first = app_id not in seen
    seen.add(app_id or "")
    if app_id in LOOP_IDS and first and not seen & (set(LOOP_IDS) - {app_id}):
        if (len(block.sub_blocks) == 2 and len(block.sub_blocks[1]) == 3
                and block.sub_blocks[1][0] == 1):
            return None
        return "loop extension holds more than the loop count"
    if app_id == ICC_ID and first:
        profile = usable_icc_profile(block)
        if profile is not None and icc_extension(profile) == data[block.offset : block.end]:
            return None
        return "ICC profile is invalid or has extra bytes"
    return f"{block.name} is not allowed in a cleaned GIF"


def verify_details(cleaned: bytes, original: bytes | None = None) -> dict:
    """GIF-specific verification, beyond the field-level whitelist.

    - The file must be complete (trailer present, nothing malformed).
    - Block whitelist: only images, Graphic Control extensions in their
      minimal form that apply to an image, one minimal loop extension and
      one ICC profile extension holding exactly a valid profile may remain.
      Anything else is a leftover.
    - SHA-256 of the image data (screen descriptor, color tables, Graphic
      Control fields, image descriptors, LZW data, loop count); compared
      with the original's if one is given.
    """
    out: dict = {"leftovers": [], "problems": [], "image_sha256": None,
                 "original_image_sha256": None, "image_data_match": None}
    try:
        image = image_data(cleaned)
    except MalformedFileError as exc:
        out["problems"].append(f"not a complete, well-formed GIF: {exc}")
        return out
    out["image_sha256"] = hashlib.sha256(image).hexdigest()
    structure = walk_blocks(cleaned)
    # Hidden data in the image data is reported by parse() as a leftover section.
    out["problems"] += image_data_problems(check_images(structure))
    seen: set[str] = set()
    for i, block in enumerate(structure.blocks):
        problem = _leftover_problem(structure, i, cleaned, seen)
        if problem is not None:
            name = block.name + (f" {block.app_id}" if block.app_id else "")
            size = block.end - block.offset
            out["leftovers"].append(("GIF structure", f"{name} at offset {block.offset}",
                                     f"{problem} ({size} bytes)"))
    if original is not None:
        try:
            original_image = image_data(original)
        except MalformedFileError as exc:
            out["problems"].append(f"original is not a complete, well-formed GIF ({exc}); "
                                   "image data not compared")
        else:
            out["original_image_sha256"] = hashlib.sha256(original_image).hexdigest()
            out["image_data_match"] = original_image == image
            if original_image != image:
                out["problems"].append("image data differs from the original")
    return out


def removed_sizes(data: bytes | bytearray | memoryview) -> dict[str, int]:
    """Byte size of the data after the trailer ("trailing"), for the strip summary."""
    data = bytes(data)
    structure = walk_blocks(data)
    if structure.end_of_image is not None and len(data) > structure.end_of_image:
        return {"trailing": len(data) - structure.end_of_image}
    return {}


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
    sections += hidden_image_data_sections(structure, warnings)
    sections += trailing_sections(structure, data, warnings)
    return {
        "warnings": warnings,
        "sections": sections,
        "structure": structure_section(structure),
        "complete": structure.error is None,
    }
