"""Verification of cleaned OpenDocument files (ODT, ODS, ODP).

Like core/ooxml_verify.py: the cleaned file must be a valid OpenDocument
package ("mimetype" first and stored, every listed file present, every file
listed), nothing strip removes may be left (decided per field by
kept_by_design()), and, compared with the original, only files strip may
remove are missing, none are added, and every other file has the same
content: the same bytes, or the same canonical XML (which leaves out what
strip may change: comment authors and dates, tracked change authors and
dates, revision IDs, the printer and rsid settings, and meta.xml as a
whole), or the same image data for pictures.
"""

from __future__ import annotations

from metaex.core import odf
from metaex.core.errors import MalformedFileError, UnsupportedFormatError
from metaex.core.odf import (ANNOTATION_NAMESPACES, CONFIG_NS, DC_NS, MANIFEST, MIMETYPE,
                             OFFICE_NS, STRIPPED_SETTINGS, OdfPackage, is_rsid_attribute,
                             open_odf)
from metaex.core.ooxml_verify import IMAGE_LABEL, Rules, _image_digest, _sha, canonical
from metaex.core.ooxml_verify import kept_by_design as _ooxml_kept
from metaex.core.result import Field
from metaex.core.xmldoc import XmlElement

KEPT_SECTIONS = {"odf_settings"}
"""Mail merge data source names: kept (with a warning)."""


def kept_by_design(section_key: str, field: Field) -> bool:
    return section_key in KEPT_SECTIONS or _ooxml_kept(section_key, field)


def _dropped(element: XmlElement) -> bool:
    if element.local in ("annotation", "annotation-end") and element.uri in ANNOTATION_NAMESPACES:
        return True
    return (element.is_(CONFIG_NS, "config-item")
            and element.get("name", CONFIG_NS) in STRIPPED_SETTINGS)


def _ignored_text(element: XmlElement) -> bool:
    parent = element.parent
    return (element.uri == DC_NS and element.local in ("creator", "date")
            and parent is not None and parent.is_(OFFICE_NS, "change-info"))


ODF_RULES = Rules(_dropped, is_rsid_attribute, _ignored_text, lambda _e, _n: False)


def removable(pkg: OdfPackage) -> set[str]:
    """Files of an original that strip may remove."""
    return (set(odf.thumbnail_names(pkg)) | set(odf.config_names(pkg))
            | set(odf.signature_names(pkg)) | set(odf.unlisted_names(pkg)))


def _is_xml(pkg: OdfPackage, name: str) -> bool:
    return name.lower().endswith(odf.XML_SUFFIXES) or (pkg.entries.get(name) or "").endswith("xml")


def _pair_digest(original: OdfPackage, cleaned: OdfPackage, name: str) -> tuple[str, str]:
    a, b = original.read(name), cleaned.read(name)
    if a == b:
        return _sha(a), _sha(b)
    if _is_xml(original, name):
        if name.rsplit("/", 1)[-1] == "meta.xml":
            ra, rb = original.xml(name).root, cleaned.xml(name).root
            return _sha(repr((ra.uri, ra.local)).encode()), _sha(repr((rb.uri, rb.local)).encode())
        return _sha(canonical(a, name, ODF_RULES)), _sha(canonical(b, name, ODF_RULES))
    da, db = _image_digest(a), _image_digest(b)
    if da is not None and db is not None:
        return da, db
    return _sha(a), _sha(b)


def structure_problems(pkg: OdfPackage) -> list[str]:
    problems = []
    first = pkg.archive.entries[0] if pkg.archive.entries else None
    if first is None or first.name != MIMETYPE or first.method != 0 or first.extra:
        problems.append("the mimetype entry is not first, stored and without extra fields")
    for path in pkg.entries:
        if path == "/" or path.endswith("/"):
            continue
        if not pkg.has(path):
            problems.append(f"{MANIFEST} lists {path}, which is not in the archive")
    return problems


def verify_package(cleaned: bytes, original: bytes | None, kind: str, warnings_for) -> dict:
    out: dict = {"leftovers": [], "problems": [], "notes": [], "image_sha256": None,
                 "original_image_sha256": None, "image_data_match": None,
                 "image_label": IMAGE_LABEL}
    try:
        pkg = open_odf(cleaned)
        if pkg.kind != kind:
            raise MalformedFileError(f"it is a {odf.KIND_NAMES[pkg.kind]}")
        odf.check_xml_files(pkg)
    except (MalformedFileError, UnsupportedFormatError) as exc:
        out["problems"].append(f"not an OpenDocument file metaex accepts: {exc}")
        return out
    out["problems"] += structure_problems(pkg)
    out["notes"] += warnings_for(pkg)
    names = [n for n in pkg.names() if n not in (MIMETYPE, MANIFEST)]
    if original is None:
        out["image_sha256"] = _sha("".join(f"{n}:{_sha(pkg.read(n))}\n"
                                           for n in sorted(names)).encode())
        return out
    try:
        before = open_odf(original)
    except (MalformedFileError, UnsupportedFormatError) as exc:
        out["problems"].append(f"original is not an OpenDocument file metaex accepts ({exc}); "
                               "the content was not compared")
        return out
    original_names = [n for n in before.names() if n not in (MIMETYPE, MANIFEST)]
    for name in sorted(set(original_names) - set(names) - removable(before)):
        out["problems"].append(f"{name} of the original is missing")
    for name in sorted(set(names) - set(original_names)):
        out["problems"].append(f"{name} is not in the original")
    if before.mimetype != pkg.mimetype:
        out["problems"].append("the mimetype differs from the original")
    gone = set(original_names) - set(names)
    folders = {p for p in before.entries if p.endswith("/")
               and not any(n.startswith(p) for n in names)
               and any(n.startswith(p) for n in gone)}
    expected = {(p, t) for p, t in before.entries.items() if p not in gone and p not in folders}
    if set(pkg.entries.items()) != expected:
        out["problems"].append(f"{MANIFEST} differs from the original's (other than the "
                               "removed files)")
    mine, theirs = [], []
    try:
        for name in sorted(set(names) & set(original_names)):
            a, b = _pair_digest(before, pkg, name)
            theirs.append(f"{name}:{a}\n")
            mine.append(f"{name}:{b}\n")
            if a != b:
                out["problems"].append(f"the content of {name} differs from the original")
    except MalformedFileError as exc:
        out["problems"].append(f"the content could not be compared: {exc}")
        return out
    out["image_sha256"] = _sha("".join(mine).encode())
    out["original_image_sha256"] = _sha("".join(theirs).encode())
    out["image_data_match"] = (out["image_sha256"] == out["original_image_sha256"]
                               and not out["problems"])
    return out
