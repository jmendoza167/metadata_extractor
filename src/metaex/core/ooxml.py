"""Office Open XML packages (DOCX, XLSX, PPTX): parts, content types and
relationships.

An OOXML file is a ZIP archive of "parts". [Content_Types].xml says what
each part is, and relationship parts (_rels/*.rels) link parts to each
other: the package's relationships lead to the main document
(word/document.xml, xl/workbook.xml, ppt/presentation.xml) and the
document properties; the main document's relationships lead to styles,
images, comments, and so on.

Refused: archives without [Content_Types].xml or a main document, Strict
Open XML (ISO/IEC 29500 Strict; it uses other namespaces throughout),
binary workbooks (.xlsb), and anything the ZIP reader or the XML parser
refuses.
"""

from __future__ import annotations

import posixpath
import urllib.parse
from dataclasses import dataclass, field

from metaex.core.errors import MalformedFileError, UnsupportedFormatError
from metaex.core.inflate import Budget
from metaex.core.paths import local_paths, server_name, user_name
from metaex.core.result import Section
from metaex.core.xmldoc import XmlDocument, XmlEditor, XmlElement, parse_xml
from metaex.core.ziparchive import ZipArchive, ZipLimits, DEFAULT_LIMITS, open_archive

CONTENT_TYPES = "[Content_Types].xml"
PACKAGE_RELS = "_rels/.rels"

CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
STRICT_R_NS = "http://purl.oclc.org/ooxml/officeDocument/relationships"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships/"
MS_REL = "http://schemas.microsoft.com/office/2006/relationships/"

OFFICE_DOCUMENT = REL + "officeDocument"
STRICT_OFFICE_DOCUMENT = STRICT_R_NS + "/officeDocument"
CORE_PROPERTIES = PKG_REL + "metadata/core-properties"
CORE_PROPERTIES_OLD = REL + "metadata/core-properties"
EXTENDED_PROPERTIES = REL + "extended-properties"
CUSTOM_PROPERTIES = REL + "custom-properties"
THUMBNAIL = PKG_REL + "metadata/thumbnail"
DIGITAL_SIGNATURE_ORIGIN = PKG_REL + "digital-signature/origin"
VBA_PROJECT = MS_REL + "vbaProject"
VBA_CONTENT_TYPE = "application/vnd.ms-office.vbaProject"

# Main document content type -> (kind, macro-enabled, template, what it is)
MAIN_TYPES: dict[str, tuple[str, bool, bool, str]] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml":
        ("docx", False, False, "Word document"),
    "application/vnd.ms-word.document.macroEnabled.main+xml":
        ("docx", True, False, "Word macro-enabled document (.docm)"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml":
        ("docx", False, True, "Word template (.dotx)"),
    "application/vnd.ms-word.template.macroEnabledTemplate.main+xml":
        ("docx", True, True, "Word macro-enabled template (.dotm)"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml":
        ("xlsx", False, False, "Excel workbook"),
    "application/vnd.ms-excel.sheet.macroEnabled.main+xml":
        ("xlsx", True, False, "Excel macro-enabled workbook (.xlsm)"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml":
        ("xlsx", False, True, "Excel template (.xltx)"),
    "application/vnd.ms-excel.template.macroEnabled.main+xml":
        ("xlsx", True, True, "Excel macro-enabled template (.xltm)"),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml":
        ("pptx", False, False, "PowerPoint presentation"),
    "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml":
        ("pptx", True, False, "PowerPoint macro-enabled presentation (.pptm)"),
    "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml":
        ("pptx", False, True, "PowerPoint template (.potx)"),
    "application/vnd.ms-powerpoint.template.macroEnabled.main+xml":
        ("pptx", True, True, "PowerPoint macro-enabled template (.potm)"),
    "application/vnd.openxmlformats-officedocument.presentationml.slideshow.main+xml":
        ("pptx", False, False, "PowerPoint slide show (.ppsx)"),
    "application/vnd.ms-powerpoint.slideshow.macroEnabled.main+xml":
        ("pptx", True, False, "PowerPoint macro-enabled slide show (.ppsm)"),
}
BINARY_WORKBOOK = "application/vnd.ms-excel.sheet.binary.macroEnabled.main"

STRICT_NAMESPACES = ("http://purl.oclc.org/ooxml/",)


@dataclass
class Relationship:
    source: str
    """Part the relationship belongs to ("" for the package itself)."""
    id: str
    type: str
    target: str
    """Target as written."""
    external: bool
    part: str | None
    """For internal targets: the part name it points to (None if it leaves the package)."""
    element: XmlElement = field(repr=False)

    @property
    def type_name(self) -> str:
        return self.type.rsplit("/", 1)[-1]


@dataclass
class ContentTypes:
    defaults: dict[str, str]
    """Lowercase extension -> content type."""
    overrides: dict[str, str]
    """Lowercase part name (no leading "/") -> content type."""
    doc: XmlDocument = field(repr=False)
    override_elements: dict[str, list[XmlElement]] = field(default_factory=dict, repr=False)
    """Lowercase part name -> its <Override> elements (for removing them)."""

    def type_of(self, part: str) -> str | None:
        found = self.overrides.get(part.lower())
        if found is not None:
            return found
        ext = posixpath.splitext(part)[1][1:].lower()
        return self.defaults.get(ext)


def rels_name(part: str) -> str:
    """The relationship part of ``part`` ("" means the package)."""
    if part == "":
        return PACKAGE_RELS
    folder, name = posixpath.split(part)
    return posixpath.join(folder, "_rels", name + ".rels")


def source_of_rels(rels: str) -> str | None:
    """The part a relationship part belongs to; None if ``rels`` isn't one."""
    if rels == PACKAGE_RELS:
        return ""
    folder, name = posixpath.split(rels)
    if posixpath.basename(folder) != "_rels" or not name.endswith(".rels"):
        return None
    return posixpath.join(posixpath.dirname(folder), name[:-len(".rels")])


def resolve_target(source: str, target: str) -> str | None:
    """Part name an internal target refers to; None if it leaves the package."""
    target = target.split("#", 1)[0]
    if not target:
        return None
    target = urllib.parse.unquote(target)
    path = target[1:] if target.startswith("/") else posixpath.join(posixpath.dirname(source),
                                                                     target)
    parts: list[str] = []
    for piece in path.split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(piece)
    return "/".join(parts) or None


@dataclass
class Package:
    archive: ZipArchive
    content_types: ContentTypes
    relationships: dict[str, list[Relationship]]
    """Source part ("" for the package) -> its relationships."""
    main: str
    main_type: str
    kind: str
    macro_enabled: bool
    template: bool
    description: str
    warnings: list[str] = field(default_factory=list)
    _xml: dict[str, XmlDocument] = field(default_factory=dict, repr=False)

    # -- parts -----------------------------------------------------------------------

    def part_names(self) -> list[str]:
        """Every file in the archive except folders, in archive order."""
        return [e.name for e in self.archive.entries if not e.is_dir]

    def entry_name(self, part: str) -> str | None:
        """The archive entry for a part name (part names ignore case)."""
        entry = self.archive.get(part) or self.archive.find(part)
        return None if entry is None or entry.is_dir else entry.name

    def has(self, part: str) -> bool:
        return self.entry_name(part) is not None

    def read(self, part: str) -> bytes:
        name = self.entry_name(part)
        if name is None:
            raise MalformedFileError(f"the package has no part {part!r}")
        return self.archive.read(name)

    def xml(self, part: str) -> XmlDocument:
        """A part parsed as XML (cached)."""
        name = self.entry_name(part)
        if name is None:
            raise MalformedFileError(f"the package has no part {part!r}")
        if name not in self._xml:
            self._xml[name] = parse_xml(self.archive.read(name), name)
        return self._xml[name]

    def type_of(self, part: str) -> str | None:
        return self.content_types.type_of(part)

    # -- relationships ------------------------------------------------------------------

    def rels(self, source: str) -> list[Relationship]:
        return self.relationships.get(source, [])

    def related(self, source: str, rel_type: str) -> list[str]:
        """Internal parts ``source`` links to with relationships of ``rel_type``
        (the type's last path segment, or the full URI)."""
        return [r.part for r in self.rels(source)
                if not r.external and r.part is not None and self.has(r.part)
                and (r.type == rel_type or r.type_name == rel_type)]

    def all_relationships(self) -> list[Relationship]:
        return [r for rels in self.relationships.values() for r in rels]

    def reachable(self) -> set[str]:
        """Entry names of the parts reachable from the package relationships."""
        seen: set[str] = set()
        todo = [""]
        while todo:
            source = todo.pop()
            for r in self.rels(source):
                if r.external or r.part is None:
                    continue
                name = self.entry_name(r.part)
                if name is not None and name not in seen:
                    seen.add(name)
                    todo.append(name)
        return seen

    def unreferenced(self) -> list[str]:
        """Parts no relationship leads to (other than [Content_Types].xml and
        relationship parts): nothing in the document uses them."""
        reachable = self.reachable()
        return [n for n in self.part_names()
                if n != CONTENT_TYPES and source_of_rels(n) is None and n not in reachable]

    def parts_of_type(self, content_type: str) -> list[str]:
        return [n for n in self.part_names() if self.type_of(n) == content_type]

    @property
    def vba_parts(self) -> list[str]:
        """Macro projects (vbaProject.bin), found by relationship or content type."""
        found = {self.entry_name(p) for p in self.related(self.main, VBA_PROJECT)}
        found |= set(self.parts_of_type(VBA_CONTENT_TYPE))
        return sorted(n for n in found if n is not None)


def _parse_content_types(archive: ZipArchive) -> ContentTypes:
    entry = archive.get(CONTENT_TYPES) or archive.find(CONTENT_TYPES)
    if entry is None:
        raise MalformedFileError("not an Office Open XML package (no [Content_Types].xml)")
    doc = parse_xml(archive.read(entry), CONTENT_TYPES)
    if not doc.root.is_(CT_NS, "Types"):
        raise MalformedFileError(f"{CONTENT_TYPES} has the wrong root element "
                                 f"<{doc.root.name}>")
    defaults, overrides = {}, {}
    elements: dict[str, list[XmlElement]] = {}
    for e in doc.root.elements():
        if e.is_(CT_NS, "Default"):
            ext, ctype = e.get("Extension"), e.get("ContentType")
            if ext is None or ctype is None:
                raise MalformedFileError(f"{CONTENT_TYPES}: <Default> without Extension or "
                                         "ContentType")
            defaults[ext.lower()] = ctype
        elif e.is_(CT_NS, "Override"):
            name, ctype = e.get("PartName"), e.get("ContentType")
            if name is None or ctype is None:
                raise MalformedFileError(f"{CONTENT_TYPES}: <Override> without PartName or "
                                         "ContentType")
            key = urllib.parse.unquote(name.lstrip("/")).lower()
            overrides[key] = ctype
            elements.setdefault(key, []).append(e)
    return ContentTypes(defaults, overrides, doc, elements)


def _parse_rels(archive: ZipArchive, entry_name: str, source: str) -> list[Relationship]:
    doc = parse_xml(archive.read(entry_name), entry_name)
    if not doc.root.is_(RELS_NS, "Relationships"):
        raise MalformedFileError(f"{entry_name} has the wrong root element <{doc.root.name}>")
    out = []
    ids = set()
    for e in doc.root.elements():
        if not e.is_(RELS_NS, "Relationship"):
            continue
        rid, rtype, target = e.get("Id"), e.get("Type"), e.get("Target")
        if rid is None or rtype is None or target is None:
            raise MalformedFileError(f"{entry_name}: a relationship has no Id, Type or Target")
        if rid in ids:
            raise MalformedFileError(f"{entry_name}: two relationships have the Id {rid!r}")
        ids.add(rid)
        external = (e.get("TargetMode") or "Internal") == "External"
        part = None if external else resolve_target(source, target)
        out.append(Relationship(source, rid, rtype, target, external, part, e))
    return out


def open_package(data: bytes | bytearray | memoryview,
                 limits: ZipLimits = DEFAULT_LIMITS, *, depth: int = 0,
                 budget: Budget | None = None) -> Package:
    """Open an OOXML package: content types, every relationship part and
    the main document's type. Raises MalformedFileError if the package is
    damaged or unsafe, UnsupportedFormatError for Strict Open XML and
    binary workbooks. ``depth`` and ``budget`` are for packages embedded in
    other packages (they share the outer one's limits)."""
    archive = open_archive(data, limits, depth=depth, budget=budget)
    content_types = _parse_content_types(archive)
    relationships: dict[str, list[Relationship]] = {}
    for entry in archive.entries:
        source = source_of_rels(entry.name)
        if source is None or entry.is_dir:
            continue
        relationships[source] = _parse_rels(archive, entry.name, source)

    package_rels = relationships.get("", [])
    if any(r.type == STRICT_OFFICE_DOCUMENT or r.type.startswith(STRICT_NAMESPACES)
           for r in package_rels):
        raise UnsupportedFormatError("this is a Strict Open XML document (ISO/IEC 29500 "
                                     "Strict), which metaex doesn't support; save it as a normal "
                                     "Office document (not \"Strict Open XML\") and try again")
    main_rels = [r for r in package_rels if r.type == OFFICE_DOCUMENT and not r.external]
    if not main_rels:
        raise MalformedFileError("the package has no main document (no officeDocument "
                                 "relationship in _rels/.rels)")
    if len(main_rels) > 1:
        raise MalformedFileError("the package has more than one main document")
    main = main_rels[0].part
    if main is None or archive.find(main) is None:
        raise MalformedFileError(f"the main document {main_rels[0].target!r} is missing")
    main = archive.find(main).name
    main_type = content_types.type_of(main)
    if main_type == BINARY_WORKBOOK:
        raise UnsupportedFormatError("binary Excel workbooks (.xlsb) are not supported; save it "
                                     "as .xlsx and try again")
    if main_type not in MAIN_TYPES:
        raise UnsupportedFormatError(f"the main document has the content type {main_type!r}, "
                                     "which metaex doesn't support")
    kind, macro, template, description = MAIN_TYPES[main_type]
    package = Package(archive, content_types, relationships, main, main_type, kind, macro,
                      template, description)
    root = package.xml(main).root
    if root.uri is not None and root.uri.startswith(STRICT_NAMESPACES):
        raise UnsupportedFormatError("this is a Strict Open XML document (ISO/IEC 29500 "
                                     "Strict), which metaex doesn't support; save it as a normal "
                                     "Office document (not \"Strict Open XML\") and try again")
    return package


# -- document properties (docProps/core.xml, docProps/app.xml) ---------------------------

CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
DCTERMS_NS = "http://purl.org/dc/terms/"
EP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
CORE_TYPE = "application/vnd.openxmlformats-package.core-properties+xml"
APP_TYPE = "application/vnd.openxmlformats-officedocument.extended-properties+xml"

MAX_VALUE_CHARS = 2000
"""Longer property values are shortened in the report."""
MAX_VECTOR_ITEMS = 100

CORE_FIELDS: dict[tuple[str, str], tuple[str, str]] = {
    (DC_NS, "creator"): ("creator", "Author"),
    (CP_NS, "lastModifiedBy"): ("lastModifiedBy", "Last modified by"),
    (DCTERMS_NS, "created"): ("created", "Created"),
    (DCTERMS_NS, "modified"): ("modified", "Modified"),
    (CP_NS, "lastPrinted"): ("lastPrinted", "Last printed"),
    (CP_NS, "revision"): ("revision", "Revision number"),
    (DC_NS, "title"): ("title", "Title"),
    (DC_NS, "subject"): ("subject", "Subject"),
    (CP_NS, "keywords"): ("keywords", "Keywords"),
    (DC_NS, "description"): ("description", "Comments (description)"),
    (CP_NS, "category"): ("category", "Category"),
    (CP_NS, "contentStatus"): ("contentStatus", "Status"),
    (DC_NS, "identifier"): ("identifier", "Identifier"),
    (DC_NS, "language"): ("language", "Language"),
    (CP_NS, "version"): ("version", "Version"),
    (CP_NS, "contentType"): ("contentType", "Content type"),
}

APP_LABELS = {
    "Application": "Application", "AppVersion": "Application version", "Company": "Company",
    "Manager": "Manager", "Template": "Template", "TotalTime": "Total editing time (minutes)",
    "HyperlinkBase": "Hyperlink base", "DocSecurity": "Document security",
    "Pages": "Pages", "Words": "Words", "Characters": "Characters",
    "CharactersWithSpaces": "Characters (with spaces)", "Lines": "Lines",
    "Paragraphs": "Paragraphs", "Slides": "Slides", "Notes": "Notes",
    "HiddenSlides": "Hidden slides", "MMClips": "Multimedia clips",
    "PresentationFormat": "Presentation format", "ScaleCrop": "Scale or crop thumbnail",
    "LinksUpToDate": "Links up to date", "SharedDoc": "Shared document",
    "HyperlinksChanged": "Hyperlinks changed", "HeadingPairs": "Part types (heading pairs)",
    "TitlesOfParts": "Titles of parts", "HLinks": "Hyperlinks", "DigSig": "Digital signature",
}


def shorten(text: str, limit: int = MAX_VALUE_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + f"... ({len(text)} characters)"


def _part_by_rel_or_type(pkg: Package, rel_types: tuple[str, ...], content_type: str,
                         default: str) -> str | None:
    for r in pkg.rels(""):
        if r.type in rel_types and not r.external and r.part is not None and pkg.has(r.part):
            return pkg.entry_name(r.part)
    found = pkg.parts_of_type(content_type)
    if found:
        return found[0]
    return pkg.entry_name(default)


def core_part(pkg: Package) -> str | None:
    """docProps/core.xml (wherever the package keeps it), or None."""
    return _part_by_rel_or_type(pkg, (CORE_PROPERTIES, CORE_PROPERTIES_OLD), CORE_TYPE,
                                "docProps/core.xml")


def app_part(pkg: Package) -> str | None:
    """docProps/app.xml (wherever the package keeps it), or None."""
    return _part_by_rel_or_type(pkg, (EXTENDED_PROPERTIES,), APP_TYPE, "docProps/app.xml")


def core_section(pkg: Package) -> Section | None:
    """The core properties: author, last editor, dates, revision, title, ...
    Elements metaex doesn't know are listed too, by name."""
    part = core_part(pkg)
    if part is None:
        return None
    root = pkg.xml(part).root
    section = Section("office_core", "Document properties")
    for e in root.elements():
        key, label = CORE_FIELDS.get((e.uri, e.local), (f"other.{e.local}", e.name))
        value = shorten(" ".join(e.text().split()) if key != "description" else e.text())
        if value == "" and key != "description":
            continue
        section.add(key, label, value, sensitive=True)
    return section if section.fields else None


def vector_values(element: XmlElement) -> list[str]:
    """Text of the items of a vt:vector (variants included), at most MAX_VECTOR_ITEMS."""
    values = []
    for item in element.iter():
        if item is element or item.uri != VT_NS or item.local in ("vector", "variant"):
            continue
        values.append(shorten(item.text()))
        if len(values) >= MAX_VECTOR_ITEMS:
            break
    return values


def app_section(pkg: Package) -> Section | None:
    """The extended ("app") properties: application and version, company,
    manager, template, total editing time, counts, ..."""
    part = app_part(pkg)
    if part is None:
        return None
    root = pkg.xml(part).root
    section = Section("office_app", "Application properties")
    for e in root.elements():
        label = APP_LABELS.get(e.local, e.name) if e.uri == EP_NS else e.name
        key = e.local if e.uri == EP_NS else f"other.{e.local}"
        vector = e.child(VT_NS, "vector")
        if vector is not None:
            value: object = vector_values(vector)
            if not value:
                continue
        else:
            value = shorten(" ".join(e.text().split()))
            if value == "":
                continue
        section.add(key, label, value, sensitive=e.local in (
            "Company", "Manager", "Template", "HyperlinkBase", "HLinks", "TitlesOfParts"))
    return section if section.fields else None


# -- custom properties, sensitivity labels, custom XML ----------------------------------

CUSTOM_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
CUSTOM_TYPE = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
LABEL_INFO_TYPE = "application/vnd.ms-office.classificationlabels+xml"
LABEL_INFO_REL = "http://schemas.microsoft.com/office/2020/02/relationships/classificationlabels"
CUSTOM_XML_REL = REL + "customXml"
CUSTOM_XML_PROPS_REL = REL + "customXmlProps"
DS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/customXml"
SENSITIVITY_PREFIX = "MSIP_Label_"
MAX_LISTED = 200


def custom_part(pkg: Package) -> str | None:
    return _part_by_rel_or_type(pkg, (CUSTOM_PROPERTIES,), CUSTOM_TYPE, "docProps/custom.xml")


def label_info_part(pkg: Package) -> str | None:
    """docMetadata/LabelInfo.xml: where newer Office versions keep sensitivity labels."""
    return _part_by_rel_or_type(pkg, (LABEL_INFO_REL,), LABEL_INFO_TYPE,
                                "docMetadata/LabelInfo.xml")


def _limited(section: Section, total: int) -> None:
    if total > MAX_LISTED:
        section.add("more", "More", f"{total - MAX_LISTED} more not listed")


def custom_sections(pkg: Package) -> list[Section]:
    """Custom properties (docProps/custom.xml), with Microsoft Purview
    sensitivity labels (MSIP_Label_*, docMetadata/LabelInfo.xml) in a
    section of their own."""
    custom = Section("office_custom", "Custom properties")
    labels = Section("office_labels", "Sensitivity labels")
    part = custom_part(pkg)
    if part is not None:
        props = [e for e in pkg.xml(part).root.elements() if e.local == "property"]
        for e in props:
            name = e.get("name") or "(no name)"
            value_element = next(e.elements(), None)
            value = shorten(value_element.text()) if value_element is not None else ""
            target = labels if name.startswith(SENSITIVITY_PREFIX) else custom
            if len(target.fields) < MAX_LISTED:
                target.add(f"property.{name}", name, value, sensitive=True)
        _limited(custom, sum(1 for e in props
                             if not (e.get("name") or "").startswith(SENSITIVITY_PREFIX)))
    info = label_info_part(pkg)
    if info is not None:
        for n, e in enumerate((x for x in pkg.xml(info).root.iter() if x.local == "label"), 1):
            details = ", ".join(f"{k.split(':', 1)[-1]}={v}" for k, v in e.attrs
                                if not k.startswith("xmlns"))
            labels.add(f"label_info.{n}", f"Label {n} ({info})", details, sensitive=True)
            if n >= MAX_LISTED:
                break
    return [s for s in (custom, labels) if s.fields]


def custom_xml_parts(pkg: Package) -> list[str]:
    """The data store items (customXml/item1.xml, ...), not their property parts."""
    linked = {pkg.entry_name(r.part) for r in pkg.all_relationships()
              if r.type == CUSTOM_XML_REL and not r.external and r.part is not None}
    found = {n for n in pkg.part_names()
             if n.lower().startswith("customxml/") and source_of_rels(n) is None
             and not posixpath.basename(n).lower().startswith("itemprops")}
    return sorted((linked | found) - {None})


def leaf_values(element: XmlElement, limit: int) -> list[tuple[str, str]]:
    """(path, text) of the elements under ``element`` that hold text."""
    out: list[tuple[str, str]] = []
    stack: list[tuple[XmlElement, str]] = [(element, element.local)]
    while stack and len(out) < limit:
        e, path = stack.pop()
        children = list(e.elements())
        text = " ".join(e.own_text().split())
        if text:
            out.append((path, shorten(text)))
        stack.extend((c, f"{path}/{c.local}") for c in reversed(children))
    return out


def custom_xml_section(pkg: Package) -> Section | None:
    """Custom XML data store items: SharePoint document properties (who it
    is shared with, document IDs), bibliography sources, data bound to
    content controls, and data added by other programs."""
    section = Section("office_custom_xml", "Custom XML data")
    total = 0
    for part in custom_xml_parts(pkg):
        root = pkg.xml(part).root
        values = leaf_values(root, MAX_LISTED)
        total += len(values) or 1
        if not values:
            if len(section.fields) < MAX_LISTED:
                section.add(f"{part}:", f"{part}", f"<{root.name}> ({root.uri or 'no namespace'})",
                            sensitive=True)
            continue
        for path, text in values:
            if len(section.fields) < MAX_LISTED:
                section.add(f"{part}:{path}", f"{part}: {path}", text, sensitive=True)
    _limited(section, total)
    return section if section.fields else None


# -- helpers for comments and people -----------------------------------------------------


def date_range(values: list[str]) -> str:
    """"first to last (n dates)" for ISO dates, or the single date."""
    dates = sorted(v for v in values if v)
    if not dates:
        return ""
    if dates[0] == dates[-1]:
        return dates[0] if len(dates) == 1 else f"{dates[0]} ({len(dates)} times)"
    return f"{dates[0]} to {dates[-1]} ({len(dates)} dates)"


def distinct(values) -> list[str]:
    """Values in first-seen order, each once, empty ones left out."""
    out: list[str] = []
    for v in values:
        if v and v not in out:
            out.append(v)
    return out


def comments_summary(section: Section, count: int, authors: list[str], initials: list[str],
                     dates: list[str], where: str = "") -> None:
    """Add the usual comment fields: count, authors, initials, dates."""
    if not count:
        return
    suffix = f" ({where})" if where else ""
    key = f"{where}." if where else ""
    section.add(f"{key}count", f"Comments{suffix}", count)
    if any(authors):
        section.add(f"{key}authors", f"Comment authors{suffix}", distinct(authors),
                    sensitive=True)
    if any(initials):
        section.add(f"{key}initials", f"Comment author initials{suffix}", distinct(initials),
                    sensitive=True)
    if any(dates):
        section.add(f"{key}dates", f"Comment dates{suffix}", date_range(dates), sensitive=True)


def add_person(section: Section, n: int, name: str | None, user_id: str | None,
               provider: str | None, initials: str | None = None) -> None:
    """One person of a people / persons / authors list."""
    section.add(f"person.{n}.name", f"Person {n}", name or "(no name)", sensitive=True)
    if initials:
        section.add(f"person.{n}.initials", f"Person {n}: initials", initials, sensitive=True)
    if user_id:
        section.add(f"person.{n}.userId", f"Person {n}: user ID", user_id, sensitive=True)
    if provider:
        section.add(f"person.{n}.providerId", f"Person {n}: account provider", provider,
                    sensitive=True)


# -- external links and local paths -------------------------------------------------------

REL_LABELS = {
    "hyperlink": "Hyperlink", "image": "Linked image", "attachedTemplate": "Attached template",
    "oleObject": "Linked object", "externalLinkPath": "Linked workbook",
    "subDocument": "Subdocument", "frame": "Frame", "video": "Linked video",
    "audio": "Linked audio", "media": "Linked media", "package": "Linked package",
    "xmlMaps": "XML map", "mailMergeSource": "Mail merge data source",
    "mailMergeHeaderSource": "Mail merge header source", "recipientData": "Mail merge data",
    "dataSource": "Data source", "externalLink": "External link",
}


def path_note(path: str) -> str | None:
    """What a local path reveals: a user name, a server name."""
    user, server = user_name(path), server_name(path)
    notes = []
    if user:
        notes.append(f"reveals the user name {user!r}")
    if server:
        notes.append(f"reveals the server {server!r}")
    return "; ".join(notes) or None


def add_link(links: Section, paths: Section, key: str, label: str, target: str) -> None:
    """A link or path: into ``paths`` if it is a local path that names a
    user, else into ``links`` (with a note naming a server, if any)."""
    found = local_paths(target)
    if found and any(user_name(p) for p in found):
        paths.add(key, label, shorten(target), sensitive=True, note=path_note(found[0]))
    else:
        note = path_note(found[0]) if found else None
        links.add(key, label, shorten(target), sensitive=True, note=note)


def external_link_sections(pkg: Package) -> list[Section]:
    """Every relationship that points outside the package: hyperlinks,
    linked images and files, the attached template, linked workbooks, mail
    merge sources. File paths that name a user go in "Local file paths"."""
    links = Section("office_external", "External links")
    paths = Section("local_paths", "Local file paths")
    external = [r for r in pkg.all_relationships() if r.external]
    for r in external[:MAX_LISTED]:
        label = f"{REL_LABELS.get(r.type_name, r.type_name)} ({r.source or 'package'})"
        add_link(links, paths, f"{r.source}:{r.id}", label, r.target)
    _limited(links, len(external))
    return [s for s in (links, paths) if s.fields]


# -- embedded images and objects ----------------------------------------------------------

MEDIA_REL_TYPES = ("image", "media", "video", "audio", "hdphoto")
OBJECT_REL_TYPES = ("oleObject", "package", "control", "activeXControlBinary")
EMBEDDED_PACKAGE_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-excel", "application/msword", "application/vnd.ms-powerpoint",
    "application/vnd.ms-excel.sheet.macroEnabled.12", "application/vnd.ms-word.document.macroEnabled.12",
}
"""Content types of Office files embedded as objects (charts' workbooks, ...)."""
MAX_EMBEDDED = 1000


def media_parts(pkg: Package) -> list[str]:
    """Images, audio and video stored in the package (not the thumbnail)."""
    thumbs = set(thumbnail_parts(pkg))
    found = []
    for r in pkg.all_relationships():
        if r.external or r.part is None or r.type_name not in MEDIA_REL_TYPES:
            continue
        name = pkg.entry_name(r.part)
        if name is not None and name not in found and name not in thumbs:
            found.append(name)
    for name in pkg.part_names():
        ctype = pkg.type_of(name) or ""
        if ctype.startswith("image/") and name not in found and name not in thumbs:
            found.append(name)
    return found


def object_parts(pkg: Package) -> list[str]:
    """Embedded objects: OLE objects, embedded Office files and other
    packages, ActiveX controls."""
    found = []
    for r in pkg.all_relationships():
        if r.external or r.part is None or r.type_name not in OBJECT_REL_TYPES:
            continue
        name = pkg.entry_name(r.part)
        if name is not None and name not in found:
            found.append(name)
    for name in pkg.part_names():
        ctype = pkg.type_of(name) or ""
        if name in found or name == pkg.main:
            continue
        if "oleObject" in ctype or "activeX" in ctype or ctype in EMBEDDED_PACKAGE_TYPES:
            found.append(name)
    return found


def embedded_image_sections(pkg: Package, warnings: list[str]) -> list[Section]:
    """A list of the images (and other media) in the package, then the
    metadata sections of each image, found with its own format's parser."""
    from metaex.core import filetype
    from metaex.core.registry import format_module

    listing = Section("embedded_images", "Embedded images")
    inner: list[Section] = []
    parts = media_parts(pkg)
    for name in parts[:MAX_EMBEDDED]:
        data = pkg.read(name)
        detected = filetype.detect(data)
        kind = detected.name if detected.key != filetype.UNKNOWN.key else (
            pkg.type_of(name) or "unknown data")
        module = format_module(detected) if detected.key in IMAGE_KEYS else None
        if module is None:
            listing.add(name, name, f"{kind}, {len(data)} bytes (not examined: metaex can't "
                        "read this type)", sensitive=True)
            continue
        try:
            parsed = module.parse(data)
        except MalformedFileError as exc:
            listing.add(name, name, f"{kind}, {len(data)} bytes (not examined: malformed: "
                        f"{exc})", sensitive=True)
            warnings.append(f"image {name} ({kind}) is malformed and was not examined: {exc}")
            continue
        prefix = f"{name} ({kind})"
        if parsed.get("complete", True):
            listing.add(name, name, f"{kind}, {len(data)} bytes")
        else:
            listing.add(name, name, f"{kind}, {len(data)} bytes (malformed: partly examined)",
                        sensitive=True)
            warnings.append(f"image {name} ({kind}) is malformed and was only partly examined")
        for w in parsed.get("warnings", []):
            if w.startswith("CRITICAL: "):
                warnings.append(f"CRITICAL: {prefix}: {w[len('CRITICAL: '):]}")
            else:
                warnings.append(f"{prefix}: {w}")
        for section in parsed.get("sections", []):
            inner.append(Section(section.key, f"{prefix}: {section.title}", section.fields))
    if len(parts) > MAX_EMBEDDED:
        listing.add("more", "More", f"{len(parts) - MAX_EMBEDDED} more not examined")
        warnings.append(f"only the first {MAX_EMBEDDED} of {len(parts)} images were examined")
    return ([listing] if listing.fields else []) + inner


IMAGE_KEYS = ("jpeg", "png", "gif", "webp", "svg")
"""Embedded image types whose metadata metaex reads (and strips)."""


def embedded_object_section(pkg: Package) -> Section | None:
    """Embedded objects: OLE objects (oleObject*.bin), embedded Office files
    and other packages, ActiveX controls. Their contents are not cleaned;
    for embedded Office files, the authors are shown."""
    section = Section("office_embedded", "Embedded objects")
    parts = object_parts(pkg)
    for name in parts[:MAX_LISTED]:
        data = pkg.read(name)
        section.add(name, name, f"{pkg.type_of(name) or 'unknown type'}, {len(data)} bytes",
                    sensitive=True)
        if data.startswith(b"PK\x03\x04"):
            try:
                inner = open_package(data, pkg.archive.limits, depth=pkg.archive.depth + 1,
                                     budget=pkg.archive.budget)
                core = core_section(inner)
            except (MalformedFileError, UnsupportedFormatError):
                continue
            for f in (core.fields if core else []):
                if f.key in ("creator", "lastModifiedBy", "created", "modified"):
                    section.add(f"{name}:{f.key}", f"{name}: {f.label}", f.value, sensitive=True)
    _limited(section, len(parts))
    return section if section.fields else None


# -- thumbnail, printer settings, macros, unused parts ---------------------------------------

PRINTER_SETTINGS_HINT = "printerSettings"


def thumbnail_parts(pkg: Package) -> list[str]:
    found = {pkg.entry_name(r.part) for r in pkg.rels("")
             if r.type == THUMBNAIL and not r.external and r.part is not None}
    found |= {n for n in pkg.part_names() if n.lower().startswith("docprops/thumbnail.")}
    return sorted(n for n in found if n is not None)


def thumbnail_section(pkg: Package) -> Section | None:
    """The preview picture of the first page or slide (docProps/thumbnail.*)."""
    section = Section("office_thumbnail", "Thumbnail")
    for name in thumbnail_parts(pkg):
        section.add(name, name, f"{pkg.type_of(name) or 'image'}, "
                    f"{pkg.archive.get(name).size} bytes", sensitive=True)
    return section if section.fields else None


def printer_settings_parts(pkg: Package) -> list[str]:
    found = [pkg.entry_name(r.part) for r in pkg.all_relationships()
             if r.type_name == "printerSettings" and not r.external and r.part is not None]
    found += [n for n in pkg.part_names() if PRINTER_SETTINGS_HINT in (pkg.type_of(n) or "")]
    out: list[str] = []
    for n in found:
        if n is not None and n not in out:
            out.append(n)
    return out


def printer_name(devmode: bytes) -> str | None:
    """The printer's name from a Windows DEVMODE structure (its first 32
    UTF-16 characters)."""
    if len(devmode) < 64:
        return None
    name = devmode[:64].decode("utf-16-le", errors="replace").split("\x00", 1)[0].strip()
    return name or None


def printer_section(pkg: Package) -> Section | None:
    """Printer settings (printerSettings*.bin): the printer's name and
    driver settings, saved with the document."""
    section = Section("printer_settings", "Printer settings")
    for name in printer_settings_parts(pkg):
        data = pkg.read(name)
        printer = printer_name(data)
        value = f"{len(data)} bytes" + (f", printer {printer!r}" if printer else "")
        section.add(name, name, value, sensitive=bool(printer))
    return section if section.fields else None


def macros_section(pkg: Package) -> Section | None:
    """VBA macro projects (vbaProject.bin)."""
    section = Section("macros", "Macros")
    for name in pkg.vba_parts:
        section.add(name, name, f"VBA project, {pkg.archive.get(name).size} bytes",
                    sensitive=True)
    return section if section.fields else None


def unused_parts_section(pkg: Package) -> Section | None:
    """Parts no relationship leads to: Office never shows them, so they can
    only hide data (or be left over from editing)."""
    section = Section("office_hidden_parts", "Unused parts (hidden data)")
    parts = pkg.unreferenced()
    for name in parts[:MAX_LISTED]:
        section.add(name, name, f"{pkg.type_of(name) or 'unknown type'}, "
                    f"{pkg.archive.get(name).size} bytes", sensitive=True)
    _limited(section, len(parts))
    return section if section.fields else None


# -- the whole report ------------------------------------------------------------------------

KIND_NAMES = {"docx": "Word document", "xlsx": "Excel workbook", "pptx": "PowerPoint presentation"}
XML_SUFFIXES = (".xml", ".rels", ".vml")


def xml_parts(pkg: Package) -> list[str]:
    """Parts that are XML, by content type or name."""
    return [n for n in pkg.part_names()
            if n.lower().endswith(XML_SUFFIXES) or (pkg.type_of(n) or "").endswith("xml")]


def check_xml_parts(pkg: Package) -> None:
    """Refuse the package if any XML part declares a DOCTYPE or entities
    (also the parts metaex doesn't otherwise parse)."""
    from metaex.core.xmldoc import check_no_dtd

    for name in xml_parts(pkg):
        check_no_dtd(pkg.read(name), name)


def structure_section(pkg: Package) -> Section:
    """Every entry of the archive with its content type and sizes."""
    section = Section("structure", "Package parts")
    for e in pkg.archive.entries[:MAX_LISTED * 5]:
        method = {0: "stored", 8: "deflated"}.get(e.method, str(e.method))
        section.add(e.name, e.name, f"{pkg.type_of(e.name) or '-'}, {e.size} bytes ({method})")
    if len(pkg.archive.entries) > MAX_LISTED * 5:
        section.add("more", "More", f"{len(pkg.archive.entries) - MAX_LISTED * 5} more")
    return section


def _collect(builders, pkg: Package, warnings: list[str]) -> tuple[list[Section], bool]:
    """Run each section builder; a part that can't be read becomes a warning
    and marks the report incomplete, without stopping the others."""
    sections: list[Section] = []
    complete = True
    for builder in builders:
        try:
            found = builder(pkg)
        except MalformedFileError as exc:
            warnings.append(f"part of the document could not be read: {exc}; its metadata "
                            "was NOT examined")
            complete = False
            continue
        if isinstance(found, Section):
            sections.append(found)
        elif found:
            sections.extend(found)
    return sections, complete


def _merge(sections: list[Section]) -> list[Section]:
    """One section per key for the sections that several builders may add
    to (local file paths), in first-seen order."""
    out: list[Section] = []
    by_key: dict[str, Section] = {}
    for section in sections:
        if section.key == "local_paths" and section.key in by_key:
            by_key[section.key].fields.extend(section.fields)
            continue
        by_key.setdefault(section.key, section)
        out.append(section)
    return out


def summary_warnings(pkg: Package, sections: list[Section]) -> list[str]:
    """What a person should know before sharing the file."""
    found = {s.key: s for s in sections}
    warnings = []
    changes = found.get("tracked_changes")
    if changes is not None:
        deleted = changes.get("deleted_characters")
        if deleted is not None:
            warnings.append(f"CRITICAL: the document has tracked changes, and {deleted.value} "
                            "characters of deleted text are still stored in the file")
        else:
            warnings.append("the document has tracked changes (who changed what, and when)")
    paths = found.get("local_paths")
    if paths is not None:
        warnings.append(f"CRITICAL: {len(paths.fields)} file path(s) reveal a user name")
    if "office_hidden_parts" in found:
        warnings.append("CRITICAL: the package holds parts nothing in the document uses "
                        "(hidden data)")
    if "zip_hidden" in found:
        warnings.append("CRITICAL: the ZIP container holds data outside its entries "
                        "(hidden data)")
    if pkg.vba_parts:
        warnings.append("the document contains VBA macros; their code can hold names, paths "
                        "and other identifying information")
    if "office_embedded" in found:
        warnings.append("the document contains embedded objects (other files); metaex can't "
                        "clean their contents")
    return warnings


def parse_package(data: bytes | bytearray | memoryview, kind: str, format_builders) -> dict:
    """Everything metaex reports for an OOXML file of ``kind``: the package
    parts shared by all formats, plus ``format_builders`` (functions of the
    Package returning sections) for the format's own parts.

    Raises MalformedFileError if the package can't be opened at all, and
    UnsupportedFormatError for Strict Open XML and binary workbooks.
    """
    from metaex.core.ziparchive import report_sections

    pkg = open_package(data)
    if pkg.kind != kind:
        raise MalformedFileError(f"the file looks like a {KIND_NAMES[kind]}, but its main "
                                 f"document is a {pkg.description}")
    warnings: list[str] = []
    image_warnings: list[str] = []
    builders = [
        core_section, app_section, custom_sections, custom_xml_section,
        *format_builders,
        external_link_sections, embedded_object_section,
        lambda p: embedded_image_sections(p, image_warnings),
        thumbnail_section, printer_section, macros_section, unused_parts_section,
        lambda p: report_sections(p.archive),
        check_xml_parts,
    ]
    sections, complete = _collect(builders, pkg, warnings)
    sections = _merge(sections)
    warnings = summary_warnings(pkg, sections) + warnings + image_warnings
    if pkg.macro_enabled or pkg.template:
        warnings.append(f"this is a {pkg.description}")
    return {"warnings": warnings, "sections": sections, "structure": structure_section(pkg),
            "complete": complete, "package": pkg}


# -- stripping ---------------------------------------------------------------------------------


class PackageEditor:
    """Changes to a package, written out by build().

    Parts that are not changed are copied with their compressed bytes
    unchanged. A part edited through editor() is re-written with only the
    edited bytes changed. remove_part() also removes the part's own
    relationships, every relationship pointing to it and its content type
    override. build() keeps only what the package needs: the parts that
    relationships still lead to (from the package's own relationships),
    their relationship parts and [Content_Types].xml; everything else is
    dropped, including parts that were unused to begin with.
    """

    def __init__(self, pkg: Package) -> None:
        self.pkg = pkg
        self.editors: dict[str, XmlEditor] = {}
        self.replaced: dict[str, bytes] = {}
        self.removed: set[str] = set()
        self.removed_rels: set[tuple[str, str]] = set()

    def editor(self, part: str) -> XmlEditor:
        name = self.pkg.entry_name(part)
        if name is None:
            raise MalformedFileError(f"the package has no part {part!r}")
        if name not in self.editors:
            self.editors[name] = XmlEditor(self.pkg.xml(name))
        return self.editors[name]

    def replace(self, part: str, data: bytes) -> None:
        name = self.pkg.entry_name(part)
        if name is None:
            raise MalformedFileError(f"the package has no part {part!r}")
        self.replaced[name] = data

    def remove_relationship(self, r: Relationship) -> None:
        if (r.source, r.id) in self.removed_rels:
            return
        self.removed_rels.add((r.source, r.id))
        self.editor(rels_name(r.source)).remove(r.element)

    def remove_part(self, part: str) -> None:
        """Remove a part with its relationships and content type override."""
        name = self.pkg.entry_name(part)
        if name is None or name in self.removed:
            return
        self.removed.add(name)
        own_rels = self.pkg.entry_name(rels_name(name))
        if own_rels is not None:
            self.removed.add(own_rels)
        for r in self.pkg.all_relationships():
            if not r.external and r.part is not None and self.pkg.entry_name(r.part) == name:
                self.remove_relationship(r)
        for e in self.pkg.content_types.override_elements.get(name.lower(), []):
            self.editor(CONTENT_TYPES).remove(e)

    def live_rels(self, source: str) -> list[Relationship]:
        return [r for r in self.pkg.rels(source) if (r.source, r.id) not in self.removed_rels]

    def reachable(self) -> set[str]:
        """Parts the remaining relationships lead to from the package."""
        seen: set[str] = set()
        todo = [""]
        while todo:
            source = todo.pop()
            for r in self.live_rels(source):
                if r.external or r.part is None:
                    continue
                name = self.pkg.entry_name(r.part)
                if name is not None and name not in seen and name not in self.removed:
                    seen.add(name)
                    todo.append(name)
        return seen

    def build(self) -> bytes:
        from metaex.core.zipwriter import copied, new, write_zip

        pkg = self.pkg
        ct_name = pkg.entry_name(CONTENT_TYPES)
        reachable = self.reachable()
        # Unused parts are dropped too (with their content type overrides).
        for name in pkg.part_names():
            if name == ct_name or source_of_rels(name) is not None or name in reachable:
                continue
            self.remove_part(name)
        keep = reachable | {ct_name}
        keep |= {n for n in (pkg.entry_name(rels_name(s)) for s in [""] + sorted(reachable))
                 if n is not None and n not in self.removed}
        entries = []
        for entry in pkg.archive.entries:
            name = entry.name
            if entry.is_dir or name not in keep:
                continue
            if name in self.replaced:
                entries.append(new(name, self.replaced[name], method=entry.method))
            elif name in self.editors and self.editors[name].changed:
                data = self.editors[name].result()
                parse_xml(data, name)  # self-check: still well-formed, prefixes bound
                entries.append(new(name, data, method=entry.method))
            else:
                entries.append(copied(pkg.archive, entry))
        return write_zip(entries, first=(ct_name,))


def strip_properties(ed: PackageEditor) -> None:
    """Empty core.xml and app.xml (keeping the parts, their root elements and
    namespace declarations); remove custom.xml and the sensitivity label
    part (docMetadata/LabelInfo.xml)."""
    pkg = ed.pkg
    for part in (core_part(pkg), app_part(pkg)):
        if part is None:
            continue
        root = pkg.xml(part).root
        if root.children:
            ed.editor(part).set_content(root, "")
    for part in (custom_part(pkg), label_info_part(pkg)):
        if part is not None:
            ed.remove_part(part)


def custom_xml_props_part(pkg: Package, item: str) -> str | None:
    found = pkg.related(item, CUSTOM_XML_PROPS_REL)
    return pkg.entry_name(found[0]) if found else None


def custom_xml_item_id(pkg: Package, item: str) -> str | None:
    """The data store item ID ({GUID}) of a custom XML part, from its
    properties part; content controls refer to their data by it."""
    props = custom_xml_props_part(pkg, item)
    if props is None:
        return None
    return pkg.xml(props).root.get("itemID", DS_NS)


def strip_custom_xml(ed: PackageEditor, keep: set[str] = frozenset()) -> None:
    """Remove the custom XML parts, except those in ``keep`` (data that
    content controls show: removing it would change the document)."""
    for item in custom_xml_parts(ed.pkg):
        if item in keep:
            continue
        props = custom_xml_props_part(ed.pkg, item)
        ed.remove_part(item)
        if props is not None:
            ed.remove_part(props)


def strip_package(data: bytes | bytearray | memoryview, kind: str, format_strip) -> bytes:
    """A cleaned copy of an OOXML file of ``kind``: the package-level
    clean-up plus ``format_strip(editor)`` for the format's own parts.

    Raises MalformedFileError if the package is damaged or unsafe (also a
    DOCTYPE in any XML part), UnsupportedFormatError for Strict Open XML.
    """
    pkg = open_package(data)
    if pkg.kind != kind:
        raise MalformedFileError(f"the file looks like a {KIND_NAMES[kind]}, but its main "
                                 f"document is a {pkg.description}")
    check_xml_parts(pkg)
    ed = PackageEditor(pkg)
    strip_properties(ed)
    format_strip(ed)
    return ed.build()
