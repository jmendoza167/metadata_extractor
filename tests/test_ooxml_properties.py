"""Reading docProps/core.xml and docProps/app.xml."""

from __future__ import annotations

import pytest

from helpers.ooxml import (APP_TYPE, CORE_TYPE, JANE_APP, JANE_CORE, app_xml, core_xml, docx,
                           pptx, with_properties, xlsx)
from metaex.core.errors import MalformedFileError
from metaex.core.ooxml import app_part, app_section, core_part, core_section, open_package


def fields(section):
    return {f.key: f.value for f in section.fields}


@pytest.mark.parametrize("builder", [docx, xlsx, pptx])
def test_core_properties(builder):
    pkg = open_package(with_properties(builder()).build())
    section = core_section(pkg)
    assert section.key == "office_core"
    assert fields(section) == JANE_CORE
    labels = {f.key: f.label for f in section.fields}
    assert labels["creator"] == "Author" and labels["lastModifiedBy"] == "Last modified by"
    assert all(f.sensitive for f in section.fields)


def test_app_properties():
    pkg = open_package(with_properties(docx()).build())
    found = fields(app_section(pkg))
    for name, value in JANE_APP.items():
        assert found[name] == value
    assert found["TitlesOfParts"] == ["Floor plan"]
    assert found["HeadingPairs"] == ["Title", "1"]


def test_unknown_core_elements_are_listed_too():
    core = core_xml({"creator": "Jane Doe"}).replace(
        "</cp:coreProperties>", '<x:mood xmlns:x="urn:x">tired</x:mood></cp:coreProperties>')
    pkg = open_package(with_properties(docx(), core=core).build())
    assert fields(core_section(pkg)) == {"creator": "Jane Doe", "other.mood": "tired"}


def test_empty_values_are_skipped_but_whitespace_in_descriptions_kept():
    core = core_xml({"creator": "", "title": "  A   title ", "description": "line 1\nline 2"})
    pkg = open_package(with_properties(docx(), core=core).build())
    assert fields(core_section(pkg)) == {"title": "A title", "description": "line 1\nline 2"}


def test_no_properties():
    pkg = open_package(docx().build())
    assert core_part(pkg) is None and core_section(pkg) is None
    assert app_part(pkg) is None and app_section(pkg) is None


def test_properties_found_by_content_type_and_default_name():
    pkg = docx()
    pkg.add("props/core.xml", core_xml({"creator": "Jane Doe"}), CORE_TYPE)
    pkg.add("docProps/app.xml", app_xml({"Company": "Example Corp"}, titles=()))
    opened = open_package(pkg.build())
    assert core_part(opened) == "props/core.xml"
    assert app_part(opened) == "docProps/app.xml"
    assert fields(app_section(opened)) == {"Company": "Example Corp"}


def test_long_values_are_shortened():
    pkg = open_package(with_properties(docx(), core=core_xml({"title": "x" * 3000})).build())
    assert fields(core_section(pkg))["title"].endswith("... (3000 characters)")


def test_broken_core_xml_raises():
    pkg = open_package(with_properties(docx(), core="<cp:coreProperties>").build())
    with pytest.raises(MalformedFileError):
        core_section(pkg)


def test_app_type_constant_matches():
    assert APP_TYPE.endswith("extended-properties+xml")
