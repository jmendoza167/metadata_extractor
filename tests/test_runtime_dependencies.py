"""metaex uses only the standard library at runtime.

Pillow is a test-only dependency (an independent decoder for the pixel
checks); metaex itself must never import it.
"""

import json
import subprocess
import sys
import textwrap

from helpers.gif import kitchen_sink_gif
from helpers.jpeg import kitchen_sink_jpeg
from helpers.png import kitchen_sink_png
from helpers.svg import kitchen_sink_svg
from helpers.webp import kitchen_sink_webp

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
    loaded = sorted(m for m in sys.modules if m == "PIL" or m.startswith("PIL."))
    with open(report, "w", encoding="utf-8") as f:
        json.dump({"codes": codes, "pil_modules": loaded}, f)
""")


def test_cli_never_imports_pillow(tmp_path):
    samples = {"photo.jpg": kitchen_sink_jpeg(), "image.png": kitchen_sink_png(),
               "anim.gif": kitchen_sink_gif(), "image.webp": kitchen_sink_webp(),
               "drawing.svg": kitchen_sink_svg()}
    paths = []
    for name, data in samples.items():
        (tmp_path / name).write_bytes(data)
        paths.append(str(tmp_path / name))
    report = tmp_path / "report.json"

    result = subprocess.run([sys.executable, "-c", CHILD, *paths, str(report)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    child = json.loads(report.read_text(encoding="utf-8"))

    assert child["pil_modules"] == []
    # Every command ran; show, strip and verify succeeded (risk's exit code
    # depends on what it finds, so it is not checked here).
    assert len(child["codes"]) == 5 * len(samples)
    assert all(code == 0 for command, code in child["codes"] if command != "risk")
    for name in samples:
        assert (tmp_path / name.replace(".", "_clean.", 1)).exists()


def test_pillow_is_importable_in_the_test_environment():
    """The pixel checks rely on Pillow; it must be installed (the [dev] extra),
    so a missing Pillow fails the run instead of skipping tests."""
    import PIL

    assert PIL.__version__
