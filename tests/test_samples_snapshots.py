"""Snapshot tests on the committed sample files in tests/samples/.

For every valid sample (all generated ones outside malformed/, plus any
file in real/), `metaex show --json` and `metaex risk --json` must match the
snapshots stored in tests/samples/expected/, and strip -> verify must be
clean, with the Pillow pixel check for images.

The snapshots leave out what depends on the machine, not on the file: the
path (replaced by the sample's relative path), file system times and
permissions, the Mark of the Web, and the metaex version.

Snapshots are never written automatically. To update them on purpose:

    METAEX_UPDATE_SNAPSHOTS=1 pytest tests/test_samples_snapshots.py

then review the diff of tests/samples/expected/ before committing.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from helpers.pixels import assert_same_image
from helpers.samples import SAMPLES_DIR, make_samples

EXPECTED_DIR = SAMPLES_DIR / "expected"
REAL_DIR = SAMPLES_DIR / "real"
UPDATE = os.environ.get("METAEX_UPDATE_SNAPSHOTS") == "1"
UPDATE_HINT = ("if the change is intended, run METAEX_UPDATE_SNAPSHOTS=1 pytest "
               "tests/test_samples_snapshots.py and review the diff")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp")
FILE_SYSTEM_KEPT = ("path", "size_bytes", "size")
MACHINE_SECTIONS = ("mark_of_the_web",)


def real_samples() -> list[str]:
    """Files added by hand in real/ (none yet): every file but notes and dotfiles."""
    if not REAL_DIR.is_dir():
        return []
    return [p.relative_to(SAMPLES_DIR).as_posix() for p in sorted(REAL_DIR.rglob("*"))
            if p.is_file() and not p.name.startswith(".") and p.suffix.lower() != ".md"]


def valid_samples() -> list[str]:
    generated = [name for name in make_samples.files_on_disk(SAMPLES_DIR)
                 if not name.startswith("malformed/")]
    return sorted(generated) + real_samples()


SAMPLES = valid_samples()


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


def copy_sample(name: str, tmp_path: Path) -> Path:
    """The sample's bytes in a fresh file: no download mark, no read-only flag."""
    copy = tmp_path / "samples" / name
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_bytes((SAMPLES_DIR / name).read_bytes())
    return copy


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def assert_no_local_paths(report, tmp_path: Path) -> None:
    forms = {str(tmp_path), tmp_path.as_posix(), str(SAMPLES_DIR), SAMPLES_DIR.as_posix()}
    for text in strings(report):
        assert not any(form in text for form in forms), text


def normalized_show(report: dict, name: str) -> dict:
    report = dict(report)
    report.pop("metaex_version")
    assert Path(report["subject"]).as_posix().endswith(name)
    report["subject"] = name
    sections = {k: v for k, v in report["sections"].items() if k not in MACHINE_SECTIONS}
    file_system = sections["file_system"]
    fields = {k: v for k, v in file_system["fields"].items() if k in FILE_SYSTEM_KEPT}
    fields["path"] = dict(fields["path"], value=name)
    sections["file_system"] = dict(file_system, fields=fields)
    report["sections"] = sections
    return report


def normalized_risk(report: dict, name: str) -> dict:
    report = dict(report)
    assert Path(report["path"]).as_posix().endswith(name)
    report["path"] = name
    report["findings"] = [f for f in report["findings"]
                          if f["section"] != "Download origin (Mark of the Web)"]
    return report


def snapshot_path(name: str, command: str) -> Path:
    return EXPECTED_DIR / f"{name}.{command}.json"


def check_snapshot(name: str, command: str, report: dict) -> None:
    path = snapshot_path(name, command)
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        path.write_bytes(text.encode("utf-8"))
        return
    if not path.is_file():
        pytest.fail(f"no snapshot {path.relative_to(SAMPLES_DIR).as_posix()}; to create it, "
                    "run METAEX_UPDATE_SNAPSHOTS=1 pytest tests/test_samples_snapshots.py, "
                    "then review and commit it")
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert report == expected, f"{command} --json changed for {name}; {UPDATE_HINT}"


def run_json(command: str, copy: Path) -> dict:
    r = metaex(command, "--json", copy)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# -- the set of samples -------------------------------------------------------------------


GENERATED = [
    "docx/clean.docx", "docx/kitchen_sink.docx", "docx/macro.docm",
    "flac/clean.flac", "flac/kitchen_sink.flac",
    "gif/animated_metadata.gif", "gif/clean.gif",
    "jpeg/clean.jpg", "jpeg/gps_camera.jpg", "jpeg/kitchen_sink.jpg",
    "jpeg/orientation_6.jpg",
    "mp3/clean.mp3", "mp3/kitchen_sink.mp3",
    "odp/clean.odp", "odp/kitchen_sink.odp", "ods/clean.ods", "ods/kitchen_sink.ods",
    "odt/clean.odt", "odt/kitchen_sink.odt",
    "png/apng_metadata.png", "png/clean.png", "png/exif_time.png", "png/text_chunks.png",
    "pptx/clean.pptx", "pptx/kitchen_sink.pptx",
    "svg/clean.svg", "svg/doctype_svg11.svg", "svg/editor.svg", "svg/embedded_jpeg.svg",
    "wav/clean.wav", "wav/kitchen_sink.wav",
    "webp/animated_metadata.webp", "webp/lossless.webp", "webp/lossy_metadata.webp",
    "xlsx/clean.xlsx", "xlsx/kitchen_sink.xlsx",
]


def test_the_valid_samples():
    assert SAMPLES[:len(GENERATED)] == GENERATED
    assert SAMPLES[len(GENERATED):] == real_samples()


def test_real_samples_are_collected(tmp_path, monkeypatch):
    """Files added to real/ later are picked up (notes and dotfiles are not)."""
    real = tmp_path / "real"
    monkeypatch.setattr(sys.modules[__name__], "SAMPLES_DIR", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "REAL_DIR", real)
    assert real_samples() == []
    for name in ("phone/IMG_0001.jpg", "inkscape/plan.svg", "README.md", ".gitkeep"):
        (real / name).parent.mkdir(parents=True, exist_ok=True)
        (real / name).write_bytes(b"x")
    assert real_samples() == ["real/inkscape/plan.svg", "real/phone/IMG_0001.jpg"]


def test_no_stale_snapshots():
    """Every snapshot belongs to a sample; none are deleted automatically."""
    expected = {snapshot_path(n, c) for n in SAMPLES for c in ("show", "risk")}
    found = {p for p in EXPECTED_DIR.rglob("*") if p.is_file()}
    stale = sorted(p.relative_to(SAMPLES_DIR).as_posix() for p in found - expected)
    assert not stale, f"snapshots without a sample (delete them by hand): {stale}"


# -- snapshots ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", SAMPLES)
def test_show_matches_the_snapshot(name, tmp_path):
    report = normalized_show(run_json("show", copy_sample(name, tmp_path)), name)
    assert_no_local_paths(report, tmp_path)
    check_snapshot(name, "show", report)


@pytest.mark.parametrize("name", SAMPLES)
def test_risk_matches_the_snapshot(name, tmp_path):
    report = normalized_risk(run_json("risk", copy_sample(name, tmp_path)), name)
    assert_no_local_paths(report, tmp_path)
    check_snapshot(name, "risk", report)


def test_normalizing_keeps_what_the_file_decides():
    report = {"metaex_version": "9.9", "subject": "/tmp/x/samples/jpeg/a.jpg", "warnings": [],
              "sections": {
                  "file_system": {"title": "File system", "fields": {
                      k: {"label": k, "value": v, "sensitive": False, "note": None}
                      for k, v in (("path", "/tmp/x/samples/jpeg/a.jpg"), ("size_bytes", 5),
                                   ("size", "5 B"), ("modified", "2026-01-01T00:00:00"),
                                   ("accessed", "2026-01-01T00:00:00"),
                                   ("created", "2026-01-01T00:00:00"),
                                   ("permissions", "read-only"))}},
                  "mark_of_the_web": {"title": "Download origin", "fields": {}},
                  "hashes": {"title": "Hashes", "fields": {}}}}
    out = normalized_show(report, "jpeg/a.jpg")
    assert "metaex_version" not in out and out["subject"] == "jpeg/a.jpg"
    assert list(out["sections"]) == ["file_system", "hashes"]
    assert list(out["sections"]["file_system"]["fields"]) == ["path", "size_bytes", "size"]
    assert out["sections"]["file_system"]["fields"]["path"]["value"] == "jpeg/a.jpg"
    assert report["sections"]["file_system"]["fields"]["path"]["value"].startswith("/tmp")


# -- strip -> verify ----------------------------------------------------------------------


@pytest.mark.parametrize("paranoid", [False, True], ids=["default", "paranoid"])
@pytest.mark.parametrize("name", SAMPLES)
def test_strip_then_verify_is_clean(name, paranoid, tmp_path):
    copy = copy_sample(name, tmp_path)
    out = tmp_path / ("stripped" + copy.suffix)
    r = metaex("strip", copy, "-o", out, *(["--paranoid"] if paranoid else []))
    assert r.returncode == 0, r.stderr
    v = metaex("verify", out, "--original", copy, "--json")
    assert v.returncode == 0, v.stdout + v.stderr
    result = json.loads(v.stdout)
    assert result["clean"] is True and result["leftovers"] == [] and result["problems"] == []
    assert result["image_data_match"] is True
    assert copy.read_bytes() == (SAMPLES_DIR / name).read_bytes()  # the original is untouched
    if copy.suffix.lower() in IMAGE_SUFFIXES:
        # a JPEG's MPF extra images are removed by design: compare the main image
        assert_same_image(copy, out, primary_only=copy.suffix.lower() in (".jpg", ".jpeg"))


# -- SAMPLES.md ---------------------------------------------------------------------------

_ROW = re.compile(r"^\| `([^`]+)` \|.*\| (\d+) / (\d+) / (\d+) / (\d+) \|$", re.M)


def test_samples_md_lists_every_sample_with_its_risk_summary():
    text = (SAMPLES_DIR / "SAMPLES.md").read_text(encoding="utf-8")
    rows = {m.group(1): tuple(map(int, m.groups()[1:])) for m in _ROW.finditer(text)}
    generated = [n for n in SAMPLES if not n.startswith("real/")]
    assert sorted(rows) == generated
    for name, counts in rows.items():
        summary = json.loads(snapshot_path(name, "risk").read_text(encoding="utf-8"))["summary"]
        assert counts == (summary["critical"], summary["high"], summary["medium"],
                          summary["low"]), name
    for name in make_samples.files_on_disk(SAMPLES_DIR):
        if name.startswith("malformed/"):
            assert f"| `{name}` |" in text, name
