"""Regression: deleted text kept by tracked changes is always shown in
quotes, by show, risk, strip and verify, for DOCX and ODT, so its text is
never mistaken for a count; and every command reports the same number of
deleted characters."""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from helpers.samples import SAMPLES_DIR

DELETED = "twelve hundred"
COUNT = len(DELETED)


def metaex(*args):
    r = subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


def assert_quoted_in_text(output: str) -> None:
    around = re.findall(f"(.){DELETED}(.)", output)
    assert around, output
    assert all(pair == ('"', '"') for pair in around), around


def deleted_values(data) -> list:
    """Every JSON value that holds the deleted text."""
    found = []
    if isinstance(data, dict):
        for value in data.values():
            found += deleted_values(value)
    elif isinstance(data, list):
        for value in data:
            found += deleted_values(value)
    elif isinstance(data, str) and DELETED in data:
        found.append(data)
    return found


@pytest.mark.parametrize("sample", ["docx/kitchen_sink.docx", "odt/kitchen_sink.odt"])
def test_deleted_text_is_quoted_everywhere(sample, tmp_path):
    original = tmp_path / sample.split("/")[1]
    original.write_bytes((SAMPLES_DIR / sample).read_bytes())
    cleaned = tmp_path / original.name.replace(".", "_clean.")

    assert_quoted_in_text(metaex("show", original))
    assert_quoted_in_text(metaex("risk", original))
    assert_quoted_in_text(metaex("strip", original))
    assert_quoted_in_text(metaex("verify", cleaned, "--original", original))

    json_outputs = [json.loads(metaex("show", original, "--json")),
                    json.loads(metaex("risk", original, "--json")),
                    json.loads(metaex("verify", cleaned, "--original", original, "--json"))]
    cleaned.unlink()
    json_outputs.append(json.loads(metaex("strip", original, "--json")))
    for data in json_outputs:
        values = [v for v in deleted_values(data) if "DELETED TEXT" not in v]
        assert values and all(v == f'"{DELETED}"' for v in values), values


@pytest.mark.parametrize("sample", ["docx/kitchen_sink.docx", "odt/kitchen_sink.odt"])
def test_every_command_reports_the_same_count(sample, tmp_path):
    original = tmp_path / sample.split("/")[1]
    original.write_bytes((SAMPLES_DIR / sample).read_bytes())
    cleaned = tmp_path / original.name.replace(".", "_clean.")

    risk = json.loads(metaex("risk", original, "--json"))
    counts = [f["value"] for f in risk["findings"]
              if f["label"] == "Deleted text still in the file (characters)"]
    assert counts == [str(COUNT)]
    strip = metaex("strip", original)
    verify = metaex("verify", cleaned, "--original", original)
    for output in (strip, verify):
        assert f"{COUNT} characters of DELETED TEXT" in output
        assert f"Deleted text still in the file (characters) = {COUNT}" in output
