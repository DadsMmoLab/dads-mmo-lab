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
DEFAULT_TICKS = 1800
"""How long the helper waits for the app to exit, as a NUMBER OF TICKS it is given.

1800 x 0.2 s = **six minutes**, and the number is read off the close path rather
than chosen. `main._stop_background_threads()` is what runs between the helper
starting and this process exiting, and its bounds add up:

    every controller tab's `shutdown()`      (its own joins)
    every log panel      `panel.wait(5000)`   5 s EACH
    the update thread    `thread.wait(8000)`  8 s
    every held job       `wait_all(8000)`     8 s

so a player with several tabs open and a log following in each can legitimately
take a minute or more to close. 120 seconds — what this was — is inside that
range, and a helper that gives up moves nothing and relaunches nothing: the
player would have closed Yu'lon for an update that then silently did not
happen. Six minutes is comfortably past the sum for any plausible number of
tabs, and the cost of it being too long is only that a HUNG app leaves a
waiting shell around for six minutes instead of two.

**It is an argument and not a constant in the script text**, because until cold
review 1 it was the latter and nothing could test the give-up path: mutating
the give-up branch to swap anyway left all 21 swap tests green. A test now runs
the real helper with a handful of ticks against a process that stays alive.

Past the bound the helper does **nothing**: it does not swap, and it does not
relaunch either. A process still alive six minutes after being asked to close
is one whose files are still open — and it is evidently still running, so a
second copy beside it is the wrong answer to every question.
"""

POSIX_FOLDER_HELPER = f"""#!/bin/sh
# Yu'lon self-update helper (one-dir install). Every path arrives as an argument.
# $1 ticks  $2 pid  $3 target  $4 launch  $5 n  then n entries executable-FIRST
# followed by the same n entries executable-LAST.
trap 'rm -f -- "$0"' EXIT
ticks="$1"
pid="$2"
target="$3"
launch="$4"
half="$5"
shift 5
new="$target/{layout.NEW_NAME}"
old="$target/{layout.OLD_NAME}"
marker="{layout.MARKER_NAME}"
stamp="$old/{layout.HELPER_STAMP}"

# ---- validate before anything moves --------------------------------------
case "$ticks" in ""|*[!0-9]*) exit 64 ;; esac
case "$pid" in ""|*[!0-9]*) exit 64 ;; esac
case "$half" in ""|*[!0-9]*) exit 64 ;; esac
[ "$ticks" -gt 0 ] || exit 64
[ "$pid" -gt 1 ] || exit 64
[ "$half" -gt 0 ] || exit 64
[ "$#" -eq "$((half * 2))" ] || exit 64
case "$target" in /*) ;; *) exit 64 ;; esac
[ -d "$target" ] || exit 64
[ -d "$new" ] || exit 64
[ -d "$old" ] || exit 64
[ -f "$new/$marker" ] || exit 64
[ -f "$old/$marker" ] || exit 64
for e in "$@"; do
  case "$e" in
    ""|.|..|-*|*/*|*[!-._A-Za-z0-9]*) exit 64 ;;
    "{layout.NEW_NAME}"|"{layout.OLD_NAME}"|"{layout.DOWNLOAD_NAME}"|"$marker") exit 64 ;;
  esac
done
# The two halves must name the same set, or the swap's own invariants mean
# nothing: phase 1 would move one set out and phase 2 bring another in.
i=0
for e in "$@"; do
  i=$((i + 1))
  [ "$i" -le "$half" ] || break
  found=0
  j=0
  for f in "$@"; do
    j=$((j + 1))
    [ "$j" -gt "$half" ] || continue
    [ "$f" = "$e" ] && found=1
  done
  [ "$found" -eq 1 ] || exit 64
done

# ---- say that we are alive, before waiting for anything --------------------
# The app does not close until this file exists. On the Windows gate of
# 2026-09-21 the helper was spawned and never ran, and the app closed anyway.
log="$old/{layout.HELPER_LOG}"
: > "$stamp" || exit 64
echo "started pid=$$ target=$target entries=$half" >> "$log"

# ---- wait for the app ------------------------------------------------------
i=0
while kill -0 "$pid" 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -gt "$ticks" ]; then
    # Still running. Move nothing, start nothing, and leave the staged build
    # where it is for the next start to reuse or clean up.
    echo "gave up: pid $pid still running after $ticks ticks" >> "$log"
    exit 75
  fi
  sleep {TICK_SECONDS}
done

# ---- move the current entries aside, executable FIRST ----------------------
# The two halves of "$@" are the same names in the two orders this needs. They
# are read with a counter rather than by shifting, so nothing is ever rebuilt
# into a string and nothing is ever word-split or glob-expanded.
ok=1
i=0
for e in "$@"; do
  i=$((i + 1))
  [ "$i" -le "$half" ] || break
  if [ -e "$target/$e" ]; then
    mv -- "$target/$e" "$old/$e" || {{ ok=0; echo "FAILED moving out $e" >> "$log"; break; }}
  fi
  echo "moved out $e" >> "$log"
done

# ---- bring the staged entries in, executable LAST --------------------------
if [ "$ok" -eq 1 ]; then
  i=0
  for e in "$@"; do
    i=$((i + 1))
    [ "$i" -gt "$half" ] || continue
    mv -- "$new/$e" "$target/$e" || {{ ok=0; echo "FAILED bringing in $e" >> "$log"; break; }}
    echo "brought in $e" >> "$log"
  done
fi

# ---- any failure is undone, and the filesystem says what to undo -----------
if [ "$ok" -eq 0 ]; then
  echo "rolling back" >> "$log"
  # Take the new entries back out, executable first: an entry that was placed
  # is the one that is no longer in the staging directory.
  i=0
  for e in "$@"; do
    i=$((i + 1))
    [ "$i" -le "$half" ] || break
    if [ ! -e "$new/$e" ] && [ -e "$target/$e" ]; then
      mv -- "$target/$e" "$new/$e"
    fi
  done
  # Put the old entries back, executable last.
  i=0
  for e in "$@"; do
    i=$((i + 1))
    [ "$i" -gt "$half" ] || continue
    if [ -e "$old/$e" ]; then
      mv -- "$old/$e" "$target/$e"
    fi
  done
fi

echo "relaunching $launch" >> "$log"
cd -- "$target" || exit 70
rm -f -- "$0"
exec "$launch"
"""
"""`sh` and not `bash`: an AppImage may run on a box with no bash.

**Nothing is ever built into a string** (cold review 2, S2). The previous
version collected the moved names into `moved="$e $moved"` and then iterated
`for e in $moved`, unquoted — so an entry called `my file` would have failed
the swap and left itself in `.yulon-old`, and an entry called `*` would have
expanded against the working directory. The entries now arrive TWICE, in the
two orders the swap needs, and each loop reads its half of `"$@"` with a
counter. `layout.is_entry_name` refuses whitespace and glob characters on the
Python side as well, and the `case` above is the same rule again in the shell.

**What is undone is read off the filesystem**, not off a list: an entry that
was placed is one that is no longer in `.yulon-new`, and an entry that was
moved aside is one that is now in `.yulon-old`.

The `trap` removes the script on every exit that is not the `exec` — a refused
argument list, the give-up path — and the `exec` is preceded by its own `rm`,
because `exec` replaces the process and an EXIT trap never runs.

`cd -- "$target"` before the exec, so the new build starts in the folder a
double-click would start it in.
"""

POSIX_FILE_HELPER = f"""#!/bin/sh
# Yu'lon self-update helper (AppImage). Every path arrives as an argument.
# $1 ticks  $2 pid  $3 target (the .AppImage file)
trap 'rm -f -- "$0"' EXIT
ticks="$1"
pid="$2"
target="$3"
new="$target{layout.NEW_NAME}"
old="$target{layout.OLD_NAME}"
marker="{layout.MARKER_NAME}"
entry="{layout.APPIMAGE_ENTRY}"
stamp="$old/{layout.HELPER_STAMP}"
log="$old/{layout.HELPER_LOG}"

# ---- validate before anything moves --------------------------------------
case "$ticks" in ""|*[!0-9]*) exit 64 ;; esac
case "$pid" in ""|*[!0-9]*) exit 64 ;; esac
[ "$ticks" -gt 0 ] || exit 64
[ "$pid" -gt 1 ] || exit 64
case "$target" in /*) ;; *) exit 64 ;; esac
[ -f "$target" ] || exit 64
[ -d "$new" ] || exit 64
[ -d "$old" ] || exit 64
[ -f "$new/$marker" ] || exit 64
[ -f "$old/$marker" ] || exit 64
[ -f "$new/$entry" ] || exit 64

# ---- say that we are alive, before waiting for anything --------------------
: > "$stamp" || exit 64
echo "started pid=$$ target=$target" >> "$log"

# ---- wait for the app ------------------------------------------------------
i=0
while kill -0 "$pid" 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -gt "$ticks" ]; then
    echo "gave up: pid $pid still running after $ticks ticks" >> "$log"
    exit 75
  fi
  sleep {TICK_SECONDS}
done

# ---- one entry, the same two moves a folder install makes ------------------
if mv -- "$target" "$old/$entry"; then
  echo "moved out $entry" >> "$log"
  if mv -- "$new/$entry" "$target"; then
    echo "brought in $entry" >> "$log"
  else
    echo "FAILED bringing in $entry; rolling back" >> "$log"
    mv -- "$old/$entry" "$target"
  fi
fi
chmod 0755 -- "$target" 2>/dev/null
echo "relaunching $target" >> "$log"
cd -- "$(dirname -- "$target")" || exit 70
rm -f -- "$0"
exec "$target"
"""
"""The AppImage half: one file replaced by one file, and its name kept.

The file keeps its old version number on purpose, so a desktop entry or a
shortcut pointing at it keeps working; the window title is what says the
version. Its work directories are marked directories beside it, exactly as a
folder install's are inside it, so `layout.discard_ours` is the only thing that
can ever delete either of them.

The `trap` removes the script on the refusal and give-up paths; the success
path removes it just before the `exec`, which an EXIT trap never survives.
"""

POWERSHELL_FOLDER_HELPER = f"""param(
  [int]$Ticks, [int]$ProcId, [string]$Target, [string]$Launch, [int]$Half,
  [Parameter(ValueFromRemainingArguments = $true)][string[]]$Entries
)
# Yu'lon self-update helper (one-dir install). Every path arrives as a parameter.
# $Entries holds the same names twice: executable-FIRST, then executable-LAST.
$ErrorActionPreference = 'Stop'
$New = Join-Path $Target '{layout.NEW_NAME}'
$Old = Join-Path $Target '{layout.OLD_NAME}'
$Marker = '{layout.MARKER_NAME}'
$Stamp = Join-Path $Old '{layout.HELPER_STAMP}'
$Log = Join-Path $Old '{layout.HELPER_LOG}'
$Reserved = @('{layout.NEW_NAME}', '{layout.OLD_NAME}', '{layout.DOWNLOAD_NAME}', $Marker)

function Say($text) {{
  try {{ Add-Content -LiteralPath $Log -Value $text -ErrorAction SilentlyContinue }} catch {{}}
}}
function Refuse() {{
  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
  exit 64
}}

# `[int]` refuses a non-numeric argument before this body runs at all:
# PowerShell fails the parameter binding and the script exits non-zero having
# executed none of it. These checks are for values that ARE integers and are
# still wrong.
if ($Ticks -le 0 -or $ProcId -le 1 -or $Half -le 0) {{ Refuse }}
if (-not [System.IO.Path]::IsPathRooted($Target)) {{ Refuse }}
if (-not (Test-Path -LiteralPath $Target -PathType Container)) {{ Refuse }}
if (-not (Test-Path -LiteralPath (Join-Path $New $Marker) -PathType Leaf)) {{ Refuse }}
if (-not (Test-Path -LiteralPath (Join-Path $Old $Marker) -PathType Leaf)) {{ Refuse }}
if (-not $Entries -or $Entries.Count -ne ($Half * 2)) {{ Refuse }}
foreach ($e in $Entries) {{
  if ([string]::IsNullOrWhiteSpace($e) -or $e -eq '.' -or $e -eq '..') {{ Refuse }}
  if ($e -notmatch '^[._A-Za-z0-9][-._A-Za-z0-9]*$') {{ Refuse }}
  if ($Reserved -contains $e) {{ Refuse }}
}}
$Forward = $Entries[0..($Half - 1)]
$Backward = $Entries[$Half..($Entries.Count - 1)]
# The two halves must name the same set, or phase 1 moves one set out and
# phase 2 brings another in.
foreach ($e in $Forward) {{ if ($Backward -notcontains $e) {{ Refuse }} }}
foreach ($e in $Backward) {{ if ($Forward -notcontains $e) {{ Refuse }} }}

# ---- say that we are alive, before waiting for anything --------------------
# The app does not close until this file exists. Measured on the Windows 11
# gate box, 2026-09-21: spawned with DETACHED_PROCESS this script never ran at
# all, and the app closed anyway.
try {{ New-Item -ItemType File -Path $Stamp -Force | Out-Null }} catch {{ Refuse }}
Say "started pid=$PID target=$Target entries=$Half"

$deadline = (Get-Date).AddSeconds($Ticks * {TICK_SECONDS})
while (Get-Process -Id $ProcId -ErrorAction SilentlyContinue) {{
  if ((Get-Date) -gt $deadline) {{
    Say "gave up: pid $ProcId still running"
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

$ok = $true
foreach ($e in $Forward) {{
  if (Test-Path -LiteralPath (Join-Path $Target $e)) {{
    if (Move-One (Join-Path $Target $e) (Join-Path $Old $e)) {{ Say "moved out $e" }}
    else {{ Say "FAILED moving out $e"; $ok = $false; break }}
  }}
}}
if ($ok) {{
  foreach ($e in $Backward) {{
    if (Move-One (Join-Path $New $e) (Join-Path $Target $e)) {{ Say "brought in $e" }}
    else {{ Say "FAILED bringing in $e"; $ok = $false; break }}
  }}
}}
if (-not $ok) {{
  Say "rolling back"
  foreach ($e in $Forward) {{
    if (-not (Test-Path -LiteralPath (Join-Path $New $e)) -and
        (Test-Path -LiteralPath (Join-Path $Target $e))) {{
      Move-One (Join-Path $Target $e) (Join-Path $New $e) | Out-Null
    }}
  }}
  foreach ($e in $Backward) {{
    if (Test-Path -LiteralPath (Join-Path $Old $e)) {{
      Move-One (Join-Path $Old $e) (Join-Path $Target $e) | Out-Null
    }}
  }}
}}

try {{
  Start-Process -FilePath $Launch -WorkingDirectory $Target
  Say "relaunched $Launch"
}} catch {{
  Say "FAILED relaunching $Launch : $_"
}}
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
"""
"""The Windows half. `-LiteralPath` everywhere: `-Path` globs, and `[` is legal in a folder name.

**Measured on a real Windows 11 box, 2026-09-21 (the lead's gate).** The script
itself is good: run by hand with
`powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <ps1>
5 999999 "<target with a space and an &>" "<target>\\yulon.exe" 2 yulon.exe
_internal _internal yulon.exe` it exited 0, swapped both entries, left the old
build in `.yulon-old`, and deleted itself. So on that box: `-ExecutionPolicy
Bypass -File` from `%TEMP%` runs, `[int]` and `ValueFromRemainingArguments`
bind the doubled list, a path with a space and an `&` survives as a parameter,
and `Move-Item` of the executable and `_internal` works once the app is gone.

What failed there was the SPAWN, not this file — see `_spawn_detached`.

`Refuse` removes the script on every argument refusal: the previous version
left a `.ps1` in `%TEMP%` on each of those paths.
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
    body: str = ""
    """The script's text, so `ensure_script()` can write it again at the moment of use.

    The body is a module constant and holds no path, so carrying it here costs
    nothing and cannot carry anything of the player's with it.
    """


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
        script, body = _write(script_dir, f"yulon-update-{pid}", which, POSIX_FILE_HELPER)
        argv = _argv(which, script, [ticks, str(pid), str(target)])
        logger.info(f"self-update: helper written to {script} for the AppImage")
        return SwapPlan(script, argv, (layout.APPIMAGE_ENTRY,), body)
    named = tuple(entries)
    if not named or not all(layout.is_entry_name(name) for name in named):
        raise UpdateError("Yu'lon could not work out which files to replace.")
    _make_the_backup_dir(install, marker, named)
    launch = target / install.executable
    script, body = _write(script_dir, f"yulon-update-{pid}", which, POSIX_FOLDER_HELPER)
    # **The entries go twice, in the two orders the swap needs**: the current
    # ones are moved aside executable-FIRST and the staged ones are brought in
    # executable-LAST, so the executable is never present beside libraries of
    # the other version. Reversing a list inside POSIX `sh` cannot be done
    # without rebuilding it into a string, and a string is word-split — which
    # is the defect this avoids by doing the reversing HERE, where lists are
    # lists (cold review 2, S2).
    values = [ticks, str(pid), str(target), str(launch), str(len(named))]
    argv = _argv(which, script, [*values, *named, *reversed(named)])
    logger.info(f"self-update: helper written to {script} for {len(named)} entries")
    del staged  # the helper finds it by name under the target it validated
    return SwapPlan(script, argv, named, body)


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
            f"There is a {layout.OLD_NAME} folder beside Yu'lon that this copy cannot vouch "
            "for — it was left by an earlier update, or by another copy of Yu'lon, and Yu'lon "
            "will not delete a folder it cannot prove it made. Move or rename it, then try "
            "again."
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


def _write(script_dir: Path, stem: str, which: str, posix_body: str) -> tuple[Path, str]:
    """Write the helper script, 0o700, with LF endings. Returns `(path, text)`."""
    text = POWERSHELL_FOLDER_HELPER if which == "windows" else posix_body
    script = script_dir / (f"{stem}.ps1" if which == "windows" else f"{stem}.sh")
    _write_body(script, text)
    return script, text


def _write_body(script: Path, text: str) -> None:
    """The write itself, so `ensure_script()` can repeat it without rebuilding a plan."""
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


def _argv(which: str, script: Path, values: list[str]) -> list[str]:
    if which == "windows":
        return [powershell_path(), *_POWERSHELL_FLAGS, str(script), *values]
    return ["/bin/sh", str(script), *values]


def script_in(argv: list[str]) -> Path:
    """The helper script inside an argv, **by position and not by guessing**.

    `argv[-1]` was used for the Windows shape and is now an entry NAME, because
    the entries are trailing arguments — so `_spawn_detached` was setting the
    helper's working directory to something that is not a directory at all
    (cold review 2). POSIX puts the script at index 1; PowerShell puts it
    immediately after `-File`.
    """
    if "-File" in argv:
        return Path(argv[argv.index("-File") + 1])
    return Path(argv[1])


def staging_is_intact(install: Install, plan: SwapPlan) -> str | None:
    """Is the staged build still there, still ours, and still complete? None = yes.

    **Asked immediately before the helper is started** (round 3, F1), because
    minutes can pass between staging and pressing: a second copy of Yu'lon can
    start and clear the work dirs, a cleaner can empty `/tmp`, or the player
    can delete the folders themselves. Measured in that review: the app closed
    itself for a helper that then exited 64, and nothing reopened it.

    It re-reads the markers rather than trusting the plan, so a directory that
    has been replaced by something unmarked answers here and not in the shell.
    """
    for name in (layout.NEW_NAME, layout.OLD_NAME):
        if layout.read_marker(install, name) is None:
            logger.info(f"self-update: {name} is gone or is no longer marked as ours")
            return "the files it had prepared are gone"
    staged = layout.work_dir(install, layout.NEW_NAME)
    for entry in plan.entries:
        if not (staged / entry).exists():
            logger.info(f"self-update: the staged {entry} is gone")
            return f"part of it ({entry}) is gone"
    return None


def ensure_script(plan: SwapPlan) -> bool:
    """Write the helper script again, now, just before it is used. False if it cannot be.

    **Written at the last moment rather than trusted from minutes ago** (round
    3, F1). It lives in the temporary directory, which on a long-running
    desktop is swept by the system: a plan made before a database import and
    started after it could name a script that no longer exists, and the app
    would close for nothing.

    Rewriting is cheap — it is twenty lines — and it is the same bytes either
    way, because the body is a module constant and the paths are arguments.
    """
    try:
        _write_body(plan.script, plan.body)
    except UpdateError as exc:
        logger.info(f"self-update: the helper script could not be written: {exc}")
        return False
    return True


Spawn = Callable[[list[str]], None]
"""How the helper is started. A seam, so a test never starts one it cannot join."""


def windows_creation_flags(available: object = subprocess, *, breakaway: bool = True) -> int:
    """The flags a Windows helper is started with. **Measured, and one of them was wrong.**

    `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`, optionally with
    `CREATE_BREAKAWAY_FROM_JOB`.

    **`DETACHED_PROCESS` is deliberately NOT here** (Windows 11 gate,
    2026-09-21). With it the helper was spawned and never ran: Microsoft
    documents `DETACHED_PROCESS` and `CREATE_NO_WINDOW` as mutually exclusive,
    and a `powershell.exe` started with no console at all exits immediately.
    The measured symptom was the whole failure — no `powershell.exe` carrying
    `yulon-update` at any of 40 samples over 120 s, `.yulon-new` still holding
    the new build, the `.ps1` still in `%TEMP%`, the app closed and nothing
    coming back. The same script run by hand on the same box worked, which is
    what narrowed it to the spawn.

    `CREATE_NO_WINDOW` gives it a console that is never shown, which is what
    PowerShell 5.1 needs; `CREATE_NEW_PROCESS_GROUP` keeps a Ctrl-C in the
    parent's group away from it. `CREATE_BREAKAWAY_FROM_JOB` is asked for
    because a launcher such as Steam or Task Scheduler may put this app in a
    job object that would kill the helper with it — and it is asked for
    SEPARATELY, because a job that does not permit breakaway makes
    `CreateProcess` fail outright, so `_spawn_detached` retries without it.

    **Still to be re-measured by the lead**: that the helper now really runs on
    that box, and whether the breakaway flag is accepted or refused there.
    """
    flags = 0
    for name in ("CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP"):
        flags |= int(getattr(available, name, 0))
    if breakaway:
        flags |= int(getattr(available, "CREATE_BREAKAWAY_FROM_JOB", 0))
    return flags


def _spawn_detached(argv: list[str]) -> None:
    """Start the helper so that it OUTLIVES this process.

    POSIX: `start_new_session=True` puts it in a session of its own, so the
    terminal's SIGHUP and this process's exit do not reach it.

    Windows: see `windows_creation_flags` for what is set and for the gate that
    settled it. The breakaway flag is dropped and the spawn retried if the job
    this app is in refuses it — measured as the one plausible way
    `CreateProcess` fails on flags alone.

    `cwd` is the SCRIPT's own directory and never the install folder: on
    Windows a process whose working directory is a folder holds that folder
    open, and the whole point of this helper is to move things inside it.

    `runner.child_env()`, so the frozen parent's `LD_LIBRARY_PATH` does not
    reach the shell that will start the new build.
    """
    posix = os.name == "posix"
    script = script_in(argv)
    where = str(script.parent if script.parent.is_dir() else Path.cwd())

    def start(flags: int) -> None:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            cwd=where,
            env=runner.child_env(),
            start_new_session=posix,
            creationflags=flags,
        )

    if posix:
        start(0)
        return
    try:  # pragma: no cover - Windows only; the gate measures this
        start(windows_creation_flags())
    except OSError as exc:  # pragma: no cover - including PermissionError
        logger.info(f"self-update: the helper would not start with breakaway ({exc}); retrying")
        start(windows_creation_flags(breakaway=False))


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
