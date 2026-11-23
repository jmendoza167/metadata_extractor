"""SVG DOCTYPE handling: only the standard W3C SVG 1.0 / 1.1 DOCTYPE is
accepted (shown as low-risk info, removed by strip, its DTD never loaded);
every other DOCTYPE, any entity declaration and any undefined entity
reference is refused with nothing written."""

import json
import os
import socket
import subprocess
import sys
import time
import unittest.mock
import urllib.request
import xml.parsers.expat

import pytest

from helpers.svg import SVG_NS, data_uri, simple_svg
from metaex.core.errors import CannotCleanError, MalformedFileError
from metaex.core.verify import verify
from metaex.formats import svg

PUBLIC_10 = "-//W3C//DTD SVG 1.0//EN"
SYSTEM_10 = "http://www.w3.org/TR/2001/REC-SVG-20010904/DTD/svg10.dtd"
PUBLIC_11 = "-//W3C//DTD SVG 1.1//EN"
SYSTEM_11 = "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd"
DOCTYPE_11 = f'<!DOCTYPE svg PUBLIC "{PUBLIC_11}" "{SYSTEM_11}">\n'
DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'


def with_doctype(doctype: str, body: str = '<rect x="1" y="1" width="8" height="8"/>',
                 declaration: str = DECLARATION) -> bytes:
    return simple_svg(body, prolog=declaration + doctype)


def doctype_field(data: bytes):
    [section] = [s for s in svg.parse(data)["sections"] if s.key == "doctype"]
    [f] = section.fields
    return f


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *args], capture_output=True,
                          text=True, timeout=60)


# -- accepted ----------------------------------------------------------------------------------

ACCEPTED = {
    "svg_1_1": (DOCTYPE_11, "SVG 1.1"),
    "svg_1_0": (f'<!DOCTYPE svg PUBLIC "{PUBLIC_10}" "{SYSTEM_10}">\n', "SVG 1.0"),
    "single_quotes": (f"<!DOCTYPE svg PUBLIC '{PUBLIC_11}' '{SYSTEM_11}'>", "SVG 1.1"),
    "mixed_quotes": (f"<!DOCTYPE svg PUBLIC '{PUBLIC_10}' \"{SYSTEM_10}\">", "SVG 1.0"),
    "newlines": (f'<!DOCTYPE svg PUBLIC "{PUBLIC_11}"\n  "{SYSTEM_11}">\n', "SVG 1.1"),
    "extra_whitespace": (f'<!DOCTYPE  \t svg \r\n PUBLIC\n\n"{PUBLIC_11}"\t\t"{SYSTEM_11}"  \n>',
                         "SVG 1.1"),
    "space_before_close": (f'<!DOCTYPE svg PUBLIC "{PUBLIC_10}" "{SYSTEM_10}" >', "SVG 1.0"),
}


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_standard_doctype_is_accepted_and_shown_as_low_risk_info(name):
    from metaex.core.risk import LOW, assess

    doctype, version = ACCEPTED[name]
    data = with_doctype(doctype)
    f = doctype_field(data)
    assert f.value.startswith(version + " (")
    assert not f.sensitive
    assert "never loaded" in f.note
    findings = [x for x in assess(svg.parse(data)["sections"]) if x.label == "DOCTYPE"]
    assert [x.level for x in findings] == [LOW]


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_strip_removes_the_doctype_and_keeps_the_drawing(name):
    data = with_doctype(ACCEPTED[name][0])
    cleaned = svg.strip(data)
    assert b"DOCTYPE" not in cleaned
    assert cleaned.startswith(b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n<svg ')
    assert svg.drawing(svg.parse_document(cleaned)) == svg.drawing(svg.parse_document(data))
    result = verify(cleaned, data)
    assert result.clean and result.image_data_match


def test_doctype_without_xml_declaration_and_after_comments_and_pis():
    data = with_doctype("<!-- old file -->\n<?editor note?>\n" + DOCTYPE_11, declaration="")
    assert doctype_field(data).note.startswith("line 3;")
    assert b"DOCTYPE" not in svg.strip(data)


def test_predefined_entities_and_character_references_still_work():
    body = ('<text x="1" y="5" id="a&amp;b">Tom &amp; Jerry &lt;3 &#65;&#x42; &quot;&apos;'
            '</text><!-- &nbsp; is fine here --><style><![CDATA[ .a::after { content: "&x;" } ]]>'
            '</style>')
    data = with_doctype(DOCTYPE_11, body)
    doc = svg.parse_document(data)
    assert svg.text_of(doc.root).startswith("Tom & Jerry <3 AB \"'")
    cleaned = svg.strip(data)
    assert b"Tom &amp; Jerry &lt;3 AB" in cleaned
    assert verify(cleaned, data).image_data_match


def test_verify_reports_a_doctype_left_in_a_cleaned_file():
    data = with_doctype(DOCTYPE_11)
    result = verify(data)
    assert not result.clean
    assert [(f.section, f.label) for f in result.leftovers] == [("Document type", "DOCTYPE")]


def test_embedded_svg_with_the_standard_doctype_is_stripped_too():
    inner = with_doctype(DOCTYPE_11, "<!-- made by Jane --><circle r='2'/>")
    data = simple_svg(f'<image href="{data_uri(inner, "image/svg+xml")}" width="4" height="4"/>')
    parsed = svg.parse(data)
    assert any(s.key == "doctype" and "Embedded image 1" in s.title for s in parsed["sections"])
    cleaned = svg.strip(data)
    result = verify(cleaned, data)
    assert result.clean and result.image_data_match


def test_the_dtd_is_never_fetched(monkeypatch):
    """No network and no external entity loading while showing, stripping
    and verifying a file with a standard DOCTYPE."""
    def no_network(*args, **kwargs):
        raise AssertionError("metaex tried to use the network")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    data = with_doctype(DOCTYPE_11)
    svg.parse(data)
    cleaned = svg.strip(data)
    assert verify(cleaned, data).clean


def test_the_parser_never_resolves_external_entities():
    """Defense in depth: even if the text checks were bypassed, the parser's
    hooks refuse to skip or load entities instead of silently dropping them."""
    data = with_doctype(DOCTYPE_11)
    original = xml.parsers.expat.ParserCreate
    seen = {}

    def spy(*args, **kwargs):
        parser = original(*args, **kwargs)
        seen["parser"] = parser
        return parser

    with unittest.mock.patch.object(xml.parsers.expat, "ParserCreate", spy):
        svg.parse_document(data)
    parser = seen["parser"]
    with pytest.raises(MalformedFileError, match="entity &foo;"):
        parser.SkippedEntityHandler("foo", 0)
    with pytest.raises(MalformedFileError, match="external entity"):
        parser.ExternalEntityRefHandler("ctx", None, SYSTEM_11, PUBLIC_11)


def test_undefined_entity_in_text_is_refused_by_the_parser_too(monkeypatch):
    monkeypatch.setattr(svg, "_undeclared_entity", lambda text: None)  # bypass the text check
    with pytest.raises(MalformedFileError, match="entity &foo;"):
        svg.parse_document(with_doctype(DOCTYPE_11, "<text>a &foo; b</text>"))


# -- refused -----------------------------------------------------------------------------------

REFUSED = {
    "internal_subset": (with_doctype(f'<!DOCTYPE svg PUBLIC "{PUBLIC_11}" "{SYSTEM_11}" '
                                     '[ <!ATTLIST rect fill CDATA "red"> ]>'),
                        "internal subset"),
    "empty_internal_subset": (with_doctype(f'<!DOCTYPE svg PUBLIC "{PUBLIC_11}" "{SYSTEM_11}" '
                                           '[]>'), "internal subset"),
    "entity_declaration": (with_doctype('<!DOCTYPE svg [ <!ENTITY name "Jane"> ]>',
                                        "<text>&name;</text>"), "ENTITY declaration"),
    "billion_laughs": (b'<!DOCTYPE svg [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
                       b'<svg xmlns="http://www.w3.org/2000/svg">&b;</svg>', "ENTITY declaration"),
    "external_entity": (b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
                        b'<svg xmlns="http://www.w3.org/2000/svg">&x;</svg>', "ENTITY declaration"),
    "entity_reference_in_text": (with_doctype(DOCTYPE_11, "<text>&copy; Jane</text>"),
                                 "entity &copy;"),
    "entity_reference_in_attribute": (with_doctype(DOCTYPE_11, '<rect id="&foo;"/>'),
                                      "entity &foo;"),
    "wrong_url": (with_doctype(DOCTYPE_11.replace("svg11.dtd", "svg11-flat.dtd")),
                  "not the standard SVG 1.0 or 1.1"),
    "url_of_the_other_version": (with_doctype(f'<!DOCTYPE svg PUBLIC "{PUBLIC_11}" '
                                              f'"{SYSTEM_10}">'), "not the standard"),
    "https_url": (with_doctype(DOCTYPE_11.replace("http://", "https://")), "not the standard"),
    "wrong_public_id": (with_doctype(DOCTYPE_11.replace("SVG 1.1//EN", "SVG 1.1 Tiny//EN")),
                        "not the standard"),
    "system_id_only": (with_doctype(f'<!DOCTYPE svg SYSTEM "{SYSTEM_11}">'), "not the standard"),
    "other_root_name": (with_doctype(DOCTYPE_11.replace("DOCTYPE svg", "DOCTYPE html")),
                        "not the standard"),
    "lowercase_keyword": (with_doctype(DOCTYPE_11.replace("DOCTYPE", "doctype")),
                          "not the standard"),
    "two_doctypes": (with_doctype(DOCTYPE_11 + DOCTYPE_11), "more than one DOCTYPE"),
    "doctype_after_the_root_element": (simple_svg() + DOCTYPE_11.encode(),
                                       "after the start of the drawing"),
    "doctype_inside_the_drawing": (simple_svg(DOCTYPE_11), "after the start of the drawing"),
    "doctype_before_the_xml_declaration": (simple_svg(prolog=DOCTYPE_11 + DECLARATION),
                                           "not well-formed"),
}


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_other_doctypes_and_entities_are_refused(name):
    data, message = REFUSED[name]
    with pytest.raises(MalformedFileError, match=message):
        svg.parse(data)
    with pytest.raises(MalformedFileError, match=message):
        svg.strip(data)


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_cli_refuses_and_writes_nothing(tmp_path, name):
    path = tmp_path / "drawing.svg"
    path.write_bytes(REFUSED[name][0])
    result = metaex("strip", str(path))
    assert result.returncode == 1
    # A lowercase <!doctype> isn't XML, so the file isn't even detected as SVG.
    assert any(m in result.stderr for m in ("refused", "not well-formed", "not recognized"))
    assert os.listdir(tmp_path) == ["drawing.svg"]


def test_refused_doctype_message_shows_what_was_found():
    data = with_doctype(DOCTYPE_11.replace("svg11.dtd", "svg11-flat.dtd"))
    with pytest.raises(MalformedFileError, match="svg11-flat.dtd"):
        svg.parse(data)


def test_embedded_svg_with_a_refused_doctype_blocks_strip():
    inner = with_doctype(DOCTYPE_11 + DOCTYPE_11)
    data = simple_svg(f'<image href="{data_uri(inner, "image/svg+xml")}" width="4" height="4"/>')
    listing = next(s for s in svg.parse(data)["sections"] if s.key == "embedded_images")
    assert "more than one DOCTYPE" in listing.fields[0].value
    with pytest.raises(CannotCleanError):
        svg.strip(data)


@pytest.mark.parametrize("filler", ["<!--" * 200_000, "<?" * 200_000, "&" * 400_000,
                                    "<!-- x -->" * 100_000, "&amp;" * 200_000])
def test_checks_are_fast_on_hostile_input(filler):
    """The DOCTYPE and entity checks scan the text once: no quadratic time on
    many unterminated comments / processing instructions or many '&'."""
    text = DECLARATION + DOCTYPE_11 + f'<svg xmlns="{SVG_NS}"><text>{filler}</text></svg>'
    start = time.perf_counter()
    try:
        svg.check_doctype(text)
    except MalformedFileError:
        pass
    assert time.perf_counter() - start < 5


# -- workflow ----------------------------------------------------------------------------------

def test_show_strip_verify_with_the_svg_1_1_doctype(tmp_path):
    original = tmp_path / "plan.svg"
    original.write_bytes(with_doctype(
        DOCTYPE_11, '<metadata><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>'
        '</metadata><!-- by Jane --><rect x="1" y="1" width="8" height="8"/>'))

    shown = json.loads(metaex("show", str(original), "--json").stdout)
    doctype = shown["sections"]["doctype"]["fields"]["doctype"]
    assert doctype["value"] == f"SVG 1.1 ({PUBLIC_11}, {SYSTEM_11})"
    assert doctype["sensitive"] is False
    risk = json.loads(metaex("risk", str(original), "--json").stdout)
    assert {(f["label"], f["level"]) for f in risk["findings"]} >= {("DOCTYPE", "low")}

    result = metaex("strip", str(original))
    assert result.returncode == 0, result.stderr
    assert "DOCTYPE" in result.stdout  # listed among what was removed
    cleaned = tmp_path / "plan_clean.svg"
    assert b"<!DOCTYPE" not in cleaned.read_bytes()

    verified = metaex("verify", str(cleaned), "--original", str(original), "--json")
    assert verified.returncode == 0, verified.stdout
    v = json.loads(verified.stdout)
    assert v["clean"] and v["image_data_match"]
    after = json.loads(metaex("show", str(cleaned), "--json").stdout)
    assert "doctype" not in after["sections"]
