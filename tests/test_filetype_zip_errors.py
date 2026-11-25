"""ZIP type detection on a damaged "mimetype" entry.

Regression test (found by fuzzing): the type of a ZIP file is told from its
names and its small "mimetype" entry, read with the standard zipfile
module. A mimetype entry marked as compressed with invalid data made zlib
raise an error that was not caught, so every command stopped with
"internal error". Such files are now detected as a ZIP whose directory
can't be used, like other damaged archives."""

import struct
import subprocess
import sys
import zlib

import pytest

from metaex.core import filetype


def one_entry_zip(method: int, payload: bytes, size: int = 5) -> bytes:
    """A ZIP with one "mimetype" entry stored with ``method`` (119 bytes)."""
    name = b"mimetype"
    crc = zlib.crc32(b"x" * size)
    local = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, method, 0, 0x21, crc,
                        len(payload), size, len(name), 0) + name + payload
    central = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, method, 0, 0x21, crc,
                          len(payload), size, len(name), 0, 0, 0, 0, 0, 0) + name
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(central), len(local), 0)
    return local + central + end


BROKEN = {
    "deflate": one_entry_zip(8, b"\x01\x05\x00\x00\x00"),  # stored block, LEN != ~NLEN
    "lzma": one_entry_zip(14, b"\x09\x14\x05\x00" + b"\xff" * 8),
    "bzip2": one_entry_zip(12, b"BZh91AY&SY" + b"\xff" * 8),
}


@pytest.mark.parametrize("data", BROKEN.values(), ids=BROKEN.keys())
def test_detection_does_not_crash(data):
    detected = filetype.detect(data)
    assert detected.key == "zip"


@pytest.mark.parametrize("command", ["show", "risk", "strip", "verify"])
def test_no_internal_error(tmp_path, command):
    path = tmp_path / "broken.zip"
    path.write_bytes(BROKEN["deflate"])
    r = subprocess.run([sys.executable, "-m", "metaex", command, str(path)],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert "internal error" not in r.stderr, r.stderr
