"""SVG: the kitchen sink, editor styles, refusals and random mutations."""

import random

import pytest

from helpers.svg import (SVG_NS, XLINK_NS, illustrator_svg, inkscape_svg, kitchen_sink_svg,
                         simple_svg)
from metaex.core.errors import CannotCleanError, MalformedFileError
from metaex.core.report import Report, render_json, render_text
from metaex.core.verify import verify
from metaex.formats import svg

# -- kitchen sink ------------------------------------------------------------------------


def test_kitchen_sink_show():
    parsed = svg.parse(kitchen_sink_svg())
    keys = [s.key for s in parsed["sections"]]
    assert keys[:7] == ["svg_metadata", "title_desc", "comments", "processing_instructions",
                        "editor", "local_paths", "embedded_images"]
    titles = [s.title for s in parsed["sections"]]
    for title in ("Embedded image 1 (JPEG): GPS", "Embedded image 2 (PNG): Text",
                  "Embedded image 3 (SVG): Comments"):
        assert title in titles
    w = parsed["warnings"]
    assert any("<script>" in x for x in w) and any("tracker.example" in x for x in w)
    critical = [x for x in w if x.startswith("CRITICAL")]
    assert len(critical) == 3  # local paths, embedded JPEG GPS (EXIF and XMP)


@pytest.mark.parametrize("paranoid", [False, True])
def test_kitchen_sink_strip(paranoid):
    original = kitchen_sink_svg()
    cleaned = svg.strip(original, paranoid=paranoid)
    result = verify(cleaned, original)
    assert result.clean, (result.leftovers, result.problems)
    assert result.image_data_match
    for secret in (b"Jane Doe", b"/home/jane", b"sodipodi", b"inkscape", b"xml-stylesheet",
                   b"<!--", b"<metadata"):
        assert secret not in cleaned, secret
    assert (b"<desc" in cleaned) != paranoid
    # what is not metadata stays, with a warning
    assert b"<script>" in cleaned and b"https://tracker.example/pixel.png" in cleaned
    assert svg.strip(cleaned, paranoid=paranoid) == cleaned


@pytest.mark.parametrize("make", [inkscape_svg, kitchen_sink_svg])
def test_namespaces_are_preserved(make):
    cleaned = svg.strip(make()).decode("utf-8")
    root = cleaned.split(">", 2)[1]  # the <svg ...> start tag
    assert f'xmlns="{SVG_NS}"' in root and f'xmlns:xlink="{XLINK_NS}"' in root
    assert "ns0" not in cleaned and "xlink:href=" in cleaned
    doc = svg.parse_document(cleaned.encode("utf-8"))
    assert doc.root.uri == SVG_NS and doc.root.name == "svg"


def test_illustrator_style_is_refused_only_for_its_linked_file():
    with pytest.raises(CannotCleanError, match="local files"):
        svg.strip(illustrator_svg())
    fixed = illustrator_svg().replace(b"C:\\Users\\Jane Doe\\Documents\\", b"")
    result = verify(svg.strip(fixed), fixed)
    assert result.clean and result.image_data_match


@pytest.mark.parametrize("data", [
    b'<?xml version="1.0"?>\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1 Tiny//EN" '
    b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11-tiny.dtd">\n<svg xmlns="'
    + SVG_NS.encode() + b'"/>',
    b'<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
    b"<svg>&lol2;</svg>",
    b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><svg>&xxe;</svg>',
])
def test_unsupported_doctype_and_entities_are_refused_everywhere(data):
    with pytest.raises(MalformedFileError, match="DOCTYPE|ENTITY"):
        svg.parse(data)
    with pytest.raises(MalformedFileError, match="DOCTYPE|ENTITY"):
        svg.strip(data)


def test_doctype_inside_an_embedded_svg_is_reported_not_parsed():
    from helpers.svg import data_uri

    inner = b'<!DOCTYPE svg [<!ENTITY a "b">]><svg>&a;</svg>'
    data = simple_svg(f'<image href="{data_uri(inner, "image/svg+xml")}"/>')
    listing = next(s for s in svg.parse(data)["sections"] if s.key == "embedded_images")
    assert "ENTITY declaration" in listing.fields[0].value
    with pytest.raises(CannotCleanError):
        svg.strip(data)


def test_deep_nesting_is_refused_without_recursion_errors():
    deep = simple_svg("<g>" * 1000 + "</g>" * 1000)
    with pytest.raises(MalformedFileError, match="nested more than"):
        svg.parse(deep)


# -- mutation tests ----------------------------------------------------------------------


def mutate(data: bytes, rng: random.Random) -> bytes:
    d = bytearray(data)
    op = rng.random()
    if op < 0.5:
        for _ in range(rng.randint(1, 4)):
            d[rng.randrange(len(d))] = rng.choice(b'<>/="\' abcxyz:-&;!?0123456789\n\x00\xff')
    elif op < 0.7:
        del d[rng.randrange(len(d)):]
    elif op < 0.85:
        start = rng.randrange(len(d))
        del d[start : start + rng.randint(1, 40)]
    else:
        pos = rng.randrange(len(d))
        d[pos:pos] = rng.choice([b"<!-- x -->", b"<g>", b"</g>", b' a="b"', b"&amp;", b"<?p q?>",
                                 b"<sodipodi:x/>", b"<![CDATA[x]]>", rng.randbytes(4)])
    return bytes(d)


ORIGINALS = [kitchen_sink_svg(), inkscape_svg(), illustrator_svg(),
             simple_svg('<text x="1">Hello <tspan>world</tspan></text>')]


def test_random_mutations_only_raise_malformed_file_error():
    """1,000 random mutations: parse (and rendering) succeeds or raises
    MalformedFileError; any other exception is a bug."""
    rng = random.Random(20261120)
    ok = 0
    for _ in range(1000):
        data = mutate(rng.choice(ORIGINALS), rng)
        try:
            result = svg.parse(data)
        except MalformedFileError:
            continue
        report = Report("mutated.svg", result["sections"] + [result["structure"]],
                        result["warnings"])
        render_text(report, width=100)
        render_json(report)
        ok += 1
    assert ok > 150


def test_mutations_either_raise_or_produce_clean_output():
    """500 random mutations: strip raises MalformedFileError or CannotCleanError,
    or returns an SVG that verifies clean with the same drawing."""
    rng = random.Random(31337)
    stripped = 0
    for i in range(500):
        data = mutate(rng.choice(ORIGINALS), rng)
        paranoid = bool(i % 2)
        try:
            cleaned = svg.strip(data, paranoid=paranoid)
        except (MalformedFileError, CannotCleanError):
            continue
        stripped += 1
        result = verify(cleaned, data)
        assert result.clean, (i, result.leftovers, result.problems)
        assert result.image_data_match is True
        assert svg.strip(cleaned, paranoid=paranoid) == cleaned
    # Most mutations break the XML (or the embedded base64), so fewer stay
    # strippable than for binary formats.
    assert stripped > 25
