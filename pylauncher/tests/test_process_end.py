"""The forced exit ends the process at once, on Windows too (T709).

Measured on yulon-win11 2026-10-11 (T690's live proof, then T709): after "Quit anyway"
Yu'lon's own quit path reached `os._exit` within ~15 ms, and the process then stayed
alive for 3.8 s, 10.7 s and ~40 s, with one thread left in user-mode waits. On Windows
`os._exit` is the C runtime's `_exit`, which is `ExitProcess`: the other threads are
killed, and then the exiting thread runs every loaded DLL's process-detach code and its
fibre-local-storage callbacks before the process ends. `TerminateProcess` on the process
itself runs none of that.

The exit-time hook these tests arm is real native code on that path: a fibre-local-storage
callback pointed at `kernel32!Sleep`, with the delay as its value. `ExitProcess` calls it on
the way out; `TerminateProcess` does not (measured on yulon-win11 with `flsprobe.py`: 4.10 s
against 0.11 s). Each Windows test first shows, in the same run, that `os._exit` DOES wait
for it, so a Windows that stopped calling the callback could not make the fix look proven.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import main
from yulon import process_end

PYLAUNCHER = Path(main.__file__).parent

HOOK_MS = 5000
"""How long the armed exit-time hook holds `ExitProcess`."""

QUICK_SECONDS = 2.5
"""What the forced exit may take from the call to the parent seeing the child gone.

Half the hook: an exit that ran the hook cannot come in under it, and a loaded CI box
still has two seconds for the child to be reaped.
"""

_ARM_THE_HOOK = """\
import ctypes, os, sys, time
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.FlsAlloc.restype = ctypes.c_uint32
k.FlsAlloc.argtypes = [ctypes.c_void_p]
k.FlsSetValue.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
index = k.FlsAlloc(ctypes.cast(k.Sleep, ctypes.c_void_p).value)
assert index != 0xFFFFFFFF, ctypes.get_last_error()
assert k.FlsSetValue(index, {hook_ms})
"""

_HOW = {
    "os._exit": "os._exit(7)",
    "end_now": "from yulon import process_end\nprocess_end.end_now(7)",
    # The real forced exit, as "Quit anyway" reaches it: the log is flushed and closed,
    # abandoned streams are ended, and main's own `_hard_exit` seam runs (no conftest
    # applies in a child).
    "main": (
        "import main\n"
        'main._leave_with_jobs_still_running(7, ["work Quit anyway cut off: a test"])'
    ),
}


def _run_child(how: str, tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], float]:
    """Run a child that arms the hook and leaves by `how`; answer it and the time from just
    before its exit call to the parent seeing it gone."""
    script = _ARM_THE_HOOK.format(hook_ms=HOOK_MS) + (
        'print(f"T709 leaving at {time.time()}", flush=True)\n' + _HOW[how] + "\n"
        'print("T709 still here", flush=True)\n'
    )
    env = dict(os.environ)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env.update({"PYTHONPATH": str(PYLAUNCHER), "APPDATA": str(home), "HOME": str(home)})
    env.update({"XDG_DATA_HOME": str(home), "QT_QPA_PLATFORM": "offscreen"})
    done = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PYLAUNCHER,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    ended = time.time()
    leaving = [line for line in done.stdout.splitlines() if line.startswith("T709 leaving at ")]
    assert leaving, f"the child never reached its exit:\n{done.stdout}\n{done.stderr}"
    return done, ended - float(leaving[0].removeprefix("T709 leaving at "))


def test_end_now_keeps_the_code_and_runs_no_atexit_hook(tmp_path: Path) -> None:
    """What `os._exit` was chosen for (T113) stays true: no interpreter teardown, same code.

    An atexit hook is the cheapest witness of a teardown: `sys.exit` would print it.
    """
    script = (
        "import atexit\n"
        'atexit.register(lambda: print("T709 atexit ran", flush=True))\n'
        "from yulon import process_end\n"
        "process_end.end_now(5)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PYLAUNCHER,
        env={**os.environ, "PYTHONPATH": str(PYLAUNCHER)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 5, f"{done.stdout}\n{done.stderr}"
    assert "T709 atexit ran" not in done.stdout


@pytest.mark.slow
@pytest.mark.skipif(sys.platform != "win32", reason="ExitProcess's detach work is Windows-only")
def test_os_exit_waits_for_an_exit_time_hook_and_end_now_does_not(tmp_path: Path) -> None:
    """The control first: `os._exit` sits out the hook. Then `end_now` leaves at once."""
    slow, slow_took = _run_child("os._exit", tmp_path)
    assert slow.returncode == 7, f"{slow.stdout}\n{slow.stderr}"
    assert slow_took >= HOOK_MS / 1000 * 0.9, (
        f"os._exit took {slow_took:.2f} s: the hook did not hold ExitProcess, "
        "so this box cannot show the difference"
    )

    quick, quick_took = _run_child("end_now", tmp_path)
    assert quick.returncode == 7, f"{quick.stdout}\n{quick.stderr}"
    assert "T709 still here" not in quick.stdout
    assert quick_took < QUICK_SECONDS, f"end_now took {quick_took:.2f} s:\n{quick.stderr}"


@pytest.mark.slow
@pytest.mark.skipif(sys.platform != "win32", reason="ExitProcess's detach work is Windows-only")
def test_the_forced_exit_from_main_does_not_wait_for_exit_time_hooks(tmp_path: Path) -> None:
    """Through main's real forced exit, the one "Quit anyway" takes (T690, T709).

    Measured before the fix: the log said "leaving without Qt's teardown" and the
    process stayed ~40 s longer. The control is the same hook under a plain `os._exit`.
    """
    slow, slow_took = _run_child("os._exit", tmp_path)
    assert slow_took >= HOOK_MS / 1000 * 0.9, f"the hook did not hold os._exit ({slow_took:.2f} s)"

    done, took = _run_child("main", tmp_path)
    report = f"exit {done.returncode} after {took:.2f} s\n{done.stdout}\n{done.stderr}"
    assert done.returncode == 7, report
    assert "T709 still here" not in done.stdout, report
    assert took < QUICK_SECONDS, report


def test_end_now_ends_through_terminate_process_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Windows branch, in-process on any platform: TerminateProcess on itself, with the code.

    `os._exit` is replaced so a fall-through is seen, not suffered; the child-process tests
    above prove the real call on Windows.
    """
    calls: list[str] = []

    class _Left(Exception):
        pass

    def _terminate(code: int) -> bool:
        calls.append(f"terminate {code}")
        raise _Left

    def _os_exit(code: int) -> None:
        calls.append(f"os._exit {code}")
        raise _Left

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(process_end, "_terminate_self", _terminate)
    monkeypatch.setattr(os, "_exit", _os_exit)
    with pytest.raises(_Left):
        process_end.end_now(4)
    assert calls == ["terminate 4"]


def test_a_refused_terminate_process_still_leaves_by_os_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If TerminateProcess answers instead of ending the process, `os._exit` still ends it."""
    calls: list[str] = []

    class _Left(Exception):
        pass

    def _os_exit(code: int) -> None:
        calls.append(f"os._exit {code}")
        raise _Left

    monkeypatch.setattr(sys, "platform", "win32")

    def _refused(code: int) -> bool:
        calls.append("refused")
        return False

    monkeypatch.setattr(process_end, "_terminate_self", _refused)
    monkeypatch.setattr(os, "_exit", _os_exit)
    with pytest.raises(_Left):
        process_end.end_now(6)
    assert calls == ["refused", "os._exit 6"]


_REAL_HARD_EXIT = main._hard_exit
"""main's seam as collection found it, before conftest's `_no_forced_exit` replaced it."""


def test_mains_forced_exit_seam_ends_through_end_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main._hard_exit` is the one door every forced exit takes; it must use `end_now`."""
    calls: list[str] = []

    class _Left(Exception):
        pass

    def _end_now(code: int) -> None:
        calls.append(f"end_now {code}")
        raise _Left

    def _os_exit(code: int) -> None:
        calls.append(f"os._exit {code}")
        raise _Left

    monkeypatch.setattr(process_end, "end_now", _end_now)
    monkeypatch.setattr(os, "_exit", _os_exit)
    with pytest.raises(_Left):
        _REAL_HARD_EXIT(3)
    assert calls == ["end_now 3"]
