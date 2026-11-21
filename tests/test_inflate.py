"""Capped zlib decompression."""

import zlib

import pytest

from helpers.png import zlib_bomb
from metaex.core.errors import MalformedFileError
from metaex.core.inflate import Budget, DecompressionLimitError, inflate


def test_inflate_roundtrip():
    result = inflate(zlib.compress(b"hello" * 100))
    assert result.data == b"hello" * 100 and result.extra == 0


def test_budget_is_shared_and_reduced():
    budget = Budget(1000)
    inflate(zlib.compress(bytes(600)), budget)
    assert budget.remaining == 400
    with pytest.raises(DecompressionLimitError):
        inflate(zlib.compress(bytes(600)), budget)
    assert budget.remaining == 0
    with pytest.raises(DecompressionLimitError):  # used up: even small data fails
        inflate(zlib.compress(b"x"), budget)


def test_exactly_the_limit_is_allowed():
    budget = Budget(100)
    assert len(inflate(zlib.compress(bytes(100)), budget).data) == 100
    assert budget.remaining == 0


def test_bomb_is_stopped_without_expanding_it():
    bomb = zlib_bomb(200 * 1024 * 1024)  # 200 MiB of zeros in ~200 KB
    assert len(bomb) < 1024 * 1024
    with pytest.raises(DecompressionLimitError, match="16 MiB limit"):
        inflate(bomb)


def test_extra_bytes_after_the_stream_are_counted():
    assert inflate(zlib.compress(b"abc") + b"hidden").extra == 6


@pytest.mark.parametrize("data", [b"", b"not zlib", zlib.compress(b"abcdef" * 50)[:-6]])
def test_invalid_or_incomplete_streams(data):
    with pytest.raises(MalformedFileError):
        inflate(data)


def test_negative_limit_is_rejected():
    with pytest.raises(ValueError):
        Budget(-1)


# -- inflate_stream ----------------------------------------------------------------------

from metaex.core.inflate import inflate_stream  # noqa: E402

DATA = bytes(range(256)) * 4000  # ~1 MB, compresses well but not trivially


def pieces(data: bytes, size: int) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)]


@pytest.mark.parametrize("piece", [1, 7, 1000, 10**7])
@pytest.mark.parametrize("step", [1, 4096, 1 << 20])
def test_stream_output_matches_zlib(piece, step):
    if piece == 1 or step == 1:
        data = DATA[:5000]  # byte-sized steps are slow; a small input is enough
    else:
        data = DATA
    out = []
    compressed = zlib.compress(data, 9)
    r = inflate_stream(pieces(compressed, piece), len(data) + 10, out.append, step=step)
    assert b"".join(out) == data
    assert (r.size, r.complete, r.more, r.unused) == (len(data), True, False, 0)
    assert max(len(p) for p in out) <= step


def test_stream_counts_input_after_the_end():
    compressed = zlib.compress(DATA)
    r = inflate_stream([compressed[:-2], compressed[-2:] + b"abc", b"defg"], len(DATA))
    assert r.complete and r.unused == 7 and r.size == len(DATA)


def test_stream_stops_at_the_limit():
    out = []
    r = inflate_stream([zlib.compress(DATA)], 1000, out.append)
    assert (r.size, r.complete, r.more) == (1000, False, True)
    assert b"".join(out) == DATA[:1000]
    exact = inflate_stream([zlib.compress(DATA)], len(DATA))
    assert exact.complete and not exact.more


def test_stream_bomb_is_bounded():
    import time

    start = time.monotonic()
    r = inflate_stream([zlib_bomb(256 * 1024 * 1024)], 16 * 1024 * 1024)
    assert r.more and r.size == 16 * 1024 * 1024
    assert time.monotonic() - start < 10


def test_stream_incomplete_and_invalid():
    c = zlib.compressobj()
    partial = c.compress(DATA) + c.flush(zlib.Z_SYNC_FLUSH)
    r = inflate_stream([partial], len(DATA) + 1)
    assert r.size == len(DATA) and not r.complete and not r.more
    short = inflate_stream([zlib.compress(DATA)[:100]], len(DATA))
    assert not short.complete and short.size < len(DATA)
    with pytest.raises(MalformedFileError):
        inflate_stream([b"not zlib data at all"], 100)
    assert inflate_stream([], 100) == inflate_stream([b""], 100)


def test_stream_rejects_bad_arguments():
    with pytest.raises(ValueError):
        inflate_stream([b""], -1)
    with pytest.raises(ValueError):
        inflate_stream([b""], 1, step=0)
