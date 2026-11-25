"""CI workflows: every third-party action is pinned to a full commit SHA
(with its version in a comment), permissions are read-only by default, and
checkouts don't keep the token."""

import re
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows")
                   .glob("*.yml"))
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$", re.M)


def test_there_are_workflows():
    assert {p.name for p in WORKFLOWS} >= {"tests.yml", "gitleaks.yml"}


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_to_commit_shas(workflow):
    text = workflow.read_text(encoding="utf-8")
    for ref, rest in USES.findall(text):
        if ref.startswith("./"):  # a workflow of this repository
            continue
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), ref
        assert re.fullmatch(r"\s+#\s+v\d+\.\d+\.\d+\s*", rest), f"{ref}: no version comment"


def test_same_action_same_pin():
    pins = {}
    for p in WORKFLOWS:
        for ref, rest in USES.findall(p.read_text(encoding="utf-8")):
            name, _, sha = ref.partition("@")
            pins.setdefault(name, set()).add((sha, rest.strip()))
    assert {k: v for k, v in pins.items() if len(v) > 1} == {}


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_read_only_by_default(workflow):
    text = workflow.read_text(encoding="utf-8")
    assert re.search(r"(?m)^permissions:\n  contents: read\n", text) or re.search(
        r"(?m)^permissions: \{\}\n", text)


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_checkouts_do_not_keep_the_token(workflow):
    text = workflow.read_text(encoding="utf-8")
    checkouts = len(re.findall(r"uses:\s*actions/checkout@", text))
    assert text.count("persist-credentials: false") == checkouts


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_no_stored_secrets(workflow):
    """Publishing uses Trusted Publishing (OIDC): no API token secrets."""
    text = workflow.read_text(encoding="utf-8")
    assert "secrets." not in text.replace("secrets.GITHUB_TOKEN", "")
    assert "password:" not in text


# -- release.yml ---------------------------------------------------------------------------

RELEASE = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml")


def jobs(text: str) -> dict[str, str]:
    """Each job's text, by job id (two-space indented keys under "jobs:")."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"(?m)^  ([\w-]+):\n", body)
    return dict(zip(parts[1::2], parts[2::2]))


def test_release_workflow_name_and_triggers():
    """The Trusted Publishers on PyPI and TestPyPI are registered for this
    exact file name and these environment names."""
    assert RELEASE.name == "release.yml" and RELEASE.is_file()
    text = RELEASE.read_text(encoding="utf-8")
    assert re.search(r'(?m)^  push:\n    tags: \["v\*"\]\n', text)
    assert re.search(r"(?m)^  workflow_dispatch:\n", text)
    assert re.search(r"(?m)^permissions: \{\}\n", text)


def test_release_jobs_and_permissions():
    j = jobs(RELEASE.read_text(encoding="utf-8"))
    assert set(j) == {"tests", "check", "publish-pypi", "publish-testpypi", "github-release"}
    assert "uses: ./.github/workflows/tests.yml" in j["tests"]
    # least privilege: only the publish jobs get an OIDC token, only the
    # release job may write
    for name, body in j.items():
        assert ("id-token: write" in body) == name.startswith("publish-"), name
        assert ("contents: write" in body) == (name == "github-release"), name
        assert "permissions:" in body, f"{name}: no job permissions"


def test_publish_jobs():
    j = jobs(RELEASE.read_text(encoding="utf-8"))
    pypi, testpypi = j["publish-pypi"], j["publish-testpypi"]
    assert re.search(r"environment:\n      name: pypi\n", pypi)
    assert re.search(r"environment:\n      name: testpypi\n", testpypi)
    assert "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')" in pypi
    assert "github.event_name == 'workflow_dispatch'" in testpypi
    assert "repository-url: https://test.pypi.org/legacy/" in testpypi
    assert "repository-url" not in pypi
    for body in (pypi, testpypi):
        assert "needs: check" in body
        assert "uses: pypa/gh-action-pypi-publish@" in body
        assert "name: dist" in body  # the files built once by tests.yml


def test_release_checks_tag_and_writes_notes():
    j = jobs(RELEASE.read_text(encoding="utf-8"))
    assert 'release_notes.py --dist dist --tag "$TAG"' in j["check"]
    assert "needs: publish-pypi" in j["github-release"]
    assert "gh release create" in j["github-release"]
    assert "--notes-file notes.md" in j["github-release"]
    assert "dist/*" in j["github-release"]


def test_tests_workflow_can_be_called():
    text = (RELEASE.parent / "tests.yml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^  workflow_call:", text)
    assert "name: dist" in jobs(text)["package"]


# -- the build of the published files -------------------------------------------------------

BUILD_REQUIREMENTS = RELEASE.parent.parent / "build-requirements.txt"


def steps(job_text: str) -> list[str]:
    """The job's steps, as text, in order."""
    return re.split(r"(?m)^      - ", job_text)[1:]


def test_packages_are_built_with_tools_pinned_by_hash():
    """Regression test: the build tools were pinned by version only, and the
    isolated build environment installed the newest setuptools, so the
    published wheel was built with whatever was on PyPI that day."""
    package = jobs((RELEASE.parent / "tests.yml").read_text(encoding="utf-8"))["package"]
    assert ("python -m pip install --require-hashes --no-deps "
            "-r .github/build-requirements.txt") in package
    assert "python -m build --no-isolation --outdir dist ." in package
    order = steps(package)
    upload = next(i for i, s in enumerate(order) if "actions/upload-artifact@" in s)
    # nothing but the pinned tools is installed before the upload
    installs = [i for i, s in enumerate(order) if "pip install" in s]
    assert installs[0] < upload and all(
        "--require-hashes" in order[i] for i in installs if i < upload)
    assert any("twine check" in s for s in order[upload + 1:])


def test_build_requirements_are_pinned_by_hash():
    text = BUILD_REQUIREMENTS.read_text(encoding="utf-8")
    entries = re.findall(r"(?m)^([A-Za-z0-9_.-]+)==(\S+) \\\n    --hash=sha256:([0-9a-f]{64})$",
                         text)
    names = {name.lower().replace("_", "-") for name, _v, _h in entries}
    assert names == {"build", "packaging", "pyproject-hooks", "setuptools"}
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    assert len(lines) == 2 * len(entries)  # nothing unpinned
    version = dict((n.lower(), v) for n, v, _h in entries)["setuptools"]
    pyproject = (RELEASE.parent.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    minimum = re.search(r'requires = \["setuptools>=(\d+)"\]', pyproject).group(1)
    assert int(version.split(".")[0]) >= int(minimum)
