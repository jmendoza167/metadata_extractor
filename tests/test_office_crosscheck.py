"""Open original and stripped Office files with python-docx, openpyxl and
python-pptx, independent readers that metaex never uses itself (a test
enforces that): the text, tables, cell values, sheet names, slide text and
notes must be exactly the same, and the properties strip removes must be
gone as these libraries see them.

These packages are part of the [dev] extra. They are imported at module
level on purpose: if one is missing, these tests fail instead of skipping.
"""

from __future__ import annotations

import datetime
import io

import docx as python_docx
import openpyxl
import pptx as python_pptx
import pytest
from openpyxl.comments import Comment

from helpers import jpeg
from helpers.ooxml import (COMMENTED_BODY, HIDDEN_TEXT_BODY, TRACKED_BODY, WORD_NAMESPACES,
                           add_excel_comments, add_hidden_slide_and_notes, add_media,
                           add_ppt_comments, add_word_comments, docx, kitchen_sink_xlsx_workbook,
                           paragraph, pptx, with_properties, xlsx)
from metaex.core.verify import verify
from metaex.formats import docx as docx_format
from metaex.formats import pptx as pptx_format
from metaex.formats import xlsx as xlsx_format

WHEN = datetime.datetime(2024, 5, 1, 10, 0, 0)


def save(document) -> bytes:
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


# -- files made by the libraries ------------------------------------------------------------------


def library_docx() -> bytes:
    d = python_docx.Document()
    props = d.core_properties
    props.author, props.last_modified_by, props.title = "Jane Doe", "John Example", "Plans"
    props.comments, props.keywords, props.created = "draft", "flat", WHEN
    d.add_heading("Floor plan notes", level=1)
    first = d.add_paragraph("The kitchen is ")
    first.add_run("small").bold = True
    d.add_comment(first.runs, text="Ask about stairs", author="Jane Doe", initials="JD")
    table = d.add_table(rows=2, cols=2)
    for r, row in enumerate(table.rows):
        for c, cell in enumerate(row.cells):
            cell.text = f"cell {r}.{c}"
    d.sections[0].header.paragraphs[0].text = "Draft header"
    d.add_picture(io.BytesIO(jpeg.sample_photo()))
    return save(d)


def library_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    wb.properties.creator, wb.properties.lastModifiedBy = "Jane Doe", "John Example"
    wb.properties.created = WHEN
    ws = wb.active
    ws.title = "Budget"
    ws.append(["Item", "Cost", "When"])
    ws.append(["Rent", 950, WHEN])
    ws.append(["Total", "=SUM(B2:B2)", None])
    ws["B2"].comment = Comment("Check the totals", "Jane Doe")
    hidden = wb.create_sheet("Salaries")
    hidden.sheet_state = "hidden"
    hidden["A1"] = 1234.5
    return save(wb)


def library_pptx() -> bytes:
    p = python_pptx.Presentation()
    p.core_properties.author, p.core_properties.last_modified_by = "Jane Doe", "John Example"
    p.core_properties.created = WHEN
    for n in range(2):
        slide = p.slides.add_slide(p.slide_layouts[1])
        slide.shapes.title.text = f"Slide {n + 1}"
        slide.placeholders[1].text = f"Body text {n + 1}"
        slide.notes_slide.notes_text_frame.text = f"Speaker note {n + 1}"
    return save(p)


# -- files made by the test helpers (comments, tracked changes, hidden content) ---------------------


def helper_docx() -> bytes:
    body = COMMENTED_BODY + TRACKED_BODY + HIDDEN_TEXT_BODY + paragraph("Last line")
    pkg = with_properties(add_word_comments(docx(body, extra_ns=WORD_NAMESPACES)))
    add_media(pkg, "word/document.xml", "word/media/image1.jpeg", jpeg.sample_photo(), "jpeg",
              "image/jpeg")
    return pkg.build()


def helper_xlsx() -> bytes:
    return with_properties(add_excel_comments(kitchen_sink_xlsx_workbook(xlsx()))).build()


def helper_pptx() -> bytes:
    return with_properties(add_ppt_comments(add_hidden_slide_and_notes(pptx()))).build()


# -- what the libraries see -------------------------------------------------------------------------


def docx_content(data: bytes) -> dict:
    d = python_docx.Document(io.BytesIO(data))
    return {
        "paragraphs": [(p.style.name if p.style is not None else None, p.text)
                       for p in d.paragraphs],
        "runs": [[(r.text, r.bold) for r in p.runs] for p in d.paragraphs],
        "tables": [[[cell.text for cell in row.cells] for row in t.rows] for t in d.tables],
        "headers": [p.text for s in d.sections for p in s.header.paragraphs],
        "pictures": len(d.inline_shapes),
        "comments": [c.text for c in d.comments],
    }


def xlsx_content(data: bytes) -> dict:
    wb = openpyxl.load_workbook(io.BytesIO(data))
    return {
        "sheets": [(ws.title, ws.sheet_state) for ws in wb.worksheets],
        "cells": {ws.title: [[c.value for c in row] for row in ws.iter_rows()]
                  for ws in wb.worksheets},
        "comments": {ws.title: [(c.coordinate, c.comment.text) for row in ws.iter_rows()
                                for c in row if c.comment is not None]
                     for ws in wb.worksheets},
    }


def pptx_content(data: bytes) -> dict:
    p = python_pptx.Presentation(io.BytesIO(data))
    slides = []
    for slide in p.slides:
        texts = [s.text_frame.text for s in slide.shapes if s.has_text_frame]
        notes = slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else None
        slides.append({"texts": texts, "notes": notes, "show": slide._element.get("show")})
    return {"slides": slides}


CASES = [
    ("library.docx", library_docx, docx_format, docx_content),
    ("helpers.docx", helper_docx, docx_format, docx_content),
    ("library.xlsx", library_xlsx, xlsx_format, xlsx_content),
    ("helpers.xlsx", helper_xlsx, xlsx_format, xlsx_content),
    ("library.pptx", library_pptx, pptx_format, pptx_content),
    ("helpers.pptx", helper_pptx, pptx_format, pptx_content),
]


@pytest.mark.parametrize("name, build, module, content", CASES, ids=[c[0] for c in CASES])
def test_content_is_unchanged(name, build, module, content):
    original = build()
    cleaned = module.strip(original)
    assert verify(cleaned, original, filename=name).clean
    before, after = content(original), content(cleaned)
    assert after == before
    assert any(before.values())  # the comparison really compared something


@pytest.mark.parametrize("name, build, module, content", CASES, ids=[c[0] for c in CASES])
def test_properties_are_gone(name, build, module, content):
    data = module.strip(build())
    if name.endswith(".docx"):
        props = python_docx.Document(io.BytesIO(data)).core_properties
        found = (props.author, props.last_modified_by, props.title, props.created)
    elif name.endswith(".xlsx"):
        props = openpyxl.load_workbook(io.BytesIO(data)).properties
        # openpyxl reports its own name as the creator when a file has none
        assert props.creator == "openpyxl"
        from metaex.core.ooxml import open_package

        core = open_package(data).xml("docProps/core.xml").root
        assert list(core.elements()) == []
        found = ("", props.lastModifiedBy or "", props.title or "", None)
    else:
        props = python_pptx.Presentation(io.BytesIO(data)).core_properties
        found = (props.author, props.last_modified_by, props.title, props.created)
    assert found == ("", "", "", None)


def test_comments_keep_their_text_with_an_anonymous_author():
    d = python_docx.Document(io.BytesIO(docx_format.strip(library_docx())))
    assert [(c.author, c.initials, c.text) for c in d.comments] == \
        [("Author", None, "Ask about stairs")]
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_format.strip(library_xlsx())))
    comment = wb["Budget"]["B2"].comment
    assert comment.author == "Author" and "Check the totals" in comment.text


def test_removed_comments_are_gone_for_the_libraries_too():
    d = python_docx.Document(io.BytesIO(docx_format.strip(library_docx(), remove_comments=True)))
    assert list(d.comments) == []
    assert docx_content(docx_format.strip(library_docx(), remove_comments=True))["paragraphs"] \
        == docx_content(library_docx())["paragraphs"]
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_format.strip(library_xlsx(),
                                                             remove_comments=True)))
    assert wb["Budget"]["B2"].comment is None and wb["Budget"]["B2"].value == 950


def test_the_cross_check_packages_are_installed():
    """Part of the [dev] extra; a missing package must fail the run, not skip it."""
    assert python_docx.__version__ and openpyxl.__version__ and python_pptx.__version__
