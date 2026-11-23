"""The Pillow pixel helper decodes known tiny images to known pixels."""

import struct
import zlib

import pytest

from helpers import gif, png, webp
from helpers.pixels import Frame, decode_frames, loop_count
from helpers.tiff import SHORT, build_tiff


def unfiltered(raw: bytes, width: int, channels: int = 3) -> bytes:
    """Scanlines without their filter bytes (the helpers write filter 0 only)."""
    stride = 1 + width * channels
    return b"".join(raw[i + 1:i + stride] for i in range(0, len(raw), stride))


def test_png_decodes_to_exact_pixels():
    rows = [bytes([255, 0, 0, 0, 255, 0]), bytes([0, 0, 255, 10, 20, 30])]
    raw = b"".join(b"\x00" + row for row in rows)
    data = (png.SIGNATURE + png.ihdr(2, 2) + png.chunk("IDAT", zlib.compress(raw))
            + png.chunk("IEND"))
    assert decode_frames(data) == [Frame("RGB", (2, 2), b"".join(rows), None)]


def test_gif_decodes_to_the_palette_indices():
    frames = decode_frames(gif.make_gif(gif.image(3, 2, seed=5), width=3, height=2))
    assert frames == [Frame("P", (3, 2), gif.pixels(3, 2, 4, seed=5), None)]


def test_lossless_webp_decodes_to_the_encoded_color():
    frames = decode_frames(webp.lossless(3, 2, argb=(255, 200, 100, 50)))
    assert frames == [Frame("RGB", (3, 2), bytes([200, 100, 50]) * 6, None)]


def test_lossless_webp_checkerboard():
    frames = decode_frames(webp.lossless(2, 2, argb=(255, 1, 7, 3), green2=99))
    # code 0 is the smaller green: (x + y) even -> 7, odd -> 99
    assert frames[0].pixels == bytes([1, 7, 3, 1, 99, 3, 1, 99, 3, 1, 7, 3])


def test_every_apng_frame_and_its_duration():
    frames = decode_frames(png.apng(frames=3))
    assert len(frames) == 3
    assert [f.duration for f in frames] == [100, 100, 100]  # fcTL delay 1/10 s
    assert frames[0].pixels == unfiltered(png.raw_image(4, 4), 4)
    for i in (1, 2):
        assert frames[i].pixels == unfiltered(png.raw_image(4, 4, seed=10 + i), 4)
    assert loop_count(png.apng()) == 0


def test_every_animated_gif_frame_duration_and_loop_count():
    frames = decode_frames(gif.animated_gif(frames=3, count=3))
    assert len(frames) == 3
    assert [f.duration for f in frames] == [100, 200, 300]
    assert all(f.size == (6, 5) for f in frames)
    assert loop_count(gif.animated_gif(count=3)) == 3
    assert loop_count(gif.animated_gif(count=0)) == 0


def test_every_animated_webp_frame_and_duration():
    frames = decode_frames(webp.animated(frames=3))
    assert [f.duration for f in frames] == [100, 101, 102]
    assert all(f.size == (6, 4) for f in frames)
    assert len({f.pixels for f in frames}) == 3
    assert loop_count(webp.animated()) == 0


def test_still_images_have_no_duration():
    assert decode_frames(png.make_png())[0].duration is None
    assert loop_count(png.make_png()) is None


def test_transpose_applies_exif_orientation():
    tiff = build_tiff([(0x0112, SHORT, 6)])
    data = png.make_png(png.exif_chunk(tiff), width=3, height=2)
    assert decode_frames(data)[0].size == (3, 2)
    assert decode_frames(data, transpose=True)[0].size == (2, 3)


def test_mode_converts_palette_indices_to_colors():
    data = png.palette_png(width=2, height=1)
    [indexed] = decode_frames(data)
    [rgba] = decode_frames(data, mode="RGBA")
    assert indexed.mode == "P" and rgba.mode == "RGBA"
    palette = [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
    alpha = [0, 128, 255, 255]
    expected = b"".join(bytes(palette[i]) + bytes([alpha[i]]) for i in indexed.pixels)
    assert rgba.pixels == expected


def test_reads_from_a_path(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(png.make_png())
    assert decode_frames(path) == decode_frames(png.make_png())
    assert decode_frames(str(path)) == decode_frames(png.make_png())


def test_truncated_image_raises_instead_of_decoding_partially():
    data = png.make_png(width=16, height=16)
    idat = data.index(b"IDAT")
    length = struct.unpack(">I", data[idat - 4:idat])[0]
    with pytest.raises(OSError):
        decode_frames(data[:idat + 4 + length // 2])


def test_not_an_image_raises():
    with pytest.raises(OSError):
        decode_frames(b"not an image")
