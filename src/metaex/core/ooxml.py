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
from metaex.core.xmldoc import XmlDocument, XmlElement, parse_xml
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
            overrides[urllib.parse.unquote(name.lstrip("/")).lower()] = ctype
    return ContentTypes(defaults, overrides, doc)


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
                 limits: ZipLimits = DEFAULT_LIMITS) -> Package:
    """Open an OOXML package: content types, every relationship part and
    the main document's type. Raises MalformedFileError if the package is
    damaged or unsafe, UnsupportedFormatError for Strict Open XML and
    binary workbooks."""
    archive = open_archive(data, limits)
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
