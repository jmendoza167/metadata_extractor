"""Build small, real GIF files for tests (pixel data LZW-compressed)."""

from __future__ import annotations

import struct

XMP_MAGIC_TRAILER = b"\x01" + bytes(range(255, -1, -1)) + b"\x00"


# -- LZW ---------------------------------------------------------------------------------


def lzw_encode(indices: bytes, min_code_size: int) -> bytes:
    """GIF-flavored LZW: variable code width (up to 12 bits), LSB-first bit
    packing, a clear code first and the end code last."""
    clear, end = 1 << min_code_size, (1 << min_code_size) + 1
    codes: list[tuple[int, int]] = []  # (code, width)

    def reset():
        return {bytes([i]): i for i in range(clear)}, end + 1, min_code_size + 1

    table, next_code, width = reset()
    codes.append((clear, width))
    w = b""
    for k in indices:
        wk = w + bytes([k])
        if wk in table:
            w = wk
            continue
        codes.append((table[w], width))
        if next_code < 4096:
            table[wk] = next_code
            next_code += 1
            if next_code > (1 << width) and width < 12:
                width += 1
        else:
            codes.append((clear, width))
            table, next_code, width = reset()
        w = bytes([k])
    if w:
        codes.append((table[w], width))
    codes.append((end, width))
    out = bytearray()
    acc = bits = 0
    for code, size in codes:
        acc |= code << bits
        bits += size
        while bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8
    if bits:
        out.append(acc & 0xFF)
    return bytes(out)


def lzw_decode(data: bytes, min_code_size: int) -> bytes:
    """Decoder used to check the encoder (and the builders) in the tests."""
    clear, end = 1 << min_code_size, (1 << min_code_size) + 1
    width = min_code_size + 1
    table = [bytes([i]) for i in range(clear)] + [b"", b""]
    out = bytearray()
    prev = None
    pos = 0
    total_bits = len(data) * 8
    value = int.from_bytes(data, "little")
    while pos + width <= total_bits:
        code = (value >> pos) & ((1 << width) - 1)
        pos += width
        if code == clear:
            table = table[: end + 1]
            width = min_code_size + 1
            prev = None
            continue
        if code == end:
            break
        if prev is None:
            entry = table[code]
        elif code < len(table):
            entry = table[code]
            if len(table) < 4096:
                table.append(prev + entry[:1])
        else:
            entry = prev + prev[:1]
            table.append(entry)
        out += entry
        prev = entry
        if len(table) == (1 << width) and width < 12:
            width += 1
    return bytes(out)


def sub_blocks(data: bytes) -> bytes:
    """``data`` as 255-byte sub-blocks plus the terminator."""
    out = bytearray()
    for i in range(0, len(data), 255):
        part = data[i : i + 255]
        out += bytes([len(part)]) + part
    return bytes(out + b"\x00")


# -- blocks ------------------------------------------------------------------------------


def palette(colors: int = 4, seed: int = 0) -> bytes:
    return bytes((seed + 37 * i) % 256 for i in range(3 * colors))


def pixels(width: int, height: int, colors: int = 4, seed: int = 1) -> bytes:
    return bytes((seed + x * 3 + y * 5 + (x * y) % 7) % colors
                 for y in range(height) for x in range(width))


def table_bits(colors: int) -> int:
    """The 3-bit size field for a color table of ``colors`` entries (a power of 2)."""
    return max(colors.bit_length() - 2, 0)


def image(width: int = 4, height: int = 4, left: int = 0, top: int = 0, *,
          colors: int = 4, seed: int = 1, local_table: bytes | None = None,
          interlace: bool = False, lzw: bytes | None = None,
          min_code_size: int | None = None) -> bytes:
    """An image block: descriptor, optional local color table and LZW data."""
    flags = 0x40 if interlace else 0
    if local_table is not None:
        flags |= 0x80 | table_bits(len(local_table) // 3)
    if min_code_size is None:
        min_code_size = max(2, (colors - 1).bit_length())
    if lzw is None:
        lzw = lzw_encode(pixels(width, height, colors, seed), min_code_size)
    return (b"\x2c" + struct.pack("<HHHHB", left, top, width, height, flags)
            + (local_table or b"") + bytes([min_code_size]) + sub_blocks(lzw))


def graphic_control(delay: int = 10, transparent: int | None = None, disposal: int = 0) -> bytes:
    flags = (disposal << 2) | (1 if transparent is not None else 0)
    return b"\x21\xf9\x04" + struct.pack("<BHB", flags, delay, transparent or 0) + b"\x00"


def comment(text: str | bytes) -> bytes:
    raw = text.encode("utf-8") if isinstance(text, str) else text
    return b"\x21\xfe" + sub_blocks(raw)


def plain_text(text: str, x: int = 0, y: int = 0) -> bytes:
    header = struct.pack("<HHHHBBBB", x, y, 40, 8, 8, 8, 1, 0)
    return b"\x21\x01" + bytes([12]) + header + sub_blocks(text.encode("ascii"))


def application(identifier: bytes, data: bytes | str = b"") -> bytes:
    """An application extension with an 11-byte identifier + auth code."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    assert len(identifier) == 11
    return b"\x21\xff\x0b" + identifier + (sub_blocks(data) if data else b"\x00")


def loop(count: int = 0, identifier: bytes = b"NETSCAPE2.0") -> bytes:
    return b"\x21\xff\x0b" + identifier + b"\x03\x01" + struct.pack("<H", count) + b"\x00"


def xmp_extension(packet: bytes | str) -> bytes:
    """XMP as the XMP specification stores it: raw bytes + the magic trailer."""
    if isinstance(packet, str):
        packet = packet.encode("utf-8")
    return b"\x21\xff\x0b" + b"XMP DataXMP" + packet + XMP_MAGIC_TRAILER


def icc_extension(profile: bytes) -> bytes:
    return application(b"ICCRGBG1012", profile)


def make_gif(*blocks: bytes, width: int = 4, height: int = 4, colors: int = 4,
             version: bytes = b"89a", global_table: bool = True, trailer: bool = True,
             trailing: bytes = b"", background: int = 0) -> bytes:
    """Header, screen descriptor, global color table, ``blocks`` (one 4x4
    image if none are given), trailer and trailing bytes."""
    flags = 0
    gct = b""
    if global_table:
        flags = 0x80 | 0x70 | table_bits(colors)
        gct = palette(colors)
    body = b"".join(blocks) if blocks else image(width, height, colors=colors)
    return (b"GIF" + version + struct.pack("<HHBBB", width, height, flags, background, 0)
            + gct + body + (b"\x3b" if trailer else b"") + trailing)


# -- variants and kitchen sink -------------------------------------------------------------


def animated_gif(*extra: bytes, frames: int = 3, width: int = 6, height: int = 5,
                 count: int = 0) -> bytes:
    """An animation: loop extension, then per frame a Graphic Control
    extension and an image (the second frame has a local color table)."""
    blocks = [loop(count)]
    for i in range(frames):
        blocks.append(graphic_control(delay=10 * (i + 1), transparent=i % 4 if i else None,
                                      disposal=i % 3))
        local = palette(4, seed=50) if i == 1 else None
        blocks.append(image(width - i % 2, height - i % 2, i % 2, i % 2, seed=i + 1,
                            local_table=local))
    return make_gif(*blocks, *extra, width=width, height=height)


def variants() -> dict[str, bytes]:
    """Small real GIFs of every common kind, each with a comment."""
    note = comment("Generator 1.0")
    return {
        "static": make_gif(note, image(5, 3, colors=4), width=5, height=3),
        "two_colors": make_gif(note, image(9, 2, colors=2), width=9, height=2, colors=2),
        "256_colors": make_gif(note, image(40, 30, colors=256), width=40, height=30,
                               colors=256),
        "gif87a": make_gif(image(), version=b"87a"),
        "local_table_only": make_gif(note, image(local_table=palette(4, 9)),
                                     global_table=False),
        "interlaced": make_gif(note, image(7, 9, interlace=True), width=7, height=9),
        "transparent": make_gif(note, graphic_control(transparent=3), image()),
        "animated": animated_gif(note),
        "big_lzw": make_gif(note, image(64, 64, colors=256, seed=3), width=64, height=64,
                            colors=256),
    }


def kitchen_sink_gif() -> bytes:
    """Every block type metaex reads: comments (UTF-8 and Latin-1), Plain
    Text, XMP with GPS (with the magic trailer), an ICC profile naming a
    device, the loop extension, Graphic Control extensions, three frames
    (one interlaced, one with a local color table), unknown application and
    extension blocks and data after the trailer. Values are made up."""
    from helpers.jpeg import SAMPLE_XMP, icc_profile

    return make_gif(
        comment("Created by Jane Doe with Editor 2.0"),
        loop(0),
        icc_extension(icc_profile()),
        xmp_extension(SAMPLE_XMP),
        application(b"MGK8BIM0000", b"8BIM\x04\x04" + bytes(20)),
        graphic_control(delay=20, disposal=1),
        image(8, 8, colors=4, seed=1),
        comment(b"caf\xe9 at 5"),
        graphic_control(delay=5),
        plain_text("meet at the cafe at 5", 2, 3),
        graphic_control(delay=30, transparent=2),
        image(6, 6, 1, 1, seed=2, local_table=palette(4, 40), interlace=True),
        b"\x21\x42" + sub_blocks(b"unknown extension data"),
        graphic_control(delay=40),
        image(8, 8, seed=3),
        application(b"ImageMagick", b"gamma=0.45455"),
        width=8, height=8, trailing=b"secret after trailer")
