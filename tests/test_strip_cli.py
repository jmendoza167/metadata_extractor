"""Basic tests for `metaex strip` and the verified writer."""

import json
import os
import re
import subprocess
import sys

import pytest

from helpers.jpeg import icc_profile, icc_segments, kitchen_sink_jpeg, make_jpeg
from metaex.core.errors import FileAccessError, MetaexError
from metaex.core.verify import verify
from metaex.core.writer import write_verified
from metaex.formats import jpeg

PNG = b"\x89PNG\r\n\x1a\n" + bytes(32)


def run(*args, stdin=None):
    return subprocess.run([sys.executable, "-m", "metaex", *args], capture_output=True,
                          text=True, timeout=60, input=stdin)


@pytest.fixture
def photo(tmp_path):
    p = tmp_path / "photo.jpg"
    p.write_bytes(kitchen_sink_jpeg())
    return p


def listing(directory):
    return sorted(os.listdir(directory))


def test_strip_writes_clean_copy(photo):
    original = photo.read_bytes()
    result = run("strip", str(photo))
    assert result.returncode == 0, result.stderr
    out = photo.with_name("photo_clean.jpg")
    assert out.exists()
    assert photo.read_bytes() == original  # original untouched
    check = verify(out.read_bytes(), original)
    assert check.clean and check.image_data_match
    text = result.stdout
    for expected in ("Removed:", "GPS location", "Thumbnail", "XMP", "Kept by design:",
                     "ICC profile", "Verification: clean", "Image data unchanged"):
        assert expected in text
    assert listing(photo.parent) == ["photo.jpg", "photo_clean.jpg"]


def test_strip_json(photo):
    result = run("strip", str(photo), "--json")
    data = json.loads(result.stdout)
    assert data["output"].endswith("photo_clean.jpg")
    assert data["verify"]["clean"] is True
    assert data["verify"]["image_data_match"] is True
    assert "GPS location" in data["removed"]
    assert {k["section"] for k in data["kept_by_design"]} == {"ICC profile", "JFIF", "Adobe"}


def test_existing_output_is_refused(photo):
    out = photo.with_name("photo_clean.jpg")
    out.write_bytes(b"keep")
    result = run("strip", str(photo))
    assert result.returncode == 1
    assert "already exists" in result.stderr and "-o" in result.stderr
    assert out.read_bytes() == b"keep"


def test_output_option(photo, tmp_path):
    target = tmp_path / "elsewhere.jpg"
    assert run("strip", str(photo), "-o", str(target)).returncode == 0
    assert verify(target.read_bytes()).clean


def test_unsupported_type_writes_nothing(tmp_path):
    p = tmp_path / "image.png"
    p.write_bytes(PNG)
    result = run("strip", str(p))
    assert result.returncode == 1
    assert "metaex can't strip PNG files yet; no file written" in result.stderr
    assert listing(tmp_path) == ["image.png"]


def test_malformed_jpeg_writes_nothing(tmp_path):
    p = tmp_path / "broken.jpg"
    p.write_bytes(kitchen_sink_jpeg()[:3000])
    result = run("strip", str(p))
    assert result.returncode == 1
    assert "no file written" in result.stderr
    assert listing(tmp_path) == ["broken.jpg"]


def test_paranoid_and_no_orientation(tmp_path):
    from helpers.jpeg import app
    from helpers.tiff import SHORT, build_tiff, exif_payload

    p = tmp_path / "p.jpg"
    p.write_bytes(make_jpeg(app(1, exif_payload(build_tiff([(0x0112, SHORT, 6)]))),
                            *icc_segments(icc_profile())))
    assert run("strip", str(p), "-o", str(tmp_path / "a.jpg")).returncode == 0
    assert run("strip", str(p), "-o", str(tmp_path / "b.jpg"), "--paranoid",
               "--no-orientation").returncode == 0
    a = {s.key for s in jpeg.parse((tmp_path / "a.jpg").read_bytes())["sections"]}
    b = {s.key for s in jpeg.parse((tmp_path / "b.jpg").read_bytes())["sections"]}
    assert a == {"exif", "icc"}
    assert b == set()


# --- --in-place --------------------------------------------------------------------

class FakeTerminal:
    def __init__(self, answer):
        self.answer = answer

    def isatty(self):
        return True

    def readline(self):
        return self.answer


def test_in_place_with_yes(photo):
    original = photo.read_bytes()
    result = run("strip", str(photo), "--in-place", "--yes")
    assert result.returncode == 0, result.stderr
    cleaned = photo.read_bytes()
    assert cleaned != original
    check = verify(cleaned, original)
    assert check.clean and check.image_data_match
    assert listing(photo.parent) == ["photo.jpg"]


def test_in_place_without_terminal_is_refused(photo):
    original = photo.read_bytes()
    result = run("strip", str(photo), "--in-place", stdin="yes\n")
    assert result.returncode == 1
    assert "pass --yes" in result.stderr
    assert photo.read_bytes() == original


@pytest.mark.parametrize("answer,changed", [("yes\n", True), ("y\n", False), ("", False),
                                            ("no\n", False)])
def test_in_place_confirmation_prompt(photo, monkeypatch, capsys, answer, changed):
    from metaex import cli

    original = photo.read_bytes()
    monkeypatch.setattr(sys, "stdin", FakeTerminal(answer))
    code = cli.main(["strip", str(photo), "--in-place"])
    err = capsys.readouterr().err
    assert f"Type 'yes' to overwrite {photo}:" in err
    assert code == (0 if changed else 1)
    assert (photo.read_bytes() != original) == changed
    if not changed:
        assert "not confirmed" in err


def test_in_place_and_output_conflict(photo, tmp_path):
    result = run("strip", str(photo), "--in-place", "-o", str(tmp_path / "x.jpg"))
    assert result.returncode == 2


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_in_place_keeps_permissions(photo):
    os.chmod(photo, 0o640)
    assert run("strip", str(photo), "--in-place", "--yes").returncode == 0
    assert (os.stat(photo).st_mode & 0o777) == 0o640


# --- timestamps and Zone.Identifier ---------------------------------------------------

def test_output_does_not_copy_original_timestamps(photo):
    os.utime(photo, (1_000_000_000, 1_000_000_000))
    data = json.loads(run("strip", str(photo), "--json").stdout)
    out = photo.with_name("photo_clean.jpg")
    assert os.stat(out).st_mtime > 1_000_000_000 + 10 * 365 * 86400
    assert data["timestamps"] == "new (not copied from the original)"


def test_fixed_time(photo):
    result = run("strip", str(photo), "--fixed-time", "--json")
    out = photo.with_name("photo_clean.jpg")
    st = os.stat(out)
    assert st.st_mtime == 946684800
    assert st.st_atime == 946684800
    assert json.loads(result.stdout)["timestamps"].startswith(
        "modified and accessed set to 2000-01-01T00:00:00Z")


def test_fixed_time_in_place(photo):
    assert run("strip", str(photo), "--in-place", "--yes", "--fixed-time").returncode == 0
    assert os.stat(photo).st_mtime == 946684800


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS alternate data streams")
def test_output_has_no_zone_identifier_on_windows(photo):
    try:
        with open(f"{photo}:Zone.Identifier", "wb") as f:
            f.write(b"[ZoneTransfer]\r\nZoneId=3\r\nHostUrl=https://example.com/x.jpg\r\n")
    except OSError as exc:
        pytest.skip(f"no alternate data streams here: {exc}")
    result = run("strip", str(photo), "--json")
    data = json.loads(result.stdout)
    assert data["zone_identifier"].startswith("none")
    out = photo.with_name("photo_clean.jpg")
    with pytest.raises(OSError):
        open(f"{out}:Zone.Identifier", "rb").close()
    assert "Windows Zone.Identifier stream: none" in run(
        "strip", str(photo), "-o", str(photo.with_name("second.jpg"))).stdout


@pytest.mark.skipif(sys.platform == "win32", reason="Zone.Identifier is Windows-only")
def test_zone_identifier_not_reported_elsewhere(photo):
    data = json.loads(run("strip", str(photo), "--json").stdout)
    assert data["zone_identifier"] is None


# --- --rename ------------------------------------------------------------------------


def test_rename_uses_random_name_with_real_extension(tmp_path):
    p = tmp_path / "Holiday in Paris (Jane).JPEG"
    p.write_bytes(kitchen_sink_jpeg())
    data = json.loads(run("strip", str(p), "--rename", "--json").stdout)
    name = os.path.basename(data["output"])
    assert re.fullmatch(r"img_[0-9a-f]{8}\.jpg", name)
    assert os.path.dirname(data["output"]) == str(tmp_path)
    assert verify((tmp_path / name).read_bytes()).clean
    assert sorted(os.listdir(tmp_path)) == sorted([p.name, name])


def test_rename_names_differ(photo):
    first = json.loads(run("strip", str(photo), "--rename", "--json").stdout)["output"]
    second = json.loads(run("strip", str(photo), "--rename", "--json").stdout)["output"]
    assert first != second


def test_rename_skips_existing_names(photo, monkeypatch):
    from metaex.commands import strip as strip_cmd
    from metaex.core import filetype as ft

    tokens = iter(["aaaaaaaa", "bbbbbbbb"])
    monkeypatch.setattr(strip_cmd.secrets, "token_hex", lambda n: next(tokens))
    photo.with_name("img_aaaaaaaa.jpg").write_bytes(b"taken")
    path = strip_cmd.random_output_path(str(photo), ft.JPEG)
    assert os.path.basename(path) == "img_bbbbbbbb.jpg"


@pytest.mark.parametrize("extra", [["--in-place"], ["-o", "x.jpg"]])
def test_rename_conflicts_with_other_targets(photo, extra):
    assert run("strip", str(photo), "--rename", *extra).returncode == 2


# --- write_verified -----------------------------------------------------------------

def test_failed_check_leaves_nothing(tmp_path):
    def reject(data):
        raise MetaexError("not clean")

    with pytest.raises(MetaexError):
        write_verified(tmp_path / "out.jpg", b"data", reject)
    assert listing(tmp_path) == []


def test_check_sees_bytes_from_disk(tmp_path):
    seen = []
    write_verified(tmp_path / "out.bin", b"abc", seen.append)
    assert seen == [b"abc"]
    assert (tmp_path / "out.bin").read_bytes() == b"abc"


def test_never_clobbers_without_overwrite(tmp_path):
    target = tmp_path / "out.bin"
    target.write_bytes(b"old")
    with pytest.raises(FileAccessError, match="already exists"):
        write_verified(target, b"new", lambda d: None)
    assert target.read_bytes() == b"old"
    assert listing(tmp_path) == ["out.bin"]


def test_overwrite_replaces(tmp_path):
    target = tmp_path / "out.bin"
    target.write_bytes(b"old")
    write_verified(target, b"new", lambda d: None, overwrite=True)
    assert target.read_bytes() == b"new"
    assert listing(tmp_path) == ["out.bin"]


def test_missing_directory(tmp_path):
    with pytest.raises(FileAccessError, match="no such directory"):
        write_verified(tmp_path / "missing" / "out.bin", b"x", lambda d: None)


def test_trailing_data_and_mpf_images_are_summarized_by_size(photo):
    data = photo.read_bytes()
    sizes = jpeg.removed_sizes(data)
    structure = jpeg.walk_segments(data)
    assert sizes["trailing"] == len(data) - structure.end_of_image
    extra_image = make_jpeg(width=64, height=48)
    assert sizes["mpf"] == len(extra_image)

    result = run("strip", str(photo))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    trailing = next(line for line in lines if "Data after end of image" in line)
    assert trailing.rstrip().endswith(f"{sizes['trailing']} B")
    assert "field" not in trailing
    mpf = next(line for line in lines if "Extra images (MPF)" in line)
    assert mpf.rstrip().endswith(f"{len(extra_image)} B")
    assert "field" not in mpf

    data_json = json.loads(run("strip", str(photo), "-o",
                               str(photo.with_name("other.jpg")), "--json").stdout)
    assert data_json["removed_sizes"] == {"Data after end of image": sizes["trailing"],
                                          "Extra images (MPF)": len(extra_image)}


def test_size_summary_uses_binary_units(tmp_path):
    from metaex.commands.strip import StripOutcome, summary_lines
    from metaex.core import filetype as ft

    outcome = StripOutcome("a.jpg", "a_clean.jpg", ft.JPEG, 2_000_000, 1000,
                           removed={"Data after end of image": ["Size", "Content"]},
                           removed_sizes={"Data after end of image": 1_258_291})
    text = "\n".join(summary_lines(outcome))
    assert "Data after end of image  1.2 MiB" in text


def test_jpeg_without_trailing_data_has_no_sizes():
    assert jpeg.removed_sizes(make_jpeg()) == {}
