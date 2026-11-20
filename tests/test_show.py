import json
import subprocess
import sys

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + b"\x00" * 64


def run_show(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "metaex", "show", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_show_text_report(tmp_path):
    p = tmp_path / "image.png"
    p.write_bytes(PNG)
    result = run_show(str(p))
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    out = " ".join(result.stdout.split())  # ignore line wrapping
    for expected in ("File type", "PNG", "planned (not implemented yet)",
                     "File system", "Hashes", "SHA-256", "was NOT examined"):
        assert expected in out


def test_show_json_report(tmp_path):
    p = tmp_path / "image.png"
    p.write_bytes(PNG)
    result = run_show(str(p), "--json")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["sections"]["file_type"]["fields"]["name"]["value"] == "PNG"
    assert data["sections"]["file_system"]["fields"]["size_bytes"]["value"] == len(PNG)


def test_show_missing_file_is_one_line_error(tmp_path):
    result = run_show(str(tmp_path / "missing.png"))
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("metaex: error:")
    assert len(result.stderr.strip().splitlines()) == 1
    assert "Traceback" not in result.stderr
