"""Skip helper: is there a `bash` that can actually run a script on this machine?

A plain module rather than a conftest fixture, so a reader of `test_runner.py`
or `test_prompt.py` can see by name where the skip condition comes from — the
same shape as `tests/support_native.py`.

This function was written for the product, in `yulon/catalog/installer.py`,
back when `Installer` shelled out to the bash installers; plan 7.2 (Group F)
retires that lineage, and F.1 copied the probe here on 2026-09-01 so the test
suite keeps its skip after F.3 deletes `Installer`. Three test files skip on
"no usable bash" today and F.5's `bash -n` check on the one surviving script
wants the same condition.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable

from yulon import runner


def bash_available(run: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> bool:
    """True if a `bash` that can actually run a script is on PATH.

    Being on PATH is not enough on Windows, for two different reasons measured
    on real machines:

    - On a Windows that has had WSL enabled at some point, `bash.exe` is the
      Store alias for WSL and fails with `execvpe(/bin/bash)` when no distro is
      installed. Docker Desktop's own WSL distros do not provide one.
    - On a genuinely clean Windows 11 (25H2, build 26200, measured 2026-08-22)
      there is no `bash.exe` at all — not in System32, not as an execution
      alias — so this returns False at the `which()` line and never runs
      anything.

    Both end at "no usable bash", which is why the probe runs the binary
    instead of trusting PATH. Note that `which()` alone is actively misleading
    on Windows for a different reason: `shutil.which("python")` returns a
    truthy path to a zero-byte Store alias on a machine with no Python at all,
    so any future interpreter probe needs this same shape.
    """
    if shutil.which("bash") is None:
        return False
    call = run if run is not None else runner.run
    try:
        return call(["bash", "-c", "exit 0"]).returncode == 0
    except OSError:
        return False
