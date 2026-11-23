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


# -- hidden slides and speaker notes ------------------------------------------------------

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
MAX_SAMPLE = 200


def slide_parts(pkg: Package) -> list[str]:
    return [n for n in (pkg.entry_name(p) for p in pkg.related(pkg.main, "slide"))
            if n is not None]


def notes_parts(pkg: Package) -> list[str]:
    out = []
    for slide in slide_parts(pkg):
        out += [n for n in (pkg.entry_name(p) for p in pkg.related(slide, "notesSlide"))
                if n is not None and n not in out]
    return out


def notes_text(pkg: Package, part: str) -> str:
    """The text a speaker wrote in a notes page (without the slide image
    placeholder and slide number)."""
    texts = []
    for shape in pkg.xml(part).find_all(P_NS, "sp"):
        placeholder = next((e for e in shape.iter() if e.is_(P_NS, "ph")), None)
        if placeholder is not None and placeholder.get("type") in ("sldImg", "sldNum"):
            continue
        texts.append(" ".join(t.text() for t in shape.find_all(A_NS, "t")))
    return " ".join(" ".join(texts).split())


def hidden_section(pkg: Package) -> Section | None:
    """Slides hidden from the slide show, and speaker notes (visible to
    anyone who opens the file, but easy to forget)."""
    section = Section("hidden_content", "Hidden content")
    for n, slide in enumerate(slide_parts(pkg), 1):
        if (pkg.xml(slide).root.get("show") or "1").lower() in ("0", "false"):
            section.add(f"hidden_slide.{slide}", "Hidden slide", f"slide {n} ({slide})",
                        sensitive=True)
    notes = [(part, notes_text(pkg, part)) for part in notes_parts(pkg)]
    notes = [(part, text) for part, text in notes if text]
    if notes:
        section.add("notes", "Slides with speaker notes", len(notes), sensitive=True)
        sample = " | ".join(text for _part, text in notes)
        section.add("notes_text", "Speaker notes",
                    sample if len(sample) <= MAX_SAMPLE else sample[:MAX_SAMPLE] + "...",
                    sensitive=True)
    return section if section.fields else None


BUILDERS = [comments_section, people_section, hidden_section]
"""What this module reports, in order (between the package-level sections)."""


# -- parse -------------------------------------------------------------------------------


def parse(data: bytes | bytearray | memoryview) -> dict:
    """Parse a PPTX file and return its warnings, report sections and
    structure. Raises MalformedFileError if it can't be opened at all,
    UnsupportedFormatError for Strict Open XML."""
    from metaex.core.ooxml import parse_package

    parsed = parse_package(data, "pptx", BUILDERS)
    parsed.pop("package")
    return parsed


# -- strip -------------------------------------------------------------------------------


def _strip_pptx(ed) -> None:
    from metaex.core.ooxml import strip_custom_xml

    strip_custom_xml(ed)


def strip(data: bytes | bytearray | memoryview, *, keep_orientation: bool = True,
          paranoid: bool = False) -> bytes:
    """Return a cleaned copy of a PPTX file (see core/ooxml.py for what the
    package-level clean-up does). Raises MalformedFileError for damaged or
    unsafe files and UnsupportedFormatError for Strict Open XML."""
    from metaex.core.ooxml import strip_package

    return strip_package(data, "pptx", _strip_pptx)


def kept_warnings(cleaned: bytes) -> list[str]:
    """What a cleaned presentation still contains that the user should know about."""
    return []
