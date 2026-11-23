"""Stripping never changes what an image shows.

Pillow, a decoder independent of metaex, decodes the original and the
stripped copy; mode, size and pixels must be identical for every frame,
and so must the displayed orientation (after ImageOps.exif_transpose).
Each format also has a test that damages the image data of a stripped
copy on purpose and confirms the check catches it.
"""

import subprocess
import sys
import zlib

import pytest

from helpers import png as png_helpers
from helpers.jpeg import (
    corrupt_scan, kitchen_sink_jpeg, sample_photo, sample_photo_rotated,
)
from helpers.pixels import decode_frames, loop_count
from metaex.formats import jpeg, png


def decoded(data: bytes, transpose: bool = False):
    try:
        return decode_frames(data, transpose=transpose)
    except OSError as exc:
        raise AssertionError(f"Pillow can't decode the image: {exc}") from None


def assert_same_image(original: bytes, cleaned: bytes, *, primary_only: bool = False) -> None:
    """Same mode, size and pixels for every frame (with their durations and
    the loop count), stored and as displayed. With ``primary_only``, only
    the original's first frame is compared (a JPEG's MPF extra images are
    removed by design) and the cleaned copy must have exactly one."""
    for transpose in (False, True):
        before, after = decoded(original, transpose), decoded(cleaned, transpose)
        if primary_only:
            before = before[:1]
        assert len(after) == len(before), "number of frames differs"
        for n, (a, b) in enumerate(zip(before, after), 1):
            what = f"frame {n}" + (" as displayed" if transpose else "")
            assert (a.mode, a.size) == (b.mode, b.size), f"{what}: mode or size differs"
            assert a.pixels == b.pixels, f"{what}: pixels differ"
            assert a.duration == b.duration, f"{what}: duration differs"
    assert loop_count(original) == loop_count(cleaned), "loop count differs"


def displayed_size(data: bytes) -> tuple[int, int]:
    return decode_frames(data, transpose=True)[0].size


# -- JPEG --------------------------------------------------------------------------------------

JPEG_SAMPLES = {
    "sample_photo": sample_photo,
    "sample_photo_motorola": lambda: sample_photo(">"),
    "sample_photo_rotated": sample_photo_rotated,
    "sample_photo_rotated_motorola": lambda: sample_photo_rotated(">"),
    "kitchen_sink": kitchen_sink_jpeg,
    "kitchen_sink_motorola": lambda: kitchen_sink_jpeg(">"),
}
OPTIONS = {"default": {}, "paranoid": {"paranoid": True}}


@pytest.mark.parametrize("options", sorted(OPTIONS))
@pytest.mark.parametrize("name", sorted(JPEG_SAMPLES))
def test_jpeg_strip_keeps_pixels_and_orientation(name, options):
    original = JPEG_SAMPLES[name]()
    cleaned = jpeg.strip(original, **OPTIONS[options])
    assert cleaned != original
    assert_same_image(original, cleaned, primary_only=True)


def test_rotated_jpeg_really_is_displayed_rotated():
    """So the orientation comparison above is not trivially true."""
    cleaned = jpeg.strip(sample_photo_rotated())
    assert displayed_size(sample_photo_rotated()) == displayed_size(cleaned) == (32, 48)
    assert decode_frames(cleaned)[0].size == (48, 32)


def test_jpeg_no_orientation_keeps_pixels_but_not_the_rotation():
    """--no-orientation drops the Orientation tag on purpose: the stored
    pixels are identical, but the picture is no longer shown turned."""
    original = sample_photo_rotated()
    cleaned = jpeg.strip(original, keep_orientation=False)
    assert decode_frames(cleaned) == decode_frames(original)
    assert displayed_size(original) == (32, 48)
    assert displayed_size(cleaned) == (48, 32)


@pytest.mark.parametrize("where", [0.1, 0.5, 0.9])
def test_jpeg_pixel_check_catches_damaged_scan_data(where):
    original = kitchen_sink_jpeg()
    cleaned = jpeg.strip(original)
    assert_same_image(original, cleaned, primary_only=True)
    with pytest.raises(AssertionError, match="pixels differ|can't decode"):
        assert_same_image(original, corrupt_scan(cleaned, where), primary_only=True)


# -- PNG ---------------------------------------------------------------------------------------

PNG_SAMPLES = {
    "kitchen_sink": png_helpers.kitchen_sink_png,
    "kitchen_sink_motorola": lambda: png_helpers.kitchen_sink_png(">"),
    "apng": png_helpers.apng,
    **{name: (lambda name=name: png_helpers.variants()[name]) for name in png_helpers.variants()},
}


@pytest.mark.parametrize("options", sorted(OPTIONS))
@pytest.mark.parametrize("name", sorted(PNG_SAMPLES))
def test_png_strip_keeps_pixels_and_orientation(name, options):
    original = PNG_SAMPLES[name]()
    cleaned = png.strip(original, **OPTIONS[options])
    assert cleaned != original
    assert_same_image(original, cleaned)


def test_kitchen_sink_png_really_is_animated_and_rotated():
    """So the frame and orientation comparisons above are not trivially true."""
    cleaned = png.strip(png_helpers.kitchen_sink_png())
    stored, shown = decode_frames(cleaned), decode_frames(cleaned, transpose=True)
    assert len(stored) == 2
    # Orientation 6 is kept: the (square) picture is shown turned, not as stored.
    assert shown[0].pixels != stored[0].pixels


@pytest.mark.parametrize("idat", [zlib.compress(png_helpers.raw_image() + b"a hidden message"),
                                  zlib.compress(png_helpers.raw_image()) + b"hidden!"])
def test_png_rebuilt_image_data_keeps_pixels(idat):
    original = (png_helpers.SIGNATURE + png_helpers.ihdr() + png_helpers.chunk("IDAT", idat)
                + png_helpers.chunk("IEND"))
    cleaned = png.strip(original, rebuild_image_data=True)
    assert cleaned != original
    assert_same_image(original, cleaned)


def chunks(data: bytes):
    pos = 8
    while pos < len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        yield data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        pos += 12 + length


def with_damaged_pixels(data: bytes, chunk_type: bytes) -> bytes:
    """``data`` with one pixel byte changed in the first IDAT (all IDATs as
    one stream) or fdAT image, compressed again with correct CRCs: a valid
    PNG that differs from the original in exactly one decompressed byte."""
    width = int.from_bytes(data[16:20], "big")
    stride = 1 + 3 * width  # the samples are 8-bit RGB
    out, done = [], False
    all_chunks = list(chunks(data))
    for kind, body in all_chunks:
        if kind == chunk_type and not done:
            prefix = body[:4] if kind == b"fdAT" else b""
            stream = (b"".join(b for k, b in all_chunks if k == b"IDAT") if kind == b"IDAT"
                      else body[4:])
            raw = bytearray(zlib.decompress(stream))
            i = len(raw) // 2
            if i % stride == 0:
                i += 1  # a pixel byte, not a filter type byte
            raw[i] ^= 0x01
            out.append(png_helpers.chunk(kind, prefix + zlib.compress(bytes(raw))))
            done = True
        elif kind == b"IDAT" and chunk_type == b"IDAT" and done:
            continue  # merged into the first IDAT
        else:
            out.append(png_helpers.chunk(kind, body))
    assert done
    return data[:8] + b"".join(out)


@pytest.mark.parametrize("build,chunk_type", [(png_helpers.kitchen_sink_png, b"IDAT"),
                                              (png_helpers.apng, b"IDAT"),
                                              (png_helpers.apng, b"fdAT")])
def test_png_pixel_check_catches_damaged_image_data(build, chunk_type):
    original = build()
    cleaned = png.strip(original)
    assert_same_image(original, cleaned)
    damaged = with_damaged_pixels(cleaned, chunk_type)
    with pytest.raises(AssertionError, match="pixels differ"):
        assert_same_image(original, damaged)


# -- through the CLI ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,data", [("photo.jpg", kitchen_sink_jpeg()),
                                       ("image.png", png_helpers.kitchen_sink_png())])
def test_cli_strip_output_has_identical_pixels(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    result = subprocess.run([sys.executable, "-m", "metaex", "strip", str(path)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    cleaned = (tmp_path / name.replace(".", "_clean.")).read_bytes()
    assert_same_image(data, cleaned, primary_only=name.endswith(".jpg"))
