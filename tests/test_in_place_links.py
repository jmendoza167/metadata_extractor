"""--in-place on a link.

Regression test: --in-place writes a verified temp file and renames it over
the path given. For a file with several hard links that replaced only that
name, so the other names kept the original with its GPS position, and for a
symbolic link it replaced the link while the file it pointed to stayed as it
was; both said "Wrote ...". Such paths are now refused and nothing changes."""

import os
import subprocess
import sys

import pytest

from helpers.jpeg import sample_photo
from metaex.commands.strip import strip_file
from metaex.core.errors import MetaexError

PHOTO = sample_photo()


def test_hard_link_is_refused(tmp_path):
    real = tmp_path / "real.jpg"
    real.write_bytes(PHOTO)
    os.link(real, tmp_path / "other.jpg")
    with pytest.raises(MetaexError, match="2 hard links"):
        strip_file(tmp_path / "other.jpg", in_place=True)
    assert real.read_bytes() == PHOTO
    assert (tmp_path / "other.jpg").read_bytes() == PHOTO


def test_symbolic_link_is_refused(tmp_path):
    real = tmp_path / "real.jpg"
    real.write_bytes(PHOTO)
    link = tmp_path / "link.jpg"
    try:
        os.symlink(real, link)
    except OSError:
        pytest.skip("creating symbolic links is not allowed here")
    with pytest.raises(MetaexError, match="symbolic link"):
        strip_file(link, in_place=True)
    assert os.path.islink(link) and real.read_bytes() == PHOTO


def test_a_file_with_one_name_is_cleaned_in_place(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(PHOTO)
    outcome = strip_file(path, in_place=True)
    assert outcome.verify.clean and path.read_bytes() != PHOTO


def test_cli_refuses_and_changes_nothing(tmp_path):
    real = tmp_path / "real.jpg"
    real.write_bytes(PHOTO)
    os.link(real, tmp_path / "other.jpg")
    r = subprocess.run([sys.executable, "-m", "metaex", "strip", "--in-place", "--yes",
                        str(tmp_path / "other.jpg")], capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
    assert r.returncode == 1
    assert "hard links" in r.stderr and "Nothing changed" in r.stderr
    assert real.read_bytes() == PHOTO
    assert sorted(p.name for p in tmp_path.iterdir()) == ["other.jpg", "real.jpg"]
