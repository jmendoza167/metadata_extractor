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
