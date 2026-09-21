"""Where an update's working files live, and the marker that says they are ours.

**This module exists because of one measured data loss** (cold review 1 of
T90 plan 3). The first design took `sys.executable.parent` to BE the install
and renamed that whole folder. A Windows zip has no top-level directory, so a
player who chooses "Extract here" in their Downloads folder makes the install
folder `Downloads` — and the swap then renamed `Downloads` to `Downloads.old`,
put the new build where `Downloads` had been, and the next start deleted
`Downloads.old`. Measured end to end with the real helper: a `thesis.docx` and
a `photos/` folder beside the executable were **gone**. The same loss reaches
anyone who keeps a file inside a proper install folder.

So nothing here ever renames or deletes the install FOLDER. The update works
on the ENTRIES the build ships, inside the folder:

    <target>/.yulon-new/       the staged build, and the download
    <target>/.yulon-old/       the entries the swap moved aside
    <target>/<entry>           replaced one at a time by the helper

and for a file install (the AppImage) on the file and two siblings of it:

    <file>.yulon-new/          holding the staged AppImage and the marker
    <file>.yulon-old/          holding the one it replaced, and the marker

**Every delete in this package is gated on a marker this app wrote.** A
directory is removed only when its name is exactly one of the two above AND it
holds a `.yulon-marker` that parses. A `.yulon-old` a player made by hand is
never touched — the update refuses instead and names the folder, because
moving somebody's folder aside is the defect this module was written for.
"""

from __future__ import annotations

import dataclasses
import json
import os
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from yulon.log import get_logger
from yulon.selfupdate.detect import Install, InstallKind
from yulon.update_state import load_update_state, remember

logger = get_logger(__name__)

NEW_NAME = ".yulon-new"
OLD_NAME = ".yulon-old"
DOWNLOAD_NAME = ".yulon-download"
MARKER_NAME = ".yulon-marker"
"""The four fixed names. **Constants, never anything computed from an argument.**

The helper scripts spell these themselves rather than taking them as arguments,
so the only paths either of them can remove are these, under a directory it has
already validated.

`.yulon-download` is its own directory and not a file inside `.yulon-new`
(cold review 2): the archive used to be downloaded into the staging directory,
which made it one of the "entries the staged build ships" — so the helper moved
a 90 MB tarball into the player's folder as though it were part of the program,
and a player who had already saved that same release's archive there got a
"Yu'lon did not put that there" refusal about their own file.
"""

WORK_NAMES = (NEW_NAME, OLD_NAME, DOWNLOAD_NAME)
"""Everything `discard_ours` will even consider. Anything else raises."""

RESERVED_NAMES = frozenset({*WORK_NAMES, MARKER_NAME, "helper-started", "helper.log"})
"""Names that can never be a build ENTRY, so a swap can never move one."""

HELPER_STAMP = "helper-started"
"""Written inside `.yulon-old` by the helper, as its first act after validating.

**The app does not close until this exists** (Windows gate, 2026-09-21). On
that box the helper was spawned and never ran — `DETACHED_PROCESS` leaves
PowerShell 5.1 with no console and it exits at once — and the app closed
anyway. The player was left with a shut launcher, an un-swapped folder and not
a word. A file the helper writes itself is the only thing that can prove it is
alive; everything else is faith in a `Popen` that returned.
"""

HELPER_LOG = "helper.log"
"""A few plain lines the helper appends as it goes, inside the marked backup dir.

The Windows gate could only guess at what had happened because the helper left
no trace at all. It is inside `.yulon-old`, so it is removed with the rest by a
marker-gated delete, and the next start quotes its last line when it reports a
failure.
"""

APPIMAGE_ENTRY = "yulon.AppImage"
"""What the staged AppImage is called inside its work directory.

A fixed name the helper script spells itself, so a file install has one
entry and the swap is the same two moves a folder install makes.
"""

SHIPPED_MANIFEST = "yulon-shipped.txt"
"""`_internal/yulon-shipped.txt`: the top-level names the RUNNING build shipped.

Written by `release.yml` after PyInstaller. It is what lets the swap say "this
entry is ours to replace"; anything in the install folder that is NOT in it
belongs to the player and is never moved.
"""

FALLBACK_ENTRIES = ("_internal",)
"""What a build with no manifest is assumed to have shipped, besides its executable.

Measured against the real published artifacts of `v0.8.712-fixtest`: the Linux
tar.gz's `yulon/` holds exactly `_internal` and `yulon`, and the Windows zip's
top level is exactly `_internal` and `yulon.exe`. So today the fallback IS the
truth — the manifest exists so that a future spec which ships a third entry
cannot silently make that entry unreplaceable, and so that a build from before
the manifest existed still updates.
"""

STAGED = "staged"
SWAPPING = "swapping"
"""What the marker says has happened. `swapping` is written just before the helper starts."""

_SIGNATURE = "yulon-self-update"


@dataclass(frozen=True)
class Marker:
    """Proof that a working directory is this app's, and what it was doing.

    `pid` and `stamp` are what make the staging directory a lock as well as a
    marker: a second copy of Yu'lon that finds a marker whose pid is alive and
    is not its own knows that another copy is already updating.
    """

    role: str
    from_version: str
    to_version: str
    pid: int
    stamp: float
    entries: tuple[str, ...] = ()
    state: str = STAGED
    target: str = ""
    """The absolute install this marker belongs to. A marker copied elsewhere is not ours."""
    token: str = ""
    """A random secret kept in `update.json`. **This is what a forged marker cannot have.**

    A marker on its own is a file an archive could contain and a player could
    write by hand: the second cold review produced a `.yulon-old/` holding a
    hand-written marker and a `save.dat`, and it was deleted. Binding the
    marker to this install's path AND to a token that lives in the config
    directory means forging one needs to read a file that no release body and
    no archive can reach.
    """

    def as_json(self) -> str:
        return json.dumps(
            {
                "signature": _SIGNATURE,
                "role": self.role,
                "from_version": self.from_version,
                "to_version": self.to_version,
                "pid": self.pid,
                "stamp": self.stamp,
                "entries": list(self.entries),
                "state": self.state,
                "target": self.target,
                "token": self.token,
            },
            indent=2,
        )


_NOT_IN_AN_ENTRY = frozenset('/\\*?[]:"<>|')
"""Separators, and the characters a shell or Windows would read as something else.

The glob characters are here because the POSIX helper's loops are written not to
word-split — but a `*` reaching a shell at all is a class of bug this does not
want to depend on one file to avoid, and Windows refuses these in a filename
anyway. `:` and the quoting characters go with them for the same reason.
"""


def is_entry_name(name: object) -> bool:
    """Is `name` a single, ordinary path segment that can only be INSIDE the target?

    The one rule the Python side and both helper scripts apply to every entry.
    Refused: anything with a separator, `.`/`..`/empty, whitespace, a glob or
    quoting character, a leading `-` (which a command would read as an option),
    a control character, and any of this app's own reserved names — a swap must
    never be able to move `.yulon-old` or a marker.

    **Whitespace and globs are refused rather than quoted** (cold review 2).
    Today's builds ship `yulon` and `_internal`, so none of this is reachable;
    the shipped manifest exists so that a future build CAN ship another name,
    and the moment it can, an entry called `my file` would have failed the swap
    and left itself in `.yulon-old`.
    """
    return (
        isinstance(name, str)
        and bool(name)
        and name not in (".", "..")
        and name not in RESERVED_NAMES
        and not name.startswith("-")
        and not any(c in _NOT_IN_AN_ENTRY for c in name)
        and not any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in name)
    )


def work_dir(install: Install, name: str) -> Path:
    """`<target>/<name>` for a folder install, `<target><name>` for a file one.

    Both are on the same filesystem as what they serve by construction, which
    is what makes every move a rename rather than a copy.
    """
    target = install.target
    if target is None:
        raise ValueError("this install has nothing to update")
    if install.kind is InstallKind.APPIMAGE:
        return Path(str(target) + name)
    return target / name


def marker_path(install: Install, name: str) -> Path:
    """Where the marker for `work_dir(install, name)` lives: inside it, always.

    **Both kinds of install use marked DIRECTORIES**, which is what makes one
    rule cover both: a folder install's work dirs sit inside it, an AppImage's
    sit beside the file, and in each case the thing that is deleted is a
    directory holding a marker. The AppImage's one file lives inside its work
    dir under `APPIMAGE_ENTRY`, so a file install has an "entry" too and the
    swap is the same move either way.
    """
    return work_dir(install, name) / MARKER_NAME


def install_token(state_path: Path | None = None) -> str:
    """This installation's secret, from `update.json`; made on first use.

    Kept beside the update check's own state because that is this app's config
    directory — somewhere an archive being unpacked, or a release body, has no
    way to read. An unwritable config directory answers `""`, and everything
    below then treats a token as "cannot be checked" rather than as "wrong":
    the alternative is a launcher that refuses to update because it could not
    write a preferences file.
    """
    state = load_update_state(state_path)
    if state.update_token:
        return state.update_token
    fresh = secrets.token_hex(16)
    if not remember({"update_token": fresh}, state_path):
        logger.info("self-update: could not record this install's token")
        return ""
    return fresh


def write_marker(install: Install, name: str, marker: Marker) -> None:
    """Write the marker for one working directory. Raises `OSError` on failure.

    The install's path and token are filled in here rather than by the caller,
    so no caller can forget the binding.
    """
    target = install.target
    bound = dataclasses.replace(
        marker,
        target=str(target) if target is not None else "",
        token=marker.token or install_token(),
    )
    path = marker_path(install, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bound.as_json() + "\n", encoding="utf-8")


def read_marker(install: Install, name: str) -> Marker | None:
    """The marker for one working directory, or None if there is not a valid one.

    None means "not ours", and every caller treats it that way: nothing is
    deleted, nothing is moved, and the update refuses rather than guessing.
    """
    try:
        raw = json.loads(marker_path(install, name).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        logger.debug(f"self-update: no readable marker for {name}: {exc}")
        return None
    if not isinstance(raw, dict) or raw.get("signature") != _SIGNATURE:
        return None
    if raw.get("role") != name:
        # A marker is for the directory it is IN. One copied from `.yulon-new`
        # into `.yulon-old` names the wrong role and is not this app's word
        # about this directory.
        logger.info(f"self-update: a marker in {name} claims role {raw.get('role')!r}")
        return None
    target = install.target
    if raw.get("target") != (str(target) if target is not None else ""):
        logger.info(f"self-update: a marker in {name} names a different install")
        return None
    ours = install_token()
    if ours and raw.get("token") != ours:
        logger.info(f"self-update: a marker in {name} does not carry this install's token")
        return None
    entries = raw.get("entries")
    if not isinstance(entries, list) or not all(is_entry_name(e) for e in entries):
        return None
    try:
        return Marker(
            role=str(raw["role"]),
            from_version=str(raw.get("from_version", "")),
            to_version=str(raw.get("to_version", "")),
            pid=int(raw.get("pid", 0)),
            stamp=float(raw.get("stamp", 0.0)),
            entries=tuple(str(e) for e in entries),
            state=str(raw.get("state", STAGED)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def helper_stamp(install: Install) -> Path:
    """Where the helper says "I am running": `<.yulon-old>/helper-started`."""
    return work_dir(install, OLD_NAME) / HELPER_STAMP


def helper_log_tail(install: Install) -> str:
    """The last line the helper wrote, or `""`. Never raises.

    Quoted back to the player when the next start has to report a failure — on
    the Windows gate there was nothing at all to quote, which is why the log
    exists.
    """
    try:
        lines = [
            line.strip()
            for line in (work_dir(install, OLD_NAME) / HELPER_LOG)
            .read_text(encoding="utf-8", errors="replace")
            .splitlines()
            if line.strip()
        ]
    except OSError:
        return ""
    return lines[-1][:200] if lines else ""


def is_empty_work_dir(install: Install, name: str) -> bool:
    """An existing work dir holding nothing, or nothing but a marker file.

    **Ours by construction** (cold review 2, S1): `prepare()` makes the
    directory and then writes the marker, and a disk that filled up between the
    two used to leave an empty `.yulon-new` that every later attempt refused
    for ever. An empty directory under one of this app's four reserved names
    holds nothing of anybody's, so removing it costs nothing and unsticks the
    update.
    """
    path = work_dir(install, name)
    try:
        return path.is_dir() and all(
            p.name in (MARKER_NAME, HELPER_STAMP, HELPER_LOG) for p in path.iterdir()
        )
    except OSError:
        return False


def is_ours(install: Install, name: str) -> bool:
    """Does `work_dir(install, name)` exist AND carry a marker this app wrote?

    The gate on every delete. A `.yulon-old` a player made by hand answers
    False here, and is then left exactly where it is.
    """
    if not work_dir(install, name).exists():
        return False
    return read_marker(install, name) is not None or is_empty_work_dir(install, name)


def discard_ours(install: Install, name: str) -> bool:
    """Remove one MARKED working directory (or file). False if it was not ours.

    Never raises. The only delete in this package, and it can only ever reach
    `<target>/.yulon-new`, `<target>/.yulon-old` or the AppImage's two
    siblings — and only when the marker says so.
    """
    if name not in WORK_NAMES:
        raise ValueError(f"{name!r} is not one of this app's working directories")
    path = work_dir(install, name)
    if not path.exists():
        return True
    if read_marker(install, name) is None and not is_empty_work_dir(install, name):
        logger.info(f"self-update: {path} carries no marker of ours; leaving it alone")
        return False
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        logger.info(f"self-update: could not remove {path}: {exc}")
        return False
    return True


def shipped_entries(install: Install) -> frozenset[str]:
    """The top-level names the RUNNING build shipped, from its own manifest.

    **What an update is allowed to touch.** An entry in the install folder that
    is not in here was not put there by Yu'lon, so the swap will not move it —
    it refuses instead. The manifest is read from the running tree rather than
    guessed, and a build that has none (anything cut before this existed) falls
    back to the two names every one-dir build has had.
    """
    target = install.target
    if target is None or install.kind is InstallKind.APPIMAGE:
        return frozenset()
    base = {install.executable, *FALLBACK_ENTRIES} if install.executable else set(FALLBACK_ENTRIES)
    try:
        text = (target / "_internal" / SHIPPED_MANIFEST).read_text(encoding="utf-8-sig")
    except OSError as exc:
        logger.info(f"self-update: no shipped manifest in {target} ({exc}); using {sorted(base)}")
        return frozenset(base)
    listed = {line.strip() for line in text.splitlines()}
    named = {name for name in listed if is_entry_name(name)}
    if not named:
        logger.info(f"self-update: the shipped manifest in {target} names nothing usable")
        return frozenset(base)
    # The executable is added whatever the manifest says: it is the one entry
    # this process can prove is the running build's, because it is running it.
    return frozenset(named | ({install.executable} if install.executable else set()))


def another_copy_is_updating(install: Install, our_pid: int) -> str | None:
    """Is another copy of Yu'lon already part-way through an update here?

    The staging directory is the lock, and its marker holds the pid that made
    it. A marker whose pid is alive and is not ours means a second instance is
    staging into the same folder — which is how one instance's `stage()` came
    to delete the other's staged build (cold review 1).

    A marker whose pid is dead is a crashed attempt, and is ours to replace.
    """
    marker = read_marker(install, NEW_NAME)
    if marker is None or marker.pid in (0, our_pid):
        return None
    if not pid_is_alive(marker.pid):
        return None
    return (
        "Another copy of Yu'lon is already installing an update into this folder. "
        "Close it, or wait for it to finish."
    )


def pid_is_alive(pid: int) -> bool:
    """Is there a process with this id? Best effort, and False when it cannot tell."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # somebody else's process, and therefore alive
        return True
    except OSError:
        return False
    return True


def other_instances(executable: Path, our_pid: int) -> list[int]:
    """Every OTHER live process running `executable`, read off `/proc`. Linux only.

    **Why this matters here**: with two copies of Yu'lon open, the first one's
    helper would swap the entries under the second one, and the second one's
    first start after that would then be running a tree that had been replaced
    beneath it. Measured in the cold review as a lost install.

    `/proc/<pid>/exe` is a symlink to the binary the process is running, which
    is exactly the question. An unreadable entry is skipped: it belongs to
    another user, and another user's Yu'lon is not running out of this folder.
    Returns `[]` on any platform with no `/proc`, where the caller says so.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    try:
        wanted = executable.resolve()
    except OSError:
        return []
    found: list[int] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == our_pid:
            continue
        try:
            if (entry / "exe").resolve() == wanted:
                found.append(pid)
        except OSError:
            continue
    return found


def now() -> float:
    """The clock, in one place, so a marker's stamp is never hand-typed."""
    return time.time()
