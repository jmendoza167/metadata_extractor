"""gzip files written by hand, with every header field under control.

The deflate data is made of stored (uncompressed) blocks by default, so the
files are the same on every machine (zlib's compressed output is not).
"""

from __future__ import annotations

import struct
import zlib

from helpers.deflate import stored_zlib

FTEXT, FHCRC, FEXTRA, FNAME, FCOMMENT = 0x01, 0x02, 0x04, 0x08, 0x10


def stored_deflate(data: bytes) -> bytes:
    """Raw deflate data (no zlib header or checksum) of stored blocks."""
    return stored_zlib(data)[2:-4]


def member(data: bytes, *, name: bytes | None = None, mtime: int = 0, os: int = 255,
           comment: bytes | None = None, extra: bytes | None = None, header_crc: bool = False,
           text: bool = False, xfl: int = 0, deflate: bytes | None = None,
           crc: int | None = None, size: int | None = None, flags: int = 0) -> bytes:
    """One gzip member; ``deflate`` replaces the compressed data, ``crc`` and
    ``size`` the trailer values, ``flags`` adds raw flag bits."""
    flags |= ((FTEXT if text else 0) | (FHCRC if header_crc else 0)
              | (FEXTRA if extra is not None else 0) | (FNAME if name is not None else 0)
              | (FCOMMENT if comment is not None else 0))
    head = b"\x1f\x8b\x08" + struct.pack("<BIBB", flags, mtime, xfl, os)
    if extra is not None:
        head += struct.pack("<H", len(extra)) + extra
    if name is not None:
        head += name + b"\x00"
    if comment is not None:
        head += comment + b"\x00"
    if header_crc:
        head += struct.pack("<H", zlib.crc32(head) & 0xFFFF)
    body = stored_deflate(data) if deflate is None else deflate
    trailer = struct.pack("<II", zlib.crc32(data) if crc is None else crc,
                          (len(data) if size is None else size) & 0xFFFFFFFF)
    return head + body + trailer


def gzip(data: bytes, **kwargs) -> bytes:
    return member(data, **kwargs)


def subfield(sub_id: bytes, data: bytes) -> bytes:
    """One subfield of a gzip extra field."""
    return sub_id + struct.pack("<H", len(data)) + data
