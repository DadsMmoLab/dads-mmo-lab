"""A live process that really is running a given file, for the self-update guards.

Test support. `layout.other_instances` and `layout.pid_is_this_app` read
`/proc/<pid>/exe`, so the tests that drive them need a real second process whose
`/proc/<pid>/exe` IS the install's executable — a copy of some program under the
install's own name, started and left waiting.

What that program is has been wrong three times:

- **the interpreter**: a Python copied out of its prefix cannot find its
  standard library and exits in milliseconds;
- **`sleep` on busybox** (round 5, N6): an applet, and busybox started under
  another name looks for an applet of THAT name and exits at once;
- **`sleep` on Ubuntu 26.04** (CI, 2026-10-30): `/usr/bin/sleep` is the uutils
  `coreutils` multicall binary, which picks its tool from the name in
  `/proc/self/exe` — not even `argv[0]` — and printed "unknown program 'yulon'".
  It lived just long enough for `pid_is_this_app` to see it once, so the tests
  then looked at a process that was already gone: three tests failing on
  every upstream run once `ubuntu-latest` became 26.04.

So the stand-in is a copy of `sh` blocked in its own `read` builtin: a shell is
a real program on every box this runs on, it waits without starting a child,
and `argv[0]` stays `sh` so a busybox `sh` still finds its applet. Its stdin is
a pipe whose write end only the child holds, so the read never returns and the
test process keeps no descriptor. If the copy is not alive a moment later, this
box cannot answer the question and the test says so instead of failing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from yulon.selfupdate import layout


def a_stand_in(exe: Path) -> subprocess.Popen[bytes]:
    """A live process whose `/proc/<pid>/exe` really is `exe`, or a skip."""
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("this box has no `sh` to stand in for the app")
    shutil.copy2(sh, exe)
    read_end, write_end = os.pipe()
    try:
        child = subprocess.Popen(
            ["sh", "-c", "read x"],
            executable=str(exe),
            stdin=read_end,
            pass_fds=(write_end,),
        )
    finally:
        os.close(read_end)
        os.close(write_end)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if child.poll() is not None:
            pytest.skip(f"a copy of {sh} does not run under another name")
        if layout.pid_is_this_app(child.pid, exe) is True:
            # Seen once is not enough: the uutils copy was seen and then died.
            time.sleep(0.05)
            if child.poll() is None:
                return child
        time.sleep(0.02)
    child.kill()
    child.wait(timeout=30)
    pytest.skip("a copied `sh` could not be identified through /proc on this box")
