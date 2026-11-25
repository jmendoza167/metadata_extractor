"""The README is for users and is the PyPI page: its single-file examples are
real output (each line shown comes from running the command on the sample
it names), its links work outside GitHub, every option it mentions exists,
and development setup stays out of it (CLAUDE.md)."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from metaex import cli

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / "README.md").read_text(encoding="utf-8")
REPO = "https://github.com/jmendoza167/metadata_extractor"
SAMPLES = ROOT / "tests" / "samples"

# the files the examples use, made from the committed samples
EXAMPLE_FILES = {"photo.jpg": "jpeg/kitchen_sink.jpg", "letter.docx": "docx/kitchen_sink.docx"}


def example_blocks():
    """(command line, shown output lines) of every single-file example."""
    for block in re.findall(r"```\n(\$ metaex .*?)```", TEXT, re.S):
        command, *lines = block.rstrip("\n").split("\n")
        args = command[len("$ metaex "):].split()
        if any(a in EXAMPLE_FILES or a == "photo_clean.jpg" for a in args):
            yield command, args, lines


EXAMPLES = list(example_blocks())


def test_examples_are_found():
    assert {args[0] for _, args, _ in EXAMPLES} >= {"risk", "strip", "verify"}


@pytest.mark.parametrize("command, args, lines", EXAMPLES, ids=[c for c, _, _ in EXAMPLES])
def test_examples_are_real_output(tmp_path, command, args, lines):
    for name, sample in EXAMPLE_FILES.items():
        shutil.copyfile(SAMPLES / sample, tmp_path / name)
    if "photo_clean.jpg" in args:
        subprocess.run([sys.executable, "-m", "metaex", "strip", "photo.jpg"], cwd=tmp_path,
                       capture_output=True, check=True, timeout=120)
    r = subprocess.run([sys.executable, "-m", "metaex", *args], cwd=tmp_path,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    real = r.stdout.splitlines()
    for line in lines:
        if line.strip() == "..." or not line.strip():
            continue
        if line.endswith("..."):  # shortened in the README
            start = line[:-3].rstrip(" ,")
            assert any(o.startswith(start) for o in real), f"{command}: {line!r}"
        else:
            assert line in real, f"{command}: {line!r} is not in the real output"


def test_install_and_badges():
    assert "\npip install metaex\n" in TEXT
    assert f"[![tests]({REPO}/actions/workflows/tests.yml/badge.svg)]" in TEXT
    assert "https://img.shields.io/pypi/v/metaex" in TEXT


def test_links_to_other_files_are_absolute_and_exist():
    """Relative links break on PyPI, so files are linked on GitHub."""
    links = re.findall(r"\]\(([^)]+)\)", TEXT)
    for link in links:
        assert link.startswith(("https://", "#")), f"relative link: {link}"
        if link.startswith(f"{REPO}/blob/main/"):
            assert (ROOT / link[len(f"{REPO}/blob/main/"):]).is_file(), link
    for name in ("FORMATS.md", "SECURITY.md", "CHANGELOG.md", "LICENSE"):
        assert f"{REPO}/blob/main/{name}" in links


def test_in_page_links_resolve():
    headings = [line.lstrip("#").strip() for line in TEXT.splitlines() if line.startswith("#")]
    anchors = {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-") for h in headings}
    for target in re.findall(r"\]\(#([^)]+)\)", TEXT):
        assert target in anchors, target


def all_options() -> set[str]:
    parser = cli.build_parser()
    options = {o for a in parser._actions for o in a.option_strings}
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    for command in sub.choices.values():
        options |= {o for a in command._actions for o in a.option_strings}
    return options


def test_every_option_mentioned_exists():
    mentioned = set(re.findall(r"(?<![\w-])(--?[a-z][a-z-]*)", " ".join(
        re.findall(r"`([^`]+)`", TEXT))))
    unknown = {o for o in mentioned if o.startswith("--") or len(o) == 2} - all_options()
    assert unknown == set()


def test_every_command_is_documented():
    sub = next(a for a in cli.build_parser()._actions
               if a.__class__.__name__ == "_SubParsersAction")
    for command in sub.choices:
        assert f"metaex {command}" in TEXT, command


def test_no_development_setup():
    for word in ("python -m venv", "pytest", "gitleaks", "install_hooks", "pip install -e"):
        assert word not in TEXT, word
