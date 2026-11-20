"""Smoke tests for the metaex command-line interface.

The CLI is run as ``sys.executable -m metaex`` so the tests use the same
interpreter as pytest and work on Windows, Linux and macOS.
"""

import subprocess
import sys

import pytest

from metaex import __version__


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "metaex", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_version_flag_prints_name_and_version():
    result = run_cli("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == f"metaex {__version__}"
    assert result.stdout.strip() == "metaex 0.0.1"


def test_no_arguments_prints_help():
    result = run_cli()
    assert result.returncode == 0
    assert result.stdout.startswith("usage: metaex")
    for command in ("show", "risk", "strip", "verify"):
        assert command in result.stdout


@pytest.mark.parametrize("command", ["show", "strip", "verify", "risk"])
def test_subcommands_without_path_are_usage_errors(command):
    result = run_cli(command)
    assert result.returncode == 2
    assert "path" in result.stderr


def test_unknown_subcommand_is_a_usage_error():
    result = run_cli("no-such-command")
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def test_show_without_path_is_a_usage_error():
    result = run_cli("show")
    assert result.returncode == 2
    assert "path" in result.stderr
