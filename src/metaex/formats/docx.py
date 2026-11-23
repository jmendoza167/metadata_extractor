"""DOCX: Word documents (also .docm, .dotx, .dotm).

The package-level parts (document properties, custom XML, relationships,
the ZIP container) are handled in core/ooxml.py; this module reads what is
specific to Word: document variables, comments, people, tracked changes,
revision IDs and hidden text.
"""

from __future__ import annotations

from metaex.core.ooxml import Package, shorten
from metaex.core.result import Section

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MAX_LISTED = 200


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
