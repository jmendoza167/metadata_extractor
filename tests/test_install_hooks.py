"""Tests for scripts/install_hooks.py (a dev tool, not part of metaex)."""

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install_hooks.py"
spec = importlib.util.spec_from_file_location("install_hooks", SCRIPT)
install_hooks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install_hooks)

GIT = shutil.which("git")
SH = shutil.which("sh")
needs_git = pytest.mark.skipif(GIT is None, reason="git is not installed")
needs_sh = pytest.mark.skipif(SH is None, reason="sh is not available")


@pytest.fixture
def repo(tmp_path):
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def hook_path(repo: Path) -> Path:
    return install_hooks.hooks_dir(repo) / "pre-commit"


def test_hook_script_is_ascii_with_lf_line_endings():
    data = install_hooks.HOOK_SCRIPT.encode("ascii")
    assert b"\r" not in data
    assert data.startswith(b"#!/bin/sh\n")
    assert b"--redact" in data and b"--staged" in data


@needs_git
def test_installs_hook(repo):
    code, message = install_hooks.install(repo)
    assert code == 0
    assert "installed" in message
    hook = hook_path(repo)
    assert hook.read_bytes() == install_hooks.HOOK_SCRIPT.encode("ascii")
    if sys.platform != "win32":
        assert hook.stat().st_mode & stat.S_IXUSR


@needs_git
def test_installing_twice_is_harmless(repo):
    install_hooks.install(repo)
    code, message = install_hooks.install(repo)
    assert code == 0
    assert "already installed" in message


@needs_git
def test_refuses_to_overwrite_a_different_hook(repo):
    hook = hook_path(repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_bytes(b"#!/bin/sh\necho my own hook\n")
    code, message = install_hooks.install(repo)
    assert code == 1
    assert "refusing to overwrite" in message
    assert hook.read_bytes() == b"#!/bin/sh\necho my own hook\n"


@needs_git
def test_runs_as_a_script(repo):
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=repo,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert hook_path(repo).exists()


def test_outside_a_repository(tmp_path):
    if GIT is None:
        pytest.skip("git is not installed")
    with pytest.raises(SystemExit, match="not inside a git repository"):
        install_hooks.install(tmp_path)


# --- running the installed hook ---------------------------------------------------

def run_hook(repo: Path, path_dirs: list[str]) -> subprocess.CompletedProcess:
    install_hooks.install(repo)
    env = dict(os.environ, PATH=os.pathsep.join(path_dirs))
    return subprocess.run([SH, str(hook_path(repo))], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=60)


def fake_gitleaks(directory: Path, exit_code: int) -> str:
    tool = directory / "gitleaks"
    tool.write_bytes(f"#!/bin/sh\nexit {exit_code}\n".encode("ascii"))
    tool.chmod(0o755)
    return str(directory)


@needs_git
@needs_sh
def test_hook_blocks_when_gitleaks_is_missing(repo):
    result = run_hook(repo, [str(Path(SH).parent)])
    assert result.returncode == 1
    assert "gitleaks is not installed" in result.stderr
    assert "BLOCKED" in result.stderr


@needs_git
@needs_sh
@pytest.mark.parametrize("exit_code,blocked", [(0, False), (1, True), (2, True)])
def test_hook_follows_the_gitleaks_result(repo, tmp_path_factory, exit_code, blocked):
    bin_dir = fake_gitleaks(tmp_path_factory.mktemp("bin"), exit_code)
    result = run_hook(repo, [bin_dir, str(Path(SH).parent)])
    assert result.returncode == (1 if blocked else 0)
    if blocked:
        assert "commit BLOCKED" in result.stderr
