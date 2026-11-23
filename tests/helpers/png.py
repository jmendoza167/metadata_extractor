"""Build small, real PNG files for tests (pixel data compressed with zlib).

Inside ``with stored_deflate():`` every helper here writes its zlib data as
stored blocks instead (helpers.deflate), so the files are byte-identical
on every machine; the committed sample files are built that way.
"""

from __future__ import annotations

import contextlib
import struct
import zlib
from collections.abc import Iterator

from helpers.deflate import stored_zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"

GRAY, RGB, PALETTE, GRAY_ALPHA, RGBA = 0, 2, 3, 4, 6
_CHANNELS = {GRAY: 1, RGB: 3, PALETTE: 1, GRAY_ALPHA: 2, RGBA: 4}


_stored = False


@contextlib.contextmanager
def stored_deflate() -> Iterator[None]:
    """Within this block, the helpers compress with stored deflate blocks."""
    global _stored
    previous, _stored = _stored, True
    try:
        yield
    finally:
        _stored = previous


def deflate(data: bytes, level: int = -1) -> bytes:
    """zlib data for ``data``: zlib.compress(), or stored blocks inside
    stored_deflate()."""
    return stored_zlib(data) if _stored else zlib.compress(data, level)


def chunk(chunk_type: bytes | str, data: bytes = b"", crc: int | None = None) -> bytes:
    """One chunk with a correct CRC (unless ``crc`` is given)."""
    if isinstance(chunk_type, str):
        chunk_type = chunk_type.encode("ascii")
    if crc is None:
        crc = zlib.crc32(chunk_type + data)
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def ihdr(width: int = 4, height: int = 4, depth: int = 8, color: int = RGB,
         interlace: int = 0) -> bytes:
    return chunk("IHDR", struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace))


def _row_bytes(width: int, depth: int, color: int) -> int:
    return (width * _CHANNELS[color] * depth + 7) // 8


def _pixels(width: int, height: int, depth: int, color: int, seed: int) -> bytes:
    """Unfiltered scanlines (filter byte 0 + a deterministic pattern)."""
    rows = []
    n = _row_bytes(width, depth, color)
    for y in range(height):
        rows.append(b"\x00" + bytes((seed + 7 * x + 13 * y) % 256 for x in range(n)))
    return b"".join(rows)


ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2),
         (0, 1, 1, 2))


def raw_image(width: int = 4, height: int = 4, depth: int = 8, color: int = RGB,
              seed: int = 1, interlace: int = 0) -> bytes:
    """Uncompressed image data (filtered scanlines), plain or Adam7-interlaced."""
    if not interlace:
        return _pixels(width, height, depth, color, seed)
    passes = []
    for i, (x0, y0, dx, dy) in enumerate(ADAM7):
        w = max(0, -(-(width - x0) // dx))
        h = max(0, -(-(height - y0) // dy))
        if w and h:  # empty passes have no scanlines at all
            passes.append(_pixels(w, h, depth, color, seed + i))
    return b"".join(passes)


def expected_raw_size(width: int, height: int, depth: int, color: int, interlace: int) -> int:
    return len(raw_image(width, height, depth, color, 0, interlace))


def idat_chunks(raw: bytes, parts: int = 1) -> list[bytes]:
    """The zlib-compressed image data split over ``parts`` IDAT chunks."""
    compressed = deflate(raw, 9)
    size = -(-len(compressed) // parts)
    return [chunk("IDAT", compressed[i : i + size]) for i in range(0, len(compressed), size)]


def make_png(*chunks: bytes, width: int = 4, height: int = 4, depth: int = 8,
             color: int = RGB, before_idat: tuple[bytes, ...] = (), idat_parts: int = 1,
             iend: bool = True, trailing: bytes = b"", seed: int = 1,
             interlace: int = 0) -> bytes:
    """Signature, IHDR, ``before_idat`` chunks, IDAT(s), ``chunks``, IEND, trailing bytes.

    For palette images, pass PLTE (and tRNS) in ``before_idat``.
    """
    raw = raw_image(width, height, depth, color, seed, interlace)
    return (SIGNATURE + ihdr(width, height, depth, color, interlace)
            + b"".join(before_idat)
            + b"".join(idat_chunks(raw, idat_parts))
            + b"".join(chunks)
            + (chunk("IEND") if iend else b"")
            + trailing)


def text(keyword: str, value: str) -> bytes:
    return chunk("tEXt", keyword.encode("latin-1") + b"\x00" + value.encode("latin-1"))


def ztxt(keyword: str, value: str, method: int = 0, compressed: bytes | None = None) -> bytes:
    body = compressed if compressed is not None else deflate(value.encode("latin-1"))
    return chunk("zTXt", keyword.encode("latin-1") + b"\x00" + bytes([method]) + body)


def itxt(keyword: str, value: str, language: str = "", translated: str = "",
         compress: bool = False, compressed: bytes | None = None) -> bytes:
    body = value.encode("utf-8")
    if compressed is not None:
        body, compress = compressed, True
    elif compress:
        body = deflate(body)
    return chunk("iTXt", keyword.encode("latin-1") + b"\x00" + bytes([int(compress), 0])
                 + language.encode("ascii") + b"\x00" + translated.encode("utf-8") + b"\x00"
                 + body)


def zlib_bomb(size: int) -> bytes:
    """zlib data that expands to ``size`` zero bytes."""
    c = zlib.compressobj(9)
    out = bytearray()
    block = bytes(1024 * 1024)
    left = size
    while left > 0:
        out += c.compress(block[: min(left, len(block))])
        left -= len(block)
    return bytes(out + c.flush())


def iccp(profile: bytes, name: str = "ICC profile", compressed: bytes | None = None) -> bytes:
    body = compressed if compressed is not None else deflate(profile)
    return chunk("iCCP", name.encode("latin-1") + b"\x00\x00" + body)


def exif_chunk(tiff: bytes, prefix: bool = False) -> bytes:
    return chunk("eXIf", (b"Exif\x00\x00" if prefix else b"") + tiff)


def time_chunk(year=2024, month=5, day=1, hour=12, minute=30, second=0) -> bytes:
    return chunk("tIME", struct.pack(">HBBBBB", year, month, day, hour, minute, second))


def phys(x: int = 2835, y: int = 2835, unit: int = 1) -> bytes:
    return chunk("pHYs", struct.pack(">IIB", x, y, unit))


def actl(frames: int, plays: int = 0) -> bytes:
    return chunk("acTL", struct.pack(">II", frames, plays))


def fctl(sequence: int, width: int = 4, height: int = 4) -> bytes:
    return chunk("fcTL", struct.pack(">IIIIIHHBB", sequence, width, height, 0, 0, 1, 10, 0, 0))


def fdat(sequence: int, raw: bytes) -> bytes:
    return chunk("fdAT", struct.pack(">I", sequence) + deflate(raw))


# -- image variants ----------------------------------------------------------------


def palette_png(*chunks: bytes, **kwargs) -> bytes:
    """4-color palette image with per-entry transparency."""
    plte = chunk("PLTE", bytes([0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255]))
    trns = chunk("tRNS", bytes([0, 128, 255, 255]))
    return make_png(*chunks, color=PALETTE, depth=2, before_idat=(plte, trns), **kwargs)


def apng(*chunks: bytes, frames: int = 3, width: int = 4, height: int = 4) -> bytes:
    """An animated PNG: the default image is frame 1; the others are in fdAT chunks."""
    sequence = 0
    before = [actl(frames)]
    before.append(fctl(sequence, width, height))
    sequence += 1
    after = []
    for i in range(1, frames):
        after.append(fctl(sequence, width, height))
        after.append(fdat(sequence + 1, raw_image(width, height, seed=10 + i)))
        sequence += 2
    return make_png(*after, *chunks, width=width, height=height, before_idat=tuple(before))


def variants() -> dict[str, bytes]:
    """Small real PNGs of every common kind, with a little metadata each."""
    meta = text("Software", "Generator 1.0")
    return {
        "gray8": make_png(meta, color=GRAY, width=5, height=3),
        "gray1": make_png(meta, color=GRAY, depth=1, width=13, height=2),
        "rgb8": make_png(meta),
        "rgba8": make_png(meta, color=RGBA),
        "gray_alpha16": make_png(meta, color=GRAY_ALPHA, depth=16),
        "rgb16": make_png(meta, depth=16, width=3, height=5),
        "palette_trns": palette_png(meta),
        "interlaced": make_png(meta, width=9, height=7, interlace=1),
        "interlaced_1x1": make_png(meta, width=1, height=1, interlace=1),
        "multi_idat": make_png(meta, width=16, height=16, idat_parts=5),
        "apng": apng(meta),
    }


# -- kitchen sink ------------------------------------------------------------------

AI_PARAMETERS = ("a photo of a cat on a beach\nNegative prompt: blurry\n"
                 "Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1234567, "
                 "Model: example-model-v1")
COMFY_PROMPT = '{"3": {"class_type": "KSampler", "inputs": {"seed": 1234567}}}'
COMFY_WORKFLOW = '{"last_node_id": 9, "nodes": [{"id": 3, "type": "KSampler"}]}'


def kitchen_sink_tiff(order: str = "<") -> bytes:
    from helpers.tiff import ASCII, RATIONAL, SHORT, build_tiff

    return build_tiff(
        [(0x010F, ASCII, "ExampleCam"), (0x0110, ASCII, "Model X"), (0x0112, SHORT, 6),
         (0x0131, ASCII, "Editor 2.0"), (0x0132, ASCII, "2024:05:01 14:30:00")],
        exif=[(0x9003, ASCII, "2024:05:01 14:30:00"), (0xA431, ASCII, "SN-0001")],
        gps=[(0x0001, ASCII, "N"), (0x0002, RATIONAL, [(48, 1), (51, 1), (3012, 100)]),
             (0x0003, ASCII, "E"), (0x0004, RATIONAL, [(2, 1), (17, 1), (4013, 100)])],
        order=order)


def kitchen_sink_png(order: str = "<") -> bytes:
    """Every chunk type metaex reads: text (tEXt, zTXt, iTXt), AI generation
    data, XMP with GPS, eXIf with GPS and Orientation 6, tIME, iCCP, all
    color and display chunks, a suggested palette with histogram, caBX,
    APNG animation, an unknown chunk and data after IEND. Values are made up."""
    from helpers.jpeg import SAMPLE_XMP, icc_profile

    before = (
        exif_chunk(kitchen_sink_tiff(order)),
        iccp(icc_profile(), "Jane's MacBook display"),
        chunk("sRGB", b"\x00"),
        chunk("gAMA", struct.pack(">I", 45455)),
        chunk("cHRM", struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000,
                                  15000, 6000)),
        chunk("cICP", bytes([1, 13, 0, 1])),
        chunk("mDCv", struct.pack(">8H2I", 35400, 14600, 8500, 39850, 6550, 2300, 15635,
                                  16450, 10000000, 50)),
        chunk("cLLI", struct.pack(">II", 10000000, 4000000)),
        chunk("sBIT", b"\x08\x08\x08"),
        chunk("PLTE", bytes(range(12))),  # suggested palette for a truecolor image
        chunk("bKGD", b"\x00\x10\x00\x20\x00\x30"),
        chunk("hIST", struct.pack(">4H", 1, 2, 3, 4)),
        chunk("tRNS", b"\x00\x00\x00\x00\x00\x00"),
        phys(),
        chunk("sPLT", b"Jane's palette\x00\x08" + bytes(12)),
        chunk("oFFs", struct.pack(">iiB", 120, 80, 0)),
        chunk("pCAL", b"elevation\x00" + struct.pack(">ii", 0, 100) + b"\x00\x02m\x00" + b"0\x00100"),
        chunk("sCAL", b"\x010.001\x000.001"),
        time_chunk(2024, 5, 1, 12, 30, 0),
        text("Author", "Jane Doe"),
        actl(2),
        fctl(0),
    )
    after = (
        fctl(1), fdat(2, raw_image(seed=20)),
        text("Software", "Editor 2.0"),
        text("Title", "Holiday"),
        text("Description", "Our trip"),
        text("Copyright", "(c) Jane Doe"),
        text("Creation Time", "2024-05-01T14:30:00+02:00"),
        text("Source", "ExampleCam Model X"),
        text("Disclaimer", "none"),
        text("Warning", "none"),
        text("Private Note", "meet at the cafe at 5"),
        ztxt("Comment", "a compressed comment"),
        itxt("Title", "Vacances", language="fr", translated="Titre", compress=True),
        text("parameters", AI_PARAMETERS),
        text("prompt", COMFY_PROMPT),
        itxt("workflow", COMFY_WORKFLOW),
        itxt("XML:com.adobe.xmp", SAMPLE_XMP),
        chunk("caBX", b"\x00\x00\x00\x20jumb" + bytes(24)),
        chunk("prVt", b"private data"),
    )
    return make_png(*after, before_idat=before, idat_parts=2, trailing=b"secret after IEND")
