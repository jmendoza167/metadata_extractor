"""PNG image data too large to check for hidden data.

Regression test: when the IHDR said the image needs more than
MAX_CHECKED_IMAGE_SIZE bytes uncompressed, the hidden-data check was
skipped with only a note, so strip copied bytes hidden after the image
data and both strip and verify said CLEAN. Now strip refuses such files
and verify reports a problem. Also: the image data is decompressed once per
command, not once per read of the file."""

import struct

import pytest

from helpers.png import chunk, make_png
from metaex.commands.strip import strip_file
from metaex.commands.verify import verify_file
from metaex.core.errors import CannotCleanError
from metaex.core.verify import verify
from metaex.formats import png

SIGNATURE = b"\x89PNG\r\n\x1a\n"
SECRET = b"Jane Doe jane.doe@example.com"


def huge_png() -> bytes:
    """An IHDR of 16385 x 16385 RGBA (just over 1 GiB uncompressed); the
    IDAT is tiny, with data after its zlib stream. Too large data is never
    decompressed, so it doesn't have to be real."""
    header = struct.pack(">IIBBBBB", 16385, 16385, 8, 6, 0, 0, 0)
    stream = bytes.fromhex("789c030000000001")  # an empty zlib stream
    return (SIGNATURE + chunk("IHDR", header) + chunk("IDAT", stream + SECRET)
            + chunk("IEND"))


def test_image_data_too_large_to_check_is_refused():
    data = huge_png()
    assert png.check_image_data(png.walk_chunks(data)).skipped
    with pytest.raises(CannotCleanError, match="can't be ruled out"):
        png.strip(data)


def test_verify_fails_on_its_own():
    result = verify(huge_png())
    assert not result.clean
    assert any("was not checked for hidden data" in p for p in result.problems)


def test_image_data_is_checked_once_per_command(tmp_path, monkeypatch):
    calls = []
    real = png._check_image_data
    monkeypatch.setattr(png, "_check_image_data",
                        lambda structure: calls.append(1) or real(structure))
    png._CHECK_CACHE.clear()
    src = tmp_path / "photo.png"
    src.write_bytes(make_png(chunk("tEXt", b"Author\x00Jane Doe"), width=8, height=8))
    outcome = strip_file(src, tmp_path / "out.png")
    assert outcome.verify.clean
    assert verify_file(tmp_path / "out.png", src).clean
    assert len(calls) == 1
