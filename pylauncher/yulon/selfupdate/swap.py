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
import secrets
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
# $1 ticks  $2 pid  $3 nonce  $4 target  $5 launch  $6 n  then n entries
# executable-FIRST followed by the same n entries executable-LAST.
ticks="$1"
pid="$2"
nonce="$3"
target="$4"
launch="$5"
half="$6"
shift 6
new="$target/{layout.NEW_NAME}"
old="$target/{layout.OLD_NAME}"
marker="{layout.MARKER_NAME}"
lock="$old/{layout.HELPER_LOCK}"
stamp="$old/{layout.HELPER_STAMP}"
standdown="$old/{layout.STAND_DOWN}"
log="$old/{layout.HELPER_LOG}"
have_lock=0

cleanup() {{
  if [ "$have_lock" -eq 1 ]; then
    rm -f -- "$lock/owner" 2>/dev/null
    rmdir -- "$lock" 2>/dev/null
  fi
  rm -f -- "$0"
}}
trap cleanup EXIT

# ---- validate before anything moves --------------------------------------
case "$ticks" in ""|*[!0-9]*) exit 64 ;; esac
case "$pid" in ""|*[!0-9]*) exit 64 ;; esac
case "$half" in ""|*[!0-9]*) exit 64 ;; esac
case "$nonce" in ""|*[!0-9a-f]*) exit 64 ;; esac
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
    "{layout.HELPER_STAMP}"|"{layout.HELPER_LOG}"|"{layout.HELPER_LOCK}") exit 64 ;;
    "{layout.STAND_DOWN}") exit 64 ;;
  esac
done
# The two halves must name the same SET, both ways round: checking one
# direction alone passes (a,a) against (a,b).
i=0
for e in "$@"; do
  i=$((i + 1))
  found=0
  j=0
  for f in "$@"; do
    j=$((j + 1))
    if [ "$i" -le "$half" ]; then
      [ "$j" -gt "$half" ] || continue
    else
      [ "$j" -le "$half" ] || continue
    fi
    [ "$f" = "$e" ] && found=1
  done
  [ "$found" -eq 1 ] || exit 64
done

# ---- become THE helper, atomically ----------------------------------------
# `mkdir` creates or fails; there is no window between looking and taking. Two
# helpers on one install produced a mixed-version install with no backup at all
# (round 4, 6 runs of 6).
if mkdir -- "$lock" 2>/dev/null; then
  have_lock=1
  printf %s "$nonce" > "$lock/owner"
else
  echo "another helper holds the lock; standing aside" >> "$log"
  exit 73
fi

# ---- say that we are alive, before waiting for anything --------------------
printf %s "$nonce" > "$stamp" || exit 64
echo "started pid=$$ nonce=$nonce target=$target entries=$half" >> "$log"

# ---- anything that means "do not do this after all" ------------------------
told_to_stop() {{
  if [ -f "$standdown" ] && grep -q -- "$nonce" "$standdown" 2>/dev/null; then
    return 0
  fi
  return 1
}}
still_ours() {{
  [ -d "$new" ] && [ -d "$old" ] || return 1
  [ -f "$new/$marker" ] && [ -f "$old/$marker" ] || return 1
  [ -f "$lock/owner" ] && [ "$(cat "$lock/owner" 2>/dev/null)" = "$nonce" ] || return 1
  [ -f "$stamp" ] && grep -q -- "$nonce" "$stamp" 2>/dev/null || return 1
  i=0
  for e in "$@"; do
    i=$((i + 1))
    [ "$i" -le "$half" ] || break
    [ -e "$new/$e" ] || return 1
  done
  return 0
}}

# ---- wait for the app ------------------------------------------------------
i=0
while kill -0 "$pid" 2>/dev/null; do
  if told_to_stop; then
    echo "stood down while waiting" >> "$log"
    exit 74
  fi
  i=$((i + 1))
  if [ "$i" -gt "$ticks" ]; then
    echo "gave up: pid $pid still running after $ticks ticks" >> "$log"
    exit 75
  fi
  sleep {TICK_SECONDS}
done

# ---- everything again, at the last moment before the first move -----------
# The app may have decided against this while we were waiting, and the folders
# may have been cleared by a second copy of Yu'lon or by the player.
if told_to_stop; then
  echo "stood down before moving anything" >> "$log"
  exit 74
fi
if ! still_ours "$@"; then
  echo "the staged update is no longer ours; moving nothing" >> "$log"
  exit 74
fi

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
cleanup
exec "$launch"
"""
"""`sh` and not `bash`: an AppImage may run on a box with no bash.

**Three things make this helper single-owner and stoppable** (round 4, which
measured all three holes on this very script):

* a **nonce** it is given and writes into its stamp, so a stamp left by an
  earlier attempt cannot make the app close for a helper that never started;
* a **lock** — `mkdir` of `<.yulon-old>/helper.lock`, which is atomic — so two
  helpers on one install cannot both swap. That produced a mixed-version
  install with NO backup: both exited 0, both relaunched, and the second moved
  the first's new executable into a backup holding only bookkeeping;
* a **stand-down** file the app writes, read on every tick of the wait AND
  again immediately before the first move, together with a full re-validation
  at that same moment. An orphan helper used to swap twelve seconds after the
  app had told the player nothing had changed.

Nothing is ever built into a string: the entries arrive TWICE, in the two
orders the swap needs, and each loop reads its half of `"$@"` with a counter.

The `trap` removes the lock and the script on every exit that is not the
`exec`; the success path calls `cleanup` explicitly, because `exec` replaces
the process and an EXIT trap never runs.
"""

POSIX_FILE_HELPER = f"""#!/bin/sh
# Yu'lon self-update helper (AppImage). Every path arrives as an argument.
# $1 ticks  $2 pid  $3 nonce  $4 target (the .AppImage file)
ticks="$1"
pid="$2"
nonce="$3"
target="$4"
new="$target{layout.NEW_NAME}"
old="$target{layout.OLD_NAME}"
marker="{layout.MARKER_NAME}"
entry="{layout.APPIMAGE_ENTRY}"
lock="$old/{layout.HELPER_LOCK}"
stamp="$old/{layout.HELPER_STAMP}"
standdown="$old/{layout.STAND_DOWN}"
log="$old/{layout.HELPER_LOG}"
have_lock=0

cleanup() {{
  if [ "$have_lock" -eq 1 ]; then
    rm -f -- "$lock/owner" 2>/dev/null
    rmdir -- "$lock" 2>/dev/null
  fi
  rm -f -- "$0"
}}
trap cleanup EXIT

# ---- validate before anything moves --------------------------------------
case "$ticks" in ""|*[!0-9]*) exit 64 ;; esac
case "$pid" in ""|*[!0-9]*) exit 64 ;; esac
case "$nonce" in ""|*[!0-9a-f]*) exit 64 ;; esac
[ "$ticks" -gt 0 ] || exit 64
[ "$pid" -gt 1 ] || exit 64
case "$target" in /*) ;; *) exit 64 ;; esac
[ -f "$target" ] || exit 64
[ -d "$new" ] || exit 64
[ -d "$old" ] || exit 64
[ -f "$new/$marker" ] || exit 64
[ -f "$old/$marker" ] || exit 64
[ -f "$new/$entry" ] || exit 64

if mkdir -- "$lock" 2>/dev/null; then
  have_lock=1
  printf %s "$nonce" > "$lock/owner"
else
  echo "another helper holds the lock; standing aside" >> "$log"
  exit 73
fi

printf %s "$nonce" > "$stamp" || exit 64
echo "started pid=$$ nonce=$nonce target=$target" >> "$log"

told_to_stop() {{
  if [ -f "$standdown" ] && grep -q -- "$nonce" "$standdown" 2>/dev/null; then
    return 0
  fi
  return 1
}}
still_ours() {{
  [ -d "$new" ] && [ -d "$old" ] || return 1
  [ -f "$new/$marker" ] && [ -f "$old/$marker" ] || return 1
  [ -f "$new/$entry" ] || return 1
  [ -f "$lock/owner" ] && [ "$(cat "$lock/owner" 2>/dev/null)" = "$nonce" ] || return 1
  [ -f "$stamp" ] && grep -q -- "$nonce" "$stamp" 2>/dev/null || return 1
  return 0
}}

# ---- wait for the app ------------------------------------------------------
i=0
while kill -0 "$pid" 2>/dev/null; do
  if told_to_stop; then
    echo "stood down while waiting" >> "$log"
    exit 74
  fi
  i=$((i + 1))
  if [ "$i" -gt "$ticks" ]; then
    echo "gave up: pid $pid still running after $ticks ticks" >> "$log"
    exit 75
  fi
  sleep {TICK_SECONDS}
done

if told_to_stop; then
  echo "stood down before moving anything" >> "$log"
  exit 74
fi
if ! still_ours; then
  echo "the staged update is no longer ours; moving nothing" >> "$log"
  exit 74
fi

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
cleanup
exec "$target"
"""
"""The AppImage half: one file replaced by one file, and its name kept.

The same nonce, lock and stand-down protocol as the folder helper, for the
same three measured reasons — see `POSIX_FOLDER_HELPER`.

The file keeps its old version number on purpose, so a desktop entry or a
shortcut pointing at it keeps working; the window title is what says the
version.
"""

POWERSHELL_FOLDER_HELPER = f"""param(
  [int]$Ticks, [int]$ProcId, [string]$Nonce, [string]$Target, [string]$Launch, [int]$Half,
  [Parameter(ValueFromRemainingArguments = $true)][string[]]$Entries
)
# Yu'lon self-update helper (one-dir install). Every path arrives as a parameter.
# $Entries holds the same names twice: executable-FIRST, then executable-LAST.
$ErrorActionPreference = 'Stop'
$New = Join-Path $Target '{layout.NEW_NAME}'
$Old = Join-Path $Target '{layout.OLD_NAME}'
$Marker = '{layout.MARKER_NAME}'
$Lock = Join-Path $Old '{layout.HELPER_LOCK}'
$Owner = Join-Path $Lock 'owner'
$Stamp = Join-Path $Old '{layout.HELPER_STAMP}'
$StandDown = Join-Path $Old '{layout.STAND_DOWN}'
$Log = Join-Path $Old '{layout.HELPER_LOG}'
$Reserved = @('{layout.NEW_NAME}', '{layout.OLD_NAME}', '{layout.DOWNLOAD_NAME}', $Marker,
              '{layout.HELPER_STAMP}', '{layout.HELPER_LOG}', '{layout.HELPER_LOCK}',
              '{layout.STAND_DOWN}')
$HaveLock = $false

function Say($text) {{
  try {{ Add-Content -LiteralPath $Log -Value $text -ErrorAction SilentlyContinue }} catch {{}}
}}
function Done($code) {{
  if ($HaveLock) {{
    Remove-Item -LiteralPath $Lock -Recurse -Force -ErrorAction SilentlyContinue
  }}
  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
  exit $code
}}
function ToldToStop() {{
  if (-not (Test-Path -LiteralPath $StandDown -PathType Leaf)) {{ return $false }}
  try {{ return (Get-Content -LiteralPath $StandDown -Raw) -match [regex]::Escape($Nonce) }}
  catch {{ return $false }}
}}
function StillOurs() {{
  if (-not (Test-Path -LiteralPath (Join-Path $New $Marker) -PathType Leaf)) {{ return $false }}
  if (-not (Test-Path -LiteralPath (Join-Path $Old $Marker) -PathType Leaf)) {{ return $false }}
  if (-not (Test-Path -LiteralPath $Owner -PathType Leaf)) {{ return $false }}
  try {{ if ((Get-Content -LiteralPath $Owner -Raw).Trim() -ne $Nonce) {{ return $false }} }}
  catch {{ return $false }}
  try {{ if (-not ((Get-Content -LiteralPath $Stamp -Raw) -match [regex]::Escape($Nonce))) {{
    return $false
  }} }} catch {{ return $false }}
  foreach ($e in $Forward) {{
    if (-not (Test-Path -LiteralPath (Join-Path $New $e))) {{ return $false }}
  }}
  return $true
}}

# `[int]` refuses a non-numeric argument before this body runs at all:
# PowerShell fails the parameter binding and the script exits non-zero having
# executed none of it. These checks are for values that ARE integers and are
# still wrong.
if ($Ticks -le 0 -or $ProcId -le 1 -or $Half -le 0) {{ Done 64 }}
if ($Nonce -notmatch '^[0-9a-f]+$') {{ Done 64 }}
if (-not [System.IO.Path]::IsPathRooted($Target)) {{ Done 64 }}
if (-not (Test-Path -LiteralPath $Target -PathType Container)) {{ Done 64 }}
if (-not (Test-Path -LiteralPath (Join-Path $New $Marker) -PathType Leaf)) {{ Done 64 }}
if (-not (Test-Path -LiteralPath (Join-Path $Old $Marker) -PathType Leaf)) {{ Done 64 }}
if (-not $Entries -or $Entries.Count -ne ($Half * 2)) {{ Done 64 }}
foreach ($e in $Entries) {{
  if ([string]::IsNullOrWhiteSpace($e) -or $e -eq '.' -or $e -eq '..') {{ Done 64 }}
  if ($e -notmatch '^[._A-Za-z0-9][-._A-Za-z0-9]*$') {{ Done 64 }}
  if ($Reserved -contains $e) {{ Done 64 }}
}}
$Forward = $Entries[0..($Half - 1)]
$Backward = $Entries[$Half..($Entries.Count - 1)]
foreach ($e in $Forward) {{ if ($Backward -notcontains $e) {{ Done 64 }} }}
foreach ($e in $Backward) {{ if ($Forward -notcontains $e) {{ Done 64 }} }}

# ---- become THE helper, atomically ----------------------------------------
try {{
  New-Item -ItemType Directory -Path $Lock -ErrorAction Stop | Out-Null
  $HaveLock = $true
  Set-Content -LiteralPath $Owner -Value $Nonce -NoNewline
}} catch {{
  Say "another helper holds the lock; standing aside"
  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
  exit 73
}}

Set-Content -LiteralPath $Stamp -Value $Nonce -NoNewline
Say "started pid=$PID nonce=$Nonce target=$Target entries=$Half"

$deadline = (Get-Date).AddSeconds($Ticks * {TICK_SECONDS})
while (Get-Process -Id $ProcId -ErrorAction SilentlyContinue) {{
  if (ToldToStop) {{ Say "stood down while waiting"; Done 74 }}
  if ((Get-Date) -gt $deadline) {{ Say "gave up: pid $ProcId still running"; Done 75 }}
  Start-Sleep -Milliseconds 200
}}
if (ToldToStop) {{ Say "stood down before moving anything"; Done 74 }}
if (-not (StillOurs)) {{ Say "the staged update is no longer ours; moving nothing"; Done 74 }}

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
Done 0
"""
"""The Windows half: the same nonce, lock and stand-down protocol as the POSIX one.

**Measured on a real Windows 11 box, 2026-09-21 (two gates).** The script
itself is good: run by hand it exits 0, swaps both entries, leaves the old
build in `.yulon-old` and deletes itself; and with the spawn corrected (see
`windows_creation_flags`) the whole update ran end to end in an interactive
session, breakaway refused with `[WinError 5]` and the retry taking over.

**What is untested here**: the lock, the stand-down and the re-validation
lines — every one of them was added after the last Windows run, and this side
has no PowerShell. The gate list says what to do: delay the stamp past the
app's window with a `Start-Sleep` before `Set-Content -LiteralPath $Stamp`,
confirm the app refuses to close, then close the app by hand and confirm
nothing moved.
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
    """Everything the swap needs: the script that was written, and how to start it.

    `arm()` makes a NEW plan from this one with a fresh nonce and its own
    script, which is what is actually started. The fields below describe the
    swap; the nonce describes one attempt at it.
    """

    script: Path
    argv: list[str]
    """`argv[0]` is the interpreter. Every path in here is one whole element."""
    entries: tuple[str, ...] = ()
    """The names the helper will replace, executable first. Empty for an AppImage."""
    body: str = ""
    """The script's text, so `arm()` can write it again at the moment of use.

    The body is a module constant and holds no path, so carrying it here costs
    nothing and cannot carry anything of the player's with it.
    """
    nonce: str = ""
    """This attempt's secret. A stamp that does not hold it is not this attempt's."""
    fixed: tuple[str, ...] = ()
    """The arguments before the nonce and after it, so `arm()` can rebuild argv.

    `(which, script_dir, ticks, pid, target, launch, half, *entries)`. Kept as
    plain strings because that is what they are on the command line.
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
        parts: tuple[str, ...] = (which, str(script_dir), ticks, str(pid), str(target))
        return arm(SwapPlan(Path(), [], (layout.APPIMAGE_ENTRY,), POSIX_FILE_HELPER, "", parts))
    named = tuple(entries)
    if not named or not all(layout.is_entry_name(name) for name in named):
        raise UpdateError("Yu'lon could not work out which files to replace.")
    _make_the_backup_dir(install, marker, named)
    launch = target / install.executable
    # **The entries go twice, in the two orders the swap needs**: the current
    # ones are moved aside executable-FIRST and the staged ones are brought in
    # executable-LAST, so the executable is never present beside libraries of
    # the other version. Reversing a list inside POSIX `sh` cannot be done
    # without rebuilding it into a string, and a string is word-split — which
    # is the defect this avoids by doing the reversing HERE, where lists are
    # lists (cold review 2, S2).
    parts = (
        which,
        str(script_dir),
        ticks,
        str(pid),
        str(target),
        str(launch),
        str(len(named)),
        *named,
        *reversed(named),
    )
    del staged  # the helper finds it by name under the target it validated
    return arm(SwapPlan(Path(), [], named, POSIX_FOLDER_HELPER, "", parts))


def _make_the_backup_dir(install: Install, marker: layout.Marker, entries: Sequence[str]) -> None:
    """Create `.yulon-old` with a marker, so the helper's own check can pass.

    The helper refuses to touch anything unless BOTH work directories carry a
    marker — a directory the helper made itself would prove nothing — so this
    is where the backup directory comes from, and the marker it writes is what
    the FIRST START AFTER the swap reads to decide whether the swap finished
    (`cleanup.finish_previous_update`): it carries the version being installed
    and the entries that were supposed to land.
    """
    target = install.target
    assert target is not None  # `plan_swap` refused a `None` target already
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
                new_entries=tuple(name for name in entries if not (target / name).exists()),
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


def arm(plan: SwapPlan) -> SwapPlan:
    """A NEW plan for ONE attempt: a fresh nonce, its own script file, its own argv.

    **Every start gets its own nonce and its own script** (round 4). The nonce
    is what the helper writes into its stamp, so a stamp left behind by an
    earlier attempt cannot make the app close for a helper that never started;
    and the script is named after the nonce, so two attempts cannot overwrite
    each other's file.

    Called immediately before the helper is started, which is also what makes
    it the answer to a `/tmp` sweep between staging and pressing: the script is
    written here, not minutes earlier.
    """
    which, script_dir, *rest = plan.fixed
    nonce = secrets.token_hex(8)
    script, body = _write(Path(script_dir), f"yulon-update-{rest[1]}-{nonce}", which, plan.body)
    # The nonce goes third, after the ticks and the pid: the helper reads it by
    # position, like every other argument.
    argv = _argv(which, script, [rest[0], rest[1], nonce, *rest[2:]])
    logger.info(f"self-update: helper written to {script}")
    return SwapPlan(script, argv, plan.entries, plan.body, nonce, plan.fixed)


def stand_down(install: Install, plan: SwapPlan) -> None:
    """Tell a helper this app started that it is not wanted after all. Never raises.

    Written into the marked backup, holding the attempt's nonce, and read by
    the helper on every tick of its wait AND immediately before its first move.
    Without it an orphan swapped twelve seconds after the app had told the
    player that nothing had changed (round 4).
    """
    if not plan.nonce:
        return
    try:
        layout.stand_down_path(install).write_text(plan.nonce + "\n", encoding="utf-8")
        logger.info(f"self-update: stood down the helper for nonce {plan.nonce}")
    except OSError as exc:
        logger.warning(f"self-update: could not write the stand-down file: {exc}")


def clear_stamp(install: Install) -> None:
    """Remove any stamp and stand-down left by an earlier attempt. Never raises."""
    for path in (layout.helper_stamp(install), layout.stand_down_path(install)):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.info(f"self-update: could not remove {path}: {exc}")


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


Spawn = Callable[[list[str]], object]
"""How the helper is started, and what it hands back.

The handle is kept by the app so a helper it decided against can be ENDED
rather than merely asked to stand down (round 4): the `Popen` used to be
dropped on the floor, and an orphan swapped twelve seconds later.
"""


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


def _spawn_detached(argv: list[str]) -> object:
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
    started: list[object] = []
    where = str(script.parent if script.parent.is_dir() else Path.cwd())

    def start(flags: int) -> None:
        started.append(
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
        )

    if posix:
        start(0)
        return started[0]
    try:  # pragma: no cover - Windows only; the gate measures this
        start(windows_creation_flags())
    except FileNotFoundError:  # pragma: no cover - nothing to retry: the exe is not there
        raise
    except OSError as exc:  # pragma: no cover - PermissionError under a job object
        # **Measured on the Windows 11 gate, 2026-09-21**: under a scheduled
        # task the breakaway flag is refused with `[WinError 5] Access is
        # denied`, and the retry without it starts the helper and the whole
        # update then runs. A missing executable is NOT retried — the second
        # attempt would fail the same way and hide the reason.
        logger.info(f"self-update: the helper would not start with breakaway ({exc}); retrying")
        start(windows_creation_flags(breakaway=False))
    return started[0]


def end_helper(handle: object, *, seconds: float = 5.0) -> None:
    """Stop a helper this app started and wait for it to go. Never raises.

    Belt to the stand-down file's braces: the file is what a helper checks, and
    this is what makes sure there is nothing left to check it. Both, because a
    helper that is between two checks when the app gives up would otherwise
    have a window to act in.
    """
    if handle is None:
        return
    try:
        if handle.poll() is not None:  # type: ignore[attr-defined]
            return
        handle.terminate()  # type: ignore[attr-defined]
        handle.wait(timeout=seconds)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - tidying must not raise
        logger.info(f"self-update: could not end the helper: {exc}")


def start_helper(plan: SwapPlan, *, spawn: Spawn = _spawn_detached) -> object:
    """Start the helper. **Call this only once the window really is going to close.**

    `main.py` asks `close_refusal()` twice — before the update starts, and
    again immediately before this call — because the work in between takes
    minutes and a database import can begin in them. If the close were refused
    after the helper had started, the helper would wait out its bound and then
    do nothing at all, which is the give-up path this design chose over
    launching a second copy beside the running one.
    """
    logger.info(f"self-update: starting the helper as {plan.argv[0]}")
    return spawn(plan.argv)
