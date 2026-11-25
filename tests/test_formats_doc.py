"""FORMATS.md can't go stale: every supported format of the registry has its
own section with every extension, every refused type is in the refused
table, and every strip option it describes exists (and the other way round)."""

import re
from pathlib import Path

import pytest

from metaex import cli
from metaex.commands import formats
from metaex.core.registry import STRIPPABLE

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / "FORMATS.md").read_text(encoding="utf-8")
HEADINGS = [line.lstrip("#").strip() for line in TEXT.splitlines() if line.startswith("##")]

# strip options that only change what some formats keep
FORMAT_OPTIONS = ("--paranoid", "--no-orientation", "--rebuild-image-data",
                  "--remove-comments", "--deep")


def section(title_word: str) -> str:
    """The text from the heading that names a format to the next heading of
    the same or a higher level."""
    lines = TEXT.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("##") and re.search(rf"\b{re.escape(title_word)}\b", line):
            level = len(line) - len(line.lstrip("#"))
            end = next((j for j in range(i + 1, len(lines))
                        if lines[j].startswith("#")
                        and len(lines[j]) - len(lines[j].lstrip("#")) <= level), len(lines))
            return "\n".join(lines[i:end])
    raise AssertionError(f"no heading names {title_word}")


def test_supported_list_matches_the_registry():
    assert {t.key for _, t, _ in formats.SUPPORTED} == set(STRIPPABLE)


@pytest.mark.parametrize("filetype", [t for _, t, _ in formats.SUPPORTED],
                         ids=lambda t: t.key)
def test_every_supported_format_has_a_section(filetype):
    assert any(re.search(rf"\b{re.escape(filetype.name)}\b", h) for h in HEADINGS), filetype.name
    text = section(filetype.name)
    for ext in filetype.extensions:
        assert f"`{ext}`" in text, f"{filetype.name}: {ext} missing from its section"
    for part in ("**strip removes**", "**Refused**"):
        assert part in text, f"{filetype.name}: no {part}"


@pytest.mark.parametrize("category", sorted({c for c, _, _ in formats.SUPPORTED}))
def test_every_category_has_a_section(category):
    assert category in HEADINGS


@pytest.mark.parametrize("filetype", [t for t, _ in formats.refused_types()],
                         ids=lambda t: t.key)
def test_every_refused_type_is_listed(filetype):
    refused = section("Refused formats")
    row = next((line for line in refused.splitlines()
                if line.startswith(f"| {filetype.name} |")), None)
    assert row is not None, f"{filetype.name} missing from the refused table"
    assert f"`{filetype.extensions[0]}`" in row, filetype.name


def test_refused_table_has_no_unknown_rows():
    names = {t.name for t, _ in formats.refused_types()}
    rows = [line.split("|")[1].strip() for line in section("Refused formats").splitlines()
            if line.startswith("| ") and not line.startswith("| Format")]
    assert set(rows) == names


def strip_options() -> set[str]:
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    return {o for a in sub.choices["strip"]._actions for o in a.option_strings}


@pytest.mark.parametrize("option", FORMAT_OPTIONS)
def test_format_options_exist_and_are_described(option):
    assert option in strip_options()
    assert f"`{option}`" in TEXT


def test_every_option_mentioned_exists():
    known = strip_options() | {o for a in cli.build_parser()._actions for o in a.option_strings}
    mentioned = set(re.findall(r"`(--[a-z-]+)`", TEXT)) | set(re.findall(r"\((--[a-z-]+)\)", TEXT))
    assert mentioned - known == set()


def test_links_inside_the_file_resolve():
    anchors = {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-") for h in HEADINGS}
    for target in re.findall(r"\]\(#([^)]+)\)", TEXT):
        assert target in anchors, target
