import builtins
import os

import pytest

from metaex.core.errors import FileAccessError, FileTooLargeError, MetaexError
from metaex.core.loader import DEFAULT_MAX_SIZE, MIB, format_size, load_file


def test_default_limit_is_512_mib():
    assert DEFAULT_MAX_SIZE == 512 * MIB


def test_loads_small_file(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"hello")
    assert load_file(p) == b"hello"
    assert load_file(str(p)) == b"hello"


def test_empty_file(tmp_path):
    p = tmp_path / "empty"
    p.write_bytes(b"")
    assert load_file(p) == b""


def test_file_exactly_at_limit_is_allowed(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"x" * 10)
    assert load_file(p, max_size=10) == b"x" * 10


def test_file_over_limit_is_refused(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"x" * 11)
    with pytest.raises(FileTooLargeError, match="too large"):
        load_file(p, max_size=10)


def test_size_is_checked_before_reading(tmp_path, monkeypatch):
    p = tmp_path / "big.bin"
    p.write_bytes(b"x" * 100)

    def no_open(*args, **kwargs):
        raise AssertionError("file was opened despite being too large")

    monkeypatch.setattr(builtins, "open", no_open)
    with pytest.raises(FileTooLargeError):
        load_file(p, max_size=10)


def recording_open(monkeypatch):
    """Patch open() so every read(n) size is recorded."""
    sizes = []
    real_open = builtins.open

    class Recorder:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

        def read(self, n=-1):
            sizes.append(n)
            return self.f.read(n)

    monkeypatch.setattr(builtins, "open", lambda *a, **k: Recorder(real_open(*a, **k)))
    return sizes


def test_reads_only_the_file_size_not_the_limit(tmp_path, monkeypatch):
    """Regression: read(max_size + 1) allocated 512 MiB for every file loaded."""
    p = tmp_path / "a.bin"
    p.write_bytes(b"x" * 100)
    sizes = recording_open(monkeypatch)
    assert load_file(p) == b"x" * 100
    assert sizes == [101]


class FakeStat:
    def __init__(self, real, size):
        self.st_mode, self.st_size = real.st_mode, size


def test_file_that_grew_after_stat_is_loaded_whole(tmp_path, monkeypatch):
    p = tmp_path / "a.bin"
    p.write_bytes(b"x" * 500)
    real_stat = os.stat
    monkeypatch.setattr(os, "stat", lambda name: FakeStat(real_stat(name), 100))
    assert load_file(p, max_size=1000) == b"x" * 500


def test_file_that_grew_past_the_limit_after_stat_is_refused(tmp_path, monkeypatch):
    p = tmp_path / "a.bin"
    p.write_bytes(b"x" * 5000)
    real_stat = os.stat
    monkeypatch.setattr(os, "stat", lambda name: FakeStat(real_stat(name), 100))
    sizes = recording_open(monkeypatch)
    with pytest.raises(FileTooLargeError):
        load_file(p, max_size=1000)
    assert sizes == [101, 900]  # never more than max_size + 1 in total


def test_missing_file(tmp_path):
    with pytest.raises(FileAccessError, match="no such file"):
        load_file(tmp_path / "nope.jpg")


def test_directory(tmp_path):
    with pytest.raises(FileAccessError, match="is a directory"):
        load_file(tmp_path)


def test_permission_denied(tmp_path, monkeypatch):
    # chmod cannot reliably deny reads on Windows or as root, so simulate it.
    p = tmp_path / "secret"
    p.write_bytes(b"data")

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(builtins, "open", denied)
    with pytest.raises(FileAccessError, match="permission denied"):
        load_file(p)


def test_permission_denied_on_stat(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "stat", denied)
    with pytest.raises(FileAccessError, match="permission denied"):
        load_file(tmp_path / "x")


def test_errors_are_metaex_errors(tmp_path):
    with pytest.raises(MetaexError):
        load_file(tmp_path / "nope")


@pytest.mark.parametrize("bad", [-1, 1.5, None, True])
def test_invalid_max_size(tmp_path, bad):
    p = tmp_path / "a"
    p.write_bytes(b"")
    with pytest.raises(ValueError):
        load_file(p, max_size=bad)


@pytest.mark.parametrize(
    "n,expected",
    [(0, "0 B"), (1, "1 byte"), (1023, "1023 B"), (1024, "1.0 KiB"),
     (1536, "1.5 KiB"), (MIB, "1.0 MiB"), (512 * MIB, "512.0 MiB")],
)
def test_format_size(n, expected):
    assert format_size(n) == expected
