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


# -- a deterministic, highly compressed stream (for zip bomb tests) ---------------------------


class _Bits:
    """Deflate bit order: values LSB first, Huffman codes MSB first."""

    def __init__(self) -> None:
        self.out = bytearray()
        self.acc = 0
        self.n = 0

    def value(self, v: int, bits: int) -> None:
        self.acc |= v << self.n
        self.n += bits
        while self.n >= 8:
            self.out.append(self.acc & 0xFF)
            self.acc >>= 8
            self.n -= 8

    def code(self, code: str) -> None:
        for bit in code:
            self.value(int(bit), 1)

    def bytes(self) -> bytes:
        return bytes(self.out) + (bytes([self.acc]) if self.n else b"")


def repeat_deflate(byte: int, matches: int) -> tuple[bytes, bytes]:
    """(raw deflate stream, what it expands to): ``byte`` followed by
    ``matches`` copies of the 258 bytes before, in one dynamic Huffman block
    written by hand, so it is the same on every machine (zlib's own output
    is not). Each 258-byte match costs 2 bits: a ratio of about 1000:1.

    Literal/length code: match length 258 (symbol 285) = "0", the literal =
    "10", end of block (256) = "11"; distance code: distance 1 = "0".
    """
    if not 0 <= byte <= 255 or byte == 256 or matches < 0:
        raise ValueError("invalid byte or count")
    b = _Bits()
    b.value(1, 1)  # BFINAL
    b.value(2, 2)  # BTYPE = dynamic Huffman
    b.value(286 - 257, 5)  # HLIT: literal/length codes 0..285
    b.value(0, 5)  # HDIST: 1 distance code
    b.value(18 - 4, 4)  # HCLEN: code length codes in the order below, up to "1"
    order = (16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1)
    cl_lengths = {18: 1, 1: 2, 2: 2}  # canonical codes: 18 = "0", 1 = "10", 2 = "11"
    for symbol in order:
        b.value(cl_lengths.get(symbol, 0), 3)
    cl_code = {18: "0", 1: "10", 2: "11"}

    def zeros(n: int) -> None:
        while n:
            run = min(n, 138)
            if run < 11:
                raise ValueError("runs of zeros shorter than 11 are not needed here")
            b.code(cl_code[18])
            b.value(run - 11, 7)
            n -= run

    lengths = [0] * 286
    lengths[byte], lengths[256], lengths[285] = 2, 2, 1
    # write the literal/length code lengths, then the one distance code length
    run = 0
    for length in lengths:
        if length == 0:
            run += 1
            continue
        if run:
            zeros(run)
            run = 0
        b.code(cl_code[length])
    b.code(cl_code[1])  # distance code 0 has length 1
    literal_first = sorted([byte, 256])
    lit_code = {285: "0", literal_first[0]: "10", literal_first[1]: "11"}
    b.code(lit_code[byte])
    for _ in range(matches):
        b.code(lit_code[285])  # length 258 (no extra bits)
        b.code("0")  # distance 1
    b.code(lit_code[256])
    return b.bytes(), bytes([byte]) * (1 + 258 * matches)
