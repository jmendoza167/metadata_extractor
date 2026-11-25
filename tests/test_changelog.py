"""CHANGELOG.md follows Keep a Changelog: an Unreleased section on top, then
one section per version (newest first), each with a link reference."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
SECTIONS = re.findall(r"(?m)^## \[([^\]]+)\](?: - (.+))?$", TEXT)
REPO = "https://github.com/jmendoza167/metadata_extractor"


def test_unreleased_section_comes_first():
    assert SECTIONS[0] == ("Unreleased", "")


def test_versions_are_semantic_and_newest_first():
    versions = [v for v, _ in SECTIONS[1:]]
    assert versions, "no version section"
    keys = [tuple(int(p) for p in v.split(".")) for v in versions]
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in versions)
    assert keys == sorted(keys, reverse=True) and len(set(keys)) == len(keys)


def test_each_version_has_a_date_or_unreleased():
    for version, date in SECTIONS[1:]:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}|Unreleased", date), f"{version}: {date!r}"
    # only the newest version may still be waiting for its release date
    assert all(date != "Unreleased" for _, date in SECTIONS[2:])


def test_link_references():
    versions = [v for v, _ in SECTIONS[1:]]
    assert f"[Unreleased]: {REPO}/compare/v{versions[0]}...HEAD" in TEXT
    for version in versions:
        assert re.search(rf"(?m)^\[{re.escape(version)}\]: {re.escape(REPO)}/", TEXT), version
