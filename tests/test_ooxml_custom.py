"""Custom properties, sensitivity labels, custom XML and document variables."""

from __future__ import annotations

from helpers.ooxml import (CUSTOM_TYPE, JANE_CUSTOM, LABEL_GUID, TENANT_GUID, add_custom_xml,
                           custom_xml, doc_vars, docx, label_info_xml, settings_xml, xlsx,
                           SETTINGS_TYPE)
from metaex.core.ooxml import custom_sections, custom_xml_parts, custom_xml_section, open_package
from metaex.formats.docx import variables_section


def fields(section):
    return {f.key: f.value for f in section.fields}


def with_custom(pkg):
    pkg.add_linked("", "custom-properties", "docProps/custom.xml", custom_xml(), CUSTOM_TYPE)
    return pkg


def test_custom_properties_and_sensitivity_labels_are_separated():
    sections = {s.key: s for s in custom_sections(open_package(with_custom(docx()).build()))}
    assert fields(sections["office_custom"]) == {
        "property.ProjectCode": "FLAT-42",
        "property.ContentTypeId": JANE_CUSTOM["ContentTypeId"]}
    labels = fields(sections["office_labels"])
    assert labels[f"property.MSIP_Label_{LABEL_GUID}_Name"] == "Confidential"
    assert labels[f"property.MSIP_Label_{LABEL_GUID}_SiteId"] == TENANT_GUID
    assert len(labels) == 4


def test_label_info_part():
    pkg = docx()
    pkg.add_linked("", "http://schemas.microsoft.com/office/2020/02/relationships/"
                   "classificationlabels", "docMetadata/LabelInfo.xml", label_info_xml(),
                   "application/vnd.ms-office.classificationlabels+xml")
    sections = custom_sections(open_package(pkg.build()))
    assert [s.key for s in sections] == ["office_labels"]
    value = fields(sections[0])["label_info.1"]
    assert f"siteId={{{TENANT_GUID}}}" in value and "method=Standard" in value


def test_no_custom_properties():
    assert custom_sections(open_package(docx().build())) == []


def test_many_custom_properties_are_counted():
    props = {f"p{i}": str(i) for i in range(250)}
    pkg = docx()
    pkg.add_linked("", "custom-properties", "docProps/custom.xml", custom_xml(props), CUSTOM_TYPE)
    section = custom_sections(open_package(pkg.build()))[0]
    assert len(section.fields) == 201 and fields(section)["more"] == "50 more not listed"


def test_sharepoint_custom_xml():
    pkg = docx()
    add_custom_xml(pkg, "word/document.xml")
    opened = open_package(pkg.build())
    assert custom_xml_parts(opened) == ["customXml/item1.xml"]
    found = fields(custom_xml_section(opened))
    assert found["customXml/item1.xml:properties/documentManagement/SharedWithUsers/UserInfo/"
                 "DisplayName"] == "Jane Doe"
    assert found["customXml/item1.xml:properties/documentManagement/_dlc_DocId"] == \
        "EXAMPLE-123-456"


def test_custom_xml_in_a_workbook_and_unlinked_items():
    pkg = xlsx()
    add_custom_xml(pkg, "xl/workbook.xml")
    pkg.add("customXml/item2.xml", "<empty/>")
    opened = open_package(pkg.build())
    assert custom_xml_parts(opened) == ["customXml/item1.xml", "customXml/item2.xml"]
    assert fields(custom_xml_section(opened))["customXml/item2.xml:"] == \
        "<empty> (no namespace)"


def test_no_custom_xml():
    assert custom_xml_section(open_package(docx().build())) is None


def test_document_variables():
    pkg = docx()
    pkg.add_linked("word/document.xml", "settings", "word/settings.xml",
                   settings_xml(doc_vars(ClientName="Jane Doe", CaseNo="2024-17")), SETTINGS_TYPE)
    found = fields(variables_section(open_package(pkg.build())))
    assert found == {"docVar.ClientName": "Jane Doe", "docVar.CaseNo": "2024-17"}


def test_no_document_variables():
    pkg = docx()
    pkg.add_linked("word/document.xml", "settings", "word/settings.xml", settings_xml(),
                   SETTINGS_TYPE)
    assert variables_section(open_package(pkg.build())) is None
    assert variables_section(open_package(docx().build())) is None
