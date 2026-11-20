"""Capped zlib decompression for untrusted data.

A few bytes of zlib data can expand to gigabytes ("zip bomb"). Every
decompression here draws from a Budget: output beyond what is left in the
budget is never produced, and running over it raises
DecompressionLimitError. One Budget is shared by everything decompressed
from one file, so many small bombs can't add up either.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

from metaex.core.errors import MalformedFileError, MetaexError

DEFAULT_LIMIT = 16 * 1024 * 1024


class DecompressionLimitError(MetaexError):
    """Decompressing would exceed the size limit (possible decompression bomb)."""


class Budget:
    """How many decompressed bytes may still be produced."""

    def __init__(self, limit: int = DEFAULT_LIMIT) -> None:
        if limit < 0:
            raise ValueError("limit must not be negative")
        self.limit = limit
        self.remaining = limit


@dataclass(frozen=True)
class Inflated:
    data: bytes
    extra: int
    """Number of bytes after the end of the zlib stream (normally 0)."""


def inflate(data: bytes | bytearray | memoryview, budget: Budget | None = None) -> Inflated:
    """Decompress one complete zlib stream, within ``budget``.

    Raises MalformedFileError if the stream is invalid or incomplete, and
    DecompressionLimitError if its output would exceed the budget (the
    budget is then used up, so further calls fail fast).
    """
    if budget is None:
        budget = Budget()
    d = zlib.decompressobj()
    try:
        # max_length=0 would mean "no limit", so always ask for at least 1 byte;
        # one byte more than allowed tells us the limit was exceeded.
        out = d.decompress(bytes(data), budget.remaining + 1)
    except zlib.error as exc:
        raise MalformedFileError(f"compressed data is invalid ({exc})") from None
    if len(out) > budget.remaining:
        budget.remaining = 0
        raise DecompressionLimitError(
            f"decompressed data is larger than the {budget.limit // (1024 * 1024)} MiB limit "
            "(possible decompression bomb)")
    if not d.eof:
        raise MalformedFileError("compressed data is incomplete")
    budget.remaining -= len(out)
    return Inflated(out, len(d.unused_data))
