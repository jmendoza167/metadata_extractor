"""SVG: strip keeps only what is drawn (a whitelist). Elements, attributes
and text that are not part of the drawing are reported by show and risk,
removed by strip, and make verify fail on their own. Regression tests for
SVG files whose data survived because only known metadata was removed."""

import pytest

from helpers.svg import simple_svg
from metaex.commands.risk import assess_file
from metaex.core.errors import CannotCleanError
from metaex.core.verify import verify
from metaex.formats import svg

NO_NAMESPACE = (b'<svg><metadata><x>Jane Doe jane.doe@example.com</x></metadata>'
                b'<desc>secret</desc><rect width="1" height="1"/></svg>')

HIDDEN = [
    # an element in another namespace (not drawn)
    '<x:info xmlns:x="urn:example:app">Jane Doe jane.doe@example.com</x:info>',
    # attributes no renderer uses
    '<rect width="1" height="1" data-author="Jane Doe"/>',
    '<rect xmlns:x="urn:example:app" width="1" height="1" x:owner="jane.doe"/>',
    # an element that is not an SVG element
    '<author>Jane Doe</author>',
    # text outside the text elements is never displayed
    '<g>Jane Doe jane.doe@example.com</g>',
]
DRAWIO = ('content="&lt;mxfile host=&quot;Electron&quot; modified=&quot;2024-05-01T10:00:00Z'
          '&quot; agent=&quot;Jane Doe example agent&quot;&gt;&lt;/mxfile&gt;"')


def other_data(data):
    return [s for s in svg.parse(data)["sections"] if s.key == "svg_other"]


def test_svg_without_the_svg_namespace_is_refused():
    with pytest.raises(CannotCleanError, match="not in the SVG namespace"):
        svg.strip(NO_NAMESPACE)
    # show/risk list what it holds, and verify fails on its own
    (section,) = other_data(NO_NAMESPACE)
    assert any("Jane Doe" in str(f.value) for f in section.fields)
    result = verify(NO_NAMESPACE)
    assert not result.clean
    assert any("SVG namespace" in p for p in result.problems)


def test_namespace_typo_is_refused():
    data = simple_svg("<metadata>Jane Doe</metadata>").replace(
        b'xmlns="http://www.w3.org/2000/svg"', b'xmlns="http:/www.w3.org/2000/svg"')
    with pytest.raises(CannotCleanError):
        svg.strip(data)
    assert not verify(data).clean


@pytest.mark.parametrize("body", HIDDEN)
def test_data_that_is_not_drawn_is_removed(body):
    data = simple_svg(body + '<rect x="1" y="1" width="8" height="8" fill="red"/>')
    assert other_data(data), "show/risk must list it"
    before = verify(data)
    assert not before.clean
    assert any(f.section == "Data that is not part of the drawing" for f in before.leftovers)
    cleaned = svg.strip(data)
    assert b"Jane" not in cleaned and b"jane" not in cleaned
    assert b'fill="red"' in cleaned
    after = verify(cleaned, data)
    assert after.clean and after.image_data_match is True


def test_drawio_content_attribute_is_removed(tmp_path):
    data = simple_svg("<rect/>", attrs=DRAWIO)
    path = tmp_path / "diagram.svg"
    path.write_bytes(data)
    _report, findings, _strippable = assess_file(str(path))
    assert any("mxfile" in f.value for f in findings)
    assert not verify(data).clean
    cleaned = svg.strip(data)
    assert b"mxfile" not in cleaned and b"content=" not in cleaned
    assert verify(cleaned, data).clean


def test_drawn_content_is_kept():
    body = ('<defs><linearGradient id="g"><stop offset="0" stop-color="#fff"/>'
            '</linearGradient></defs>'
            '<g transform="translate(1 1)" role="img" aria-label="plan">'
            '<path d="M0 0 L5 5" stroke="#000" stroke-width="2" stroke-dasharray="1 1"/>'
            '<use xlink:href="#g" x="2"/>'
            '<text x="1" y="5" font-family="serif" xml:space="preserve">Kitchen</text></g>')
    data = simple_svg(body, attrs='viewBox="0 0 10 10" preserveAspectRatio="none"')
    assert other_data(data) == []
    cleaned = svg.strip(data)
    for kept in ('stroke-dasharray="1 1"', 'xlink:href="#g"', 'aria-label="plan"',
                 '>Kitchen</text>', 'viewBox="0 0 10 10"', 'stop-color="#fff"'):
        assert kept.encode() in cleaned, kept
    assert verify(cleaned, data).clean


def test_html_text_in_foreign_object():
    body = ('<foreignObject width="5" height="5"><div xmlns="http://www.w3.org/1999/xhtml" '
            'style="color:red" data-user="jane.doe"><b>Kitchen</b></div></foreignObject>')
    cleaned = svg.strip(simple_svg(body))
    assert b"<b>Kitchen</b>" in cleaned and b'style="color:red"' in cleaned
    assert b"jane.doe" not in cleaned
    assert verify(cleaned, simple_svg(body)).clean


@pytest.mark.parametrize("inner", [
    '<img xmlns="http://www.w3.org/1999/xhtml" src="data:image/png;base64,AAAA"/>',
    '<x:note xmlns:x="urn:example:app">Jane Doe</x:note>',
])
def test_foreign_object_content_metaex_cant_check_is_refused(inner):
    data = simple_svg(f'<foreignObject width="5" height="5">{inner}</foreignObject>')
    with pytest.raises(CannotCleanError, match="foreignObject"):
        svg.strip(data)
    assert not verify(data).clean
