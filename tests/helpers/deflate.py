"""zlib streams made of stored (uncompressed) deflate blocks.

zlib.compress() output depends on the zlib build Python uses (classic
zlib and zlib-ng compress the same data differently), so files built with
it are not byte-identical on every machine. A stored block is just a
header and the raw bytes, so stored_zlib() gives the same output
everywhere; any zlib decoder reads it.
"""

from __future__ import annotations

import struct
import zlib

MAX_STORED_BLOCK = 0xFFFF
"""Largest payload of one stored block (its LEN field is 16 bits)."""

ZLIB_HEADER = b"\x78\x01"
"""CMF = deflate with a 32 KiB window, FLG = no dictionary, "fastest" level."""


def stored_zlib(data: bytes, block_size: int = MAX_STORED_BLOCK) -> bytes:
    """``data`` as a zlib stream of stored blocks of at most ``block_size``
    bytes (always at least one block, so empty input works too)."""
    if not 1 <= block_size <= MAX_STORED_BLOCK:
        raise ValueError(f"block_size must be between 1 and {MAX_STORED_BLOCK}")
    out = bytearray(ZLIB_HEADER)
    pos = 0
    while True:
        block = data[pos : pos + block_size]
        pos += len(block)
        final = pos >= len(data)
        out.append(1 if final else 0)  # BFINAL bit, BTYPE 00 = stored
        out += struct.pack("<HH", len(block), len(block) ^ 0xFFFF)
        out += block
        if final:
            break
    out += struct.pack(">I", zlib.adler32(data))
    return bytes(out)
