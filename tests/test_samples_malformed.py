"""The damaged samples in tests/samples/malformed/: strip refuses each one
with a clear error and writes nothing; show and risk report the damage."""

import json
import shutil
import subprocess
import sys

import pytest

from helpers.samples import SAMPLES_DIR

PROBLEMS = {
    "bad_crc.png": "tEXt chunk at offset 644 has a bad CRC",
    "bad_id3_size.mp3": "the ID3v2 tag at offset 0 is truncated: it needs 1000010 bytes",
    "broken_block_length.flac": "metadata block at offset 42 is truncated: needs 100000 bytes",
    "truncated.mp3": "the MPEG frame at offset 8387 is truncated: needs 418 bytes",
    "wrong_riff_size.wav": "RIFF size says the file has 8174 bytes, but it has 8074",
    "broken_subblock.gif": "a sub-block needs 255 bytes, 46 available",
    "encrypted_entry.docx": "'word/document.xml' is encrypted (password-protected)",
    "entity.svg": "ENTITY declaration (possible XML entity attack)",
    "path_traversal.docx": "goes up a folder (\"..\"; path traversal)",
    "truncated.docx": "the file may be truncated",
    "truncated.jpg": "missing end-of-image (EOI) marker",
    "wrong_riff_size.webp": "RIFF size says the file has 134 bytes, but it has 34",
    "zip_bomb.docx": "more than the compression ratio limit (possible zip bomb)",
}


def metaex(*args):
    return subprocess.run([sys.executable, "-m", "metaex", *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


def test_the_malformed_samples():
    assert sorted(p.name for p in (SAMPLES_DIR / "malformed").iterdir()) == sorted(PROBLEMS)


@pytest.mark.parametrize("name", sorted(PROBLEMS))
def test_strip_refuses_and_writes_nothing(name, tmp_path):
    sample = tmp_path / name
    shutil.copyfile(SAMPLES_DIR / "malformed" / name, sample)
    out = tmp_path / "out"
    out.mkdir()
    for args in ([sample], [sample, "-o", out / name], [sample, "--rename"],
                 [sample, "--paranoid"], [sample, "--json"]):
        r = metaex("strip", *args)
        assert r.returncode == 1, (args, r.stdout, r.stderr)
        assert PROBLEMS[name] in r.stderr and "no file written" in r.stderr
        assert "internal error" not in r.stderr and "Traceback" not in r.stderr
        assert sorted(p.name for p in tmp_path.iterdir()) == sorted([name, "out"])
        assert list(out.iterdir()) == []
    assert sample.read_bytes() == (SAMPLES_DIR / "malformed" / name).read_bytes()


@pytest.mark.parametrize("name", sorted(PROBLEMS))
def test_strip_in_place_refuses_and_keeps_the_original(name, tmp_path):
    sample = tmp_path / name
    shutil.copyfile(SAMPLES_DIR / "malformed" / name, sample)
    r = metaex("strip", sample, "--in-place", "--yes")
    assert r.returncode == 1 and PROBLEMS[name] in r.stderr
    assert sample.read_bytes() == (SAMPLES_DIR / "malformed" / name).read_bytes()
    assert [p.name for p in tmp_path.iterdir()] == [name]


@pytest.mark.parametrize("name", sorted(PROBLEMS))
def test_show_and_risk_report_the_damage(name):
    path = SAMPLES_DIR / "malformed" / name
    show = metaex("show", "--json", path)
    assert show.returncode == 0, show.stderr
    assert any(PROBLEMS[name] in w for w in json.loads(show.stdout)["warnings"])
    risk = metaex("risk", "--json", path)
    assert risk.returncode == 0, risk.stderr
    assert any(PROBLEMS[name] in w for w in json.loads(risk.stdout)["warnings"])
    text = metaex("show", path)
    assert text.returncode == 0
    assert PROBLEMS[name] in " ".join(text.stdout.split())  # the report wraps long lines


@pytest.mark.parametrize("name", sorted(PROBLEMS))
def test_verify_does_not_call_a_damaged_file_clean(name):
    r = metaex("verify", SAMPLES_DIR / "malformed" / name)
    assert r.returncode in (1, 3), r.stdout
    assert "internal error" not in r.stderr
