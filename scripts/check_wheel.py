"""Install a built metaex wheel in a fresh virtual environment and try it.

Usage:

    python scripts/check_wheel.py dist/metaex-X.Y.Z-py3-none-any.whl tests/samples

The wheel is installed with no dependencies and no package index, in a new
venv in a temporary folder. Every command runs the installed `metaex`
program from that temporary folder, so the source tree can't be imported by
accident (the check fails if metaex is imported from anywhere else). It
checks:

- `metaex --version` shows the wheel's version,
- `metaex formats` works,
- strip + verify on samples of each kind (image, document, audio, archive):
  the cleaned copy verifies as clean, the original is reported not clean
  and is left unchanged,
- a batch run over a folder.

Exit code 0 when every check passes, 1 otherwise. Development tool only:
standard library, used by CI (tests.yml, release.yml) and RELEASING.md.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

# (sample, extra strip options): one of each kind
SAMPLES = (
    ("jpeg/kitchen_sink.jpg", ()),
    ("docx/kitchen_sink.docx", ()),
    ("mp3/kitchen_sink.mp3", ()),
    ("flac/kitchen_sink.flac", ()),
    ("zip/kitchen_sink.zip", ("--deep",)),
)


class CheckFailed(Exception):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wheel_version(wheel: Path) -> str:
    parts = wheel.name.split("-")
    if len(parts) < 5 or parts[0] != "metaex" or not wheel.name.endswith(".whl"):
        raise CheckFailed(f"not a metaex wheel: {wheel.name}")
    return parts[1]


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def run(cmd: list[str], cwd: Path, expect: int = 0) -> str:
    shown = " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd))
    print(f"$ {shown}", flush=True)
    r = subprocess.run(cmd, cwd=cwd, env=clean_env(), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    out = (r.stdout + r.stderr).rstrip()
    lines = out.splitlines()
    for line in lines[:6]:
        print(f"  {line}")
    if len(lines) > 6:
        print(f"  ... ({len(lines) - 6} more lines)")
    if r.returncode != expect:
        raise CheckFailed(f"{shown}: exit code {r.returncode}, expected {expect}")
    print(f"  (exit {r.returncode})", flush=True)
    return r.stdout


def check(wheel: Path, samples: Path, base: Path) -> None:
    version = wheel_version(wheel)
    env_dir = base / "venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
    scripts = env_dir / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    metaex = scripts / ("metaex.exe" if os.name == "nt" else "metaex")
    work = base / "work"
    work.mkdir()

    run([str(python), "-m", "pip", "install", "--no-index", "--no-deps",
         "--disable-pip-version-check", "--quiet", str(wheel)], cwd=work)
    if not metaex.exists():
        raise CheckFailed(f"the wheel installed no metaex program ({metaex})")

    location = run([str(python), "-I", "-c", "import metaex; print(metaex.__file__)"],
                   cwd=work).strip()
    if not Path(location).resolve().is_relative_to(env_dir.resolve()):
        raise CheckFailed(f"metaex was imported from {location}, not from the new venv")

    out = run([str(metaex), "--version"], cwd=work).strip()
    if out != f"metaex {version}":
        raise CheckFailed(f"--version shows {out!r}, expected 'metaex {version}'")

    out = run([str(metaex), "formats"], cwd=work)
    for name in ("JPEG", "DOCX", "MP3", "ZIP"):
        if name not in out:
            raise CheckFailed(f"metaex formats doesn't list {name}")

    for rel, options in SAMPLES:
        source = samples / rel
        if not source.is_file():
            raise CheckFailed(f"sample missing: {source}")
        original = work / source.name
        shutil.copyfile(source, original)
        before = sha256(original)
        cleaned = original.with_name(f"{original.stem}_clean{original.suffix}")
        run([str(metaex), "strip", original.name, *options], cwd=work)
        if not cleaned.is_file():
            raise CheckFailed(f"strip wrote no {cleaned.name}")
        run([str(metaex), "verify", cleaned.name, "--original", original.name], cwd=work)
        run([str(metaex), "verify", original.name], cwd=work, expect=3)
        if sha256(original) != before:
            raise CheckFailed(f"strip changed the original {original.name}")

    batch = work / "batch"
    batch.mkdir()
    for rel, _ in SAMPLES:
        shutil.copyfile(samples / rel, batch / Path(rel).name)
    run([str(metaex), "strip", "batch", "--output-dir", "batch_out", "--deep", "--quiet"],
        cwd=work)
    run([str(metaex), "verify", "batch_out", "--original", "batch", "--quiet"], cwd=work)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python scripts/check_wheel.py WHEEL SAMPLES_FOLDER", file=sys.stderr)
        return 2
    wheel, samples = Path(argv[0]).resolve(), Path(argv[1]).resolve()
    if not wheel.is_file():
        print(f"check_wheel: no such wheel: {wheel}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="metaex_wheel_") as tmp:
        try:
            check(wheel, samples, Path(tmp))
        except (CheckFailed, OSError, subprocess.SubprocessError) as e:
            print(f"check_wheel: FAILED: {e}", file=sys.stderr)
            return 1
    print(f"check_wheel: all checks passed for {wheel.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
