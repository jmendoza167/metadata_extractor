"""metaex show and risk on DOCX, XLSX and PPTX files."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from helpers import jpeg
from helpers.ooxml import (TRACKED_BODY, Package, add_excel_comments, add_hidden_slide_and_notes,
                           add_links_and_template, add_media, add_ppt_comments,
                           add_word_comments, docx, kitchen_sink_xlsx_workbook, pptx,
                           with_properties, xlsx)
from helpers.zip import make_zip
from metaex.formats import docx as docx_format


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


def word_file() -> bytes:
    pkg = with_properties(add_word_comments(add_links_and_template(docx(TRACKED_BODY))))
    add_media(pkg, "word/document.xml", "word/media/image1.jpeg", jpeg.sample_photo(), "jpeg",
              "image/jpeg")
    return pkg.build()


def show_json(path):
    r = metaex("show", "--json", path)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("name, data, sections", [
    ("letter.docx", word_file(),
     ["office_core", "office_app", "office_comments", "office_people", "tracked_changes",
      "office_external", "local_paths", "embedded_images"]),
    ("budget.xlsx", with_properties(add_excel_comments(kitchen_sink_xlsx_workbook(xlsx()))).build(),
     ["office_core", "office_app", "office_comments", "office_people", "hidden_content",
      "xlsx_workbook", "local_paths"]),
    ("talk.pptx", with_properties(add_ppt_comments(add_hidden_slide_and_notes(pptx()))).build(),
     ["office_core", "office_app", "office_comments", "office_people", "hidden_content"]),
])
def test_show_lists_office_metadata(name, data, sections, tmp_path):
    path = tmp_path / name
    path.write_bytes(data)
    report = show_json(path)
    found = list(report["sections"])
    for key in sections:
        assert key in found, key
    support = report["sections"]["file_type"]["fields"]["support"]["value"]
    embedded = report["sections"]["file_type"]["fields"]["embedded_metadata"]["value"]
    assert embedded == "examined"
    assert support in ("viewing supported; stripping not implemented yet", "supported")
    assert report["sections"]["office_core"]["fields"]["creator"]["value"] == "Jane Doe"
    text = metaex("show", path)
    assert text.returncode == 0 and "Jane Doe" in text.stdout


def test_show_warns_about_deleted_text_and_user_paths(tmp_path):
    path = tmp_path / "letter.docx"
    path.write_bytes(word_file())
    warnings = show_json(path)["warnings"]
    assert warnings[0].startswith("CRITICAL")
    assert any("14 characters of deleted text" in w for w in warnings)
    assert any("reveal a user name" in w for w in warnings)


def test_risk_on_a_word_document(tmp_path):
    path = tmp_path / "letter.docx"
    path.write_bytes(word_file())
    r = metaex("risk", "--json", path)
    assert r.returncode == 0, r.stderr
    risk = json.loads(r.stdout)
    critical = {(f["section"], f["label"]) for f in risk["findings"] if f["level"] == "critical"}
    assert ("Document properties", "Author") in critical
    assert ("Tracked changes", "Deleted text") in critical
    assert ("word/media/image1.jpeg (JPEG): GPS", "Coordinates") in critical


def test_structure_lists_the_parts(tmp_path):
    path = tmp_path / "letter.docx"
    path.write_bytes(docx().build())
    report = show_json(path) if False else json.loads(metaex("show", "--json", "--structure",
                                                              path).stdout)
    parts = report["sections"]["structure"]["fields"]
    assert parts["word/document.xml"]["value"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml")


def test_clean_document_has_no_metadata(tmp_path):
    path = tmp_path / "clean.docx"
    path.write_bytes(docx().build())
    report = show_json(path)
    assert [k for k in report["sections"]] == ["file_type", "file_system", "hashes"]
    assert report["warnings"] == []


def test_strict_open_xml_is_reported_not_examined(tmp_path):
    pkg = Package()
    pkg.add("word/document.xml", '<w:document xmlns:w="http://purl.oclc.org/ooxml/'
            'wordprocessingml/main"/>', "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document.main+xml")
    pkg.link("", "officeDocument", "word/document.xml")
    path = tmp_path / "strict.docx"
    path.write_bytes(pkg.build())
    report = show_json(path)
    assert any("Strict Open XML" in w for w in report["warnings"])
    assert report["sections"]["file_type"]["fields"]["embedded_metadata"]["value"] == \
        "not examined (this variant is not supported)"


def test_damaged_document_is_reported(tmp_path):
    files = docx().files()
    del files["[Content_Types].xml"]
    path = tmp_path / "broken.docx"
    path.write_bytes(make_zip({"[Content_Types].xml": "<Types/>", **files}))
    report = show_json(path)
    assert any("structure could not be read" in w for w in report["warnings"])
    risk = metaex("risk", path)
    assert risk.returncode == 0


def test_a_damaged_part_does_not_hide_the_rest(tmp_path):
    pkg = with_properties(docx())
    pkg.add_linked("word/document.xml", "comments", "word/comments.xml", "<w:comments>",
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml")
    path = tmp_path / "partly.docx"
    path.write_bytes(pkg.build())
    report = show_json(path)
    assert report["sections"]["office_core"]["fields"]["creator"]["value"] == "Jane Doe"
    assert report["sections"]["file_type"]["fields"]["embedded_metadata"]["value"] == \
        "partly examined (file is malformed)"
    assert any("word/comments.xml" in w for w in report["warnings"])


def test_doctype_in_an_unparsed_part_is_reported(tmp_path):
    pkg = docx()
    pkg.add_linked("word/document.xml", "styles", "word/styles.xml",
                   '<!DOCTYPE x [<!ENTITY a "b">]><w:styles/>')
    path = tmp_path / "dtd.docx"
    path.write_bytes(pkg.build())
    report = show_json(path)
    assert any("word/styles.xml contains a DOCTYPE" in w for w in report["warnings"])


def test_main_document_of_another_kind():
    with pytest.raises(Exception, match="looks like a Word document"):
        docx_format.parse(xlsx().build())


def test_macro_enabled_documents_are_named(tmp_path):
    from helpers.ooxml import MS_REL, VBA, WORD_MACRO

    pkg = docx(main_type=WORD_MACRO)
    pkg.add_linked("word/document.xml", MS_REL + "vbaProject", "word/vbaProject.bin", b"VBA",
                   VBA)
    path = tmp_path / "macro.docm"
    path.write_bytes(pkg.build())
    report = show_json(path)
    assert any("VBA macros" in w for w in report["warnings"])
    assert any("Word macro-enabled document (.docm)" in w for w in report["warnings"])
    assert "macros" in report["sections"]
