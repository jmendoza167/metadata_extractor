"""Basic tests for `metaex risk`."""

import io
import json
import os
import subprocess
import sys

import pytest

from helpers.jpeg import kitchen_sink_jpeg
from metaex.commands import risk as risk_cmd
from metaex.formats import jpeg

ESC = chr(27)


def run(*args, env=None):
    return subprocess.run([sys.executable, "-m", "metaex", "risk", *args], capture_output=True,
                          text=True, timeout=60, env=env)


@pytest.fixture
def photo(tmp_path):
    p = tmp_path / "photo.jpg"
    p.write_bytes(kitchen_sink_jpeg())
    return p


def test_summary_line_and_groups(photo):
    result = run(str(photo))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"metaex risk: {photo}"
    assert lines[1].startswith(tuple(f"{n} critical" for n in range(1, 200)))
    assert " high, " in lines[1] and " medium, " in lines[1] and lines[1].endswith(" low")
    order = [result.stdout.index(h) for h in ("CRITICAL (", "HIGH (", "MEDIUM (", "LOW (")]
    assert order == sorted(order)
    assert "GPS: Coordinates" in result.stdout
    assert lines[-1] == f'Run "metaex strip {photo}" to remove these.'


def test_no_color_when_not_a_terminal(photo):
    assert ESC not in run(str(photo)).stdout


def test_json(photo):
    data = json.loads(run(str(photo), "--json").stdout)
    assert set(data["summary"]) == {"critical", "high", "medium", "low"}
    assert data["can_strip"] is True
    levels = [f["level"] for f in data["findings"]]
    assert levels == sorted(levels, key=["critical", "high", "medium", "low"].index)
    assert sum(data["summary"].values()) == len(data["findings"])


def test_cleaned_file_has_no_critical_or_high(tmp_path):
    p = tmp_path / "clean.jpg"
    p.write_bytes(jpeg.strip(kitchen_sink_jpeg()))
    data = json.loads(run(str(p), "--json").stdout)
    assert data["summary"]["critical"] == 0
    assert data["summary"]["high"] == 0


def test_unsupported_type_hint(tmp_path):
    p = tmp_path / "a.flac"
    p.write_bytes(b"fLaC" + bytes(20))
    result = run(str(p))
    assert result.returncode == 0
    assert "No embedded metadata found." in result.stdout
    assert "NOT examined" in result.stdout


class Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize(
    "flag,env,tty,expected",
    [
        (False, {}, True, True),
        (True, {}, True, False),
        (False, {"NO_COLOR": "1"}, True, False),
        (False, {"NO_COLOR": ""}, True, False),
        (False, {}, False, False),
    ],
)
def test_use_color(monkeypatch, flag, env, tty, expected):
    monkeypatch.delenv("NO_COLOR", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(risk_cmd, "_enable_windows_ansi", lambda: True)
    stream = Tty() if tty else io.StringIO()
    assert risk_cmd.use_color(flag, stream) is expected


def test_colored_output_and_sanitized_values(photo):
    report, findings, strippable = risk_cmd.assess_file(str(photo))
    colored = "\n".join(risk_cmd.risk_lines(str(photo), report, findings, strippable, color=True))
    assert ESC + "[1;31mCRITICAL" in colored
    plain = "\n".join(risk_cmd.risk_lines(str(photo), report, findings, strippable, color=False))
    assert ESC not in plain


def test_control_characters_in_values_cannot_inject_escapes(tmp_path):
    from helpers.jpeg import make_jpeg, segment

    p = tmp_path / "evil.jpg"
    p.write_bytes(make_jpeg(segment(0xFE, b"\x1b[2J\x1b]0;pwned\x07")))
    out = run(str(p)).stdout
    assert ESC not in out and "\x07" not in out
