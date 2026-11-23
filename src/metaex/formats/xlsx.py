"""XLSX: Excel workbooks (also .xlsm, .xltx, .xltm).

The package-level parts (document properties, custom XML, relationships,
the ZIP container) are handled in core/ooxml.py; this module reads what is
specific to Excel: comments (legacy notes and threaded comments), the
people who wrote them, external links, hidden sheets and the file's
history pointers.
"""

from __future__ import annotations

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
