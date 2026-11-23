"""Every image the test helpers generate opens and fully decodes in Pillow.

This cross-checks the helpers' own encoders (GIF LZW, VP8L, Adam7
interlacing, the baseline JPEG encoder) against a real decoder: for those,
the decoded pixels must be exactly the ones that were encoded.
"""

import pytest
from PIL import Image

from helpers import gif, jpeg, jpeg_encoder, png, webp
from helpers.pixels import Frame, decode_frames, open_image

FORMATS = {"jpeg": {"JPEG", "MPO"}, "png": {"PNG"}, "gif": {"GIF"}, "webp": {"WEBP"}}
"""Pillow's format names (a JPEG with MPF extra images opens as MPO)."""

GENERATED = {
    # JPEG. make_jpeg() is a placeholder (see test_placeholder_jpeg_is_flat);
    # the samples hold a real image.
    "jpeg/make_jpeg": (jpeg.make_jpeg, 1),
    "jpeg/make_jpeg_160x120": (lambda: jpeg.make_jpeg(width=160, height=120), 1),
    "jpeg/make_mpf_jpeg": (lambda: jpeg.make_mpf_jpeg([(0x010001, jpeg.make_jpeg()),
                                                       (0x020002, jpeg.make_jpeg())]), 3),
    "jpeg/jpeg_file": (jpeg_encoder.jpeg_file, 1),
    "jpeg/jpeg_file_gray": (lambda: jpeg_encoder.jpeg_file(color=False), 1),
    "jpeg/sample_photo": (jpeg.sample_photo, 1),
    "jpeg/sample_photo_motorola": (lambda: jpeg.sample_photo(">"), 1),
    "jpeg/sample_photo_rotated": (jpeg.sample_photo_rotated, 1),
    "jpeg/kitchen_sink": (jpeg.kitchen_sink_jpeg, 2),  # + the MPF extra image
    "jpeg/kitchen_sink_motorola": (lambda: jpeg.kitchen_sink_jpeg(">"), 2),
    # PNG and APNG
    "png/make_png": (png.make_png, 1),
    "png/palette_png": (png.palette_png, 1),
    "png/apng": (png.apng, 3),
    "png/kitchen_sink": (png.kitchen_sink_png, 2),
    "png/kitchen_sink_motorola": (lambda: png.kitchen_sink_png(">"), 2),
    **{f"png/{name}": (lambda name=name: png.variants()[name], 3 if name == "apng" else 1)
       for name in png.variants()},
    # GIF
    "gif/make_gif": (gif.make_gif, 1),
    "gif/animated_gif": (gif.animated_gif, 3),
    "gif/kitchen_sink": (gif.kitchen_sink_gif, 3),
    **{f"gif/{name}": (lambda name=name: gif.variants()[name], 3 if name == "animated" else 1)
       for name in gif.variants()},
    # WebP
    "webp/lossless": (webp.lossless, 1),
    "webp/lossy": (webp.lossy, 1),
    "webp/animated": (webp.animated, 3),
    "webp/kitchen_sink": (webp.kitchen_sink_webp, 2),
    "webp/kitchen_sink_motorola": (lambda: webp.kitchen_sink_webp(">"), 2),
    **{f"webp/{name}": (lambda name=name: webp.variants()[name],
                        {"animated": 3, "animated_lossy": 2}.get(name, 1))
       for name in webp.variants()},
}


@pytest.mark.parametrize("name", sorted(GENERATED))
def test_generated_image_fully_decodes(name):
    build, frame_count = GENERATED[name]
    data = build()
    assert open_image(data).format in FORMATS[name.split("/")[0]]
    frames = decode_frames(data)
    assert len(frames) == frame_count
    for frame in frames:
        assert frame.size[0] > 0 and frame.size[1] > 0
        assert len(frame.pixels) == len(Image.new(frame.mode, frame.size).tobytes())


def test_placeholder_jpeg_is_flat():
    """make_jpeg() only has the marker structure: it decodes, but to a flat
    gray picture, so it is never used for pixel checks."""
    frames = decode_frames(jpeg.make_jpeg(width=16, height=8))
    assert set(frames[0].pixels) == {128}


# -- GIF LZW and interlacing ----------------------------------------------------------------

def deinterlaced_rows(height: int) -> list[int]:
    """Display row of each row in an interlaced GIF's stream."""
    return ([y for y in range(0, height, 8)] + [y for y in range(4, height, 8)]
            + [y for y in range(2, height, 4)] + [y for y in range(1, height, 2)])


@pytest.mark.parametrize("width,height,colors,seed,interlace", [
    (5, 3, 4, 1, False), (9, 2, 2, 1, False), (40, 30, 256, 1, False), (64, 64, 256, 3, False),
    (1, 1, 2, 0, False), (7, 9, 4, 1, True), (6, 6, 4, 2, True), (13, 17, 16, 4, True),
    (200, 150, 256, 7, False),  # long enough to fill the code table (4096 codes) repeatedly
])
def test_gif_lzw_decodes_to_the_exact_indices(width, height, colors, seed, interlace):
    data = gif.make_gif(gif.image(width, height, colors=colors, seed=seed, interlace=interlace),
                        width=width, height=height, colors=colors)
    frames = decode_frames(data)
    stream = gif.pixels(width, height, colors, seed)
    if interlace:
        rows = [b""] * height
        for k, y in enumerate(deinterlaced_rows(height)):
            rows[y] = stream[k * width:(k + 1) * width]
        expected = b"".join(rows)
    else:
        expected = stream
    assert frames == [Frame("P", (width, height), expected, None)]


# -- PNG Adam7 ---------------------------------------------------------------------------------

@pytest.mark.parametrize("width,height", [(9, 7), (1, 1), (8, 8), (3, 17), (16, 5)])
def test_png_adam7_decodes_to_the_exact_pixels(width, height):
    data = png.make_png(width=width, height=height, interlace=1)
    frame = decode_frames(data)[0]
    expected = bytearray(3 * width * height)
    for i, (x0, y0, dx, dy) in enumerate(png.ADAM7):
        w = max(0, -(-(width - x0) // dx))
        h = max(0, -(-(height - y0) // dy))
        if not (w and h):
            continue
        rows = png.raw_image(w, h, seed=1 + i)
        for py in range(h):
            row = rows[py * (1 + 3 * w) + 1:(py + 1) * (1 + 3 * w)]
            for px in range(w):
                x, y = x0 + px * dx, y0 + py * dy
                expected[3 * (y * width + x):3 * (y * width + x) + 3] = row[3 * px:3 * px + 3]
    assert (frame.mode, frame.size) == ("RGB", (width, height))
    assert frame.pixels == bytes(expected)


# -- VP8L --------------------------------------------------------------------------------------

@pytest.mark.parametrize("width,height,argb,green2,alpha_used", [
    (1, 1, (255, 0, 0, 0), None, False), (9, 5, (255, 200, 100, 50), 7, False),
    (16, 2, (255, 1, 2, 3), 99, False), (3, 3, (128, 1, 2, 3), None, True),
    (33, 21, (40, 250, 0, 125), 0, True),
])
def test_vp8l_decodes_to_the_exact_pixels(width, height, argb, green2, alpha_used):
    a, r, g, b = argb
    frame = decode_frames(webp.lossless(width, height, argb=argb, green2=green2,
                                        alpha_used=alpha_used))[0]
    low, high = (g, g) if green2 is None else sorted((g, green2))
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels += bytes([r, high if (x + y) % 2 and green2 is not None else low, b])
            if alpha_used:
                pixels.append(a)
    assert (frame.mode, frame.size) == ("RGBA" if alpha_used else "RGB", (width, height))
    assert frame.pixels == bytes(pixels)
