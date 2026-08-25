"""Tests for `yulon.runner` (roadmap Phase 1.1)."""

from __future__ import annotations

import io
import subprocess
import threading
import time
from pathlib import Path

import pytest

from yulon import runner
from yulon.catalog.installer import bash_available
from yulon.runner import creationflags, run, stream

# Not just `which bash`: on Windows that finds the Store alias for WSL, which fails
# with execvpe(/bin/bash) when no distro is installed (Windows test VM, 2026-08-21).
needs_bash = pytest.mark.skipif(
    not bash_available(), reason="no bash that can run a script on this machine"
)


def _python_cmd(script: str) -> list[str]:
    """Build a python -c command list that runs the given script."""
    import sys

    return [sys.executable, "-c", script]


def test_creationflags_no_window_on_windows_zero_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`CREATE_NO_WINDOW` on native Windows, 0 off it — the console-flash guard (6.3).

    The one place to ask, so a flag applied to some spawn sites but not others
    cannot leave a window that flashes anyway. `sys.platform` is monkeypatched,
    not the stdlib, so the `subprocess.CREATE_NO_WINDOW` attribute (which does
    not exist on POSIX) is only consulted behind the platform check.
    """
    monkeypatch.setattr(runner.sys, "platform", "win32")
    monkeypatch.setattr(runner.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert creationflags() == 0x08000000

    monkeypatch.setattr(runner.sys, "platform", "linux")
    assert creationflags() == 0


def test_run_captures_stdout() -> None:
    """`run()` captures stdout as a string."""
    proc = run(_python_cmd("print('hello')"))
    assert proc.returncode == 0
    assert proc.stdout == "hello\n"


def test_run_captures_stderr_separately() -> None:
    """`run()` keeps stderr distinct from stdout."""
    proc = run(_python_cmd("import sys; print('out'); print('err', file=sys.stderr)"))
    assert proc.returncode == 0
    assert "out" in proc.stdout
    assert "err" in proc.stderr


def test_run_does_not_raise_on_nonzero_exit() -> None:
    """`run()` returns the process; it does not raise on non-zero exit."""
    proc = run(_python_cmd("import sys; sys.exit(3)"))
    assert proc.returncode == 3


def test_run_raises_oserror_on_missing_executable() -> None:
    """`run()` propagates OSError if the executable can't be found/started."""
    with pytest.raises(OSError):
        run(["this-command-should-not-exist-anywhere-1234"])


def test_stream_yields_stdout_lines() -> None:
    """`stream()` yields each stdout line without trailing newline."""
    lines = list(stream(_python_cmd("print('a'); print('b')")))
    assert lines == ["a", "b"]


def test_stream_yields_stderr_lines_after_stdout() -> None:
    """`stream()` surfaces stderr, but only after stdout is exhausted.

    This is a documented behavior, not real-time interleaving: stderr is
    drained on a background thread purely to avoid pipe-buffer deadlock, then
    appended as a block once the process exits.
    """
    script = "import sys; print('o1'); print('e1', file=sys.stderr); print('o2')"
    lines = list(stream(_python_cmd(script)))
    assert lines == ["o1", "o2", "e1"]


def test_stream_can_interleave_stderr_live_for_a_command_whose_output_is_stderr() -> None:
    """`merge_stderr` puts both streams on one pipe, in the child's own order.

    Written for the native install engine's build stage (roadmap 6.2): BuildKit
    writes ALL of its progress to stderr, so the default ordering above turns a
    two-to-four-hour compile into a log panel that stays blank until the build
    has already finished.
    """
    script = (
        "import sys\n"
        "print('o1'); sys.stdout.flush()\n"
        "print('e1', file=sys.stderr); sys.stderr.flush()\n"
        "print('o2'); sys.stdout.flush()\n"
    )
    assert list(stream(_python_cmd(script), merge_stderr=True)) == ["o1", "e1", "o2"]
    # And the default is unchanged, because every other caller reads a command
    # whose stderr is an error report rather than its output.
    assert list(stream(_python_cmd(script))) == ["o1", "o2", "e1"]


def test_stream_still_reports_a_failure_with_the_streams_merged() -> None:
    with pytest.raises(subprocess.CalledProcessError):
        list(stream(_python_cmd("import sys; sys.exit(5)"), merge_stderr=True))


def test_stream_raises_on_nonzero_exit() -> None:
    """`stream()` raises CalledProcessError if the command exits non-zero."""
    with pytest.raises(subprocess.CalledProcessError):
        list(stream(_python_cmd("import sys; sys.exit(5)")))


def test_stream_raises_oserror_on_missing_executable() -> None:
    """`stream()` propagates OSError if the executable can't be found/started."""
    with pytest.raises(OSError):
        list(stream(["this-command-should-not-exist-anywhere-1234"]))


def test_stream_respects_cwd(tmp_path: Path) -> None:
    """`stream()` runs the child in the requested working directory."""
    (tmp_path / "marker.txt").write_text("hi", encoding="utf-8")
    script = "import pathlib; print(pathlib.Path('marker.txt').exists())"
    lines = list(stream(_python_cmd(script), cwd=tmp_path))
    assert lines == ["True"]


def test_stream_does_not_deadlock_on_large_stderr_payload() -> None:
    """A stderr payload bigger than the OS pipe buffer must not hang stream().

    This is the actual regression test for the background stderr-reader
    thread: without it, a child writing enough to stderr to fill the pipe
    buffer (commonly ~64KB) while nobody reads it would deadlock, because the
    child blocks on the full pipe and the parent blocks waiting for the child.
    """
    # 200,000 short stderr lines is comfortably larger than any common pipe
    # buffer size, on every platform this runs on.
    script = (
        "import sys\n"
        "for i in range(200_000):\n"
        "    print(i, file=sys.stderr)\n"
        "print('done')\n"
    )
    start = time.monotonic()
    lines = list(stream(_python_cmd(script)))
    elapsed = time.monotonic() - start

    assert lines[0] == "done"
    assert len(lines) == 200_001  # 1 stdout line + 200,000 stderr lines
    assert elapsed < 30  # generous bound; a real deadlock hangs indefinitely


def test_stream_terminates_child_on_early_generator_abandonment() -> None:
    """Abandoning a stream() generator early must not leak the child process.

    Regression test: a caller that `break`s out of a `for line in stream(...)`
    loop (or otherwise never exhausts the generator) must not leave the child
    process running indefinitely, and must not hang waiting for it.
    """
    # A child that would run "forever" if not terminated by stream()'s cleanup.
    script = "import sys, time\n" "print('started')\n" "sys.stdout.flush()\n" "time.sleep(60)\n"
    gen = stream(_python_cmd(script))
    first_line = next(gen)
    assert first_line == "started"

    start = time.monotonic()
    gen.close()  # triggers GeneratorExit at the suspended yield
    elapsed = time.monotonic() - start

    # Cleanup (terminate + join) must complete promptly, not wait for the
    # child's full 60-second sleep.
    assert elapsed < 10


@needs_bash
def test_interact_cancel_interrupts_a_child_stuck_on_a_prompt(tmp_path: Path) -> None:
    """An unanswered no-newline prompt used to block forever; `cancel` gets out of it."""
    script = tmp_path / "stuck.sh"
    script.write_text(
        "#!/bin/bash\necho hello\necho -n 'A question no rule answers: '\nread -r answer\n",
        encoding="utf-8",
    )
    cancel = threading.Event()
    lines: list[str] = []
    started = time.monotonic()
    threading.Timer(1.0, cancel.set).start()
    for line in runner.interact(
        ["bash", str(script)], respond=lambda _line: None, quiet_seconds=0.2, cancel=cancel
    ):
        lines.append(line)
    assert time.monotonic() - started < 20  # would never return before this fix
    assert lines and lines[0] == "hello"


# ---------------------------------------------------- the frozen library path
# The bug these cover shipped and could not be seen from here: it exists ONLY
# in the packaged app, and everything in this file runs from source.


def _frozen(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    """Pretend to be the PyInstaller bundle, with the environment it creates."""
    monkeypatch.setattr(runner.sys, "frozen", True, raising=False)
    monkeypatch.setattr(runner.os, "environ", dict(env))


def test_running_from_source_leaves_the_child_environment_alone() -> None:
    """None means "inherit", which is what every caller has always got.

    The fix must not change the unfrozen case at all: the bug does not exist
    there, and a source checkout that suddenly spawns children with a rebuilt
    environment would be a new bug wearing the old one's clothes.
    """
    assert runner.child_env() is None
    assert runner.child_env({"A": "1"}) == {"A": "1"}


@pytest.mark.parametrize(
    "var", ["LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "LIBPATH"]
)
def test_a_frozen_launcher_hands_back_the_users_own_loader_path(
    monkeypatch: pytest.MonkeyPatch, var: str
) -> None:
    """PyInstaller saves the pre-launch value; the child must get THAT one.

    Parametrised across all four because the bug is one bug and a fix applied
    to `LD_LIBRARY_PATH` alone is a fix on Linux only - macOS carries the same
    breakage under two different names.
    """
    _frozen(monkeypatch, **{var: "/bundle/_internal", f"{var}_ORIG": "/opt/mine/lib"})
    got = runner.child_env()
    assert got is not None
    assert got[var] == "/opt/mine/lib"
    assert f"{var}_ORIG" not in got


@pytest.mark.parametrize("orig", ["", None])
def test_a_frozen_launcher_unsets_a_path_the_user_never_had(
    monkeypatch: pytest.MonkeyPatch, orig: str | None
) -> None:
    """No _ORIG, or an empty one, means it was unset before the bundle set it.

    Unset is not the same as empty: an empty `LD_LIBRARY_PATH` means "look in
    the current directory" to some loaders, so leaving one behind would swap a
    library-path bug for a subtler one. Measured inside the real AppImage:
    `LD_LIBRARY_PATH_ORIG` is present and EMPTY there, which is exactly this
    case and the one that bites (2026-08-24).
    """
    env = {"LD_LIBRARY_PATH": "/bundle/_internal"}
    if orig is not None:
        env["LD_LIBRARY_PATH_ORIG"] = orig
    _frozen(monkeypatch, **env)
    got = runner.child_env()
    assert got is not None
    assert "LD_LIBRARY_PATH" not in got
    assert "LD_LIBRARY_PATH_ORIG" not in got


def test_a_caller_supplied_environment_is_sanitised_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`platform._c_locale_env()` copies os.environ, so it carries the poison.

    A caller passing an environment is not opting out of this - it is usually
    os.environ plus one key, which is the exact shape that hands the bundle's
    libraries to a child while looking deliberate.
    """
    _frozen(monkeypatch)
    poisoned = {"LC_ALL": "C", "LD_LIBRARY_PATH": "/bundle/_internal"}
    got = runner.child_env(poisoned)
    assert got == {"LC_ALL": "C"}


def test_every_spawn_site_in_this_module_sanitises_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asked of the seam, not of the source.

    Grepping for `child_env(` would pass on a call that computes the right
    environment and then throws it away, and the defect being guarded against
    is precisely a spawn site that forgets. So each entry point is driven and
    the environment `subprocess` was actually handed is read back.
    """
    _frozen(monkeypatch, LD_LIBRARY_PATH="/bundle/_internal", PATH="/usr/bin")
    seen: list[dict[str, str] | None] = []

    class _Proc:
        returncode = 0
        pid = 1234

        def __init__(self, *a: object, **kw: object) -> None:
            seen.append(kw.get("env"))  # type: ignore[arg-type]
            # Real file objects: `stream()` asserts on both and iterates stdout,
            # so a double with None here fails for the wrong reason.
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def poll(self) -> int:
            return 0

        def kill(self) -> None:
            return None

    def fake_run(*a: object, **kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(kw.get("env"))  # type: ignore[arg-type]
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner.subprocess, "Popen", _Proc)

    runner.run(["true"])
    runner.run(["true"], env={"LD_LIBRARY_PATH": "/bundle/_internal", "X": "1"})
    list(runner.stream(["true"]))

    assert seen, "no spawn site was reached; this test is measuring nothing"
    for env in seen:
        assert env is not None, "a frozen spawn passed env=None and so inherited the bundle's path"
        assert "LD_LIBRARY_PATH" not in env, env
