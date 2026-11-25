"""Content metaex could not examine is never called clean.

Regression tests: files inside an archive past the limits (more than
MAX_INNER_FILES files, nested deeper than MAX_NESTING) were neither shown
by show/risk nor counted by verify, so `strip --deep` copied photos with
their GPS position unchanged and verify said CLEAN. The same went for
embedded images and objects in Office documents that metaex can't read.
Now verify lists them as "not checked" and its verdict is NOT CLEAN."""

import struct
import subprocess
import sys

import pytest

from helpers.jpeg import sample_photo
from helpers.ooxml import add_media, docx
from helpers.zip import make_zip
from metaex.commands.strip import strip_file
from metaex.commands.verify import verify_file
from metaex.core import archive
from metaex.core.verify import verdict, verify
from metaex.formats import docx as docx_format
from metaex.formats import zip as zip_format

GPS_PHOTO = sample_photo()


def tiff_with_gps() -> bytes:
    """The EXIF block of a GPS photo is a TIFF file with the same tags."""
    i = GPS_PHOTO.index(b"Exif\x00\x00")
    length = struct.unpack(">H", GPS_PHOTO[i - 2:i])[0]
    return GPS_PHOTO[i + 6:i - 2 + length]


@pytest.fixture
def few_inner_files(monkeypatch):
    monkeypatch.setattr(archive, "MAX_INNER_FILES", 3)


def test_files_past_the_file_limit_are_not_checked(tmp_path, few_inner_files):
    photos = make_zip({f"IMG_{i:04}.jpg": GPS_PHOTO for i in range(5)})
    result = verify(photos)
    assert not result.clean
    assert any("IMG_0004.jpg" in u and "more than 3 files" in u for u in result.unchecked)
    # risk/show mention them
    assert any("not examined and may contain metadata" in w
               for w in zip_format.parse(photos)["warnings"])
    # strip --deep cleans the first 3, copies the others, and says so
    src = tmp_path / "photos.zip"
    src.write_bytes(photos)
    outcome = strip_file(src, tmp_path / "out.zip", deep=True)
    assert not outcome.verify.clean
    assert "could not check" in verdict(outcome.verify)
    again = verify_file(tmp_path / "out.zip")
    assert not again.clean and len(again.unchecked) == 2


def nested_zip(levels: int = 5) -> bytes:
    """A GPS photo in a ZIP in a ZIP ...; the outer archive has the neutral
    headers of a cleaned one."""
    data, name = GPS_PHOTO, "photo.jpg"
    for level in range(levels):
        data, name = make_zip({name: data}), f"level{level}.zip"
    return zip_format.strip(data)


def test_files_nested_too_deep_are_not_checked():
    result = verify(nested_zip())
    assert not result.clean and not result.leftovers
    (item,) = result.unchecked
    assert item.startswith("level3.zip > level2.zip > level1.zip > level0.zip (ZIP)")
    assert "3 levels deep" in item
    assert verdict(result) == "NOT CLEAN: 1 item(s) metaex could not check"


def test_files_of_unrecognized_type_are_plain_content():
    assert verify(zip_format.strip(make_zip({"notes.txt": "kitchen plans"}))).clean


def test_office_images_metaex_cant_read_are_not_checked():
    pkg = docx()
    add_media(pkg, "word/document.xml", "word/media/image1.tiff", tiff_with_gps(), "tiff",
              "image/tiff")
    cleaned = docx_format.strip(pkg.build())
    result = verify(cleaned)
    assert not result.clean
    assert any("word/media/image1.tiff" in u for u in result.unchecked)


def test_cli_verify_lists_what_was_not_checked(tmp_path):
    path = tmp_path / "nested.zip"
    path.write_bytes(nested_zip())
    r = subprocess.run([sys.executable, "-m", "metaex", "verify", str(path)],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 3
    assert "Result: NOT CLEAN: 1 item(s) metaex could not check" in r.stdout
    assert "Not checked (1; they may contain metadata):" in r.stdout
