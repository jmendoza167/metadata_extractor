"""The version is set everywhere a release needs it: the package, the
CHANGELOG, SECURITY.md's supported versions and the commands in
RELEASING.md; and the release checks pass on this repository."""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import metaex

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "release_notes.py"
spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)

VERSION = metaex.__version__
MAJOR_MINOR = ".".join(VERSION.split(".")[:2])


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_version_is_semantic():
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION)
    assert release_notes.source_version() == VERSION


def test_changelog_has_the_version():
    text = read("CHANGELOG.md")
    assert re.search(rf"(?m)^## \[{re.escape(VERSION)}\] - (Unreleased|\d{{4}}-\d{{2}}-\d{{2}})$",
                     text)
    assert f"[{VERSION}]: https://github.com/jmendoza167/metadata_extractor/releases/tag/v{VERSION}" in text


def test_security_lists_the_version_as_supported():
    assert f"| {MAJOR_MINOR}.x   | yes       |" in read("SECURITY.md")


def test_releasing_commands_use_the_version():
    text = read("RELEASING.md")
    assert f'git tag -a v{VERSION} -m "metaex {VERSION}"' in text
    assert f"git push origin v{VERSION}" in text
    assert f"dist/metaex-{VERSION}-py3-none-any.whl" in text
    assert f"## [{VERSION}] - Unreleased" in text
    old = set(re.findall(r"\b\d+\.\d+\.\d+\b", text)) - {VERSION}
    assert old <= {f"{VERSION[:-1]}{int(VERSION[-1]) + 1}"}, old  # only the next patch


def test_releasing_links_resolve():
    text = read("RELEASING.md")
    headings = [line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#")]
    anchors = {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-") for h in headings}
    for target in re.findall(r"\]\(#([^)]+)\)", text):
        assert target in anchors, target


def test_command_line_on_this_repository(tmp_path):
    """The real CHANGELOG has a section for the current version."""
    version = release_notes.source_version()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / f"metaex-{version}-py3-none-any.whl").write_bytes(b"x")
    (dist / f"metaex-{version}.tar.gz").write_bytes(b"x")
    out = tmp_path / "notes.md"
    r = subprocess.run([sys.executable, str(SCRIPT), "--dist", str(dist), "--out", str(out)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert out.read_text(encoding="utf-8").strip()
    r = subprocess.run([sys.executable, str(SCRIPT), "--dist", str(dist), "--out", str(out),
                        "--tag", "v0.0.0-wrong"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    assert "release_notes: tag 'v0.0.0-wrong' doesn't match" in r.stderr
