"""strip with --remove-comments: comments removed completely from DOCX, XLSX, PPTX."""

from __future__ import annotations

import subprocess
import sys

import pytest

from helpers import jpeg
from helpers.ooxml import (COMMENTED_BODY, R_NS, S_NS, TRACKED_BODY, WORD_NAMESPACES,
                           add_excel_comments, add_ppt_comments, add_word_comments, docx, pptx,
                           worksheet, xlsx)
from metaex.core.errors import CannotCleanError
from metaex.core.ooxml import open_package
from metaex.formats import docx as docx_format
from metaex.formats import pptx as pptx_format
from metaex.formats import xlsx as xlsx_format

VML_TYPE = "application/vnd.openxmlformats-officedocument.vmlDrawing"


def vml(*kinds: str) -> str:
    shapes = "".join(f'<v:shape id="_x0000_s{i}" type="#_x0000_t202"><x:ClientData '
                     f'ObjectType="{k}"><x:Row>0</x:Row></x:ClientData></v:shape>'
                     for i, k in enumerate(kinds, 1025))
    return ('<xml xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:'
            'office:office" xmlns:x="urn:schemas-microsoft-com:office:excel">'
            f"{shapes}</xml>")


def excel_with_notes(*kinds: str):
    kinds = kinds or ("Note",)
    pkg = add_excel_comments(xlsx())
    pkg.defaults["vml"] = VML_TYPE
    rid = pkg.link("xl/worksheets/sheet1.xml", "vmlDrawing", "xl/drawings/vmlDrawing1.vml")
    pkg.add("xl/drawings/vmlDrawing1.vml", vml(*kinds))
    sheet = worksheet().replace("</sheetData>", f'</sheetData><legacyDrawing r:id="{rid}"/>')
    sheet = sheet.replace(f'<worksheet xmlns="{S_NS}">',
                          f'<worksheet xmlns="{S_NS}" xmlns:r="{R_NS}">')
    pkg.parts["xl/worksheets/sheet1.xml"] = sheet.encode("utf-8")
    return pkg


def test_word_comments_are_removed_with_their_anchors():
    data = add_word_comments(docx(COMMENTED_BODY, extra_ns=WORD_NAMESPACES)).build()
    cleaned = open_package(docx_format.strip(data, remove_comments=True))
    names = cleaned.part_names()
    for part in ("word/comments.xml", "word/commentsExtensible.xml", "word/people.xml"):
        assert part not in names
    document = cleaned.read("word/document.xml")
    assert b"comment" not in document.lower().replace(b"commentreference", b"")
    assert b"commentReference" not in document and b"CommentReference" not in document
    assert b"<w:t>Kitchen</w:t>" in document  # the commented text stays
    assert docx_format.comments_section(cleaned) is None
    assert docx_format.people_section(cleaned) is None
    assert not any("comment" in w for w in docx_format.kept_warnings(
        docx_format.strip(data, remove_comments=True)))
    assert not any("comment" in r.type.lower() or "people" in r.type
                   for r in cleaned.all_relationships())


def test_people_stay_when_tracked_changes_refer_to_them():
    data = add_word_comments(docx(COMMENTED_BODY + TRACKED_BODY, extra_ns=WORD_NAMESPACES)).build()
    cleaned = open_package(docx_format.strip(data, remove_comments=True))
    assert "word/people.xml" in cleaned.part_names()
    assert {f.value for f in docx_format.people_section(cleaned).fields} == {"Author"}


def test_a_comment_mark_inside_a_run_with_text_keeps_the_text():
    body = ('<w:p><w:commentRangeStart w:id="0"/><w:r><w:t>Kitchen</w:t>'
            '<w:commentReference w:id="0"/></w:r><w:commentRangeEnd w:id="0"/></w:p>')
    data = add_word_comments(docx(body), people=False).build()
    document = open_package(docx_format.strip(data, remove_comments=True)).read(
        "word/document.xml")
    assert b"<w:p><w:r><w:t>Kitchen</w:t></w:r></w:p>" in document


def test_excel_notes_drawings_threaded_comments_and_persons_are_removed():
    cleaned = open_package(xlsx_format.strip(excel_with_notes().build(), remove_comments=True))
    names = cleaned.part_names()
    for part in ("xl/comments1.xml", "xl/threadedComments/threadedComment1.xml",
                 "xl/persons/person.xml", "xl/drawings/vmlDrawing1.vml"):
        assert part not in names, part
    sheet = cleaned.read("xl/worksheets/sheet1.xml")
    assert b"legacyDrawing" not in sheet and b"<sheetData>" in sheet
    assert xlsx_format.comments_section(cleaned) is None
    assert xlsx_format.people_section(cleaned) is None


def test_excel_drawing_with_other_objects_is_refused():
    with pytest.raises(CannotCleanError, match="also holds other objects \\(Button\\)"):
        xlsx_format.strip(excel_with_notes("Note", "Button").build(), remove_comments=True)
    # without --remove-comments the same file is fine
    xlsx_format.strip(excel_with_notes("Note", "Button").build())


def test_powerpoint_comments_and_authors_are_removed():
    cleaned = open_package(pptx_format.strip(add_ppt_comments(pptx()).build(),
                                             remove_comments=True))
    assert not any("comment" in n.lower() or n == "ppt/authors.xml"
                   for n in cleaned.part_names())
    assert pptx_format.comments_section(cleaned) is None
    assert pptx_format.people_section(cleaned) is None


def test_without_the_option_comments_stay():
    data = add_word_comments(docx(COMMENTED_BODY, extra_ns=WORD_NAMESPACES)).build()
    assert "word/comments.xml" in open_package(docx_format.strip(data)).part_names()


def test_option_is_refused_for_images(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(jpeg.sample_photo())
    r = subprocess.run([sys.executable, "-m", "metaex", "strip", str(path), "--remove-comments"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1
    assert "--remove-comments is not available for JPEG files" in r.stderr
    assert [p.name for p in tmp_path.iterdir()] == ["photo.jpg"]
