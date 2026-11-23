"""A small baseline JPEG encoder for tests (no Pillow).

It writes real, decodable JPEGs: 8-bit baseline sequential DCT, Huffman
coded, with the example tables from ITU-T T.81 Annex K (quantization
tables K.1/K.2 scaled the way libjpeg scales them for a quality setting,
Huffman tables K.3-K.6). Grayscale or YCbCr without subsampling (4:4:4).

Because it is independent of Pillow, decoding its output with Pillow
cross-checks both: a wrong DCT, table or bitstream shows up as an error
or as pixels that don't match the pattern.
"""

from __future__ import annotations

import functools
import math
import struct

# ITU-T T.81 Annex K.1 and K.2, in natural (row-major) order.
STD_LUMINANCE_QUANT = (
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
)
STD_CHROMINANCE_QUANT = (
    17, 18, 24, 47, 99, 99, 99, 99,
    18, 21, 26, 66, 99, 99, 99, 99,
    24, 26, 56, 99, 99, 99, 99, 99,
    47, 66, 99, 99, 99, 99, 99, 99,
) + (99,) * 32

# ITU-T T.81 Annex K.3-K.6: (BITS: number of codes of each length 1..16, HUFFVAL).
DC_LUMINANCE = ((0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0), tuple(range(12)))
DC_CHROMINANCE = ((0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0), tuple(range(12)))
AC_LUMINANCE = (
    (0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7D),
    (0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06, 0x13, 0x51,
     0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xA1, 0x08, 0x23, 0x42, 0xB1, 0xC1,
     0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0A, 0x16, 0x17, 0x18,
     0x19, 0x1A, 0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39,
     0x3A, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57,
     0x58, 0x59, 0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75,
     0x76, 0x77, 0x78, 0x79, 0x7A, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8A, 0x92,
     0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7,
     0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3,
     0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9, 0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8,
     0xD9, 0xDA, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF1, 0xF2,
     0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA),
)
AC_CHROMINANCE = (
    (0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77),
    (0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12, 0x41, 0x51, 0x07,
     0x61, 0x71, 0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91, 0xA1, 0xB1, 0xC1, 0x09,
     0x23, 0x33, 0x52, 0xF0, 0x15, 0x62, 0x72, 0xD1, 0x0A, 0x16, 0x24, 0x34, 0xE1, 0x25,
     0xF1, 0x17, 0x18, 0x19, 0x1A, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x35, 0x36, 0x37, 0x38,
     0x39, 0x3A, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55, 0x56,
     0x57, 0x58, 0x59, 0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74,
     0x75, 0x76, 0x77, 0x78, 0x79, 0x7A, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
     0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3, 0xA4, 0xA5,
     0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA,
     0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9, 0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6,
     0xD7, 0xD8, 0xD9, 0xDA, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF2,
     0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA),
)

ZIGZAG = tuple(sorted(range(64), key=lambda i: (
    i // 8 + i % 8, (i // 8) if (i // 8 + i % 8) % 2 else (i % 8))))
"""ZIGZAG[k] is the natural-order index of the k-th coefficient in zigzag order."""

_COS = [[(math.sqrt(0.5) if u == 0 else 1.0) * 0.5 * math.cos((2 * x + 1) * u * math.pi / 16)
         for x in range(8)] for u in range(8)]


def scaled_table(table: tuple[int, ...], quality: int) -> tuple[int, ...]:
    """A quantization table scaled for ``quality`` (1-100), as libjpeg does."""
    scale = 5000 // quality if quality < 50 else 200 - 2 * quality
    return tuple(min(255, max(1, (q * scale + 50) // 100)) for q in table)


def _fdct(block: list[float]) -> list[float]:
    """2-D forward DCT of an 8x8 block (natural order): F = C . f . C^T."""
    rows = [[sum(_COS[u][x] * block[y * 8 + x] for x in range(8)) for u in range(8)]
            for y in range(8)]
    return [sum(_COS[v][y] * rows[y][u] for y in range(8)) for v in range(8) for u in range(8)]


def _huffman_codes(spec) -> dict[int, tuple[int, int]]:
    """symbol -> (code, length), built as in T.81 Annex C."""
    bits, values = spec
    assert sum(bits) == len(values)
    codes: dict[int, tuple[int, int]] = {}
    code, k = 0, 0
    for length, count in enumerate(bits, 1):
        for _ in range(count):
            codes[values[k]] = (code, length)
            code += 1
            k += 1
        code <<= 1
    return codes


class _BitWriter:
    def __init__(self) -> None:
        self.out = bytearray()
        self.acc = 0
        self.n = 0

    def put(self, value: int, length: int) -> None:
        self.acc = (self.acc << length) | (value & ((1 << length) - 1))
        self.n += length
        while self.n >= 8:
            self.n -= 8
            byte = (self.acc >> self.n) & 0xFF
            self.out.append(byte)
            if byte == 0xFF:
                self.out.append(0x00)  # byte stuffing
        self.acc &= (1 << self.n) - 1

    def flush(self) -> bytes:
        if self.n:
            self.put((1 << (8 - self.n)) - 1, 8 - self.n)  # pad with 1 bits
        return bytes(self.out)


def _magnitude(value: int) -> tuple[int, int]:
    """(size category, additional bits) of a DC difference or AC coefficient."""
    size = abs(value).bit_length()
    return size, (value if value >= 0 else value + (1 << size) - 1)


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def _planes(width: int, height: int, rgb: bytes, color: bool) -> list[list[float]]:
    """Level-shifted component planes (JFIF YCbCr, or luma only)."""
    n = width * height
    r, g, b = rgb[0::3], rgb[1::3], rgb[2::3]
    y = [0.299 * r[i] + 0.587 * g[i] + 0.114 * b[i] - 128 for i in range(n)]
    if not color:
        return [y]
    cb = [-0.168736 * r[i] - 0.331264 * g[i] + 0.5 * b[i] for i in range(n)]
    cr = [0.5 * r[i] - 0.418688 * g[i] - 0.081312 * b[i] for i in range(n)]
    return [y, cb, cr]


def encode(width: int, height: int, rgb: bytes, *, color: bool = True,
           quality: int = 90) -> tuple[list[bytes], bytes]:
    """Encode ``rgb`` (width x height RGB bytes) as a baseline JPEG.

    Returns (table and frame segments: DQT, SOF0, DHT, SOS, in that order;
    entropy-coded scan data). Put SOI, any APPn segments, these segments,
    the scan data and EOI together to get a JPEG file (see jpeg_file)."""
    if not (1 <= width <= 4096 and 1 <= height <= 4096):
        raise ValueError("size out of range for the test encoder")
    if len(rgb) != 3 * width * height:
        raise ValueError("rgb must hold 3 bytes per pixel")
    planes = _planes(width, height, rgb, color)
    quant = [scaled_table(STD_LUMINANCE_QUANT, quality),
             scaled_table(STD_CHROMINANCE_QUANT, quality)]
    dc_specs, ac_specs = [DC_LUMINANCE, DC_CHROMINANCE], [AC_LUMINANCE, AC_CHROMINANCE]
    dc_codes = [_huffman_codes(s) for s in dc_specs]
    ac_codes = [_huffman_codes(s) for s in ac_specs]
    table_of = [0, 1, 1][:len(planes)]  # luma uses tables 0, chroma tables 1

    bits = _BitWriter()
    previous_dc = [0] * len(planes)
    for by in range(0, height, 8):
        for bx in range(0, width, 8):
            for c, plane in enumerate(planes):
                # Edge pixels are repeated to fill partial blocks.
                block = [plane[min(by + y, height - 1) * width + min(bx + x, width - 1)]
                         for y in range(8) for x in range(8)]
                coefficients = _fdct(block)
                q = quant[table_of[c]]
                zz = [int(round(coefficients[ZIGZAG[k]] / q[ZIGZAG[k]])) for k in range(64)]
                dc_table, ac_table = dc_codes[table_of[c]], ac_codes[table_of[c]]
                size, extra = _magnitude(zz[0] - previous_dc[c])
                previous_dc[c] = zz[0]
                bits.put(*dc_table[size])
                if size:
                    bits.put(extra, size)
                run = 0
                for k in range(1, 64):
                    if zz[k] == 0:
                        run += 1
                        continue
                    while run > 15:
                        bits.put(*ac_table[0xF0])  # ZRL: 16 zeros
                        run -= 16
                    size, extra = _magnitude(zz[k])
                    bits.put(*ac_table[(run << 4) | size])
                    bits.put(extra, size)
                    run = 0
                if run:
                    bits.put(*ac_table[0x00])  # EOB

    n = len(planes)
    dqt = _segment(0xDB, b"".join(bytes([t]) + bytes(quant[t][ZIGZAG[k]] for k in range(64))
                                  for t in range(min(n, 2))))
    sof = _segment(0xC0, struct.pack(">BHHB", 8, height, width, n)
                   + b"".join(bytes([c + 1, 0x11, table_of[c]]) for c in range(n)))
    dht = _segment(0xC4, b"".join(
        bytes([klass << 4 | t]) + bytes(spec[0]) + bytes(spec[1])
        for t in range(min(n, 2))
        for klass, spec in ((0, dc_specs[t]), (1, ac_specs[t]))))
    sos = _segment(0xDA, bytes([n]) + b"".join(bytes([c + 1, table_of[c] << 4 | table_of[c]])
                                               for c in range(n)) + b"\x00\x3f\x00")
    return [dqt, sof, dht, sos], bits.flush()


def pattern(width: int, height: int) -> bytes:
    """A fixed, non-flat RGB test pattern: red rises left to right, green top
    to bottom, and blue alternates between dark and light 8x8 blocks (aligned
    to the JPEG blocks, so the edges don't blur)."""
    out = bytearray()
    for y in range(height):
        for x in range(width):
            out += bytes([32 + 192 * x // max(width - 1, 1),
                          32 + 192 * y // max(height - 1, 1),
                          48 if (x // 8 + y // 8) % 2 else 208])
    return bytes(out)


@functools.lru_cache(maxsize=16)
def encoded_pattern(width: int, height: int, color: bool = True) -> tuple[tuple[bytes, ...], bytes]:
    """encode(pattern(...)), cached: (segments, scan data)."""
    segments, scan = encode(width, height, pattern(width, height), color=color)
    return tuple(segments), scan


def jpeg_file(*app_segments: bytes, width: int = 48, height: int = 32, color: bool = True,
              trailing: bytes = b"") -> bytes:
    """SOI, ``app_segments``, a real baseline image of pattern(width, height),
    EOI and ``trailing`` bytes."""
    segments, scan = encoded_pattern(width, height, color)
    return (b"\xff\xd8" + b"".join(app_segments) + b"".join(segments) + scan + b"\xff\xd9"
            + trailing)
