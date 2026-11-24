"""The committed samples match their generator, stay small, and contain no
real-looking personal data."""

from __future__ import annotations

import base64
import re
import subprocess
import sys

import pytest

from helpers.samples import SAMPLES_DIR, SCRIPT

ALLOWED_USER = "jane.doe"
"""The one (made-up) user name allowed in a user folder path (svg/editor.svg)."""
ALLOWED_EMAIL_DOMAIN = "example.com"

MAX_FILE_SIZE = 100 * 1024
MAX_FOLDER_SIZE = 1536 * 1024
"""The samples (under 1 MiB, see scripts/make_samples.py) plus their snapshots in
expected/: an archive sample's snapshots also report the files inside it."""

# -- the generator ------------------------------------------------------------------------


def test_samples_match_the_generator():
    r = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True,
                       text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert "sample files match the generator" in r.stdout


def test_the_generator_does_not_use_pillow():
    code = ("import importlib.util, sys\n"
            f"spec = importlib.util.spec_from_file_location('make_samples', {str(SCRIPT)!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "module.build()\n"
            "print(sorted(m for m in sys.modules if m.split('.')[0] == 'PIL'))\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"


def all_files():
    return sorted(p for p in SAMPLES_DIR.rglob("*") if p.is_file())


def test_sizes():
    files = all_files()
    assert files
    for path in files:
        assert path.stat().st_size < MAX_FILE_SIZE, path
    assert sum(p.stat().st_size for p in files) < MAX_FOLDER_SIZE


# -- personal data ------------------------------------------------------------------------

_SEP = r"(?:\\|/)+"  # \, / and JSON-escaped \\
_USER_PATH = re.compile(rf"(?i)(?:\b[a-z]:{_SEP}(?:users|documents and settings){_SEP}"
                        rf"|/home/|/users/)([^\\/\"'<>\s&;:*?|]+)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
_DATA_URI = re.compile(rb"data:[\w.+/-]+;base64,([A-Za-z0-9+/=\s]+)")


def texts(data: bytes, depth: int = 0):
    """The bytes read as Latin-1 and as UTF-16 (both byte orders and
    alignments), plus the same for every base64 data: URI inside."""
    yield data.decode("latin-1")
    for start in (0, 1):
        for codec in ("utf-16-le", "utf-16-be"):
            chunk = data[start:]
            yield chunk[: len(chunk) // 2 * 2].decode(codec, errors="replace")
    if depth < 3:
        for m in _DATA_URI.finditer(data):
            try:
                inner = base64.b64decode(re.sub(rb"\s", b"", m.group(1)), validate=True)
            except ValueError:
                continue
            yield from texts(inner, depth + 1)


def personal_data(data: bytes) -> list[str]:
    found = []
    for text in texts(data):
        for m in _USER_PATH.finditer(text):
            if m.group(1) != ALLOWED_USER:
                found.append(f"user path: {m.group(0)!r}")
        for m in _EMAIL.finditer(text):
            domain = m.group(1).lower()
            if domain != ALLOWED_EMAIL_DOMAIN and not domain.endswith("." + ALLOWED_EMAIL_DOMAIN):
                found.append(f"email address: {m.group(0)!r}")
    return sorted(set(found))


@pytest.mark.parametrize("path", all_files(),
                         ids=lambda p: p.relative_to(SAMPLES_DIR).as_posix())
def test_no_personal_data_in_samples(path):
    assert personal_data(path.read_bytes()) == []


def test_the_allowed_user_path_is_really_there():
    """So the scan above is not passing because it finds nothing at all."""
    data = (SAMPLES_DIR / "svg" / "editor.svg").read_bytes()
    assert b"C:\\Users\\jane.doe\\Pictures" in data
    assert [m.group(1) for m in _USER_PATH.finditer(data.decode("latin-1"))] == ["jane.doe"]


# The cases below build their values at runtime, so no real-looking path or
# address appears literally in this file.
SEP = "\\"


@pytest.mark.parametrize("data", [
    f"C:{SEP}Users{SEP}bob{SEP}Pictures".encode(),
    f"c:/users/{'bob'}/x".encode(),
    f"D:{SEP}Documents and Settings{SEP}bob{SEP}".encode(),
    ("/" + "home/alice/notes.txt").encode(),
    ("/" + "Users/carol/Desktop").encode(),
    ("file:///" + "home/alice/a.png").encode(),
    ('{"value": "C:' + SEP * 2 + "Users" + SEP * 2 + 'bob"}').encode(),  # JSON-escaped
    f"C:{SEP}Users{SEP}bob{SEP}".encode("utf-16-le"),  # EXIF XPAuthor style
    b"\x00" + f"C:{SEP}Users{SEP}bob{SEP}".encode("utf-16-be"),
    f"C:{SEP}Users{SEP}jane.doe2{SEP}".encode(),
    f"C:{SEP}Users{SEP}Jane.Doe{SEP}".encode(),
    b'<image href="data:image/svg+xml;base64,'
    + base64.b64encode(("/" + "home/bob/x").encode()) + b'"/>',
    ("bob" + "@" + "mail.test").encode(),
    ("jane.doe" + "@" + "example.com.evil.test").encode(),
    ("jane" + "@" + "notexample.com").encode(),
])
def test_scan_finds_real_looking_data(data):
    assert personal_data(data) != []


@pytest.mark.parametrize("data", [
    f"C:{SEP}Users{SEP}jane.doe{SEP}Pictures{SEP}floor plan.png".encode(),
    ("/" + "home/jane.doe/a.png").encode(),
    ("jane.doe" + "@" + "example.com").encode(),
    ("someone" + "@" + "mail.example.com").encode(),
    b"D:\\Projects\\plan.svg /tmp/plan.svg http://ns.adobe.com/xap/1.0/ xmp.did:1234",
])
def test_scan_allows_the_fake_values(data):
    assert personal_data(data) == []
