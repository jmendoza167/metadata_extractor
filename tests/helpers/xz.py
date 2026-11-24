"""xz files written by hand: one block of uncompressed LZMA2 chunks.

liblzma's compressed output may differ between versions, so test files
use LZMA2's uncompressed chunks instead: the same bytes on every machine,
read by any xz decoder. Stream padding, several streams and broken
footers are easy to make with the pieces below.
"""

from __future__ import annotations

import struct
import zlib

MAGIC = b"\xfd7zXZ\x00"
CHECK_NONE, CHECK_CRC32 = 0, 1
CHUNK = 0x10000


def varint(n: int) -> bytes:
    out = bytearray()
    while n >= 0x80:
        out.append(n & 0x7F | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _pad4(data: bytes) -> bytes:
    return data + bytes(-len(data) % 4)


def lzma2_uncompressed(data: bytes) -> bytes:
    out = bytearray()
    for i, start in enumerate(range(0, len(data), CHUNK)):
        piece = data[start:start + CHUNK]
        out += bytes([1 if i == 0 else 2]) + struct.pack(">H", len(piece) - 1) + piece
    return bytes(out) + b"\x00"


def stream(data: bytes, check: int = CHECK_CRC32) -> bytes:
    flags = bytes([0, check])
    out = bytearray(MAGIC + flags + struct.pack("<I", zlib.crc32(flags)))
    records = []
    if data:
        # header size, flags (one filter, no sizes), filter LZMA2 with 4 KiB dictionary
        header = _pad4(bytes([0x00, 0x00, 0x21, 0x01, 0x00]))
        header = bytes([(len(header) + 4) // 4 - 1]) + header[1:]
        header += struct.pack("<I", zlib.crc32(header))
        body = lzma2_uncompressed(data)
        check_field = struct.pack("<I", zlib.crc32(data)) if check == CHECK_CRC32 else b""
        unpadded = len(header) + len(body) + len(check_field)
        out += header + _pad4(body) + check_field
        records.append((unpadded, len(data)))
    index = b"\x00" + varint(len(records)) + b"".join(varint(u) + varint(s) for u, s in records)
    index = _pad4(index)
    index += struct.pack("<I", zlib.crc32(index))
    out += index
    backward = struct.pack("<I", len(index) // 4 - 1) + flags
    out += struct.pack("<I", zlib.crc32(backward)) + backward + b"YZ"
    return bytes(out)


def xz(data: bytes, check: int = CHECK_CRC32, padding: int = 0, streams: int = 1) -> bytes:
    """``data`` as an xz file; ``streams`` > 1 splits it into that many
    streams, each followed by ``padding`` zero bytes (a multiple of 4)."""
    if streams == 1:
        return stream(data, check) + bytes(padding)
    size = -(-len(data) // streams)
    return b"".join(stream(data[i * size:(i + 1) * size], check) + bytes(padding)
                    for i in range(streams))
