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


def anonymize(ed) -> None:
    """Every comment author becomes "Author" without initials, and comments
    lose their dates. Where PowerPoint requires a value (legacy author
    initials; modern authors' user ID and provider; modern comment dates)
    it is set to an empty or neutral one: initials "", user ID "Author",
    provider "None" (what PowerPoint writes for a local account), date
    2000-01-01T00:00:00."""
    from metaex.core.ooxml import PLACEHOLDER_DATE, anonymize_attributes

    pkg = ed.pkg
    for part in pkg.parts_of_type(COMMENT_AUTHORS_TYPE):
        for a in pkg.xml(part).find_all(P_NS, "cmAuthor"):
            anonymize_attributes(ed, part, a, names=("name",), blank=("initials",))
    for part in pkg.parts_of_type(COMMENTS_TYPE):
        for c in pkg.xml(part).find_all(P_NS, "cm"):
            anonymize_attributes(ed, part, c, dates=("dt",))
    for part in pkg.parts_of_type(AUTHORS_TYPE):
        for a in pkg.xml(part).find_all(P188_NS, "author"):
            anonymize_attributes(ed, part, a, names=("name", "userId"), remove=("initials",))
            if a.get("providerId") not in (None, "None"):
                ed.editor(part).set_attr(a, a.attr_name("providerId"), "None")
    for part in pkg.parts_of_type(MODERN_COMMENTS_TYPE):
        for c in pkg.xml(part).root.iter():
            if c.uri == P188_NS and c.local in ("cm", "reply"):
                for name in ("startDate", "dueDate"):
                    anonymize_attributes(ed, part, c, dates=(name,))
                if c.get("created") not in (None, PLACEHOLDER_DATE):
                    ed.editor(part).set_attr(c, c.attr_name("created"), PLACEHOLDER_DATE)


def remove_comments(ed) -> None:
    """Remove the comments completely: legacy and modern comments and both
    lists of comment authors."""
    pkg = ed.pkg
    for content_type in (COMMENTS_TYPE, MODERN_COMMENTS_TYPE, COMMENT_AUTHORS_TYPE,
                         AUTHORS_TYPE):
        for part in pkg.parts_of_type(content_type):
            ed.remove_part(part)


def _strip_pptx(ed, remove_all_comments: bool = False) -> None:
    from metaex.core.ooxml import strip_custom_xml

    strip_custom_xml(ed)
    if remove_all_comments:
        remove_comments(ed)
    anonymize(ed)


CAN_REMOVE_COMMENTS = True


def strip(data: bytes | bytearray | memoryview, *, keep_orientation: bool = True,
          paranoid: bool = False, remove_comments: bool = False) -> bytes:
    """Return a cleaned copy of a PPTX file (see core/ooxml.py for what the
    package-level clean-up does). Comment authors become "Author", unless
    ``remove_comments`` removes the comments completely. Raises
    MalformedFileError for damaged or unsafe files and
    UnsupportedFormatError for Strict Open XML."""
    from metaex.core.ooxml import strip_package

    return strip_package(data, "pptx", lambda ed: _strip_pptx(ed, remove_comments),
                         keep_orientation=keep_orientation, paranoid=paranoid)


def kept_warnings(cleaned: bytes) -> list[str]:
    """What a cleaned presentation still contains that the user should know about."""
    from metaex.core.ooxml import open_package

    return warnings_for(open_package(cleaned))


def warnings_for(pkg: Package) -> list[str]:
    """kept_warnings() for an opened package."""
    from metaex.core.ooxml import kept_package_warnings

    out = []
    comments = comments_section(pkg)
    if comments is not None:
        out.append(f'kept {comments.get("count").value} comment(s) (their authors are now '
                   '"Author"); use --remove-comments to remove them')
    hidden = hidden_section(pkg)
    if hidden is not None:
        slides = [f.value for f in hidden.fields if f.key.startswith("hidden_slide.")]
        if slides:
            out.append(f"kept {len(slides)} hidden slide(s) ({', '.join(slides)}): they are "
                       "part of the presentation")
        notes = hidden.get("notes")
        if notes is not None:
            out.append(f"kept the speaker notes of {notes.value} slide(s); they are visible to "
                       "anyone who opens the file")
    return out + kept_package_warnings(pkg)


# -- verify ------------------------------------------------------------------------------

IMAGE_LABEL = "Document content"


def is_kept_by_design(section_key: str, field) -> bool:
    """What may stay in a cleaned presentation (see core/ooxml_verify.py)."""
    from metaex.core.ooxml_verify import kept_by_design

    return kept_by_design(section_key, field)


def _details(pkg: Package) -> tuple[list[tuple[str, str, str]], list[str]]:
    from metaex.core.ooxml import custom_xml_parts

    leftovers = [("Custom XML data", part, "custom XML is removed by strip")
                 for part in custom_xml_parts(pkg)]
    return leftovers, warnings_for(pkg)


def verify_details(cleaned: bytes, original: bytes | None = None) -> dict:
    """PPTX-specific verification (see core/ooxml_verify.py): a valid
    package, nothing strip removes left, and, compared with the original,
    the same content."""
    from metaex.core.ooxml_verify import verify_package

    return verify_package(bytes(cleaned), None if original is None else bytes(original),
                          "pptx", _details)
