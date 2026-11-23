"""Removing revision IDs (rsids) and the thumbnail."""

from __future__ import annotations

import pytest

from helpers import jpeg
from helpers.ooxml import (COMMENTED_BODY, DECL, RSIDS, SETTINGS_TYPE, TRACKED_BODY, W_NS,
                           WORD_NAMESPACES, add_thumbnail, docx, pptx, settings_xml, xlsx)
from metaex.core import ooxml
from metaex.core.ooxml import open_package
from metaex.formats import docx as docx_format
from metaex.formats import pptx as pptx_format
from metaex.formats import xlsx as xlsx_format

STYLES = (f'{DECL}<w:styles xmlns:w="{W_NS}"><w:style w:type="paragraph" w:styleId="Normal">'
          '<w:name w:val="Normal"/><w:rsid w:val="00A1B2C3"/><w:rPr w:rsidR="00A1B2C3"/>'
          "</w:style></w:styles>")


def word() -> bytes:
    pkg = docx(COMMENTED_BODY + TRACKED_BODY, extra_ns=WORD_NAMESPACES)
    pkg.add_linked("word/document.xml", "settings", "word/settings.xml", settings_xml(RSIDS),
                   SETTINGS_TYPE)
    pkg.add_linked("word/document.xml", "styles", "word/styles.xml", STYLES,
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml")
    return pkg.build()


def test_revision_ids_are_found_and_removed():
    before = docx_format.revision_ids_section(open_package(word()))
    assert {f.key: f.value for f in before.fields} == {
        "rsids": 3, "rsid_attributes": 8, "rsid_elements": 1}
    cleaned = open_package(docx_format.strip(word()))
    assert docx_format.revision_ids_section(cleaned) is None
    for part in ("word/document.xml", "word/settings.xml", "word/styles.xml"):
        assert b"rsid" not in cleaned.read(part), part


def test_removing_revision_ids_keeps_everything_else():
    cleaned = open_package(docx_format.strip(word()))
    document = cleaned.read("word/document.xml")
    assert b'w14:paraId="1A2B3C4D"' in document and b'mc:Ignorable="w14 w15"' in document
    assert b"<w:delText>twelve hundred</w:delText>" in document
    settings = cleaned.read("word/settings.xml")
    assert b'<w:zoom w:percent="100"/>' in settings
    styles = cleaned.read("word/styles.xml")
    assert b'<w:name w:val="Normal"/><w:rPr/>' in styles


@pytest.mark.parametrize("builder, module", [(docx, docx_format), (xlsx, xlsx_format),
                                             (pptx, pptx_format)])
def test_thumbnail_is_removed(builder, module):
    original = add_thumbnail(builder(), jpeg.sample_photo()).build()
    assert ooxml.thumbnail_section(open_package(original)) is not None
    cleaned = open_package(module.strip(original))
    assert ooxml.thumbnail_parts(cleaned) == []
    assert all("thumbnail" not in r.type for r in cleaned.rels(""))
    assert not any(n.startswith("docProps/thumbnail") for n in cleaned.part_names())
