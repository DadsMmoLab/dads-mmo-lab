"""Each entry module imports cold, in a fresh interpreter (T668).

`yulon.catalog.native` imports `yulon.seeded_accounts` for a `Seams` default, and the SRP6 writer
that module needs imports back into `native`; a top-level import made `import yulon.seeded_accounts`
and `import yulon.ui.controller_view` fail with a partially initialised module while every test
(which imports in a friendly order) passed. A child process is the only honest check.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "module",
    [
        "yulon.seeded_accounts",
        "yulon.ui.controller_view",
        "yulon.catalog.native",
        "yulon.controller",
        "main",
    ],
)
def test_the_module_imports_in_a_fresh_interpreter(module: str) -> None:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    done = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-1500:]
