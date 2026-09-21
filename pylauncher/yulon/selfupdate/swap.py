"""The helper that replaces the install's own entries after this process has quit.

**The running app never renames its own files, and that is a decision rather
than caution.** A one-dir build is a live process reading its own folder: Qt
plugins, `base_library.zip` and every lazily imported module are opened when
they are first needed, which on this app is minutes into a session. So the app
stages everything, writes a small script, starts it detached, and quits through
its normal close path; the script waits for the pid and then acts.

**It moves ENTRIES, never the install folder** (cold review 1). The folder the
executable sits in may be the player's Downloads folder, and renaming it cost a
measured `thesis.docx`. So the helper is given the names the build ships — and
only names the running build can be proved to have shipped — and moves them one
at a time into `<target>/.yulon-old/`, then the staged ones into place.

**Order is part of the contract.** The entries arrive with the executable
FIRST; the helper moves the current entries out in that order and brings the
staged ones in reversed. So the executable is the first thing to leave and the
last thing to arrive, and every failure is undone in the exact reverse of what
was done. A half-finished swap is therefore "no executable", never "the new
executable on the old libraries".

**Paths reach the script as ARGUMENTS.** Nothing is interpolated into the
script's text. A Windows folder can hold a `&`, a `%VAR%` and a quote, and a
Linux one can hold `$(…)`; as `argv` they are strings no shell parses. The two
work-directory names are constants the SCRIPTS spell themselves, so the only
paths either script can remove are `<target>/.yulon-new` and
`<target>/.yulon-old` under a target it has already validated.

**Every argument is validated before anything moves**: the target must be an
absolute directory, both work directories must carry this app's marker, and
every entry must be a single path segment. Nothing moves if any of that fails.

PowerShell and not `.cmd` on Windows: `%` and `&` in a path cannot be quoted
safely in a batch file. And PowerShell **by its absolute path under
`%SystemRoot%`**, never by name — a bare `powershell.exe` is whatever the PATH
resolves it to.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from yulon import platform, runner
from yulon.log import get_logger
from yulon.selfupdate import layout
from yulon.selfupdate.detect import Install, InstallKind
from yulon.selfupdate.fetch import UpdateError

logger = get_logger(__name__)

TICK_SECONDS = "0.2"
DEFAULT_TICKS = 600
"""How long the helper waits for the app to exit, as a NUMBER OF TICKS it is given.

600 x 0.2 s = 120 seconds by default. **It is an argument and not a constant in
the script text**, because until cold review 1 it was the latter and nothing
could test the give-up path: mutating the give-up branch to swap anyway left
all 21 swap tests green. A test now runs the real helper with a handful of
ticks against a process that stays alive.

Past the bound the helper does **nothing**: it does not swap, and it does not
relaunch either. A process still alive two minutes after being asked to close
is one whose files are still open — and it is evidently still running, so a
second copy beside it is the wrong answer to every question.
"""

POSIX_FOLDER_HELPER = f"""#!/bin/sh
# Yu'lon self-update helper (one-dir install). Every path arrives as an argument.
# $1 ticks  $2 pid  $3 target  $4 launch  $5.. the entries, executable FIRST
ticks="$1"
pid="$2"
target="$3"
launch="$4"
shift 4
new="$target/{layout.NEW_NAME}"
old="$target/{layout.OLD_NAME}"
marker="{layout.MARKER_NAME}"

# ---- validate before anything moves --------------------------------------
case "$target" in
  /*) ;;
  *) exit 64 ;;
esac
[ -d "$target" ] || exit 64
[ -d "$new" ] || exit 64
[ -d "$old" ] || exit 64
[ -f "$new/$marker" ] || exit 64
[ -f "$old/$marker" ] || exit 64
[ "$#" -ge 1 ] || exit 64
for e in "$@"; do
  case "$e" in
    ""|"."|".."|*/*|*\\\\*) exit 64 ;;
  esac
done

# ---- wait for the app ------------------------------------------------------
i=0
while kill -0 "$pid" 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -gt "$ticks" ]; then
    # Still running. Move nothing, start nothing, and leave the staged build
    # where it is for the next start to reuse or clean up.
    rm -f -- "$0"
    exit 75
  fi
  sleep {TICK_SECONDS}
done

# ---- move the current entries aside, executable first ----------------------
moved=""
ok=1
for e in "$@"; do
  if [ -e "$target/$e" ]; then
    if mv -- "$target/$e" "$old/$e"; then moved="$e $moved"; else ok=0; break; fi
  else
    moved="$e $moved"
  fi
done

# ---- bring the staged entries in, executable last --------------------------
placed=""
if [ "$ok" -eq 1 ]; then
  for e in $moved; do
    if mv -- "$new/$e" "$target/$e"; then placed="$e $placed"; else ok=0; break; fi
  done
fi

# ---- any failure is undone in the exact reverse of what was done -----------
if [ "$ok" -eq 0 ]; then
  for e in $placed; do mv -- "$target/$e" "$new/$e"; done
  for e in $moved; do
    [ -e "$old/$e" ] && mv -- "$old/$e" "$target/$e"
  done
fi

cd -- "$target" || exit 70
rm -f -- "$0"
exec "$launch"
"""
"""`sh` and not `bash`: an AppImage may run on a box with no bash.

`moved` and `placed` are built by PREPENDING, so iterating them walks the
reverse of the order they were filled in — which is what makes the rollback the
exact inverse of the work, and what makes the staged entries arrive with the
executable last.

An entry that is not in the target yet is recorded as moved without a `mv`, so
a build that gained an entry still installs and still rolls back cleanly.

`rm -f -- "$0"` before the `exec`: the shell has the script open on a
descriptor, so unlinking it does not stop it being read, and this is the only
moment at which the helper can remove itself.

`cd -- "$target"` before the exec, so the new build starts in the folder a
double-click would start it in — and, on Windows, so the helper itself is never
the process holding the install folder open.
"""

POSIX_FILE_HELPER = f"""#!/bin/sh
# Yu'lon self-update helper (AppImage). Every path arrives as an argument.
# $1 ticks  $2 pid  $3 target (the .AppImage file)
ticks="$1"
pid="$2"
target="$3"
new="$target{layout.NEW_NAME}"
old="$target{layout.OLD_NAME}"
marker="{layout.MARKER_NAME}"
entry="{layout.APPIMAGE_ENTRY}"

# ---- validate before anything moves --------------------------------------
case "$target" in
  /*) ;;
  *) exit 64 ;;
esac
[ -f "$target" ] || exit 64
[ -d "$new" ] || exit 64
[ -d "$old" ] || exit 64
[ -f "$new/$marker" ] || exit 64
[ -f "$old/$marker" ] || exit 64
[ -f "$new/$entry" ] || exit 64

# ---- wait for the app ------------------------------------------------------
i=0
while kill -0 "$pid" 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -gt "$ticks" ]; then
    rm -f -- "$0"
    exit 75
  fi
  sleep {TICK_SECONDS}
done

# ---- one entry, the same two moves a folder install makes ------------------
if mv -- "$target" "$old/$entry"; then
  if ! mv -- "$new/$entry" "$target"; then
    mv -- "$old/$entry" "$target"
  fi
fi
chmod 0755 -- "$target" 2>/dev/null
cd -- "$(dirname -- "$target")" || exit 70
rm -f -- "$0"
exec "$target"
"""
"""The AppImage half: one file replaced by one file, and its name kept.

The file keeps its old version number on purpose, so a desktop entry or a
shortcut pointing at it keeps working; the window title is what says the
version. Its work directories are marked directories beside it, exactly as a
folder install's are inside it, so `layout.discard_ours` is the only thing
that can ever delete either of them.
"""

POWERSHELL_FOLDER_HELPER = f"""param(
  [int]$Ticks, [int]$ProcId, [string]$Target, [string]$Launch,
  [Parameter(ValueFromRemainingArguments = $true)][string[]]$Entries
)
# Yu'lon self-update helper (one-dir install). Every path arrives as a parameter.
$ErrorActionPreference = 'Stop'
$New = Join-Path $Target '{layout.NEW_NAME}'
$Old = Join-Path $Target '{layout.OLD_NAME}'
$Marker = '{layout.MARKER_NAME}'

if (-not [System.IO.Path]::IsPathRooted($Target)) {{ exit 64 }}
if (-not (Test-Path -LiteralPath $Target -PathType Container)) {{ exit 64 }}
if (-not (Test-Path -LiteralPath (Join-Path $New $Marker) -PathType Leaf)) {{ exit 64 }}
if (-not (Test-Path -LiteralPath (Join-Path $Old $Marker) -PathType Leaf)) {{ exit 64 }}
if (-not $Entries -or $Entries.Count -lt 1) {{ exit 64 }}
foreach ($e in $Entries) {{
  if ([string]::IsNullOrEmpty($e) -or $e -eq '.' -or $e -eq '..') {{ exit 64 }}
  if ($e.Contains('/') -or $e.Contains('\\')) {{ exit 64 }}
}}

$deadline = (Get-Date).AddSeconds($Ticks * {TICK_SECONDS})
while (Get-Process -Id $ProcId -ErrorAction SilentlyContinue) {{
  if ((Get-Date) -gt $deadline) {{
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    exit 75
  }}
  Start-Sleep -Milliseconds 200
}}

function Move-One($from, $to) {{
  # Windows releases a process's handles late: the loader, Explorer's
  # thumbnailer and an antivirus scanner can all hold a file for a moment
  # after the process has gone. Thirty tries at a second is the bound.
  for ($i = 0; $i -lt 30; $i++) {{
    try {{ Move-Item -LiteralPath $from -Destination $to -ErrorAction Stop; return $true }}
    catch {{ Start-Sleep -Seconds 1 }}
  }}
  return $false
}}

$moved = @()
$placed = @()
$ok = $true
foreach ($e in $Entries) {{
  if (Test-Path -LiteralPath (Join-Path $Target $e)) {{
    if (Move-One (Join-Path $Target $e) (Join-Path $Old $e)) {{ $moved = ,$e + $moved }}
    else {{ $ok = $false; break }}
  }} else {{ $moved = ,$e + $moved }}
}}
if ($ok) {{
  foreach ($e in $moved) {{
    if (Move-One (Join-Path $New $e) (Join-Path $Target $e)) {{ $placed = ,$e + $placed }}
    else {{ $ok = $false; break }}
  }}
}}
if (-not $ok) {{
  foreach ($e in $placed) {{ Move-One (Join-Path $Target $e) (Join-Path $New $e) | Out-Null }}
  foreach ($e in $moved) {{
    if (Test-Path -LiteralPath (Join-Path $Old $e)) {{
      Move-One (Join-Path $Old $e) (Join-Path $Target $e) | Out-Null
    }}
  }}
}}

Start-Process -FilePath $Launch -WorkingDirectory $Target
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
"""
"""The Windows half. `-LiteralPath` everywhere: `-Path` globs, and `[` is legal in a folder name.

**Unmeasured from the Linux side, and the gate list says exactly what to look
at**: whether a detached PowerShell survives its parent's exit from Explorer
and from a shortcut, how long the one-dir folder's entries really stay locked
after the process goes, and whether `-ExecutionPolicy Bypass -File` runs a
script written to `%TEMP%` under the box's policy.
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
    entries: tuple[str, ...] = ()
    """The names the helper will replace, executable first. Empty for an AppImage."""


def plan_swap(
    install: Install,
    staged: Path,
    *,
    pid: int,
    script_dir: Path,
    entries: Sequence[str] = (),
    platform_id: str | None = None,
) -> SwapPlan:
    """Write the helper for this install and return how to start it. Nothing is started here.

    It also makes the marked `.yulon-old` the helper moves things into, because
    the helper refuses to touch anything unless BOTH work directories carry a
    marker — a directory the helper made itself would prove nothing.

    Written but not run, so the caller can do the one thing that matters about
    the ordering: start the helper only once it knows the window really is
    going to close.
    """
    target = install.target
    if not install.can_swap or target is None:
        raise UpdateError("Yu'lon cannot replace this install by itself.")
    which = platform_id or platform.detect()
    marker = layout.read_marker(install, layout.NEW_NAME)
    if marker is None:
        raise UpdateError("The staged update is not marked as Yu'lon's. Nothing was changed.")
    ticks = str(DEFAULT_TICKS)
    if install.kind is InstallKind.APPIMAGE:
        # One entry, under the fixed name the script spells itself.
        _make_the_backup_dir(install, marker, (layout.APPIMAGE_ENTRY,))
        script = _write(script_dir, f"yulon-update-{pid}", which, POSIX_FILE_HELPER, None)
        argv = _argv(which, script, [ticks, str(pid), str(target)])
        logger.info(f"self-update: helper written to {script} for the AppImage")
        return SwapPlan(script, argv, (layout.APPIMAGE_ENTRY,))
    named = tuple(entries)
    _make_the_backup_dir(install, marker, named)
    if not named or not all(layout.is_entry_name(name) for name in named):
        raise UpdateError("Yu'lon could not work out which files to replace.")
    launch = target / install.executable
    body = POSIX_FOLDER_HELPER if which != "windows" else POWERSHELL_FOLDER_HELPER
    script = _write(script_dir, f"yulon-update-{pid}", which, body, POWERSHELL_FOLDER_HELPER)
    argv = _argv(which, script, [ticks, str(pid), str(target), str(launch), *named])
    logger.info(f"self-update: helper written to {script} for {len(named)} entries")
    del staged  # the helper finds it by name under the target it validated
    return SwapPlan(script, argv, named)


def _make_the_backup_dir(install: Install, marker: layout.Marker, entries: Sequence[str]) -> None:
    """Create `.yulon-old` with a marker, so the helper's own check can pass.

    The helper refuses to touch anything unless BOTH work directories carry a
    marker — a directory the helper made itself would prove nothing — so this
    is where the backup directory comes from, and the marker it writes is what
    the FIRST START AFTER the swap reads to decide whether the swap finished
    (`cleanup.finish_previous_update`): it carries the version being installed
    and the entries that were supposed to land.
    """
    if not layout.discard_ours(install, layout.OLD_NAME):
        raise UpdateError(
            "There is already a .yulon-old in your Yu'lon folder and Yu'lon did not put it "
            "there. Move or rename it, then try again."
        )
    old = layout.work_dir(install, layout.OLD_NAME)
    try:
        old.mkdir(parents=True, exist_ok=True)
        layout.write_marker(
            install,
            layout.OLD_NAME,
            layout.Marker(
                role=layout.OLD_NAME,
                from_version=marker.from_version,
                to_version=marker.to_version,
                pid=marker.pid,
                stamp=layout.now(),
                entries=tuple(entries),
                state=layout.SWAPPING,
            ),
        )
    except OSError as exc:
        raise UpdateError(f"Yu'lon could not prepare the backup folder: {exc}") from exc


def _write(
    script_dir: Path, stem: str, which: str, posix_body: str, windows_body: str | None
) -> Path:
    """Write the helper script, 0o700, with LF endings. Returns its path."""
    text = windows_body if (which == "windows" and windows_body is not None) else posix_body
    script = script_dir / (f"{stem}.ps1" if which == "windows" else f"{stem}.sh")
    try:
        script.parent.mkdir(parents=True, exist_ok=True)
        # `newline="\n"`: a POSIX `sh` script whose shebang line ends `\r` is
        # refused by the kernel with a confusing error, and the same code
        # writes both scripts.
        script.write_text(text, encoding="utf-8", newline="\n")
        # 0o700: it is about to be run with this app's own install folder as an
        # argument, so a world-writable copy of it in a shared temp directory
        # is a way to have Yu'lon replace itself with something else.
        os.chmod(script, 0o700)
    except OSError as exc:
        raise UpdateError(f"Yu'lon could not write its update helper: {exc}") from exc
    return script


def _argv(which: str, script: Path, values: list[str]) -> list[str]:
    if which == "windows":
        return [powershell_path(), *_POWERSHELL_FLAGS, str(script), *values]
    return ["/bin/sh", str(script), *values]


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

    `cwd` is the SCRIPT's own directory and never the install folder: on
    Windows a process whose working directory is a folder holds that folder
    open, and the whole point of this helper is to move things inside it.

    `runner.child_env()`, so the frozen parent's `LD_LIBRARY_PATH` does not
    reach the shell that will start the new build.
    """
    posix = os.name == "posix"
    flags = 0
    if not posix:  # pragma: no cover - Windows only; the gate measures this
        for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
            flags |= int(getattr(subprocess, name, 0))
    script = Path(argv[1] if argv[0].endswith("sh") else argv[-1])
    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        cwd=str(script.parent if script.parent.is_dir() else Path.cwd()),
        env=runner.child_env(),
        start_new_session=posix,
        creationflags=flags,
    )


def start_helper(plan: SwapPlan, *, spawn: Spawn = _spawn_detached) -> None:
    """Start the helper. **Call this only once the window really is going to close.**

    `main.py` asks `close_refusal()` twice — before the update starts, and
    again immediately before this call — because the work in between takes
    minutes and a database import can begin in them. If the close were refused
    after the helper had started, the helper would wait out its bound and then
    do nothing at all, which is the give-up path this design chose over
    launching a second copy beside the running one.
    """
    logger.info(f"self-update: starting the helper as {plan.argv[0]}")
    spawn(plan.argv)
