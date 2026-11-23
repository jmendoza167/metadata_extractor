"""The test JPEG encoder writes real baseline JPEGs that Pillow decodes to
the expected pattern (JPEG is lossy, so within a small tolerance)."""

import io

import pytest
from PIL import Image

from helpers import jpeg_encoder as enc
from helpers.jpeg import (
    PHOTO_HEIGHT, PHOTO_WIDTH, corrupt_scan, kitchen_sink_jpeg, sample_photo,
    sample_photo_rotated, scan_range,
)
from helpers.pixels import decode_frames

MAX_ERROR = 8
"""Largest difference per channel allowed between the pattern and Pillow's decode."""
MAX_MEAN_ERROR = 2.0


def gray(rgb: bytes) -> bytes:
    return bytes(round(0.299 * rgb[i] + 0.587 * rgb[i + 1] + 0.114 * rgb[i + 2])
                 for i in range(0, len(rgb), 3))


def assert_close(decoded: bytes, expected: bytes) -> None:
    assert len(decoded) == len(expected)
    diffs = [abs(a - b) for a, b in zip(decoded, expected)]
    assert max(diffs) <= MAX_ERROR, max(diffs)
    assert sum(diffs) / len(diffs) <= MAX_MEAN_ERROR


def pillow_segments(data: bytes) -> dict[int, list[bytes]]:
    """Marker -> payloads of the segments before the first SOS."""
    out: dict[int, list[bytes]] = {}
    i = 2
    while data[i + 1] != 0xDA:
        length = int.from_bytes(data[i + 2:i + 4], "big")
        out.setdefault(data[i + 1], []).append(data[i + 4:i + 2 + length])
        i += 2 + length
    return out


@pytest.mark.parametrize("width,height", [(48, 32), (37, 21), (8, 8), (9, 17), (1, 1)])
def test_color_image_decodes_to_the_pattern(width, height):
    frames = decode_frames(enc.jpeg_file(width=width, height=height))
    assert len(frames) == 1
    assert (frames[0].mode, frames[0].size) == ("RGB", (width, height))
    assert_close(frames[0].pixels, enc.pattern(width, height))


def test_grayscale_image_decodes_to_the_pattern_luma():
    frames = decode_frames(enc.jpeg_file(width=24, height=16, color=False))
    assert (frames[0].mode, frames[0].size) == ("L", (24, 16))
    assert_close(frames[0].pixels, gray(enc.pattern(24, 16)))


def test_pattern_is_not_flat():
    p = enc.pattern(48, 32)
    for channel in range(3):
        assert len(set(p[channel::3])) > 1
    assert len(set(zip(p[0::3], p[1::3], p[2::3]))) > 100


def test_damaged_scan_data_changes_the_decoded_pixels():
    data = enc.jpeg_file()
    good = decode_frames(data)[0].pixels
    for where in (0.1, 0.5, 0.9):
        damaged = corrupt_scan(data, where)
        assert damaged != data
        try:
            pixels = decode_frames(damaged)[0].pixels
        except OSError:
            continue  # a decoder error is fine too: the damage is visible
        assert pixels != good


def test_tables_are_the_standard_ones_pillow_writes():
    """At quality 50 libjpeg (through Pillow) writes the T.81 Annex K tables
    unscaled, and the standard Huffman tables unless asked to optimize."""
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (10, 200, 30)).save(buf, "JPEG", quality=50, subsampling=0)
    theirs = pillow_segments(buf.getvalue())
    quant = Image.open(io.BytesIO(buf.getvalue())).quantization
    assert tuple(quant[0]) == enc.STD_LUMINANCE_QUANT
    assert tuple(quant[1]) == enc.STD_CHROMINANCE_QUANT
    assert enc.scaled_table(enc.STD_LUMINANCE_QUANT, 50) == enc.STD_LUMINANCE_QUANT

    pillow_tables = {}
    for payload in theirs[0xC4]:
        i = 0
        while i < len(payload):  # a DHT segment may hold several tables
            klass_id, bits = payload[i], tuple(payload[i + 1:i + 17])
            values = tuple(payload[i + 17:i + 17 + sum(bits)])
            pillow_tables[klass_id] = (bits, values)
            i += 17 + sum(bits)
    assert pillow_tables == {0x00: enc.DC_LUMINANCE, 0x10: enc.AC_LUMINANCE,
                             0x01: enc.DC_CHROMINANCE, 0x11: enc.AC_CHROMINANCE}


def test_zigzag_order():
    assert enc.ZIGZAG[:10] == (0, 1, 8, 16, 9, 2, 3, 10, 17, 24)
    assert enc.ZIGZAG[-3:] == (55, 62, 63)
    assert sorted(enc.ZIGZAG) == list(range(64))


def test_bit_writer_stuffs_ff_bytes_and_pads_with_ones():
    bits = enc._BitWriter()
    bits.put(0xFF, 8)
    bits.put(0b101, 3)
    assert bits.flush() == b"\xff\x00\xbf"


def test_scan_range_covers_the_entropy_coded_data():
    data = enc.jpeg_file()
    start, end = scan_range(data)
    assert data[end:] == b"\xff\xd9"
    assert data[start - 14:start - 12] == b"\xff\xda"  # SOS with 3 components: 12-byte length


@pytest.mark.parametrize("bad", [dict(width=0, height=1, rgb=b""),
                                 dict(width=2, height=2, rgb=b"\x00" * 11)])
def test_encoder_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        enc.encode(bad["width"], bad["height"], bad["rgb"])


@pytest.mark.parametrize("build", [sample_photo, kitchen_sink_jpeg])
def test_samples_hold_the_real_pattern(build):
    frames = decode_frames(build())
    assert (frames[0].mode, frames[0].size) == ("RGB", (PHOTO_WIDTH, PHOTO_HEIGHT))
    assert_close(frames[0].pixels, enc.pattern(PHOTO_WIDTH, PHOTO_HEIGHT))


def test_rotated_sample_is_displayed_turned_clockwise():
    data = sample_photo_rotated()
    stored = decode_frames(data)[0]
    shown = decode_frames(data, transpose=True)[0]
    assert stored.size == (PHOTO_WIDTH, PHOTO_HEIGHT)
    assert shown.size == (PHOTO_HEIGHT, PHOTO_WIDTH)
    expected = Image.frombytes("RGB", stored.size, stored.pixels)
    expected = expected.transpose(Image.Transpose.ROTATE_270)  # 90 degrees clockwise
    assert shown.pixels == expected.tobytes()
    # The unrotated sample is shown as stored.
    assert decode_frames(sample_photo(), transpose=True)[0].size == (PHOTO_WIDTH, PHOTO_HEIGHT)
