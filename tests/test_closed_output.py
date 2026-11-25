"""Output piped into a program that stops reading (`metaex show ... | head`).

Regression test: when the reader closed the pipe, the next write raised
BrokenPipeError (OSError EINVAL on Windows), which metaex reported as
"internal error: BrokenPipeError". It now stops quietly with exit code 1."""

import subprocess
import sys

import pytest

from helpers.jpeg import kitchen_sink_jpeg


@pytest.mark.parametrize("extra", [[], ["--csv"], ["--json"]])
def test_reader_closing_the_pipe_stops_quietly(tmp_path, extra):
    photos = tmp_path / "photos"
    photos.mkdir()
    for i in range(40):  # plenty of output, more than a pipe holds
        (photos / f"photo{i:02}.jpg").write_bytes(kitchen_sink_jpeg())
    # stderr goes to a file: with --json and --csv the progress lines go
    # there, and a pipe nobody reads would fill up and block metaex
    with open(tmp_path / "stderr.txt", "wb") as stderr:
        p = subprocess.Popen([sys.executable, "-m", "metaex", "show", *extra, str(photos)],
                             stdout=subprocess.PIPE, stderr=stderr)
        p.stdout.read(200)
        p.stdout.close()  # like head after its lines
        assert p.wait(timeout=120) == 1
    err = (tmp_path / "stderr.txt").read_text(encoding="utf-8", errors="replace")
    assert "internal error" not in err
    assert "Traceback" not in err and "Exception ignored" not in err
