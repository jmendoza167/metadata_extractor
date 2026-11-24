"""--deep with every format metaex cleans, inside ZIP, TAR and compressed
files; files it must refuse inside; nesting and size limits."""

from __future__ import annotations

import pytest

from helpers import flac, gif, gz, mp3, odf, ooxml, png, svg, wav, webp, xz
from helpers.deflate import repeat_deflate
from helpers.jpeg import kitchen_sink_jpeg, sample_photo
from helpers.tar import build_tar, file
from helpers.zip import DEFLATED, Entry, build_zip, make_zip
from metaex.core import archive, compress
from metaex.core import filetype as ft
from metaex.core import tar as tarcore
from metaex.core.inflate import Budget, sharing
from metaex.core.verify import verify
from metaex.core.ziparchive import open_archive
from metaex.formats import tar as tarformat
from metaex.formats import xz as xzformat
from metaex.formats import zip as zipformat


def inner_files() -> dict[str, bytes]:
    with png.stored_deflate():
        return {
            "photo.jpg": kitchen_sink_jpeg(),
            "image.png": png.make_png(png.text("Author", "Jane Doe"), width=8, height=8),
            "anim.gif": gif.make_gif(gif.comment("Jane Doe"), gif.image(4, 4), width=4,
                                     height=4),
            "pic.webp": webp.animated(),
            "plan.svg": svg.simple_svg('<metadata>Jane Doe</metadata><rect width="1" '
                                       'height="1"/>'),
            "letter.docx": ooxml.kitchen_sink_docx(),
            "sheet.xlsx": ooxml.kitchen_sink_xlsx(),
            "slides.pptx": ooxml.kitchen_sink_pptx(),
            "notes.odt": odf.kitchen_sink("odt"),
            "song.mp3": mp3.kitchen_sink_mp3(junk=False, count=10),
            "take.wav": wav.kitchen_sink_wav(),
            "tone.flac": flac.kitchen_sink_flac(),
        }


FILES = inner_files()


def check_inside(names_and_data: dict[str, bytes]) -> None:
    for name, data in names_and_data.items():
        result = verify(data, FILES[name])
        assert result.clean and result.image_data_match, (name, result.leftovers,
                                                           result.problems)


def test_every_format_inside_a_zip():
    data = build_zip([Entry(n, d, method=DEFLATED) for n, d in FILES.items()])
    with archive.deep_cleaning() as session:
        cleaned = zipformat.strip(data, deep=True)
    assert session.copied == []
    # files without metadata (the animated WebP) come out unchanged: not "cleaned"
    assert set(FILES) - {p for p, _ in session.cleaned} == {"pic.webp"}
    out = open_archive(cleaned)
    check_inside({e.name: out.read(e) for e in out.entries})
    result = verify(cleaned, data)
    assert result.clean and result.image_data_match


def test_every_format_inside_a_tar_xz():
    data = xz.xz(build_tar([file(n, d) for n, d in FILES.items()]))
    cleaned = xzformat.strip(data, deep=True)
    inner = tarcore.open_tar(compress.open_compressed(cleaned).content)
    check_inside({e.name: inner.read(e) for e in inner.entries})
    assert verify(cleaned).clean
    assert verify(cleaned, data).image_data_match


def test_office_options_keep_their_defaults_inside():
    """Comments in an Office file inside are kept with "Author" (there is no
    --remove-comments for archives), and the kept warnings say so."""
    data = make_zip({"letter.docx": FILES["letter.docx"]})
    cleaned = open_archive(zipformat.strip(data, deep=True)).read("letter.docx")
    from metaex.formats import docx

    assert any("comment" in w for w in docx.kept_warnings(cleaned))


@pytest.mark.parametrize("name, data, reason", [
    ("doc.pdf", b"%PDF-1.7\n", "metaex can't clean PDF files"),
    ("old.doc", None, "legacy Word 97-2003 documents"),
    ("book.epub", make_zip({"mimetype": "application/epub+zip"}), "EPUB e-books"),
    ("app.jar", make_zip({"META-INF/MANIFEST.MF": "x"}), "Java archives"),
    ("broken.png", None, "CRC"),
    ("data.bin", b"\x00\x01\x02", "type not recognized"),
    ("bomb.docx", None, "bomb"),
])
def test_files_metaex_must_not_clean_are_copied_and_listed(name, data, reason):
    if name == "old.doc":
        from helpers.ole2 import legacy_word

        data = legacy_word()
    elif name == "broken.png":
        data = png.make_png(png.chunk("tEXt", b"Author\x00Jane", crc=1), width=4, height=4)
    elif name == "bomb.docx":
        raw, expanded = repeat_deflate(0x20, 8200)
        files = ooxml.docx().files()
        data = build_zip([Entry(n, d) if n != "word/document.xml" else
                          Entry(n, expanded, method=DEFLATED, compressed=raw)
                          for n, d in files.items()])
    container = make_zip({name: data, "photo.jpg": sample_photo()})
    with archive.deep_cleaning() as session:
        cleaned = zipformat.strip(container, deep=True)
    assert [p for p, _ in session.copied] == [name]
    assert reason in session.copied[0][1]
    assert [p for p, _ in session.cleaned] == ["photo.jpg"]
    assert open_archive(cleaned).read(name) == data
    warning = archive.deep_warnings(session)[0]
    assert warning.startswith("--deep copied 1 file(s) inside unchanged") and name in warning


def test_three_levels_of_nesting_are_cleaned():
    photo = sample_photo()
    # d.jpg sits in the third archive inside (c.tgz is one archive: a compressed TAR)
    data = make_zip({"a.zip": make_zip({"b.tar": build_tar([file("c.tgz", gz.gzip(
        build_tar([file("d.jpg", photo)])))])})})
    with archive.deep_cleaning() as session:
        cleaned = zipformat.strip(data, deep=True)
    assert [p for p, _ in session.cleaned] == [
        "a.zip > b.tar > c.tgz > d.jpg", "a.zip > b.tar > c.tgz", "a.zip > b.tar", "a.zip"]
    assert session.copied == []
    assert verify(cleaned).clean
    # one more level: the ZIP in the gzip file is a fourth archive, copied as it is
    data = make_zip({"a.zip": make_zip({"b.tar": build_tar([file("c.gz", gz.gzip(
        make_zip({"d.jpg": photo})))])})})
    with archive.deep_cleaning() as session:
        zipformat.strip(data, deep=True)
    assert session.copied == [("a.zip > b.tar > c.gz > (the gzip file's contents)",
                               "archives are cleaned only 3 levels deep")]


def test_deep_shares_the_size_budget():
    data = make_zip({f"{i}.jpg": sample_photo() for i in range(3)})
    with sharing(Budget(len(sample_photo()) * 2 + 100)):
        with pytest.raises(Exception, match="limit|bomb"):
            zipformat.strip(data, deep=True)


def test_number_of_files_cleaned_is_limited(monkeypatch):
    monkeypatch.setattr(archive, "MAX_INNER_FILES", 2)
    data = make_zip({f"{i}.jpg": sample_photo() for i in range(3)})
    with archive.deep_cleaning() as session:
        zipformat.strip(data, deep=True)
    assert len(session.cleaned) == 2
    assert session.copied == [("2.jpg", "more than 2 files inside")]


def test_detected_types_inside_do_not_trust_names():
    data = make_zip({"photo.txt": sample_photo()})
    with archive.deep_cleaning() as session:
        cleaned = zipformat.strip(data, deep=True)
    assert session.cleaned == [("photo.txt", "JPEG")]
    assert ft.detect(open_archive(cleaned).read("photo.txt")) == ft.JPEG


def test_tar_with_replaced_and_junk_files_deep():
    data = build_tar([file("a.jpg", b"old version"), file("a.jpg", sample_photo()),
                      file(".DS_Store", b"\x00\x00\x00\x01Bud1")])
    cleaned = tarformat.strip(data, deep=True)
    result = tarcore.open_tar(cleaned)
    assert [e.name for e in result.entries] == ["a.jpg"]
    assert verify(result.read(result.entries[0])).clean
