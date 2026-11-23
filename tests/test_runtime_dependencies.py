"""metaex uses only the standard library at runtime.

Pillow, python-docx, openpyxl and python-pptx (and lxml, which they use)
are test-only dependencies: independent readers for the pixel and Office
content checks. metaex itself must never import them.
"""

import json
import subprocess
import sys
import textwrap

from helpers import jpeg
from helpers.gif import kitchen_sink_gif
from helpers.jpeg import kitchen_sink_jpeg
from helpers.ooxml import (COMMENTED_BODY, TRACKED_BODY, WORD_NAMESPACES, add_excel_comments,
                           add_media, add_ppt_comments, add_word_comments, docx, pptx,
                           with_properties, xlsx)
from helpers.png import kitchen_sink_png
from helpers.svg import kitchen_sink_svg
from helpers.webp import kitchen_sink_webp

FORBIDDEN = ("PIL", "docx", "openpyxl", "pptx", "lxml", "xlsxwriter")

CHILD = textwrap.dedent("""
    import json, sys
    from metaex.cli import main

    files, report = sys.argv[1:-1], sys.argv[-1]
    codes = []
    for path in files:
        clean = path.replace(".", "_clean.", 1)
        for args in (["show", path], ["show", path, "--json"], ["risk", path],
                     ["strip", path], ["verify", clean, "--original", path]):
            codes.append([args[0], main(args)])
    forbidden = %r
    loaded = sorted(m for m in sys.modules if m.split(".")[0] in forbidden)
    with open(report, "w", encoding="utf-8") as f:
        json.dump({"codes": codes, "forbidden_modules": loaded}, f)
""" % (FORBIDDEN,))


def office_samples() -> dict[str, bytes]:
    word = with_properties(add_word_comments(docx(COMMENTED_BODY + TRACKED_BODY,
                                                  extra_ns=WORD_NAMESPACES)))
    add_media(word, "word/document.xml", "word/media/image1.jpeg", jpeg.sample_photo(), "jpeg",
              "image/jpeg")
    return {"letter.docx": word.build(),
            "budget.xlsx": with_properties(add_excel_comments(xlsx())).build(),
            "talk.pptx": with_properties(add_ppt_comments(pptx())).build()}


def test_cli_never_imports_test_only_packages(tmp_path):
    samples = {"photo.jpg": kitchen_sink_jpeg(), "image.png": kitchen_sink_png(),
               "anim.gif": kitchen_sink_gif(), "image.webp": kitchen_sink_webp(),
               "drawing.svg": kitchen_sink_svg(), **office_samples()}
    paths = []
    for name, data in samples.items():
        (tmp_path / name).write_bytes(data)
        paths.append(str(tmp_path / name))
    report = tmp_path / "report.json"

    result = subprocess.run([sys.executable, "-c", CHILD, *paths, str(report)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    child = json.loads(report.read_text(encoding="utf-8"))

    assert child["forbidden_modules"] == []
    # Every command ran; show, strip and verify succeeded (risk's exit code
    # depends on what it finds, so it is not checked here).
    assert len(child["codes"]) == 5 * len(samples)
    assert all(code == 0 for command, code in child["codes"] if command != "risk")
    for name in samples:
        assert (tmp_path / name.replace(".", "_clean.", 1)).exists()


def test_test_only_packages_are_importable_in_the_test_environment():
    """The pixel and Office content checks rely on these packages; they must be
    installed (the [dev] extra), so a missing one fails the run instead of
    skipping tests."""
    import docx
    import openpyxl
    import PIL
    import pptx

    assert PIL.__version__ and openpyxl.__version__ and docx and pptx
