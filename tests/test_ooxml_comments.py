"""Comments, people and tracked changes in DOCX, XLSX and PPTX."""

from __future__ import annotations

from helpers.ooxml import (COMMENTED_BODY, JANE_USER_ID, TRACKED_BODY, WORD_CT, WORD_NAMESPACES,
                           add_excel_comments, add_ppt_comments, add_word_comments, docx, pptx,
                           word_comments, xlsx)
from metaex.core.ooxml import open_package
from metaex.formats import docx as docx_format
from metaex.formats import pptx as pptx_format
from metaex.formats import xlsx as xlsx_format


def fields(section):
    return {f.key: f.value for f in section.fields}


# -- DOCX ---------------------------------------------------------------------------------------


def test_word_comments_and_extension_dates():
    pkg = open_package(add_word_comments(docx(COMMENTED_BODY, extra_ns=WORD_NAMESPACES)).build())
    assert fields(docx_format.comments_section(pkg)) == {
        "count": 1, "authors": ["Jane Doe"], "initials": ["JD"],
        "dates": "2024-05-01T10:00:00Z", "dateUtc": "2024-05-01T08:00:00Z"}


def test_word_comment_authors_are_listed_once():
    pkg = docx()
    pkg.add_linked("word/document.xml", "comments", "word/comments.xml", word_comments(
        ("Jane Doe", "JD", "2024-05-01T10:00:00Z", "a"),
        ("John Example", "JE", "2024-05-03T10:00:00Z", "b"),
        ("Jane Doe", "JD", "2024-05-02T10:00:00Z", "c")), WORD_CT + "comments+xml")
    found = fields(docx_format.comments_section(open_package(pkg.build())))
    assert found["count"] == 3 and found["authors"] == ["Jane Doe", "John Example"]
    assert found["dates"] == "2024-05-01T10:00:00Z to 2024-05-03T10:00:00Z (3 dates)"


def test_word_people_with_accounts():
    pkg = open_package(add_word_comments(docx()).build())
    assert fields(docx_format.people_section(pkg)) == {
        "person.1.name": "Jane Doe", "person.1.userId": JANE_USER_ID,
        "person.1.providerId": "AD", "person.2.name": "John Example"}


def test_tracked_changes_and_deleted_text():
    pkg = open_package(docx(TRACKED_BODY).build())
    assert fields(docx_format.tracked_changes_section(pkg)) == {
        "insertions": 1, "deletions": 1, "formatting": 1,
        "authors": ["Jane Doe", "John Example"],
        "dates": "2024-05-01T10:15:00Z to 2024-05-02T09:00:00Z (3 dates)",
        "deleted_characters": 14, "deleted_text": '"twelve hundred"'}


def test_tracked_changes_in_headers_and_footnotes():
    pkg = docx()
    header = ('<?xml version="1.0"?><w:hdr xmlns:w="http://schemas.openxmlformats.org/'
              'wordprocessingml/2006/main"><w:p><w:del w:id="9" w:author="Jane Doe">'
              '<w:r><w:delText>Draft - do not share</w:delText></w:r></w:del></w:p></w:hdr>')
    pkg.add_linked("word/document.xml", "header", "word/header1.xml", header,
                   WORD_CT + "header+xml")
    opened = open_package(pkg.build())
    assert docx_format.story_parts(opened) == ["word/document.xml", "word/header1.xml"]
    found = fields(docx_format.tracked_changes_section(opened))
    assert found["deleted_text"] == '"Draft - do not share"' and found["deletions"] == 1


def test_long_deleted_text_is_shortened_but_counted():
    body = ('<w:p><w:del w:id="1" w:author="A"><w:r><w:delText>' + "x" * 500
            + "</w:delText></w:r></w:del></w:p>")
    found = fields(docx_format.tracked_changes_section(open_package(docx(body).build())))
    assert found["deleted_characters"] == 500 and found["deleted_text"] == '"' + "x" * 200 + '..."'


def test_no_comments_people_or_changes():
    pkg = open_package(docx().build())
    assert docx_format.comments_section(pkg) is None
    assert docx_format.people_section(pkg) is None
    assert docx_format.tracked_changes_section(pkg) is None


# -- XLSX ---------------------------------------------------------------------------------------


def test_excel_notes_threaded_comments_and_persons():
    pkg = open_package(add_excel_comments(xlsx()).build())
    assert fields(xlsx_format.comments_section(pkg)) == {
        "notes.count": 1, "notes.authors": ["Jane Doe"],
        "threaded.count": 1, "threaded.dates": "2024-05-01T10:00:00.00"}
    assert fields(xlsx_format.people_section(pkg)) == {
        "person.1.name": "Jane Doe", "person.1.userId": "jane.doe@example.com",
        "person.1.providerId": "AD"}


def test_excel_without_comments():
    pkg = open_package(xlsx().build())
    assert xlsx_format.comments_section(pkg) is None and xlsx_format.people_section(pkg) is None


# -- PPTX ---------------------------------------------------------------------------------------


def test_powerpoint_legacy_and_modern_comments():
    pkg = open_package(add_ppt_comments(pptx()).build())
    assert fields(pptx_format.comments_section(pkg)) == {
        "count": 2, "authors": ["Jane Doe"], "initials": ["JD"],
        "dates": "2024-05-01T10:00:00.000 to 2024-05-02T11:00:00.000 (2 dates)"}
    assert fields(pptx_format.people_section(pkg)) == {
        "person.1.name": "Jane Doe", "person.1.initials": "JD",
        "person.2.name": "Jane Doe", "person.2.initials": "JD",
        "person.2.userId": "jane.doe@example.com", "person.2.providerId": "AD"}


def test_powerpoint_without_comments():
    pkg = open_package(pptx().build())
    assert pptx_format.comments_section(pkg) is None and pptx_format.people_section(pkg) is None
