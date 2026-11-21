"""SVG: safe parsing, the document model and the metadata sections."""

import pytest

from helpers.png import make_png, text
from helpers.svg import (SVG_NS, XLINK_NS, data_uri, illustrator_svg, inkscape_svg,
                         sample_jpeg, simple_svg)
from metaex.core.errors import MalformedFileError
from metaex.formats import svg


def sections(data):
    return {s.key: s for s in svg._parse(data, 0)["sections"]}


def values(section):
    return {f.label: f.value for f in section.fields}


def warnings(data):
    return svg._parse(data, 0)["warnings"]


# -- refusing what must not be parsed -----------------------------------------------------


@pytest.mark.parametrize("data,message", [
    (b'<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
     b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd"><svg/>', "DOCTYPE or ENTITY"),
    (b'<!DOCTYPE svg [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><svg>&b;</svg>',
     "DOCTYPE or ENTITY"),
    (b'<svg><!ENTITY x "y"></svg>', "DOCTYPE or ENTITY"),
    ("<svg/>".encode("utf-16"), "not UTF-8"),
    (b'<?xml version="1.0" encoding="ISO-8859-1"?><svg/>', "only UTF-8 is supported"),
    (b"<svg>\xff\xfe</svg>", "not valid UTF-8"),
    (b"<svg><g></svg>", "not well-formed"),
    (b"<svg><p:x/></svg>", "unbound namespace prefix"),
    (b"<html/>", "not <svg>"),
    (b"", "not well-formed"),
])
def test_refused(data, message):
    with pytest.raises(MalformedFileError, match=message):
        svg.parse_document(data)


def test_size_and_count_limits(monkeypatch):
    monkeypatch.setattr(svg, "MAX_SVG_SIZE", 100)
    with pytest.raises(MalformedFileError, match="larger than"):
        svg.parse_document(simple_svg("<g/>" * 50))
    monkeypatch.setattr(svg, "MAX_SVG_SIZE", 10**6)
    monkeypatch.setattr(svg, "MAX_ELEMENTS", 10)
    with pytest.raises(MalformedFileError, match="more than 10 elements"):
        svg.parse_document(simple_svg("<g/>" * 20))
    monkeypatch.setattr(svg, "MAX_DEPTH", 5)
    with pytest.raises(MalformedFileError, match="nested more than 5 deep"):
        svg.parse_document(simple_svg("<g>" * 6 + "</g>" * 6))


# -- the document model ----------------------------------------------------------------


@pytest.mark.parametrize("data", [inkscape_svg(), illustrator_svg(), simple_svg()])
def test_writer_round_trip_keeps_everything(data):
    doc = svg.parse_document(data)
    out = svg.Writer().document(doc).encode("utf-8")
    again = svg.parse_document(out)
    assert svg.Writer().document(again).encode("utf-8") == out
    assert again.element_count == doc.element_count  # nothing was lost
    assert len(list(svg.iter_nodes(again))) == len(list(svg.iter_nodes(doc)))


def test_prefixes_are_kept_exactly():
    doc = svg.parse_document(inkscape_svg())
    out = svg.Writer().document(doc)
    assert "<sodipodi:namedview" in out and 'xmlns="http://www.w3.org/2000/svg"' in out
    assert "xlink:href=" in out and "ns0:" not in out
    assert doc.root.uri == SVG_NS
    image = next(e for e in svg.iter_elements(doc.root) if e.local == "image")
    assert image.get("href", XLINK_NS).startswith("data:image/jpeg;base64,")


def test_text_cdata_and_escaping():
    data = simple_svg('<style><![CDATA[a > b { fill: red }]]></style>'
                      '<text>Fish &amp; chips &lt;3 &#233;</text>')
    out = svg.Writer().document(svg.parse_document(data))
    assert "<![CDATA[a > b { fill: red }]]>" in out
    assert "Fish &amp; chips &lt;3 " + chr(0xE9) in out


def test_attribute_values_are_escaped():
    doc = svg.parse_document(simple_svg('<g id="a&amp;b&quot;c&#10;d"/>'))
    assert 'id="a&amp;b&quot;c&#10;d"' in svg.Writer().document(doc)


def test_declaration_prolog_and_epilog():
    data = (b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n<!-- before -->\n'
            b'<?xml-stylesheet href="style.css"?>\n<svg xmlns="http://www.w3.org/2000/svg"/>\n'
            b"<!-- after -->\n")
    doc = svg.parse_document(data)
    assert doc.declaration == ("1.0", "UTF-8", 0)
    assert [type(n).__name__ for n in doc.prolog] == ["Comment", "PI"]
    assert [n.value for n in doc.epilog] == [" after "]
    assert svg.Writer().document(doc).startswith(
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n<!-- before -->\n')


def test_utf8_bom_is_accepted():
    assert svg.parse_document(b"\xef\xbb\xbf" + simple_svg()).root.local == "svg"


# -- sections ----------------------------------------------------------------------------


def test_inkscape_metadata():
    s = sections(inkscape_svg())
    meta = values(s["svg_metadata"])
    assert meta["dc:creator/cc:Agent/dc:title"] == "Jane Doe"
    assert meta["dc:date"] == "2024-05-01"
    assert meta["dc:rights/cc:Agent/dc:title"] == "(c) Jane Doe"
    assert meta["dc:publisher/cc:Agent/dc:title"] == "Doe Design"
    assert values(s["title_desc"]) == {"Title": "Floor plan for jane"}
    assert [f.value for f in s["comments"].fields] == [
        "Created with Inkscape (http://www.inkscape.org/)", "TODO ask jane about the stairs"]


def test_inkscape_editor_data():
    editor = values(sections(inkscape_svg())["editor"])
    assert editor["sodipodi:docname"] == "floor plan.svg"
    assert editor["inkscape:version"].startswith("1.2.2")
    assert editor["inkscape:export-filename"] == "/home/jane/Pictures/floor plan.png"
    assert "<sodipodi:namedview>" in editor and "<inkscape:path-effect>" in editor
    assert editor["Inkscape namespace"] == "inkscape = http://www.inkscape.org/namespaces/inkscape"


def test_illustrator_data():
    s = sections(illustrator_svg())
    meta = values(s["svg_metadata"])
    assert meta["xmp:CreatorTool"].startswith("Adobe Illustrator")
    assert meta["metadata element: other contents"] == "<sfw>"
    editor = values(s["editor"])
    assert editor["i:extraneous"] == "self"
    assert all("xap" not in str(v) for v in editor.values())  # XMP belongs to <metadata>
    assert s["comments"].fields[0].value.startswith("Generator: Adobe Illustrator")


def test_metadata_without_rdf():
    s = sections(simple_svg("<metadata><info>made by Jane</info></metadata><rect/>"))
    [field] = s["svg_metadata"].fields
    assert field.value == "<info>; text: made by Jane" and field.sensitive


def test_processing_instructions():
    data = b'<?xml-stylesheet href="file:///C:/Users/jane/s.css"?>' + simple_svg()
    [field] = sections(data)["processing_instructions"].fields
    assert field.label == "xml-stylesheet" and "jane" in field.value


@pytest.mark.parametrize("value,path,user", [
    ("C:\\Users\\Jane Doe\\Desktop\\a.png", "C:\\Users\\Jane Doe\\Desktop\\a.png", "Jane Doe"),
    ("c:/users/jane/a.png", "c:/users/jane/a.png", "jane"),
    ("file:///home/jane/a.png", "file:///home/jane/a.png", "jane"),
    ("file:///Users/jane%20doe/a.png", "file:///Users/jane%20doe/a.png", "jane doe"),
    ("/home/jane/Pictures/x.png", "/home/jane/Pictures/x.png", "jane"),
    ("/Users/jane/x.png", "/Users/jane/x.png", "jane"),
    ("\\\\server\\share\\x.png", "\\\\server\\share\\x.png", None),
    ("/tmp/x.png", "/tmp/x.png", None),
    ("url(/home/jane/f.png)", "/home/jane/f.png", "jane"),
])
def test_local_paths(value, path, user):
    assert svg.local_paths(value) == [path]
    assert svg.user_name(path) == user


@pytest.mark.parametrize("value", [
    "M 10,10 L 20,20", "https://example.com/users/jane/a.png", "#grad1", "image.png",
    "fill:red;stroke:url(#g)", "translate(10 20)", "data:image/png;base64,AAAA", "../a/b.png",
])
def test_not_local_paths(value):
    assert svg.local_paths(value) == []


def test_local_path_section_is_critical():
    data = simple_svg('<image xlink:href="C:\\Users\\jane\\a.png" width="1" height="1"/>')
    [field] = sections(data)["local_paths"].fields
    assert field.label == "xlink:href on <image>" and "reveals the user name 'jane'" in field.note
    assert any(w.startswith("CRITICAL: 1 attribute(s) contain absolute local file paths "
                            "(user names: jane)") for w in warnings(data))


def test_embedded_jpeg_metadata_appears_under_the_svg():
    s = svg._parse(inkscape_svg(), 0)
    titles = [x.title for x in s["sections"]]
    assert "Embedded image 1 (JPEG): GPS" in titles
    listing = next(x for x in s["sections"] if x.key == "embedded_images")
    assert listing.fields[0].value == f"JPEG, {len(sample_jpeg())} bytes"
    assert "xlink:href of <image>" in listing.fields[0].note
    assert any(w.startswith("CRITICAL: Embedded image 1 (JPEG): GPS") for w in s["warnings"])


def test_embedded_png_and_percent_encoded_svg():
    png = make_png(text("Author", "Jane"))
    inner = simple_svg("<title>inner</title>").decode()
    body = (f'<image href="{data_uri(png, "image/png")}"/>'
            '<image href="data:image/svg+xml,' + inner.replace('"', "%22").replace("#", "%23")
            .replace("<", "%3C").replace(">", "%3E") + '"/>')
    titles = [x.title for x in svg._parse(simple_svg(body), 0)["sections"]]
    assert "Embedded image 1 (PNG): Text" in titles
    assert "Embedded image 2 (SVG): Title and description" in titles


def test_nested_svg_depth_is_limited(monkeypatch):
    monkeypatch.setattr(svg, "MAX_NESTED_SVG", 1)
    inner = simple_svg(f'<image href="{data_uri(simple_svg(), "image/svg+xml")}"/>')
    outer = simple_svg(f'<image href="{data_uri(inner, "image/svg+xml")}"/>')
    assert any("nested more than 1 deep" in w for w in warnings(outer))


@pytest.mark.parametrize("uri,problem", [
    ("data:image/png;base64,@@@", "invalid base64"),
    ("data:image/png;base64", "without a comma"),
    ("data:application/octet-stream;base64,AAAA", "can't read this type"),
])
def test_embedded_data_that_cant_be_examined(uri, problem):
    data = simple_svg(f'<image href="{uri}"/>')
    listing = sections(data)["embedded_images"]
    assert "not examined" in listing.fields[0].value
    assert any(problem in w for w in warnings(data))


def test_embedded_data_limit(monkeypatch):
    monkeypatch.setattr(svg, "MAX_EMBEDDED_TOTAL", 100)
    data = simple_svg(f'<image href="{data_uri(sample_jpeg(), "image/jpeg")}"/>')
    assert any("limit for embedded data" in w for w in warnings(data))


def test_malformed_embedded_image():
    data = simple_svg(f'<image href="{data_uri(b"GIF89a" + bytes(3), "image/gif")}"/>')
    assert "malformed" in sections(data)["embedded_images"].fields[0].value


def test_active_content_and_external_references():
    body = ('<script>alert(1)</script><rect onclick="x()" width="1" height="1"/>'
            '<image href="https://tracker.example/p.png"/>'
            '<a xlink:href="javascript:void(0)"><text>x</text></a>'
            '<style>@import url("https://fonts.example/f.css"); '
            '.a{background:url(data:image/png;base64,AAAA)}</style>'
            '<rect style="fill:url(http://example.com/g.svg#p)"/>')
    w = warnings(simple_svg(body))
    assert any("1 <script> element(s)" in x for x in w)
    assert any("event handler attribute(s) (e.g. onclick" in x for x in w)
    assert any("javascript: link" in x for x in w)
    refs = [x for x in w if x.startswith("external reference")]
    assert len(refs) == 3 and any("tracker.example" in x for x in refs)
    assert any("contains a data: URI; its contents were not examined" in x for x in w)
    assert not any(x.startswith("CRITICAL") for x in w)


def test_structure_section():
    fields = {f.label: f.value for f in svg.structure_section(
        svg.parse_document(inkscape_svg())).fields}
    assert fields["Root element"] == "<svg>"
    assert fields["xmlns:sodipodi"] == "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
    assert fields["XML declaration"] == "version 1.0, encoding UTF-8"


def test_root_outside_the_svg_namespace_is_a_warning():
    assert any("not in the SVG namespace" in w for w in warnings(b"<svg><rect/></svg>"))
