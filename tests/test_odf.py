"""Reading OpenDocument files (ODT, ODS, ODP): core/odf.py and the formats."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from helpers import jpeg
from helpers.odf import (ANNOTATION, DECLS, Document, content_xml, kitchen_sink, meta_xml,
                         with_meta)
from helpers.zip import Entry, build_zip
from metaex.core import odf
from metaex.core.errors import MalformedFileError, UnsupportedFormatError
from metaex.core.risk import CRITICAL, HIGH, MEDIUM, classify
from metaex.formats import odp as odp_format
from metaex.formats import ods as ods_format
from metaex.formats import odt as odt_format

MODULES = {"odt": odt_format, "ods": ods_format, "odp": odp_format}


def sections(kind: str, data: bytes) -> dict[str, dict]:
    parsed = MODULES[kind].parse(data)
    return {s.key: {f.key: f.value for f in s.fields} for s in parsed["sections"]}


@pytest.mark.parametrize("kind", sorted(MODULES))
def test_meta_xml(kind):
    found = sections(kind, kitchen_sink(kind))
    meta = found["odf_meta"]
    assert meta["initial-creator"] == "Jane Doe" and meta["creator"] == "John Example"
    assert meta["printed-by"] == "Jane Doe" and meta["editing-cycles"] == "7"
    assert meta["editing-duration"] == "PT42M10S"
    assert meta["generator"].startswith("LibreOffice/7.6")
    assert meta["template-title"] == "Letter" and meta["document-statistic.page-count"] == "1"
    assert found["odf_user_defined"] == {"Client": "Jane Doe"}
    assert found["local_paths"]["template"] == "file:///home/jane.doe/Templates/Letter.ott"


@pytest.mark.parametrize("kind", sorted(MODULES))
def test_settings_comments_and_files(kind):
    found = sections(kind, kitchen_sink(kind))
    assert found["printer_settings"]["PrinterName"] == "HP LaserJet Example"
    assert "HP LaserJet Example" in found["printer_settings"]["PrinterSetup"]
    assert found["revision_ids"]["Rsid"] == "1234567"
    assert found["office_comments"] == {"count": 1, "authors": ["Jane Doe"],
                                        "initials": ["JD"],
                                        "dates": "2024-05-01T10:00:00.000000000"}
    assert list(found["office_thumbnail"]) == ["Thumbnails/thumbnail.png"]
    assert list(found["macros"]) == ["Basic/Standard/Module1.xml"]
    assert list(found["odf_configurations"]) == ["Configurations2/accelerator/current.xml"]
    images = found["embedded_images"]
    assert list(images) == ["Pictures/10000000000000300000002096C8C1A2.jpg"]
    assert "gps" in found


def test_tracked_changes_in_a_text_document():
    found = sections("odt", kitchen_sink("odt"))
    assert found["tracked_changes"] == {
        "insertions": 1, "deletions": 1, "authors": ["Jane Doe", "John Example"],
        "dates": "2024-05-01T10:15:00 to 2024-05-02T09:00:00 (2 dates)",
        "deleted_characters": 4, "deleted_text": "1200"}
    assert found["revision_ids"]["rsid_attributes"] == 3
    assert found["office_external"] == {"content.xml:1": "https://example.com/flat"}


def test_hidden_content():
    assert sections("odt", kitchen_sink("odt"))["hidden_content"] == {
        "hidden_text.content.xml.0": "Secret note to self"}
    assert sections("ods", kitchen_sink("ods"))["hidden_content"] == {"sheet.Salaries":
                                                                       "Salaries"}
    assert sections("odp", kitchen_sink("odp"))["hidden_content"] == {
        "hidden_slide.page2": "page2", "notes": 1,
        "notes_text": "Mention the deadline: 2024-06-01"}


def test_comments_in_older_presentations():
    body = ('<draw:page draw:name="p1">' + ANNOTATION.replace("office:annotation",
                                                             "officeooo:annotation")
            + "</draw:page>")
    found = sections("odp", Document("odp", body).build())
    assert found["office_comments"]["authors"] == ["Jane Doe"]


def test_embedded_object_with_its_own_meta():
    doc = with_meta(Document("odt"))
    chart = "application/vnd.oasis.opendocument.chart"
    doc.add("Object 1/content.xml", content_xml("odt"), "text/xml")
    doc.add("Object 1/meta.xml", meta_xml("<meta:initial-creator>Jane Doe"
                                         "</meta:initial-creator>"), "text/xml")
    doc.files["Object 1/"] = (b"", chart)
    entries = [e for e in doc.entries() if e.name != "Object 1/"]
    found = sections("odt", build_zip(entries))
    assert found["office_embedded"] == {"Object 1/": chart}
    assert found["odf_meta"]["Object 1:initial-creator"] == "Jane Doe"
    assert classify("odf_meta", "Object 1:initial-creator") == CRITICAL


def test_unlisted_files_are_hidden_data():
    doc = Document("odt")
    doc.unlisted["secret.txt"] = b"not in the manifest"
    found = sections("odt", doc.build())
    assert list(found["office_hidden_parts"]) == ["secret.txt"]


def test_clean_documents_have_nothing():
    for kind, module in MODULES.items():
        parsed = module.parse(Document(kind).build())
        assert parsed["sections"] == [] and parsed["warnings"] == [] and parsed["complete"]


def test_risk_levels():
    assert classify("odf_meta", "printed-by") == CRITICAL
    assert classify("odf_meta", "template-date") == HIGH
    assert classify("hidden_content", "hidden_text.content.xml.0") == HIGH
    assert classify("printer_settings", "PrinterSetup") == MEDIUM


# -- refusals ---------------------------------------------------------------------------------


def test_encrypted_documents_are_refused():
    doc = Document("odt")
    data = doc.build()
    manifest = (b'<manifest:file-entry manifest:full-path="content.xml" '
                b'manifest:media-type="text/xml"><manifest:encryption-data '
                b'manifest:checksum-type="SHA1/1K" manifest:checksum="x"/></manifest:file-entry>')
    entries = doc.entries()
    entries[-1].data = entries[-1].data.replace(
        b'<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>',
        manifest)
    with pytest.raises(UnsupportedFormatError, match="password-protected"):
        odt_format.parse(build_zip(entries))
    assert data


def test_doctype_is_reported_when_reading():
    entries = Document("odt").entries()
    entries[1].data = b'<!DOCTYPE x [<!ENTITY a "b">]>' + entries[1].data[39:]
    parsed = odf.parse_package(build_zip(entries), "odt")
    assert not parsed["complete"]
    assert any("content.xml contains a DOCTYPE" in w for w in parsed["warnings"])


@pytest.mark.parametrize("change, message", [
    ("no mimetype", "no mimetype entry"),
    ("no manifest", "no META-INF/manifest.xml"),
    ("unknown type", "is not supported"),
])
def test_damaged_documents(change, message):
    entries = Document("odt").entries()
    if change == "no mimetype":
        entries = entries[1:]
    elif change == "no manifest":
        entries = entries[:-1]
    else:
        entries[0].data = b"application/vnd.oasis.opendocument.graphics"
    with pytest.raises((MalformedFileError, UnsupportedFormatError), match=message):
        odf.parse_package(build_zip(entries), "odt")


def test_kind_mismatch():
    with pytest.raises(MalformedFileError, match="looks like a text document"):
        odt_format.parse(Document("ods").build())


# -- the command line -------------------------------------------------------------------------


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


@pytest.mark.parametrize("kind", sorted(MODULES))
def test_show_and_risk(kind, tmp_path):
    path = tmp_path / f"notes.{kind}"
    path.write_bytes(kitchen_sink(kind))
    report = json.loads(metaex("show", "--json", path).stdout)
    assert report["sections"]["file_type"]["fields"]["embedded_metadata"]["value"] == "examined"
    risk = json.loads(metaex("risk", "--json", path).stdout)
    critical = {(f["section"], f["label"]) for f in risk["findings"] if f["level"] == "critical"}
    assert ("Document properties", "Author (initial creator)") in critical
    assert ("Local file paths", "Template") in critical


def test_jpeg_inside_is_listed(tmp_path):
    doc = Document("odt").add("Pictures/a.jpg", jpeg.sample_photo(), "image/jpeg")
    found = sections("odt", doc.build())
    assert found["embedded_images"]["Pictures/a.jpg"].startswith("JPEG")
    assert DECLS
