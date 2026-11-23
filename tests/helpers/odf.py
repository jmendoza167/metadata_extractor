"""Build OpenDocument files (ODT, ODS, ODP) in memory, the way LibreOffice
lays them out: "mimetype" first and stored, then the XML files, the
manifest last. Entries are STORED with fixed times, so the bytes are the
same on every machine. All metadata is made up (Jane Doe, jane.doe).
"""

from __future__ import annotations

import base64
from xml.sax.saxutils import escape

from helpers.zip import STORED, Entry, build_zip

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "dc": "http://purl.org/dc/elements/1.1/",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "config": "urn:oasis:names:tc:opendocument:xmlns:config:1.0",
    "officeooo": "http://openoffice.org/2009/office",
    "loext": "urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0",
}
DECLS = " ".join(f'xmlns:{p}="{u}"' for p, u in NS.items())
DECL = '<?xml version="1.0" encoding="UTF-8"?>\n'
MANIFEST_NS = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"

MIMETYPES = {"odt": "application/vnd.oasis.opendocument.text",
             "ods": "application/vnd.oasis.opendocument.spreadsheet",
             "odp": "application/vnd.oasis.opendocument.presentation"}
BODIES = {
    "odt": "<office:text>{}</office:text>",
    "ods": "<office:spreadsheet>{}</office:spreadsheet>",
    "odp": "<office:presentation>{}</office:presentation>",
}
DEFAULT_CONTENT = {
    "odt": '<text:p text:style-name="P1">Hello</text:p>',
    "ods": ('<table:table table:name="Sheet1"><table:table-row><table:table-cell '
            'office:value-type="string"><text:p>Hello</text:p></table:table-cell>'
            "</table:table-row></table:table>"),
    "odp": ('<draw:page draw:name="page1"><draw:frame><draw:text-box><text:p>Hello</text:p>'
            "</draw:text-box></draw:frame></draw:page>"),
}


def content_xml(kind: str, body: str = "", styles: str = "") -> str:
    return (f'{DECL}<office:document-content {DECLS} office:version="1.3">'
            f"<office:automatic-styles>{styles}</office:automatic-styles>"
            f"<office:body>{BODIES[kind].format(body or DEFAULT_CONTENT[kind])}</office:body>"
            "</office:document-content>")


def styles_xml(inner: str = "") -> str:
    return (f'{DECL}<office:document-styles {DECLS} office:version="1.3">'
            f"<office:styles>{inner}</office:styles></office:document-styles>")


JANE_META = (
    "<meta:initial-creator>Jane Doe</meta:initial-creator>"
    "<meta:creation-date>2024-05-01T10:00:00.000000000</meta:creation-date>"
    "<dc:creator>John Example</dc:creator><dc:date>2024-05-02T09:30:00.000000000</dc:date>"
    "<meta:editing-cycles>7</meta:editing-cycles><meta:editing-duration>PT42M10S"
    "</meta:editing-duration><meta:generator>LibreOffice/7.6.4.1$Linux_X86_64 "
    "LibreOffice_project/example</meta:generator>"
    '<meta:template xlink:type="simple" xlink:actuate="onRequest" xlink:title="Letter" '
    'xlink:href="file:///home/jane.doe/Templates/Letter.ott" '
    'meta:date="2024-04-30T08:00:00.000000000"/>'
    "<meta:printed-by>Jane Doe</meta:printed-by>"
    "<meta:print-date>2024-05-02T08:00:00.000000000</meta:print-date>"
    "<dc:title>Floor plan notes</dc:title><dc:subject>Flat</dc:subject>"
    "<dc:description>Second draft for the landlord</dc:description>"
    "<meta:keyword>plan</meta:keyword><dc:language>en-US</dc:language>"
    '<meta:user-defined meta:name="Client" meta:value-type="string">Jane Doe'
    "</meta:user-defined>"
    '<meta:document-statistic meta:page-count="1" meta:paragraph-count="3" '
    'meta:word-count="20" meta:character-count="120"/>')


def meta_xml(inner: str = JANE_META) -> str:
    return (f'{DECL}<office:document-meta {DECLS} office:version="1.3">'
            f"<office:meta>{inner}</office:meta></office:document-meta>")


def printer_setup(printer: str = "HP LaserJet Example") -> str:
    raw = (b"\x9a\x01\x10SJ\x00" + printer.encode("ascii").ljust(64, b"\x00")
           + b"HPDriverExample\x00".ljust(32, b"\x00") + b"\x00" * 60)
    return base64.b64encode(raw).decode("ascii")


def settings_xml(printer: bool = True, rsid: bool = True) -> str:
    items = ['<config:config-item config:name="ViewAreaTop" config:type="long">0'
             "</config:config-item>"]
    if printer:
        items.append('<config:config-item config:name="PrinterName" config:type="string">'
                     "HP LaserJet Example</config:config-item>")
        items.append('<config:config-item config:name="PrinterSetup" '
                     f'config:type="base64Binary">{printer_setup()}</config:config-item>')
    if rsid:
        items.append('<config:config-item config:name="Rsid" config:type="int">1234567'
                     "</config:config-item>")
        items.append('<config:config-item config:name="RsidRoot" config:type="int">7654321'
                     "</config:config-item>")
    return (f'{DECL}<office:document-settings {DECLS} office:version="1.3">'
            '<office:settings><config:config-item-set config:name="ooo:configuration-settings">'
            f"{''.join(items)}</config:config-item-set></office:settings>"
            "</office:document-settings>")


def manifest_xml(entries: dict[str, str], mimetype: str) -> str:
    lines = [f'<manifest:file-entry manifest:full-path="/" manifest:version="1.3" '
             f'manifest:media-type="{mimetype}"/>']
    lines += [f'<manifest:file-entry manifest:full-path="{escape(path)}" '
              f'manifest:media-type="{media}"/>' for path, media in entries.items()]
    return (f'{DECL}<manifest:manifest xmlns:manifest="{MANIFEST_NS}" manifest:version="1.3">'
            f"{''.join(lines)}</manifest:manifest>")


class Document:
    """An OpenDocument file under construction."""

    def __init__(self, kind: str, body: str = "", automatic_styles: str = "") -> None:
        self.kind = kind
        self.files: dict[str, tuple[bytes, str]] = {}
        self.add("content.xml", content_xml(kind, body, automatic_styles), "text/xml")
        self.add("styles.xml", styles_xml(), "text/xml")
        self.unlisted: dict[str, bytes] = {}

    def add(self, name: str, data: bytes | str, media_type: str) -> Document:
        self.files[name] = (data.encode("utf-8") if isinstance(data, str) else data, media_type)
        return self

    def entries(self) -> list[Entry]:
        mimetype = MIMETYPES[self.kind]
        out = [Entry("mimetype", mimetype.encode("ascii"))]
        out += [Entry(name, data) for name, (data, _m) in self.files.items()]
        out += [Entry(name, data) for name, data in self.unlisted.items()]
        listed = {name: media for name, (_d, media) in self.files.items()}
        folders = {n.rsplit("/", 1)[0] + "/": "" for n in self.files if "/" in n}
        for folder in folders:
            if folder.startswith("Configurations2/"):
                listed.setdefault("Configurations2/", "application/vnd.sun.xml.ui.configuration")
        out.append(Entry("META-INF/manifest.xml", manifest_xml(listed, mimetype).encode()))
        return out

    def build(self, method: int = STORED, **zip_options) -> bytes:
        entries = self.entries()
        for e in entries[1:]:
            e.method = method
        return build_zip(entries, **zip_options)


def with_meta(doc: Document, settings: bool = True) -> Document:
    doc.add("meta.xml", meta_xml(), "text/xml")
    if settings:
        doc.add("settings.xml", settings_xml(), "text/xml")
    return doc


# -- comments, tracked changes, hidden content ---------------------------------------------------

ANNOTATION = ('<office:annotation><dc:creator>Jane Doe</dc:creator>'
              "<dc:date>2024-05-01T10:00:00.000000000</dc:date>"
              "<meta:creator-initials>JD</meta:creator-initials>"
              "<text:p>Ask about stairs</text:p></office:annotation>")

TRACKED = (
    '<text:tracked-changes text:track-changes="true">'
    '<text:changed-region xml:id="ct1" text:id="ct1"><text:deletion><office:change-info>'
    "<dc:creator>Jane Doe</dc:creator><dc:date>2024-05-01T10:15:00</dc:date>"
    "</office:change-info><text:p>twelve hundred</text:p></text:deletion></text:changed-region>"
    '<text:changed-region xml:id="ct2" text:id="ct2"><text:insertion><office:change-info>'
    "<dc:creator>John Example</dc:creator><dc:date>2024-05-02T09:00:00</dc:date>"
    "</office:change-info></text:insertion></text:changed-region></text:tracked-changes>")

ODT_BODY = (
    TRACKED
    + '<text:p text:style-name="P1" officeooo:rsid="0001a2b3">The kitchen '
    + ANNOTATION + 'is small. The rent is <text:change text:change-id="ct1"/>'
    '<text:change-start text:change-id="ct2"/>950<text:change-end text:change-id="ct2"/>'
    " euros.</text:p>"
    '<text:p text:style-name="P1">See <text:a xlink:type="simple" '
    'xlink:href="https://example.com/flat">the plan</text:a>.<text:hidden-text '
    'text:condition="ooow:1" text:string-value="Secret note to self"/></text:p>')
ODT_STYLES = ('<style:style style:name="P1" style:family="paragraph">'
              '<style:text-properties officeooo:rsid="0001a2b3" '
              'officeooo:paragraph-rsid="0001a2b3"/></style:style>')

ODS_BODY = (
    '<table:table table:name="Budget" table:style-name="ta1"><table:table-row>'
    '<table:table-cell office:value-type="float" office:value="950">' + ANNOTATION
    + "<text:p>950</text:p></table:table-cell></table:table-row></table:table>"
    '<table:table table:name="Salaries" table:style-name="ta2"><table:table-row>'
    '<table:table-cell office:value-type="float" office:value="1234.5"><text:p>1234.5</text:p>'
    "</table:table-cell></table:table-row></table:table>")
ODS_STYLES = ('<style:style style:name="ta1" style:family="table"><style:table-properties '
              'table:display="true"/></style:style><style:style style:name="ta2" '
              'style:family="table"><style:table-properties table:display="false"/>'
              "</style:style>")

ODP_BODY = (
    '<draw:page draw:name="page1" draw:style-name="dp1"><draw:frame><draw:text-box>'
    "<text:p>Floor plan</text:p></draw:text-box></draw:frame>" + ANNOTATION
    + "<presentation:notes><draw:frame><draw:text-box><text:p>Mention the deadline: "
    "2024-06-01</text:p></draw:text-box></draw:frame></presentation:notes></draw:page>"
    '<draw:page draw:name="page2" draw:style-name="dp2"><draw:frame><draw:text-box>'
    "<text:p>Draft numbers</text:p></draw:text-box></draw:frame></draw:page>")
ODP_STYLES = ('<style:style style:name="dp1" style:family="drawing-page">'
              '<style:drawing-page-properties presentation:visibility="visible"/></style:style>'
              '<style:style style:name="dp2" style:family="drawing-page">'
              '<style:drawing-page-properties presentation:visibility="hidden"/></style:style>')

KITCHEN = {"odt": (ODT_BODY, ODT_STYLES), "ods": (ODS_BODY, ODS_STYLES),
           "odp": (ODP_BODY, ODP_STYLES)}

BASIC_MODULE = (f'{DECL}<script:module xmlns:script="http://openoffice.org/2000/script" '
                'script:name="Module1" script:language="StarBasic">Sub Main\n'
                "  MsgBox &quot;placeholder macro&quot;\nEnd Sub\n</script:module>")


def kitchen_sink(kind: str, macros: bool = True) -> bytes:
    """meta.xml with every field, a printer and rsids in settings.xml, a
    comment, tracked changes (ODT), hidden content, a thumbnail, a photo
    with GPS in Pictures/, user interface settings and a Basic macro."""
    from helpers import jpeg, png

    body, styles = KITCHEN[kind]
    doc = with_meta(Document(kind, body, styles))
    doc.add("Thumbnails/thumbnail.png", png.make_png(width=8, height=8), "image/png")
    doc.add("Pictures/10000000000000300000002096C8C1A2.jpg", jpeg.sample_photo(), "image/jpeg")
    doc.add("Configurations2/accelerator/current.xml", b"", "")
    if macros:
        doc.add("Basic/Standard/Module1.xml", BASIC_MODULE, "text/xml")
    return doc.build()
