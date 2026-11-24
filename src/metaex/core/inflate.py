"""Capped zlib decompression for untrusted data.

A few bytes of zlib data can expand to gigabytes ("zip bomb"). Every
decompression here draws from a Budget: output beyond what is left in the
budget is never produced, and running over it raises
DecompressionLimitError. One Budget is shared by everything decompressed
from one file, so many small bombs can't add up either.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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
        self.exhausted_at: int | None = None

    @property
    def spent(self) -> int:
        """Bytes really produced (a limit error uses the budget up without
        producing what was left)."""
        return self.limit - self.remaining if self.exhausted_at is None else self.exhausted_at

    def exhaust(self) -> None:
        """Use the budget up after a limit error, so further reads fail fast."""
        if self.exhausted_at is None:
            self.exhausted_at = self.limit - self.remaining
        self.remaining = 0


_SHARED: ContextVar[Budget | None] = ContextVar("metaex_shared_budget", default=None)


def shared_budget() -> Budget | None:
    """The budget of the archive whose files are being read, if any: ZIP
    archives and compressed files opened without a budget of their own
    (an Office document or an archive inside an archive) draw from it."""
    return _SHARED.get()


@contextmanager
def sharing(budget: Budget) -> Iterator[Budget]:
    """Make ``budget`` the shared budget while reading what an archive holds."""
    token = _SHARED.set(budget)
    try:
        yield budget
    finally:
        _SHARED.reset(token)


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
        budget.exhaust()
        raise DecompressionLimitError(
            f"decompressed data is larger than the {budget.limit // (1024 * 1024)} MiB limit "
            "(possible decompression bomb)")
    if not d.eof:
        raise MalformedFileError("compressed data is incomplete")
    budget.remaining -= len(out)
    return Inflated(out, len(d.unused_data))


# -- streams too large to hold in memory (image data) ---------------------------------

STREAM_STEP = 1024 * 1024
"""Largest piece of output produced at a time by inflate_stream()."""


@dataclass(frozen=True)
class StreamResult:
    size: int
    """Decompressed bytes produced (never more than the limit)."""
    complete: bool
    """True if the end of the zlib stream was reached."""
    more: bool
    """True if decompression stopped at the limit with more output still to come."""
    unused: int
    """Bytes of input after the end of the zlib stream (0 unless ``complete``)."""


def inflate_stream(parts: Iterable[bytes], limit: int,
                   sink: Callable[[bytes], None] | None = None,
                   step: int = STREAM_STEP) -> StreamResult:
    """Decompress one zlib stream given in pieces, without holding its output.

    At most ``limit`` bytes are produced, at most ``step`` at a time; each
    piece is passed to ``sink`` (or discarded). Input after the end of the
    stream is counted, not decompressed. The work is bounded by the input
    size and ``limit``. Raises MalformedFileError if the stream is invalid.
    """
    if limit < 0 or step <= 0:
        raise ValueError("limit must not be negative and step must be positive")
    d = zlib.decompressobj()
    produced = 0
    unused = 0

    def take(out: bytes) -> bool:
        """Count (and pass on) output up to the limit; True if it went past it."""
        nonlocal produced
        over = len(out) > limit - produced
        out = out[: limit - produced]
        if out and sink is not None:
            sink(out)
        produced += len(out)
        return over

    def decompress(buf: bytes) -> bytes:
        try:
            # Ask for one byte more than is allowed: getting it means "more".
            return d.decompress(buf, min(step, limit - produced + 1))
        except zlib.error as exc:
            raise MalformedFileError(f"compressed data is invalid ({exc})") from None

    for part in parts:
        if d.eof:
            unused += len(part)
            continue
        buf = bytes(part)
        while buf and not d.eof:
            out = decompress(buf)
            if take(out):
                return StreamResult(produced, False, True, 0)
            if not out and d.unconsumed_tail == buf:
                break  # no progress (should not happen); never loop forever
            buf = d.unconsumed_tail
            if d.eof:
                unused += len(d.unused_data)
    # All input given: collect any output zlib still holds back.
    while not d.eof:
        out = decompress(b"")
        if not out:
            break
        if take(out):
            return StreamResult(produced, False, True, 0)
        if d.eof:
            unused += len(d.unused_data)
    return StreamResult(produced, d.eof, False, unused)
