"""Comment and revision authors become "Author" in DOCX, XLSX and PPTX."""

from __future__ import annotations

import re

from helpers.ooxml import (COMMENTED_BODY, TRACKED_BODY, WORD_NAMESPACES, add_excel_comments,
                           add_ppt_comments, add_word_comments, docx, pptx, xlsx)
from metaex.core.ooxml import open_package
from metaex.formats import docx as docx_format
from metaex.formats import pptx as pptx_format
from metaex.formats import xlsx as xlsx_format


def fields(section):
    return {f.key: f.value for f in section.fields} if section is not None else {}


def word() -> bytes:
    return add_word_comments(docx(COMMENTED_BODY + TRACKED_BODY, extra_ns=WORD_NAMESPACES)).build()


def test_word_comment_and_revision_authors():
    cleaned = open_package(docx_format.strip(word()))
    assert fields(docx_format.comments_section(cleaned)) == {"count": 1, "authors": ["Author"]}
    changes = fields(docx_format.tracked_changes_section(cleaned))
    assert changes["authors"] == ["Author"] and "dates" not in changes
    assert changes["insertions"] == 1 and changes["deletions"] == 1  # the changes stay
    assert fields(docx_format.people_section(cleaned)) == {"person.1.name": "Author"}
    comments = cleaned.read("word/comments.xml")
    assert b'w:author="Author"' in comments
    assert b"w:initials" not in comments and b"w:date" not in comments
    assert b"Ask about stairs" in comments  # the comment itself stays
    assert b"dateUtc" not in cleaned.read("word/commentsExtensible.xml")
    people = cleaned.read("word/people.xml")
    assert b"presenceInfo" not in people and b"jane" not in people.lower()
    assert b'mc:Ignorable="w15"' in people


def test_word_document_changes_only_where_it_must():
    original = open_package(word()).read("word/document.xml")
    cleaned = open_package(docx_format.strip(word())).read("word/document.xml")
    expected = (original
                .replace(b'w:author="Jane Doe" w:date="2024-05-01T10:15:00Z"', b'w:author="Author"')
                .replace(b'w:author="John Example" w:date="2024-05-02T09:00:00Z"', b'w:author="Author"')
                .replace(b'w:author="Jane Doe" w:date="2024-05-01T10:16:00Z"', b'w:author="Author"'))
    expected = re.sub(rb' w:rsid[A-Za-z]*="[^"]*"', b"", expected)  # removed too (rsids)
    assert cleaned == expected
    assert b'mc:Ignorable="w14 w15"' in cleaned and b"<w:delText>twelve hundred</w:delText>" in cleaned


def test_excel_comment_authors_and_persons():
    cleaned = open_package(xlsx_format.strip(add_excel_comments(xlsx()).build()))
    found = fields(xlsx_format.comments_section(cleaned))
    assert found == {"notes.count": 1, "notes.authors": ["Author"], "threaded.count": 1}
    assert fields(xlsx_format.people_section(cleaned)) == {"person.1.name": "Author"}
    person = cleaned.read("xl/persons/person.xml")
    assert b"userId" not in person and b"providerId" not in person
    assert b'id="{AAAAAAAA-0000-1111-2222-333333333333}"' in person  # threaded comments use it


def test_powerpoint_comment_authors():
    cleaned = open_package(pptx_format.strip(add_ppt_comments(pptx()).build()))
    found = fields(pptx_format.comments_section(cleaned))
    assert found == {"count": 2, "authors": ["Author"], "dates": "2000-01-01T00:00:00.000"}
    people = fields(pptx_format.people_section(cleaned))
    assert people == {"person.1.name": "Author", "person.2.name": "Author",
                      "person.2.userId": "Author", "person.2.providerId": "None"}
    legacy = cleaned.read("ppt/commentAuthors.xml")
    assert b'name="Author" initials=""' in legacy
    assert b" dt=" not in cleaned.read("ppt/comments/comment1.xml")
    authors = cleaned.read("ppt/authors.xml")
    assert b"initials" not in authors and b"example.com" not in authors


def test_already_anonymous_files_are_not_rewritten():
    once = docx_format.strip(word())
    twice = docx_format.strip(once)
    assert once == twice
