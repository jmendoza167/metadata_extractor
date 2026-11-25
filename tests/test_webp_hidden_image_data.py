"""WebP: data after the end of a lossless bitstream.

Regression test: bytes after the end of a VP8L bitstream (inside its
chunk), which decoders never read, were copied into the cleaned file and
verify called it CLEAN. The lossless bitstreams (VP8L chunks and ALPH
chunks) are now read to their end: show and risk report such bytes, strip
refuses the file and verify fails on its own. Lossy VP8 bitstreams can't
be checked this way (see FORMATS.md)."""

import pytest

from helpers.webp import anmf, chunk, extended, lossy, riff, vp8l
from metaex.core import vp8l as vp8l_reader
from metaex.core.errors import CannotCleanError, HiddenDataError, MalformedFileError
from metaex.core.verify import verify
from metaex.formats import webp

SECRET = b"Jane Doe jane.doe@example.com"
VP8_1X1 = chunk("VP8 ", lossy()[20:])


def test_bytes_after_a_lossless_bitstream_are_hidden():
    data = riff(chunk("VP8L", vp8l(4, 4) + SECRET))
    assert "hidden_image_data" in {s.key for s in webp.parse(data)["sections"]}
    with pytest.raises(HiddenDataError, match="after the end of its bitstream"):
        webp.strip(data)
    result = verify(data)
    assert not result.clean
    assert any(f.section == "Hidden data in image data" for f in result.leftovers)


def test_bitstream_end_is_found():
    for width, height, green2 in ((1, 1, None), (4, 4, None), (13, 7, 3), (64, 3, 99)):
        stream = vp8l(width, height, green2=green2)
        assert vp8l_reader.bitstream_end(stream, 10**6).end == len(stream)


def test_hidden_bytes_in_an_animation_frame():
    frame = anmf(chunk("VP8L", vp8l(4, 4, green2=7) + SECRET), width=4, height=4)
    data = extended(chunk("ANIM", bytes(6)), frame, width=4, height=4)
    with pytest.raises(HiddenDataError, match="frame 1 VP8L"):
        webp.strip(data)
    assert not verify(data).clean


def test_raw_alpha_longer_than_the_image():
    alph = chunk("ALPH", b"\x00" + bytes(1) + SECRET)  # 1x1 image: one alpha byte
    data = extended(alph, VP8_1X1, width=1, height=1)
    with pytest.raises(HiddenDataError, match="ALPH"):
        webp.strip(data)
    assert not verify(data).clean


def test_zero_padding_carries_nothing():
    data = riff(chunk("VP8L", vp8l(4, 4) + bytes(2)))
    assert verify(webp.strip(data), data).clean


def test_truncated_bitstream_is_malformed():
    with pytest.raises(MalformedFileError, match="truncated"):
        webp.strip(riff(chunk("VP8L", vp8l(13, 7, green2=3)[:-3])))


def test_too_many_symbols_to_check_is_refused(monkeypatch):
    monkeypatch.setattr(webp, "MAX_VP8L_SYMBOLS", 3)
    data = riff(chunk("VP8L", vp8l(4, 4, green2=7)))  # one bit per pixel
    with pytest.raises(CannotCleanError, match="not checked for hidden data"):
        webp.strip(data)
    assert not verify(data).clean


@pytest.mark.parametrize("width, height", [(16384, 16384), (16136, 1024)])
def test_a_huge_flat_image_reads_no_bits_per_pixel(width, height):
    """Regression test (found by fuzzing): when every prefix code has one
    symbol, pixels read no bits, and a 34-byte file could make metaex count
    millions of them one by one. Such runs are now skipped in one step."""
    stream = vp8l(width, height)
    result = vp8l_reader.bitstream_end(stream, 1000)  # a tiny budget is enough
    assert result.end == len(stream)
    assert verify(webp.strip(riff(chunk("VP8L", stream)))).clean
