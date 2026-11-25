"""GIF: data hidden inside the LZW image data.

Regression test: an extra data sub-block after the LZW end-of-information
code (or codes for more pixels than the image has) is ignored by viewers
and was copied into the cleaned file, which verify called CLEAN. The LZW
codes are now walked (without building the pixels): show and risk report
such data, strip refuses the file and verify fails on its own."""

import pytest

from helpers.gif import image, lzw_encode, make_gif, pixels
from metaex.core.errors import CannotCleanError, HiddenDataError, MalformedFileError
from metaex.core.verify import verify
from metaex.formats import gif

SECRET = b"Jane Doe jane.doe@example.com"


def with_lzw(lzw: bytes, width: int = 4, height: int = 4) -> bytes:
    return make_gif(image(width, height, lzw=lzw), width=width, height=height)


def test_data_after_the_end_code_is_hidden():
    lzw = lzw_encode(pixels(4, 4), 2) + SECRET
    data = with_lzw(lzw)
    assert "hidden_image_data" in {s.key for s in gif.parse(data)["sections"]}
    with pytest.raises(HiddenDataError, match="after the end of the image data"):
        gif.strip(data)
    result = verify(data)
    assert not result.clean
    assert any(f.section == "Hidden data in image data" for f in result.leftovers)


def test_extra_pixels_are_hidden():
    data = with_lzw(lzw_encode(pixels(4, 4) + bytes(range(3)) * 20, 2))
    with pytest.raises(HiddenDataError, match="pixels more than the image needs"):
        gif.strip(data)
    assert not verify(data).clean


def test_zero_bytes_after_the_end_code_carry_nothing():
    data = with_lzw(lzw_encode(pixels(4, 4), 2) + bytes(3))
    assert verify(gif.strip(data), data).clean


@pytest.mark.parametrize("codes, message", [
    ((4, 1, 7), "undefined LZW code 7"),           # next free code is 6
    ((4, 7), "code 7 after a clear code is not a color"),
])
def test_invalid_codes_are_malformed(codes, message):
    # 2-bit colors: 3-bit codes, clear code 4, end code 5, packed LSB first
    value = sum(code << (3 * i) for i, code in enumerate(codes))
    data = with_lzw(value.to_bytes(2, "little"))
    with pytest.raises(MalformedFileError, match=message):
        gif.strip(data)
    assert not verify(data).clean


def test_too_many_codes_to_check_is_refused(monkeypatch):
    monkeypatch.setattr(gif, "MAX_LZW_CODES", 3)
    data = make_gif()
    with pytest.raises(CannotCleanError, match="not checked for hidden data"):
        gif.strip(data)
    assert not verify(data).clean


def test_normal_images_are_kept():
    data = make_gif(image(8, 6, colors=16, seed=3), image(3, 3, 2, 2, seed=5),
                    width=8, height=6, colors=16)
    assert all(not c.hidden and c.lzw.ended and c.lzw.pixels == c.expected
               for c in gif.check_images(gif.walk_blocks(data)))
    assert verify(gif.strip(data), data).clean
