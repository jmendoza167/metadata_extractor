"""PPTX: PowerPoint presentations (also .pptm, .potx, .potm, .ppsx, .ppsm).

The package-level parts (document properties, custom XML, relationships,
the ZIP container) are handled in core/ooxml.py; this module reads what is
specific to PowerPoint: comments (legacy and modern), their authors,
hidden slides and speaker notes.
"""

from __future__ import annotations

from metaex.core.ooxml import Package, add_person, comments_summary
from metaex.core.result import Section

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
P188_NS = "http://schemas.microsoft.com/office/powerpoint/2018/8/main"
MAX_LISTED = 200

CT = "application/vnd.openxmlformats-officedocument.presentationml."
COMMENT_AUTHORS_TYPE = CT + "commentAuthors+xml"
COMMENTS_TYPE = CT + "comments+xml"
MODERN_COMMENTS_TYPE = "application/vnd.ms-powerpoint.comments+xml"
AUTHORS_TYPE = "application/vnd.ms-powerpoint.authors+xml"


def _authors(pkg: Package) -> tuple[dict[str, tuple], dict[str, tuple]]:
    """(legacy authors by id, modern authors by id): (name, initials, user ID, provider)."""
    legacy, modern = {}, {}
    for part in pkg.parts_of_type(COMMENT_AUTHORS_TYPE):
        for a in pkg.xml(part).find_all(P_NS, "cmAuthor"):
            legacy[a.get("id") or ""] = (a.get("name"), a.get("initials"), None, None)
    for part in pkg.parts_of_type(AUTHORS_TYPE):
        for a in pkg.xml(part).find_all(P188_NS, "author"):
            modern[a.get("id") or ""] = (a.get("name"), a.get("initials"), a.get("userId"),
                                         a.get("providerId"))
    return legacy, modern


def comments_section(pkg: Package) -> Section | None:
    """Legacy comments (ppt/comments/comment*.xml) and modern comments
    (ppt/comments/modernComment_*.xml)."""
    legacy_authors, modern_authors = _authors(pkg)
    section = Section("office_comments", "Comments")
    names, initials, dates, count = [], [], [], 0
    for part in pkg.parts_of_type(COMMENTS_TYPE):
        for c in pkg.xml(part).find_all(P_NS, "cm"):
            count += 1
            author = legacy_authors.get(c.get("authorId") or "", (None, None, None, None))
            names.append(author[0] or "")
            initials.append(author[1] or "")
            dates.append(c.get("dt") or "")
    for part in pkg.parts_of_type(MODERN_COMMENTS_TYPE):
        for c in pkg.xml(part).root.iter():
            if c.uri != P188_NS or c.local not in ("cm", "reply"):
                continue
            count += 1
            author = modern_authors.get(c.get("authorId") or "", (None, None, None, None))
            names.append(author[0] or "")
            initials.append(author[1] or "")
            dates.append(c.get("created") or "")
    comments_summary(section, count, names, initials, dates)
    return section if section.fields else None


def people_section(pkg: Package) -> Section | None:
    """Comment authors (ppt/commentAuthors.xml and ppt/authors.xml), with
    the account modern comments were written with."""
    legacy, modern = _authors(pkg)
    section = Section("office_people", "People")
    for n, (name, initials, user_id, provider) in enumerate(
            list(legacy.values()) + list(modern.values()), 1):
        if n > MAX_LISTED:
            break
        add_person(section, n, name, user_id, provider, initials)
    return section if section.fields else None
