"""Removing printer settings and file paths; refusing user paths in links."""

from __future__ import annotations

import pytest

from helpers.ooxml import (PRINTER_TYPE, S_NS, SETTINGS_TYPE, SERVER_PATH, TEMPLATE_PATH,
                           add_links_and_template, doc_vars, docx, kitchen_sink_xlsx_workbook,
                           pptx, settings_xml, worksheet, xlsx, DECL, R_NS, devmode)
from metaex.core import ooxml
from metaex.core.errors import CannotCleanError
from metaex.core.ooxml import open_package
from metaex.formats import docx as docx_format
from metaex.formats import pptx as pptx_format
from metaex.formats import xlsx as xlsx_format

SEP = "\\"


def test_attached_template_and_document_variables_are_removed():
    pkg = docx()
    pkg.add_linked("word/document.xml", "settings", "word/settings.xml", settings_xml(
        '<w:attachedTemplate r:id="rId1"/>' + doc_vars(Client="Jane Doe")), SETTINGS_TYPE)
    pkg.link("word/settings.xml", "attachedTemplate", TEMPLATE_PATH, external=True)
    cleaned = open_package(docx_format.strip(pkg.build()))
    settings = cleaned.read("word/settings.xml")
    assert b"attachedTemplate" not in settings and b"docVar" not in settings
    assert b'<w:zoom w:percent="100"/>' in settings
    assert cleaned.rels("word/settings.xml") == []
    assert docx_format.variables_section(cleaned) is None
    assert ooxml.external_link_sections(cleaned) == []


def test_links_to_files_in_a_user_folder_are_refused():
    pkg = docx()
    pkg.link("word/document.xml", "hyperlink",
             f"file:///C:{SEP}Users{SEP}jane.doe{SEP}Documents{SEP}notes.docx", external=True)
    with pytest.raises(CannotCleanError, match="links to files in a user's folder") as exc:
        docx_format.strip(pkg.build())
    assert "Hyperlink in word/document.xml" in str(exc.value)


def test_other_links_are_kept():
    pkg = add_links_and_template(docx())
    cleaned = open_package(docx_format.strip(pkg.build()))
    targets = sorted(r.target for r in cleaned.all_relationships() if r.external)
    assert targets == sorted(["https://example.com/flat", SERVER_PATH])


def sheet_with_printer(printer: str = "HP LaserJet Example"):
    pkg = xlsx()
    sheet = worksheet().replace(
        "</sheetData>", '</sheetData><pageSetup paperSize="9" orientation="portrait" '
        'r:id="rId1"/>').replace(f'<worksheet xmlns="{S_NS}">',
                                 f'<worksheet xmlns="{S_NS}" xmlns:r="{R_NS}">')
    pkg.parts["xl/worksheets/sheet1.xml"] = sheet.encode("utf-8")
    pkg.add_linked("xl/worksheets/sheet1.xml", "printerSettings",
                   "xl/printerSettings/printerSettings1.bin", devmode(printer), PRINTER_TYPE)
    return pkg


def test_printer_settings_and_their_references_are_removed():
    original = sheet_with_printer().build()
    assert ooxml.printer_section(open_package(original)) is not None
    cleaned = open_package(xlsx_format.strip(original))
    assert ooxml.printer_settings_parts(cleaned) == []
    sheet = cleaned.read("xl/worksheets/sheet1.xml")
    assert b'<pageSetup paperSize="9" orientation="portrait"/>' in sheet
    assert cleaned.rels("xl/worksheets/sheet1.xml") == []


@pytest.mark.parametrize("builder, module, folder", [(docx, docx_format, "word"),
                                                     (pptx, pptx_format, "ppt")])
def test_printer_settings_in_word_and_powerpoint(builder, module, folder):
    pkg = builder()
    main = f"{folder}/document.xml" if folder == "word" else f"{folder}/presentation.xml"
    pkg.add_linked(main, "printerSettings", f"{folder}/printerSettings/printerSettings1.bin",
                   devmode(), PRINTER_TYPE.replace("spreadsheetml", "wordprocessingml"))
    cleaned = open_package(module.strip(pkg.build()))
    assert ooxml.printer_settings_parts(cleaned) == []


def test_workbook_paths_versions_and_revision_pointers_are_removed():
    cleaned = open_package(xlsx_format.strip(kitchen_sink_xlsx_workbook(xlsx()).build()))
    workbook = cleaned.read("xl/workbook.xml")
    assert b"absPath" not in workbook and b"AlternateContent" not in workbook
    assert b"revisionPtr" not in workbook and b"jane" not in workbook
    assert b'<fileVersion/>' in workbook
    assert b'mc:Ignorable="x15 xr xr6 xr10"' in workbook  # declarations stay as written
    assert xlsx_format.workbook_section(cleaned) == []
    # hidden sheets stay hidden: that is content
    assert {f.value for f in xlsx_format.hidden_section(cleaned).fields} == {"Salaries", "Lookup"}


def test_abs_path_next_to_other_content_is_removed_alone():
    history = ('<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/'
               'markup-compatibility/2006"><mc:Choice Requires="x15"><x15ac:absPath url="D:'
               f'{SEP}Projects{SEP}" xmlns:x15ac="http://schemas.microsoft.com/office/'
               'spreadsheetml/2010/11/ac"/><x15:other xmlns:x15="urn:x15"/></mc:Choice>'
               "</mc:AlternateContent>")
    from helpers.ooxml import workbook_with

    pkg = xlsx()
    pkg.parts["xl/workbook.xml"] = workbook_with('<sheet name="Sheet1" sheetId="1" r:id="rId1"/>',
                                                 history).encode("utf-8")
    workbook = open_package(xlsx_format.strip(pkg.build())).read("xl/workbook.xml")
    assert b"absPath" not in workbook and b"<x15:other" in workbook
    assert DECL
