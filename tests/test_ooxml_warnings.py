"""What strip warns about in Office files: what the cleaned copy still contains."""

from __future__ import annotations

from helpers.ooxml import (COMMENTED_BODY, HIDDEN_TEXT_BODY, MS_REL, TRACKED_BODY, VBA,
                           WORD_MACRO, WORD_NAMESPACES, add_embedded_workbook, add_excel_comments,
                           add_external_data, add_glossary, add_hidden_slide_and_notes,
                           add_links_and_template, add_ppt_comments, add_word_comments, docx,
                           kitchen_sink_xlsx_workbook, pptx, xlsx)
from metaex.commands.strip import StripOutcome, summary_lines
from metaex.core import filetype as ft
from metaex.formats import docx as docx_format
from metaex.formats import pptx as pptx_format
from metaex.formats import xlsx as xlsx_format


def warnings_for(module, data: bytes) -> list[str]:
    return module.kept_warnings(module.strip(data))


def one(warnings, text):
    found = [w for w in warnings if text in w]
    assert len(found) == 1, (text, warnings)
    return found[0]


def test_tracked_changes_with_deleted_text_are_critical():
    warnings = warnings_for(docx_format, docx(TRACKED_BODY).build())
    warning = one(warnings, "DELETED TEXT")
    assert warning.startswith("CRITICAL: ") and "14 characters" in warning
    assert "Accept All" in warning and "LibreOffice" in warning
    assert warnings[0] == warning  # the most important first


def test_tracked_changes_without_deleted_text():
    body = ('<w:p><w:ins w:id="1" w:author="Jane Doe"><w:r><w:t>new</w:t></w:r></w:ins></w:p>')
    warning = one(warnings_for(docx_format, docx(body).build()), "tracked changes")
    assert not warning.startswith("CRITICAL") and '"Author"' in warning


def test_word_comments_hidden_text_glossary_macros_objects_and_links():
    pkg = add_word_comments(docx(COMMENTED_BODY + HIDDEN_TEXT_BODY, extra_ns=WORD_NAMESPACES,
                                 main_type=WORD_MACRO))
    add_glossary(pkg)
    add_embedded_workbook(pkg, "word/document.xml", "word")
    pkg.add_linked("word/document.xml", MS_REL + "vbaProject", "word/vbaProject.bin", b"VBA", VBA)
    add_links_and_template(pkg)
    warnings = warnings_for(docx_format, pkg.build())
    assert "--remove-comments" in one(warnings, "kept 1 comment(s)")
    assert "Secret note to self" in one(warnings, "hidden text")
    one(warnings, "glossary document")
    assert "word/vbaProject.bin" in one(warnings, "VBA macros")
    assert "Microsoft_Excel_Worksheet.xlsx" in one(warnings, "embedded object(s)")
    links = one(warnings, "external link(s)")
    assert "fileserver01" in links
    assert not any(w.startswith("CRITICAL") for w in warnings)


def test_excel_warnings():
    pkg = add_external_data(add_excel_comments(kitchen_sink_xlsx_workbook(xlsx())))
    warnings = warnings_for(xlsx_format, pkg.build())
    one(warnings, "kept 2 comment(s)")
    assert "'Jane Doe'" in one(warnings, "comment text starts with the name(s)")
    assert "Salaries" in one(warnings, "hidden sheet(s)")
    assert "Lookup (very hidden)" in one(warnings, "hidden sheet(s)")
    one(warnings, "data connection(s)")
    one(warnings, "external link(s)")


def test_powerpoint_warnings():
    warnings = warnings_for(pptx_format, add_ppt_comments(add_hidden_slide_and_notes(pptx())).build())
    one(warnings, "kept 2 comment(s)")
    assert "slide 2" in one(warnings, "hidden slide(s)")
    one(warnings, "speaker notes of 1 slide(s)")


def test_clean_files_have_no_warnings():
    for module, builder in ((docx_format, docx), (xlsx_format, xlsx), (pptx_format, pptx)):
        assert warnings_for(module, builder().build()) == []


def test_critical_warnings_are_shown_first_in_the_summary():
    outcome = StripOutcome("in.docx", "out.docx", ft.DOCX, 100, 90,
                           warnings=["CRITICAL: deleted text is still there", "kept 1 comment"])
    lines = summary_lines(outcome)
    assert lines[3:6] == ["*** WARNING ***", "deleted text is still there", ""]
    assert "  Warning: kept 1 comment" in lines
    assert not any("Warning: CRITICAL" in line for line in lines)
