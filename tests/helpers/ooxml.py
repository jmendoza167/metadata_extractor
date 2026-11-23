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
        self.finalizers: list = []
        """Functions of the package run before its files are written."""

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
        for finalize in self.finalizers:
            finalize(self)
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


# A presentation PowerPoint opens needs, besides its slides, a slide master
# with a theme and at least one layout (every slide uses one), the slide
# and notes sizes, and, for speaker notes, a notes master with its own
# theme. Every shape tree starts with its group properties, and every shape
# has its non-visual properties and shape properties before its text.

PML = "application/vnd.openxmlformats-officedocument.presentationml."
THEME_TYPE = "application/vnd.openxmlformats-officedocument.theme+xml"
P14_NS = "http://schemas.microsoft.com/office/powerpoint/2010/main"
PPT = "ppt/presentation.xml"
MASTER = "ppt/slideMasters/slideMaster1.xml"
LAYOUT = "ppt/slideLayouts/slideLayout1.xml"
NOTES_MASTER = "ppt/notesMasters/notesMaster1.xml"
PPT_DECLS = f'xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}"'
GROUP = ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
         '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
         '<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')
CLR_MAP = ('bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
           'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" '
           'hlink="hlink" folHlink="folHlink"')
FIRST_SLIDE_ID = 256
CREATION_ID_BASE = 1000
"""Slide n has the creation ID 1000 + n (modern comments point to it)."""


def theme_xml() -> str:
    colors = "".join(f"<a:{name}><a:srgbClr val=\"{value}\"/></a:{name}>" for name, value in (
        ("dk2", "44546A"), ("lt2", "E7E6E6"), ("accent1", "4472C4"), ("accent2", "ED7D31"),
        ("accent3", "A5A5A5"), ("accent4", "FFC000"), ("accent5", "5B9BD5"),
        ("accent6", "70AD47"), ("hlink", "0563C1"), ("folHlink", "954F72")))
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    line = f'<a:ln w="6350">{fill}</a:ln>'
    effect = "<a:effectStyle><a:effectLst/></a:effectStyle>"
    font = '<a:latin typeface="{}"/><a:ea typeface=""/><a:cs typeface=""/>'
    return (f'{DECL}<a:theme xmlns:a="{A_NS}" name="Office Theme"><a:themeElements>'
            '<a:clrScheme name="Office"><a:dk1><a:sysClr val="windowText" lastClr="000000"/>'
            '</a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
            f'{colors}</a:clrScheme><a:fontScheme name="Office"><a:majorFont>'
            f'{font.format("Calibri Light")}</a:majorFont><a:minorFont>{font.format("Calibri")}'
            '</a:minorFont></a:fontScheme><a:fmtScheme name="Office">'
            f"<a:fillStyleLst>{fill * 3}</a:fillStyleLst><a:lnStyleLst>{line * 3}</a:lnStyleLst>"
            f"<a:effectStyleLst>{effect * 3}</a:effectStyleLst>"
            f"<a:bgFillStyleLst>{fill * 3}</a:bgFillStyleLst></a:fmtScheme></a:themeElements>"
            "<a:objectDefaults/><a:extraClrSchemeLst/></a:theme>")


def slide_master_xml() -> str:
    """The master's first relationship (rId1) is its layout."""
    return (f'{DECL}<p:sldMaster {PPT_DECLS}><p:cSld><p:spTree>{GROUP}</p:spTree></p:cSld>'
            f'<p:clrMap {CLR_MAP}/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/>'
            "</p:sldLayoutIdLst></p:sldMaster>")


def slide_layout_xml() -> str:
    return (f'{DECL}<p:sldLayout {PPT_DECLS} type="blank" preserve="1"><p:cSld name="Blank">'
            f"<p:spTree>{GROUP}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/>"
            "</p:clrMapOvr></p:sldLayout>")


def notes_master_xml() -> str:
    return (f'{DECL}<p:notesMaster {PPT_DECLS}><p:cSld><p:spTree>{GROUP}</p:spTree></p:cSld>'
            f'<p:clrMap {CLR_MAP}/></p:notesMaster>')


def text_shape(shape_id: int, text: str, placeholder: str = "") -> str:
    """A text box (or a placeholder, e.g. '<p:ph type="body" idx="1"/>')."""
    non_visual = (f'<p:cNvSpPr txBox="1"/><p:nvPr/>' if not placeholder else
                  f"<p:cNvSpPr/><p:nvPr>{placeholder}</p:nvPr>")
    body = (f'<a:p><a:r><a:rPr lang="en-US"/><a:t>{escape(text)}</a:t></a:r></a:p>' if text
            else "<a:p/>")
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="Shape {shape_id}"/>{non_visual}'
            '</p:nvSpPr><p:spPr><a:xfrm><a:off x="838200" y="1825625"/><a:ext cx="10515600" '
            'cy="1000000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
            f"<p:txBody><a:bodyPr/><a:lstStyle/>{body}</p:txBody></p:sp>")


def picture_shape(shape_id: int, rel_id: str) -> str:
    return (f'<p:pic><p:nvPicPr><p:cNvPr id="{shape_id}" name="Picture {shape_id}"/>'
            '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
            f'<p:blipFill><a:blip r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch>'
            '</p:blipFill><p:spPr><a:xfrm><a:off x="838200" y="3000000"/><a:ext cx="4572000" '
            'cy="3048000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
            "</p:pic>")


def slide(text: str = "Hello", n: int = 1, *, hidden: bool = False,
          picture: str | None = None) -> str:
    """Slide n with a text box (and a picture, by relationship ID). Its
    creation ID (p14:creationId) is what modern comments refer to."""
    show = ' show="0"' if hidden else ""
    shapes = text_shape(2, text) + (picture_shape(3, picture) if picture else "")
    return (f"{DECL}<p:sld {PPT_DECLS}{show}><p:cSld><p:spTree>{GROUP}{shapes}</p:spTree>"
            "</p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr><p:extLst>"
            '<p:ext uri="{BB962C8B-B14F-4D97-AF65-F5344CB8AC3E}">'
            f'<p14:creationId xmlns:p14="{P14_NS}" val="{CREATION_ID_BASE + n}"/></p:ext>'
            "</p:extLst></p:sld>")


def presentation_xml(pkg: Package) -> str:
    """presentation.xml listing the master, notes master and slides that
    presentation.xml's relationships lead to, in that order."""
    rels = pkg.rels.get(PPT, [])
    masters = "".join(f'<p:sldMasterId id="{2147483648 + i}" r:id="{rid}"/>'
                      for i, (rid, t, _, _) in enumerate(r for r in rels
                                                          if r[1] == REL + "slideMaster"))
    notes = "".join(f'<p:notesMasterId r:id="{rid}"/>' for rid, t, _, _ in rels
                    if t == REL + "notesMaster")
    slides = "".join(f'<p:sldId id="{FIRST_SLIDE_ID + i}" r:id="{rid}"/>'
                     for i, (rid, t, _, _) in enumerate(r for r in rels if r[1] == REL + "slide"))
    return (f"{DECL}<p:presentation {PPT_DECLS} saveSubsetFonts=\"1\">"
            f"<p:sldMasterIdLst>{masters}</p:sldMasterIdLst>"
            + (f"<p:notesMasterIdLst>{notes}</p:notesMasterIdLst>" if notes else "")
            + f"<p:sldIdLst>{slides}</p:sldIdLst>"
            '<p:sldSz cx="12192000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>'
            "</p:presentation>")


def _write_presentation(pkg: Package) -> None:
    pkg.parts[PPT] = presentation_xml(pkg).encode("utf-8")


def slide_names(pkg: Package) -> list[str]:
    return [posixpath.normpath(posixpath.join("ppt", target)) for _rid, t, target, _ext
            in pkg.rels.get(PPT, []) if t == REL + "slide"]


def add_slide(pkg: Package, text: str, *, hidden: bool = False) -> str:
    """Add slide n (after the others), using the layout; returns its part name."""
    n = len(slide_names(pkg)) + 1
    name = f"ppt/slides/slide{n}.xml"
    pkg.add_linked(PPT, "slide", name, slide(text, n, hidden=hidden), SLIDE)
    pkg.link(name, "slideLayout", LAYOUT)
    return name


def pptx(*, main_type: str = PPT_MAIN) -> Package:
    """One slide ("Hello") with its layout, master and theme."""
    pkg = Package()
    pkg.add_linked("", "officeDocument", PPT, "", main_type)
    pkg.add_linked(PPT, "slideMaster", MASTER, slide_master_xml(), PML + "slideMaster+xml")
    pkg.add_linked(MASTER, "slideLayout", LAYOUT, slide_layout_xml(), PML + "slideLayout+xml")
    pkg.link(LAYOUT, "slideMaster", MASTER)
    pkg.add_linked(MASTER, "theme", "ppt/theme/theme1.xml", theme_xml(), THEME_TYPE)
    add_slide(pkg, "Hello")
    pkg.add_linked(PPT, "presProps", "ppt/presProps.xml",
                   f"{DECL}<p:presentationPr {PPT_DECLS}/>", PML + "presProps+xml")
    pkg.add_linked(PPT, "viewProps", "ppt/viewProps.xml",
                   f"{DECL}<p:viewPr {PPT_DECLS}/>", PML + "viewProps+xml")
    pkg.link(PPT, "theme", "ppt/theme/theme1.xml")
    pkg.add_linked(PPT, "tableStyles", "ppt/tableStyles.xml",
                   f'{DECL}<a:tblStyleLst xmlns:a="{A_NS}" '
                   'def="{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"/>', PML + "tableStyles+xml")
    pkg.finalizers.append(_write_presentation)
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

ITEM_ID = "{AAAAAAAA-1111-2222-3333-444444444444}"


def item_props(item_id: str = ITEM_ID) -> str:
    return (f'{DECL}<ds:datastoreItem ds:itemID="{item_id}" '
            'xmlns:ds="http://schemas.openxmlformats.org/officeDocument/2006/customXml">'
            '<ds:schemaRefs><ds:schemaRef ds:uri="http://schemas.microsoft.com/office/2006/'
            'metadata/properties"/></ds:schemaRefs></ds:datastoreItem>')


CUSTOM_XML_PROPS_TYPE = "application/vnd.openxmlformats-officedocument.customXmlProperties+xml"


def add_custom_xml(pkg: Package, source: str, item: str = SHAREPOINT_ITEM, n: int = 1,
                   item_id: str = ITEM_ID) -> str:
    """customXml/item<n>.xml linked from ``source``, with its itemProps part."""
    name = f"customXml/item{n}.xml"
    pkg.add_linked(source, "customXml", name, item)
    pkg.add_linked(name, "customXmlProps", f"customXml/itemProps{n}.xml", item_props(item_id),
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


# -- comments, people, tracked changes ------------------------------------------------------

W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
W15_NS = "http://schemas.microsoft.com/office/word/2012/wordml"
W16CID_NS = "http://schemas.microsoft.com/office/word/2016/wordml/cid"
W16CEX_NS = "http://schemas.microsoft.com/office/word/2018/wordml/cex"
WORD_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml."
JANE_USER_ID = "S::jane.doe@example.com::aaaaaaaa-1111-2222-3333-444444444444"
WORD_NAMESPACES = (f' xmlns:w14="{W14_NS}" xmlns:w15="{W15_NS}" '
                   f'xmlns:mc="{MC_NS}" mc:Ignorable="w14 w15"')

COMMENTED_BODY = (
    '<w:p w14:paraId="1A2B3C4D" w14:textId="77777777" w:rsidR="00A1B2C3" w:rsidRDefault="00A1B2C3">'
    '<w:commentRangeStart w:id="0"/><w:r w:rsidR="00A1B2C3"><w:t>Kitchen</w:t></w:r>'
    '<w:commentRangeEnd w:id="0"/><w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr>'
    '<w:commentReference w:id="0"/></w:r></w:p>')

TRACKED_BODY = (
    '<w:p w:rsidR="00A1B2C3" w:rsidRDefault="00D4E5F6"><w:r><w:t xml:space="preserve">The rent is </w:t></w:r>'
    '<w:del w:id="1" w:author="Jane Doe" w:date="2024-05-01T10:15:00Z">'
    '<w:r w:rsidDel="00D4E5F6"><w:delText>twelve hundred</w:delText></w:r></w:del>'
    '<w:ins w:id="2" w:author="John Example" w:date="2024-05-02T09:00:00Z">'
    '<w:r w:rsidR="00D4E5F6"><w:t>950</w:t></w:r></w:ins>'
    '<w:r><w:rPr><w:b/><w:rPrChange w:id="3" w:author="Jane Doe" w:date="2024-05-01T10:16:00Z">'
    '<w:rPr/></w:rPrChange></w:rPr><w:t xml:space="preserve"> euros.</w:t></w:r></w:p>')


def word_comments(*comments: tuple[str, str, str, str]) -> str:
    """comments.xml with (author, initials, date, text) comments."""
    items = "".join(
        f'<w:comment w:id="{i}" w:author={quoteattr(a)} w:date="{d}" w:initials={quoteattr(n)}>'
        f'<w:p w14:paraId="0000000{i + 1}" w14:textId="77777777"><w:r><w:t>{escape(t)}</w:t>'
        "</w:r></w:p></w:comment>" for i, (a, n, d, t) in enumerate(comments))
    return f'{DECL}<w:comments xmlns:w="{W_NS}"{WORD_NAMESPACES}>{items}</w:comments>'


def word_people(*people: tuple[str, str | None]) -> str:
    """people.xml with (name, user ID or None) people."""
    items = []
    for name, user in people:
        presence = (f'<w15:presenceInfo w15:providerId="AD" w15:userId="{user}"/>'
                    if user else "")
        items.append(f'<w15:person w15:author={quoteattr(name)}>{presence}</w15:person>')
    return (f'{DECL}<w15:people xmlns:w="{W_NS}" xmlns:w15="{W15_NS}" xmlns:mc="{MC_NS}" '
            f'mc:Ignorable="w15">{"".join(items)}</w15:people>')


def comments_extensible(*dates: str) -> str:
    items = "".join(f'<w16cex:commentExtensible w16cex:durableId="1{i:07d}" '
                    f'w16cex:dateUtc="{d}"/>' for i, d in enumerate(dates))
    return (f'{DECL}<w16cex:commentsExtensible xmlns:w16cex="{W16CEX_NS}" xmlns:mc="{MC_NS}" '
            f'mc:Ignorable="w16cex">{items}</w16cex:commentsExtensible>')


def add_word_comments(pkg: Package, people: bool = True) -> Package:
    """Jane Doe's comment, with commentsExtensible.xml and people.xml."""
    main = "word/document.xml"
    pkg.add_linked(main, "comments", "word/comments.xml",
                   word_comments(("Jane Doe", "JD", "2024-05-01T10:00:00Z", "Ask about stairs")),
                   WORD_CT + "comments+xml")
    pkg.add_linked(main, "http://schemas.microsoft.com/office/2018/08/relationships/"
                   "commentsExtensible", "word/commentsExtensible.xml",
                   comments_extensible("2024-05-01T08:00:00Z"), WORD_CT + "commentsExtensible+xml")
    if people:
        pkg.add_linked(main, "http://schemas.microsoft.com/office/2011/relationships/people",
                       "word/people.xml", word_people(("Jane Doe", JANE_USER_ID),
                                                      ("John Example", None)),
                       WORD_CT + "people+xml")
    return pkg


EXCEL_COMMENTS = "application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml"
THREADED = "application/vnd.ms-excel.threadedcomments+xml"
PERSON = "application/vnd.ms-excel.person+xml"
TC_NS = "http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments"
PERSON_GUID = "{AAAAAAAA-0000-1111-2222-333333333333}"


def excel_comments(author: str = "Jane Doe", text: str = "Check the totals") -> str:
    return (f'{DECL}<comments xmlns="{S_NS}"><authors><author>{escape(author)}</author></authors>'
            '<commentList><comment ref="A1" authorId="0"><text><r><rPr><b/><sz val="9"/></rPr>'
            f'<t>{escape(author)}:</t></r><r><rPr><sz val="9"/></rPr><t xml:space="preserve">\n'
            f'{escape(text)}</t></r></text></comment></commentList></comments>')


def excel_threaded(date: str = "2024-05-01T10:00:00.00") -> str:
    return (f'{DECL}<ThreadedComments xmlns="{TC_NS}"><threadedComment ref="B2" dT="{date}" '
            f'personId="{PERSON_GUID}" id="{{BBBBBBBB-0000-1111-2222-333333333333}}">'
            "<text>Is this right?</text></threadedComment></ThreadedComments>")


def excel_persons(name: str = "Jane Doe", user: str = "jane.doe@example.com") -> str:
    return (f'{DECL}<personList xmlns="{TC_NS}" xmlns:x="{S_NS}"><person '
            f'displayName={quoteattr(name)} id="{PERSON_GUID}" userId={quoteattr(user)} '
            'providerId="AD"/></personList>')


def add_excel_comments(pkg: Package) -> Package:
    sheet = "xl/worksheets/sheet1.xml"
    pkg.add_linked(sheet, "comments", "xl/comments1.xml", excel_comments(), EXCEL_COMMENTS)
    pkg.add_linked(sheet, "http://schemas.microsoft.com/office/2017/10/relationships/"
                   "threadedComment", "xl/threadedComments/threadedComment1.xml",
                   excel_threaded(), THREADED)
    pkg.add_linked("xl/workbook.xml", "http://schemas.microsoft.com/office/2017/10/"
                   "relationships/person", "xl/persons/person.xml", excel_persons(), PERSON)
    return pkg


PPT_CT = "application/vnd.openxmlformats-officedocument.presentationml."
P188_NS = "http://schemas.microsoft.com/office/powerpoint/2018/8/main"
AUTHOR_GUID = "{CCCCCCCC-0000-1111-2222-333333333333}"


def ppt_comment_authors(name: str = "Jane Doe", initials: str = "JD") -> str:
    return (f'{DECL}<p:cmAuthorLst xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">'
            f'<p:cmAuthor id="0" name={quoteattr(name)} initials={quoteattr(initials)} '
            'lastIdx="1" clrIdx="0"/></p:cmAuthorLst>')


def ppt_comments(date: str = "2024-05-01T10:00:00.000") -> str:
    return (f'{DECL}<p:cmLst xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">'
            f'<p:cm authorId="0" dt="{date}" idx="1"><p:pos x="10" y="10"/>'
            "<p:text>Bigger font?</p:text></p:cm></p:cmLst>")


def ppt_authors(name: str = "Jane Doe", user: str = "jane.doe@example.com") -> str:
    return (f'{DECL}<p188:authorLst xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p188="{P188_NS}">'
            f'<p188:author id="{AUTHOR_GUID}" name={quoteattr(name)} initials="JD" '
            f'userId={quoteattr(user)} providerId="AD"/></p188:authorLst>')


def ppt_modern_comments(date: str = "2024-05-02T11:00:00.000") -> str:
    return (f'{DECL}<p188:cmLst xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p188="{P188_NS}">'
            f'<p188:cm id="{{DDDDDDDD-0000-1111-2222-333333333333}}" authorId="{AUTHOR_GUID}" '
            f'created="{date}"><pc:sldMkLst xmlns:pc="http://schemas.microsoft.com/office/'
            'powerpoint/2013/main/command"><pc:docMk/>'
            f'<pc:sldMk cId="{CREATION_ID_BASE + 1}" sldId="{FIRST_SLIDE_ID}"/></pc:sldMkLst>'
            "<p188:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang=\"en-US\"/>"
            "<a:t>Approved</a:t></a:r></a:p></p188:txBody></p188:cm></p188:cmLst>")


def add_ppt_comments(pkg: Package) -> Package:
    main, slide1 = "ppt/presentation.xml", "ppt/slides/slide1.xml"
    pkg.add_linked(main, "commentAuthors", "ppt/commentAuthors.xml", ppt_comment_authors(),
                   PPT_CT + "commentAuthors+xml")
    pkg.add_linked(slide1, "comments", "ppt/comments/comment1.xml", ppt_comments(),
                   PPT_CT + "comments+xml")
    pkg.add_linked(main, "http://schemas.microsoft.com/office/2018/10/relationships/authors",
                   "ppt/authors.xml", ppt_authors(), "application/vnd.ms-powerpoint.authors+xml")
    pkg.add_linked(slide1, "http://schemas.microsoft.com/office/2018/10/relationships/comments",
                   "ppt/comments/modernComment_100_ABCDEF12.xml", ppt_modern_comments(),
                   "application/vnd.ms-powerpoint.comments+xml")
    return pkg


# -- external links, embedded content, hidden content ---------------------------------------

TEMPLATE_PATH = "file:///C:\\Users\\jane.doe\\Documents\\Custom Office Templates\\Letter.dotm"
SERVER_PATH = "file:///\\\\fileserver01\\projects\\flat\\budget.xlsx"
THUMBNAIL_TYPE = "image/jpeg"
OLE_TYPE = "application/vnd.openxmlformats-officedocument.oleObject"
EMBEDDED_XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PRINTER_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.printerSettings"


def devmode(printer: str = "HP LaserJet Example") -> bytes:
    """A Windows DEVMODE structure as Office stores it in printerSettings*.bin."""
    name = printer.encode("utf-16-le")[:62].ljust(64, b"\x00")
    return name + bytes([0x01, 0x04, 0x00, 0x05, 0xDC, 0x00]) + b"\x00" * 150


def add_links_and_template(pkg: Package) -> Package:
    """A hyperlink, a linked file on a server and an attached template in
    the user's folder (from settings.xml, as Word stores it)."""
    main = "word/document.xml"
    pkg.link(main, "hyperlink", "https://example.com/flat", external=True)
    pkg.link(main, "oleObject", SERVER_PATH, external=True)
    pkg.add_linked(main, "settings", "word/settings.xml",
                   settings_xml('<w:attachedTemplate r:id="rId1"/>'), SETTINGS_TYPE)
    pkg.link("word/settings.xml", "attachedTemplate", TEMPLATE_PATH, external=True)
    return pkg


def add_media(pkg: Package, source: str, name: str, data: bytes, ext: str, ctype: str) -> str:
    pkg.defaults.setdefault(ext, ctype)
    return pkg.add_linked(source, "image", name, data)


def add_thumbnail(pkg: Package, data: bytes) -> Package:
    pkg.defaults.setdefault("jpeg", THUMBNAIL_TYPE)
    pkg.add_linked("", PKG_REL + "metadata/thumbnail", "docProps/thumbnail.jpeg", data)
    return pkg


def add_embedded_workbook(pkg: Package, source: str, folder: str) -> Package:
    """An Excel workbook embedded as an object (as charts and pasted tables are)."""
    inner = with_properties(xlsx(), core=core_xml({"creator": "Jane Doe",
                                                   "lastModifiedBy": "John Example"}),
                            app=app_xml({"Company": "Example Corp"}, titles=())).build()
    pkg.defaults.setdefault("xlsx", EMBEDDED_XLSX_TYPE)
    pkg.defaults.setdefault("bin", OLE_TYPE)
    pkg.add_linked(source, "package", f"{folder}/embeddings/Microsoft_Excel_Worksheet.xlsx", inner)
    pkg.add_linked(source, "oleObject", f"{folder}/embeddings/oleObject1.bin",
                   b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 56)
    return pkg


HIDDEN_TEXT_BODY = ('<w:p><w:r><w:t>Visible.</w:t></w:r><w:r><w:rPr><w:vanish/></w:rPr>'
                    '<w:t>Secret note to self</w:t></w:r><w:r><w:rPr><w:vanish w:val="0"/>'
                    '</w:rPr><w:t>shown</w:t></w:r></w:p>')

GLOSSARY_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.glossary+xml"


def add_glossary(pkg: Package, text: str = "Old letterhead of Jane Doe") -> Package:
    glossary = (f'{DECL}<w:glossaryDocument xmlns:w="{W_NS}"><w:docParts><w:docPart>'
                f'<w:docPartBody>{paragraph(text)}</w:docPartBody></w:docPart></w:docParts>'
                "</w:glossaryDocument>")
    pkg.add_linked("word/document.xml", "glossaryDocument", "word/glossary/document.xml",
                   glossary, GLOSSARY_TYPE)
    return pkg


RSIDS = '<w:rsids><w:rsidRoot w:val="00A1B2C3"/><w:rsid w:val="00A1B2C3"/><w:rsid w:val="00D4E5F6"/></w:rsids>'

HIDDEN_SHEETS = ('<sheet name="Sheet1" sheetId="1" r:id="rId1"/>'
                 '<sheet name="Salaries" sheetId="2" state="hidden" r:id="rId2"/>'
                 '<sheet name="Lookup" sheetId="3" state="veryHidden" r:id="rId3"/>')
WORKBOOK_HISTORY = (
    '<fileVersion appName="xl" lastEdited="7" lowestEdited="7" rupBuild="27425"/>'
    '<workbookPr defaultThemeVersion="166925"/>'
    '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
    '<mc:Choice Requires="x15"><x15ac:absPath url="C:\\Users\\jane.doe\\Documents\\Budget\\" '
    'xmlns:x15ac="http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac"/></mc:Choice>'
    '</mc:AlternateContent>'
    '<xr:revisionPtr revIDLastSave="0" documentId="13_ncr:1_{EEEEEEEE-0000-1111-2222-333333333333}" '
    'xr6:coauthVersionLast="47" xr6:coauthVersionMax="47" '
    'xr10:uidLastSave="{00000000-0000-0000-0000-000000000000}"/>')
WORKBOOK_NS = (' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
               'mc:Ignorable="x15 xr xr6 xr10" '
               'xmlns:x15="http://schemas.microsoft.com/office/spreadsheetml/2010/11/main" '
               'xmlns:xr="http://schemas.microsoft.com/office/spreadsheetml/2014/revision" '
               'xmlns:xr6="http://schemas.microsoft.com/office/spreadsheetml/2016/revision6" '
               'xmlns:xr10="http://schemas.microsoft.com/office/spreadsheetml/2016/revision10"')


def workbook_with(sheets: str, before_sheets: str = "", ns: str = WORKBOOK_NS) -> str:
    return (f'{DECL}<workbook xmlns="{S_NS}" xmlns:r="{R_NS}"{ns}>{before_sheets}'
            f"<sheets>{sheets}</sheets></workbook>")


def kitchen_sink_xlsx_workbook(pkg: Package) -> Package:
    """Hidden sheets and workbook history in xl/workbook.xml."""
    pkg.parts["xl/workbook.xml"] = workbook_with(HIDDEN_SHEETS, WORKBOOK_HISTORY).encode("utf-8")
    for n in (2, 3):
        pkg.add_linked("xl/workbook.xml", "worksheet", f"xl/worksheets/sheet{n}.xml",
                       worksheet(), SHEET)
    return pkg


def add_external_data(pkg: Package) -> Package:
    link = (f'{DECL}<externalLink xmlns="{S_NS}" xmlns:r="{R_NS}"><externalBook r:id="rId1">'
            '<sheetNames><sheetName val="Rates"/></sheetNames><sheetDataSet><sheetData sheetId="0">'
            '<row r="1"><cell r="A1"><v>0.25</v></cell></row></sheetData></sheetDataSet>'
            "</externalBook></externalLink>")
    pkg.add_linked("xl/workbook.xml", "externalLink", "xl/externalLinks/externalLink1.xml", link,
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.externalLink+xml")
    pkg.link("xl/externalLinks/externalLink1.xml", "externalLinkPath", SERVER_PATH, external=True)
    # CT_Connection: id and refreshedVersion are required (Excel rejects the
    # part without refreshedVersion); type 1 = ODBC, which uses <dbPr>.
    connections = (f'{DECL}<connections xmlns="{S_NS}"><connection id="1" keepAlive="1" '
                   'name="Payroll" type="1" refreshedVersion="6" background="1" saveData="1">'
                   '<dbPr connection="DSN=Payroll;UID=jane.doe;Server=db01.example.com" '
                   'command="SELECT * FROM salaries"/></connection></connections>')
    pkg.add_linked("xl/workbook.xml", "connections", "xl/connections.xml", connections,
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.connections+xml")
    return pkg


NOTES_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"


def notes_slide(text: str = "Mention the deadline: 2024-06-01") -> str:
    image = ('<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image"/><p:cNvSpPr><a:spLocks '
             'noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldImg"/>'
             "</p:nvPr></p:nvSpPr><p:spPr/></p:sp>")
    return (f"{DECL}<p:notes {PPT_DECLS}><p:cSld><p:spTree>{GROUP}{image}"
            f'{text_shape(3, text, placeholder="<p:ph type=\"body\" idx=\"1\"/>")}'
            "</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>")


def add_notes(pkg: Package, slide_name: str, text: str) -> str:
    """Speaker notes for a slide (adding the notes master and its theme the
    first time)."""
    if NOTES_MASTER not in pkg.parts:
        pkg.add_linked(PPT, "notesMaster", NOTES_MASTER, notes_master_xml(),
                       PML + "notesMaster+xml")
        pkg.add_linked(NOTES_MASTER, "theme", "ppt/theme/theme2.xml", theme_xml(), THEME_TYPE)
    n = sum(1 for name in pkg.parts if name.startswith("ppt/notesSlides/")) + 1
    name = f"ppt/notesSlides/notesSlide{n}.xml"
    pkg.add_linked(slide_name, "notesSlide", name, notes_slide(text), NOTES_TYPE)
    pkg.link(name, "notesMaster", NOTES_MASTER)
    pkg.link(name, "slide", slide_name)
    return name


def add_hidden_slide_and_notes(pkg: Package) -> Package:
    """A second, hidden slide, and speaker notes on the first."""
    add_slide(pkg, "Draft numbers", hidden=True)
    add_notes(pkg, "ppt/slides/slide1.xml", "Mention the deadline: 2024-06-01")
    return pkg


# -- kitchen sinks: every kind of Office metadata metaex reads ------------------------------------
# All values are made up: Jane Doe, jane.doe@example.com, C:\Users\jane.doe\...


def add_custom(pkg: Package, source: str) -> Package:
    """Custom properties with a sensitivity label, and SharePoint custom XML."""
    pkg.add_linked("", "custom-properties", "docProps/custom.xml", custom_xml(), CUSTOM_TYPE)
    add_custom_xml(pkg, source)
    return pkg


def add_printer(pkg: Package, source: str, folder: str, kind: str) -> str:
    return pkg.add_linked(source, "printerSettings",
                          f"{folder}/printerSettings/printerSettings1.bin", devmode(),
                          PRINTER_TYPE.replace("spreadsheetml", kind))


def kitchen_sink_docx() -> bytes:
    """Properties, custom properties and XML, comments with people, tracked
    changes with deleted text, revision IDs, document variables, an
    attached template in the user's folder, links, hidden text, a glossary,
    printer settings, a thumbnail and a photo with GPS."""
    from helpers import jpeg

    main = "word/document.xml"
    body = (COMMENTED_BODY + TRACKED_BODY + HIDDEN_TEXT_BODY
            + paragraph("See the plan at the link below."))
    pkg = with_properties(add_word_comments(docx(body, extra_ns=WORD_NAMESPACES)))
    add_custom(pkg, main)
    pkg.add_linked(main, "settings", "word/settings.xml", settings_xml(
        '<w:attachedTemplate r:id="rId1"/>' + RSIDS
        + doc_vars(Client="Jane Doe", CaseNo="2024-17")), SETTINGS_TYPE)
    pkg.link("word/settings.xml", "attachedTemplate", TEMPLATE_PATH, external=True)
    pkg.link(main, "hyperlink", "https://example.com/flat", external=True)
    add_glossary(pkg)
    add_printer(pkg, main, "word", "wordprocessingml")
    add_thumbnail(pkg, jpeg.sample_photo())
    add_media(pkg, main, "word/media/image1.jpeg", jpeg.sample_photo(), "jpeg", "image/jpeg")
    return pkg.build()


def macro_docm() -> bytes:
    """A macro-enabled document with document properties and a placeholder
    vbaProject.bin (not a real VBA project)."""
    pkg = with_properties(docx(paragraph("Press the button."), main_type=WORD_MACRO))
    pkg.add_linked("word/document.xml", MS_REL + "vbaProject", "word/vbaProject.bin",
                   b"placeholder: this is not a real VBA project\n", VBA)
    return pkg.build()


def kitchen_sink_xlsx() -> bytes:
    """Properties, custom properties and XML, notes and threaded comments with
    persons, hidden sheets, the saved-in folder and version history, a
    linked workbook and a data connection, printer settings, a thumbnail
    and a photo with GPS."""
    from helpers import jpeg

    pkg = with_properties(add_excel_comments(kitchen_sink_xlsx_workbook(xlsx())))
    add_custom(pkg, "xl/workbook.xml")
    add_external_data(pkg)
    sheet = "xl/worksheets/sheet1.xml"
    rid = add_printer(pkg, sheet, "xl", "spreadsheetml")
    pkg.parts[sheet] = worksheet().replace(
        "</sheetData>", f'</sheetData><pageSetup paperSize="9" r:id="{rid}"/>').replace(
        f'<worksheet xmlns="{S_NS}">', f'<worksheet xmlns="{S_NS}" xmlns:r="{R_NS}">'
    ).encode("utf-8")
    add_thumbnail(pkg, jpeg.sample_photo())
    add_media(pkg, "xl/workbook.xml", "xl/media/image1.jpeg", jpeg.sample_photo(), "jpeg",
              "image/jpeg")
    return pkg.build()


def kitchen_sink_pptx() -> bytes:
    """Properties, custom properties and XML, legacy and modern comments with
    their authors, a hidden slide, speaker notes, printer settings, a
    thumbnail and a photo with GPS."""
    from helpers import jpeg

    main = "ppt/presentation.xml"
    pkg = with_properties(add_ppt_comments(add_hidden_slide_and_notes(pptx())))
    add_custom(pkg, main)
    add_printer(pkg, main, "ppt", "presentationml")
    add_thumbnail(pkg, jpeg.sample_photo())
    rel_id = add_media(pkg, "ppt/slides/slide1.xml", "ppt/media/image1.jpeg",
                       jpeg.sample_photo(), "jpeg", "image/jpeg")
    pkg.parts["ppt/slides/slide1.xml"] = slide("Hello", 1, picture=rel_id).encode("utf-8")
    return pkg.build()