"""The gitleaks scans skip only tests/samples/malformed/ (marked -diff in
.gitattributes, so git shows those files as binary): every other path is
still scanned, by the pre-commit hook and by the full-history scan."""

import json
import random
import shutil
import string
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GIT = shutil.which("git")
GITLEAKS = shutil.which("gitleaks")

needs_git = pytest.mark.skipif(GIT is None, reason="git is not installed")
needs_gitleaks = pytest.mark.skipif(GIT is None or GITLEAKS is None,
                                    reason="git or gitleaks is not installed")


def in_git_checkout() -> bool:
    if GIT is None or not (ROOT / ".git").exists():
        return False
    r = subprocess.run([GIT, "rev-parse", "--show-toplevel"], cwd=ROOT,
                       capture_output=True, text=True)
    return r.returncode == 0 and Path(r.stdout.strip()).resolve() == ROOT


needs_checkout = pytest.mark.skipif(not in_git_checkout(),
                                    reason="not running from a git checkout of metaex")


def fake_token() -> str:
    """A GitHub-token-shaped string, built at runtime so no literal token
    pattern exists in the source."""
    rng = random.Random(9)
    body = "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(36))
    return "gh" + "p_" + body


def git(cwd, *args):
    return subprocess.run([GIT, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=", *args],
                          cwd=cwd, capture_output=True, text=True, check=True)


def diff_attr(cwd, path):
    out = git(cwd, "check-attr", "diff", "--", path).stdout.strip()
    return out.rsplit(": ", 1)[1]


@needs_git
@needs_checkout
@pytest.mark.parametrize("path, expected", [
    ("tests/samples/malformed/truncated.docx", "unset"),
    ("tests/samples/malformed/encrypted_entry.docx", "unset"),
    ("tests/samples/malformed/entity.svg", "unset"),
])
def test_malformed_samples_are_binary_for_diffs(path, expected):
    assert diff_attr(ROOT, path) == expected


@needs_git
@needs_checkout
@pytest.mark.parametrize("path", [
    "tests/samples/svg/clean.svg",
    "tests/samples/docx/kitchen_sink.docx",
    "tests/samples/expected/docx/clean.docx.show.json",
    "tests/samples/SAMPLES.md",
    "tests/samples/malformed_notes.md",  # a name that only starts like the folder
    "src/metaex/cli.py",
    "README.md",
])
def test_nothing_else_is_excluded(path):
    assert diff_attr(ROOT, path) != "unset"


def scan(repo: Path, *args) -> tuple[subprocess.CompletedProcess, set]:
    report = repo.parent / "report.json"
    r = subprocess.run([GITLEAKS, "git", *args, "--redact", "--no-banner",
                        "--report-format", "json", "--report-path", str(report), str(repo)],
                       capture_output=True, text=True, timeout=120)
    files = {f["File"].replace("\\", "/") for f in json.loads(report.read_text() or "[]")}
    return r, files


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    shutil.copyfile(ROOT / ".gitattributes", repo / ".gitattributes")
    token = fake_token()
    # .md and .json: gitleaks' own default config never scans images,
    # SVG included, wherever they are.
    for rel in ("src/settings.py", "tests/samples/expected/leak.json", "tests/samples/LEAK.md",
                "tests/samples/malformed/leak.json"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(f'token = "{token}"\n', encoding="ascii")
    git(repo, "add", "-A")
    return repo


OUTSIDE = {"src/settings.py", "tests/samples/expected/leak.json", "tests/samples/LEAK.md"}


@needs_gitleaks
def test_hook_scan_still_catches_secrets_outside_malformed(repo):
    r, files = scan(repo, "--pre-commit", "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    assert files == OUTSIDE


@needs_gitleaks
def test_history_scan_still_catches_secrets_outside_malformed(repo):
    git(repo, "commit", "-q", "-m", "add files")
    r, files = scan(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert files == OUTSIDE


@needs_gitleaks
@needs_checkout
def test_full_history_scan_is_clean_without_errors(tmp_path):
    r = subprocess.run([GITLEAKS, "git", "--redact", "--no-banner",
                        "--report-format", "json", "--report-path", str(tmp_path / "r.json"),
                        str(ROOT)],
                       capture_output=True, text=True, timeout=300)
    output = r.stdout + r.stderr
    assert r.returncode == 0, output
    assert "no leaks found" in output
    assert "ERR" not in output
    assert "Failed to extract" not in output
