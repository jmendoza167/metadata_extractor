"""Install the metaex git pre-commit hook that scans staged changes with gitleaks.

Usage (from anywhere inside the repository):

    python scripts/install_hooks.py

The hook blocks the commit if gitleaks finds a possible secret, if the scan
fails, or if gitleaks is not installed (it never skips the scan silently).
An existing pre-commit hook with different content is never overwritten.

Development tool only: uses the standard library, and metaex itself does not
depend on gitleaks.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

HOOK_NAME = "pre-commit"

# Written with LF line endings: git runs hooks with sh on every platform,
# including Git for Windows.
HOOK_SCRIPT = """#!/bin/sh
# metaex pre-commit hook: scan staged changes for secrets with gitleaks.
# Installed by scripts/install_hooks.py; edit that script, not this file.

if ! command -v gitleaks >/dev/null 2>&1; then
    echo "pre-commit: gitleaks is not installed, so this commit is BLOCKED." >&2
    echo "Install it, then commit again:" >&2
    echo "  Windows: winget install Gitleaks.Gitleaks" >&2
    echo "  macOS:   brew install gitleaks" >&2
    echo "  Linux:   https://github.com/gitleaks/gitleaks/releases" >&2
    exit 1
fi

# gitleaks >= 8.19 scans staged changes with "git --pre-commit --staged";
# older versions use "protect --staged".
if gitleaks git --help >/dev/null 2>&1; then
    gitleaks git --pre-commit --staged --redact --verbose --no-banner
else
    gitleaks protect --staged --redact --verbose --no-banner
fi
status=$?

if [ "$status" -ne 0 ]; then
    echo "pre-commit: gitleaks reported a possible secret or failed (exit $status);" >&2
    echo "commit BLOCKED. Findings above are redacted." >&2
    exit 1
fi
exit 0
"""


def hooks_dir(cwd: Path) -> Path:
    """The repository's hooks directory (respects worktrees and core.hooksPath)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=cwd, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except FileNotFoundError:
        raise SystemExit("install_hooks: git is not installed") from None
    except subprocess.CalledProcessError:
        raise SystemExit(f"install_hooks: {cwd} is not inside a git repository") from None
    path = Path(out)
    return path if path.is_absolute() else (cwd / path)


def install(cwd: Path) -> tuple[int, str]:
    """Install the hook; return (exit code, message)."""
    directory = hooks_dir(cwd)
    hook = directory / HOOK_NAME
    if hook.exists():
        existing = hook.read_bytes().replace(b"\r\n", b"\n")
        if existing == HOOK_SCRIPT.encode("ascii"):
            return 0, f"pre-commit hook already installed: {hook}"
        return 1, (f"install_hooks: {hook} already exists with different content; "
                   "refusing to overwrite it. Review it and remove it yourself if you "
                   "want this hook instead.")
    directory.mkdir(parents=True, exist_ok=True)
    with open(hook, "xb") as f:  # exclusive create: never clobber a file
        f.write(HOOK_SCRIPT.encode("ascii"))
    mode = os.stat(hook).st_mode
    os.chmod(hook, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return 0, f"installed pre-commit hook: {hook}"


def main() -> int:
    code, message = install(Path.cwd())
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
