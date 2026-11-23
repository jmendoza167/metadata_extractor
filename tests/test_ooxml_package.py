"""OOXML packages: content types, relationships, document kinds, refusals;
and the XML parser and OLE2 detection they rely on."""

from __future__ import annotations

import subprocess
import sys

import pytest

from helpers import ole2 as ole2_files
from helpers.ooxml import (EXCEL_BINARY, EXCEL_MACRO, PPT_MACRO, VBA, WORD_MACRO, WORD_TEMPLATE,
                           MS_REL, DECL, Package, docx, pptx, xlsx)
from metaex.core import filetype as ft
from metaex.core import ole2, xmldoc
from metaex.core.errors import MalformedFileError, UnsupportedFormatError
from metaex.core.ooxml import (open_package, rels_name, resolve_target, source_of_rels)
from metaex.core.xmldoc import parse_xml

# -- XML parser -----------------------------------------------------------------------------


def test_offsets_of_elements():
    data = b'\xef\xbb\xbf<?xml version="1.0"?>\n<a x="1>2"><b/><c y=\'q\'>t&amp;u</c><d></d></a>'
    doc = parse_xml(data)
    a = doc.root
    b, c, d = list(a.elements())
    assert data[a.start:a.start_end] == b'<a x="1>2">'
    assert data[b.start:b.end] == b"<b/>" and b.empty
    assert data[c.start:c.start_end] == b"<c y='q'>" and data[c.start:c.end] == \
        b"<c y='q'>t&amp;u</c>"
    assert data[d.start:d.end] == b"<d></d>" and not d.empty
    assert data[a.start:a.end].endswith(b"</a>")
    assert c.text() == "t&u" and c.get("y") == "q"


def test_namespaces_are_resolved_without_changing_prefixes():
    doc = parse_xml(b'<w:document xmlns:w="urn:w" xmlns:x="urn:x"><w:p x:attr="1"/>'
                    b'<w:p xmlns:w="urn:other"/></w:document>')
    first, second = list(doc.root.elements())
    assert first.name == "w:p" and first.uri == "urn:w" and first.get("attr", "urn:x") == "1"
    assert first.attr_name("attr", "urn:x") == "x:attr"
    assert second.uri == "urn:other"
    assert doc.find_all("urn:w", "p") == [first]


def test_unbound_prefix_is_refused():
    with pytest.raises(MalformedFileError, match="unbound namespace prefix"):
        parse_xml(b"<w:document/>", "word/document.xml")


@pytest.mark.parametrize("data, message", [
    (b'<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>', "DOCTYPE"),
    (b'<!DOCTYPE a SYSTEM "http://example.com/a.dtd"><a/>', "DOCTYPE"),
    (b"<a>&undefined;</a>", "not well-formed"),
    (b"<a><!ENTITY x 'y'></a>", "ENTITY"),
    ('<?xml version="1.0" encoding="UTF-16"?><a/>'.encode("utf-16"), "not UTF-8"),
    (b'<?xml version="1.0" encoding="ISO-8859-1"?><a/>', "encoding"),
    (b"<a><b></a>", "not well-formed"),
    (b"", "not well-formed|no root"),
])
def test_unsafe_or_broken_xml_is_refused(data, message):
    with pytest.raises(MalformedFileError, match=message):
        parse_xml(data, "part.xml")


def test_billion_laughs_is_refused_before_expansion():
    lol = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
           + b"".join(b'<!ENTITY lol%d "&lol%s;&lol%s;">' % (i, b"" if i == 1 else b"%d" % (i - 1),
                                                           b"" if i == 1 else b"%d" % (i - 1))
                      for i in range(1, 10))
           + b"]><lolz>&lol9;</lolz>")
    with pytest.raises(MalformedFileError, match="DOCTYPE"):
        parse_xml(lol)


def test_element_and_depth_limits(monkeypatch):
    monkeypatch.setattr(xmldoc, "MAX_ELEMENTS", 3)
    with pytest.raises(MalformedFileError, match="more than 3 elements"):
        parse_xml(b"<a><b/><c/><d/></a>")
    monkeypatch.setattr(xmldoc, "MAX_DEPTH", 2)
    with pytest.raises(MalformedFileError, match="nested more than 2 deep"):
        parse_xml(b"<a><b><c/></b></a>")


def test_comments_and_processing_instructions_are_kept_as_nodes():
    data = b"<?xml version='1.0'?><!-- before --><a><!--inside--><?pi data?></a>"
    doc = parse_xml(data)
    assert [c.value for c in doc.comments()] == [" before ", "inside"]
    pi = doc.processing_instructions()[0]
    assert (pi.target, pi.data) == ("pi", "data") and data[pi.start:pi.end] == b"<?pi data?>"
    comment = doc.comments()[1]
    assert data[comment.start:comment.end] == b"<!--inside-->"


def test_check_no_dtd_for_unparsed_parts():
    xmldoc.check_no_dtd(b"<a>fine</a>", "x")
    with pytest.raises(MalformedFileError, match="DOCTYPE"):
        xmldoc.check_no_dtd(b"<!DOCTYPE a><a/>", "x")


# -- relationships ------------------------------------------------------------------------------


@pytest.mark.parametrize("source, target, part", [
    ("word/document.xml", "styles.xml", "word/styles.xml"),
    ("word/document.xml", "media/image%201.png", "word/media/image 1.png"),
    ("word/document.xml", "../customXml/item1.xml", "customXml/item1.xml"),
    ("word/document.xml", "/docProps/core.xml", "docProps/core.xml"),
    ("", "word/document.xml", "word/document.xml"),
    ("word/document.xml", "styles.xml#frag", "word/styles.xml"),
    ("word/document.xml", "../../escape.xml", None),
    ("word/document.xml", "#only-fragment", None),
])
def test_resolve_target(source, target, part):
    assert resolve_target(source, target) == part


def test_relationship_part_names():
    assert rels_name("") == "_rels/.rels"
    assert rels_name("word/document.xml") == "word/_rels/document.xml.rels"
    assert source_of_rels("word/_rels/document.xml.rels") == "word/document.xml"
    assert source_of_rels("_rels/.rels") == ""
    assert source_of_rels("word/document.xml") is None


# -- packages -----------------------------------------------------------------------------------


@pytest.mark.parametrize("builder, kind, main", [
    (docx, "docx", "word/document.xml"),
    (xlsx, "xlsx", "xl/workbook.xml"),
    (pptx, "pptx", "ppt/presentation.xml"),
])
def test_main_document_and_kind(builder, kind, main):
    pkg = open_package(builder().build())
    assert (pkg.kind, pkg.main, pkg.macro_enabled, pkg.template) == (kind, main, False, False)
    assert pkg.vba_parts == [] and pkg.unreferenced() == []


@pytest.mark.parametrize("builder, main_type, source", [
    (docx, WORD_MACRO, "word/document.xml"),
    (xlsx, EXCEL_MACRO, "xl/workbook.xml"),
    (pptx, PPT_MACRO, "ppt/presentation.xml"),
])
def test_macro_enabled_variants_and_vba_projects(builder, main_type, source):
    pkg = builder(main_type=main_type)
    folder = source.split("/")[0]
    pkg.add_linked(source, MS_REL + "vbaProject", f"{folder}/vbaProject.bin", b"VBA", VBA)
    opened = open_package(pkg.build())
    assert opened.macro_enabled and opened.vba_parts == [f"{folder}/vbaProject.bin"]
    assert "macro-enabled" in opened.description


def test_vba_project_found_by_content_type_alone():
    pkg = docx(main_type=WORD_MACRO)
    pkg.add("word/vbaProject.bin", b"VBA", VBA)
    opened = open_package(pkg.build())
    assert opened.vba_parts == ["word/vbaProject.bin"]
    assert opened.unreferenced() == ["word/vbaProject.bin"]


def test_templates():
    opened = open_package(docx(main_type=WORD_TEMPLATE).build())
    assert opened.template and opened.description == "Word template (.dotx)"


def test_content_types_and_relationships():
    pkg = docx()
    pkg.defaults["png"] = "image/png"
    pkg.add_linked("word/document.xml", "image", "word/media/image1.png", b"\x89PNG")
    pkg.link("word/document.xml", "hyperlink", "https://example.com/", external=True)
    opened = open_package(pkg.build())
    assert opened.type_of("word/media/IMAGE1.PNG") == "image/png"
    assert opened.type_of("WORD/DOCUMENT.XML").endswith("document.main+xml")
    rels = opened.rels("word/document.xml")
    assert [(r.id, r.type_name, r.external, r.part) for r in rels] == [
        ("rId1", "image", False, "word/media/image1.png"),
        ("rId2", "hyperlink", True, None)]
    assert opened.related("word/document.xml", "image") == ["word/media/image1.png"]
    assert opened.reachable() == {"word/document.xml", "word/media/image1.png"}


def test_parts_no_relationship_leads_to_are_listed():
    pkg = docx()
    pkg.add("word/hidden.xml", "<secret/>")
    pkg.add("leftover.bin", b"\x00")
    assert open_package(pkg.build()).unreferenced() == ["word/hidden.xml", "leftover.bin"]


def test_part_names_ignore_case():
    pkg = Package()
    pkg.add("Word/Document.xml", docx().parts["word/document.xml"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml")
    pkg.link("", "officeDocument", "word/document.xml")
    opened = open_package(pkg.build())
    assert opened.main == "Word/Document.xml" and opened.has("word/document.xml")


# -- refusals -----------------------------------------------------------------------------------


def broken(**changes) -> bytes:
    files = docx().files()
    files.update(changes)
    from helpers.zip import make_zip

    return make_zip({k: v for k, v in files.items() if v is not None})


@pytest.mark.parametrize("data, error, message", [
    (broken(**{"[Content_Types].xml": None}), MalformedFileError, r"no \[Content_Types\].xml"),
    (broken(**{"_rels/.rels": None}), MalformedFileError, "no main document"),
    (broken(**{"word/document.xml": None}), MalformedFileError, "main document .* is missing"),
    (broken(**{"[Content_Types].xml": b"<Wrong/>"}), MalformedFileError, "wrong root"),
    (broken(**{"[Content_Types].xml": b"<!DOCTYPE x><Types/>"}), MalformedFileError, "DOCTYPE"),
    (broken(**{"_rels/.rels": (DECL + "<!DOCTYPE r [<!ENTITY a 'b'>]><r/>").encode()}),
     MalformedFileError, "DOCTYPE"),
    (broken(**{"word/document.xml": b"<w:document>"}), MalformedFileError, "not well-formed|unbound"),
])
def test_damaged_packages_are_refused(data, error, message):
    with pytest.raises(error, match=message):
        open_package(data)


def test_duplicate_relationship_ids_are_refused():
    pkg = docx()
    pkg.link("word/document.xml", "styles", "word/styles.xml", rel_id="rId7")
    pkg.link("word/document.xml", "settings", "word/settings.xml", rel_id="rId7")
    with pytest.raises(MalformedFileError, match="two relationships have the Id 'rId7'"):
        open_package(pkg.build())


def test_two_main_documents_are_refused():
    pkg = docx()
    pkg.link("", "officeDocument", "word/document.xml")
    with pytest.raises(MalformedFileError, match="more than one main document"):
        open_package(pkg.build())


def test_strict_open_xml_is_refused():
    pkg = Package()
    pkg.add("word/document.xml", '<w:document xmlns:w="http://purl.oclc.org/ooxml/'
            'wordprocessingml/main"/>', WORD_TEMPLATE)
    pkg.link("", "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument",
             "word/document.xml")
    with pytest.raises(UnsupportedFormatError, match="Strict Open XML"):
        open_package(pkg.build())


def test_strict_namespaces_in_a_transitional_package_are_refused():
    pkg = docx(extra_ns="")
    pkg.parts["word/document.xml"] = (b'<w:document xmlns:w="http://purl.oclc.org/ooxml/'
                                      b'wordprocessingml/main"/>')
    with pytest.raises(UnsupportedFormatError, match="Strict Open XML"):
        open_package(pkg.build())


def test_binary_workbook_is_refused():
    with pytest.raises(UnsupportedFormatError, match="xlsb"):
        open_package(xlsx(main_type=EXCEL_BINARY).build())


def test_unknown_main_type_is_refused():
    with pytest.raises(UnsupportedFormatError, match="content type 'text/plain'"):
        open_package(docx(main_type="text/plain").build())


# -- OLE2: legacy and password-protected files ----------------------------------------------------


@pytest.mark.parametrize("data, kind, filetype", [
    (ole2_files.encrypted_office(), ole2.ENCRYPTED_OOXML, ft.ENCRYPTED_OFFICE),
    (ole2_files.legacy_word(), ole2.WORD, ft.DOC),
    (ole2_files.legacy_excel(), ole2.EXCEL, ft.XLS),
    (ole2_files.legacy_powerpoint(), ole2.POWERPOINT, ft.PPT),
    (ole2_files.ole2_file("__substg1.0_0037001F"), ole2.OUTLOOK, ft.OLE2),
    (ole2_files.ole2_file("Something"), ole2.OTHER, ft.OLE2),
])
def test_ole2_files_are_told_apart(data, kind, filetype):
    assert ole2.classify(data) == kind
    assert ft.detect(data) == filetype


def test_ole2_directory_with_many_entries():
    names = [f"Stream{i}" for i in range(9)] + ["WordDocument"]
    assert ole2.stream_names(ole2_files.ole2_file(*names))[-1] == "WordDocument"


@pytest.mark.parametrize("data", [
    ole2_files.ole2_file("WordDocument", loop=True),
    ole2_files.ole2_file("WordDocument", dir_sector=99),
    ole2_files.legacy_word()[:600],
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504,
])
def test_damaged_ole2_files_are_generic(data):
    with pytest.raises(MalformedFileError):
        ole2.stream_names(data)
    assert ft.detect(data) == ft.OLE2


def test_mutated_ole2_files_never_crash():
    import random

    rng = random.Random(4)
    good = ole2_files.encrypted_office()
    for _ in range(500):
        data = bytearray(good)
        for _ in range(3):
            data[rng.randrange(len(data))] = rng.randrange(256)
        ole2.classify(bytes(data))


@pytest.mark.parametrize("data, message", [
    (ole2_files.encrypted_office(), "password-protected"),
    (ole2_files.legacy_word(), "save it as .docx"),
    (ole2_files.legacy_excel(), "save it as .xlsx"),
    (ole2_files.legacy_powerpoint(), "save it as .pptx"),
])
def test_strip_refuses_legacy_and_encrypted_office_files(data, message, tmp_path):
    path = tmp_path / "report.docx"
    path.write_bytes(data)
    r = subprocess.run([sys.executable, "-m", "metaex", "strip", str(path)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1
    assert message in r.stderr and "no file written" in r.stderr
    assert [p.name for p in tmp_path.iterdir()] == ["report.docx"]
    show = subprocess.run([sys.executable, "-m", "metaex", "show", str(path)],
                          capture_output=True, text=True, encoding="utf-8")
    assert show.returncode == 0 and message in " ".join(show.stdout.split())
