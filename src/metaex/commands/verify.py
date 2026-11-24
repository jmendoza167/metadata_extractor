"""metaex verify: check that a cleaned file contains no metadata."""

from __future__ import annotations

import os
from typing import Any

from metaex.core.generic import mark_of_the_web_section
from metaex.core.loader import DEFAULT_MAX_SIZE, load_file
from metaex.core.verify import VerifyResult, verdict, verify


def verify_file(cleaned: str | os.PathLike[str], original: str | os.PathLike[str] | None = None,
                max_size: int = DEFAULT_MAX_SIZE) -> VerifyResult:
    name = os.fspath(cleaned)
    data = load_file(name, max_size)
    original_data = load_file(os.fspath(original), max_size) if original is not None else None
    result = verify(data, original_data, filename=name)
    if mark_of_the_web_section(name) is not None:
        result.notes.append("this file has a Windows Zone.Identifier stream (download origin); "
                            "it is not part of the file's content, but stays attached to "
                            "this copy on this computer")
    return result


def result_to_dict(path: str, result: VerifyResult) -> dict[str, Any]:
    def findings(items):
        return [{"section": f.section, "label": f.label, "value": f.value} for f in items]

    return {
        "path": path,
        "type": result.filetype.name,
        "clean": result.clean,
        "verdict": verdict(result),
        "leftovers": findings(result.leftovers),
        "problems": result.problems,
        "kept_by_design": findings(result.kept_by_design),
        "image_data_match": result.image_data_match,
        "image_data_rebuilt": result.image_data_rebuilt,
        "image_sha256": result.image_sha256,
        "original_image_sha256": result.original_image_sha256,
        "notes": result.notes,
    }


def result_lines(path: str, result: VerifyResult) -> list[str]:
    lines = [f"metaex verify: {path}",
             f"Result: {verdict(result)}", ""]
    if result.leftovers:
        lines.append(f"Leftovers ({len(result.leftovers)}):")
        lines += [f"  {f}" for f in result.leftovers]
        lines.append("")
    if result.problems:
        lines.append("Problems:")
        lines += [f"  {p}" for p in result.problems]
        lines.append("")
    lines.append("Kept by design:")
    lines += [f"  {f}" for f in result.kept_by_design] or ["  (nothing)"]
    lines.append("")
    if result.image_data_match is True and result.image_data_rebuilt:
        lines.append("Image data: rebuilt, pixels identical (SHA-256 of the decompressed "
                     f"image data {result.image_sha256})")
    elif result.image_data_match is True:
        lines.append(f"{result.image_label}: matches the original "
                     f"(SHA-256 {result.image_sha256})")
    elif result.image_data_match is False:
        lines.append(f"{result.image_label}: DIFFERS from the original")
        lines.append(f"  cleaned  SHA-256 {result.image_sha256}")
        lines.append(f"  original SHA-256 {result.original_image_sha256}")
    elif result.image_sha256:
        lines.append(f"{result.image_label}: SHA-256 {result.image_sha256} "
                     "(not compared; pass --original to compare)")
    for note in result.notes:
        lines.append(f"Note: {note}")
    return lines
