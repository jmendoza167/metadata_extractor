"""Check a release and write its notes from CHANGELOG.md.

Usage (from the repository root):

    python scripts/release_notes.py --dist dist --tag v0.1.0 --out notes.md
    python scripts/release_notes.py --dist dist --out notes.md      # TestPyPI dry run

Checks, and exits with 1 (and a clear message) if any fails:

- the version in src/metaex/__init__.py is X.Y.Z,
- dist/ holds exactly one sdist and one wheel, both of that version,
- with --tag: the tag is exactly "v" + that version, and CHANGELOG.md has a
  section "## [X.Y.Z] - YYYY-MM-DD" with a real date (not "Unreleased"),
- without --tag (dry run): the section may still say "Unreleased",
- the section is not empty.

Then writes the section's text (without its heading) to --out, for the
GitHub Release. Development tool only: standard library, used by
.github/workflows/release.yml and RELEASING.md.
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_RE = re.compile(r'(?m)^__version__\s*=\s*"([^"]+)"\s*$')


class ReleaseError(Exception):
    pass


def source_version(root: Path = ROOT) -> str:
    text = (root / "src" / "metaex" / "__init__.py").read_text(encoding="utf-8")
    found = VERSION_RE.findall(text)
    if len(found) != 1:
        raise ReleaseError("src/metaex/__init__.py must set __version__ exactly once")
    version = found[0]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ReleaseError(f"version {version!r} is not X.Y.Z")
    return version


def check_dist(dist: Path, version: str) -> list[Path]:
    if not dist.is_dir():
        raise ReleaseError(f"no {dist} folder")
    files = sorted(p for p in dist.iterdir() if p.is_file())
    expected = [f"metaex-{version}-py3-none-any.whl", f"metaex-{version}.tar.gz"]
    if sorted(p.name for p in files) != sorted(expected):
        raise ReleaseError(f"{dist} must hold exactly {' and '.join(expected)}; it holds: "
                           f"{', '.join(p.name for p in files) or 'nothing'}")
    return files


def changelog_section(text: str, version: str, need_date: bool) -> str:
    heading = re.compile(rf"(?m)^## \[{re.escape(version)}\] - (.+?)\s*$")
    m = heading.search(text)
    if not m:
        raise ReleaseError(f"CHANGELOG.md has no section '## [{version}] - ...'")
    date = m.group(1)
    if date == "Unreleased":
        if need_date:
            raise ReleaseError(f"CHANGELOG.md still says '## [{version}] - Unreleased'; "
                               "write the release date (YYYY-MM-DD) before tagging")
    else:
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            raise ReleaseError(f"CHANGELOG.md: {date!r} is not a date (YYYY-MM-DD)") from None
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise ReleaseError(f"CHANGELOG.md: {date!r} is not a date (YYYY-MM-DD)")
    rest = text[m.end():]
    end = re.search(r"(?m)^## |^\[[^\]]+\]: ", rest)
    body = (rest[:end.start()] if end else rest).strip()
    if not body:
        raise ReleaseError(f"the CHANGELOG.md section for {version} is empty")
    return body + "\n"


def run(dist: Path, tag: str | None, out: Path, root: Path = ROOT) -> str:
    version = source_version(root)
    if tag is not None and tag != f"v{version}":
        raise ReleaseError(f"tag {tag!r} doesn't match the package version {version} "
                           f"(expected v{version})")
    check_dist(dist, version)
    notes = changelog_section((root / "CHANGELOG.md").read_text(encoding="utf-8"), version,
                              need_date=tag is not None)
    out.write_text(notes, encoding="utf-8", newline="\n")
    return version


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check a release and write its notes.")
    parser.add_argument("--dist", type=Path, required=True, help="folder with the built files")
    parser.add_argument("--tag", help="the pushed tag (vX.Y.Z); omit for a dry run")
    parser.add_argument("--out", type=Path, required=True, help="where to write the notes")
    args = parser.parse_args(argv)
    try:
        version = run(args.dist, args.tag, args.out)
    except (ReleaseError, OSError) as e:
        print(f"release_notes: {e}", file=sys.stderr)
        return 1
    kind = f"release {args.tag}" if args.tag else "dry run"
    print(f"release_notes: {kind}: version {version} checked; notes written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
