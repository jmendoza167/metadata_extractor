"""Bounds-checked reading of binary data.

Every file metaex looks at is untrusted. ByteReader makes it hard to read
past the end of a buffer by accident: each read checks that enough bytes are
available and raises MalformedFileError (with the offset and what was being
read) instead of silently returning short data.
"""

from __future__ import annotations

import struct

from metaex.core.errors import MalformedFileError

_ENDIAN_PREFIX = {"big": ">", "little": "<"}


class ByteReader:
    """A cursor over an immutable byte buffer with checked reads.

    Offsets passed to seek() and sub() are relative to the start of this
    reader. Error messages report absolute offsets (relative to the outermost
    buffer), which is what you want when looking at the file in a hex editor.
    """

    __slots__ = ("_view", "_pos", "_endian", "_base")

    def __init__(
        self,
        data: bytes | bytearray | memoryview,
        endian: str = "big",
        *,
        base_offset: int = 0,
    ) -> None:
        view = memoryview(data)
        if view.ndim != 1 or view.format != "B":
            view = view.cast("B")
        self._view = view
        self._pos = 0
        self._base = base_offset
        self._endian = _check_endian(endian)

    # -- state ---------------------------------------------------------------

    @property
    def endian(self) -> str:
        return self._endian

    @endian.setter
    def endian(self, value: str) -> None:
        self._endian = _check_endian(value)

    def __len__(self) -> int:
        return len(self._view)

    def tell(self) -> int:
        """Current position, relative to the start of this reader."""
        return self._pos

    @property
    def remaining(self) -> int:
        """Number of bytes between the current position and the end."""
        return len(self._view) - self._pos

    def seek(self, offset: int) -> None:
        """Move to an absolute position within this reader (0..len)."""
        _check_int(offset, "offset")
        if offset < 0 or offset > len(self._view):
            raise MalformedFileError(
                f"seek to offset {self._base + offset} is outside the data "
                f"(valid range {self._base}..{self._base + len(self._view)})"
            )
        self._pos = offset

    def skip(self, n: int, what: str = "data") -> None:
        """Advance by n bytes, which must all be present."""
        self._require(n, what)
        self._pos += n

    # -- reads -----------------------------------------------------------------

    def read(self, n: int, what: str = "data") -> bytes:
        """Read exactly n bytes and advance, or raise MalformedFileError."""
        self._require(n, what)
        start = self._pos
        self._pos += n
        return self._view[start : start + n].tobytes()

    def peek(self, n: int, what: str = "data") -> bytes:
        """Return exactly n bytes without advancing, or raise."""
        self._require(n, what)
        return self._view[self._pos : self._pos + n].tobytes()

    def u8(self, what: str = "u8") -> int:
        return self._unpack("B", 1, what)

    def u16(self, what: str = "u16") -> int:
        return self._unpack("H", 2, what)

    def u32(self, what: str = "u32") -> int:
        return self._unpack("I", 4, what)

    def i32(self, what: str = "i32") -> int:
        return self._unpack("i", 4, what)

    def u64(self, what: str = "u64") -> int:
        return self._unpack("Q", 8, what)

    def sub(self, offset: int, length: int, what: str = "sub-range") -> ByteReader:
        """Return a new reader limited to [offset, offset + length).

        The new reader cannot see anything outside that slice, starts at
        position 0 and inherits this reader's endianness. This reader's
        position is not changed.
        """
        _check_int(offset, "offset")
        _check_int(length, "length")
        size = len(self._view)
        if offset < 0 or length < 0 or offset > size or length > size - offset:
            raise MalformedFileError(
                f"{what} at offset {self._base + offset} with length {length} "
                f"extends outside the data (valid range "
                f"{self._base}..{self._base + size})"
            )
        return ByteReader(
            self._view[offset : offset + length],
            self._endian,
            base_offset=self._base + offset,
        )

    # -- internals -------------------------------------------------------------

    def _require(self, n: int, what: str) -> None:
        _check_int(n, "length")
        if n < 0:
            raise MalformedFileError(
                f"invalid negative length {n} while reading {what} "
                f"at offset {self._base + self._pos}"
            )
        if n > self.remaining:
            raise MalformedFileError(
                f"unexpected end of data while reading {what} at offset "
                f"{self._base + self._pos}: need {n} bytes, "
                f"{self.remaining} available"
            )

    def _unpack(self, code: str, size: int, what: str) -> int:
        self._require(size, what)
        (value,) = struct.unpack_from(
            _ENDIAN_PREFIX[self._endian] + code, self._view, self._pos
        )
        self._pos += size
        return value


def _check_endian(value: str) -> str:
    if value not in _ENDIAN_PREFIX:
        raise ValueError(f"endian must be 'big' or 'little', not {value!r}")
    return value


def _check_int(value: object, name: str) -> None:
    # bool is an int subclass; passing one is always a programming mistake.
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an int, not {type(value).__name__}")
