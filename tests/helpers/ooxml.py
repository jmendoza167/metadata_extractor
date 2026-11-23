"""Build Office Open XML packages (DOCX, XLSX, PPTX) in memory.

A Package collects parts, their content types and the relationships
between them, and writes [Content_Types].xml and the _rels/*.rels parts
when built. Parts go into the archive in the order they were added, after
[Content_Types].xml and _rels/.rels, like Office writes them. With the
default STORED entries and fixed times the bytes are the same on every
machine.
"""

from __future__ import annotations

import posixpath
from xml.sax.saxutils import escape, quoteattr

from helpers.zip import STORED, Entry, build_zip

CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships/"
MS_REL = "http://schemas.microsoft.com/office/2006/relationships/"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'

RELS_TYPE = "application/vnd.openxmlformats-package.relationships+xml"
WORD_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
WORD_MACRO = "application/vnd.ms-word.document.macroEnabled.main+xml"
WORD_TEMPLATE = "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml"
EXCEL_MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
EXCEL_MACRO = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
EXCEL_BINARY = "application/vnd.ms-excel.sheet.binary.macroEnabled.main"
SHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
PPT_MAIN = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
PPT_MACRO = "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml"
SLIDE = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
VBA = "application/vnd.ms-office.vbaProject"


class Package:
    def __init__(self) -> None:
        self.parts: dict[str, bytes] = {}
        self.defaults: dict[str, str] = {"rels": RELS_TYPE, "xml": "application/xml"}
        self.overrides: dict[str, str] = {}
        self.rels: dict[str, list[tuple[str, str, str, bool]]] = {}
        self.extra_entries: dict[str, bytes] = {}

    def add(self, name: str, data: bytes | str, content_type: str | None = None) -> Package:
        self.parts[name] = data.encode("utf-8") if isinstance(data, str) else data
        if content_type is not None:
            self.overrides[name] = content_type
        return self

    def link(self, source: str, rel_type: str, target: str, *, external: bool = False,
             rel_id: str | None = None) -> str:
        """Add a relationship from ``source`` ("" = the package); ``target`` is
        a part name for internal links (written relative to the source)."""
        items = self.rels.setdefault(source, [])
        rel_id = rel_id or f"rId{len(items) + 1}"
        if not external and not target.startswith("/"):
            target = posixpath.relpath(target, posixpath.dirname(source) or ".")
        items.append((rel_id, rel_type if "/" in rel_type else REL + rel_type, target, external))
        return rel_id

    def add_linked(self, source: str, rel_type: str, name: str, data: bytes | str,
                   content_type: str | None = None) -> str:
        self.add(name, data, content_type)
        return self.link(source, rel_type, name)

    def content_types(self) -> bytes:
        out = [DECL, f'<Types xmlns="{CT_NS}">']
        for ext, ctype in self.defaults.items():
            out.append(f'<Default Extension="{ext}" ContentType="{ctype}"/>')
        for name, ctype in self.overrides.items():
            out.append(f'<Override PartName="/{name}" ContentType="{ctype}"/>')
        out.append("</Types>")
        return "".join(out).encode("utf-8")

    def rels_part(self, source: str) -> tuple[str, bytes]:
        if source == "":
            name = "_rels/.rels"
        else:
            folder, base = posixpath.split(source)
            name = posixpath.join(folder, "_rels", base + ".rels")
        out = [DECL, f'<Relationships xmlns="{RELS_NS}">']
        for rel_id, rel_type, target, external in self.rels[source]:
            mode = ' TargetMode="External"' if external else ""
            out.append(f'<Relationship Id="{rel_id}" Type="{rel_type}" '
                       f'Target={quoteattr(target)}{mode}/>')
        out.append("</Relationships>")
        return name, "".join(out).encode("utf-8")

    def files(self) -> dict[str, bytes]:
        files = {"[Content_Types].xml": self.content_types()}
        if "" in self.rels:
            name, data = self.rels_part("")
            files[name] = data
        for name, data in self.parts.items():
            files[name] = data
            if name in self.rels:
                rname, rdata = self.rels_part(name)
                files[rname] = rdata
        files.update(self.extra_entries)
        return files

    def build(self, method: int = STORED, **zip_options) -> bytes:
        return build_zip([Entry(name, data, method=method) for name, data in self.files().items()],
                         **zip_options)


# -- minimal documents ------------------------------------------------------------------


def paragraph(text: str) -> str:
    return f"<w:p><w:r><w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r></w:p>"


def word_document(body: str = "", extra_ns: str = "") -> str:
    return (f'{DECL}<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}"{extra_ns}>'
            f"<w:body>{body or paragraph('Hello')}<w:sectPr/></w:body></w:document>")


def docx(body: str = "", *, main_type: str = WORD_MAIN, extra_ns: str = "") -> Package:
    pkg = Package()
    pkg.add_linked("", "officeDocument", "word/document.xml", word_document(body, extra_ns),
                   main_type)
    return pkg


def workbook(sheets: str = '<sheet name="Sheet1" sheetId="1" r:id="rId1"/>') -> str:
    return (f'{DECL}<workbook xmlns="{S_NS}" xmlns:r="{R_NS}">'
            f"<sheets>{sheets}</sheets></workbook>")


def worksheet(rows: str = '<row r="1"><c r="A1" t="inlineStr"><is><t>Hello</t></is></c></row>'
              ) -> str:
    return f'{DECL}<worksheet xmlns="{S_NS}"><sheetData>{rows}</sheetData></worksheet>'


def xlsx(*, main_type: str = EXCEL_MAIN) -> Package:
    pkg = Package()
    pkg.add_linked("", "officeDocument", "xl/workbook.xml", workbook(), main_type)
    pkg.add_linked("xl/workbook.xml", "worksheet", "xl/worksheets/sheet1.xml", worksheet(), SHEET)
    return pkg


def presentation(slides: str = '<p:sldId id="256" r:id="rId1"/>') -> str:
    return (f'{DECL}<p:presentation xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">'
            f"<p:sldIdLst>{slides}</p:sldIdLst></p:presentation>")


def slide(text: str = "Hello") -> str:
    return (f'{DECL}<p:sld xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}"><p:cSld><p:spTree>'
            f"<p:sp><p:txBody><a:p><a:r><a:t>{escape(text)}</a:t></a:r></a:p></p:txBody></p:sp>"
            "</p:spTree></p:cSld></p:sld>")


def pptx(*, main_type: str = PPT_MAIN) -> Package:
    pkg = Package()
    pkg.add_linked("", "officeDocument", "ppt/presentation.xml", presentation(), main_type)
    pkg.add_linked("ppt/presentation.xml", "slide", "ppt/slides/slide1.xml", slide(), SLIDE)
    return pkg


# -- document properties -----------------------------------------------------------------

CORE_TYPE = "application/vnd.openxmlformats-package.core-properties+xml"
APP_TYPE = "application/vnd.openxmlformats-officedocument.extended-properties+xml"
CUSTOM_TYPE = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
EP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"

CORE_PREFIX = {"title": "dc", "subject": "dc", "creator": "dc", "description": "dc",
               "identifier": "dc", "language": "dc", "created": "dcterms",
               "modified": "dcterms"}

JANE_CORE = {
    "title": "Floor plan notes", "subject": "Flat", "creator": "Jane Doe",
    "keywords": "plan; flat", "description": "Second draft for the landlord",
    "lastModifiedBy": "Jane Doe", "revision": "7", "lastPrinted": "2024-05-02T08:00:00Z",
    "created": "2024-05-01T10:00:00Z", "modified": "2024-05-02T09:30:00Z",
    "category": "Private", "contentStatus": "Draft", "identifier": "DOC-0001",
    "language": "en-US", "version": "1.0",
}

JANE_APP = {
    "Template": "C:\\Users\\jane.doe\\AppData\\Roaming\\Microsoft\\Templates\\Normal.dotm",
    "TotalTime": "42", "Pages": "1", "Words": "120", "Characters": "700",
    "Application": "Microsoft Office Word", "DocSecurity": "0", "Lines": "5",
    "Paragraphs": "2", "ScaleCrop": "false", "Manager": "John Example",
    "Company": "Example Corp", "LinksUpToDate": "false", "CharactersWithSpaces": "819",
    "SharedDoc": "false", "HyperlinkBase": "https://intranet.example.com/docs/",
    "HyperlinksChanged": "false", "AppVersion": "16.0000",
}


def core_xml(props: dict[str, str] | None = None) -> str:
    """docProps/core.xml as Word writes it (``props`` by local name)."""
    props = JANE_CORE if props is None else props
    out = [DECL, f'<cp:coreProperties xmlns:cp="{CP_NS}" xmlns:dc="http://purl.org/dc/elements/'
           '1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/'
           'dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">']
    for name, value in props.items():
        prefix = CORE_PREFIX.get(name, "cp")
        typed = ' xsi:type="dcterms:W3CDTF"' if prefix == "dcterms" else ""
        out.append(f"<{prefix}:{name}{typed}>{escape(value)}</{prefix}:{name}>")
    out.append("</cp:coreProperties>")
    return "".join(out)


def app_xml(props: dict[str, str] | None = None, titles: tuple[str, ...] = ("Floor plan",)) -> str:
    """docProps/app.xml as Word writes it."""
    props = JANE_APP if props is None else props
    out = [DECL, f'<Properties xmlns="{EP_NS}" xmlns:vt="{VT_NS}">']
    for name, value in props.items():
        out.append(f"<{name}>{escape(value)}</{name}>")
    if titles:
        out.append('<HeadingPairs><vt:vector size="2" baseType="variant"><vt:variant>'
                   "<vt:lpstr>Title</vt:lpstr></vt:variant><vt:variant><vt:i4>1</vt:i4>"
                   "</vt:variant></vt:vector></HeadingPairs>")
        items = "".join(f"<vt:lpstr>{escape(t)}</vt:lpstr>" for t in titles)
        out.append(f'<TitlesOfParts><vt:vector size="{len(titles)}" baseType="lpstr">{items}'
                   "</vt:vector></TitlesOfParts>")
    out.append("</Properties>")
    return "".join(out)


def with_properties(pkg: Package, core: str | None = None, app: str | None = None) -> Package:
    """Add docProps/core.xml and docProps/app.xml (Jane Doe's by default)."""
    pkg.add_linked("", PKG_REL + "metadata/core-properties", "docProps/core.xml",
                   core_xml() if core is None else core, CORE_TYPE)
    pkg.add_linked("", "extended-properties", "docProps/app.xml",
                   app_xml() if app is None else app, APP_TYPE)
    return pkg


# -- custom properties, labels, custom XML, settings ---------------------------------------

CUSTOM_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
LABEL_GUID = "11111111-2222-3333-4444-555555555555"
TENANT_GUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
JANE_CUSTOM = {
    "ProjectCode": "FLAT-42",
    "ContentTypeId": "0x0101000AAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    f"MSIP_Label_{LABEL_GUID}_Enabled": "true",
    f"MSIP_Label_{LABEL_GUID}_SetDate": "2024-05-01T10:00:00Z",
    f"MSIP_Label_{LABEL_GUID}_Name": "Confidential",
    f"MSIP_Label_{LABEL_GUID}_SiteId": TENANT_GUID,
}


def custom_xml(props: dict[str, str] | None = None) -> str:
    props = JANE_CUSTOM if props is None else props
    items = "".join(
        f'<property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="{i}" '
        f'name="{escape(name)}"><vt:lpwstr>{escape(value)}</vt:lpwstr></property>'
        for i, (name, value) in enumerate(props.items(), 2))
    return f'{DECL}<Properties xmlns="{CUSTOM_NS}" xmlns:vt="{VT_NS}">{items}</Properties>'


def label_info_xml() -> str:
    return (f'{DECL}<clbl:labelList xmlns:clbl="http://schemas.microsoft.com/office/2020/mipLabelMetadata">'
            f'<clbl:label id="{{{LABEL_GUID}}}" enabled="1" method="Standard" siteId="{{{TENANT_GUID}}}" '
            'removed="0"/></clbl:labelList>')


SHAREPOINT_ITEM = (
    f'{DECL}<p:properties xmlns:p="http://schemas.microsoft.com/office/2006/metadata/properties" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xmlns:pc="http://schemas.microsoft.com/office/infopath/2007/PartnerControls">'
    '<documentManagement><SharedWithUsers xmlns="aaaaaaaa-0000-0000-0000-000000000001">'
    '<UserInfo><DisplayName>Jane Doe</DisplayName><AccountId>12</AccountId>'
    '<AccountType/></UserInfo></SharedWithUsers>'
    '<_dlc_DocId xmlns="aaaaaaaa-0000-0000-0000-000000000001">EXAMPLE-123-456</_dlc_DocId>'
    '</documentManagement></p:properties>')

ITEM_PROPS = (f'{DECL}<ds:datastoreItem ds:itemID="{{AAAAAAAA-1111-2222-3333-444444444444}}" '
              'xmlns:ds="http://schemas.openxmlformats.org/officeDocument/2006/customXml">'
              '<ds:schemaRefs><ds:schemaRef ds:uri="http://schemas.microsoft.com/office/2006/'
              'metadata/properties"/></ds:schemaRefs></ds:datastoreItem>')
CUSTOM_XML_PROPS_TYPE = "application/vnd.openxmlformats-officedocument.customXmlProperties+xml"


def add_custom_xml(pkg: Package, source: str, item: str = SHAREPOINT_ITEM, n: int = 1) -> str:
    """customXml/item<n>.xml linked from ``source``, with its itemProps part."""
    name = f"customXml/item{n}.xml"
    pkg.add_linked(source, "customXml", name, item)
    pkg.add_linked(name, "customXmlProps", f"customXml/itemProps{n}.xml", ITEM_PROPS,
                   CUSTOM_XML_PROPS_TYPE)
    return name


SETTINGS_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"


def settings_xml(inner: str = "", extra_ns: str = "") -> str:
    return (f'{DECL}<w:settings xmlns:w="{W_NS}" xmlns:r="{R_NS}"{extra_ns}>'
            f'<w:zoom w:percent="100"/>{inner}</w:settings>')


def doc_vars(**pairs: str) -> str:
    items = "".join(f'<w:docVar w:name="{escape(k)}" w:val={quoteattr(v)}/>'
                    for k, v in pairs.items())
    return f"<w:docVars>{items}</w:docVars>"
