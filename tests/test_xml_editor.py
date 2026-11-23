"""Minimal edits to XML parts (core/xmldoc.py XmlEditor)."""

from __future__ import annotations

import pytest

from metaex.core.xmldoc import XmlEditor, parse_xml

WORD = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        b'<w:document xmlns:w="urn:w" xmlns:w14="urn:w14" xmlns:mc="urn:mc" '
        b'mc:Ignorable="w14">\r\n'
        b"  <w:p w:rsidR='00A1' w14:paraId=\"1\" >\n"
        b'    <w:r><w:t xml:space="preserve">Hello &amp; bye</w:t></w:r>\n'
        b"    <!-- note -->\n"
        b'    <w:ins w:id="1" w:author="Jane Doe" w:date="2024-05-01T10:00:00Z"/>\n'
        b"  </w:p>\n"
        b"</w:document>")


def edit(data: bytes = WORD) -> tuple:
    doc = parse_xml(data)
    return doc, XmlEditor(doc)


def element(doc, local):
    return next(e for e in doc.iter() if e.local == local)


def test_no_edits_returns_the_original_bytes():
    doc, ed = edit()
    assert not ed.changed and ed.result() is doc.data


def test_removing_an_element_changes_only_its_bytes():
    doc, ed = edit()
    ed.remove(element(doc, "ins"))
    out = ed.result()
    assert out == WORD.replace(b'<w:ins w:id="1" w:author="Jane Doe" '
                               b'w:date="2024-05-01T10:00:00Z"/>', b"")
    assert b'mc:Ignorable="w14"' in out and b'xmlns:w14="urn:w14"' in out


def test_attribute_edits():
    doc, ed = edit()
    ins = element(doc, "ins")
    ed.set_attr(ins, "w:author", "Author")
    ed.remove_attr(ins, "w:date")
    p = element(doc, "p")
    ed.remove_attr(p, "w:rsidR")
    ed.set_attr(p, "w:new", 'a "quoted" & <odd>\nvalue')
    out = ed.result()
    assert b'<w:ins w:id="1" w:author="Author"/>' in out
    assert b'<w:p w14:paraId="1"  w:new="a &quot;quoted&quot; &amp; &lt;odd>&#10;value">' in out
    assert parse_xml(out).root.child("urn:w", "p").get("new", "urn:w") == \
        'a "quoted" & <odd>\nvalue'


def test_adding_an_attribute_to_an_empty_element():
    doc, ed = edit(b'<a xmlns:x="urn:x"><b/><c x:y="1" /></a>')
    b, c = list(doc.root.elements())
    ed.set_attr(b, "x:z", "2")
    ed.set_attr(c, "x:y", "3")
    assert ed.result() == b'<a xmlns:x="urn:x"><b x:z="2"/><c x:y="3" /></a>'


def test_removing_a_missing_attribute_does_nothing():
    doc, ed = edit()
    ed.remove_attr(element(doc, "p"), "w:missing")
    assert not ed.changed


def test_replacing_content():
    doc, ed = edit(b"<a><b>old <i>text</i></b><c/><d></d></a>")
    b, c, d = list(doc.root.elements())
    ed.set_content(b, "new & <safe>")
    ed.set_content(c, "filled")
    ed.set_content(d, "")
    assert ed.result() == b"<a><b>new &amp; &lt;safe&gt;</b><c>filled</c><d></d></a>"


def test_emptying_the_root_keeps_its_declarations():
    data = b'<?xml version="1.0"?>\n<cp:core xmlns:cp="urn:cp" xmlns:dc="urn:dc"><dc:creator>J</dc:creator></cp:core>'
    doc, ed = edit(data)
    ed.set_content(doc.root, "")
    assert ed.result() == b'<?xml version="1.0"?>\n<cp:core xmlns:cp="urn:cp" xmlns:dc="urn:dc"></cp:core>'


def test_edits_inside_removed_or_replaced_elements_are_dropped():
    doc, ed = edit()
    p = element(doc, "p")
    ins = element(doc, "ins")
    ed.set_attr(ins, "w:author", "Author")
    ed.remove(ins)
    ed.remove(p)
    ed.remove(doc.comments()[0])
    assert ed.result() == WORD[:WORD.index(b"  <w:p")] + b"  \n</w:document>"
    doc, ed = edit()
    ed.remove(element(doc, "t"))
    ed.set_content(element(doc, "r"), "")
    assert b"<w:r></w:r>" in ed.result()


def test_removing_comments():
    doc, ed = edit()
    ed.remove(doc.comments()[0])
    assert b"<!--" not in ed.result()


def test_the_root_cannot_be_removed():
    doc, ed = edit()
    with pytest.raises(ValueError):
        ed.remove(doc.root)


def test_overlapping_edits_are_a_programming_error():
    doc, ed = edit(b"<a><b>x</b></a>")
    ed.set_content(doc.root, "")
    ed.set_attr(doc.root, "y", "1")
    assert ed.result() == b'<a y="1"></a>'


def test_attribute_values_with_angle_brackets_and_quotes():
    data = b"<a title='x > y' alt=\"it's\"><b/></a>"
    doc, ed = edit(data)
    ed.remove_attr(doc.root, "title")
    assert ed.result() == b"<a alt=\"it's\"><b/></a>"


def test_non_ascii_text_keeps_byte_offsets():
    data = "<a><b>café über</b><c n='é'/></a>".encode("utf-8")
    doc, ed = edit(data)
    b, c = list(doc.root.elements())
    ed.remove(b)
    ed.set_attr(c, "n", "x")
    assert ed.result() == b'<a><c n="x"/></a>'
