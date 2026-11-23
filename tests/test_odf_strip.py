"""Stripping and verifying OpenDocument files (ODT, ODS, ODP)."""

from __future__ import annotations

import pytest

from helpers import jpeg
from helpers.odf import Document, kitchen_sink, with_meta
from helpers.zip import Entry, build_zip
from metaex.core import odf
from metaex.core.errors import CannotCleanError
from metaex.core.odf import open_odf
from metaex.core.ooxml_verify import canonical
from metaex.core.verify import verify
from metaex.core.ziparchive import open_archive
from metaex.formats import odp as odp_format
from metaex.formats import ods as ods_format
from metaex.formats import odt as odt_format

MODULES = {"odt": odt_format, "ods": ods_format, "odp": odp_format}


def parsed_keys(kind: str, data: bytes) -> set[str]:
    return {s.key for s in MODULES[kind].parse(data)["sections"]}


@pytest.mark.parametrize("kind", sorted(MODULES))
@pytest.mark.parametrize("remove_comments", [False, True])
def test_strip_then_verify(kind, remove_comments):
    original = kitchen_sink(kind)
    cleaned = MODULES[kind].strip(original, remove_comments=remove_comments)
    result = verify(cleaned, original, filename=f"x.{kind}")
    assert result.clean, (result.problems, [str(f) for f in result.leftovers])
    assert result.image_data_match is True
    keys = parsed_keys(kind, cleaned)
    for gone in ("odf_meta", "odf_user_defined", "local_paths", "printer_settings",
                 "revision_ids", "office_thumbnail", "odf_configurations", "gps"):
        assert gone not in keys, gone
    assert ("office_comments" in keys) is not remove_comments


def test_what_changes_in_the_files():
    cleaned = open_odf(odt_format.strip(kitchen_sink("odt")))
    names = cleaned.names()
    assert names[0] == "mimetype" and cleaned.archive.entries[0].method == 0
    assert not any(n.startswith(("Thumbnails/", "Configurations2/")) for n in names)
    assert "Basic/Standard/Module1.xml" in names  # macros stay
    meta = cleaned.read("meta.xml")
    assert b"Jane" not in meta and b"<office:meta></office:meta>" in meta
    settings = cleaned.read("settings.xml")
    assert b"Printer" not in settings and b"Rsid" not in settings
    assert b'config:name="ViewAreaTop"' in settings
    content = cleaned.read("content.xml")
    assert b"Jane" not in content and b"John" not in content and b"officeooo:rsid" not in content
    assert b"<dc:creator>Author</dc:creator>" in content
    assert b"<dc:date>2000-01-01T00:00:00</dc:date>" in content  # tracked change: required
    assert b"2024-05-01T10:00:00" not in content  # the comment's date is gone
    assert b"creator-initials" not in content
    assert b"<text:p>twelve hundred</text:p>" in content  # deleted text is content: kept (with a warning)
    manifest = cleaned.read("META-INF/manifest.xml")
    assert b"Thumbnails" not in manifest and b"Configurations2" not in manifest
    picture = cleaned.read("Pictures/10000000000000300000002096C8C1A2.jpg")
    assert b"Exif" not in picture


def test_removed_comments_leave_the_text_joined():
    content = open_odf(odt_format.strip(kitchen_sink("odt"), remove_comments=True)).read(
        "content.xml")
    assert b"annotation" not in content
    assert b"The kitchen is small." in content


def test_warnings():
    warnings = odt_format.kept_warnings(odt_format.strip(kitchen_sink("odt")))
    assert warnings[0].startswith("CRITICAL: ") and "DELETED TEXT" in warnings[0]
    assert any("kept 1 comment(s)" in w for w in warnings)
    assert any("macros (Basic/Standard/Module1.xml)" in w for w in warnings)
    assert any("hidden text" in w for w in warnings)
    assert any("external link(s)" in w for w in warnings)
    ods = ods_format.kept_warnings(ods_format.strip(kitchen_sink("ods")))
    assert any("hidden sheet" in w for w in ods)
    odp = odp_format.kept_warnings(odp_format.strip(kitchen_sink("odp")))
    assert any("hidden slide" in w and "speaker notes" in w for w in odp)


def test_unlisted_files_and_signatures_are_removed():
    doc = Document("odt")
    doc.unlisted["secret.txt"] = b"hidden"
    doc.unlisted["META-INF/documentsignatures.xml"] = b"<signatures>Jane Doe</signatures>"
    cleaned = open_odf(odt_format.strip(doc.build()))
    assert sorted(cleaned.names()) == ["META-INF/manifest.xml", "content.xml", "mimetype",
                                       "styles.xml"]


def test_mimetype_is_moved_first_and_stored():
    entries = Document("ods").entries()
    entries = entries[1:] + [Entry("mimetype", entries[0].data, method=8)]
    cleaned = ods_format.strip(build_zip(entries))
    archive = open_archive(cleaned)
    assert archive.entries[0].name == "mimetype" and archive.entries[0].method == 0
    assert cleaned[30:38] == b"mimetype"


def test_links_into_a_user_folder_are_refused():
    body = ('<text:p><draw:frame><draw:image xlink:href="file:///home/jane.doe/Pictures/a.png" '
            'xlink:type="simple"/></draw:frame></text:p>')
    with pytest.raises(CannotCleanError, match="links to files in a user's folder"):
        odt_format.strip(Document("odt", body).build())


def test_pictures_metaex_cannot_clean_are_kept_with_a_warning():
    doc = Document("odt").add("Pictures/a.svm", b"VCLMTF\x00\x00", "image/x-vclmtf")
    cleaned = odt_format.strip(doc.build())
    assert open_odf(cleaned).read("Pictures/a.svm") == b"VCLMTF\x00\x00"
    assert any("picture(s) metaex can't clean" in w for w in odt_format.kept_warnings(cleaned))


def test_damaged_pictures_are_refused():
    doc = Document("odt").add("Pictures/a.jpg", jpeg.sample_photo()[:200], "image/jpeg")
    with pytest.raises(CannotCleanError, match="Pictures/a.jpg"):
        odt_format.strip(doc.build())


def test_tampering_is_detected():
    original = kitchen_sink("odt")
    archive = open_archive(odt_format.strip(original))
    files = {n: archive.read(n) for n in archive.names()}
    files["content.xml"] = files["content.xml"].replace(b"is small", b"is large")
    result = verify(build_zip([Entry(n, d) for n, d in files.items()]), original, filename="x.odt")
    assert not result.clean
    assert any("content of content.xml differs" in p for p in result.problems)
    del files["styles.xml"]
    result = verify(build_zip([Entry(n, d) for n, d in files.items()]), original, filename="x.odt")
    assert any("lists styles.xml, which is not in the archive" in p for p in result.problems)


def test_originals_are_not_clean():
    result = verify(kitchen_sink("odp"), filename="x.odp")
    assert not result.clean and any(f.label == "Author (initial creator)"
                                    for f in result.leftovers)


def test_canonical_joins_text_around_a_removed_element():
    """Regression: text on both sides of a left-out element must compare
    equal to the joined text the cleaned copy has."""
    ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
          'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"')
    before = (f"<text:p {ns}>The kitchen <office:annotation><text:p>x</text:p>"
              "</office:annotation>is small.</text:p>").encode()
    after = f"<text:p {ns}>The kitchen is small.</text:p>".encode()
    from metaex.core.odf_verify import ODF_RULES

    assert canonical(before, "a", ODF_RULES) == canonical(after, "b", ODF_RULES)


def test_clean_documents_stay_clean():
    for kind, module in MODULES.items():
        original = Document(kind).build()
        cleaned = module.strip(original)
        assert verify(cleaned, original, filename=f"x.{kind}").clean
        assert module.kept_warnings(cleaned) == []


def test_meta_of_embedded_objects_is_emptied():
    doc = with_meta(Document("odt"))
    from helpers.odf import content_xml, meta_xml

    doc.add("Object 1/content.xml", content_xml("odt"), "text/xml")
    doc.add("Object 1/meta.xml", meta_xml(), "text/xml")
    original = doc.build()
    cleaned = odt_format.strip(original)
    assert b"Jane" not in open_odf(cleaned).read("Object 1/meta.xml")
    assert verify(cleaned, original, filename="x.odt").clean
    assert odf.MANIFEST
