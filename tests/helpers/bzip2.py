"""A small bzip2 encoder written by hand, so test files are the same on
every machine.

bzip2 has no "stored" mode, and the output of the bzip2 library may differ
between versions, so samples are encoded here: run-length encoding,
Burrows-Wheeler transform (by sorting the rotations), move-to-front, the
RUNA/RUNB coding of zero runs, and two Huffman tables with fixed (but
complete) code lengths. The result is a valid bzip2 stream that any
decoder reads; it just compresses less well than the real thing.
"""

from __future__ import annotations

BLOCK_MAGIC = 0x314159265359
END_MAGIC = 0x177245385090
BLOCK_INPUT = 200_000
"""Bytes of input per block (at most 250,000 after run-length encoding,
far below the 900,000-byte block of level 9)."""
GROUP = 50


def crc32_msb(data: bytes, crc: int = 0xFFFFFFFF) -> int:
    """bzip2's CRC-32: polynomial 0x04C11DB7, most significant bit first."""
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 \
                else (crc << 1) & 0xFFFFFFFF
    return crc


def block_crc(data: bytes) -> int:
    return crc32_msb(data) ^ 0xFFFFFFFF


class _Bits:
    def __init__(self) -> None:
        self.out = bytearray()
        self.acc = 0
        self.n = 0

    def put(self, value: int, bits: int) -> None:
        for i in range(bits - 1, -1, -1):
            self.acc = (self.acc << 1) | ((value >> i) & 1)
            self.n += 1
            if self.n == 8:
                self.out.append(self.acc)
                self.acc = self.n = 0

    def bytes(self) -> bytes:
        tail = bytes([self.acc << (8 - self.n)]) if self.n else b""
        return bytes(self.out) + tail


def rle1(data: bytes) -> bytes:
    """Runs of 4 to 255 equal bytes become 4 bytes and a count of the rest."""
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        run = 1
        while i + run < len(data) and data[i + run] == b and run < 255:
            run += 1
        if run >= 4:
            out += bytes([b]) * 4 + bytes([run - 4])
        else:
            out += bytes([b]) * run
        i += run
    return bytes(out)


def bwt(data: bytes) -> tuple[bytes, int]:
    """(last column of the sorted rotations, row of the original)."""
    n = len(data)
    order = list(range(n))
    rank = list(data)
    k = 1
    while True:
        order.sort(key=lambda i: (rank[i], rank[(i + k) % n]))
        new = [0] * n
        for j in range(1, n):
            a, b = order[j - 1], order[j]
            new[b] = new[a] + ((rank[a], rank[(a + k) % n]) != (rank[b], rank[(b + k) % n]))
        rank = new
        if rank[order[-1]] == n - 1 or k >= n:
            break
        k *= 2
    return bytes(data[(i - 1) % n] for i in order), order.index(0)


def _code_lengths(alpha: int) -> list[int]:
    """Complete prefix code lengths for ``alpha`` symbols (Kraft sum 1)."""
    bits = max(1, (alpha - 1).bit_length())
    short = (1 << bits) - alpha  # symbols that get bits - 1
    return [bits - 1] * short + [bits] * (alpha - short)


def _codes(lengths: list[int]) -> list[int]:
    codes = [0] * len(lengths)
    code = 0
    for n in range(min(lengths), max(lengths) + 1):
        for i, length in enumerate(lengths):
            if length == n:
                codes[i] = code
                code += 1
        code <<= 1
    return codes


def _block(bits: _Bits, data: bytes) -> int:
    crc = block_crc(data)
    rle = rle1(data)
    last, origin = bwt(rle)
    used = sorted(set(rle))
    index = {b: i for i, b in enumerate(used)}
    alpha = len(used) + 2
    eob = len(used) + 1
    # move-to-front, then zero runs as RUNA (0) / RUNB (1)
    mtf = list(range(len(used)))
    symbols: list[int] = []
    zeros = 0

    def flush_zeros() -> None:
        nonlocal zeros
        while zeros > 0:
            if zeros & 1:
                symbols.append(0)
                zeros = (zeros - 1) // 2
            else:
                symbols.append(1)
                zeros = (zeros - 2) // 2

    for byte in last:
        pos = mtf.index(index[byte])
        if pos == 0:
            zeros += 1
            continue
        flush_zeros()
        mtf.insert(0, mtf.pop(pos))
        symbols.append(pos + 1)
    flush_zeros()
    symbols.append(eob)

    bits.put(BLOCK_MAGIC, 48)
    bits.put(crc, 32)
    bits.put(0, 1)  # not randomized
    bits.put(origin, 24)
    groups = [any(b >> 4 == g for b in used) for g in range(16)]
    bits.put(sum(1 << (15 - g) for g in range(16) if groups[g]), 16)
    for g in range(16):
        if groups[g]:
            bits.put(sum(1 << (15 - (b & 15)) for b in used if b >> 4 == g), 16)
    tables = 2
    selectors = -(-len(symbols) // GROUP)
    bits.put(tables, 3)
    bits.put(selectors, 15)
    for _ in range(selectors):
        bits.put(0, 1)  # every group uses table 0
    lengths = _code_lengths(alpha)
    for _ in range(tables):
        current = lengths[0]
        bits.put(current, 5)
        for length in lengths:
            while current < length:
                bits.put(0b10, 2)
                current += 1
            while current > length:
                bits.put(0b11, 2)
                current -= 1
            bits.put(0, 1)
    codes = _codes(lengths)
    for s in symbols:
        bits.put(codes[s], lengths[s])
    return crc


def compress(data: bytes, level: int = 9) -> bytes:
    """``data`` as a bzip2 stream (one block per BLOCK_INPUT bytes)."""
    bits = _Bits()
    bits.put(int.from_bytes(b"BZh" + str(level).encode(), "big"), 32)
    combined = 0
    for start in range(0, len(data), BLOCK_INPUT):
        crc = _block(bits, data[start:start + BLOCK_INPUT])
        combined = (((combined << 1) | (combined >> 31)) & 0xFFFFFFFF) ^ crc
    bits.put(END_MAGIC, 48)
    bits.put(combined, 32)
    return bits.bytes()
