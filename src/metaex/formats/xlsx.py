"""XLSX: Excel workbooks (also .xlsm, .xltx, .xltm).

The package-level parts (document properties, custom XML, relationships,
the ZIP container) are handled in core/ooxml.py; this module reads what is
specific to Excel: comments (legacy notes and threaded comments), the
people who wrote them, external links, hidden sheets and the file's
history pointers.
"""

from __future__ import annotations

import re

from metaex.core.ooxml import Package, add_person, comments_summary
from metaex.core.result import Section

S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
TC_NS = "http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments"
MAX_LISTED = 200

COMMENTS_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml"
THREADED_TYPE = "application/vnd.ms-excel.threadedcomments+xml"
PERSON_TYPE = "application/vnd.ms-excel.person+xml"


def comments_section(pkg: Package) -> Section | None:
    """Legacy comments ("notes", with an author list) and threaded comments
    (with dates and person IDs)."""
    section = Section("office_comments", "Comments")
    authors, count = [], 0
    for part in pkg.parts_of_type(COMMENTS_TYPE):
        root = pkg.xml(part).root
        names = [a.text() for a in root.find_all(S_NS, "author")]
        for c in root.find_all(S_NS, "comment"):
            count += 1
            try:
                authors.append(names[int(c.get("authorId") or "-1")])
            except (ValueError, IndexError):
                pass
        authors += names
    comments_summary(section, count, authors, [], [], "notes" if count else "")
    threaded, dates = 0, []
    for part in pkg.parts_of_type(THREADED_TYPE):
        for c in pkg.xml(part).find_all(TC_NS, "threadedComment"):
            threaded += 1
            dates.append(c.get("dT") or "")
    if threaded:
        comments_summary(section, threaded, [], [], dates, "threaded")
    return section if section.fields else None


def people_section(pkg: Package) -> Section | None:
    """People who wrote threaded comments (xl/persons/person.xml), with the
    account they were signed in with."""
    section = Section("office_people", "People")
    n = 0
    for part in pkg.parts_of_type(PERSON_TYPE):
        for person in pkg.xml(part).find_all(TC_NS, "person"):
            n += 1
            if n > MAX_LISTED:
                break
            add_person(section, n, person.get("displayName"), person.get("userId"),
                       person.get("providerId"))
    return section if section.fields else None


# -- hidden sheets, workbook history, external data -----------------------------------------

X15AC_NS = "http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac"
XR_NS = "http://schemas.microsoft.com/office/spreadsheetml/2014/revision"
XR6_NS = "http://schemas.microsoft.com/office/spreadsheetml/2016/revision6"
XR10_NS = "http://schemas.microsoft.com/office/spreadsheetml/2016/revision10"
EXTERNAL_LINK_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.externalLink+xml"
CONNECTIONS_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.connections+xml"

FILE_VERSION_LABELS = {"appName": "Application", "lastEdited": "Last edited with version",
                       "lowestEdited": "Lowest version edited with",
                       "rupBuild": "Application build"}
REVISION_PTR_LABELS = {"revIDLastSave": "Revision ID at last save",
                       "documentId": "Document ID (co-authoring)",
                       "coauthVersionLast": "Co-authoring version (last)",
                       "coauthVersionMax": "Co-authoring version (highest)",
                       "uidLastSave": "Unique ID at last save"}


def hidden_section(pkg: Package) -> Section | None:
    """Sheets hidden from view ("hidden" can be shown from Excel's menu;
    "veryHidden" only with macros)."""
    section = Section("hidden_content", "Hidden content")
    for sheet in pkg.xml(pkg.main).find_all(S_NS, "sheet"):
        state = sheet.get("state") or "visible"
        if state in ("hidden", "veryHidden"):
            name = sheet.get("name") or "(no name)"
            label = "Hidden sheet" if state == "hidden" else "Very hidden sheet"
            section.add(f"sheet.{name}", label, name, sensitive=True)
    return section if section.fields else None


def workbook_section(pkg: Package) -> list[Section]:
    """What workbook.xml records about where and with what the file was
    saved: the folder (x15ac:absPath), the Excel version (fileVersion) and
    co-authoring revision pointers (xr:revisionPtr)."""
    from metaex.core.ooxml import add_link

    section = Section("xlsx_workbook", "Workbook information")
    paths = Section("local_paths", "Local file paths")
    root = pkg.xml(pkg.main).root
    for e in root.iter():
        if e.is_(X15AC_NS, "absPath"):
            add_link(section, paths, "absPath", "Folder the workbook was saved in (absPath)",
                     e.get("url") or "")
        elif e.is_(S_NS, "fileVersion"):
            for name, value in e.attrs:
                if name in FILE_VERSION_LABELS:
                    section.add(f"fileVersion.{name}", FILE_VERSION_LABELS[name], value)
        elif e.is_(XR_NS, "revisionPtr"):
            for name, value in e.attrs:
                local = name.split(":", 1)[-1]
                if local in REVISION_PTR_LABELS and not name.startswith("xmlns"):
                    section.add(f"revisionPtr.{local}", REVISION_PTR_LABELS[local], value,
                                sensitive=local in ("documentId", "uidLastSave"))
    return [s for s in (section, paths) if s.fields]


def external_data_section(pkg: Package) -> Section | None:
    """Linked workbooks (with values copied from them) and data connections
    (connection strings can name servers, databases and user names)."""
    from metaex.core.ooxml import shorten

    section = Section("xlsx_external_data", "External data")
    for part in pkg.parts_of_type(EXTERNAL_LINK_TYPE):
        root = pkg.xml(part).root
        cells = len(root.find_all(S_NS, "cell"))
        sheets = [s.get("val") or "" for s in root.find_all(S_NS, "sheetName")]
        section.add(part, f"Linked workbook data ({part})",
                    f"{cells} cached cells from sheets {', '.join(sheets) or '(none)'}",
                    sensitive=True)
    for part in pkg.parts_of_type(CONNECTIONS_TYPE):
        for n, conn in enumerate(pkg.xml(part).find_all(S_NS, "connection"), 1):
            details = []
            for child in conn.elements():
                for attr in ("connection", "command", "url", "sourceFile"):
                    value = child.get(attr)
                    if value:
                        details.append(f"{attr}={value}")
            section.add(f"connection.{n}", f"Data connection {conn.get('name') or n}",
                        shorten("; ".join(details) or "(no details)"), sensitive=True)
    return section if section.fields else None


BUILDERS = [comments_section, people_section, hidden_section, workbook_section,
            external_data_section]
"""What this module reports, in order (between the package-level sections)."""


# -- parse -------------------------------------------------------------------------------


def parse(data: bytes | bytearray | memoryview) -> dict:
    """Parse a XLSX file and return its warnings, report sections and
    structure. Raises MalformedFileError if it can't be opened at all,
    UnsupportedFormatError for Strict Open XML and binary workbooks."""
    from metaex.core.ooxml import parse_package

    parsed = parse_package(data, "xlsx", BUILDERS)
    parsed.pop("package")
    return parsed


# -- strip -------------------------------------------------------------------------------


def anonymize(ed) -> None:
    """Every comment author becomes "Author" (legacy notes); threaded comments
    lose their dates; every person becomes "Author" without the account
    (user ID, provider) they were signed in with. The person IDs stay:
    threaded comments refer to them."""
    from metaex.core.ooxml import ANONYMOUS, anonymize_attributes

    pkg = ed.pkg
    for part in pkg.parts_of_type(COMMENTS_TYPE):
        for author in pkg.xml(part).find_all(S_NS, "author"):
            if author.text() != ANONYMOUS:
                ed.editor(part).set_content(author, ANONYMOUS)
    for part in pkg.parts_of_type(THREADED_TYPE):
        for c in pkg.xml(part).find_all(TC_NS, "threadedComment"):
            anonymize_attributes(ed, part, c, dates=("dT",))
    for part in pkg.parts_of_type(PERSON_TYPE):
        for person in pkg.xml(part).find_all(TC_NS, "person"):
            anonymize_attributes(ed, part, person, names=("displayName",),
                                 remove=("userId", "providerId"))


MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def remove_workbook_history(ed) -> None:
    """Remove from workbook.xml the folder the file was saved in
    (x15ac:absPath, with the mc:AlternateContent around it when that holds
    nothing else), the program and version that last saved it (fileVersion
    appName, lastEdited, lowestEdited, rupBuild) and the co-authoring revision
    pointer (xr:revisionPtr)."""
    pkg = ed.pkg
    main = pkg.main
    editor = ed.editor(main)
    for e in pkg.xml(main).root.iter():
        if e.is_(X15AC_NS, "absPath"):
            choice, wrapper = e.parent, e.parent.parent if e.parent is not None else None
            alone = (choice is not None and choice.is_(MC_NS, "Choice") and wrapper is not None
                     and wrapper.is_(MC_NS, "AlternateContent")
                     and list(wrapper.elements()) == [choice] and list(choice.elements()) == [e])
            editor.remove(wrapper if alone else e)
        elif e.is_(S_NS, "fileVersion"):
            for name in ("appName", "lastEdited", "lowestEdited", "rupBuild"):
                editor.remove_attr(e, name)
        elif e.is_(XR_NS, "revisionPtr"):
            editor.remove(e)


VML_OBJECT_TYPE = re.compile(rb"""ObjectType\s*=\s*["']([A-Za-z]+)["']""")


def remove_comments(ed) -> None:
    """Remove the comments completely: legacy notes with the drawings that
    show them, threaded comments, and the list of persons. A sheet whose
    legacy drawing also holds other objects (form controls) is refused:
    its notes can't be removed without them."""
    from metaex.core.errors import CannotCleanError
    from metaex.core.ooxml import R_NS

    pkg = ed.pkg
    comment_parts = set(pkg.parts_of_type(COMMENTS_TYPE)) | set(pkg.parts_of_type(THREADED_TYPE))
    for source in list(pkg.relationships):
        rels = pkg.rels(source)
        if not any(not r.external and r.part is not None and pkg.entry_name(r.part) in comment_parts
                   for r in rels):
            continue
        for r in rels:
            if r.external or r.part is None or r.type_name != "vmlDrawing":
                continue
            vml = pkg.entry_name(r.part)
            if vml is None:
                continue
            kinds = {k.decode("ascii") for k in VML_OBJECT_TYPE.findall(pkg.read(vml))}
            if kinds - {"Note"}:
                raise CannotCleanError(f"can't remove the comments of {source}: its drawing "
                                       f"{vml} also holds other objects "
                                       f"({', '.join(sorted(kinds - {'Note'}))})")
            for e in pkg.xml(source).find_all(S_NS, "legacyDrawing"):
                if e.get("id", R_NS) == r.id:
                    ed.editor(source).remove(e)
            ed.remove_part(vml)
    for part in sorted(comment_parts):
        ed.remove_part(part)
    for part in pkg.parts_of_type(PERSON_TYPE):
        ed.remove_part(part)


def _strip_xlsx(ed, remove_all_comments: bool = False) -> None:
    from metaex.core.ooxml import strip_custom_xml

    strip_custom_xml(ed)
    if remove_all_comments:
        remove_comments(ed)
    anonymize(ed)
    remove_workbook_history(ed)


CAN_REMOVE_COMMENTS = True


def strip(data: bytes | bytearray | memoryview, *, keep_orientation: bool = True,
          paranoid: bool = False, remove_comments: bool = False) -> bytes:
    """Return a cleaned copy of an XLSX file (see core/ooxml.py for what the
    package-level clean-up does). Comment authors become "Author", unless
    ``remove_comments`` removes the comments completely. Raises
    MalformedFileError for damaged or unsafe files and
    UnsupportedFormatError for Strict Open XML and binary workbooks."""
    from metaex.core.ooxml import strip_package

    return strip_package(data, "xlsx", lambda ed: _strip_xlsx(ed, remove_comments),
                         keep_orientation=keep_orientation, paranoid=paranoid)


def names_in_comment_text(pkg: Package) -> list[str]:
    """Names at the start of legacy comments: Excel begins each note with
    the author's name in bold ("Jane Doe:"), as part of the comment text."""
    found = []
    for part in pkg.parts_of_type(COMMENTS_TYPE):
        for c in pkg.xml(part).find_all(S_NS, "comment"):
            text = c.child(S_NS, "text")
            run = text.child(S_NS, "r") if text is not None else None
            props = run.child(S_NS, "rPr") if run is not None else None
            t = run.child(S_NS, "t") if run is not None else None
            if props is not None and props.child(S_NS, "b") is not None and t is not None:
                value = t.text().strip()
                if value.endswith(":") and value[:-1] and value[:-1] not in found:
                    found.append(value[:-1])
    return found


def kept_warnings(cleaned: bytes) -> list[str]:
    """What a cleaned workbook still contains that the user should know about."""
    from metaex.core.ooxml import open_package

    return warnings_for(open_package(cleaned))


def warnings_for(pkg: Package) -> list[str]:
    """kept_warnings() for an opened package."""
    from metaex.core.ooxml import kept_package_warnings

    out = []
    comments = comments_section(pkg)
    if comments is not None:
        count = sum(f.value for f in comments.fields if f.key.endswith("count"))
        out.append(f'kept {count} comment(s) (their authors are now "Author"); use '
                   "--remove-comments to remove them")
    names = names_in_comment_text(pkg)
    if names:
        out.append(f"comment text starts with the name(s) {', '.join(repr(n) for n in names)} "
                   "(Excel writes the author's name into each note); that text is part of the "
                   "comment and was not changed")
    hidden = hidden_section(pkg)
    if hidden is not None:
        sheets = ", ".join(f.value + (" (very hidden)" if f.label.startswith("Very") else "")
                           for f in hidden.fields)
        out.append(f"kept hidden sheet(s) {sheets}: they are part of the workbook")
    data = external_data_section(pkg)
    connections = [f for f in data.fields if f.key.startswith("connection.")] if data else []
    if connections:
        out.append(f"kept {len(connections)} data connection(s); connection strings can name "
                   "servers, databases and user names")
    return out + kept_package_warnings(pkg)


# -- verify ------------------------------------------------------------------------------

IMAGE_LABEL = "Document content"


def is_kept_by_design(section_key: str, field) -> bool:
    """What may stay in a cleaned workbook (see core/ooxml_verify.py)."""
    from metaex.core.ooxml_verify import kept_by_design

    return kept_by_design(section_key, field)


def _details(pkg: Package) -> tuple[list[tuple[str, str, str]], list[str]]:
    from metaex.core.ooxml import custom_xml_parts

    leftovers = [("Custom XML data", part, "custom XML is removed by strip")
                 for part in custom_xml_parts(pkg)]
    return leftovers, warnings_for(pkg)


def verify_details(cleaned: bytes, original: bytes | None = None) -> dict:
    """XLSX-specific verification (see core/ooxml_verify.py): a valid
    package, nothing strip removes left, and, compared with the original,
    the same content."""
    from metaex.core.ooxml_verify import verify_package

    return verify_package(bytes(cleaned), None if original is None else bytes(original),
                          "xlsx", _details)
