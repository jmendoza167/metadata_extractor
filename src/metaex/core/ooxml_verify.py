"""Verification of cleaned Office Open XML files (DOCX, XLSX, PPTX).

verify_package() checks a cleaned package independently of how it was
cleaned:

- it is a valid package: a ZIP the safe reader accepts, every internal
  relationship leads to a part, every part has a content type and every
  content type override names a part that exists;
- nothing is left that strip removes (this is decided by kept_by_design()
  for the fields parse() reports, and checked here for custom XML);
- compared with the original, no part was added, only parts strip may
  remove were removed, and every other part has the same content: the
  same bytes, or, for XML parts strip edits, the same canonical form, which
  ignores exactly what strip is allowed to change (authors, dates, revision
  IDs, paths, comment anchors, ...); images are compared by their image
  data, like a cleaned image file.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from metaex.core import ooxml
from metaex.core.errors import MalformedFileError, UnsupportedFormatError
from metaex.core.ooxml import (ANONYMOUS, CONTENT_TYPES, PLACEHOLDER_DATE, Package, R_NS,
                               open_package, source_of_rels)
from metaex.core.result import Field
from metaex.core.xmldoc import XmlComment, XmlElement, XmlPI, XmlText, parse_xml

IMAGE_LABEL = "Document content"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
W16CEX = "http://schemas.microsoft.com/office/word/2018/wordml/cex"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
TC = "http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
P188 = "http://schemas.microsoft.com/office/powerpoint/2018/8/main"
X15AC = "http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac"
XR = "http://schemas.microsoft.com/office/spreadsheetml/2014/revision"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"

# -- what may stay in a cleaned file ----------------------------------------------------------

KEPT_SECTIONS = {"hidden_content", "office_external", "xlsx_external_data", "office_embedded",
                 "macros", "embedded_images", "office_custom_xml"}
"""Kept by design: content (hidden sheets, notes, links, objects, macros, the
list of images) and custom XML shown by content controls (checked
separately: see verify_package)."""
COUNTS = ("count", "insertions", "deletions", "moves", "formatting")


def _image_kept() -> dict[str, set[str] | None]:
    import importlib

    kept: dict[str, set[str] | None] = {}
    for name in ooxml.IMAGE_KEYS:
        module = importlib.import_module(f"metaex.formats.{name}")
        for key, fields in getattr(module, "KEPT_BY_DESIGN", {}).items():
            if fields is None or kept.get(key, set()) is None:
                kept[key] = None
            else:
                kept[key] = kept.get(key, set()) | fields
    return kept


IMAGE_KEPT = _image_kept()


def kept_by_design(section_key: str, field: Field) -> bool:
    """True if ``field`` of ``section_key`` may stay in a cleaned Office file."""
    key = field.key
    last = key.rsplit(".", 1)[-1]
    if section_key in KEPT_SECTIONS:
        return True
    if section_key in ("office_comments", "tracked_changes"):
        if last in COUNTS:
            return True
        if last == "authors":
            return field.value == [ANONYMOUS]
        if last == "dates":
            # the placeholder written where a date is required (PowerPoint
            # comments, OpenDocument tracked changes), once or many times
            value = str(field.value)
            return value.startswith(PLACEHOLDER_DATE[:19]) and " to " not in value
        # deleted text is content: strip keeps it, with a loud warning
        return last in ("deleted_characters", "deleted_text")
    if section_key == "office_people":
        if last == "name":
            return field.value == ANONYMOUS
        return (last, field.value) in (("userId", ANONYMOUS), ("providerId", "None"))
    if section_key in IMAGE_KEPT:
        allowed = IMAGE_KEPT[section_key]
        return allowed is None or key in allowed
    return False


# -- canonical form of XML parts -------------------------------------------------------------

DROPPED_ELEMENTS = {
    (W, "rsids"), (W, "rsid"), (W, "docVars"), (W, "attachedTemplate"),
    (W, "commentRangeStart"), (W, "commentRangeEnd"), (W, "commentReference"),
    (W15, "presenceInfo"), (X15AC, "absPath"), (XR, "revisionPtr"), (S, "legacyDrawing"),
}
REVISION_ELEMENTS = {"ins", "del", "cellIns", "cellDel", "customXmlInsRangeStart",
                     "customXmlDelRangeStart", "moveFrom", "moveTo", "moveFromRangeStart",
                     "moveToRangeStart", "rPrChange", "pPrChange", "sectPrChange", "tblPrChange",
                     "tblPrExChange", "trPrChange", "tcPrChange", "tblGridChange",
                     "numberingChange", "cellMerge", "comment"}
DROPPED_ATTRIBUTES = {
    (W16CEX, "commentExtensible"): {(W16CEX, "dateUtc")},
    (TC, "threadedComment"): {(None, "dT")},
    (P, "cm"): {(None, "dt")},
    (P, "cmAuthor"): {(None, "name"), (None, "initials")},
    (P188, "cm"): {(None, "created"), (None, "startDate"), (None, "dueDate")},
    (P188, "reply"): {(None, "created"), (None, "startDate"), (None, "dueDate")},
    (P188, "author"): {(None, "name"), (None, "initials"), (None, "userId"),
                       (None, "providerId")},
    (TC, "person"): {(None, "displayName"), (None, "userId"), (None, "providerId")},
    (W15, "person"): {(W15, "author")},
    (S, "fileVersion"): {(None, "appName"), (None, "lastEdited"), (None, "lowestEdited"),
                         (None, "rupBuild")},
    (S, "pageSetup"): {(R_NS, "id")},
    (S, "chartsheetPageSetup"): {(R_NS, "id")},
}
IGNORED_TEXT = {(S, "author")}
"""Elements whose text strip may change (the author list of Excel notes)."""
IGNORED_PARTS = (ooxml.CORE_TYPE, ooxml.APP_TYPE,
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.people+xml")
"""Parts whose whole content is metadata (emptied or anonymized)."""


def _dropped_attribute(element: XmlElement, name: str) -> bool:
    uri = element.attr_uri(name)
    local = name.split(":", 1)[-1]
    if uri == W and local.startswith("rsid"):
        return True
    if element.uri == W and element.local in REVISION_ELEMENTS and uri == W and \
            local in ("author", "date", "initials"):
        return True
    return (uri, local) in DROPPED_ATTRIBUTES.get((element.uri, element.local), ())


def _dropped(element: XmlElement) -> bool:
    if (element.uri, element.local) in DROPPED_ELEMENTS:
        return True
    if element.uri == W15 and element.local == "person" and element.parent is not None:
        # people.xml keeps one person: all would be "Author"
        return any(c.is_(W15, "person") for c in _before(element))
    return False


def _before(element: XmlElement):
    for c in element.parent.children:
        if c is element:
            return
        if isinstance(c, XmlElement):
            yield c


def _ignored_text(element: XmlElement) -> bool:
    return (element.uri, element.local) in IGNORED_TEXT


def _droppable_when_empty(element: XmlElement, kept_children: int) -> bool:
    """A run left with nothing but its properties (it only held a comment
    mark), or a compatibility wrapper left empty."""
    if element.is_(W, "r"):
        return kept_children <= 1 and all(
            not isinstance(c, XmlElement) or c.is_(W, "rPr") or _dropped(c)
            for c in element.children) and not element.own_text().strip()
    return element.uri == MC and element.local in ("AlternateContent", "Choice")         and kept_children == 0


@dataclass(frozen=True)
class Rules:
    """What a canonical form leaves out (what strip may change)."""
    dropped: Callable[[XmlElement], bool]
    dropped_attribute: Callable[[XmlElement, str], bool]
    ignored_text: Callable[[XmlElement], bool]
    droppable_when_empty: Callable[[XmlElement, int], bool]


OOXML_RULES = Rules(_dropped, _dropped_attribute, _ignored_text, _droppable_when_empty)


def canonical(data: bytes, what: str, rules: Rules = OOXML_RULES) -> bytes:
    """A form of an XML part that is the same for an original and its cleaned
    copy: elements and attributes by namespace URI (not prefix), attributes
    sorted, namespace declarations left out, and without what strip may
    change (``rules``). Text on both sides of a left-out element is joined,
    as it is once the element is removed. Whitespace between elements is
    left out; other text is kept exactly."""
    doc = parse_xml(data, what)
    out: list[str] = []

    def emit(element: XmlElement) -> bool:
        """Write the element; False if it turned out empty and droppable."""
        attrs = sorted((element.attr_uri(n) or "", n.split(":", 1)[-1], v)
                       for n, v in element.attrs
                       if not n.startswith("xmlns") and not rules.dropped_attribute(element, n))
        start = len(out)
        out.append("<" + repr((element.uri or "", element.local, attrs)))
        kept_children = 0
        text = ""
        has_elements = any(isinstance(c, XmlElement) for c in element.children)
        ignore_text = rules.ignored_text(element)

        def flush() -> None:
            nonlocal text
            if text and not (has_elements and text.isspace()) and not ignore_text:
                out.append("T" + repr(text))
            text = ""

        for child in element.children:
            if isinstance(child, XmlText):
                text += child.value
            elif isinstance(child, XmlElement):
                if rules.dropped(child):
                    continue  # the text around it joins up, as in the cleaned copy
                flush()
                if emit(child):
                    kept_children += 1
            elif isinstance(child, (XmlComment, XmlPI)):
                flush()
                out.append("C" + repr(child.value if isinstance(child, XmlComment)
                                      else (child.target, child.data)))
        flush()
        out.append(">")
        if rules.droppable_when_empty(element, kept_children):
            del out[start:]
            return False
        return True

    emit(doc.root)
    return "".join(out).encode("utf-8")


# -- comparing parts --------------------------------------------------------------------------


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _image_digest(data: bytes) -> str | None:
    """Digest of an image's image data (what a cleaned image must keep)."""
    from metaex.core import filetype
    from metaex.core.registry import format_module

    detected = filetype.detect(data)
    if detected.key not in ooxml.IMAGE_KEYS:
        return None
    try:
        if detected.key == "svg":
            from metaex.formats import svg

            return "svg:" + _sha(svg.drawing(svg.parse_document(data)))
        return f"{detected.key}:" + _sha(format_module(detected).image_data(data))
    except MalformedFileError:
        return None


def _is_xml(pkg: Package, name: str) -> bool:
    return name.lower().endswith(ooxml.XML_SUFFIXES) or (pkg.type_of(name) or "").endswith("xml")


def _pair_digest(original: Package, cleaned: Package, name: str) -> tuple[str, str]:
    a, b = original.read(name), cleaned.read(name)
    if a == b:
        return _sha(a), _sha(b)
    if _is_xml(original, name):
        if original.type_of(name) in IGNORED_PARTS:
            ra, rb = original.xml(name).root, cleaned.xml(name).root
            return _sha(repr((ra.uri, ra.local)).encode()), _sha(repr((rb.uri, rb.local)).encode())
        return _sha(canonical(a, name)), _sha(canonical(b, name))
    da, db = _image_digest(a), _image_digest(b)
    if da is not None and db is not None:
        return da, db
    return _sha(a), _sha(b)


def removable_parts(pkg: Package) -> set[str]:
    """Parts of an original that strip may remove."""
    from metaex.core.ooxml import (custom_part, custom_xml_parts, custom_xml_props_part,
                                   label_info_part, printer_settings_parts, thumbnail_parts)

    out = set(pkg.unreferenced()) | set(thumbnail_parts(pkg)) | set(printer_settings_parts(pkg))
    out |= {p for p in (custom_part(pkg), label_info_part(pkg)) if p is not None}
    for item in custom_xml_parts(pkg):
        out.add(item)
        props = custom_xml_props_part(pkg, item)
        if props is not None:
            out.add(props)
    comment_types = ("comments+xml", "commentsExtended+xml", "commentsIds+xml",
                     "commentsExtensible+xml", "people+xml", "threadedcomments+xml",
                     "person+xml", "commentAuthors+xml", "authors+xml", "vmlDrawing")
    out |= {n for n in pkg.part_names()
            if (pkg.type_of(n) or "").endswith(comment_types)}
    return out


def _rel_set(pkg: Package, source: str, removed: set[str]) -> set[tuple]:
    out = set()
    for r in pkg.rels(source):
        if r.type_name == "attachedTemplate":
            continue
        if not r.external and r.part is not None and pkg.entry_name(r.part) in removed:
            continue
        if not r.external and r.part is not None and pkg.entry_name(r.part) is None and \
                r.part.lower() in {x.lower() for x in removed}:
            continue
        out.add((r.id, r.type, r.target, r.external))
    return out


# -- verification ------------------------------------------------------------------------------


def structure_problems(pkg: Package) -> list[str]:
    """Relationships that lead nowhere, parts without a content type,
    content type overrides for parts that don't exist."""
    problems = []
    for r in pkg.all_relationships():
        if not r.external and (r.part is None or not pkg.has(r.part)):
            problems.append(f"relationship {r.id} of {r.source or 'the package'} points to a "
                            f"missing part ({r.target})")
    for name in pkg.part_names():
        if name != pkg.entry_name(CONTENT_TYPES) and pkg.type_of(name) is None:
            problems.append(f"part {name} has no content type")
    for name in pkg.content_types.overrides:
        if not pkg.has(name):
            problems.append(f"[Content_Types].xml names a part that doesn't exist (/{name})")
    return problems


def verify_package(cleaned: bytes, original: bytes | None, kind: str,
                   details_hook=None) -> dict:
    """See the module docstring. ``details_hook(pkg)`` returns extra
    (leftovers, notes) for the format."""
    out: dict = {"leftovers": [], "problems": [], "notes": [], "image_sha256": None,
                 "original_image_sha256": None, "image_data_match": None,
                 "image_label": IMAGE_LABEL}
    try:
        pkg = open_package(cleaned)
        if pkg.kind != kind:
            raise MalformedFileError(f"its main document is a {pkg.description}")
        ooxml.check_xml_parts(pkg)
    except (MalformedFileError, UnsupportedFormatError) as exc:
        out["problems"].append(f"not an Office file metaex accepts: {exc}")
        return out
    out["problems"] += structure_problems(pkg)
    if details_hook is not None:
        leftovers, notes = details_hook(pkg)
        out["leftovers"] += leftovers
        out["notes"] += notes

    names = [n for n in pkg.part_names() if source_of_rels(n) is None and n != CONTENT_TYPES]
    if original is None:
        out["image_sha256"] = _sha("".join(f"{n}:{_sha(pkg.read(n))}\n"
                                           for n in sorted(names)).encode())
        return out
    try:
        before = open_package(original)
    except (MalformedFileError, UnsupportedFormatError) as exc:
        out["problems"].append(f"original is not an Office file metaex accepts ({exc}); the "
                               "content was not compared")
        return out

    original_names = [n for n in before.part_names()
                      if source_of_rels(n) is None and n != CONTENT_TYPES]
    removable = removable_parts(before)
    removed = set(original_names) - set(names)
    for name in sorted(removed - removable):
        out["problems"].append(f"part {name} of the original is missing")
    for name in sorted(set(names) - set(original_names)):
        out["problems"].append(f"part {name} is not in the original")
    mine, theirs = [], []
    try:
        for name in sorted(set(names) & set(original_names)):
            a, b = _pair_digest(before, pkg, name)
            theirs.append(f"{name}:{a}\n")
            mine.append(f"{name}:{b}\n")
            if a != b:
                out["problems"].append(f"the content of {name} differs from the original")
        for source in sorted({""} | (set(names) & set(original_names))):
            if _rel_set(before, source, removed) != _rel_set(pkg, source, removed):
                out["problems"].append(f"the relationships of {source or 'the package'} differ "
                                       "from the original")
    except MalformedFileError as exc:
        out["problems"].append(f"the content could not be compared: {exc}")
        return out
    out["image_sha256"] = _sha("".join(mine).encode())
    out["original_image_sha256"] = _sha("".join(theirs).encode())
    out["image_data_match"] = (out["image_sha256"] == out["original_image_sha256"]
                               and not out["problems"])
    return out
