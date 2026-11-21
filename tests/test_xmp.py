import pytest

from helpers.jpeg import SAMPLE_XMP
from metaex.core.errors import MalformedFileError
from metaex.core.xmp import parse_xmp


def props(xml):
    result = parse_xmp(xml.encode("utf-8") if isinstance(xml, str) else xml)
    return {p.path: p for p in result.properties}, result.warnings


def test_simple_properties_as_prefix_name():
    p, _ = props(SAMPLE_XMP)
    assert p["xmp:CreatorTool"].value == "Editor 2.0"
    assert p["xmpMM:DocumentID"].value == "xmp.did:1234"
    assert p["xmpMM:OriginalDocumentID"].value == "xmp.did:abcd"
    assert p["tiff:Orientation"].value == "1"
    assert p["dc:creator"].value == "Jane Doe"
    assert p["dc:subject"].value == "holiday; beach"


def test_history_is_flattened():
    p, _ = props(SAMPLE_XMP)
    assert p["xmpMM:History[1]/stEvt:action"].value == "created"
    assert p["xmpMM:History[1]/stEvt:when"].value == "2024-05-01T10:00:00+02:00"
    assert p["xmpMM:History[2]/stEvt:action"].value == "saved"
    assert p["xmpMM:History[2]/stEvt:softwareAgent"].value == "Editor 2.0"


def test_sensitivity():
    p, _ = props(SAMPLE_XMP)
    for path in ("xmpMM:DocumentID", "xmpMM:OriginalDocumentID", "exif:GPSLatitude",
                 "exif:GPSLongitude", "xmpMM:History[1]/stEvt:action", "dc:creator",
                 "xmp:CreatorTool"):
        assert p[path].sensitive, path
    assert not p["tiff:Orientation"].sensitive


def test_gps_is_critical():
    _, warnings = props(SAMPLE_XMP)
    assert any(w.startswith("CRITICAL: XMP contains GPS data (exif:GPSLatitude") for w in warnings)


@pytest.mark.parametrize(
    "xml",
    [
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><x/>',
        b"<!doctype x><x/>",
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><!ENTITY a "b"></x:xmpmeta>',
        b"<! DOCTYPE x><x/>",
    ],
)
def test_doctype_and_entity_are_refused(xml):
    with pytest.raises(MalformedFileError, match="DOCTYPE or ENTITY"):
        parse_xmp(xml)


def test_utf16_packets_are_refused():
    with pytest.raises(MalformedFileError, match="not UTF-8"):
        parse_xmp("<x/>".encode("utf-16"))


def test_malformed_xml():
    with pytest.raises(MalformedFileError, match="not well-formed"):
        parse_xmp(b"<x:xmpmeta><rdf:RDF>")


def test_no_rdf_element():
    p, warnings = props("<root/>")
    assert p == {}
    assert "no rdf:RDF" in warnings[0]


def test_unknown_namespace_uses_declared_prefix():
    xml = ('<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           '<rdf:Description xmlns:foo="http://example.com/foo/" foo:Bar="baz"/></rdf:RDF>')
    p, _ = props(xml)
    assert p["foo:Bar"].value == "baz"


def test_typed_nodes_are_read_like_descriptions():
    """RDF typed nodes (<cc:Work> in SVG metadata) are a shorthand for rdf:Description."""
    packet = (b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
              b'xmlns:cc="http://creativecommons.org/ns#" '
              b'xmlns:dc="http://purl.org/dc/elements/1.1/">'
              b'<cc:Work rdf:about=""><dc:date>2024</dc:date>'
              b'<dc:creator><cc:Agent><dc:title>Jane</dc:title></cc:Agent></dc:creator>'
              b'</cc:Work></rdf:RDF>')
    props = {p.path: p.value for p in parse_xmp(packet).properties}
    assert props == {"dc:date": "2024", "dc:creator/cc:Agent/dc:title": "Jane"}
