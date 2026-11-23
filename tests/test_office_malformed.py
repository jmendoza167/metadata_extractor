"""Damaged and hostile Office files: parse, strip and verify must either
work or raise one of metaex's own errors, never crash or hang."""

from __future__ import annotations

import random

import pytest

from helpers.ooxml import (kitchen_sink_docx, kitchen_sink_pptx, kitchen_sink_xlsx,
                           macro_docm)
from helpers.zip import Entry, build_zip
from metaex.core.errors import MetaexError
from metaex.core.report import Report, render_json, render_text
from metaex.core.verify import verify
from metaex.core.ziparchive import open_archive
from metaex.formats import docx as docx_format
from metaex.formats import pptx as pptx_format
from metaex.formats import xlsx as xlsx_format

FORMATS = [("docx", kitchen_sink_docx, docx_format), ("docm", macro_docm, docx_format),
           ("xlsx", kitchen_sink_xlsx, xlsx_format), ("pptx", kitchen_sink_pptx, pptx_format)]


def mutate_bytes(data: bytes, rng: random.Random) -> bytes:
    d = bytearray(data)
    op = rng.random()
    if op < 0.6:
        for _ in range(rng.randint(1, 8)):
            d[rng.randrange(len(d))] = rng.randrange(256)
    elif op < 0.8:
        del d[rng.randrange(len(d)):]
    else:
        pos = rng.randrange(len(d))
        d[pos:pos] = rng.randbytes(rng.randint(1, 16))
    return bytes(d)


def mutate_part(data: bytes, rng: random.Random) -> bytes:
    """Damage one part and rebuild the archive with correct CRCs, so the
    damage reaches the XML and package logic instead of the ZIP checks."""
    archive = open_archive(data)
    files = {n: archive.read(n) for n in archive.names()}
    name = rng.choice(sorted(files))
    part = bytearray(files[name])
    op = rng.random()
    if op < 0.4 and part:
        for _ in range(rng.randint(1, 4)):
            part[rng.randrange(len(part))] = rng.choice(b'<>/"=: xA0\x00&;')
    elif op < 0.6:
        files.pop(name)
    elif op < 0.8 and part:
        a = rng.randrange(len(part))
        del part[a:a + rng.randint(1, 40)]
    else:
        pos = rng.randrange(len(part) + 1)
        part[pos:pos] = rng.choice([b"<x/>", b"</w:p>", b'r:id="rId99"', b"&bogus;",
                                    b"<!-- c -->", b"\xff\xfe", b'Target="../../a"'])
    if name in files:
        files[name] = bytes(part)
    return build_zip([Entry(n, d) for n, d in files.items()])


def exercise(module, data: bytes) -> str:
    """parse, render, strip and verify; returns how far it got."""
    try:
        parsed = module.parse(data)
    except MetaexError:
        return "refused"
    report = Report("mutated", parsed["sections"] + [parsed["structure"]], parsed["warnings"])
    render_text(report, width=100)
    render_json(report)
    try:
        cleaned = module.strip(data)
    except MetaexError:
        return "parsed"
    try:
        verify(cleaned, data, filename="x." + module.__name__.rsplit(".", 1)[-1])
    except MetaexError:
        return "stripped"
    return "verified"


@pytest.mark.parametrize("kind, build, module", FORMATS, ids=[f[0] for f in FORMATS])
def test_random_byte_mutations(kind, build, module):
    rng = random.Random(f"bytes-{kind}")
    original = build()
    outcomes: dict[str, int] = {}
    for _ in range(300):
        outcome = exercise(module, mutate_bytes(original, rng))
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    assert outcomes.get("refused", 0) > 100  # most byte flips break a CRC


@pytest.mark.parametrize("kind, build, module", FORMATS, ids=[f[0] for f in FORMATS])
def test_random_part_mutations(kind, build, module):
    rng = random.Random(f"parts-{kind}")
    original = build()
    outcomes: dict[str, int] = {}
    for _ in range(300):
        outcome = exercise(module, mutate_part(original, rng))
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    assert outcomes.get("verified", 0) > 30  # many still strip and verify
    assert outcomes.get("refused", 0) + outcomes.get("parsed", 0) > 30


def test_strip_output_of_mutated_files_never_keeps_the_author():
    """Whatever survives stripping no longer names Jane Doe in its properties."""
    from metaex.core.ooxml import core_section, open_package

    rng = random.Random("authors")
    original = kitchen_sink_docx()
    for _ in range(150):
        data = mutate_part(original, rng)
        try:
            cleaned = docx_format.strip(data)
        except MetaexError:
            continue
        core = core_section(open_package(cleaned))
        assert core is None


def test_deeply_nested_xml_is_refused():
    archive = open_archive(kitchen_sink_docx())
    files = {n: archive.read(n) for n in archive.names()}
    files["word/document.xml"] = (b'<w:document xmlns:w="http://schemas.openxmlformats.org/'
                                  b'wordprocessingml/2006/main">' + b"<w:p>" * 300
                                  + b"</w:p>" * 300 + b"</w:document>")
    data = build_zip([Entry(n, d) for n, d in files.items()])
    with pytest.raises(MetaexError, match="nested more than"):
        docx_format.strip(data)


def test_relationship_cycles_do_not_hang():
    from helpers.ooxml import docx

    pkg = docx()
    pkg.link("word/document.xml", "subDocument", "word/document.xml")
    pkg.add_linked("word/document.xml", "glossaryDocument", "word/glossary/document.xml",
                   pkg.parts["word/document.xml"])
    pkg.link("word/glossary/document.xml", "glossaryDocument", "word/document.xml")
    data = pkg.build()
    docx_format.parse(data)
    cleaned = docx_format.strip(data)
    assert verify(cleaned, data, filename="x.docx").clean
