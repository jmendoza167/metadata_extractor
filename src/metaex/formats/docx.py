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
        sample = text if len(text) <= MAX_DELETED_SAMPLE else text[:MAX_DELETED_SAMPLE] + "..."
        section.add("deleted_text", "Deleted text", sample, sensitive=True)
    return section
