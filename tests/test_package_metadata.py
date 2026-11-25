"""Package metadata: one version, shown by `metaex --version`, author without
an email address, and the classifiers and URLs PyPI shows.

These read the metadata of the installed package (pip install -e ".[dev]"),
so they check what a build actually produces, not just pyproject.toml."""

import importlib.metadata
import re
import subprocess
import sys
from pathlib import Path

import pytest

import metaex

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
REPO = "https://github.com/jmendoza167/metadata_extractor"


@pytest.fixture(scope="module")
def meta():
    try:
        return importlib.metadata.metadata("metaex")
    except importlib.metadata.PackageNotFoundError:
        pytest.fail('metaex is not installed; run pip install -e ".[dev]"')


def test_installed_version_is_the_package_version(meta):
    assert meta["Version"] == metaex.__version__, (
        "the installed metadata is stale; run pip install -e \".[dev]\" again")


def test_version_flag_shows_the_package_version():
    r = subprocess.run([sys.executable, "-m", "metaex", "--version"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    assert r.stdout.strip() == f"metaex {importlib.metadata.version('metaex')}"


def test_version_is_defined_in_one_place():
    if sys.version_info < (3, 11):
        pytest.skip("tomllib needs Python 3.11+")
    import tomllib
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert "version" not in project["project"]
    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "metaex.__version__"}
    assert re.fullmatch(r"\d+\.\d+\.\d+", metaex.__version__)
    assignments = [p for p in (ROOT / "src" / "metaex").rglob("*.py")
                   if re.search(r"(?m)^__version__\s*=", p.read_text(encoding="utf-8"))]
    assert assignments == [ROOT / "src" / "metaex" / "__init__.py"]


def test_author_without_email(meta):
    assert meta["Author"] == "jmendoza167"
    assert meta.get("Author-email") is None
    assert meta.get("Maintainer-email") is None
    for key, value in meta.items():
        if key != "Description":  # the README; checked separately
            assert "@" not in value, f"{key}: {value}"


def test_no_real_email_address_in_the_readme():
    """The README is the package's long description. Only the made-up
    addresses of the sample output (reserved example domains) may appear."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    domains = {m.lower() for m in re.findall(r"[\w.+-]+@([\w.-]+\.[a-z]+)", text, re.I)}
    assert domains <= {"example.com", "example.org", "example.net", "example.invalid"}


def test_summary_license_and_python(meta):
    assert meta["Summary"].startswith("Show and remove metadata")
    assert meta["License-Expression"] == "MIT"
    assert meta["Requires-Python"] == ">=3.10"
    assert meta.get_all("Requires-Dist") is None or all(
        "extra ==" in r for r in meta.get_all("Requires-Dist"))  # no runtime dependencies
    assert meta["Description-Content-Type"] == "text/markdown"


def test_classifiers(meta):
    classifiers = meta.get_all("Classifier")
    for minor in range(10, 15):
        assert f"Programming Language :: Python :: 3.{minor}" in classifiers
    for c in ("Operating System :: OS Independent", "Environment :: Console",
              "Topic :: Security", "Programming Language :: Python :: 3 :: Only"):
        assert c in classifiers
    assert not [c for c in classifiers if c.startswith("License ::")]  # License-Expression


def test_project_urls(meta):
    urls = dict(u.split(", ", 1) for u in meta.get_all("Project-URL"))
    assert urls == {"Homepage": REPO, "Issues": f"{REPO}/issues",
                    "Changelog": f"{REPO}/blob/main/CHANGELOG.md"}


def test_ci_tests_every_python_version_in_the_classifiers(meta):
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    matrix = re.search(r"python-version: \[(.*?)\]", workflow).group(1)
    tested = {v.strip(' "') for v in matrix.split(",")}
    claimed = {c.rsplit(" :: ", 1)[1] for c in meta.get_all("Classifier")
               if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", c)}
    assert claimed <= tested
