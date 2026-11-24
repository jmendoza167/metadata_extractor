"""Build TAR archives header by header, including broken and hostile ones.

The stdlib tarfile module can't write most of what the tests need (bad
checksums, data hidden in unused fields or padding, GNU atime/ctime,
traversal names), so this helper writes every block itself. All values
are fixed, so the output is the same on every machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BLOCK = 512
RECORD = 10240
USTAR, GNU, V7 = "ustar", "gnu", "v7"
JAN_2024 = 1714559400
"""2024-05-01T10:30:00Z."""


def octal(value: int, width: int) -> bytes:
    return f"{value:0{width - 1}o}".encode("ascii") + b"\x00"


def base256(value: int, width: int) -> bytes:
    return bytes([0x80]) + value.to_bytes(width - 1, "big")


def _fit(value: bytes, width: int) -> bytes:
    assert len(value) <= width, (value, width)
    return value + bytes(width - len(value))


@dataclass
class Header:
    name: bytes | str
    typeflag: bytes = b"0"
    size: int | None = None
    """Default: the length of ``data``."""
    data: bytes = b""
    mode: int = 0o644
    uid: int = 0
    gid: int = 0
    mtime: int = 315532800
    uname: bytes = b""
    gname: bytes = b""
    linkname: bytes | str = b""
    devmajor: int = 0
    devminor: int = 0
    prefix: bytes = b""
    format: str = USTAR
    atime: int = 0
    """GNU headers only."""
    ctime: int = 0
    """GNU headers only."""
    checksum: int | None = None
    """Override the checksum."""
    patch: dict[int, bytes] = field(default_factory=dict)
    """Bytes to put at offsets of the header block (before the checksum)."""
    padding: bytes | None = None
    """Bytes after the data, up to the next block (default zeros)."""
    raw_fields: dict[str, bytes] = field(default_factory=dict)
    """Replace a numeric field's bytes (e.g. "size": base256(...))."""

    def block(self) -> bytes:
        name = self.name.encode() if isinstance(self.name, str) else self.name
        link = self.linkname.encode() if isinstance(self.linkname, str) else self.linkname
        size = len(self.data) if self.size is None else self.size

        def num(key: str, value: int, width: int) -> bytes:
            return self.raw_fields.get(key, octal(value, width))

        b = (_fit(name, 100) + num("mode", self.mode, 8) + num("uid", self.uid, 8)
             + num("gid", self.gid, 8) + num("size", size, 12) + num("mtime", self.mtime, 12)
             + b" " * 8 + self.typeflag + _fit(link, 100))
        if self.format == V7:
            b += bytes(255)
        elif self.format == GNU:
            b += (b"ustar  \x00" + _fit(self.uname, 32) + _fit(self.gname, 32)
                  + octal(self.devmajor, 8) + octal(self.devminor, 8)
                  + (octal(self.atime, 12) if self.atime else bytes(12))
                  + (octal(self.ctime, 12) if self.ctime else bytes(12)) + bytes(143))
        else:
            b += (b"ustar\x0000" + _fit(self.uname, 32) + _fit(self.gname, 32)
                  + octal(self.devmajor, 8) + octal(self.devminor, 8) + _fit(self.prefix, 155)
                  + bytes(12))
        block = bytearray(b)
        assert len(block) == BLOCK, len(block)
        for offset, raw in self.patch.items():
            block[offset:offset + len(raw)] = raw
        checksum = sum(block) if self.checksum is None else self.checksum
        block[148:156] = f"{checksum:06o}".encode() + b"\x00 "
        return bytes(block)

    def bytes(self) -> bytes:
        pad = -len(self.data) % BLOCK
        padding = bytes(pad) if self.padding is None else self.padding
        assert len(padding) == pad
        return self.block() + self.data + padding


def pax_records(records: dict[str, bytes | str]) -> bytes:
    out = b""
    for key, value in records.items():
        v = value.encode() if isinstance(value, str) else value
        body = b" " + key.encode() + b"=" + v + b"\n"
        length = len(body) + len(str(len(body)))
        if len(str(length)) > len(str(len(body))):
            length = len(body) + len(str(length))
        out += str(length).encode() + body
    return out


def pax(records: dict[str, bytes | str], name: bytes = b"././@PaxHeader", flag: bytes = b"x",
        **kwargs) -> Header:
    return Header(name, flag, data=pax_records(records), **kwargs)


def gnu_long(value: bytes | str, flag: bytes = b"L") -> Header:
    v = value.encode() if isinstance(value, str) else value
    return Header(b"././@LongLink", flag, data=v + b"\x00", format=GNU)


def build_tar(headers: list[Header], *, end_blocks: int = 2, record: bool = True,
              suffix: bytes = b"") -> bytes:
    out = b"".join(h.bytes() for h in headers) + bytes(end_blocks * BLOCK)
    if record:
        out += bytes(-len(out) % RECORD)
    return out + suffix


def file(name: str | bytes, data: bytes, typeflag: bytes = b"0", **kwargs) -> Header:
    return Header(name, typeflag, data=data, **kwargs)


def folder(name: str, **kwargs) -> Header:
    kwargs.setdefault("mode", 0o755)
    return Header(name if name.endswith("/") else name + "/", b"5", **kwargs)


def symlink(name: str, target: str, **kwargs) -> Header:
    kwargs.setdefault("mode", 0o777)
    return Header(name, b"2", linkname=target, **kwargs)


def hardlink(name: str, target: str, **kwargs) -> Header:
    return Header(name, b"1", linkname=target, **kwargs)
