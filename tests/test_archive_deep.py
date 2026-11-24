"""strip --deep: the files inside archives are cleaned too."""

from __future__ import annotations

import subprocess
import sys

from helpers import gz
from helpers.jpeg import sample_photo
from helpers.ooxml import kitchen_sink_docx
from helpers.tar import build_tar, file
from helpers.zip import Entry, build_zip, make_zip
from metaex.core import archive, compress
from metaex.core import tar as tarcore
from metaex.core.verify import verify
from metaex.core.ziparchive import open_archive
from metaex.formats import gzip as gzipformat
from metaex.formats import jpeg
from metaex.formats import tar as tarformat
from metaex.formats import zip as zipformat

PHOTO = sample_photo()


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


def clean(data: bytes) -> bool:
    return verify(data).clean


def test_zip_deep_cleans_supported_files_and_copies_the_rest():
    data = make_zip({"a.jpg": PHOTO, "notes.txt": "plain text", "report.docx":
                     kitchen_sink_docx()})
    with archive.deep_cleaning() as session:
        cleaned = zipformat.strip(data, deep=True)
    result = open_archive(cleaned)
    assert result.names() == ["a.jpg", "notes.txt", "report.docx"]
    assert clean(result.read("a.jpg")) and clean(result.read("report.docx"))
    assert result.read("notes.txt") == b"plain text"
    assert jpeg.image_data(result.read("a.jpg")) == jpeg.image_data(PHOTO)
    assert sorted(session.cleaned) == [("a.jpg", "JPEG"), ("report.docx", "DOCX")]
    assert session.copied == [("notes.txt", "type not recognized")]
    assert verify(cleaned).clean
    check = verify(cleaned, data)
    assert check.clean and check.image_data_match, check.problems


def test_deep_keeps_the_compression_method():
    data = build_zip([Entry("a.jpg", PHOTO, method=8)])
    entry = open_archive(zipformat.strip(data, deep=True)).entries[0]
    assert entry.method == 8


def test_without_deep_nothing_inside_changes():
    data = make_zip({"a.jpg": PHOTO})
    assert open_archive(zipformat.strip(data)).read("a.jpg") == PHOTO


def test_tar_deep():
    data = build_tar([file("a.jpg", PHOTO), file("b.bin", b"\x00\x01")])
    cleaned = tarformat.strip(data, deep=True)
    result = tarcore.open_tar(cleaned)
    assert clean(result.read(result.entries[0]))
    assert result.read(result.entries[1]) == b"\x00\x01"
    assert verify(cleaned, data).image_data_match


def test_compressed_single_file_and_tar_deep():
    cleaned = gzipformat.strip(gz.gzip(PHOTO, name=b"photo.jpg"), deep=True)
    assert clean(compress.open_compressed(cleaned).content)
    tgz = gz.gzip(build_tar([file("a.jpg", PHOTO)]))
    inner = tarcore.open_tar(compress.open_compressed(gzipformat.strip(tgz, deep=True)).content)
    assert clean(inner.read(inner.entries[0]))


def test_nested_archives_are_cleaned():
    data = make_zip({"inner.zip": make_zip({"a.jpg": PHOTO}),
                     "inner.tar.gz": gz.gzip(build_tar([file("b.jpg", PHOTO)]))})
    with archive.deep_cleaning() as session:
        cleaned = zipformat.strip(data, deep=True)
    assert sorted(p for p, _ in session.cleaned) == [
        "inner.tar.gz", "inner.tar.gz > b.jpg", "inner.zip", "inner.zip > a.jpg"]
    assert verify(cleaned).clean
    assert verify(cleaned, data).image_data_match


def test_nesting_limit():
    level4 = make_zip({"deep.jpg": PHOTO})
    data = make_zip({"l1.zip": make_zip({"l2.zip": make_zip({"l3.zip": make_zip(
        {"l4.zip": level4})})})})
    with archive.deep_cleaning() as session:
        zipformat.strip(data, deep=True)
    assert session.copied == [("l1.zip > l2.zip > l3.zip > l4.zip",
                               "archives are cleaned only 3 levels deep")]


def test_refused_files_inside_are_copied_and_listed():
    from helpers.zip import FLAG_ENCRYPTED

    broken = PHOTO[:300]
    data = make_zip({"broken.jpg": broken, "locked.zip": build_zip(
        [Entry("x", b"x", flags=FLAG_ENCRYPTED)])})
    with archive.deep_cleaning() as session:
        cleaned = zipformat.strip(data, deep=True)
    reasons = dict(session.copied)
    assert set(reasons) == {"broken.jpg", "locked.zip"} and "encrypted" in reasons["locked.zip"]
    assert open_archive(cleaned).read("broken.jpg") == broken


def test_paranoid_is_passed_to_the_files_inside():
    from helpers.jpeg import kitchen_sink_jpeg

    data = make_zip({"k.jpg": kitchen_sink_jpeg()})
    normal = open_archive(zipformat.strip(data, deep=True)).read("k.jpg")
    paranoid = open_archive(zipformat.strip(data, deep=True, paranoid=True)).read("k.jpg")
    assert len(paranoid) < len(normal)  # the ICC profile is gone


def test_cli_deep(tmp_path):
    path = tmp_path / "photos.zip"
    path.write_bytes(make_zip({"a.jpg": PHOTO, "notes.txt": "text"}))
    r = metaex("strip", "--deep", path)
    assert r.returncode == 0, r.stderr
    assert "--deep cleaned 1 file(s) inside: a.jpg (JPEG)" in r.stdout
    assert "--deep copied 1 file(s) inside unchanged" in r.stdout
    assert "notes.txt (type not recognized)" in r.stdout
    assert "Verification: clean" in r.stdout
    r = metaex("verify", tmp_path / "photos_clean.zip")
    assert r.returncode == 0, r.stdout


def test_cli_deep_is_only_for_archives(tmp_path):
    path = tmp_path / "a.jpg"
    path.write_bytes(PHOTO)
    r = metaex("strip", "--deep", path)
    assert r.returncode == 1 and "--deep is only for archives" in r.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.jpg"]
