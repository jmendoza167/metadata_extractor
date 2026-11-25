"""Collecting the files a batch works on (core/batch.py): skip rules,
folders, duplicates, order, links and platform-specific cases."""

import os
import subprocess
import sys

import pytest

from metaex.core import batch
from metaex.core.batch import collect, is_batch, is_clean_output, is_inside, same_path
from metaex.core.loader import load_file


def touch(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def names(collection):
    return ["/".join(item.relative) for item in collection.items]


def skipped(collection):
    return {os.path.basename(s.path): s.reason for s in collection.skipped}


# -- arguments --------------------------------------------------------------------------


def test_file_argument(tmp_path):
    f = touch(tmp_path / "photo.jpg")
    c = collect([str(f)])
    assert len(c.items) == 1
    item = c.items[0]
    assert item.path == str(f) and item.relative == ("photo.jpg",)
    assert item.explicit and item.root is None
    assert not c.skipped and not c.errors


def test_folder_is_one_level_deep(tmp_path):
    touch(tmp_path / "a.jpg")
    touch(tmp_path / "sub" / "b.jpg")
    c = collect([str(tmp_path)])
    assert names(c) == ["a.jpg"]
    assert not c.items[0].explicit and c.items[0].root == str(tmp_path)
    assert skipped(c) == {"sub": batch.SUBFOLDER}


def test_recursive_includes_nested_folders(tmp_path):
    for rel in ("a.jpg", "sub/b.jpg", "sub/deeper/c.png", "sub/deeper/deepest/d.gif",
                "z/e.mp3"):
        touch(tmp_path / rel)
    c = collect([str(tmp_path)], recursive=True)
    assert names(c) == ["a.jpg", "sub/b.jpg", "sub/deeper/c.png", "sub/deeper/deepest/d.gif",
                        "z/e.mp3"]
    assert c.items[2].path == os.path.join(str(tmp_path), "sub", "deeper", "c.png")
    assert not c.skipped


def test_several_arguments_and_empty_folder(tmp_path):
    touch(tmp_path / "one" / "a.jpg")
    touch(tmp_path / "two" / "b.jpg")
    (tmp_path / "empty").mkdir()
    c = collect([str(tmp_path / "two"), str(tmp_path / "one"), str(tmp_path / "empty")])
    assert [os.path.basename(i.path) for i in c.items] == ["a.jpg", "b.jpg"]


def test_missing_argument_is_an_error_and_the_rest_goes_on(tmp_path):
    touch(tmp_path / "a.jpg")
    c = collect([str(tmp_path / "missing.jpg"), str(tmp_path)])
    assert names(c) == ["a.jpg"]
    assert [(os.path.basename(e.path), e.reason) for e in c.errors] == [
        ("missing.jpg", "no such file or folder")]


def test_is_batch(tmp_path):
    f = touch(tmp_path / "a.jpg")
    assert not is_batch([str(f)])
    assert not is_batch([str(tmp_path / "missing.jpg")])
    assert is_batch([str(tmp_path)])
    assert is_batch([str(f), str(f)])


# -- skip rules ---------------------------------------------------------------------------


def test_hidden_files_and_folders_are_skipped(tmp_path):
    touch(tmp_path / ".secret.jpg")
    touch(tmp_path / ".hidden" / "a.jpg")
    touch(tmp_path / "visible.jpg")
    c = collect([str(tmp_path)], recursive=True)
    assert names(c) == ["visible.jpg"]
    assert skipped(c) == {".secret.jpg": batch.HIDDEN, ".hidden": batch.HIDDEN}


def test_include_hidden(tmp_path):
    touch(tmp_path / ".secret.jpg")
    touch(tmp_path / ".hidden" / "a.jpg")
    c = collect([str(tmp_path)], recursive=True, include_hidden=True)
    assert names(c) == [".hidden/a.jpg", ".secret.jpg"]


@pytest.mark.parametrize("folder, reason", [
    (".git", batch.VCS), (".hg", batch.VCS), (".svn", batch.VCS),
    ("node_modules", batch.DEPENDENCIES), ("__pycache__", batch.DEPENDENCIES),
])
@pytest.mark.parametrize("include_hidden", [False, True])
def test_tool_folders_are_always_skipped(tmp_path, folder, reason, include_hidden):
    touch(tmp_path / folder / "a.jpg")
    touch(tmp_path / "keep.jpg")
    c = collect([str(tmp_path)], recursive=True, include_hidden=include_hidden)
    assert names(c) == ["keep.jpg"]
    assert skipped(c) == {folder: reason}


@pytest.mark.parametrize("marker", ["pyvenv.cfg", "conda-meta/history"])
@pytest.mark.parametrize("include_hidden", [False, True])
def test_virtual_environments_are_always_skipped(tmp_path, marker, include_hidden):
    touch(tmp_path / "env" / marker)
    touch(tmp_path / "env" / "Lib" / "a.png")
    touch(tmp_path / "keep.jpg")
    c = collect([str(tmp_path)], recursive=True, include_hidden=include_hidden)
    assert names(c) == ["keep.jpg"]
    assert skipped(c) == {"env": batch.VENV}


@pytest.mark.parametrize("name, expected", [
    ("photo_clean.jpg", True), ("backup_clean.tar.gz", True), ("photo_clean.jpg.gz", True),
    ("report_clean.docx", True), ("clean.jpg", False), ("photo_clean", False),
    ("my_clean_photo.jpg", False), ("a_clean.b.c.d", False), ("_clean.jpg", False),
    ("photo.jpg", False),
])
def test_is_clean_output(name, expected):
    assert is_clean_output(name) is expected


def test_clean_outputs_are_skipped_inside_folders(tmp_path):
    touch(tmp_path / "photo.jpg")
    touch(tmp_path / "photo_clean.jpg")
    touch(tmp_path / "backup_clean.tar.gz")
    c = collect([str(tmp_path)])
    assert names(c) == ["photo.jpg"]
    assert skipped(c) == {"photo_clean.jpg": batch.CLEAN_OUTPUT,
                          "backup_clean.tar.gz": batch.CLEAN_OUTPUT}
    assert names(collect([str(tmp_path)], skip_clean_outputs=False)) == [
        "backup_clean.tar.gz", "photo.jpg", "photo_clean.jpg"]


def test_files_named_explicitly_skip_no_rule(tmp_path):
    clean = touch(tmp_path / "photo_clean.jpg")
    hidden = touch(tmp_path / ".hidden.jpg")
    c = collect([str(clean), str(hidden)])
    assert sorted(names(c)) == [".hidden.jpg", "photo_clean.jpg"]
    assert all(item.explicit for item in c.items)


def test_output_folder_inside_the_input_folder_is_excluded(tmp_path):
    touch(tmp_path / "a.jpg")
    touch(tmp_path / "out" / "a.jpg")
    touch(tmp_path / "out" / "deep" / "b.jpg")
    c = collect([str(tmp_path)], recursive=True, exclude=[str(tmp_path / "out")])
    assert names(c) == ["a.jpg"]
    assert skipped(c) == {"out": batch.OUTPUT_FOLDER}


def test_output_folder_is_recognized_in_another_spelling(tmp_path, monkeypatch):
    touch(tmp_path / "in" / "a.jpg")
    touch(tmp_path / "in" / "out" / "a.jpg")
    monkeypatch.chdir(tmp_path)
    spelling = os.path.join(".", "in", "sub", "..", "out")
    if sys.platform in ("win32", "darwin"):
        spelling = spelling.upper()
    c = collect([str(tmp_path / "in")], recursive=True, exclude=[spelling])
    assert names(c) == ["a.jpg"]


# -- duplicates and order -----------------------------------------------------------------


def test_duplicates_are_processed_once(tmp_path, monkeypatch):
    f = touch(tmp_path / "a.jpg")
    touch(tmp_path / "b.jpg")
    monkeypatch.chdir(tmp_path)
    c = collect([str(f), "a.jpg", os.path.join(".", "a.jpg"), str(tmp_path), "b.jpg"])
    assert sorted(os.path.basename(i.path) for i in c.items) == ["a.jpg", "b.jpg"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file names ignore case")
def test_duplicates_in_another_case_on_windows(tmp_path):
    f = touch(tmp_path / "Photo.JPG")
    c = collect([str(f), str(tmp_path / "photo.jpg")])
    assert len(c.items) == 1


def test_order_is_sorted_and_independent_of_the_arguments(tmp_path):
    for rel in ("b.jpg", "a-b.jpg", "a/b.jpg", "c/a.jpg", "a.jpg", "a/Z.jpg"):
        touch(tmp_path / "in" / rel)
    root = str(tmp_path / "in")
    first = collect([root], recursive=True)
    # Compared by path parts and code points: the same on every system.
    assert names(first) == ["a/Z.jpg", "a/b.jpg", "a-b.jpg", "a.jpg", "b.jpg", "c/a.jpg"]
    again = collect([os.path.join(root, "c"), os.path.join(root, "b.jpg"), root],
                    recursive=True)
    assert sorted(i.path for i in again.items) == sorted(i.path for i in first.items)
    assert [i.path for i in again.items] == sorted(
        (i.path for i in again.items), key=batch.sort_key)


# -- links ---------------------------------------------------------------------------------


def make_symlink(target, link, directory=False):
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symbolic links here ({exc}); on Windows this needs "
                    "Developer Mode or administrator rights")


def test_symlinks_inside_folders_are_never_followed(tmp_path):
    outside = touch(tmp_path / "outside" / "secret.jpg")
    touch(tmp_path / "in" / "a.jpg")
    make_symlink(str(outside), str(tmp_path / "in" / "link.jpg"))
    make_symlink(str(outside.parent), str(tmp_path / "in" / "linkdir"), directory=True)
    c = collect([str(tmp_path / "in")], recursive=True)
    assert names(c) == ["a.jpg"]
    assert skipped(c) == {"link.jpg": batch.SYMLINK, "linkdir": batch.SYMLINK}


def test_symlink_arguments_are_refused(tmp_path):
    outside = touch(tmp_path / "secret.jpg")
    make_symlink(str(outside), str(tmp_path / "link.jpg"))
    make_symlink(str(tmp_path), str(tmp_path / "linkdir"), directory=True)
    c = collect([str(tmp_path / "link.jpg"), str(tmp_path / "linkdir")])
    assert not c.items
    assert len(c.errors) == 2
    assert all("symbolic link" in e.reason for e in c.errors)


def test_a_loop_of_links_does_not_hang(tmp_path):
    touch(tmp_path / "in" / "a.jpg")
    make_symlink(str(tmp_path / "in"), str(tmp_path / "in" / "loop"), directory=True)
    c = collect([str(tmp_path / "in")], recursive=True)
    assert names(c) == ["a.jpg"]


@pytest.mark.skipif(sys.platform != "win32", reason="junctions exist only on Windows")
def test_windows_junctions_are_never_followed(tmp_path):
    touch(tmp_path / "outside" / "secret.jpg")
    touch(tmp_path / "in" / "a.jpg")
    junction = tmp_path / "in" / "junction"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(tmp_path / "outside")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"cannot create a junction here: {r.stderr.strip()}")
    c = collect([str(tmp_path / "in")], recursive=True)
    assert names(c) == ["a.jpg"]
    assert skipped(c) == {"junction": batch.SYMLINK}
    # named on the command line: refused, not followed
    c = collect([str(junction)])
    assert not c.items and "symbolic link" in c.errors[0].reason
    # a junction that points back to its own folder does not make the walk loop
    loop = tmp_path / "in" / "loop"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(loop), str(tmp_path / "in")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    c = collect([str(tmp_path / "in")], recursive=True)
    assert names(c) == ["a.jpg"]
    assert skipped(c) == {"junction": batch.SYMLINK, "loop": batch.SYMLINK}


# -- platform-specific files ---------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="the hidden attribute exists only on Windows")
def test_windows_hidden_attribute(tmp_path):
    import ctypes

    hidden = touch(tmp_path / "hidden.jpg")
    folder = tmp_path / "hiddendir"
    touch(folder / "a.jpg")
    touch(tmp_path / "visible.jpg")
    for path in (hidden, folder):
        assert ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)
    c = collect([str(tmp_path)], recursive=True)
    assert names(c) == ["visible.jpg"]
    assert skipped(c) == {"hidden.jpg": batch.HIDDEN, "hiddendir": batch.HIDDEN}
    assert names(collect([str(tmp_path)], recursive=True, include_hidden=True)) == [
        "hidden.jpg", "hiddendir/a.jpg", "visible.jpg"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes need a POSIX system")
def test_pipes_are_skipped(tmp_path):
    os.mkfifo(tmp_path / "pipe.jpg")
    touch(tmp_path / "a.jpg")
    c = collect([str(tmp_path)])
    assert names(c) == ["a.jpg"]
    assert skipped(c) == {"pipe.jpg": batch.NOT_REGULAR}
    c = collect([str(tmp_path / "pipe.jpg")])
    assert not c.items and c.errors[0].reason == batch.NOT_REGULAR


@pytest.mark.skipif(sys.platform == "win32" or getattr(os, "geteuid", lambda: 0)() == 0,
                    reason="needs POSIX permissions and a user other than root")
def test_unreadable_folder_is_an_error_and_the_rest_goes_on(tmp_path):
    touch(tmp_path / "a.jpg")
    locked = tmp_path / "locked"
    touch(locked / "b.jpg")
    locked.chmod(0)
    try:
        c = collect([str(tmp_path)], recursive=True)
    finally:
        locked.chmod(0o755)
    assert names(c) == ["a.jpg"]
    assert [os.path.basename(e.path) for e in c.errors] == ["locked"]
    assert "cannot read the folder" in c.errors[0].reason


def test_unicode_names(tmp_path):
    for rel in ("fotografía.jpg", "日本/写真.png", "Ünïcödé/émoji 📷.gif"):
        touch(tmp_path / rel)
    c = collect([str(tmp_path)], recursive=True)
    assert sorted(names(c)) == sorted(["fotografía.jpg", "日本/写真.png",
                                       "Ünïcödé/émoji 📷.gif"])
    for item in c.items:
        assert load_file(item.path) == b"x"


@pytest.mark.skipif(sys.platform != "win32", reason="the 260-character limit is Windows only")
def test_long_paths_on_windows(tmp_path):
    root = tmp_path / "in"
    deep = "\\\\?\\" + str(root)
    for i in range(12):
        deep = os.path.join(deep, f"folder_with_a_long_name_{i:02d}")
    os.makedirs(deep)
    with open(os.path.join(deep, "photo.jpg"), "wb") as f:
        f.write(b"x")
    assert len(deep) > 300
    c = collect([str(root)], recursive=True)
    assert len(c.items) == 1 and not c.errors
    item = c.items[0]
    assert item.relative[-1] == "photo.jpg" and len(item.relative) == 13
    assert load_file(item.path) == b"x"
    assert batch.display_path(item.path).startswith(str(root))


# -- path helpers ---------------------------------------------------------------------------


def test_same_path_and_is_inside(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a" / "b").mkdir(parents=True)
    assert same_path("a", str(tmp_path / "a"))
    assert same_path(os.path.join("a", "b", ".."), "a")
    assert is_inside(os.path.join("a", "b", "c.jpg"), "a")
    assert is_inside("a", "a")
    assert not is_inside("ab", "a")
    assert not is_inside("a", os.path.join("a", "b"))
    if sys.platform in ("win32", "darwin"):
        assert same_path("A", "a")
