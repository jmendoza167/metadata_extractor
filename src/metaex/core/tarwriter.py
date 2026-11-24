"""Write TAR archives that reveal nothing about when, where or by whom they
were made.

Every entry gets a plain ustar header: time 1980-01-01 00:00:00 UTC, owner
and group 0 with empty names, the neutral permissions 644 (755 for folders
and executables, 777 for symbolic links), device numbers only for device
entries, and zeros in every unused byte. A pax header is written only when
a name or link target doesn't fit the ustar fields, and it holds nothing
but "path" and "linkpath". The data is copied byte for byte and padded
with zeros; the archive ends with two zero blocks and zeros up to a
multiple of the 10240-byte record, as GNU tar and bsdtar write it.
"""

from __future__ import annotations

from dataclasses import dataclass

from metaex.core.archive import NEUTRAL_TIME

BLOCK = 512
RECORD = 20 * BLOCK
PAX_NAME = b"././@PaxHeader"
"""The name Python's tarfile gives pax headers (it reveals nothing)."""
MAX_OCTAL_SIZE = 8 ** 11 - 1


@dataclass(frozen=True)
class OutMember:
    name: bytes
    """Raw name (folders end with "/")."""
    typeflag: bytes
    mode: int
    """Permission bits."""
    data: bytes = b""
    linkname: bytes = b""
    devmajor: int = 0
    devminor: int = 0


def _octal(value: int, width: int) -> bytes:
    """``width`` bytes: zero-padded octal digits and a NUL, like GNU tar."""
    text = f"{value:0{width - 1}o}".encode("ascii")
    if len(text) > width - 1:
        raise ValueError(f"{value} doesn't fit in a {width}-byte field")
    return text + b"\x00"


def _field(value: bytes, width: int) -> bytes:
    if len(value) > width:
        raise ValueError("value too long for its field")
    return value + bytes(width - len(value))


def split_name(name: bytes) -> tuple[bytes, bytes] | None:
    """(prefix, name) for the ustar fields, or None if the name needs pax."""
    if len(name) <= 100:
        return b"", name
    if len(name) > 256:
        return None
    for i in range(len(name) - 1, 0, -1):
        if name[i:i + 1] == b"/" and i <= 155 and len(name) - i - 1 <= 100 \
                and len(name) - i - 1 > 0:
            return name[:i], name[i + 1:]
    return None


def header(name: bytes, prefix: bytes, typeflag: bytes, mode: int, size: int,
           linkname: bytes = b"", devmajor: int = 0, devminor: int = 0,
           device: bool = False) -> bytes:
    block = (_field(name, 100) + _octal(mode, 8) + _octal(0, 8) + _octal(0, 8)
             + _octal(size, 12) + _octal(NEUTRAL_TIME, 12) + b" " * 8 + typeflag
             + _field(linkname, 100) + b"ustar\x0000" + bytes(32) + bytes(32)
             + (_octal(devmajor, 8) + _octal(devminor, 8) if device else bytes(16))
             + _field(prefix, 155) + bytes(12))
    assert len(block) == BLOCK
    checksum = f"{sum(block):06o}".encode("ascii") + b"\x00 "
    return block[:148] + checksum + block[156:]


def pax_record(key: str, value: bytes) -> bytes:
    body = f" {key}=".encode("utf-8") + value + b"\n"
    length = len(body) + len(str(len(body)))
    if len(str(length)) > len(str(len(body))):
        length = len(body) + len(str(length))
    return str(length).encode("ascii") + body


def _padded(data: bytes) -> bytes:
    return data + bytes(-len(data) % BLOCK)


def write_tar(members: list[OutMember]) -> bytes:
    """The archive of ``members``, in order. Raises ValueError for values
    that don't fit (an entry larger than 8 GiB)."""
    out = bytearray()
    for m in members:
        records = b""
        parts = split_name(m.name)
        if parts is None:
            records += pax_record("path", m.name)
            parts = (b"", m.name.rstrip(b"/")[-99:].rsplit(b"/", 1)[-1]
                     + (b"/" if m.name.endswith(b"/") else b""))
        if len(m.linkname) > 100:
            records += pax_record("linkpath", m.linkname)
            link = m.linkname[:100]
        else:
            link = m.linkname
        if len(m.data) > MAX_OCTAL_SIZE:
            raise ValueError("entry too large")
        if records:
            out += header(PAX_NAME, b"", b"x", 0o644, len(records)) + _padded(records)
        device = m.typeflag in (b"3", b"4")
        out += header(parts[1], parts[0], m.typeflag, m.mode, len(m.data), link,
                      m.devmajor, m.devminor, device)
        out += _padded(m.data)
    out += bytes(2 * BLOCK)
    out += bytes(-len(out) % RECORD)
    return bytes(out)
