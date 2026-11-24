"""verify on --deep output: each cleaned file inside is verified against the
original file inside; anything else that differs is a problem."""

from __future__ import annotations

import subprocess
import sys

from helpers import gz
from helpers.jpeg import sample_photo
from helpers.tar import build_tar, file
from helpers.zip import make_zip
from metaex.core import compress
from metaex.core import tar as tarcore
from metaex.core.verify import verify
from metaex.core.ziparchive import open_archive
from metaex.core.zipwriter import new, write_zip
from metaex.formats import gzip as gzipformat
from metaex.formats import jpeg
from metaex.formats import tar as tarformat
from metaex.formats import zip as zipformat

PHOTO = sample_photo()


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


def rebuilt(cleaned: bytes, replace: dict[str, bytes]) -> bytes:
    """``cleaned`` with some entries' contents replaced (neutral headers kept)."""
    archive = open_archive(cleaned)
    out = []
    for e in archive.entries:
        out.append(new(e.name, replace.get(e.name, archive.read(e)), e.method,
                       e.external_attr >> 16))
    return write_zip(out)


def test_deep_output_verifies_with_notes():
    data = make_zip({"a.jpg": PHOTO, "b.jpg": PHOTO, "c.txt": "x"})
    result = verify(zipformat.strip(data, deep=True), data)
    assert result.clean and result.image_data_match
    assert result.image_label == "Archive contents (2 file(s) inside cleaned and verified)"
    assert [n.split(":")[0] for n in result.notes if "cleaned copy" in n] == ["a.jpg", "b.jpg"]


def test_a_different_image_inside_is_a_problem():
    data = make_zip({"a.jpg": PHOTO})
    cleaned = zipformat.strip(data, deep=True)
    other = jpeg.strip(PHOTO)[:-2] + b"\x00\xff\xd9"  # different scan data
    result = verify(rebuilt(cleaned, {"a.jpg": other}), data)
    assert not result.clean and result.image_data_match is False
    assert any(p.startswith("a.jpg differs from the original") for p in result.problems)


def test_a_file_that_is_not_a_cleaned_copy_is_a_problem():
    data = make_zip({"notes.txt": "hello"})
    tampered = rebuilt(zipformat.strip(data), {"notes.txt": b"HELLO"})
    result = verify(tampered, data)
    assert not result.clean
    assert "notes.txt differs from the original" in result.problems[0]


def test_unchanged_file_with_metadata_is_found_by_verify():
    data = make_zip({"a.jpg": PHOTO})
    plain = zipformat.strip(data)  # without --deep: the photo is unchanged
    result = verify(plain, data)
    assert result.image_data_match is True  # same contents as the original ...
    assert not result.clean  # ... but the photo inside still has its metadata
    assert any(f.section.startswith("a.jpg: ") for f in result.leftovers)


def test_tar_and_compressed_deep_output_verify():
    tar = build_tar([file("a.jpg", PHOTO)])
    result = verify(tarformat.strip(tar, deep=True), tar)
    assert result.clean and "1 file(s) inside cleaned" in result.image_label
    single = gz.gzip(PHOTO)
    result = verify(gzipformat.strip(single, deep=True), single)
    assert result.clean and result.image_label == "Compressed file contents (cleaned and verified)"
    other = gz.gzip(jpeg.strip(PHOTO)[:-2] + b"\x00\xff\xd9")
    assert verify(other, single).image_data_match is False


def test_nested_deep_output_verifies():
    data = make_zip({"inner.zip": make_zip({"a.jpg": PHOTO}),
                     "x.tar.gz": gz.gzip(build_tar([file("b.jpg", PHOTO)]))})
    cleaned = zipformat.strip(data, deep=True)
    result = verify(cleaned, data)
    assert result.clean and result.image_data_match, result.problems
    inner = open_archive(open_archive(cleaned).read("inner.zip"))
    assert verify(inner.read("a.jpg")).clean
    tgz = compress.open_compressed(open_archive(cleaned).read("x.tar.gz")).content
    b = tarcore.open_tar(tgz)
    assert verify(b.read(b.entries[0])).clean


def test_cli_verify_deep_output(tmp_path):
    path = tmp_path / "photos.zip"
    path.write_bytes(make_zip({"trip/a.jpg": PHOTO}))
    r = metaex("strip", "--deep", path)
    assert r.returncode == 0, r.stderr
    cleaned = tmp_path / "photos_clean.zip"
    r = metaex("verify", cleaned, "--original", path)
    assert r.returncode == 0, r.stdout
    assert "Archive contents (1 file(s) inside cleaned and verified): matches the original" \
        in r.stdout
    assert "trip/a.jpg: a cleaned copy, verified against the original file inside" in r.stdout


def test_compressed_tar_deep_output_says_what_was_cleaned(tmp_path):
    """Regression: the gzip wrapper passed on the TAR's verdict but not its
    label, so strip said "Archive contents unchanged" after --deep."""
    original = gz.gzip(build_tar([file("trip/a.jpg", PHOTO)]))
    result = verify(gzipformat.strip(original, deep=True), original)
    assert result.clean and result.image_data_match
    assert result.image_label == "Archive contents (1 file(s) inside cleaned and verified)"
    assert result.notes[0].startswith("trip/a.jpg: a cleaned copy, verified")
    path = tmp_path / "trip.tar.gz"
    path.write_bytes(original)
    r = metaex("strip", "--deep", path)
    assert r.returncode == 0, r.stderr
    assert "unchanged" not in r.stdout.split("Verification:")[1].splitlines()[1]
    assert "(1 file(s) inside cleaned and verified): the same files" in r.stdout


def test_strip_names_the_gzip_header_it_removed(tmp_path):
    path = tmp_path / "notes.txt.gz"
    path.write_bytes(gz.gzip(b"plain text", name=b"notes.txt", mtime=1714559400,
                             comment=b"draft"))
    r = metaex("strip", path)
    assert r.returncode == 0, r.stderr
    assert "gzip header" in r.stdout and "  Other " not in r.stdout
