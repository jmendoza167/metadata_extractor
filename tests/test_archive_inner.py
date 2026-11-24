"""Files inside archives: show and risk read them (read-only, bounded),
strip without --deep leaves them as they are and warns."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from helpers import gz
from helpers.jpeg import sample_photo
from helpers.ooxml import kitchen_sink_docx
from helpers.tar import build_tar, file
from helpers.zip import Entry, build_zip, make_zip
from metaex.core import archive
from metaex.core.inflate import Budget, sharing
from metaex.core.risk import CRITICAL, assess
from metaex.core.verify import verify
from metaex.core.ziparchive import ArchiveLimitError
from metaex.formats import gzip as gzipformat
from metaex.formats import tar as tarformat
from metaex.formats import zip as zipformat

PHOTO = sample_photo()


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


def titles(found: dict) -> list[str]:
    return [s.title for s in found["sections"]]


def test_zip_files_inside_are_reported_with_their_path():
    found = zipformat.parse(make_zip({"photos/a.jpg": PHOTO, "notes.txt": "plain text"}))
    assert "photos/a.jpg: GPS" in titles(found)
    overview = next(s for s in found["sections"] if s.key == "archive_files")
    values = {f.label: f.value for f in overview.fields}
    assert values["photos/a.jpg"].startswith("JPEG; ") and "metadata field" in values[
        "photos/a.jpg"]
    assert values["notes.txt"] == "unrecognized; not examined: type not recognized"
    assert [i.path for i in found["inner"] if i.leftovers] == ["photos/a.jpg"]


def test_inner_findings_keep_their_levels():
    found = assess(zipformat.parse(make_zip({"a.jpg": PHOTO}))["sections"])
    gps = [f for f in found if f.section == "a.jpg: GPS"]
    assert gps and all(f.level == CRITICAL for f in gps)
    assert not any(f.section_key == "archive_files" for f in found)


def test_tar_and_compressed_files_inside():
    tar = build_tar([file("docs/report.docx", kitchen_sink_docx()), file("a.jpg", PHOTO)])
    found = tarformat.parse(tar)
    assert "docs/report.docx: Document properties" in titles(found)
    found = gzipformat.parse(gz.gzip(tar, name=b"backup.tar"))
    assert "a.jpg: GPS" in titles(found)
    found = gzipformat.parse(gz.gzip(PHOTO, name=b"photo.jpg"))
    assert "photo.jpg: GPS" in titles(found)
    found = gzipformat.parse(gz.gzip(PHOTO))
    assert "(the gzip file's contents): GPS" in titles(found)


def test_nested_archives_up_to_the_limit():
    """Archives inside the archive are opened 3 levels deep, not more."""
    level4 = make_zip({"deep.jpg": PHOTO})
    level3 = make_zip({"l4.zip": level4, "c.jpg": PHOTO})
    level2 = make_zip({"l3.zip": level3})
    level1 = make_zip({"l2.zip": level2})
    found = zipformat.parse(make_zip({"l1.zip": level1}))
    assert "l1.zip: l2.zip: l3.zip: c.jpg: GPS" in titles(found)
    assert not any("deep.jpg" in t for t in titles(found))
    deepest = next(s for s in found["sections"]
                   if s.title == "l1.zip: l2.zip: l3.zip: Files inside")
    assert "read only 3 levels deep" in deepest.get("file_1").value
    found = zipformat.parse(level1)
    assert "l2.zip: l3.zip: l4.zip: deep.jpg: GPS" in titles(found)


def test_damaged_file_inside_is_a_warning():
    found = zipformat.parse(make_zip({"broken.jpg": PHOTO[:200], "ok.jpg": PHOTO}))
    assert "ok.jpg: GPS" in titles(found)
    assert any(w.startswith("broken.jpg") for w in found["warnings"])


def test_junk_and_empty_files_are_not_examined():
    found = zipformat.parse(make_zip({"__MACOSX/._a.jpg": PHOTO, "empty.jpg": b""}))
    assert not any(s.key == "archive_files" for s in found["sections"])


def test_files_inside_share_one_budget():
    data = make_zip({f"{i}.txt": "x" * 4000 for i in range(5)})
    with sharing(Budget(10_000)):
        with pytest.raises(ArchiveLimitError):
            zipformat.parse(data)
    inner = make_zip({"a.txt": "x" * 4000, "b.txt": "y" * 4000})
    with sharing(Budget(14_000)):  # the outer reads 8 KB + the inner zip, then 8 KB inside
        found = zipformat.parse(make_zip({"inner.zip": inner}))
    assert any("can't be read" in w or "could not be examined" in w for w in found["warnings"])


def test_number_of_files_examined_is_limited(monkeypatch):
    monkeypatch.setattr(archive, "MAX_INNER_FILES", 3)
    found = zipformat.parse(make_zip({f"{i}.jpg": PHOTO for i in range(5)}))
    overview = next(s for s in found["sections"] if s.key == "archive_files")
    assert sum("more than 3 files" in f.value for f in overview.fields) == 2


def test_scanning_can_be_turned_off():
    with archive.scan_inner(False):
        found = zipformat.parse(make_zip({"a.jpg": PHOTO}))
    assert not any(s.key == "archive_files" for s in found["sections"])


def test_verify_counts_inner_metadata_as_leftovers():
    cleaned = zipformat.strip(make_zip({"a.jpg": PHOTO}))
    result = verify(cleaned)
    assert not result.clean
    assert any(f.section == "a.jpg: GPS" for f in result.leftovers)
    with archive.scan_inner(False):
        assert verify(cleaned).clean


def test_kept_by_design_inside_follows_the_inner_format():
    from metaex.formats import jpeg

    clean_photo = jpeg.strip(PHOTO)
    result = verify(zipformat.strip(make_zip({"a.jpg": clean_photo})))
    assert result.clean, result.leftovers


def test_each_file_inside_is_judged_by_its_own_format():
    """Regression: every file's rules, not the last file's."""
    from helpers import flac
    from helpers.jpeg import kitchen_sink_jpeg
    from metaex.formats import flac as flacformat
    from metaex.formats import jpeg

    photo = jpeg.strip(kitchen_sink_jpeg())  # keeps its ICC profile, by design
    data = make_zip({"a.jpg": photo, "b.flac": flacformat.strip(flac.flac())})
    result = verify(zipformat.strip(data))
    assert result.clean, [str(f) for f in result.leftovers]
    assert any(f.section == "a.jpg: ICC profile" for f in result.kept_by_design)


def test_a_bomb_inside_does_not_stop_the_report():
    from helpers.deflate import repeat_deflate
    from helpers.ooxml import docx
    from helpers.zip import DEFLATED

    raw, expanded = repeat_deflate(0x20, 8200)
    bomb = build_zip([Entry(n, d) if n != "word/document.xml" else
                      Entry(n, expanded, method=DEFLATED, compressed=raw)
                      for n, d in docx().files().items()])
    found = zipformat.parse(make_zip({"bomb.docx": bomb, "a.jpg": PHOTO}))
    assert "a.jpg: GPS" in titles(found)
    assert any(w.startswith("bomb.docx") and "bomb" in w for w in found["warnings"])


def test_strip_without_deep_warns_and_keeps_files_inside(tmp_path):
    path = tmp_path / "photos.zip"
    path.write_bytes(build_zip([Entry("a.jpg", PHOTO), Entry("b.txt", b"text")]))
    r = metaex("strip", path)
    assert r.returncode == 0, r.stderr
    assert "1 file(s) inside the archive still contain metadata: a.jpg (JPEG)" in r.stdout
    assert "use --deep" in r.stdout
    expected = ("NOT CLEAN: archive metadata clean; 1 file(s) inside still contain metadata "
                "(not cleaned, use --deep)")
    assert f"Verification: {expected}" in r.stdout  # strip still writes the file (exit 0)
    cleaned = tmp_path / "photos_clean.zip"
    from metaex.core.ziparchive import open_archive

    assert open_archive(cleaned.read_bytes()).read("a.jpg") == PHOTO
    r = metaex("verify", cleaned)
    assert r.returncode == 3 and "a.jpg: GPS" in r.stdout
    assert f"Result: {expected}" in r.stdout


def test_cli_risk_groups_findings_by_entry(tmp_path):
    path = tmp_path / "photos.tar"
    path.write_bytes(build_tar([file("trip/a.jpg", PHOTO)]))
    r = metaex("risk", "--json", path)
    found = json.loads(r.stdout)
    sections = {f["section"] for f in found["findings"]}
    assert "trip/a.jpg: GPS" in sections and found["summary"]["critical"] > 0
