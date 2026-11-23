"""Verifying cleaned DOCX, XLSX and PPTX files (core/ooxml_verify.py)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from helpers import jpeg, png
from helpers.ooxml import (COMMENTED_BODY, CUSTOM_TYPE, RSIDS, SETTINGS_TYPE, TRACKED_BODY,
                           WORD_NAMESPACES, add_custom_xml, add_excel_comments, add_media,
                           add_ppt_comments, add_thumbnail, add_word_comments, custom_xml,
                           doc_vars, docx, kitchen_sink_xlsx_workbook, pptx, settings_xml,
                           with_properties, xlsx)
from helpers.zip import Entry, build_zip, make_zip
from metaex.core.ooxml_verify import canonical
from metaex.core.verify import verify
from metaex.core.ziparchive import open_archive
from metaex.formats import docx as docx_format
from metaex.formats import pptx as pptx_format
from metaex.formats import xlsx as xlsx_format

SEP = "\\"


def word() -> bytes:
    pkg = with_properties(add_word_comments(docx(COMMENTED_BODY + TRACKED_BODY,
                                                 extra_ns=WORD_NAMESPACES)))
    pkg.add_linked("", "custom-properties", "docProps/custom.xml", custom_xml(), CUSTOM_TYPE)
    add_custom_xml(pkg, "word/document.xml")
    add_thumbnail(pkg, jpeg.sample_photo())
    pkg.add_linked("word/document.xml", "settings", "word/settings.xml",
                   settings_xml(RSIDS + doc_vars(Client="Jane Doe")), SETTINGS_TYPE)
    add_media(pkg, "word/document.xml", "word/media/image1.jpeg", jpeg.sample_photo(), "jpeg",
              "image/jpeg")
    add_media(pkg, "word/document.xml", "word/media/image2.png", png.kitchen_sink_png(), "png",
              "image/png")
    return pkg.build()


def excel() -> bytes:
    pkg = with_properties(add_excel_comments(kitchen_sink_xlsx_workbook(xlsx())))
    add_media(pkg, "xl/workbook.xml", "xl/media/image1.jpeg", jpeg.sample_photo(), "jpeg",
              "image/jpeg")
    return pkg.build()


def powerpoint() -> bytes:
    pkg = with_properties(add_ppt_comments(pptx()))
    add_media(pkg, "ppt/slides/slide1.xml", "ppt/media/image1.jpeg", jpeg.sample_photo(),
              "jpeg", "image/jpeg")
    return pkg.build()


CASES = [("letter.docx", word, docx_format), ("budget.xlsx", excel, xlsx_format),
         ("talk.pptx", powerpoint, pptx_format)]


@pytest.mark.parametrize("name, builder, module", CASES)
@pytest.mark.parametrize("remove_comments", [False, True])
def test_stripped_files_verify_clean(name, builder, module, remove_comments):
    original = builder()
    cleaned = module.strip(original, remove_comments=remove_comments)
    result = verify(cleaned, original, filename=name)
    assert result.clean, (result.leftovers, result.problems)
    assert result.image_data_match is True and result.image_label == "Document content"
    assert result.image_sha256 == result.original_image_sha256


@pytest.mark.parametrize("name, builder, module", CASES)
def test_originals_are_not_clean(name, builder, module):
    result = verify(builder(), filename=name)
    assert not result.clean
    labels = {f.label for f in result.leftovers}
    assert "Author" in labels and "Created" in labels


def test_verify_without_an_original():
    result = verify(docx_format.strip(word()), filename="letter.docx")
    assert result.clean and result.image_data_match is None and result.image_sha256
    assert any("DELETED TEXT" in n for n in result.notes)


# -- tampering ----------------------------------------------------------------------------------


def tampered(cleaned: bytes, **changes) -> bytes:
    archive = open_archive(cleaned)
    files = {n: archive.read(n) for n in archive.names()}
    for name, data in changes.items():
        name = name.replace("__", "/").replace("CT", "[Content_Types].xml")
        if data is None:
            files.pop(name, None)
        else:
            files[name] = data
    return make_zip(files)


def problems(cleaned: bytes, original: bytes) -> list[str]:
    result = verify(cleaned, original, filename="x.docx")
    assert not result.clean
    return result.problems + [str(f) for f in result.leftovers]


def test_changed_text_is_detected():
    original = word()
    cleaned = docx_format.strip(original)
    document = open_archive(cleaned).read("word/document.xml").replace(b"Kitchen", b"Kitchn")
    found = problems(tampered(cleaned, word__document_xml=None, **{"word/document.xml": document}),
                     original)
    assert any("content of word/document.xml differs" in p for p in found)


def test_missing_and_added_parts_are_detected():
    original = word()
    cleaned = docx_format.strip(original)
    without = tampered(cleaned, **{"word/media/image2.png": None})
    assert any("relationship" in p and "missing part" in p for p in problems(without, original))
    assert any("part word/media/image2.png of the original is missing" in p
               for p in problems(without, original))
    extra = tampered(cleaned, **{"word/extra.xml": b"<x/>"})
    found = problems(extra, original)
    assert any("not in the original" in p for p in found)
    assert any("no content type" in p or "Unused parts" in p for p in found)


def test_override_without_a_part_is_detected():
    original = docx().build()
    cleaned = docx_format.strip(original)
    ct = open_archive(cleaned).read("[Content_Types].xml").replace(
        b"</Types>", b'<Override PartName="/ghost.xml" ContentType="application/xml"/></Types>')
    found = problems(tampered(cleaned, **{"[Content_Types].xml": ct}), original)
    assert any("names a part that doesn't exist (/ghost.xml)" in p for p in found)


def test_a_real_author_left_in_a_comment_is_a_leftover():
    original = word()
    cleaned = docx_format.strip(original)
    comments = open_archive(cleaned).read("word/comments.xml").replace(
        b'w:author="Author"', b'w:author="Jane Doe"')
    found = problems(tampered(cleaned, **{"word/comments.xml": comments}), original)
    assert any("Comment authors = Jane Doe" in p for p in found)


def test_unbound_custom_xml_is_a_leftover():
    original = docx().build()
    pkg = docx()
    add_custom_xml(pkg, "word/document.xml")
    found = problems(pkg.build(), original)
    assert any("customXml/item1.xml" in p and "not shown by any content control" in p
               for p in found)


def test_zip_comment_and_entry_times_are_leftovers():
    original = docx().build()
    archive = open_archive(docx_format.strip(original))
    data = build_zip([Entry(n, archive.read(n), date=0x58A1) for n in archive.names()],
                     comment=b"made by Jane")
    found = problems(data, original)
    assert any("ZIP archive comment" in p for p in found)
    assert any("Entry modification times" in p for p in found)


def test_not_a_package_is_an_error():
    from metaex.core.errors import MalformedFileError

    with pytest.raises(MalformedFileError, match="wrong root element"):
        verify(make_zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>"}),
               docx().build(), filename="x.docx")


# -- canonical form -----------------------------------------------------------------------------

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def doc(body: str, ns: str = "") -> bytes:
    return f'<w:document xmlns:w="{W}"{ns}><w:body>{body}</w:body></w:document>'.encode()


def test_canonical_ignores_what_strip_changes():
    before = doc('<w:p w:rsidR="00A1"><w:commentRangeStart w:id="0"/><w:r><w:t>Hi</w:t></w:r>'
                 '<w:commentRangeEnd w:id="0"/><w:r><w:rPr/><w:commentReference w:id="0"/></w:r>'
                 '<w:ins w:id="1" w:author="Jane" w:date="2024-01-01T00:00:00Z"><w:r>'
                 "<w:t>x</w:t></w:r></w:ins></w:p>")
    after = doc('<w:p><w:r><w:t>Hi</w:t></w:r><w:ins w:id="1" w:author="Author"><w:r>'
                "<w:t>x</w:t></w:r></w:ins></w:p>")
    assert canonical(before, "a") == canonical(after, "b")


def test_canonical_ignores_prefixes_and_formatting_whitespace():
    a = doc("<w:p>\n  <w:r><w:t xml:space='preserve'> a </w:t></w:r>\n</w:p>")
    b = (f'<x:document xmlns:x="{W}"><x:body><x:p><x:r><x:t xml:space="preserve"> a </x:t>'
         "</x:r></x:p></x:body></x:document>").encode()
    assert canonical(a, "a") == canonical(b, "b")


@pytest.mark.parametrize("change", [
    ("<w:t>Hi</w:t>", "<w:t>Ho</w:t>"),
    ("<w:t>Hi</w:t>", "<w:t>Hi </w:t>"),
    ('<w:p w:rsidR="00A1">', '<w:p w:rsidR="00A1" w:x="1">'),
    ("<w:r><w:t>Hi</w:t></w:r>", ""),
    ('w:id="1"', 'w:id="2"'),
])
def test_canonical_sees_content_changes(change):
    base = ('<w:p w:rsidR="00A1"><w:r><w:t>Hi</w:t></w:r><w:ins w:id="1" w:author="A">'
            "<w:r><w:t>x</w:t></w:r></w:ins></w:p>")
    assert canonical(doc(base), "a") != canonical(doc(base.replace(*change)), "b")


# -- the command line -----------------------------------------------------------------------------


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


@pytest.mark.parametrize("name, builder, module", CASES)
def test_strip_and_verify_on_the_command_line(name, builder, module, tmp_path):
    path = tmp_path / name
    path.write_bytes(builder())
    r = metaex("strip", path, "--json")
    assert r.returncode == 0, r.stderr
    outcome = json.loads(r.stdout)
    assert outcome["verify"]["clean"] is True and outcome["verify"]["image_data_match"] is True
    cleaned = tmp_path / name.replace(".", "_clean.")
    v = metaex("verify", cleaned, "--original", path)
    assert v.returncode == 0, v.stdout
    assert "Document content: matches the original" in v.stdout


def test_strip_refuses_links_into_a_user_folder(tmp_path):
    pkg = docx()
    pkg.link("word/document.xml", "hyperlink",
             f"file:///C:{SEP}Users{SEP}jane.doe{SEP}notes.docx", external=True)
    path = tmp_path / "links.docx"
    path.write_bytes(pkg.build())
    r = metaex("strip", path)
    assert r.returncode == 1 and "user's folder" in r.stderr and "no file written" in r.stderr
    assert [p.name for p in tmp_path.iterdir()] == ["links.docx"]
