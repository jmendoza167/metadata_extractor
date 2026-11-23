"""DOCX: Word documents (also .docm, .dotx, .dotm).

The package-level parts (document properties, custom XML, relationships,
the ZIP container) are handled in core/ooxml.py; this module reads what is
specific to Word: document variables, comments, people, tracked changes,
revision IDs and hidden text.
"""

from __future__ import annotations

from metaex.core.ooxml import (Package, add_person, comments_summary, date_range, distinct,
                               shorten)
from metaex.core.result import Section

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W15_NS = "http://schemas.microsoft.com/office/word/2012/wordml"
W16CEX_NS = "http://schemas.microsoft.com/office/word/2018/wordml/cex"
MAX_LISTED = 200

CT = "application/vnd.openxmlformats-officedocument.wordprocessingml."
COMMENTS_TYPE = CT + "comments+xml"
COMMENTS_EXTENDED_TYPE = CT + "commentsExtended+xml"
COMMENTS_IDS_TYPE = CT + "commentsIds+xml"
COMMENTS_EXTENSIBLE_TYPE = CT + "commentsExtensible+xml"
PEOPLE_TYPE = CT + "people+xml"
STORY_REL_TYPES = ("header", "footer", "footnotes", "endnotes", "comments", "glossaryDocument")

INSERTIONS = {"ins", "cellIns", "customXmlInsRangeStart"}
DELETIONS = {"del", "cellDel", "customXmlDelRangeStart"}
MOVES = {"moveFrom", "moveTo", "moveFromRangeStart", "moveToRangeStart"}
FORMATTING = {"rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "tblPrExChange",
              "trPrChange", "tcPrChange", "tblGridChange", "numberingChange", "cellMerge"}
REVISIONS = INSERTIONS | DELETIONS | MOVES | FORMATTING
DELETED_TEXT = {"delText", "delInstrText"}
MAX_DELETED_SAMPLE = 200


def settings_part(pkg: Package) -> str | None:
    parts = pkg.related(pkg.main, "settings")
    return pkg.entry_name(parts[0]) if parts else None


def variables_section(pkg: Package) -> Section | None:
    """Document variables (w:docVars in settings.xml): name/value pairs that
    macros and add-ins store, invisible in the document."""
    part = settings_part(pkg)
    if part is None:
        return None
    section = Section("docx_variables", "Document variables")
    found = [e for e in pkg.xml(part).find_all(W_NS, "docVar")]
    for e in found[:MAX_LISTED]:
        name = e.get("name", W_NS) or "(no name)"
        section.add(f"docVar.{name}", name, shorten(e.get("val", W_NS) or ""), sensitive=True)
    if len(found) > MAX_LISTED:
        section.add("more", "More", f"{len(found) - MAX_LISTED} more not listed")
    return section if section.fields else None


# -- comments, people, tracked changes ----------------------------------------------------


def story_parts(pkg: Package) -> list[str]:
    """Parts holding document text: the main document, headers, footers,
    footnotes, endnotes, comments and the glossary (building blocks) with
    its own headers, footers, ..."""
    parts = [pkg.main]
    todo = [pkg.main]
    while todo:
        source = todo.pop()
        for rel_type in STORY_REL_TYPES:
            for part in pkg.related(source, rel_type):
                name = pkg.entry_name(part)
                if name is not None and name not in parts:
                    parts.append(name)
                    if rel_type == "glossaryDocument":
                        todo.append(name)
    return parts


def comments_section(pkg: Package) -> Section | None:
    """Comments (word/comments.xml) and the parts that extend them."""
    section = Section("office_comments", "Comments")
    authors, initials, dates, count = [], [], [], 0
    for part in pkg.parts_of_type(COMMENTS_TYPE):
        for c in pkg.xml(part).find_all(W_NS, "comment"):
            count += 1
            authors.append(c.get("author", W_NS) or "")
            initials.append(c.get("initials", W_NS) or "")
            dates.append(c.get("date", W_NS) or "")
    comments_summary(section, count, authors, initials, dates)
    extensible = [e.get("dateUtc", W16CEX_NS) or ""
                  for part in pkg.parts_of_type(COMMENTS_EXTENSIBLE_TYPE)
                  for e in pkg.xml(part).iter() if e.local == "commentExtensible"]
    if any(extensible):
        section.add("dateUtc", "Comment dates (UTC)", date_range(extensible), sensitive=True)
    return section if section.fields else None


def people_section(pkg: Package) -> Section | None:
    """People who commented or edited (word/people.xml), with the account
    they were signed in with (presenceInfo: user ID and provider)."""
    section = Section("office_people", "People")
    n = 0
    for part in pkg.parts_of_type(PEOPLE_TYPE):
        for person in pkg.xml(part).find_all(W15_NS, "person"):
            n += 1
            if n > MAX_LISTED:
                break
            presence = person.child(W15_NS, "presenceInfo")
            add_person(section, n, person.get("author", W15_NS),
                       presence.get("userId", W15_NS) if presence is not None else None,
                       presence.get("providerId", W15_NS) if presence is not None else None)
    return section if section.fields else None


def tracked_changes_section(pkg: Package) -> Section | None:
    """Tracked changes (revisions) in every story part: how many, by whom and
    when, and how much deleted text is still stored in the file."""
    counts = {"insertions": 0, "deletions": 0, "moves": 0, "formatting": 0}
    authors, dates, deleted = [], [], []
    for part in story_parts(pkg):
        for e in pkg.xml(part).iter():
            if e.uri != W_NS:
                continue
            if e.local in REVISIONS:
                kind = ("insertions" if e.local in INSERTIONS else "deletions"
                        if e.local in DELETIONS else "moves" if e.local in MOVES
                        else "formatting")
                counts[kind] += 1
                authors.append(e.get("author", W_NS) or "")
                dates.append(e.get("date", W_NS) or "")
            elif e.local in DELETED_TEXT:
                deleted.append(e.text())
    if not any(counts.values()) and not deleted:
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
        section.add("deleted_text", "Deleted text", _sample(text), sensitive=True)
    return section


def _sample(text: str) -> str:
    return text if len(text) <= MAX_DELETED_SAMPLE else text[:MAX_DELETED_SAMPLE] + "..."


# -- hidden content and revision IDs -------------------------------------------------------


def _is_on(element) -> bool:
    """A Word on/off property (w:vanish, w:b, ...): on unless w:val says false."""
    return (element.get("val", W_NS) or "true").lower() not in ("false", "0", "off")


def hidden_text(pkg: Package) -> tuple[int, str]:
    """(number of runs, their text) formatted as hidden (w:vanish) in the
    story parts other than the glossary."""
    runs, texts = 0, []
    glossary = set(glossary_parts(pkg))
    for part in story_parts(pkg):
        if part in glossary:
            continue
        for run in pkg.xml(part).find_all(W_NS, "r"):
            props = run.child(W_NS, "rPr")
            vanish = props.child(W_NS, "vanish") if props is not None else None
            if vanish is not None and _is_on(vanish):
                runs += 1
                texts.append("".join(t.text() for t in run.elements()
                                     if t.uri == W_NS and t.local in ("t", "delText")))
    return runs, "".join(texts)


def glossary_parts(pkg: Package) -> list[str]:
    """The glossary document (building blocks, AutoText) and its stories."""
    found = [pkg.entry_name(p) for p in pkg.related(pkg.main, "glossaryDocument")]
    out = [n for n in found if n is not None]
    for name in list(out):
        for rel_type in STORY_REL_TYPES:
            out += [n for n in (pkg.entry_name(p) for p in pkg.related(name, rel_type))
                    if n is not None and n not in out]
    return out


def hidden_section(pkg: Package) -> Section | None:
    """Text formatted as hidden, and the glossary document (building blocks
    that can hold text from earlier documents)."""
    section = Section("hidden_content", "Hidden content")
    runs, text = hidden_text(pkg)
    if runs:
        section.add("hidden_text_runs", "Hidden text (runs)", runs, sensitive=True)
        if text:
            section.add("hidden_text", "Hidden text", _sample(text), sensitive=True)
    glossary = glossary_parts(pkg)
    if glossary:
        paragraphs = len(pkg.xml(glossary[0]).find_all(W_NS, "p"))
        sample = _sample(" ".join(pkg.xml(glossary[0]).root.text().split()))
        section.add("glossary", "Glossary document (building blocks)",
                    f"{glossary[0]}, {paragraphs} paragraphs" + (f": {sample}" if sample else ""),
                    sensitive=True)
    return section if section.fields else None


def rsid_parts(pkg: Package) -> list[str]:
    """XML parts in which Word writes revision IDs."""
    parts = story_parts(pkg)
    for rel_type in ("styles", "numbering", "settings", "stylesWithEffects"):
        parts += [n for n in (pkg.entry_name(p) for p in pkg.related(pkg.main, rel_type))
                  if n is not None and n not in parts]
    return parts


def is_rsid_attribute(element, name: str) -> bool:
    return name.split(":", 1)[-1].startswith("rsid") and element.attr_uri(name) == W_NS


def revision_ids_section(pkg: Package) -> Section | None:
    """Revision save IDs (rsids): Word marks every edit with the ID of the
    editing session, which links documents edited on the same computer."""
    session_ids = 0
    part = settings_part(pkg)
    if part is not None:
        session_ids = sum(1 for rsids in pkg.xml(part).find_all(W_NS, "rsids")
                          for e in rsids.elements() if e.uri == W_NS)
    attributes = elements = 0
    for name in rsid_parts(pkg):
        for e in pkg.xml(name).iter():
            attributes += sum(1 for a, _v in e.attrs if is_rsid_attribute(e, a))
            if e.is_(W_NS, "rsid") and not (e.parent is not None and e.parent.is_(W_NS, "rsids")):
                elements += 1
    if not session_ids and not attributes and not elements:
        return None
    section = Section("revision_ids", "Revision IDs (rsids)")
    if session_ids:
        section.add("rsids", "Editing sessions listed in settings.xml", session_ids)
    if attributes:
        section.add("rsid_attributes", "Revision ID attributes", attributes)
    if elements:
        section.add("rsid_elements", "Revision IDs of styles", elements)
    return section


BUILDERS = [variables_section, comments_section, people_section, tracked_changes_section,
            hidden_section, revision_ids_section]
"""What this module reports, in order (between the package-level sections)."""


# -- parse -------------------------------------------------------------------------------


def parse(data: bytes | bytearray | memoryview) -> dict:
    """Parse a DOCX file and return its warnings, report sections and
    structure. Raises MalformedFileError if it can't be opened at all,
    UnsupportedFormatError for Strict Open XML."""
    from metaex.core.ooxml import parse_package

    parsed = parse_package(data, "docx", BUILDERS)
    parsed.pop("package")
    return parsed


# -- strip -------------------------------------------------------------------------------

STORE_CORE_PROPERTIES = "{6C3C8BC8-F283-45AE-878A-BAB7291924A1}"
STORE_EXTENDED_PROPERTIES = "{6668398D-A668-4E3E-A5EB-62B293D839F1}"
STORE_COVER_PAGE = "{55AF091B-3C7A-41E3-B477-F2FDAA23CFDA}"
PROPERTY_STORES = {STORE_CORE_PROPERTIES, STORE_EXTENDED_PROPERTIES, STORE_COVER_PAGE}


def data_bindings(pkg: Package) -> list[str]:
    """The store item IDs (upper case) content controls take their text from."""
    out = []
    for part in story_parts(pkg):
        for e in pkg.xml(part).iter():
            if e.local == "dataBinding" and e.uri in (W_NS, W15_NS):
                item = e.get("storeItemID", e.uri)
                if item:
                    out.append(item.upper())
    return out


def bound_custom_xml(pkg: Package) -> set[str]:
    """Custom XML parts whose data content controls show."""
    from metaex.core.ooxml import custom_xml_item_id, custom_xml_parts

    ids = set(data_bindings(pkg))
    return {item for item in custom_xml_parts(pkg)
            if (custom_xml_item_id(pkg, item) or "").upper() in ids}


def revision_parts(pkg: Package) -> list[str]:
    """Parts in which Word stores tracked changes (stories, styles, numbering)."""
    return rsid_parts(pkg)


def anonymize(ed) -> None:
    """Every comment and revision author becomes "Author"; comment initials
    and the dates of comments and revisions are removed; people.xml keeps a
    single person, "Author", without the account it was signed in with."""
    from metaex.core.ooxml import anonymize_attributes

    pkg = ed.pkg
    for part in pkg.parts_of_type(COMMENTS_TYPE):
        for c in pkg.xml(part).find_all(W_NS, "comment"):
            anonymize_attributes(ed, part, c, names=("author",), dates=("date",),
                                 remove=("initials",), uri=W_NS)
    for part in revision_parts(pkg):
        for e in pkg.xml(part).iter():
            if e.uri == W_NS and e.local in REVISIONS:
                anonymize_attributes(ed, part, e, names=("author",), dates=("date",), uri=W_NS)
    for part in pkg.parts_of_type(COMMENTS_EXTENSIBLE_TYPE):
        for e in pkg.xml(part).iter():
            if e.local == "commentExtensible" and e.uri == W16CEX_NS:
                anonymize_attributes(ed, part, e, dates=("dateUtc",), uri=W16CEX_NS)
    for part in pkg.parts_of_type(PEOPLE_TYPE):
        people = pkg.xml(part).find_all(W15_NS, "person")
        editor = ed.editor(part)
        for n, person in enumerate(people):
            if n > 0:
                editor.remove(person)  # they would all be "Author": one is enough
                continue
            anonymize_attributes(ed, part, person, names=("author",), uri=W15_NS)
            for child in person.elements():
                editor.remove(child)  # presenceInfo: user ID and account provider


def remove_revision_ids(ed) -> None:
    """Remove every rsid attribute (w:rsidR, w:rsidRPr, w:rsidDel, ...), the
    w:rsid elements of styles and the list of editing sessions (w:rsids)
    in settings.xml."""
    pkg = ed.pkg
    for part in rsid_parts(pkg):
        for e in pkg.xml(part).iter():
            if e.uri == W_NS and e.local in ("rsids", "rsid"):
                ed.editor(part).remove(e)
                continue
            for name, _value in e.attrs:
                if is_rsid_attribute(e, name):
                    ed.editor(part).remove_attr(e, name)


def remove_settings_paths(ed) -> None:
    """Remove the attached template (w:attachedTemplate and its link, usually
    a path into the user's own folder) and the document variables
    (w:docVars) from settings.xml."""
    pkg = ed.pkg
    part = settings_part(pkg)
    if part is None:
        return
    root = pkg.xml(part).root
    for e in root.elements():
        if e.is_(W_NS, "docVars"):
            ed.editor(part).remove(e)
        elif e.is_(W_NS, "attachedTemplate"):
            ed.editor(part).remove(e)
            rid = e.get("id", "http://schemas.openxmlformats.org/officeDocument/2006/"
                              "relationships")
            for r in pkg.rels(part):
                if r.id == rid:
                    ed.remove_relationship(r)


COMMENT_PART_TYPES = (COMMENTS_TYPE, COMMENTS_EXTENDED_TYPE, COMMENTS_IDS_TYPE,
                      COMMENTS_EXTENSIBLE_TYPE)
COMMENT_ANCHORS = ("commentRangeStart", "commentRangeEnd")


def remove_comments(ed) -> None:
    """Remove the comments completely: the comment parts, their anchors in
    the text (comment ranges, and the runs that only hold a comment mark)
    and people.xml if no tracked change is left to refer to it."""
    pkg = ed.pkg
    comment_parts = set()
    for content_type in COMMENT_PART_TYPES:
        for part in pkg.parts_of_type(content_type):
            comment_parts.add(part)
            ed.remove_part(part)
    revisions = False
    for part in story_parts(pkg):
        if part in comment_parts:
            continue
        for e in pkg.xml(part).iter():
            if e.uri != W_NS:
                continue
            if e.local in COMMENT_ANCHORS:
                ed.editor(part).remove(e)
            elif e.local == "commentReference":
                run = e.parent
                rest = [c for c in run.elements() if c is not e and not c.is_(W_NS, "rPr")]
                ed.editor(part).remove(run if run.is_(W_NS, "r") and not rest else e)
            elif e.local in REVISIONS:
                revisions = True
    if not revisions:
        for part in pkg.parts_of_type(PEOPLE_TYPE):
            ed.remove_part(part)


def _strip_word(ed, remove_all_comments: bool = False) -> None:
    from metaex.core.ooxml import strip_custom_xml

    strip_custom_xml(ed, keep=bound_custom_xml(ed.pkg))
    if remove_all_comments:
        remove_comments(ed)
    anonymize(ed)
    remove_revision_ids(ed)
    remove_settings_paths(ed)


CAN_REMOVE_COMMENTS = True


def strip(data: bytes | bytearray | memoryview, *, keep_orientation: bool = True,
          paranoid: bool = False, remove_comments: bool = False) -> bytes:
    """Return a cleaned copy of a DOCX file (see core/ooxml.py for what the
    package-level clean-up does). Comment authors become "Author", unless
    ``remove_comments`` removes the comments completely. Raises
    MalformedFileError for damaged or unsafe files and
    UnsupportedFormatError for Strict Open XML."""
    from metaex.core.ooxml import strip_package

    return strip_package(data, "docx", lambda ed: _strip_word(ed, remove_comments),
                         keep_orientation=keep_orientation, paranoid=paranoid)


TRACKED_CHANGES_ADVICE = ("Accept or reject all changes in Word (Review > Accept > Accept All "
                          "Changes) or LibreOffice (Edit > Track Changes > Accept All), save, "
                          "and run metaex again")


def kept_warnings(cleaned: bytes) -> list[str]:
    """What a cleaned document still contains that the user should know about."""
    from metaex.core.ooxml import open_package

    return warnings_for(open_package(cleaned))


def warnings_for(pkg: Package) -> list[str]:
    """kept_warnings() for an opened package."""
    from metaex.core.ooxml import kept_package_warnings

    out = []
    changes = tracked_changes_section(pkg)
    if changes is not None:
        deleted = changes.get("deleted_characters")
        if deleted is not None:
            out.append(f"CRITICAL: the document still contains tracked changes, and "
                       f"{deleted.value} characters of DELETED TEXT are still stored in the file, "
                       f"where anyone can read them. {TRACKED_CHANGES_ADVICE}.")
        else:
            out.append(f"the document still contains tracked changes (their authors are now "
                       f'"Author"). {TRACKED_CHANGES_ADVICE} to remove them.')
    comments = comments_section(pkg)
    if comments is not None:
        out.append(f'kept {comments.get("count").value} comment(s) (their authors are now '
                   '"Author"); use --remove-comments to remove them')
    runs, text = hidden_text(pkg)
    if runs:
        sample = " ".join(text.split())[:60]
        out.append(f"kept {runs} run(s) of hidden text (e.g. {sample!r}): it is part of the "
                   "document; Word shows it with Home > Show/Hide")
    if glossary_parts(pkg):
        out.append("kept the glossary document (building blocks and AutoText), which can hold "
                   "text copied from other documents")
    out += kept_package_warnings(pkg)
    bound = sorted(bound_custom_xml(pkg))
    if bound:
        out.append(f"kept {', '.join(bound)}: content controls show its data, so removing it "
                   "would change the document; it may hold names or other details")
    shown = sum(1 for item in data_bindings(pkg) if item in PROPERTY_STORES)
    if shown:
        out.append(f"{shown} content control(s) show document properties (such as the "
                   "author's name or the company); the text they show is part of the "
                   "document and was not changed")
    return out


# -- verify ------------------------------------------------------------------------------

IMAGE_LABEL = "Document content"


def is_kept_by_design(section_key: str, field) -> bool:
    """What may stay in a cleaned document (see core/ooxml_verify.py)."""
    from metaex.core.ooxml_verify import kept_by_design

    return kept_by_design(section_key, field)


def _details(pkg: Package) -> tuple[list[tuple[str, str, str]], list[str]]:
    from metaex.core.ooxml import custom_xml_parts

    bound = bound_custom_xml(pkg)
    leftovers = [("Custom XML data", part, "not shown by any content control")
                 for part in custom_xml_parts(pkg) if part not in bound]
    return leftovers, warnings_for(pkg)


def verify_details(cleaned: bytes, original: bytes | None = None) -> dict:
    """DOCX-specific verification (see core/ooxml_verify.py): a valid
    package, nothing strip removes left, and, compared with the original,
    the same content."""
    from metaex.core.ooxml_verify import verify_package

    return verify_package(bytes(cleaned), None if original is None else bytes(original),
                          "docx", _details)
