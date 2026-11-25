"""--csv: one row per finding, UTF-8 with BOM, proper quoting, and no
formula injection (metadata and file names are attacker-controlled)."""

import csv
import io
import subprocess
import sys

import pytest

from helpers.jpeg import kitchen_sink_jpeg
from helpers.png import itxt, make_png, text
from metaex.core.csvout import BOM, HEADER, CsvWriter, protect, render
from metaex.formats import jpeg

JPEG = kitchen_sink_jpeg()


def run(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)], input=b"",
                          capture_output=True, timeout=180)


def parse(raw: bytes):
    assert raw.startswith(BOM.encode("utf-8"))
    text_ = raw.decode("utf-8")[1:]
    return list(csv.reader(io.StringIO(text_, newline="")))


# -- protection and quoting --------------------------------------------------------------


@pytest.mark.parametrize("value", ["=1+1", "+1", "-1", "@SUM(A1)",
                                   '=HYPERLINK("http://evil","x")', "=cmd|' /C calc'!A0"])
def test_formula_starts_get_an_apostrophe(value):
    assert protect(value) == "'" + value


@pytest.mark.parametrize("value, shown", [
    ("\tx", r"\tx"), ("\rx", r"\rx"),
    ("Au\x1b]0;title\x07\x1b[2Jthor", r"Au\x1b]0;title\x07\x1b[2Jthor"),
    ("gpj.exe\u202e", r"gpj.exe\u202e"),
])
def test_control_characters_are_escaped(value, shown):
    """CSV often goes straight to a terminal: escape sequences and bidi
    controls in file data must not reach it (regression test)."""
    assert protect(value) == shown


@pytest.mark.parametrize("value", ["plain", "1=1", " =1", "a+b", "", "'quoted", "€uro"])
def test_other_values_are_unchanged(value):
    assert protect(value) == value


def test_none_is_empty():
    assert protect(None) == ""


def test_quoting_of_commas_quotes_and_newlines():
    out = render([("a,b", 'say "hi"', "line1\nline2", "=1", "x", "low")])
    lines = out.split("\r\n")
    assert lines[0] == ",".join(HEADER)
    rows = list(csv.reader(io.StringIO(out, newline="")))
    # a newline is shown escaped, as in the text output: one line per row
    assert rows[1] == ["a,b", 'say "hi"', r"line1\nline2", "'=1", "x", "low"]
    assert '"a,b","say ""hi""",line1\\nline2' in out


def test_writer_writes_utf8_bytes_with_one_bom_and_one_header():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", newline="\n")  # like a redirected console
    writer = CsvWriter(stream)
    writer.write([("p1", "JPEG", "EXIF", "Artist", "Jürgen 日本", "high")])
    writer.write([("p2", "PNG", "Text", "Author", "x", "high")])
    data = raw.getvalue()
    assert data.count(BOM.encode("utf-8")) == 1 and data.startswith(BOM.encode("utf-8"))
    assert data.count(b"path,format") == 1
    assert b"\r\r\n" not in data and data.count(b"\r\n") == 3
    assert parse(data)[1][4] == "Jürgen 日本"


# -- the commands ----------------------------------------------------------------------------


def test_risk_csv_for_one_file(tmp_path):
    p = tmp_path / "photo.jpg"
    p.write_bytes(JPEG)
    r = run("risk", p, "--csv")
    assert r.returncode == 0
    rows = parse(r.stdout)
    assert tuple(rows[0]) == HEADER
    assert all(row[0] == str(p) and row[1] == "JPEG" for row in rows[1:])
    assert {row[5] for row in rows[1:]} <= {"critical", "high", "medium", "low"}
    assert ["EXIF", "Make"] in [row[2:4] for row in rows[1:]]


def test_show_csv_has_every_field(tmp_path):
    p = tmp_path / "photo.jpg"
    p.write_bytes(JPEG)
    rows = parse(run("show", p, "--csv").stdout)
    sections = {row[2] for row in rows[1:]}
    assert {"File type", "EXIF", "File system", "Hashes"} <= sections
    hashes = [row for row in rows if row[2] == "Hashes"]
    assert hashes and all(row[5] == "" for row in hashes)  # not metadata: no risk level
    gps = [row for row in rows if row[2] == "GPS"]
    assert gps and all(row[5] == "critical" for row in gps)


def test_verify_csv_lists_leftovers_with_their_level(tmp_path):
    p = tmp_path / "photo.jpg"
    p.write_bytes(JPEG)
    r = run("verify", p, "--csv")
    assert r.returncode == 3
    rows = parse(r.stdout)
    assert len(rows) > 10
    assert ["EXIF", "Make", "ExampleCam", "high"] in [row[2:] for row in rows]
    clean = tmp_path / "clean.jpg"
    clean.write_bytes(jpeg.strip(JPEG))
    r = run("verify", clean, "--csv")
    assert r.returncode == 0 and parse(r.stdout) == [list(HEADER)]


def test_metadata_formulas_are_neutralized(tmp_path):
    p = tmp_path / "evil.png"
    p.write_bytes(make_png(text("Author", "=cmd|' /C calc'!A0"),
                           itxt("Comment", '@SUM(1,2)\n=HYPERLINK("http://x")'),
                           text("Title", "-2+3")))
    rows = parse(run("risk", p, "--csv").stdout)
    values = [row[4] for row in rows[1:]]
    assert "'=cmd|' /C calc'!A0" in values
    assert "'-2+3" in values
    assert any(v.startswith("'@SUM(1,2)") for v in values)
    for row in rows[1:]:
        for cell in row:
            assert not cell.startswith(("=", "+", "-", "@", "\t", "\r"))


@pytest.mark.parametrize("name", ["=1+1.png", "+x.png", "-x.png", "@x.png"])
def test_file_names_are_neutralized_too(tmp_path, name):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / name).write_bytes(make_png(text("Author", "x")))
    r = run("risk", folder, "--csv")
    rows = parse(r.stdout)
    assert rows[1:]  # paths start with the folder; the cells are still checked
    for row in rows[1:]:
        assert not row[0].startswith(("=", "+", "-", "@"))
    # a relative path that starts with the name itself
    r = subprocess.run([sys.executable, "-m", "metaex", "risk", "--csv", "--", name],
                       cwd=folder, capture_output=True, timeout=60, input=b"")
    rows = parse(r.stdout)
    assert rows[1][0] == "'" + name


def test_csv_for_a_batch(tmp_path):
    (tmp_path / "a.jpg").write_bytes(JPEG)
    (tmp_path / "b.png").write_bytes(make_png(text("Author", "Jane, \"JD\" Doe")))
    (tmp_path / "notes.txt").write_bytes(b"x")
    r = run("risk", tmp_path, "--csv")
    assert r.returncode == 0
    rows = parse(r.stdout)
    assert rows[0] == list(HEADER) and rows.count(list(HEADER)) == 1
    assert {row[1] for row in rows[1:]} == {"JPEG", "PNG"}
    assert ["Jane, \"JD\" Doe"] == [row[4] for row in rows if row[3] == "Author"]
    err = r.stderr.decode("utf-8")
    assert "Summary: 3 entries" in err and "notes.txt" in err  # the summary: stderr


def test_csv_for_a_batch_without_findings_is_just_the_header(tmp_path):
    (tmp_path / "notes.txt").write_bytes(b"x")
    r = run("verify", tmp_path, "--csv")
    assert parse(r.stdout) == [list(HEADER)]


def test_csv_and_json_exclude_each_other(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(JPEG)
    r = run("risk", p, "--csv", "--json")
    assert r.returncode == 2 and b"not allowed with argument" in r.stderr


@pytest.mark.parametrize("command", ["show", "risk"])
def test_no_escape_sequences_reach_the_terminal(tmp_path, command):
    """Regression test: a PNG text keyword (a field label) with terminal
    escape sequences was written raw by --csv (text and JSON escaped it)."""
    keyword = "Au\x1b]0;PWNED\x07\x1b[2Jthor"
    path = tmp_path / "photo.png"
    path.write_bytes(make_png(text(keyword, "value\x1b[31mred")))
    r = run(command, "--csv", path)
    assert r.returncode == 0, r.stderr
    assert b"\x1b" not in r.stdout and b"\x07" not in r.stdout
    assert any(r"Au\x1b]0;PWNED\x07\x1b[2Jthor" in cell for row in parse(r.stdout)
               for cell in row)
