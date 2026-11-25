"""Unsupported formats: one shared message for every command.

strip and verify refuse (exit 1, nothing written); show and risk still
report file system information (exit 0) with the same message as a warning.
"""

import os
import subprocess
import sys

import pytest

from helpers.ole2 import encrypted_office, legacy_word
from metaex.core import filetype as ft
from metaex.core.registry import unsupported_message


def run(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def flat(text):
    return " ".join(text.split())


CASES = [
    ("notes.txt", b"just some text\n", "detected type: unknown (not recognized)"),
    ("paper.pdf", b"%PDF-1.7\n", "detected type: PDF"),
    ("old.doc", legacy_word(), "save it as .docx"),
    ("secret.docx", encrypted_office(), "password-protected"),
]


def test_message_names_the_type_and_the_formats_command():
    text = unsupported_message(ft.PDF)
    assert text == ("detected type: PDF. metaex doesn't support this format yet. "
                    'Run "metaex formats" to see the supported formats')


def test_message_for_unknown_data():
    assert unsupported_message(ft.UNKNOWN).startswith(
        "detected type: unknown (not recognized). metaex doesn't support this format yet.")


def test_message_includes_the_specific_reason():
    text = unsupported_message(ft.RAR)
    assert text.startswith("detected type: RAR. metaex doesn't support this format yet: ")
    assert ft.refusal(ft.RAR) in text
    assert text.endswith('Run "metaex formats" to see the supported formats')


@pytest.mark.parametrize("name, data, expected", CASES)
def test_strip_refuses_with_the_shared_message(tmp_path, name, data, expected):
    path = tmp_path / name
    path.write_bytes(data)
    r = run("strip", path)
    assert r.returncode == 1
    message = flat(r.stderr)
    assert expected in message
    assert "metaex doesn't support this format yet" in message
    assert 'Run "metaex formats" to see the supported formats' in message
    assert "no file written" in message
    assert os.listdir(tmp_path) == [name]


@pytest.mark.parametrize("name, data, expected", CASES)
def test_verify_refuses_with_the_shared_message(tmp_path, name, data, expected):
    path = tmp_path / name
    path.write_bytes(data)
    r = run("verify", path)
    assert r.returncode == 1
    message = flat(r.stderr)
    assert expected in message
    assert "metaex doesn't support this format yet" in message
    assert '"metaex formats"' in message
    assert os.listdir(tmp_path) == [name]


@pytest.mark.parametrize("command", ["show", "risk"])
@pytest.mark.parametrize("name, data, expected", CASES)
def test_show_and_risk_report_with_the_shared_message(tmp_path, command, name, data, expected):
    path = tmp_path / name
    path.write_bytes(data)
    r = run(command, path)
    assert r.returncode == 0
    output = flat(r.stdout)
    assert expected in output
    assert "metaex doesn't support this format yet" in output
    assert '"metaex formats"' in output
    assert os.listdir(tmp_path) == [name]


def test_show_json_warning_is_the_shared_message(tmp_path):
    import json

    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.7\n")
    data = json.loads(run("show", path, "--json").stdout)
    assert any(w.startswith(unsupported_message(ft.PDF)) for w in data["warnings"])
