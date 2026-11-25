"""metaex formats: supported formats, what strip removes, refused formats."""

import json
import subprocess
import sys

from metaex.commands.formats import SUPPORTED, all_types, formats_to_dict, refused_types
from metaex.core import filetype as ft
from metaex.core.registry import STRIPPABLE, can_strip, format_module


def run(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *args], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)


def test_every_strippable_format_is_listed_once():
    keys = [t.key for _, t, _ in SUPPORTED]
    assert sorted(keys) == sorted(STRIPPABLE)
    assert len(keys) == len(set(keys))


def test_listed_formats_really_strip():
    for _, t, removes in SUPPORTED:
        assert can_strip(format_module(t)), t.key
        assert removes and "\n" not in removes


def test_refused_types_are_everything_else_except_unknown():
    refused = {t.key for t, _ in refused_types()}
    assert not refused & STRIPPABLE
    assert ft.UNKNOWN.key not in refused
    assert refused | STRIPPABLE | {ft.UNKNOWN.key} == {t.key for t in all_types()}
    for t, _ in refused_types():
        assert not can_strip(format_module(t)), t.key


def test_refusal_reasons_come_from_filetype():
    reasons = dict((t.key, reason) for t, reason in refused_types())
    assert reasons["rar"] == ft.refusal(ft.RAR)
    assert reasons["pdf"] == "not supported yet"


def test_text_output():
    r = run("formats")
    assert r.returncode == 0 and r.stderr == ""
    out = r.stdout
    assert out.startswith("Supported formats (show, risk, strip and verify):")
    for _, t, _ in SUPPORTED:
        assert f"  {t.name}  {' '.join(t.extensions)}\n" in out
    assert out.count("strip removes: ") == len(SUPPORTED)
    flat = " ".join(out.split())
    assert "Refused (strip and verify refuse them" in flat
    assert "PDF (.pdf): not supported yet" in flat
    assert "7-Zip (.7z): 7-Zip archives are not supported" in flat
    assert "TAR archives with sparse files" in flat
    assert out.rstrip().endswith("Files of any other type are refused the same way.")


def test_json_output():
    r = run("formats", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data == formats_to_dict()
    assert set(data) == {"supported", "refused", "refused_variants"}
    jpeg = data["supported"][0]
    assert jpeg == {"key": "jpeg", "name": "JPEG", "category": "Images",
                    "extensions": [".jpg", ".jpeg", ".jpe", ".jfif"], "mime": "image/jpeg",
                    "strip_removes": jpeg["strip_removes"]}
    assert "GPS" in jpeg["strip_removes"]
    assert {r["key"] for r in data["refused"]} >= {"pdf", "rar", "7z", "doc", "heic"}
    for entry in data["refused"]:
        assert set(entry) == {"key", "name", "extensions", "reason"} and entry["reason"]


def test_formats_is_listed_in_help():
    r = run()
    assert "formats" in r.stdout
