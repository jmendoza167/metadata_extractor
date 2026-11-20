import pytest

from helpers.tiff import ASCII, build_tiff, exif_payload, tiff_header
from metaex.core.errors import MalformedFileError
from metaex.core.exif import parse_tiff_header, tiff_from_exif_payload


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
