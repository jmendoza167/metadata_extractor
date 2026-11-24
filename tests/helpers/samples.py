"""The generator of the committed sample files (scripts/make_samples.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "make_samples.py"


def _load():
    module = sys.modules.get("make_samples")
    if module is None:
        spec = importlib.util.spec_from_file_location("make_samples", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.modules["make_samples"] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            # Don't leave a half-loaded module behind: the next test file
            # would get it and fail with a misleading AttributeError instead
            # of the real error.
            del sys.modules["make_samples"]
            raise
    return module


make_samples = _load()
SAMPLES_DIR: Path = make_samples.SAMPLES_DIR
