"""External links, embedded content and hidden content in DOCX, XLSX and PPTX."""

from __future__ import annotations

import pytest

from helpers import jpeg, png
from helpers.ooxml import (GLOSSARY_TYPE, HIDDEN_TEXT_BODY, PRINTER_TYPE, RSIDS, SERVER_PATH,
                           SETTINGS_TYPE, TEMPLATE_PATH, TRACKED_BODY, VBA, WORD_MACRO, MS_REL,
                           add_embedded_workbook, add_external_data, add_glossary,
                           add_hidden_slide_and_notes, add_links_and_template, add_media,
                           add_thumbnail, devmode, docx, kitchen_sink_xlsx_workbook, pptx,
                           settings_xml, xlsx)
from metaex.core import ooxml
from metaex.core.ooxml import open_package
from metaex.core.paths import server_name, user_name
from metaex.formats import docx as docx_format
from metaex.formats import pptx as pptx_format
from metaex.formats import xlsx as xlsx_format


def fields(section):
    return {f.key: f.value for f in section.fields}


def by_key(sections):
    return {s.key: s for s in sections}


# -- paths ----------------------------------------------------------------------------------

SEP = "\\"


@pytest.mark.parametrize("path, user, server", [
    (f"C:{SEP}Users{SEP}jane.doe{SEP}a.docx", "jane.doe", None),
    (f"file:///C:{SEP}Users{SEP}jane.doe{SEP}a.dotm", "jane.doe", None),
    (f"{SEP * 2}fileserver01{SEP}share{SEP}a.xlsx", None, "fileserver01"),
    (f"file:///{SEP * 2}fileserver01{SEP}share{SEP}a.xlsx", None, "fileserver01"),
    ("file://fileserver01/share/a.xlsx", None, "fileserver01"),
    ("/home/jane.doe/a.odt", "jane.doe", None),
    ("https://example.com/a", None, None),
])
def test_user_and_server_names(path, user, server):
    assert user_name(path) == user
    assert server_name(path) == server


# -- external links ---------------------------------------------------------------------------


def test_external_links_and_user_paths():
    sections = by_key(ooxml.external_link_sections(
        open_package(add_links_and_template(docx()).build())))
    links = fields(sections["office_external"])
    assert links["word/document.xml:rId1"] == "https://example.com/flat"
    assert links["word/document.xml:rId2"] == SERVER_PATH
    server = sections["office_external"].get("word/document.xml:rId2")
    assert server.note == "reveals the server 'fileserver01'"
    assert server.label == "Linked object (word/document.xml)"
    template = sections["local_paths"].get("word/settings.xml:rId1")
    assert template.value == TEMPLATE_PATH and template.label == \
        "Attached template (word/settings.xml)"
    assert template.note == "reveals the user name 'jane.doe'"


def test_no_external_links():
    assert ooxml.external_link_sections(open_package(docx().build())) == []


# -- embedded images and objects ----------------------------------------------------------------


def test_embedded_images_are_parsed_with_their_own_format():
    pkg = docx()
    add_media(pkg, "word/document.xml", "word/media/image1.jpeg", jpeg.sample_photo(), "jpeg",
              "image/jpeg")
    add_media(pkg, "word/document.xml", "word/media/image2.png", png.make_png(), "png",
              "image/png")
    add_media(pkg, "word/document.xml", "word/media/image3.emf", b"\x01\x00\x00\x00EMF",
              "emf", "image/x-emf")
    warnings = []
    sections = ooxml.embedded_image_sections(open_package(pkg.build()), warnings)
    listing = fields(sections[0])
    assert listing["word/media/image1.jpeg"].startswith("JPEG, ")
    assert listing["word/media/image2.png"].startswith("PNG, ")
    assert "not examined: metaex can't read this type" in listing["word/media/image3.emf"]
    gps = [s for s in sections if s.key == "gps"]
    assert gps and gps[0].title == "word/media/image1.jpeg (JPEG): GPS"


def test_malformed_embedded_image_is_reported():
    pkg = docx()
    add_media(pkg, "word/document.xml", "word/media/image1.png",
              png.make_png()[:40], "png", "image/png")
    warnings = []
    sections = ooxml.embedded_image_sections(open_package(pkg.build()), warnings)
    assert "malformed" in fields(sections[0])["word/media/image1.png"]
    assert warnings


def test_embedded_objects_with_the_authors_of_embedded_office_files():
    pkg = add_embedded_workbook(docx(), "word/document.xml", "word")
    found = fields(ooxml.embedded_object_section(open_package(pkg.build())))
    xlsx_part = "word/embeddings/Microsoft_Excel_Worksheet.xlsx"
    assert found[f"{xlsx_part}:creator"] == "Jane Doe"
    assert found[f"{xlsx_part}:lastModifiedBy"] == "John Example"
    assert "oleObject" in found["word/embeddings/oleObject1.bin"]


def test_thumbnail_printer_settings_macros_and_unused_parts():
    pkg = docx(main_type=WORD_MACRO)
    add_thumbnail(pkg, jpeg.sample_photo())
    pkg.add_linked("word/document.xml", "printerSettings", "word/printerSettings/printerSettings1.bin",
                   devmode(), PRINTER_TYPE.replace("spreadsheetml", "wordprocessingml"))
    pkg.add_linked("word/document.xml", MS_REL + "vbaProject", "word/vbaProject.bin", b"VBA", VBA)
    pkg.add("word/stray.xml", "<x>left over</x>")
    opened = open_package(pkg.build())
    assert list(fields(ooxml.thumbnail_section(opened))) == ["docProps/thumbnail.jpeg"]
    assert "printer 'HP LaserJet Example'" in fields(ooxml.printer_section(opened))[
        "word/printerSettings/printerSettings1.bin"]
    assert list(fields(ooxml.macros_section(opened))) == ["word/vbaProject.bin"]
    assert list(fields(ooxml.unused_parts_section(opened))) == ["word/stray.xml"]
    # the thumbnail is not listed as an embedded image
    assert ooxml.media_parts(opened) == []


def test_printer_name():
    assert ooxml.printer_name(devmode("Office printer")) == "Office printer"
    assert ooxml.printer_name(b"\x00" * 100) is None
    assert ooxml.printer_name(b"short") is None


def test_nothing_embedded():
    pkg = open_package(docx().build())
    for function in (ooxml.thumbnail_section, ooxml.printer_section, ooxml.macros_section,
                     ooxml.unused_parts_section, ooxml.embedded_object_section):
        assert function(pkg) is None
    assert ooxml.embedded_image_sections(pkg, []) == []


# -- DOCX -------------------------------------------------------------------------------------


def test_hidden_text_and_glossary():
    pkg = add_glossary(docx(HIDDEN_TEXT_BODY))
    found = fields(docx_format.hidden_section(open_package(pkg.build())))
    assert found["hidden_text_runs"] == 1 and found["hidden_text"] == "Secret note to self"
    assert found["glossary"] == ("word/glossary/document.xml, 1 paragraphs: Old letterhead "
                                 "of Jane Doe")
    assert GLOSSARY_TYPE


def test_revision_ids():
    pkg = docx(TRACKED_BODY)
    pkg.add_linked("word/document.xml", "settings", "word/settings.xml", settings_xml(RSIDS),
                   SETTINGS_TYPE)
    found = fields(docx_format.revision_ids_section(open_package(pkg.build())))
    assert found == {"rsids": 3, "rsid_attributes": 4}


def test_no_hidden_content_in_docx():
    pkg = open_package(docx().build())
    assert docx_format.hidden_section(pkg) is None
    assert docx_format.revision_ids_section(pkg) is None


# -- XLSX -------------------------------------------------------------------------------------


def test_hidden_sheets_and_workbook_history():
    pkg = open_package(kitchen_sink_xlsx_workbook(xlsx()).build())
    assert fields(xlsx_format.hidden_section(pkg)) == {"sheet.Salaries": "Salaries",
                                                      "sheet.Lookup": "Lookup"}
    sections = by_key(xlsx_format.workbook_section(pkg))
    info = fields(sections["xlsx_workbook"])
    assert info["fileVersion.lastEdited"] == "7" and info["fileVersion.rupBuild"] == "27425"
    assert info["revisionPtr.documentId"].startswith("13_ncr:1_")
    assert fields(sections["local_paths"])["absPath"] == f"C:{SEP}Users{SEP}jane.doe{SEP}" \
        f"Documents{SEP}Budget{SEP}"


def test_external_data():
    pkg = open_package(add_external_data(xlsx()).build())
    found = fields(xlsx_format.external_data_section(pkg))
    assert found["xl/externalLinks/externalLink1.xml"] == "1 cached cells from sheets Rates"
    assert "UID=jane.doe" in found["connection.1"] and "SELECT" in found["connection.1"]


def test_no_hidden_content_in_xlsx():
    pkg = open_package(xlsx().build())
    assert xlsx_format.hidden_section(pkg) is None
    assert xlsx_format.workbook_section(pkg) == []
    assert xlsx_format.external_data_section(pkg) is None


# -- PPTX -------------------------------------------------------------------------------------


def test_hidden_slides_and_speaker_notes():
    pkg = open_package(add_hidden_slide_and_notes(pptx()).build())
    assert fields(pptx_format.hidden_section(pkg)) == {
        "hidden_slide.ppt/slides/slide2.xml": "slide 2 (ppt/slides/slide2.xml)",
        "notes": 1, "notes_text": "Mention the deadline: 2024-06-01"}


def test_no_hidden_content_in_pptx():
    assert pptx_format.hidden_section(open_package(pptx().build())) is None
