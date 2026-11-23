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

from helpers import gif as gif_helpers
from helpers import png as png_helpers
from helpers import webp as webp_helpers
from helpers.jpeg import (
    corrupt_scan, kitchen_sink_jpeg, sample_photo, sample_photo_rotated,
)
from helpers.pixels import decode_frames, loop_count
from metaex.formats import gif, jpeg, png, webp


def decoded(data: bytes, transpose: bool = False, mode: str | None = None):
    try:
        return decode_frames(data, transpose=transpose, mode=mode)
    except OSError as exc:
        raise AssertionError(f"Pillow can't decode the image: {exc}") from None


def assert_same_image(original: bytes, cleaned: bytes, *, primary_only: bool = False) -> None:
    """Same mode, size and pixels for every frame (with their durations and
    the loop count): as stored, as displayed (EXIF orientation applied) and
    converted to RGBA (so palette colors and transparency count, not only
    palette indices). With ``primary_only``, only the original's first
    frame is compared (a JPEG's MPF extra images are removed by design)
    and the cleaned copy must have exactly one."""
    for transpose, mode in ((False, None), (True, None), (False, "RGBA")):
        before, after = decoded(original, transpose, mode), decoded(cleaned, transpose, mode)
        if primary_only:
            before = before[:1]
        assert len(after) == len(before), "number of frames differs"
        for n, (a, b) in enumerate(zip(before, after), 1):
            what = (f"frame {n}" + (" as displayed" if transpose else "")
                    + (f" in {mode}" if mode else ""))
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


# -- GIF ---------------------------------------------------------------------------------------

GIF_SAMPLES = {
    "kitchen_sink": gif_helpers.kitchen_sink_gif,
    "animated_forever": gif_helpers.animated_gif,
    "animated_3_loops": lambda: gif_helpers.animated_gif(count=3),
    **{name: (lambda name=name: gif_helpers.variants()[name]) for name in gif_helpers.variants()},
}


@pytest.mark.parametrize("options", sorted(OPTIONS))
@pytest.mark.parametrize("name", sorted(GIF_SAMPLES))
def test_gif_strip_keeps_every_frame_duration_and_loop_count(name, options):
    original = GIF_SAMPLES[name]()
    cleaned = gif.strip(original, **OPTIONS[options])
    assert_same_image(original, cleaned)


def test_gif_animations_have_real_timing_to_compare():
    """So the duration and loop count comparisons above are not trivially true."""
    kitchen = gif.strip(gif_helpers.kitchen_sink_gif())
    assert [f.duration for f in decode_frames(kitchen)] == [200, 300, 400]
    assert loop_count(kitchen) == 0
    assert loop_count(gif.strip(gif_helpers.animated_gif(count=3))) == 3
    assert loop_count(gif.strip(gif_helpers.variants()["static"])) is None


def replace_once(data: bytes, old: bytes, new: bytes) -> bytes:
    assert data.count(old) == 1
    return data.replace(old, new)


def test_gif_pixel_check_catches_a_changed_pixel_in_a_later_frame():
    """Frame 2 of animated_gif() written again with one pixel changed: a
    valid GIF that differs only there."""
    original = gif_helpers.animated_gif()
    cleaned = gif.strip(original)
    assert_same_image(original, cleaned)
    frame2 = dict(width=5, height=4, left=1, top=1, seed=2,
                  local_table=gif_helpers.palette(4, seed=50))
    indices = bytearray(gif_helpers.pixels(5, 4, 4, seed=2))
    i = next(i for i in range(len(indices) // 2, len(indices))
             if indices[i] != 1)  # 1 is frame 2's transparent index
    indices[i] = 0 if indices[i] != 0 else 2
    damaged = replace_once(cleaned, gif_helpers.image(**frame2), gif_helpers.image(
        **frame2, lzw=gif_helpers.lzw_encode(bytes(indices), 2)))
    with pytest.raises(AssertionError, match="frame 2.*pixels differ"):
        assert_same_image(original, damaged)


def test_gif_pixel_check_catches_a_changed_palette_color():
    """Same indices, different color: only the RGBA comparison can see it."""
    original = gif_helpers.variants()["static"]
    cleaned = gif.strip(original)
    damaged = bytearray(cleaned)
    damaged[13] ^= 0x40  # red of global color table entry 0
    with pytest.raises(AssertionError, match="frame 1 in RGBA: pixels differ"):
        assert_same_image(original, bytes(damaged))


def test_gif_check_catches_a_changed_duration_and_loop_count():
    original = gif_helpers.animated_gif(count=3)
    cleaned = gif.strip(original)
    gce = cleaned.index(b"\x21\xf9\x04")
    slower = cleaned[:gce + 4] + b"\x63\x00" + cleaned[gce + 6:]
    with pytest.raises(AssertionError, match="frame 1: duration differs"):
        assert_same_image(original, slower)
    loops = replace_once(cleaned, b"NETSCAPE2.0\x03\x01\x03\x00", b"NETSCAPE2.0\x03\x01\x05\x00")
    with pytest.raises(AssertionError, match="loop count differs"):
        assert_same_image(original, loops)


# -- WebP --------------------------------------------------------------------------------------

WEBP_SAMPLES = {
    "kitchen_sink": webp_helpers.kitchen_sink_webp,
    "kitchen_sink_motorola": lambda: webp_helpers.kitchen_sink_webp(">"),
    "animated_forever": webp_helpers.animated,
    **{name: (lambda name=name: webp_helpers.variants()[name])
       for name in webp_helpers.variants()},
}


@pytest.mark.parametrize("options", sorted(OPTIONS))
@pytest.mark.parametrize("name", sorted(WEBP_SAMPLES))
def test_webp_strip_keeps_every_frame_duration_and_loop_count(name, options):
    original = WEBP_SAMPLES[name]()
    cleaned = webp.strip(original, **OPTIONS[options])
    assert_same_image(original, cleaned)


def test_webp_samples_have_real_timing_and_orientation_to_compare():
    """So the duration, loop count and orientation comparisons above are not
    trivially true."""
    kitchen = webp.strip(webp_helpers.kitchen_sink_webp())
    assert [f.duration for f in decode_frames(kitchen)] == [120, 80]
    assert displayed_size(kitchen) == (6, 8)  # Orientation 6 kept: 8x6 shown turned
    assert decode_frames(kitchen)[0].size == (8, 6)
    assert loop_count(webp.strip(webp_helpers.variants()["animated_lossy"])) == 2


def anmf_payload(data: bytes, n: int) -> int:
    """Offset of the payload of the n-th (1-based) ANMF chunk."""
    pos = -1
    for _ in range(n):
        pos = data.index(b"ANMF", pos + 1)
    return pos + 8


def test_webp_pixel_check_catches_a_changed_pixel_in_a_later_frame():
    """Frame 2's VP8L bitstream replaced by one of another red (same size,
    so the file stays valid)."""
    original = webp_helpers.animated()
    cleaned = webp.strip(original)
    assert_same_image(original, cleaned)
    old = webp_helpers.vp8l(4, 3, green2=10)
    new = webp_helpers.vp8l(4, 3, argb=(255, 201, 100, 50), green2=10)
    assert len(new) == len(old)
    with pytest.raises(AssertionError, match="frame 2.*pixels differ"):
        assert_same_image(original, replace_once(cleaned, old, new))


def test_webp_check_catches_a_changed_duration_and_loop_count():
    original = webp_helpers.animated()
    cleaned = webp.strip(original)
    duration = anmf_payload(cleaned, 2) + 12
    slower = cleaned[:duration] + (500).to_bytes(3, "little") + cleaned[duration + 3:]
    with pytest.raises(AssertionError, match="frame 2: duration differs"):
        assert_same_image(original, slower)
    anim = cleaned.index(b"ANIM") + 8
    loops = cleaned[:anim + 4] + (5).to_bytes(2, "little") + cleaned[anim + 6:]
    with pytest.raises(AssertionError, match="loop count differs"):
        assert_same_image(original, loops)


# -- through the CLI ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,data", [("photo.jpg", kitchen_sink_jpeg()),
                                       ("image.png", png_helpers.kitchen_sink_png()),
                                       ("anim.gif", gif_helpers.kitchen_sink_gif()),
                                       ("image.webp", webp_helpers.kitchen_sink_webp())])
def test_cli_strip_output_has_identical_pixels(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    result = subprocess.run([sys.executable, "-m", "metaex", "strip", str(path)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    cleaned = (tmp_path / name.replace(".", "_clean.")).read_bytes()
    assert_same_image(data, cleaned, primary_only=name.endswith(".jpg"))
