import pytest

from metaex.core.errors import FileAccessError
from metaex.core.writer import write_new_file


def test_writes_new_file(tmp_path):
    p = tmp_path / "out.bin"
    write_new_file(p, b"data")
    assert p.read_bytes() == b"data"


def test_refuses_existing_file(tmp_path):
    p = tmp_path / "out.bin"
    p.write_bytes(b"keep me")
    with pytest.raises(FileAccessError, match="refusing to overwrite"):
        write_new_file(p, b"new")
    assert p.read_bytes() == b"keep me"


def test_refuses_directory(tmp_path):
    with pytest.raises(FileAccessError):
        write_new_file(tmp_path, b"x")


def test_missing_parent_directory(tmp_path):
    with pytest.raises(FileAccessError, match="cannot write"):
        write_new_file(tmp_path / "no" / "such" / "dir.bin", b"x")
