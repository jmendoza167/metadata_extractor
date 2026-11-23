"""OpenDocument packages (ODT, ODS, ODP): what LibreOffice and other
OpenDocument programs store besides the document itself.

An OpenDocument file is a ZIP archive with a "mimetype" entry (stored,
first) naming the document type, META-INF/manifest.xml listing every
file, content.xml and styles.xml (the document), meta.xml (authors, dates,
editing time, template, ...), settings.xml (view and printer settings),
Thumbnails/thumbnail.png, Pictures/, embedded objects (charts: "Object 1/"
with their own meta.xml), Basic/ and Scripts/ (macros) and
Configurations2/ (user interface settings).

Refused: archives without a known mimetype or manifest, encrypted
(password-protected) documents, and anything the ZIP reader or the XML
parser refuses (a DOCTYPE or entity in any XML file, too large, ...).
"""

from __future__ import annotations

import base64
import binascii
import posixpath
import re
from dataclasses import dataclass, field

from metaex.core.errors import MalformedFileError, UnsupportedFormatError
from metaex.core.ooxml import (MAX_LISTED, add_link, comments_summary, date_range,
                               deleted_text_value, distinct, shorten)
from metaex.core.result import Section
from metaex.core.xmldoc import XmlDocument, XmlElement, check_no_dtd, parse_xml
from metaex.core.ziparchive import DEFAULT_LIMITS, ZipArchive, ZipLimits, open_archive

OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
META_NS = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
CONFIG_NS = "urn:oasis:names:tc:opendocument:xmlns:config:1.0"
TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
DRAW_NS = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
STYLE_NS = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
PRESENTATION_NS = "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"
MANIFEST_NS = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
DC_NS = "http://purl.org/dc/elements/1.1/"
XLINK_NS = "http://www.w3.org/1999/xlink"
OFFICEOOO_NS = "http://openoffice.org/2009/office"
LOEXT_NS = "urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0"

MIMETYPE = "mimetype"
MANIFEST = "META-INF/manifest.xml"

KINDS = {
    "application/vnd.oasis.opendocument.text": ("odt", False, "Text document"),
    "application/vnd.oasis.opendocument.text-template": ("odt", True, "Text document template"),
    "application/vnd.oasis.opendocument.spreadsheet": ("ods", False, "Spreadsheet"),
    "application/vnd.oasis.opendocument.spreadsheet-template":
        ("ods", True, "Spreadsheet template"),
    "application/vnd.oasis.opendocument.presentation": ("odp", False, "Presentation"),
    "application/vnd.oasis.opendocument.presentation-template":
        ("odp", True, "Presentation template"),
}
KIND_NAMES = {"odt": "text document (ODT)", "ods": "spreadsheet (ODS)",
              "odp": "presentation (ODP)"}
MACRO_FOLDERS = ("Basic/", "Scripts/")
CONFIG_FOLDER = "Configurations2/"
THUMBNAIL_FOLDER = "Thumbnails/"
XML_SUFFIXES = (".xml", ".rdf")


@dataclass
class OdfPackage:
    archive: ZipArchive
    mimetype: str
    kind: str
    template: bool
    description: str
    manifest: XmlDocument
    entries: dict[str, str]
    """Manifest: full path -> media type (folders end with "/"; "/" is the document)."""
    entry_elements: dict[str, XmlElement] = field(default_factory=dict, repr=False)
    _xml: dict[str, XmlDocument] = field(default_factory=dict, repr=False)

    def names(self) -> list[str]:
        """Every file in the archive (not folders), in archive order."""
        return [e.name for e in self.archive.entries if not e.is_dir]

    def has(self, name: str) -> bool:
        entry = self.archive.get(name)
        return entry is not None and not entry.is_dir

    def read(self, name: str) -> bytes:
        return self.archive.read(name)

    def xml(self, name: str) -> XmlDocument:
        if name not in self._xml:
            if not self.has(name):
                raise MalformedFileError(f"the document has no {name}")
            self._xml[name] = parse_xml(self.read(name), name)
        return self._xml[name]

    def xml_names(self) -> list[str]:
        return [n for n in self.names() if n.lower().endswith(XML_SUFFIXES)
                or (self.entries.get(n) or "").endswith("xml")]

    def content_names(self) -> list[str]:
        """content.xml and styles.xml of the document and of every embedded
        object (charts, formulas: "Object 1/content.xml")."""
        return [n for n in self.names()
                if posixpath.basename(n) in ("content.xml", "styles.xml")]

    def meta_names(self) -> list[str]:
        return [n for n in self.names() if posixpath.basename(n) == "meta.xml"]

    @property
    def encrypted(self) -> bool:
        return any(e.find_all(MANIFEST_NS, "encryption-data") for e in self.entry_elements.values())


def open_odf(data: bytes | bytearray | memoryview, limits: ZipLimits = DEFAULT_LIMITS
             ) -> OdfPackage:
    """Open an OpenDocument package: mimetype, manifest and document type.

    Raises MalformedFileError if it is damaged or unsafe, and
    UnsupportedFormatError if it is encrypted or of another type."""
    archive = open_archive(data, limits)
    if archive.get(MIMETYPE) is None:
        raise MalformedFileError("not an OpenDocument file (no mimetype entry)")
    raw = archive.read(MIMETYPE)
    try:
        mimetype = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        raise MalformedFileError("the mimetype entry is not plain text") from None
    if mimetype not in KINDS:
        raise UnsupportedFormatError(f"OpenDocument type {mimetype!r} is not supported")
    if archive.get(MANIFEST) is None:
        raise MalformedFileError(f"the OpenDocument file has no {MANIFEST}")
    manifest = parse_xml(archive.read(MANIFEST), MANIFEST)
    if not manifest.root.is_(MANIFEST_NS, "manifest"):
        raise MalformedFileError(f"{MANIFEST} has the wrong root element <{manifest.root.name}>")
    entries: dict[str, str] = {}
    elements: dict[str, XmlElement] = {}
    for e in manifest.root.elements():
        if e.is_(MANIFEST_NS, "file-entry"):
            path = e.get("full-path", MANIFEST_NS)
            if path is None:
                raise MalformedFileError(f"{MANIFEST}: a file entry has no full-path")
            entries[path] = e.get("media-type", MANIFEST_NS) or ""
            elements[path] = e
    kind, template, description = KINDS[mimetype]
    pkg = OdfPackage(archive, mimetype, kind, template, description, manifest, entries, elements)
    if pkg.encrypted:
        raise UnsupportedFormatError("this OpenDocument file is password-protected (encrypted), "
                                     "so metaex can't read it. Remove the password in "
                                     "LibreOffice (File > Properties > Security, or save "
                                     "without \"Save with password\") and try again")
    return pkg


def check_xml_files(pkg: OdfPackage) -> None:
    for name in pkg.xml_names():
        check_no_dtd(pkg.read(name), name)


# -- meta.xml ------------------------------------------------------------------------------------

META_FIELDS: dict[tuple[str, str], str] = {
    (META_NS, "initial-creator"): "Author (initial creator)",
    (DC_NS, "creator"): "Last modified by",
    (META_NS, "creation-date"): "Created",
    (DC_NS, "date"): "Modified",
    (META_NS, "printed-by"): "Last printed by",
    (META_NS, "print-date"): "Last printed",
    (META_NS, "editing-cycles"): "Revision number (editing cycles)",
    (META_NS, "editing-duration"): "Total editing time",
    (META_NS, "generator"): "Application",
    (META_NS, "template"): "Template",
    (DC_NS, "title"): "Title",
    (DC_NS, "subject"): "Subject",
    (DC_NS, "description"): "Comments (description)",
    (META_NS, "keyword"): "Keywords",
    (DC_NS, "language"): "Language",
    (META_NS, "auto-reload"): "Auto reload",
    (META_NS, "hyperlink-behaviour"): "Hyperlink behaviour",
}


def meta_sections(pkg: OdfPackage) -> list[Section]:
    """meta.xml of the document (and of every embedded object): authors,
    dates, editing cycles and time, application, template, who printed
    it; user-defined properties in a section of their own."""
    meta = Section("odf_meta", "Document properties")
    user = Section("odf_user_defined", "Custom properties")
    paths = Section("local_paths", "Local file paths")
    for name in pkg.meta_names():
        folder = posixpath.dirname(name)
        prefix_key = f"{folder}:" if folder else ""
        prefix_label = f"{folder}: " if folder else ""
        root = pkg.xml(name).root
        office_meta = root.child(OFFICE_NS, "meta")
        for e in (office_meta.elements() if office_meta is not None else ()):
            key = e.local
            if e.is_(META_NS, "user-defined"):
                prop = e.get("name", META_NS) or "(no name)"
                user.add(f"{prefix_key}{prop}", f"{prefix_label}{prop}", shorten(e.text()),
                         sensitive=True)
                continue
            if e.is_(META_NS, "document-statistic"):
                for attr, value in e.attrs:
                    if not attr.startswith("xmlns"):
                        local = attr.split(":", 1)[-1]
                        meta.add(f"{prefix_key}document-statistic.{local}",
                                 f"{prefix_label}{local}", value)
                continue
            label = META_FIELDS.get((e.uri, e.local), e.name)
            if e.is_(META_NS, "template"):
                href = e.get("href", XLINK_NS) or ""
                title = e.get("title", XLINK_NS)
                date = e.get("date", META_NS)
                add_link(meta, paths, f"{prefix_key}template", f"{prefix_label}Template", href)
                if title:
                    meta.add(f"{prefix_key}template-title", f"{prefix_label}Template name",
                             title, sensitive=True)
                if date:
                    meta.add(f"{prefix_key}template-date", f"{prefix_label}Template date", date,
                             sensitive=True)
                continue
            value = " ".join(e.text().split()) if key != "description" else e.text()
            if value or e.attrs:
                if (e.uri, e.local) not in META_FIELDS:
                    key = f"other.{e.local}"
                meta.add(f"{prefix_key}{key}", f"{prefix_label}{label}",
                         shorten(value or ", ".join(f"{a}={v}" for a, v in e.attrs)),
                         sensitive=True)
    return [s for s in (meta, user, paths) if s.fields]


# -- settings.xml -------------------------------------------------------------------------------

PRINTER_ITEMS = ("PrinterName", "PrinterSetup", "PrinterPaperFromSetup", "PrintFaxName")
DATA_SOURCE_ITEMS = ("CurrentDatabaseDataSource", "CurrentDatabaseCommand",
                     "CurrentDatabaseCommandType", "EmbeddedDatabaseName")
RSID_ITEMS = ("Rsid", "RsidRoot")
_PRINTABLE = re.compile(rb"[\x20-\x7e]{4,}")


def printer_setup_text(value: str) -> str:
    """What a PrinterSetup blob (base64) reveals: its size and the readable
    strings in it (printer and driver names)."""
    try:
        raw = base64.b64decode(value, validate=False)
    except (binascii.Error, ValueError):
        return f"{len(value)} characters (not valid base64)"
    names = distinct(m.group().decode("ascii").strip() for m in _PRINTABLE.finditer(raw[:512]))
    return f"{len(raw)} bytes" + (f" ({', '.join(names[:4])})" if names else "")


def settings_items(pkg: OdfPackage) -> list[tuple[str, XmlElement]]:
    """(name, config-item) of the printer, data source and rsid settings in
    settings.xml."""
    if not pkg.has("settings.xml"):
        return []
    out = []
    for e in pkg.xml("settings.xml").find_all(CONFIG_NS, "config-item"):
        name = e.get("name", CONFIG_NS) or ""
        if name in PRINTER_ITEMS + DATA_SOURCE_ITEMS + RSID_ITEMS:
            out.append((name, e))
    return out


def settings_sections(pkg: OdfPackage) -> list[Section]:
    printer = Section("printer_settings", "Printer settings")
    data = Section("odf_settings", "Application settings")
    rsids = Section("revision_ids", "Revision IDs (rsids)")
    for name, e in settings_items(pkg):
        value = e.text()
        if not value.strip():
            continue
        if name == "PrinterSetup":
            printer.add(name, "Printer setup", printer_setup_text(value), sensitive=True)
        elif name in PRINTER_ITEMS:
            printer.add(name, name, shorten(value), sensitive=True)
        elif name in DATA_SOURCE_ITEMS:
            data.add(name, name, shorten(value), sensitive=True)
        else:
            rsids.add(name, f"{name} (settings.xml)", value)
    count = 0
    for part in pkg.content_names():
        for e in pkg.xml(part).iter():
            count += sum(1 for a, _v in e.attrs if is_rsid_attribute(e, a))
    if count:
        rsids.add("rsid_attributes", "Revision ID attributes", count)
    return [s for s in (printer, data, rsids) if s.fields]


def is_rsid_attribute(element: XmlElement, name: str) -> bool:
    return (element.attr_uri(name) == OFFICEOOO_NS
            and name.split(":", 1)[-1] in ("rsid", "paragraph-rsid"))


# -- annotations, tracked changes ---------------------------------------------------------------

ANNOTATION_NAMESPACES = (OFFICE_NS, OFFICEOOO_NS)


def annotations(pkg: OdfPackage) -> list[tuple[str, XmlElement]]:
    """(part, element) of every comment (office:annotation; in older
    presentations officeooo:annotation)."""
    return [(part, e) for part in pkg.content_names() for e in pkg.xml(part).iter()
            if e.local == "annotation" and e.uri in ANNOTATION_NAMESPACES]


def annotation_details(e: XmlElement) -> tuple[str, str, str]:
    """(author, initials, date) of a comment."""
    author = initials = date = ""
    for child in e.elements():
        if child.is_(DC_NS, "creator"):
            author = child.text()
        elif child.is_(DC_NS, "date"):
            date = child.text()
        elif child.local == "creator-initials" and child.uri in (META_NS, LOEXT_NS):
            initials = child.text()
    return author, initials, date


def comments_section(pkg: OdfPackage) -> Section | None:
    section = Section("office_comments", "Comments")
    found = [annotation_details(e) for _part, e in annotations(pkg)]
    comments_summary(section, len(found), [a for a, _i, _d in found],
                     [i for _a, i, _d in found], [d for _a, _i, d in found])
    return section if section.fields else None


CHANGE_KINDS = {"insertion": "insertions", "deletion": "deletions",
                "format-change": "formatting", "cell-content-change": "formatting",
                "movement": "moves", "insertion-cut-off": "deletions"}


def change_infos(pkg: OdfPackage) -> list[tuple[str, XmlElement]]:
    """(part, office:change-info) of every tracked change."""
    return [(part, e) for part in pkg.content_names() for e in pkg.xml(part).iter()
            if e.is_(OFFICE_NS, "change-info")]


def tracked_changes_section(pkg: OdfPackage) -> Section | None:
    """Tracked changes (text:tracked-changes, table:tracked-changes): how
    many, by whom and when, and how much deleted text is still stored."""
    counts = {"insertions": 0, "deletions": 0, "moves": 0, "formatting": 0}
    authors, dates, deleted = [], [], []
    for _part, info in change_infos(pkg):
        change = info.parent
        kind = CHANGE_KINDS.get(change.local if change is not None else "", "formatting")
        counts[kind] += 1
        for child in info.elements():
            if child.is_(DC_NS, "creator"):
                authors.append(child.text())
            elif child.is_(DC_NS, "date"):
                dates.append(child.text())
        if change is not None and "deletion" in change.local:
            deleted.append("".join(p.text() for p in change.iter()
                                   if p.uri == TEXT_NS and p.local in ("p", "h")))
    if not any(counts.values()):
        return None
    section = Section("tracked_changes", "Tracked changes")
    labels = {"insertions": "Insertions", "deletions": "Deletions", "moves": "Moves",
              "formatting": "Formatting changes"}
    for key, n in counts.items():
        if n:
            section.add(key, labels[key], n)
    if any(authors):
        section.add("authors", "Revision authors", distinct(authors), sensitive=True)
    if any(dates):
        section.add("dates", "Revision dates", date_range(dates), sensitive=True)
    text = "".join(deleted)
    if text:
        section.add("deleted_characters", "Deleted text still in the file (characters)",
                    len(text), sensitive=True)
        section.add("deleted_text", "Deleted text", deleted_text_value(text), sensitive=True)
    return section


# -- links, hidden content, files ------------------------------------------------------------------


def links(pkg: OdfPackage) -> list[tuple[str, XmlElement, str]]:
    """(part, element, href) of every xlink:href that leaves the document
    (not an internal "#..." or "./Object 1" reference)."""
    out = []
    for part in pkg.content_names():
        for e in pkg.xml(part).iter():
            href = e.get("href", XLINK_NS)
            if href and not href.startswith(("#", "./", "Pictures/", "Object ")) \
                    and href.rstrip("/") not in pkg.entries and not pkg.has(href):
                out.append((part, e, href))
    return out


def link_sections(pkg: OdfPackage) -> list[Section]:
    external = Section("office_external", "External links")
    paths = Section("local_paths", "Local file paths")
    found = links(pkg)
    for n, (part, e, href) in enumerate(found[:MAX_LISTED], 1):
        add_link(external, paths, f"{part}:{n}", f"<{e.name}> ({part})", href)
    return [s for s in (external, paths) if s.fields]


def hidden_section(pkg: OdfPackage) -> Section | None:
    """Hidden text and sections (ODT), hidden sheets (ODS), hidden slides
    and speaker notes (ODP)."""
    section = Section("hidden_content", "Hidden content")
    hidden_styles = set()
    notes_text = []
    for part in pkg.content_names():
        doc = pkg.xml(part)
        for e in doc.iter():
            if e.is_(STYLE_NS, "style"):
                props = e.child(STYLE_NS, "table-properties") or e.child(
                    STYLE_NS, "drawing-page-properties")
                if props is not None and (props.get("display", TABLE_NS) == "false"
                                          or props.get("visibility", PRESENTATION_NS)
                                          == "hidden"):
                    hidden_styles.add(e.get("name", STYLE_NS))
    for part in pkg.content_names():
        for e in pkg.xml(part).iter():
            if e.uri == TEXT_NS and e.local in ("hidden-text", "hidden-paragraph"):
                section.add(f"hidden_text.{part}.{len(section.fields)}", "Hidden text",
                            shorten(e.get("string-value", TEXT_NS) or e.text()), sensitive=True)
            elif e.is_(TEXT_NS, "section") and e.get("display", TEXT_NS) == "none":
                section.add(f"hidden_section.{part}.{len(section.fields)}", "Hidden section",
                            shorten(" ".join(e.text().split())), sensitive=True)
            elif e.is_(TABLE_NS, "table") and e.get("style-name", TABLE_NS) in hidden_styles:
                name = e.get("name", TABLE_NS) or "(no name)"
                section.add(f"sheet.{name}", "Hidden sheet", name, sensitive=True)
            elif e.is_(DRAW_NS, "page"):
                if e.get("style-name", DRAW_NS) in hidden_styles:
                    name = e.get("name", DRAW_NS) or "(no name)"
                    section.add(f"hidden_slide.{name}", "Hidden slide", name, sensitive=True)
                notes = e.child(PRESENTATION_NS, "notes")
                if notes is not None:
                    text = " ".join(" ".join(p.text() for p in notes.iter()
                                             if p.is_(TEXT_NS, "p")).split())
                    if text:
                        notes_text.append(text)
    if notes_text:
        section.add("notes", "Slides with speaker notes", len(notes_text), sensitive=True)
        joined = " | ".join(notes_text)
        section.add("notes_text", "Speaker notes",
                    joined if len(joined) <= 200 else joined[:200] + "...", sensitive=True)
    return section if section.fields else None


def thumbnail_names(pkg: OdfPackage) -> list[str]:
    return [n for n in pkg.names() if n.startswith(THUMBNAIL_FOLDER)]


def thumbnail_section(pkg: OdfPackage) -> Section | None:
    section = Section("office_thumbnail", "Thumbnail")
    for name in thumbnail_names(pkg):
        section.add(name, name, f"{pkg.archive.get(name).size} bytes", sensitive=True)
    return section if section.fields else None


def macro_names(pkg: OdfPackage) -> list[str]:
    return [n for n in pkg.names() if n.startswith(MACRO_FOLDERS)]


def macros_section(pkg: OdfPackage) -> Section | None:
    """Basic macros (Basic/) and scripts (Scripts/)."""
    section = Section("macros", "Macros")
    names = macro_names(pkg)
    for name in names[:MAX_LISTED]:
        section.add(name, name, f"{pkg.archive.get(name).size} bytes", sensitive=True)
    return section if section.fields else None


def config_names(pkg: OdfPackage) -> list[str]:
    return [n for n in pkg.names() if n.startswith(CONFIG_FOLDER)]


def config_section(pkg: OdfPackage) -> Section | None:
    """User interface settings saved with the document (Configurations2/)."""
    section = Section("odf_configurations", "User interface settings (Configurations2/)")
    for name in config_names(pkg)[:MAX_LISTED]:
        section.add(name, name, f"{pkg.archive.get(name).size} bytes")
    return section if section.fields else None


def picture_names(pkg: OdfPackage) -> list[str]:
    """Images stored in the document (Pictures/, object replacement images)."""
    return [n for n in pkg.names() if not n.startswith(THUMBNAIL_FOLDER)
            and (n.startswith(("Pictures/", "ObjectReplacements/"))
                 or (pkg.entries.get(n) or "").startswith("image/"))]


def object_folders(pkg: OdfPackage) -> list[str]:
    """Embedded objects (charts, formulas, ...): folders with their own content."""
    return sorted(p for p, t in pkg.entries.items()
                  if p.endswith("/") and p != "/" and "opendocument" in t)


def embedded_section(pkg: OdfPackage) -> Section | None:
    section = Section("office_embedded", "Embedded objects")
    for folder in object_folders(pkg):
        section.add(folder, folder.rstrip("/"), pkg.entries[folder] or "unknown type")
    others = [n for n in pkg.names() if pkg.entries.get(n, "").startswith(
        ("application/vnd.sun.star.oleobject", "application/x-openoffice"))]
    for name in others[:MAX_LISTED]:
        section.add(name, name, f"{pkg.entries[name]}, {pkg.archive.get(name).size} bytes",
                    sensitive=True)
    return section if section.fields else None


def signature_names(pkg: OdfPackage) -> list[str]:
    """Files in META-INF/ other than the manifest: digital signatures (they
    name the signer) and data added by other programs."""
    return [n for n in pkg.names() if n.startswith("META-INF/") and n != MANIFEST]


def signatures_section(pkg: OdfPackage) -> Section | None:
    section = Section("odf_signatures", "Digital signatures and other META-INF files")
    for name in signature_names(pkg)[:MAX_LISTED]:
        section.add(name, name, f"{pkg.archive.get(name).size} bytes", sensitive=True)
    return section if section.fields else None


def unlisted_names(pkg: OdfPackage) -> list[str]:
    """Files the manifest doesn't list (other than mimetype and the
    manifest itself): no OpenDocument program uses them."""
    return [n for n in pkg.names()
            if n not in (MIMETYPE, MANIFEST) and n not in pkg.entries
            and not n.startswith("META-INF/")]


def unlisted_section(pkg: OdfPackage) -> Section | None:
    section = Section("office_hidden_parts", "Unlisted files (hidden data)")
    names = unlisted_names(pkg)
    for name in names[:MAX_LISTED]:
        section.add(name, name, f"{pkg.archive.get(name).size} bytes", sensitive=True)
    return section if section.fields else None


def embedded_image_sections(pkg: OdfPackage, warnings: list[str]) -> list[Section]:
    """The images in the document, then each image's own metadata sections."""
    from metaex.core import filetype
    from metaex.core.ooxml import IMAGE_KEYS
    from metaex.core.registry import format_module

    listing = Section("embedded_images", "Embedded images")
    inner: list[Section] = []
    for name in picture_names(pkg)[:1000]:
        data = pkg.read(name)
        detected = filetype.detect(data)
        kind = detected.name if detected.key != filetype.UNKNOWN.key else (
            pkg.entries.get(name) or "unknown data")
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
        listing.add(name, name, f"{kind}, {len(data)} bytes")
        prefix = f"{name} ({kind})"
        warnings += [(f"CRITICAL: {prefix}: {w[10:]}" if w.startswith("CRITICAL: ")
                      else f"{prefix}: {w}") for w in parsed.get("warnings", [])]
        inner += [Section(s.key, f"{prefix}: {s.title}", s.fields)
                  for s in parsed.get("sections", [])]
    return ([listing] if listing.fields else []) + inner


def structure_section(pkg: OdfPackage) -> Section:
    section = Section("structure", "Package files")
    for e in pkg.archive.entries[:1000]:
        method = {0: "stored", 8: "deflated"}.get(e.method, str(e.method))
        section.add(e.name, e.name, f"{pkg.entries.get(e.name) or '-'}, {e.size} bytes "
                    f"({method})")
    return section


def summary_warnings(pkg: OdfPackage, sections: list[Section]) -> list[str]:
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
    if "local_paths" in found:
        warnings.append(f"CRITICAL: {len(found['local_paths'].fields)} file path(s) reveal a "
                        "user name")
    if "office_hidden_parts" in found:
        warnings.append("CRITICAL: the archive holds files the manifest doesn't list "
                        "(hidden data)")
    if "zip_hidden" in found:
        warnings.append("CRITICAL: the ZIP container holds data outside its entries "
                        "(hidden data)")
    if "macros" in found:
        warnings.append("the document contains macros (Basic or scripts); their code can "
                        "hold names, paths and other identifying information")
    if pkg.archive.entries and pkg.archive.entries[0].name != MIMETYPE:
        warnings.append("the mimetype entry is not the first in the archive")
    return warnings


def parse_package(data: bytes | bytearray | memoryview, kind: str) -> dict:
    """Everything metaex reports for an OpenDocument file of ``kind``."""
    from metaex.core.ooxml import _collect, _merge
    from metaex.core.ziparchive import report_sections

    pkg = open_odf(data)
    if pkg.kind != kind:
        raise MalformedFileError(f"the file looks like a {KIND_NAMES[kind]}, but it is a "
                                 f"{KIND_NAMES[pkg.kind]}")
    warnings: list[str] = []
    image_warnings: list[str] = []
    builders = [meta_sections, settings_sections, comments_section, tracked_changes_section,
                hidden_section, link_sections, embedded_section,
                lambda p: embedded_image_sections(p, image_warnings),
                thumbnail_section, macros_section, config_section, signatures_section,
                unlisted_section,
                lambda p: report_sections(p.archive), check_xml_files]
    sections, complete = _collect(builders, pkg, warnings)
    sections = _merge(sections)
    warnings = summary_warnings(pkg, sections) + warnings + image_warnings
    if pkg.template:
        warnings.append(f"this is a {pkg.description.lower()}")
    return {"warnings": warnings, "sections": sections, "structure": structure_section(pkg),
            "complete": complete, "package": pkg}


# -- stripping ---------------------------------------------------------------------------------

STRIPPED_SETTINGS = PRINTER_ITEMS + RSID_ITEMS
PLACEHOLDER_DATE = "2000-01-01T00:00:00"
"""Written where OpenDocument requires a date (a tracked change's dc:date);
optional dates (comments) are removed instead."""


class OdfEditor:
    """Changes to an OpenDocument package, written out by build(): files not
    changed are copied with their compressed bytes unchanged, edited XML is
    written with only the edited bytes changed, and removed files leave the
    manifest too. build() writes "mimetype" first and stored, and drops
    every file the manifest doesn't list (and META-INF files other than the
    manifest)."""

    def __init__(self, pkg: OdfPackage) -> None:
        self.pkg = pkg
        self.editors: dict[str, object] = {}
        self.replaced: dict[str, bytes] = {}
        self.removed: set[str] = set()

    def editor(self, name: str):
        from metaex.core.xmldoc import XmlEditor

        if name not in self.editors:
            self.editors[name] = XmlEditor(self.pkg.xml(name))
        return self.editors[name]

    def remove_file(self, name: str) -> None:
        self.removed.add(name)
        element = self.pkg.entry_elements.get(name)
        if element is not None:
            self.editor(MANIFEST).remove(element)

    def remove_folder(self, folder: str) -> None:
        """Remove every file under ``folder`` and the folder's manifest entries."""
        for name in self.pkg.names():
            if name.startswith(folder):
                self.remove_file(name)
        for path, element in self.pkg.entry_elements.items():
            if path.startswith(folder) and path.endswith("/"):
                self.editor(MANIFEST).remove(element)

    def build(self) -> bytes:
        from metaex.core.ziparchive import STORED
        from metaex.core.zipwriter import copied, new, write_zip

        pkg = self.pkg
        listed = {p for p in pkg.entries if not p.endswith("/")}
        for name in pkg.names():
            unlisted = name not in listed and name not in (MIMETYPE, MANIFEST)
            if unlisted and name not in self.removed:
                self.remove_file(name)
        entries = [new(MIMETYPE, pkg.mimetype.encode("ascii"), method=STORED)]
        for entry in pkg.archive.entries:
            name = entry.name
            if entry.is_dir or name == MIMETYPE or name in self.removed:
                continue
            if name in self.replaced:
                entries.append(new(name, self.replaced[name], method=entry.method))
            elif name in self.editors and self.editors[name].changed:
                data = self.editors[name].result()
                parse_xml(data, name)  # self-check: still well-formed, prefixes bound
                entries.append(new(name, data, method=entry.method))
            else:
                entries.append(copied(pkg.archive, entry))
        return write_zip(entries, first=(MIMETYPE,))


def strip_meta(ed: OdfEditor) -> None:
    """Empty <office:meta> in meta.xml (and in embedded objects' meta.xml)."""
    for name in ed.pkg.meta_names():
        office_meta = ed.pkg.xml(name).root.child(OFFICE_NS, "meta")
        if office_meta is not None and office_meta.children:
            ed.editor(name).set_content(office_meta, "")


def strip_settings(ed: OdfEditor) -> None:
    """Remove the printer (name and driver setup) and the rsids from settings.xml."""
    for name, element in settings_items(ed.pkg):
        if name in STRIPPED_SETTINGS:
            ed.editor("settings.xml").remove(element)


def anonymize(ed: OdfEditor, remove_comments: bool) -> None:
    """Comment authors become "Author" (or the comments are removed), their
    dates and initials go; tracked change authors become "Author" with the
    placeholder date; LibreOffice's revision IDs (officeooo:rsid) go."""
    from metaex.core.ooxml import ANONYMOUS

    pkg = ed.pkg
    for part in pkg.content_names():
        for e in pkg.xml(part).iter():
            if e.local in ("annotation", "annotation-end") and e.uri in ANNOTATION_NAMESPACES:
                if remove_comments:
                    ed.editor(part).remove(e)
                    continue
                for child in e.elements():
                    if child.is_(DC_NS, "creator") and child.text() != ANONYMOUS:
                        ed.editor(part).set_content(child, ANONYMOUS)
                    elif child.is_(DC_NS, "date") or (child.local == "creator-initials"
                                                      and child.uri in (META_NS, LOEXT_NS)):
                        ed.editor(part).remove(child)
            elif e.is_(OFFICE_NS, "change-info"):
                for child in e.elements():
                    if child.is_(DC_NS, "creator") and child.text() != ANONYMOUS:
                        ed.editor(part).set_content(child, ANONYMOUS)
                    elif child.is_(DC_NS, "date") and child.text() != PLACEHOLDER_DATE:
                        ed.editor(part).set_content(child, PLACEHOLDER_DATE)
            for name, _value in e.attrs:
                if is_rsid_attribute(e, name):
                    ed.editor(part).remove_attr(e, name)


def strip_pictures(ed: OdfEditor, keep_orientation: bool, paranoid: bool) -> None:
    """Clean every JPEG, PNG, GIF, WebP and SVG picture with its own format's
    stripper, checked like a cleaned image file."""
    from metaex.core import filetype
    from metaex.core.errors import CannotCleanError, MetaexError
    from metaex.core.ooxml import IMAGE_KEYS
    from metaex.core.registry import format_module
    from metaex.core.verify import verify

    for name in picture_names(ed.pkg):
        data = ed.pkg.read(name)
        detected = filetype.detect(data)
        if detected.key not in IMAGE_KEYS:
            continue
        try:
            cleaned = format_module(detected).strip(data, keep_orientation=keep_orientation,
                                                    paranoid=paranoid)
            result = verify(cleaned, data)
        except MetaexError as exc:
            raise CannotCleanError(f"the picture {name} ({detected.name}) can't be stripped: "
                                   f"{exc}") from None
        if not result.clean or result.image_data_match is not True:
            raise CannotCleanError(f"the picture {name} ({detected.name}) could not be "
                                   "verified after stripping")
        if cleaned != data:
            ed.replaced[name] = cleaned


def refuse_user_paths(pkg: OdfPackage) -> None:
    """Refuse a document whose links point into a user's folder (removing
    them would change the document)."""
    from metaex.core.errors import CannotCleanError
    from metaex.core.paths import local_paths, user_name

    found = [f"{href} (<{e.name}> in {part})" for part, e, href in links(pkg)
             if any(user_name(p) for p in local_paths(href))]
    if found:
        listed = "; ".join(found[:5]) + (f" and {len(found) - 5} more" if len(found) > 5 else "")
        raise CannotCleanError(f"the document links to files in a user's folder: {listed}. "
                               "These paths reveal user names, but removing them would break "
                               "the links; change or remove the links and try again")


def strip_package(data: bytes | bytearray | memoryview, kind: str, *,
                  keep_orientation: bool = True, paranoid: bool = False,
                  remove_comments: bool = False) -> bytes:
    """A cleaned copy of an OpenDocument file of ``kind``. Raises
    MalformedFileError for damaged or unsafe files, UnsupportedFormatError
    for encrypted ones and CannotCleanError for links into a user's folder."""
    pkg = open_odf(data)
    if pkg.kind != kind:
        raise MalformedFileError(f"the file looks like a {KIND_NAMES[kind]}, but it is a "
                                 f"{KIND_NAMES[pkg.kind]}")
    check_xml_files(pkg)
    refuse_user_paths(pkg)
    ed = OdfEditor(pkg)
    strip_meta(ed)
    strip_settings(ed)
    for folder in (THUMBNAIL_FOLDER, CONFIG_FOLDER):
        ed.remove_folder(folder)
    for name in signature_names(pkg):
        ed.remove_file(name)
    anonymize(ed, remove_comments)
    strip_pictures(ed, keep_orientation, paranoid)
    return ed.build()


def kept_warnings(pkg: OdfPackage) -> list[str]:
    """What a cleaned document still contains that the user should know about."""
    out = []
    changes = tracked_changes_section(pkg)
    if changes is not None:
        advice = ("Accept or reject all changes in LibreOffice (Edit > Track Changes > "
                  "Accept All), save, and run metaex again")
        deleted = changes.get("deleted_characters")
        if deleted is not None:
            out.append(f"CRITICAL: the document still contains tracked changes, and "
                       f"{deleted.value} characters of DELETED TEXT are still stored in the "
                       f"file, where anyone can read them. {advice}.")
        else:
            out.append(f'the document still contains tracked changes (their authors are now '
                       f'"Author"). {advice} to remove them.')
    comments = comments_section(pkg)
    if comments is not None:
        out.append(f'kept {comments.get("count").value} comment(s) (their authors are now '
                   '"Author"); use --remove-comments to remove them')
    hidden = hidden_section(pkg)
    if hidden is not None:
        labels = sorted({f.label for f in hidden.fields})
        out.append(f"kept hidden content ({', '.join(labels).lower()}): it is part of the "
                   "document")
    macros = macro_names(pkg)
    if macros:
        out.append(f"the document contains macros ({', '.join(macros[:3])}"
                   f"{' and more' if len(macros) > 3 else ''}); metaex did not change them, "
                   "and their code may contain names, paths or other identifying information")
    objects = object_folders(pkg)
    if objects:
        out.append(f"kept {len(objects)} embedded object(s) ({', '.join(objects[:5])}); their "
                   "metadata was removed, but their contents are part of the document")
    found = links(pkg)
    if found:
        out.append(f"kept {len(found)} external link(s) (e.g. {shorten(found[0][2], 80)}); they "
                   "are part of the document, and opening it may contact those addresses")
    data_sources = [name for name, _e in settings_items(pkg) if name in DATA_SOURCE_ITEMS]
    if data_sources:
        out.append("kept the mail merge data source settings (" + ", ".join(data_sources)
                   + "); they name a database registered in LibreOffice")
    from metaex.core import filetype
    from metaex.core.ooxml import IMAGE_KEYS

    other = [n for n in picture_names(pkg)
             if filetype.detect(pkg.read(n)).key not in IMAGE_KEYS]
    if other:
        out.append(f"kept {len(other)} picture(s) metaex can't clean ({', '.join(other[:5])}); "
                   "they may contain their own metadata")
    return out
