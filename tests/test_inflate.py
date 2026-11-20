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
