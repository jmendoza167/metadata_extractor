"""Stripping document properties and custom XML from DOCX, XLSX and PPTX."""

from __future__ import annotations

import pytest

from helpers.ooxml import (CUSTOM_TYPE, add_custom_xml, custom_xml, docx, label_info_xml,
                           paragraph, pptx, with_properties, xlsx)
from helpers.zip import DEFLATED
from metaex.core.ooxml import open_package
from metaex.core.ziparchive import open_archive, report_sections
from metaex.formats import docx as docx_format
from metaex.formats import pptx as pptx_format
from metaex.formats import xlsx as xlsx_format

def content_parts(pkg):
    return [n for n in pkg.part_names() if n != "[Content_Types].xml" and "_rels/" not in n]


FORMATS = [(docx, docx_format, "word/document.xml"), (xlsx, xlsx_format, "xl/workbook.xml"),
           (pptx, pptx_format, "ppt/presentation.xml")]


def full(builder, main):
    pkg = with_properties(builder())
    pkg.add_linked("", "custom-properties", "docProps/custom.xml", custom_xml(), CUSTOM_TYPE)
    pkg.add_linked("", "http://schemas.microsoft.com/office/2020/02/relationships/"
                   "classificationlabels", "docMetadata/LabelInfo.xml", label_info_xml(),
                   "application/vnd.ms-office.classificationlabels+xml")
    add_custom_xml(pkg, main)
    return pkg


@pytest.mark.parametrize("builder, module, main", FORMATS)
def test_properties_are_emptied_and_custom_parts_removed(builder, module, main):
    original = full(builder, main).build()
    cleaned = module.strip(original)
    pkg = open_package(cleaned)
    names = pkg.part_names()
    assert "docProps/custom.xml" not in names and "docMetadata/LabelInfo.xml" not in names
    assert not any(n.startswith("customXml/") for n in names)
    core = pkg.read("docProps/core.xml")
    assert b"Jane" not in core and b"dc:creator" not in core
    assert b'xmlns:dc="http://purl.org/dc/elements/1.1/"' in core  # declarations kept
    app = pkg.read("docProps/app.xml")
    assert b"Example Corp" not in app and b"Template" not in app
    # relationships and content types follow the removed parts
    assert all("custom" not in r.type and "classificationlabels" not in r.type
               for r in pkg.all_relationships())
    assert "docprops/custom.xml" not in pkg.content_types.overrides
    assert not any("customxml" in name for name in pkg.content_types.overrides)
    # the parse of the cleaned file finds none of it
    parsed = module.parse(cleaned)
    keys = {s.key for s in parsed["sections"]}
    assert not keys & {"office_core", "office_app", "office_custom", "office_labels",
                       "office_custom_xml"}


@pytest.mark.parametrize("builder, module, main", FORMATS)
def test_untouched_parts_are_copied_byte_for_byte(builder, module, main):
    pkg = full(builder, main)
    original = open_archive(pkg.build(method=DEFLATED))
    cleaned = open_archive(module.strip(pkg.build(method=DEFLATED)))
    for entry in cleaned.entries:
        if entry.name in ("[Content_Types].xml", "_rels/.rels", "docProps/core.xml",
                          "docProps/app.xml", f"{main.rsplit('/', 1)[0]}/_rels/"
                          f"{main.rsplit('/', 1)[1]}.rels"):
            continue
        assert cleaned.raw(entry) == original.raw(original.get(entry.name)), entry.name


@pytest.mark.parametrize("builder, module, main", FORMATS)
def test_zip_container_is_normalized(builder, module, main):
    from helpers.zip import Entry, build_zip, dos_stamp, ntfs_times

    date, time = dos_stamp(2024, 5, 1, 12, 30)
    files = full(builder, main).files()
    order = [n for n in files if n != "[Content_Types].xml"] + ["[Content_Types].xml"]
    data = build_zip([Entry(n, files[n], date=date, time=time, created_by=(3 << 8) | 30,
                            local_extra=ntfs_times(1, 2, 3), comment=b"note") for n in order],
                     comment=b"Jane's archive")
    assert report_sections(open_archive(data)) != []
    cleaned = open_archive(module.strip(data))
    assert report_sections(cleaned) == []
    assert cleaned.names()[0] == "[Content_Types].xml"


def test_unused_parts_are_dropped():
    pkg = docx()
    pkg.add("word/stray.xml", "<x>left over</x>", "application/xml")
    pkg.add("secret.bin", b"\x00hidden")
    cleaned = open_package(docx_format.strip(pkg.build()))
    assert content_parts(cleaned) == ["word/document.xml"]
    assert "word/stray.xml" not in cleaned.content_types.overrides


def test_custom_xml_shown_by_content_controls_is_kept():
    item_id = "{AAAAAAAA-1111-2222-3333-444444444444}"
    body = (f'<w:sdt><w:sdtPr><w:dataBinding w:xpath="/root/name" w:storeItemID="{item_id}"/>'
            f"</w:sdtPr><w:sdtContent>{paragraph('Jane Doe')}</w:sdtContent></w:sdt>")
    pkg = docx(body)
    add_custom_xml(pkg, "word/document.xml")
    add_custom_xml(pkg, "word/document.xml", item="<other>unbound</other>", n=2,
                   item_id="{BBBBBBBB-1111-2222-3333-444444444444}")
    cleaned = docx_format.strip(pkg.build())
    names = open_package(cleaned).part_names()
    assert "customXml/item1.xml" in names and "customXml/itemProps1.xml" in names
    assert "customXml/item2.xml" not in names
    warnings = docx_format.kept_warnings(cleaned)
    assert any("kept customXml/item1.xml" in w for w in warnings)


def test_content_controls_showing_document_properties_are_reported():
    body = ('<w:sdt><w:sdtPr><w:dataBinding w:prefixMappings="" w:xpath="/ns1:coreProperties[1]/'
            'ns0:creator[1]" w:storeItemID="{6C3C8BC8-F283-45AE-878A-BAB7291924A1}"/></w:sdtPr>'
            f"<w:sdtContent>{paragraph('Jane Doe')}</w:sdtContent></w:sdt>")
    cleaned = docx_format.strip(with_properties(docx(body)).build())
    assert any("show document properties" in w for w in docx_format.kept_warnings(cleaned))


def test_documents_without_properties():
    cleaned = docx_format.strip(docx().build())
    assert content_parts(open_package(cleaned)) == ["word/document.xml"]
    assert docx_format.kept_warnings(cleaned) == []


def test_kind_mismatch_is_refused():
    from metaex.core.errors import MalformedFileError

    with pytest.raises(MalformedFileError, match="looks like a Word document"):
        docx_format.strip(xlsx().build())


def test_doctype_in_any_part_is_refused():
    from metaex.core.errors import MalformedFileError

    pkg = docx()
    pkg.add_linked("word/document.xml", "styles", "word/styles.xml",
                   '<!DOCTYPE s [<!ENTITY a "b">]><w:styles/>')
    with pytest.raises(MalformedFileError, match="DOCTYPE"):
        docx_format.strip(pkg.build())
