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


# --- edge cases ---------------------------------------------------------------


def test_empty_data():
    r = ByteReader(b"")
    assert len(r) == 0
    assert r.remaining == 0
    assert r.read(0) == b""
    assert r.peek(0) == b""
    for method in (r.u8, r.u16, r.u32, r.i32, r.u64):
        with pytest.raises(MalformedFileError):
            method()
    with pytest.raises(MalformedFileError):
        r.read(1)


def test_reading_exactly_to_the_end_then_one_more_byte():
    r = ByteReader(b"\x01\x02\x03\x04")
    assert r.u32() == 0x01020304
    assert r.remaining == 0
    assert r.read(0) == b""
    with pytest.raises(MalformedFileError) as exc:
        r.u8("trailing byte")
    assert "offset 4" in str(exc.value)
    assert "need 1 bytes, 0 available" in str(exc.value)


@pytest.mark.parametrize("width,method", [(2, "u16"), (4, "u32"), (4, "i32"), (8, "u64")])
def test_integer_one_byte_short_does_not_advance(width, method):
    r = ByteReader(b"\x00" * (width - 1))
    with pytest.raises(MalformedFileError):
        getattr(r, method)()
    assert r.tell() == 0


def test_failed_read_does_not_move_position():
    r = ByteReader(b"abc")
    r.read(1)
    with pytest.raises(MalformedFileError):
        r.read(5)
    assert r.tell() == 1
    assert r.read(2) == b"bc"


@pytest.mark.parametrize("n", [-1, -(2**63)])
def test_negative_lengths_are_rejected(n):
    r = ByteReader(b"abc")
    with pytest.raises(MalformedFileError, match="negative"):
        r.read(n)
    with pytest.raises(MalformedFileError, match="negative"):
        r.peek(n)
    with pytest.raises(MalformedFileError, match="negative"):
        r.skip(n)
    assert r.tell() == 0


@pytest.mark.parametrize("n", [4, 2**31, 2**64, 10**100])
def test_huge_lengths_are_rejected(n):
    r = ByteReader(b"abc")
    with pytest.raises(MalformedFileError):
        r.read(n)
    with pytest.raises(MalformedFileError):
        r.peek(n)
    with pytest.raises(MalformedFileError):
        r.skip(n)


@pytest.mark.parametrize("bad", [1.0, "1", None, True])
def test_non_integer_lengths_are_programming_errors(bad):
    r = ByteReader(b"abc")
    with pytest.raises(TypeError):
        r.read(bad)


def test_seek_within_range_including_end():
    r = ByteReader(b"abcd")
    r.seek(4)
    assert r.remaining == 0
    r.seek(0)
    assert r.read(1) == b"a"


@pytest.mark.parametrize("offset", [-1, 5, 2**64])
def test_seek_out_of_range(offset):
    r = ByteReader(b"abcd")
    r.read(2)
    with pytest.raises(MalformedFileError):
        r.seek(offset)
    assert r.tell() == 2


def test_both_endians_on_the_same_bytes():
    data = bytes.fromhex("0102030405060708")
    big = ByteReader(data, endian="big")
    little = ByteReader(data, endian="little")
    assert big.u64() == 0x0102030405060708
    assert little.u64() == 0x0807060504030201


def test_endian_can_be_switched_mid_stream():
    r = ByteReader(bytes.fromhex("0001 0100"))
    assert r.u16() == 1
    r.endian = "little"
    assert r.u16() == 1


def test_invalid_endian_is_rejected():
    with pytest.raises(ValueError):
        ByteReader(b"", endian="middle")
    r = ByteReader(b"")
    with pytest.raises(ValueError):
        r.endian = "BIG"


def test_signed_and_unsigned_32_bit():
    r = ByteReader(bytes.fromhex("80000000 80000000"))
    assert r.u32() == 2**31
    assert r.i32() == -(2**31)


def test_accepts_bytearray_and_memoryview():
    assert ByteReader(bytearray(b"\x00\x01")).u16() == 1
    assert ByteReader(memoryview(b"\x00\x01")).u16() == 1


def test_returned_bytes_are_independent_copies():
    buf = bytearray(b"abc")
    r = ByteReader(buf)
    out = r.read(3)
    assert isinstance(out, bytes)
    buf[0] = ord("z")
    assert out == b"abc"


def test_sub_reader_inherits_endian():
    r = ByteReader(bytes.fromhex("ff 0100"), endian="little")
    assert r.sub(1, 2).u16() == 1


def test_sub_reader_cannot_seek_or_read_outside_its_slice():
    r = ByteReader(b"0123456789")
    s = r.sub(4, 2)
    with pytest.raises(MalformedFileError):
        s.seek(3)
    with pytest.raises(MalformedFileError):
        s.peek(3)
    s.seek(2)
    with pytest.raises(MalformedFileError):
        s.u8()


@pytest.mark.parametrize(
    "offset,length",
    [(-1, 1), (0, -1), (11, 0), (5, 6), (10, 1), (0, 2**64), (2**64, 0)],
)
def test_sub_range_outside_data_is_rejected(offset, length):
    r = ByteReader(b"0123456789")
    with pytest.raises(MalformedFileError):
        r.sub(offset, length)


def test_sub_range_touching_the_end_is_allowed():
    r = ByteReader(b"0123456789")
    assert r.sub(10, 0).remaining == 0
    assert r.sub(0, 10).read(10) == b"0123456789"


def test_nested_sub_readers_cannot_escape():
    outer = ByteReader(b"AAAAbbbbCCCC")
    middle = outer.sub(4, 4)
    inner = middle.sub(1, 2)
    assert inner.read(2) == b"bb"
    with pytest.raises(MalformedFileError):
        inner.read(1)
    # Asking the middle reader for a range that would reach into "CCCC".
    with pytest.raises(MalformedFileError):
        middle.sub(2, 4)
    # A sub-reader of a sub-reader starting at its end is empty, not "CCCC".
    assert middle.sub(4, 0).remaining == 0


def test_nested_error_reports_absolute_offset():
    outer = ByteReader(b"\x00" * 100)
    inner = outer.sub(40, 10).sub(5, 2)
    inner.read(2)
    with pytest.raises(MalformedFileError) as exc:
        inner.u8("tag")
    assert "offset 47" in str(exc.value)
