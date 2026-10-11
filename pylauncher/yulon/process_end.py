"""End the process now, without teardown: the forced exit's last step (T113, T709).

`main._leave_with_jobs_still_running()` flushes what must survive (the log, abandoned
`stream()` children) and then leaves through here, because a return would reach the
interpreter's teardown, and Qt aborts there on a thread still running.

**On Windows that is `TerminateProcess` on the process itself, not `os._exit`.** There
`os._exit` is the C runtime's `_exit`, which is `ExitProcess`: it kills the other threads
and then runs, on the exiting thread, the process-detach code of every loaded DLL (Qt's
and SDL's among them) and the fibre-local-storage callbacks. Measured on yulon-win11
2026-10-11 after "Quit anyway" during a restore: the quit path reached `os._exit` about
15 ms after the press, and the process then stayed alive 3.8 s, 10.7 s and (T690's live
proof) about 40 s, with one thread left in user-mode and ALPC waits; four later runs on
the same box ended in under 0.3 s. The window was gone all that time, so a second start
would have met a live instance. `TerminateProcess` runs none of that code; the kernel
still closes every handle, and the children Yu'lon started (a restore's `docker exec`)
carry on as they did under `os._exit`.

Nothing is lost by skipping it: nothing Yu'lon writes is held in a DLL's buffers (the log
and Python's own streams are flushed before this), and `os._exit` already skipped every
Python atexit hook.

Elsewhere `os._exit` is `_exit(2)`, which runs no library code on the way out.
"""

from __future__ import annotations

import os
import sys
from typing import NoReturn


def end_now(code: int) -> NoReturn:
    """End this process with exit code `code`, running no exit-time code of any library."""
    if sys.platform == "win32":
        _terminate_self(code)
        # Only reached if TerminateProcess refused, which it does not do for the
        # caller's own pseudo-handle; os._exit still ends the process, if slowly.
    os._exit(code)


def _terminate_self(code: int) -> bool:
    """`TerminateProcess(GetCurrentProcess(), code)`; returns only if it was refused."""
    import ctypes

    # `getattr`: `ctypes.WinDLL` exists only on Windows (see `platform._mapped_network_drive`).
    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.TerminateProcess.restype = ctypes.c_int
    return bool(kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code & 0xFFFFFFFF))
