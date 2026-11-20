import pytest

from metaex.core.errors import MalformedFileError, MetaexError
from metaex.core.reader import ByteReader


def test_errors_share_a_base_class():
    assert issubclass(MalformedFileError, MetaexError)


def test_read_advances_and_returns_bytes():
    r = ByteReader(b"abcdef")
    assert r.read(2) == b"ab"
    assert r.tell() == 2
    assert r.remaining == 4
    assert r.read(4) == b"cdef"
    assert r.remaining == 0


def test_peek_does_not_advance():
    r = ByteReader(b"xyz")
    assert r.peek(2) == b"xy"
    assert r.tell() == 0


def test_integers_big_endian_by_default():
    r = ByteReader(bytes.fromhex("01 0203 04050607 08090a0b0c0d0e0f ffffffff"))
    assert r.u8() == 0x01
    assert r.u16() == 0x0203
    assert r.u32() == 0x04050607
    assert r.u64() == 0x08090A0B0C0D0E0F
    assert r.i32() == -1


def test_integers_little_endian():
    r = ByteReader(bytes.fromhex("0201 07060504"), endian="little")
    assert r.u16() == 0x0102
    assert r.u32() == 0x04050607


def test_read_past_end_raises_with_offset_and_context():
    r = ByteReader(b"abc")
    r.read(2)
    with pytest.raises(MalformedFileError) as exc:
        r.u16("chunk length")
    message = str(exc.value)
    assert "chunk length" in message
    assert "offset 2" in message


def test_sub_reader_is_limited_to_its_slice():
    r = ByteReader(b"0123456789")
    s = r.sub(2, 3)
    assert len(s) == 3
    assert s.read(3) == b"234"
    with pytest.raises(MalformedFileError):
        s.read(1)
    assert r.tell() == 0
