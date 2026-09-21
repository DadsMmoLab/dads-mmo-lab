"""The helper that replaces the install after this process has quit.

**The running app never renames its own files, and that is a decision rather
than caution.** A one-dir build is a live process reading its own folder: Qt
plugins, `base_library.zip` and every lazily imported module are opened when
they are first needed, which on this app is minutes into a session. Renaming
the folder under it leaves those reads pointing into the NEW tree through the
old path on Linux, and refused outright on Windows, where an open file cannot
be renamed at all. So the app stages everything, writes a small script, starts
it detached, and quits through its normal close path; the script waits for the
pid, renames, relaunches, and deletes itself.

**Paths reach the script as ARGUMENTS.** Nothing is interpolated into the
script's text — not once, not "just the pid". A Windows install folder can hold
a `&`, a `%VAR%` and a quote, and a Linux one can hold `$(…)`; as `argv` they
are five strings no shell parses. `test_a_hostile_folder_name_reaches_argv_…`
asserts both directions: the whole path in the argv LIST, and none of it in the
script text.

PowerShell and not `.cmd` on Windows, for the same reason: `%` and `&` in a
path cannot be quoted safely in a batch file. And PowerShell **by its absolute
path under `%SystemRoot%`**, never by name — a bare `powershell.exe` is
whatever the PATH resolves it to.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yulon import platform, runner
from yulon.log import get_logger
from yulon.selfupdate.detect import Install
from yulon.selfupdate.fetch import UpdateError
from yulon.selfupdate.stage import sibling

logger = get_logger(__name__)

WAIT_TICKS = 600
TICK_SECONDS = "0.2"
"""How long the helper waits for the app to exit: 600 x 0.2 s = 120 seconds.

Past that it does NOT swap. A process that is still alive two minutes after
being asked to close is one whose files are still open, and renaming them is
the one thing that could lose the install; relaunching what is already there
costs nothing.
"""

POSIX_HELPER = f"""#!/bin/sh
# Yu'lon self-update helper. Every path arrives as an argument; this text holds none.
# $1 pid of the app  $2 install  $3 staged build  $4 where the old one goes  $5 what to start
pid="$1"
target="$2"
new="$3"
old="$4"
launch="$5"
i=0
swap=1
while kill -0 "$pid" 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -gt {WAIT_TICKS} ]; then
    swap=0
    break
  fi
  sleep {TICK_SECONDS}
done
if [ "$swap" -eq 1 ]; then
  rm -rf -- "$old"
  if mv -- "$target" "$old"; then
    if ! mv -- "$new" "$target"; then
      mv -- "$old" "$target"
    fi
  fi
fi
rm -f -- "$0"
exec "$launch"
"""
"""The POSIX half, which is `sh` and not `bash`: an AppImage may run on a box with no bash.

`rm -f -- "$0"` before the `exec`: the shell has the script open on a
descriptor, so unlinking it does not stop it being read, and this is the only
moment at which the helper can remove itself.

The `exec` is last on every path, the 120-second give-up included, so a helper
that decided not to swap still puts the app back in front of the user.
"""

POWERSHELL_HELPER = """param(
  [int]$ProcId, [string]$Target, [string]$New, [string]$Old, [string]$Launch
)
# Yu'lon self-update helper. Every path arrives as a parameter; this text holds none.
try { Wait-Process -Id $ProcId -Timeout 120 -ErrorAction SilentlyContinue } catch {}
if (-not (Get-Process -Id $ProcId -ErrorAction SilentlyContinue)) {
  if (Test-Path -LiteralPath $Old) {
    Remove-Item -LiteralPath $Old -Recurse -Force -ErrorAction SilentlyContinue
  }
  $moved = $false
  for ($i = 0; $i -lt 30 -and -not $moved; $i++) {
    try { Move-Item -LiteralPath $Target -Destination $Old -ErrorAction Stop; $moved = $true }
    catch { Start-Sleep -Seconds 1 }
  }
  if ($moved) {
    try { Move-Item -LiteralPath $New -Destination $Target -ErrorAction Stop }
    catch { Move-Item -LiteralPath $Old -Destination $Target }
  }
}
Start-Process -FilePath $Launch
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
"""
"""The Windows half. `-LiteralPath` everywhere: `-Path` globs, and `[` is legal in a folder name.

The rename is retried thirty times at one second because **Windows releases a
process's file handles late**: the app has exited and its one-dir folder can
still be locked for a moment by the loader, by Explorer's thumbnailer or by an
antivirus scanner. This is the part of the package that has NOT been measured
from the Linux side — see the ticket's gate: what a detached PowerShell does
when its parent exits, and how long the folder really stays locked, are
Windows facts and this file only states the intent.
"""

POWERSHELL_UNDER_SYSTEMROOT = "\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
"""Where Windows keeps PowerShell 5.1, relative to `%SystemRoot%`."""

_POWERSHELL_FLAGS = (
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy",
    "Bypass",
    "-WindowStyle",
    "Hidden",
    "-File",
)
"""`-NoProfile` so a user's profile cannot fail the swap; `Bypass` for THIS script only."""


def powershell_path() -> str:
    """The absolute `powershell.exe`, from `%SystemRoot%`. **Never resolved through PATH.**"""
    return (os.environ.get("SystemRoot") or "C:\\Windows") + POWERSHELL_UNDER_SYSTEMROOT


@dataclass(frozen=True)
class SwapPlan:
    """Everything the swap needs: the script that was written, and how to start it."""

    script: Path
    argv: list[str]
    """`argv[0]` is the interpreter. Every path in here is one whole element."""


def plan_swap(
    install: Install,
    staged: Path,
    *,
    pid: int,
    script_dir: Path,
    platform_id: str | None = None,
) -> SwapPlan:
    """Write the helper for this install and return how to start it. Nothing is started here.

    Written but not run, so the caller can do the one thing that matters about
    the ordering: start the helper only once it knows the window really is
    going to close.
    """
    target = install.target
    if not install.can_swap or target is None:
        raise UpdateError("Yu'lon cannot replace this install by itself.")
    which = platform_id or platform.detect()
    launch = target / install.executable if install.executable else target
    values = [str(pid), str(target), str(staged), str(sibling(target, ".old")), str(launch)]
    if which == "windows":
        script = script_dir / f"yulon-update-{pid}.ps1"
        _write_helper(script, POWERSHELL_HELPER)
        argv = [powershell_path(), *_POWERSHELL_FLAGS, str(script), *values]
    else:
        script = script_dir / f"yulon-update-{pid}.sh"
        _write_helper(script, POSIX_HELPER)
        argv = ["/bin/sh", str(script), *values]
    logger.info(f"self-update: helper written to {script}")
    return SwapPlan(script, argv)


def _write_helper(script: Path, text: str) -> None:
    """Write the script and make it the caller's alone (0o700).

    `newline="\\n"` on purpose: on Windows this is a PowerShell file and CRLF
    is fine either way, but a POSIX `sh` script whose shebang line ends `\\r`
    is refused by the kernel with a confusing error, and this app is
    cross-compiled in the sense that the same code writes both.
    """
    try:
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(text, encoding="utf-8", newline="\n")
        os.chmod(script, 0o700)
    except OSError as exc:
        raise UpdateError(f"Yu'lon could not write its update helper: {exc}") from exc


Spawn = Callable[[list[str]], None]
"""How the helper is started. A seam, so a test never starts one it cannot join."""


def _spawn_detached(argv: list[str]) -> None:
    """Start the helper so that it OUTLIVES this process.

    POSIX: `start_new_session=True` puts it in a session of its own, so the
    terminal's SIGHUP and this process's exit do not reach it. Windows:
    `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`, fetched
    off `subprocess` at call time exactly as `runner.creationflags()` does,
    because those names do not exist on POSIX and this file is type-checked for
    both.

    `runner.child_env()`, so the frozen parent's `LD_LIBRARY_PATH` does not
    reach the shell that will start the new build.
    """
    posix = os.name == "posix"
    flags = 0
    if not posix:  # pragma: no cover - Windows only; the gate measures this
        for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
            flags |= int(getattr(subprocess, name, 0))
    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        env=runner.child_env(),
        start_new_session=posix,
        creationflags=flags,
    )


def start_helper(plan: SwapPlan, *, spawn: Spawn = _spawn_detached) -> None:
    """Start the helper. **Call this only once the window really is going to close.**

    If the close is refused after all, the helper waits out its 120 seconds and
    then relaunches what is already running rather than renaming anything — so
    the worst case is a second copy of the app, not a lost install. That is the
    fallback and not the plan; `main.py` asks `close_refusal()` first.
    """
    logger.info(f"self-update: starting the helper as {plan.argv[0]}")
    spawn(plan.argv)
