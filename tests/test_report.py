import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from metaex import __version__
from metaex.core.report import (
    MAX_VALUE_LINES,
    Report,
    format_value,
    render_json,
    render_text,
    sanitize,
    to_jsonable,
    write_text,
)
from metaex.core.result import Section


def make_report(**kwargs) -> Report:
    files = Section("file_system", "File system")
    files.add("size_bytes", "Size (bytes)", 2048)
    files.add("modified", "Modified",
              datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=-5))))
    origin = Section("origin", "Origin")
    origin.add("url", "Referrer URL", "https://example.com/x", sensitive=True)
    return Report("photo.jpg", [files, origin], **kwargs)


def test_sections_have_headers_and_aligned_values():
    text = render_text(make_report(), width=100)
    lines = text.splitlines()
    assert lines[0] == "metaex report: photo.jpg"
    assert "File system" in lines and "-----------" in lines
    value_lines = [l for l in lines if l.startswith("  ") and "  " in l.strip()]
    columns = {l.index(v) for l, v in zip(
        value_lines, ["2048", "2026-01-02T03:04:05-05:00", "https://example.com/x"])}
    assert len(columns) == 1


def test_datetime_is_iso_8601_with_offset():
    assert "2026-01-02T03:04:05-05:00" in render_text(make_report(), width=100)


def test_sensitive_fields_are_marked_with_legend():
    text = render_text(make_report(), width=100)
    assert "  * Referrer URL" in text
    assert "* = sensitive" in text


def test_no_legend_without_sensitive_fields():
    s = Section("a", "A")
    s.add("k", "Key", "v")
    assert "sensitive" not in render_text(Report("x", [s]), width=100)


def test_warnings_come_first():
    text = render_text(make_report(warnings=["extension .jpg but content is PNG"]), width=100)
    assert text.index("Warnings") < text.index("File system")
    assert "  ! extension .jpg but content is PNG" in text


@pytest.mark.parametrize(
    "value,expected",
    [
        (b"\x00\x01\x02", "<3 bytes>"),
        (bytearray(10), "<10 bytes>"),
        (memoryview(b"abcd"), "<4 bytes>"),
        (True, "yes"),
        (False, "no"),
        (None, "(none)"),
        ([1, "a"], "1, a"),
        ([], "(none)"),
        (3.5, "3.5"),
    ],
)
def test_format_value(value, expected):
    assert format_value(value) == expected


def test_binary_value_in_table():
    s = Section("a", "A")
    s.add("thumb", "Thumbnail", b"\xff" * 5000)
    assert "<5000 bytes>" in render_text(Report("x", [s]), width=100)


def test_long_values_are_wrapped():
    s = Section("a", "A")
    s.add("k", "Key", "word " * 30)
    lines = render_text(Report("x", [s]), width=60).splitlines()
    value_lines = [l for l in lines if "word" in l]
    assert len(value_lines) > 1
    assert all(len(l) <= 60 for l in lines)


def test_very_long_values_are_shortened_with_note():
    s = Section("a", "A")
    s.add("k", "Key", "x" * 10_000)
    text = render_text(Report("x", [s]), width=80)
    assert "[shortened: 10000 characters in total; use --json" in text
    assert text.count("x" * 20) <= MAX_VALUE_LINES * 4


def test_long_labels_are_wrapped_not_cut():
    s = Section("xmp", "XMP")
    label = "xmpMM:History[1]/stEvt:softwareAgent"
    s.add("a", label, "Editor 2.0")
    s.add("b", "x" * 80, "long single word")
    lines = render_text(Report("x", [s]), width=100).splitlines()
    from metaex.core.report import MAX_LABEL_WIDTH

    body = [l for l in lines if l.startswith("    ")]
    label_column = "".join(l[4 : 4 + MAX_LABEL_WIDTH].strip() for l in body)
    assert label in label_column
    assert "x" * 80 in label_column
    assert "..." not in "\n".join(lines)
    # value stays on the first line of its label
    first = next(l for l in lines if "xmpMM:History[1]/" in l)
    assert first.rstrip().endswith("Editor 2.0")


@pytest.mark.parametrize(
    "label,width,expected",
    [
        ("short", 10, ["short"]),
        ("xmpMM:History[1]/stEvt:action", 20, ["xmpMM:History[1]/", "stEvt:action"]),
        ("abcdefghij", 4, ["abcd", "efgh", "ij"]),
        ("a/b", 1, ["a", "/", "b"]),
    ],
)
def test_wrap_label(label, width, expected):
    from metaex.core.report import wrap_label

    assert wrap_label(label, width) == expected
    assert "".join(wrap_label(label, width)) == label


def test_note_is_shown():
    s = Section("a", "A")
    s.add("created", "Created", "unavailable", note="not reported by this OS")
    assert "unavailable (not reported by this OS)" in render_text(Report("x", [s]), width=100)


def test_empty_section():
    assert "(nothing found)" in render_text(Report("x", [Section("a", "A")]), width=100)


@pytest.mark.parametrize(
    "raw,escaped",
    [
        ("\x1b[31mred", "\\x1b[31mred"),
        ("a\nb\rc\td", "a\\nb\\rc\\td"),
        ("\u202eevil", "\\u202eevil"),
        ("zero\u200bwidth", "zero\\u200bwidth"),
        ("\x85", "\\x85"),
        ("caf\u00e9 \u6f22\u5b57", "caf\u00e9 \u6f22\u5b57"),
    ],
)
def test_sanitize(raw, escaped):
    assert sanitize(raw) == escaped


def test_control_characters_in_values_are_escaped_in_output():
    s = Section("a", "A")
    s.add("k", "Key\x1b[2J", "\x1b]0;pwned\x07")
    text = render_text(Report("x\x1b", [s]), width=100)
    assert "\x1b" not in text and "\x07" not in text


@pytest.mark.parametrize("encoding", ["cp1252", "ascii", "cp437"])
def test_write_text_survives_limited_console_encodings(encoding):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding=encoding)
    s = Section("a", "A")
    s.add("k", "Autor", "Jos\u00e9 \u6f22\u5b57 \U0001f600")
    write_text(render_text(Report("x", [s]), width=100), stream)
    output = raw.getvalue().decode(encoding)
    assert "Autor" in output
    assert "\\u6f22" in output


def test_write_text_utf8_passes_through():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="utf-8", newline="\n")
    write_text("\u6f22\u5b57\n", stream)
    assert raw.getvalue().decode("utf-8") == "\u6f22\u5b57\n"


# --- JSON -----------------------------------------------------------------------


def test_json_structure():
    data = json.loads(render_json(make_report(warnings=["w1"])))
    assert list(data) == ["metaex_version", "subject", "warnings", "sections"]
    assert data["metaex_version"] == __version__
    assert data["subject"] == "photo.jpg"
    assert data["warnings"] == ["w1"]
    assert list(data["sections"]) == ["file_system", "origin"]
    size = data["sections"]["file_system"]["fields"]["size_bytes"]
    assert size == {"label": "Size (bytes)", "value": 2048, "sensitive": False, "note": None}
    assert data["sections"]["origin"]["fields"]["url"]["sensitive"] is True


def test_json_datetime_is_iso_string():
    data = json.loads(render_json(make_report()))
    assert data["sections"]["file_system"]["fields"]["modified"]["value"] == "2026-01-02T03:04:05-05:00"


def test_json_bytes_have_length_and_hex_preview():
    blob = bytes(range(100))
    assert to_jsonable(blob) == {"length": 100, "hex_preview": blob[:32].hex()}
    assert to_jsonable(memoryview(b"ab")) == {"length": 2, "hex_preview": "6162"}
    assert to_jsonable(bytearray()) == {"length": 0, "hex_preview": ""}


def test_json_key_order_is_stable():
    assert render_json(make_report()) == render_json(make_report())


def test_json_is_ascii_and_round_trips_unicode():
    s = Section("a", "A")
    value = "Jos" + chr(0xE9) + " " + chr(0x6F22) + chr(0x202E) + chr(0x1B)
    s.add("k", "Key", value)
    text = render_json(Report("x", [s]))
    assert text.isascii()
    assert json.loads(text)["sections"]["a"]["fields"]["k"]["value"] == value


def test_json_duplicate_keys_are_not_lost():
    s = Section("a", "A")
    s.add("k", "Key", 1)
    s.add("k", "Key", 2)
    s.add("k", "Key", 3)
    fields = json.loads(render_json(Report("x", [s, Section("a", "Again")])))["sections"]
    assert [f["value"] for f in fields["a"]["fields"].values()] == [1, 2, 3]
    assert list(fields["a"]["fields"]) == ["k", "k_2", "k_3"]
    assert "a_2" in fields


@pytest.mark.parametrize(
    "value,expected",
    [
        (float("nan"), "nan"),
        (float("inf"), "inf"),
        (1.5, 1.5),
        ((1, b"x"), [1, {"length": 1, "hex_preview": "78"}]),
        ({"a": {1: None}}, {"a": {"1": None}}),
        (object, str(object)),
    ],
)
def test_to_jsonable(value, expected):
    assert to_jsonable(value) == expected


def test_to_jsonable_caps_nesting():
    value = []
    for _ in range(100):
        value = [value]
    json.dumps(to_jsonable(value))
