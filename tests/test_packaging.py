"""The built packages hold exactly what was decided: the wheel only the
metaex package (and its metadata and license); the sdist src/, README,
LICENSE, CHANGELOG and pyproject, never tests/ or the samples (the damaged
samples include a zip bomb).

The build runs on a copy of the files git would commit (tracked and
untracked-but-not-ignored), plus a planted private_samples/ file, so it
checks what a release build from a clean checkout produces."""

import email
import importlib.util
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

import metaex

ROOT = Path(__file__).resolve().parent.parent
GIT = shutil.which("git")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(GIT is None or not (ROOT / ".git").exists(),
                       reason="needs git and a git checkout of metaex"),
    pytest.mark.skipif(any(importlib.util.find_spec(m) is None
                           for m in ("build", "twine", "setuptools")),
                       reason='needs build, twine and setuptools (pip install -e ".[dev]")'),
]

DIST_INFO = {"METADATA", "WHEEL", "RECORD", "entry_points.txt", "top_level.txt",
             "licenses/LICENSE"}
EGG_INFO = {"PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt",
            "requires.txt", "top_level.txt"}


def project_files() -> list[str]:
    out = subprocess.run([GIT, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, check=True).stdout
    return [n for n in out.decode("utf-8").split("\0") if n and (ROOT / n).is_file()]


def package_sources() -> set[str]:
    """metaex/... for every .py file of the package."""
    return {n[len("src/"):] for n in project_files()
            if n.startswith("src/metaex/") and n.endswith(".py")}


@pytest.fixture(scope="module")
def dist(tmp_path_factory):
    base = tmp_path_factory.mktemp("packaging")
    tree = base / "tree"
    for name in project_files():
        target = tree / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (tree / "private_samples").mkdir(exist_ok=True)
    (tree / "private_samples" / "my_photo.jpg").write_bytes(b"\xff\xd8 not for packages")
    out = base / "dist"
    # no flags: build the sdist, then the wheel from that sdist
    r = subprocess.run([sys.executable, "-m", "build", "--no-isolation", "--outdir", str(out),
                        str(tree)], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    sdists = list(out.glob("*.tar.gz"))
    wheels = list(out.glob("*.whl"))
    assert len(sdists) == 1 and len(wheels) == 1, os.listdir(out)
    return sdists[0], wheels[0]


def test_file_names(dist):
    sdist, wheel = dist
    v = metaex.__version__
    assert sdist.name == f"metaex-{v}.tar.gz"
    assert wheel.name == f"metaex-{v}-py3-none-any.whl"


def test_wheel_contents(dist):
    _, wheel = dist
    with zipfile.ZipFile(wheel) as z:
        names = set(z.namelist())
        info = f"metaex-{metaex.__version__}.dist-info"
        assert names == package_sources() | {f"{info}/{n}" for n in DIST_INFO}
        assert "metaex = metaex.cli:main" in z.read(f"{info}/entry_points.txt").decode()
        assert z.read(f"{info}/licenses/LICENSE") == (ROOT / "LICENSE").read_bytes()
        # (line endings follow the checkout: CRLF on Windows, so parse the headers)
        metadata = email.message_from_bytes(z.read(f"{info}/METADATA"))
    assert metadata["Version"] == metaex.__version__
    requires = metadata.get_all("Requires-Dist")
    assert requires and all("extra ==" in r for r in requires)  # only the [dev] extra


def test_sdist_contents(dist):
    sdist, _ = dist
    top = f"metaex-{metaex.__version__}"
    with tarfile.open(sdist) as t:
        members = t.getmembers()
    assert all(m.name == top or m.name.startswith(top + "/") for m in members)
    files = {m.name[len(top) + 1:] for m in members if m.isfile()}
    expected = {"PKG-INFO", "setup.cfg", "pyproject.toml", "README.md", "LICENSE"}
    if (ROOT / "CHANGELOG.md").exists():
        expected.add("CHANGELOG.md")
    expected |= {f"src/{n}" for n in package_sources()}
    expected |= {f"src/metaex.egg-info/{n}" for n in EGG_INFO}
    assert files == expected
    assert not [m for m in members if not (m.isfile() or m.isdir())]  # no links


def test_nothing_private_or_dangerous_is_packaged(dist):
    names = []
    for path in dist:
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as z:
                names += z.namelist()
        else:
            with tarfile.open(path) as t:
                names += t.getnames()
    for bad in ("tests/", "samples", "private_samples", "scripts/", ".github", "CLAUDE.md",
                ".pyc", "__pycache__", ".git"):
        assert not [n for n in names if bad in n], bad


def test_twine_check_passes(dist):
    r = subprocess.run([sys.executable, "-m", "twine", "check", "--strict", *map(str, dist)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("PASSED") == 2, r.stdout


def test_the_wheel_works_in_a_fresh_venv(dist):
    """scripts/check_wheel.py (used by CI): install the wheel alone in a new
    venv and strip + verify samples with the installed program."""
    _, wheel = dist
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_wheel.py"), str(wheel),
                        str(ROOT / "tests" / "samples")],
                       capture_output=True, text=True, encoding="utf-8", timeout=900)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"all checks passed for {wheel.name}" in r.stdout


def test_check_wheel_fails_on_a_broken_wheel(tmp_path):
    fake = tmp_path / "metaex-9.9.9-py3-none-any.whl"
    with zipfile.ZipFile(fake, "w") as z:
        z.writestr("nothing.txt", "not a package")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_wheel.py"), str(fake),
                        str(ROOT / "tests" / "samples")],
                       capture_output=True, text=True, encoding="utf-8", timeout=900)
    assert r.returncode == 1
    assert "check_wheel: FAILED: " in r.stderr
