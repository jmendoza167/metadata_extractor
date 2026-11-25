"""strip --deep: the verdict doesn't tell the user to use --deep.

Regression test: when --deep had to copy a file inside unchanged (a damaged
photo that still holds its EXIF), the verdict said "not cleaned, use
--deep" although --deep had been used. It now says that --deep could not
clean them; verify, which doesn't know how a file was made, still suggests
strip --deep."""

from helpers.jpeg import sample_photo
from helpers.zip import make_zip
from metaex.commands.strip import strip_file, summary_lines
from metaex.core.verify import verdict, verify


def test_deep_verdict(tmp_path):
    damaged = sample_photo()[:-40]  # the scan data is cut: can't be cleaned, EXIF readable
    src = tmp_path / "photos.zip"
    src.write_bytes(make_zip({"photo.jpg": damaged}))
    outcome = strip_file(src, tmp_path / "out.zip", deep=True)
    text = "\n".join(summary_lines(outcome))
    assert "--deep could not clean them" in text
    assert "use --deep" not in text
    assert outcome.to_dict()["verify"]["verdict"].endswith("(--deep could not clean them)")
    # verify on its own still points to strip --deep
    assert verdict(verify((tmp_path / "out.zip").read_bytes())).endswith(
        "(not cleaned, use --deep)")
