import struct

import pytest

from helpers.tiff import (
    ASCII, BYTE, DOUBLE, FLOAT, LONG, RATIONAL, SBYTE, SHORT, SLONG, SRATIONAL, SSHORT,
    UNDEFINED, build_tiff, exif_payload, ifd_bytes, tiff_header,
)
from metaex.core import exif
from metaex.core.errors import MalformedFileError
from metaex.core.exif import Rational, parse_exif, parse_tiff, parse_tiff_header, tiff_from_exif_payload


@pytest.mark.parametrize("order,expected", [("<", "little"), (">", "big")])
def test_tiff_header_both_byte_orders(order, expected):
    tiff = build_tiff([(0x010F, ASCII, "Maker")], order=order)
    header = parse_tiff_header(tiff)
    assert header.byte_order == expected
    assert header.ifd0_offset == 8


def test_exif_payload_prefix_is_removed():
    tiff = build_tiff([(0x010F, ASCII, "Maker")])
    assert tiff_from_exif_payload(exif_payload(tiff)) == tiff


@pytest.mark.parametrize("payload", [b"", b"Exif", b"EXIF\x00\x00II", b"JFIF\x00\x00"])
def test_bad_exif_payload(payload):
    with pytest.raises(MalformedFileError):
        tiff_from_exif_payload(payload)


@pytest.mark.parametrize(
    "tiff",
    [
        b"",
        b"II*",
        b"XX*\x00\x08\x00\x00\x00\x00\x00",
        b"II\x2b\x00\x08\x00\x00\x00\x00\x00",  # magic 43 (BigTIFF) is not EXIF
        b"MM\x2a\x00\x00\x00\x00\x08\x00\x00",  # little-endian magic in MM file
        tiff_header("<", ifd0_offset=4) + b"\x00\x00",
        tiff_header("<", ifd0_offset=10),  # past the end
    ],
)
def test_invalid_tiff_headers(tiff):
    with pytest.raises(MalformedFileError):
        parse_tiff_header(tiff)


# --- IFD entries and types -----------------------------------------------------

ALL_TYPES = [
    (0x0001, BYTE, 7, 7),
    (0x0002, BYTE, b"\x01\x02\x03\x04\x05", (1, 2, 3, 4, 5)),
    (0x0003, ASCII, "Hello camera", "Hello camera"),
    (0x0004, SHORT, 65535, 65535),
    (0x0005, SHORT, [1, 2, 3], (1, 2, 3)),
    (0x0006, LONG, 4_000_000_000, 4_000_000_000),
    (0x0007, RATIONAL, (1, 250), Rational(1, 250)),
    (0x0008, RATIONAL, [(1, 2), (3, 4)], (Rational(1, 2), Rational(3, 4))),
    (0x0009, SBYTE, -5, -5),
    (0x000A, UNDEFINED, b"0232", b"0232"),
    (0x000B, UNDEFINED, b"longer than four", b"longer than four"),
    (0x000C, SSHORT, -300, -300),
    (0x000D, SLONG, -70000, -70000),
    (0x000E, SRATIONAL, (-1, 3), Rational(-1, 3)),
    (0x000F, FLOAT, 1.5, 1.5),
    (0x0010, DOUBLE, -2.25, -2.25),
]


@pytest.mark.parametrize("order", ["<", ">"])
def test_all_types_decode_in_both_byte_orders(order):
    tiff = build_tiff([(tag, typ, value) for tag, typ, value, _ in ALL_TYPES], order=order)
    ifd0 = parse_tiff(tiff).ifd("IFD0")
    for tag, typ, _, expected in ALL_TYPES:
        entry = ifd0.get(tag)
        assert entry.type == typ
        assert entry.error is None
        assert entry.value == expected, hex(tag)


def test_inline_and_out_of_line_values():
    tiff = build_tiff([(0x0001, SHORT, [1, 2]), (0x0002, SHORT, [1, 2, 3])])
    ifd0 = parse_tiff(tiff).ifd("IFD0")
    assert ifd0.get(0x0001).value_offset is None  # 4 bytes: inline
    assert ifd0.get(0x0002).value_offset is not None  # 6 bytes: at an offset


def test_ascii_strips_nuls_and_replaces_bad_bytes():
    tiff = build_tiff([(0x010F, ASCII, b"Can\xffon\x00\x00"), (0x0110, ASCII, b"A\x00B\x00")])
    ifd0 = parse_tiff(tiff).ifd("IFD0")
    assert ifd0.value(0x010F) == "Can" + chr(0xFFFD) + "on"
    assert ifd0.value(0x0110) == "A / B"


def test_zero_denominator_rational_does_not_crash():
    tiff = build_tiff([(0x829A, RATIONAL, (1, 0))])
    value = parse_tiff(tiff).ifd("IFD0").value(0x829A)
    assert value == Rational(1, 0)
    assert value.as_float() is None
    assert str(value) == "1/0"


def test_unknown_type_is_recorded_not_fatal():
    tiff = build_tiff([(0x1234, 99, 1, b"\x00\x00\x00\x00"), (0x010F, ASCII, "Maker")])
    result = parse_tiff(tiff)
    ifd0 = result.ifd("IFD0")
    assert ifd0.get(0x1234).error.startswith("unknown value type 99")
    assert ifd0.value(0x010F) == "Maker"
    assert any("0x1234" in w for w in result.warnings)


def test_huge_count_is_rejected_by_bounds_check():
    tiff = build_tiff([(0x0001, LONG, 0xFFFFFFFF, struct.pack("<I", 8)), (0x010F, ASCII, "M")])
    result = parse_tiff(tiff)
    assert "outside the EXIF data" in result.ifd("IFD0").get(0x0001).error
    assert result.ifd("IFD0").value(0x010F) == "M"


def test_value_offset_past_end():
    tiff = build_tiff([(0x0001, ASCII, 100, struct.pack("<I", 5000))])
    assert "outside" in parse_tiff(tiff).ifd("IFD0").get(0x0001).error


def test_sub_ifds_are_followed():
    tiff = build_tiff([(0x010F, ASCII, "M")], exif=[(0x829D, RATIONAL, (18, 10))],
                      gps=[(0x0001, ASCII, "N")], interop=[(0x0001, ASCII, "R98")])
    result = parse_tiff(tiff)
    assert [i.name for i in result.ifds] == ["IFD0", "Exif", "GPS", "Interop"]
    assert result.ifd("Exif").value(0x829D) == Rational(18, 10)
    assert result.ifd("Interop").value(0x0001) == "R98"


def test_ifd1_follows_ifd0():
    tiff = build_tiff([(0x010F, ASCII, "M")], ifd1=[(0x0103, SHORT, 6)])
    assert [i.name for i in parse_tiff(tiff).ifds] == ["IFD0", "IFD1"]


def test_ifd_loop_is_detected():
    tiff = tiff_header("<") + ifd_bytes([(0x010F, ASCII, "M")], 8, "<", next_offset=8)
    result = parse_tiff(tiff)
    assert len(result.ifds) == 1
    assert any("loop" in w for w in result.warnings)


def test_sub_ifd_pointing_back_to_ifd0_is_a_loop():
    tiff = tiff_header("<") + ifd_bytes([(0x8769, LONG, 8)], 8, "<")
    result = parse_tiff(tiff)
    assert [i.name for i in result.ifds] == ["IFD0"]
    assert any("loop" in w for w in result.warnings)


def test_ifd_count_is_capped():
    out = bytearray(tiff_header("<"))
    for i in range(40):
        start = len(out)
        out += ifd_bytes([], start, "<", next_offset=start + 6)
    out += b"\x00\x00\x00\x00\x00\x00"
    result = parse_tiff(bytes(out))
    assert len(result.ifds) == exif.MAX_IFDS
    assert any("more than 32 IFDs" in w for w in result.warnings)


def test_entries_per_ifd_are_capped():
    entries = [(i, SHORT, i) for i in range(1, 1201)]
    result = parse_tiff(build_tiff(entries))
    assert len(result.ifd("IFD0").entries) == exif.MAX_ENTRIES_PER_IFD
    assert any("only the first 1000" in w for w in result.warnings)


def test_truncated_ifd_keeps_complete_entries():
    tiff = build_tiff([(0x0001, SHORT, 1), (0x0002, SHORT, 2), (0x0003, SHORT, 3)])
    result = parse_tiff(tiff[: 8 + 2 + 12 * 2 + 5])
    assert [e.tag for e in result.ifd("IFD0").entries] == [1, 2]
    assert any("truncated" in w for w in result.warnings)


def test_parse_exif_from_app1_payload():
    payload = exif_payload(build_tiff([(0x010F, ASCII, "Maker")], order=">"))
    assert parse_exif(payload).ifd("IFD0").value(0x010F) == "Maker"
