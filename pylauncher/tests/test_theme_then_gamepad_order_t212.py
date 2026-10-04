"""T212: the D-pad navigation tests pass when `test_theme.py` runs before them.

They failed in that order -- four of them, every time -- and passed alone and in
CI, only because CI runs the files alphabetically and `g` comes before `t`.
`test_theme.py` themed the whole `QApplication` (`apply_dadcraft_theme(qapp)`)
and never put it back, and the theme's touch-target floor (`min-width`,
`min-height` on every `QPushButton`) then grew each button the `placed` fixture
put at a fixed size: 60x20 came back 98x50 (measured 2026-10-04). Buttons that
overlap answer "what is beside me" differently, so Left and Right skipped a
column and Down passed over a row.

`conftest.py`'s `_no_test_leaves_the_application_restyled` now fails whichever
test leaves the app's sheet or palette changed, in any order. This runs the two
files in the order that broke, in a process of their own, because the suite
itself never does: a guard that only holds while the files keep their names is
the defect this ticket was.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PYLAUNCHER = Path(__file__).resolve().parents[1]

ORDER_RUN_BOUND = 600.0
"""A deadlock breaker for the child run, not a claim about its speed.

The two files took 17 s together on the laptop (2026-10-04). Ten minutes is a
hang, whatever the box is doing.
"""


@pytest.mark.slow
def test_the_navigation_tests_pass_after_test_theme_in_one_process() -> None:
    """Mutation: drop the restore in `test_theme.py`'s `_application_theme_is_restored`
    and this fails: the conftest guard errors the two app-level theme tests (and
    puts the theme back, so the navigation tests after them pass). Drop the
    guard's own restore as well and the four T212 navigation tests fail here."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_theme.py",
            "tests/test_gamepad_keyboard.py",
        ],
        cwd=PYLAUNCHER,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=ORDER_RUN_BOUND,
        check=False,
    )
    tail = "\n".join(run.stdout.splitlines()[-40:])
    assert run.returncode == 0, f"test_theme.py then test_gamepad_keyboard.py:\n{tail}"
    assert " passed" in tail and "failed" not in tail and "error" not in tail, tail
