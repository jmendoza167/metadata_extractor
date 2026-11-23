"""Tests for scripts/make_samples.py (a dev tool, not part of metaex), run
on a temporary folder; the real samples are checked in test_samples_*."""

import pytest

from helpers.samples import make_samples

SAMPLES = {"jpeg/a.jpg": b"\xff\xd8 one", "svg/b.svg": b"<svg>\r\n\n\r</svg>\n"}


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_write_then_check(tmp_path):
    written, extra = make_samples.write(SAMPLES, tmp_path)
    assert written == list(SAMPLES) and extra == []
    assert files(tmp_path) == SAMPLES  # exact bytes: no line-ending conversion
    assert make_samples.check(SAMPLES, tmp_path) == []
    assert make_samples.write(SAMPLES, tmp_path) == ([], [])  # nothing to rewrite


def test_check_reports_missing_different_and_extra_files(tmp_path):
    make_samples.write(SAMPLES, tmp_path)
    (tmp_path / "svg" / "b.svg").write_bytes(b"<svg>\n\n\n</svg>\n")
    (tmp_path / "gif").mkdir()
    (tmp_path / "gif" / "hand-made.gif").write_bytes(b"GIF89a")
    (tmp_path / "notes.txt").write_bytes(b"x")
    more = dict(SAMPLES, **{"png/c.png": b"png"})
    assert make_samples.check(more, tmp_path) == [
        "differs from the generator: svg/b.svg",
        "missing: png/c.png",
        "not made by the generator: gif/hand-made.gif",
        "not made by the generator: notes.txt",
    ]


def test_check_ignores_the_files_it_does_not_own(tmp_path):
    make_samples.write(SAMPLES, tmp_path)
    for name in ("SAMPLES.md", "expected/jpeg/a.jpg.show.json", "real/office/report.docx"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not generated")
    assert make_samples.check(SAMPLES, tmp_path) == []
    assert sorted(make_samples.files_on_disk(tmp_path)) == sorted(SAMPLES)


def test_write_never_deletes_extra_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(make_samples, "build", lambda: SAMPLES)
    (tmp_path / "jpeg").mkdir()
    (tmp_path / "jpeg" / "old.jpg").write_bytes(b"old")
    assert make_samples.main([], tmp_path) == 1
    assert (tmp_path / "jpeg" / "old.jpg").read_bytes() == b"old"
    assert "not made by the generator (remove it by hand): jpeg/old.jpg" in capsys.readouterr().err


def test_main_check(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(make_samples, "build", lambda: SAMPLES)
    assert make_samples.main(["--check"], tmp_path) == 1
    assert files(tmp_path) == {}  # --check writes nothing
    assert "missing: jpeg/a.jpg" in capsys.readouterr().err
    assert make_samples.main([], tmp_path) == 0
    assert make_samples.main(["--check"], tmp_path) == 0
    assert "all 2 sample files match" in capsys.readouterr().out


def test_build_refuses_duplicate_and_misplaced_names(monkeypatch):
    for bad in ([lambda: {"jpeg/a.jpg": b"1"}, lambda: {"jpeg/a.jpg": b"2"}],
                [lambda: {"top.jpg": b"1"}], [lambda: {"expected/a.json": b"1"}],
                [lambda: {"real/a.jpg": b"1"}]):
        monkeypatch.setattr(make_samples, "BUILDERS", bad)
        with pytest.raises(ValueError):
            make_samples.build()


def test_size_limits():
    under = make_samples.MAX_FILE_SIZE - 1
    make_samples.check_sizes({"a/1": bytes(under)})
    with pytest.raises(ValueError, match="under 102400 bytes"):
        make_samples.check_sizes({"a/1": bytes(make_samples.MAX_FILE_SIZE)})
    many = {f"a/{i}": bytes(under) for i in range(11)}
    with pytest.raises(ValueError, match="add up to"):
        make_samples.check_sizes(many)


def test_build_is_deterministic():
    assert make_samples.build() == make_samples.build()
