"""scripts/release_notes.py: the release workflow's checks (tag equals the
package version, the built files, the CHANGELOG date) and its notes."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "release_notes.py"
spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)

CHANGELOG = """# Changelog

## [Unreleased]

## [1.2.0] - {date}

### Added

- something new

## [1.1.0] - 2026-01-02

- older

[Unreleased]: https://example.com/compare/v1.2.0...HEAD
[1.2.0]: https://example.com/releases/tag/v1.2.0
"""


@pytest.fixture
def repo(tmp_path):
    def make(version="1.2.0", date="2026-03-04", dist=("whl", "sdist")):
        (tmp_path / "src" / "metaex").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "metaex" / "__init__.py").write_text(
            f'"""doc"""\n\n__version__ = "{version}"\n', encoding="utf-8")
        (tmp_path / "CHANGELOG.md").write_text(CHANGELOG.format(date=date), encoding="utf-8")
        d = tmp_path / "dist"
        d.mkdir(exist_ok=True)
        for kind in dist:
            name = (f"metaex-{version}-py3-none-any.whl" if kind == "whl"
                    else f"metaex-{version}.tar.gz")
            (d / name).write_bytes(b"x")
        return tmp_path
    return make


def run(root, tag):
    out = root / "notes.md"
    release_notes.run(root / "dist", tag, out, root=root)
    return out.read_text(encoding="utf-8")


def test_release_writes_the_section(repo):
    root = repo()
    notes = run(root, "v1.2.0")
    assert notes == "### Added\n\n- something new\n"


def test_tag_must_match_the_version(repo):
    root = repo()
    for tag in ("v1.2.1", "1.2.0", "v1.2", "v1.2.0-rc1"):
        with pytest.raises(release_notes.ReleaseError, match="doesn't match"):
            run(root, tag)


def test_release_needs_a_date(repo):
    root = repo(date="Unreleased")
    with pytest.raises(release_notes.ReleaseError, match="write the release date"):
        run(root, "v1.2.0")
    assert run(root, None) == "### Added\n\n- something new\n"  # dry run: fine


@pytest.mark.parametrize("date", ["2026-13-01", "2026-3-4", "soon"])
def test_date_must_be_valid(repo, date):
    with pytest.raises(release_notes.ReleaseError, match="is not a date"):
        run(repo(date=date), "v1.2.0")


def test_missing_section(repo):
    root = repo(version="1.3.0")
    with pytest.raises(release_notes.ReleaseError, match=r"no section '## \[1.3.0\]"):
        run(root, "v1.3.0")


@pytest.mark.parametrize("dist", [("whl",), ("sdist",), ()])
def test_dist_must_hold_both_files(repo, dist):
    with pytest.raises(release_notes.ReleaseError, match="must hold exactly"):
        run(repo(dist=dist), "v1.2.0")


def test_dist_of_another_version(repo):
    root = repo()
    (root / "dist" / "metaex-1.1.0-py3-none-any.whl").write_bytes(b"x")
    with pytest.raises(release_notes.ReleaseError, match="must hold exactly"):
        run(root, "v1.2.0")


def test_version_must_be_semantic(repo):
    with pytest.raises(release_notes.ReleaseError, match="is not X.Y.Z"):
        run(repo(version="1.2"), None)


def test_empty_section(repo, tmp_path):
    root = repo()
    (root / "CHANGELOG.md").write_text("## [1.2.0] - 2026-03-04\n\n## [1.1.0] - 2026-01-02\n",
                                       encoding="utf-8")
    with pytest.raises(release_notes.ReleaseError, match="is empty"):
        run(root, "v1.2.0")

