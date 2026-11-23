"""Risk levels of Office document fields (core/risk.py)."""

from __future__ import annotations

import pytest

from helpers import jpeg
from helpers.ooxml import (TRACKED_BODY, add_links_and_template, add_media, add_word_comments,
                           docx, with_properties)
from metaex.core import ooxml
from metaex.core.ooxml import open_package
from metaex.core.result import Section
from metaex.core.risk import CRITICAL, HIGH, LOW, MEDIUM, assess, classify
from metaex.formats import docx as docx_format

SEP = "\\"


@pytest.mark.parametrize("section, key, level", [
    # who wrote it
    ("office_core", "creator", CRITICAL),
    ("office_core", "lastModifiedBy", CRITICAL),
    ("office_people", "person.1.userId", CRITICAL),
    ("office_people", "person.1.name", HIGH),
    ("office_people", "person.1.initials", HIGH),
    ("office_people", "person.1.providerId", MEDIUM),
    ("office_embedded", "word/embeddings/a.xlsx:creator", CRITICAL),
    ("office_embedded", "word/embeddings/a.xlsx", HIGH),
    # what it hides
    ("tracked_changes", "deleted_text", CRITICAL),
    ("tracked_changes", "deleted_characters", CRITICAL),
    ("tracked_changes", "authors", HIGH),
    ("tracked_changes", "dates", HIGH),
    ("tracked_changes", "insertions", LOW),
    ("office_hidden_parts", "word/stray.xml", CRITICAL),
    ("zip_hidden", "range_1", CRITICAL),
    ("hidden_content", "hidden_text", HIGH),
    ("hidden_content", "glossary", HIGH),
    ("hidden_content", "sheet.Salaries", MEDIUM),
    ("hidden_content", "notes_text", MEDIUM),
    # organization, software, dates
    ("office_app", "Company", HIGH),
    ("office_app", "Manager", HIGH),
    ("office_app", "Template", HIGH),
    ("office_app", "Application", HIGH),
    ("office_app", "AppVersion", HIGH),
    ("office_core", "created", HIGH),
    ("office_core", "modified", HIGH),
    ("office_core", "lastPrinted", HIGH),
    ("office_comments", "authors", HIGH),
    ("office_comments", "notes.authors", HIGH),
    ("office_comments", "threaded.dates", HIGH),
    ("office_comments", "dateUtc", HIGH),
    ("office_labels", "property.MSIP_Label_x_Name", HIGH),
    ("office_custom", "property.ProjectCode", HIGH),
    ("office_custom_xml", "customXml/item1.xml:properties/x", HIGH),
    ("docx_variables", "docVar.Client", HIGH),
    ("office_external", "word/document.xml:rId1", HIGH),
    ("xlsx_external_data", "connection.1", HIGH),
    ("xlsx_workbook", "fileVersion.lastEdited", HIGH),
    ("xlsx_workbook", "revisionPtr.documentId", HIGH),
    ("xlsx_workbook", "revisionPtr.revIDLastSave", MEDIUM),
    ("macros", "word/vbaProject.bin", HIGH),
    ("zip_comment", "comment", HIGH),
    ("zip_extra", "a.xml.local.0x000a", HIGH),
    ("zip_entries", "entry_times", HIGH),
    # medium
    ("office_app", "TotalTime", MEDIUM),
    ("office_core", "revision", MEDIUM),
    ("printer_settings", "xl/printerSettings/printerSettings1.bin", MEDIUM),
    ("revision_ids", "rsid_attributes", MEDIUM),
    ("office_thumbnail", "docProps/thumbnail.jpeg", MEDIUM),
    ("hidden_content", "hidden_slide.ppt/slides/slide2.xml", MEDIUM),
    # low
    ("office_app", "Words", LOW),
    ("office_app", "TitlesOfParts", LOW),
    ("office_core", "title", LOW),
    ("office_core", "keywords", LOW),
    ("office_comments", "count", LOW),
    ("embedded_images", "word/media/image1.jpeg", LOW),
    ("zip_entries", "created_by_os", LOW),
    # OpenDocument
    ("odf_meta", "initial-creator", CRITICAL),
    ("odf_meta", "creator", CRITICAL),
    ("odf_meta", "printed-by", CRITICAL),
    ("odf_meta", "creation-date", HIGH),
    ("odf_meta", "generator", HIGH),
    ("odf_meta", "editing-duration", MEDIUM),
    ("odf_meta", "editing-cycles", MEDIUM),
    ("odf_meta", "document-statistic.word-count", LOW),
    ("odf_user_defined", "Client", HIGH),
    ("odf_settings", "PrinterName", MEDIUM),
])
def test_office_levels(section, key, level):
    assert classify(section, key) == level


@pytest.mark.parametrize("value", [
    f"C:{SEP}Users{SEP}jane.doe{SEP}AppData{SEP}Roaming{SEP}Microsoft{SEP}Templates{SEP}Normal.dotm",
    "/home/jane.doe/Templates/letter.ott",
])
def test_paths_that_name_a_user_are_critical(value):
    assert classify("office_app", "Template", "Template", value) == CRITICAL
    assert classify("odf_meta", "template", "Template", value) == CRITICAL


def test_paths_without_a_user_name_keep_their_level():
    assert classify("office_app", "Template", "Template", "Normal.dotm") == HIGH
    assert classify("office_external", "x:rId1", "Hyperlink", "https://example.com/") == HIGH


def test_office_rules_do_not_change_image_rules():
    assert classify("exif", "Image.Artist", "Artist") == HIGH
    assert classify("gps", "GPSLatitude") == CRITICAL
    assert classify("xmp", "dc:title[1]") == HIGH


def test_assessing_a_document():
    pkg = with_properties(add_word_comments(add_links_and_template(docx(TRACKED_BODY))))
    add_media(pkg, "word/document.xml", "word/media/image1.jpeg", jpeg.sample_photo(), "jpeg",
              "image/jpeg")
    opened = open_package(pkg.build())
    sections = [ooxml.core_section(opened), ooxml.app_section(opened),
                docx_format.comments_section(opened), docx_format.people_section(opened),
                docx_format.tracked_changes_section(opened),
                *ooxml.external_link_sections(opened),
                *ooxml.embedded_image_sections(opened, [])]
    findings = assess([s for s in sections if s is not None])
    critical = {(f.section_key, f.field_key) for f in findings if f.level == CRITICAL}
    assert {("office_core", "creator"), ("office_core", "lastModifiedBy"),
            ("office_app", "Template"), ("office_people", "person.1.userId"),
            ("tracked_changes", "deleted_text"), ("tracked_changes", "deleted_characters"),
            ("local_paths", "word/settings.xml:rId1")} <= critical
    assert any(f.section_key == "gps" for f in findings if f.level == CRITICAL)
    # most severe first
    levels = [f.level for f in findings]
    assert levels == sorted(levels, key=[CRITICAL, HIGH, MEDIUM, LOW].index)


def test_unknown_office_field_uses_the_section_level():
    section = Section("office_custom", "Custom properties")
    section.add("property.Anything", "Anything", "value")
    assert assess([section])[0].level == HIGH
