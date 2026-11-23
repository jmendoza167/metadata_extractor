"""OpenDocument end to end: show -> strip -> verify -> show on the command
line, the refusals, and random damage (never a crash)."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys

import pytest

from helpers.odf import Document, kitchen_sink
from helpers.zip import Entry, build_zip
from metaex.core.errors import MetaexError
from metaex.core.report import Report, render_json, render_text
from metaex.core.verify import verify
from metaex.core.ziparchive import open_archive
from metaex.formats import odp as odp_format
from metaex.formats import ods as ods_format
from metaex.formats import odt as odt_format

MODULES = {"odt": odt_format, "ods": ods_format, "odp": odp_format}
NOT_METADATA = {"file_type", "file_system", "hashes"}
KEPT = {"office_comments", "tracked_changes", "hidden_content", "office_external", "macros",
        "embedded_images", "office_embedded", "odf_settings"}


def metaex(*args, stdin=None):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", timeout=120,
                          input=stdin)


def show(path) -> dict:
    r = metaex("show", path, "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("kind", sorted(MODULES))
@pytest.mark.parametrize("options", [[], ["--paranoid"], ["--remove-comments"]])
def test_show_strip_verify_show(kind, options, tmp_path):
    original = tmp_path / f"notes.{kind}"
    original.write_bytes(kitchen_sink(kind))
    before = original.read_bytes()
    assert show(original)["sections"]["odf_meta"]["fields"]["initial-creator"]["value"] == \
        "Jane Doe"
    r = metaex("strip", original, *options)
    assert r.returncode == 0, r.stderr
    assert "Verification: clean" in r.stdout
    assert original.read_bytes() == before
    cleaned = tmp_path / f"notes_clean.{kind}"
    v = metaex("verify", cleaned, "--original", original, "--json")
    assert v.returncode == 0, v.stdout
    assert json.loads(v.stdout)["image_data_match"] is True
    after = show(cleaned)
    assert set(after["sections"]) - NOT_METADATA <= KEPT
    text = json.dumps({k: v for k, v in after["sections"].items() if k != "hidden_content"})
    for secret in ("Jane Doe", "jane.doe", "John Example", "HP LaserJet", "PT42M", "GPS"):
        assert secret not in text, secret


@pytest.mark.parametrize("kind", sorted(MODULES))
def test_in_place(kind, tmp_path):
    path = tmp_path / f"notes.{kind}"
    path.write_bytes(kitchen_sink(kind))
    r = metaex("strip", path, "--in-place", "--yes")
    assert r.returncode == 0, r.stderr
    assert os.listdir(tmp_path) == [path.name]
    assert metaex("verify", path).returncode == 0


def bad_files() -> dict[str, tuple[bytes, str]]:
    kitchen = kitchen_sink("odt")
    entries = Document("odt").entries()
    encrypted = list(entries)
    encrypted[-1] = Entry("META-INF/manifest.xml", entries[-1].data.replace(
        b'manifest:full-path="content.xml" manifest:media-type="text/xml"/>',
        b'manifest:full-path="content.xml" manifest:media-type="text/xml">'
        b'<manifest:encryption-data manifest:checksum="x"/></manifest:file-entry>'))
    doctype = list(entries)
    doctype[1] = Entry("content.xml", b'<!DOCTYPE x [<!ENTITY a "b">]>' + entries[1].data[39:])
    linked = Document("odt", '<text:p><text:a xlink:href="file:///home/jane.doe/a.odt">a'
                             "</text:a></text:p>").build()
    return {
        "truncated.odt": (kitchen[: len(kitchen) // 2], "the file may be truncated"),
        "encrypted.odt": (build_zip(encrypted), "password-protected"),
        "doctype.odt": (build_zip(doctype), "DOCTYPE"),
        "linked.odt": (linked, "user's folder"),
    }


@pytest.mark.parametrize("name", sorted(bad_files()))
def test_refusals_write_nothing(name, tmp_path):
    data, message = bad_files()[name]
    path = tmp_path / name
    path.write_bytes(data)
    for args in ([path], [path, "--remove-comments"], [path, "--rename"]):
        r = metaex("strip", *args)
        assert r.returncode == 1, (args, r.stdout)
        assert message in r.stderr and "no file written" in r.stderr
        assert "internal error" not in r.stderr
        assert os.listdir(tmp_path) == [name]
    r = metaex("strip", path, "--in-place", "--yes")
    assert r.returncode == 1 and path.read_bytes() == data
    assert metaex("show", path).returncode == 0 and metaex("risk", path).returncode == 0


# -- random damage ---------------------------------------------------------------------------


def mutate_bytes(data: bytes, rng: random.Random) -> bytes:
    d = bytearray(data)
    for _ in range(rng.randint(1, 6)):
        d[rng.randrange(len(d))] = rng.randrange(256)
    if rng.random() < 0.2:
        del d[rng.randrange(len(d)):]
    return bytes(d)


def mutate_file(data: bytes, rng: random.Random) -> bytes:
    archive = open_archive(data)
    files = {n: archive.read(n) for n in archive.names()}
    name = rng.choice(sorted(files))
    part = bytearray(files[name])
    if rng.random() < 0.2:
        files.pop(name)
    elif part:
        pos = rng.randrange(len(part))
        if rng.random() < 0.5:
            part[pos:pos] = rng.choice([b"<x/>", b"</text:p>", b"&bogus;", b'xlink:href="/x"',
                                        b"<office:annotation/>", b"\xff"])
        else:
            del part[pos:pos + rng.randint(1, 30)]
        files[name] = bytes(part)
    return build_zip([Entry(n, d) for n, d in files.items()])


@pytest.mark.parametrize("kind", sorted(MODULES))
def test_random_damage_never_crashes(kind):
    module = MODULES[kind]
    original = kitchen_sink(kind)
    rng = random.Random(f"odf-{kind}")
    outcomes = {"refused": 0, "parsed": 0, "verified": 0}
    for n in range(400):
        data = (mutate_bytes if n % 2 else mutate_file)(original, rng)
        try:
            parsed = module.parse(data)
        except MetaexError:
            outcomes["refused"] += 1
            continue
        report = Report("x", parsed["sections"] + [parsed["structure"]], parsed["warnings"])
        render_text(report)
        render_json(report)
        try:
            cleaned = module.strip(data)
            verify(cleaned, data, filename=f"x.{kind}")
        except MetaexError:
            outcomes["parsed"] += 1
            continue
        outcomes["verified"] += 1
    assert outcomes["refused"] > 50 and outcomes["verified"] > 20


def test_strict_open_xml_refusal_says_nothing_was_written(tmp_path):
    """Regression: refusals of unsupported variants (Strict Open XML,
    encrypted OpenDocument) end like every other refusal."""
    from helpers.ooxml import Package

    pkg = Package()
    pkg.add("word/document.xml", '<w:document xmlns:w="http://purl.oclc.org/ooxml/'
            'wordprocessingml/main"/>', "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document.main+xml")
    pkg.link("", "officeDocument", "word/document.xml")
    path = tmp_path / "strict.docx"
    path.write_bytes(pkg.build())
    r = metaex("strip", path)
    assert r.returncode == 1
    assert "Strict Open XML" in r.stderr and "no file written" in r.stderr
    assert os.listdir(tmp_path) == ["strict.docx"]
