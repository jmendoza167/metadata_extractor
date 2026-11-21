"""SVG: stripping metadata, comments and editor data (re-serialized)."""

import base64
import re

import pytest

from helpers.png import make_png, text
from helpers.svg import (SVG_NS, data_uri, illustrator_svg, inkscape_svg, sample_jpeg,
                         simple_svg)
from metaex.core.errors import CannotCleanError, MalformedFileError
from metaex.formats import jpeg, png, svg


def parsed_keys(data):
    return [s.key for s in svg.parse(data)["sections"]]


def embedded(cleaned, n=0):
    uris = re.findall(rb'href="(data:[^"]+)"', cleaned)
    return base64.b64decode(uris[n].split(b",", 1)[1])


def test_inkscape_svg_is_cleaned():
    cleaned = svg.strip(inkscape_svg())
    text_out = cleaned.decode("utf-8")
    for gone in ("sodipodi", "inkscape", "<metadata", "<!--", "Jane Doe", "/home/jane",
                 "xmlns:rdf", "xmlns:cc", "xmlns:dc", "xmlns:svg="):
        assert gone not in text_out, gone
    assert text_out.startswith('<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n<svg')
    assert f'xmlns="{SVG_NS}"' in text_out and 'xmlns:xlink="http://www.w3.org/1999/xlink"' in text_out
    assert "ns0:" not in text_out and "xlink:href=" in text_out
    assert "<title" in text_out  # kept for accessibility
    assert "Kitchen &amp; dining" in text_out
    assert 'stroke:url(#grad1)' in text_out and '<linearGradient id="grad1">' in text_out
    keys = parsed_keys(cleaned)
    assert keys[:2] == ["title_desc", "embedded_images"]
    assert "gps" not in keys and "xmp" not in keys and "editor" not in keys


def test_embedded_jpeg_is_stripped_with_its_own_rules():
    cleaned = svg.strip(inkscape_svg())
    image = embedded(cleaned)
    assert image == jpeg.strip(sample_jpeg())
    assert b'href="data:image/jpeg;base64,' in cleaned


def test_paranoid_removes_title_and_desc():
    data = simple_svg("<title>Jane's plan</title><desc>secret</desc><rect/>")
    assert b"<title>" in svg.strip(data)
    cleaned = svg.strip(data, paranoid=True)
    assert b"<title" not in cleaned and b"<desc" not in cleaned
    assert parsed_keys(cleaned) == []


def test_illustrator_svg_with_a_linked_local_file_is_refused():
    with pytest.raises(CannotCleanError) as exc:
        svg.strip(illustrator_svg())
    message = str(exc.value)
    assert "C:\\Users\\Jane Doe\\Documents\\logo.png" in message
    assert "would break the drawing" in message


def test_illustrator_svg_without_the_link():
    data = illustrator_svg().replace(b'C:\\Users\\Jane Doe\\Documents\\logo.png', b"logo.png")
    cleaned = svg.strip(data).decode("utf-8")
    assert "Adobe" not in cleaned and "i:extraneous" not in cleaned and "xmlns:i=" not in cleaned
    assert "xmlns:x=" not in cleaned and "<sfw" not in cleaned
    assert 'xml:space="preserve"' in cleaned and ".st0{fill:#00FF00;}" in cleaned


def test_paths_in_removed_attributes_dont_block_stripping():
    data = simple_svg('<rect width="1" height="1"/>',
                      attrs='xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" '
                            'sodipodi:docbase="C:\\Users\\jane\\Desktop"')
    assert b"jane" not in svg.strip(data)


def test_unused_namespace_declarations_are_removed():
    data = simple_svg("<rect/>", attrs='xmlns:foo="http://example.com/foo"')
    cleaned = svg.strip(data)
    assert b"xmlns:foo" not in cleaned and b"xmlns:xlink" in cleaned
    used = simple_svg('<foo:thing xmlns:foo="http://example.com/foo"/>')
    assert b"xmlns:foo" in svg.strip(used)


def test_whitespace_in_text_is_never_changed():
    data = simple_svg('<text xml:space="preserve">a <!-- note --> b</text>\n  <!-- x -->\n<rect/>')
    cleaned = svg.strip(data)
    assert b'<text xml:space="preserve">a  b</text>' in cleaned
    assert b"<!--" not in cleaned


def test_embedded_png_and_nested_svg():
    inner = simple_svg("<!-- inner comment --><rect/>")
    body = (f'<image href="{data_uri(make_png(text("Author", "Jane")), "image/png")}"/>'
            f'<image href="{data_uri(inner, "image/svg+xml")}"/>')
    cleaned = svg.strip(simple_svg(body))
    assert embedded(cleaned, 0) == png.strip(make_png(text("Author", "Jane")))
    assert b"inner comment" not in embedded(cleaned, 1)


@pytest.mark.parametrize("uri,message", [
    ("data:image/bmp;base64," + base64.b64encode(b"BM" + bytes(40)).decode(), "can't strip"),
    ("data:image/png;base64,@@@", "invalid base64"),
    ("data:image/jpeg;base64," + base64.b64encode(sample_jpeg()[:200]).decode(),
     "can't be stripped"),
])
def test_embedded_data_that_cant_be_stripped_refuses_the_svg(uri, message):
    with pytest.raises(CannotCleanError, match=message):
        svg.strip(simple_svg(f'<image href="{uri}"/>'))


def test_css_data_uri_is_refused():
    data = simple_svg("<style>.a{background:url(data:image/png;base64,AAAA)}</style><rect/>")
    with pytest.raises(CannotCleanError, match="CSS of <style>"):
        svg.strip(data)


def test_embedded_image_inside_removed_metadata_is_ignored():
    data = simple_svg(f'<metadata><image href="data:image/bmp;base64,Qk0="/></metadata><rect/>')
    assert b"<metadata" not in svg.strip(data)


def test_strip_is_idempotent():
    for kwargs in ({}, {"paranoid": True}, {"keep_orientation": False}):
        once = svg.strip(inkscape_svg(), **kwargs)
        assert svg.strip(once, **kwargs) == once


@pytest.mark.parametrize("data", [b"<svg><g></svg>", b"<!DOCTYPE svg><svg/>", b"<html/>"])
def test_malformed_svg_is_refused(data):
    with pytest.raises(MalformedFileError):
        svg.strip(data)


def test_kept_warnings():
    data = simple_svg('<title>Plan</title><script>x()</script>'
                      '<image href="https://example.com/a.png"/>')
    warnings = svg.kept_warnings(svg.strip(data))
    assert any("kept 1 <title>/<desc>" in w and "--paranoid" in w for w in warnings)
    assert any("<script>" in w for w in warnings)
    assert any("external reference: https://example.com/a.png" in w for w in warnings)
