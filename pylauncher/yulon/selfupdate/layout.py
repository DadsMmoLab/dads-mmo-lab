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
from typing import Any

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

"""Names that can never be a build ENTRY, so a swap can never move one."""

HELPER_LOCK = "helper.lock"
"""A directory the helper creates to become THE helper. `mkdir` is the atomic part.

Two helpers on one install produced a mixed-version install with no backup
(round 4, measured 6 runs of 6): both passed their checks, both swapped, and
the second moved the first's new executable into a backup that then held only
bookkeeping — so the old executable was gone. `mkdir` either creates the
directory or fails; there is no window between looking and taking.
"""

STAND_DOWN = "stand-down"
"""Written by the APP to tell a helper it started that it is not wanted after all.

Holds the nonce of the attempt being cancelled. The helper reads it on every
tick of its wait and again immediately before its first move, so a helper that
outlived the app's patience cannot swap unasked — which is what an orphan did
in round 4: the app said "nothing was changed" and the helper swapped anyway
twelve seconds later.
"""

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

BOOKKEEPING = frozenset({MARKER_NAME, HELPER_STAMP, HELPER_LOG, HELPER_LOCK, STAND_DOWN})
"""The files this app keeps inside a working directory. Everything else in one is somebody's.

Used by two rules: an "empty" work dir is one holding nothing but these, and
the player's recovery instruction says to move everything EXCEPT these.
"""

RESERVED_NAMES = frozenset({*WORK_NAMES, *BOOKKEEPING})

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
    new_entries: tuple[str, ...] = ()
    """Entries the NEW build ships that the old one did not.

    Recorded because the recovery instruction has to name them: nothing in
    `.yulon-old` will replace them, and left in place they collide with the
    next update for ever (round 4, M4).
    """
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
                "new_entries": list(self.new_entries),
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
            new_entries=tuple(str(e) for e in (raw.get("new_entries") or []) if is_entry_name(e)),
            state=str(raw.get("state", STAGED)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def helper_stamp(install: Install) -> Path:
    """Where the helper says "I am running": `<.yulon-old>/helper-started`."""
    return work_dir(install, OLD_NAME) / HELPER_STAMP


def stand_down_path(install: Install) -> Path:
    """Where the app tells a helper it is not wanted: `<.yulon-old>/stand-down`."""
    return work_dir(install, OLD_NAME) / STAND_DOWN


def helper_lock(install: Install) -> Path:
    """The directory whose existence IS the lock: `<.yulon-old>/helper.lock`."""
    return work_dir(install, OLD_NAME) / HELPER_LOCK


@dataclass(frozen=True)
class LockHolder:
    """Who holds the helper lock, as the helper itself wrote it."""

    nonce: str
    """The attempt's secret. `release_lock_of` will remove a lock only for this."""
    pid: int
    """The helper's own process id, or 0 when it could not be read."""

    def alive(self) -> bool:
        """Is that helper still running? `False` when the pid is unknown."""
        return self.pid > 0 and pid_is_alive(self.pid)


def lock_holder(install: Install) -> LockHolder | None:
    """Who holds the lock, or None if nothing does. Never raises.

    **The pid is in there so the app can tell a live helper from a dead one's
    leavings** (round 5). A lock is a directory, and a helper killed with
    SIGKILL — or with `TerminateProcess`, which cannot be trapped at all —
    leaves it behind: without a pid the app could only guess whether waiting
    for it to go would ever end.
    """
    lock = helper_lock(install)
    try:
        nonce = (lock / "owner").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return LockHolder("", 0) if lock.is_dir() else None
    try:
        pid = int((lock / "pid").read_text(encoding="utf-8", errors="replace").strip() or 0)
    except (OSError, ValueError):
        pid = 0
    return LockHolder(nonce, pid)


def release_lock_of(install: Install, nonce: str) -> bool:
    """Remove the helper lock **only when it is this nonce's**. Never raises.

    The app calls this after it has ended a helper it started and confirmed it
    gone. `/bin/sh` is dash on most Linuxes and dash does not run an EXIT trap
    on SIGTERM, and Windows `TerminateProcess` runs nothing at all — so the
    lock outlives the process that took it, and every later press then gets
    exit 73 and no stamp for the rest of the session (round 5, M1).

    The nonce is the whole safety of it: a lock somebody else's helper holds is
    never touched here.
    """
    if not nonce:
        return False
    holder = lock_holder(install)
    if holder is None:
        return True
    if holder.nonce != nonce:
        logger.info(f"self-update: the lock belongs to {holder.nonce or 'nobody we know'}; leaving")
        return False
    lock = helper_lock(install)
    try:
        for name in ("owner", "pid"):
            (lock / name).unlink(missing_ok=True)
        lock.rmdir()
    except OSError as exc:
        logger.info(f"self-update: could not remove the helper lock: {exc}")
        return False
    return True


LOCK_WAIT_SECONDS = 10.0
"""How long the app waits for a helper it did not start to release the lock."""

STALE_LOCK_SECONDS = 30.0
"""How old an owner-less lock must be before it is treated as rubbish.

A lock is `mkdir` then two small writes. A helper that died between them
leaves a directory naming nobody, and without this the same staged plan got
exit 73 on every press for ever while the message kept saying "press Update
now again" (round 6). Thirty seconds is a thousand times the gap it covers.
"""


def tell_to_stop(install: Install, nonce: str) -> None:
    """Write the stand-down file for `nonce`. Never raises.

    Lives here rather than in `swap` because the two callers are on opposite
    sides of the package: the attempt that started a helper stands its own
    down, and `stage.prepare` stands down a helper it did NOT start before it
    discards the directory that helper is working in.
    """
    if not nonce:
        return
    try:
        stand_down_path(install).write_text(nonce + "\n", encoding="utf-8")
        logger.info(f"self-update: stood down the helper for nonce {nonce}")
    except OSError as exc:
        logger.warning(f"self-update: could not write the stand-down file: {exc}")


def clear_stale_lock(install: Install) -> bool:
    """Remove a lock nobody is behind. True if there is no lock afterwards.

    **A lock the app cannot free wedges the feature** (round 6): every press of
    the same staged plan got exit 73 and the message kept asking for another
    press. Two kinds are rubbish — one naming a pid that is gone, and one
    naming nobody at all that has been sitting there longer than
    `STALE_LOCK_SECONDS`. A lock whose pid is alive is never touched here.
    """
    holder = lock_holder(install)
    if holder is None:
        return True
    if holder.alive():
        return False
    lock = helper_lock(install)
    if not holder.pid:
        try:
            age = now() - lock.stat().st_mtime
        except OSError:
            age = 0.0
        if age < STALE_LOCK_SECONDS:
            return False
        logger.info(f"self-update: the helper lock names nobody and is {age:.0f}s old")
    try:
        shutil.rmtree(lock)
    except OSError as exc:
        logger.info(f"self-update: could not remove the stale helper lock: {exc}")
        return False
    return True


def make_way(install: Install, *, seconds: float = LOCK_WAIT_SECONDS) -> str | None:
    """Is a live helper working in `.yulon-old`? Stand it down; refuse if it stays.

    **The app used to delete the directory out from under it** (round 5, M2).
    The state: a first helper is alive because the close was refused after it
    had stamped; the player presses Update now again; the backup directory is
    discarded, the live helper's lock with it, and a second helper then takes a
    lock of the same name. In 5 runs of 12 the first helper's exit trap removed
    the SECOND one's lock, the second's re-validation failed, and the app
    closed with nothing swapped and nothing relaunched.

    **Called before the FIRST discard of an update, not before the last**
    (round 6): it lived beside `_make_the_backup_dir`, which runs minutes after
    `stage.prepare` has already discarded every working directory — so on the
    real path the live holder's lock was gone before this was ever asked, and
    only the script-side owner check was keeping the install whole.
    """
    if clear_stale_lock(install):
        return None
    holder = lock_holder(install)
    if holder is None:  # pragma: no cover - cleared between the two reads
        return None
    logger.info(f"self-update: a helper ({holder.nonce}) is still working here; standing it down")
    tell_to_stop(install, holder.nonce)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if clear_stale_lock(install):
            return None
        time.sleep(0.05)
    if clear_stale_lock(install):
        return None
    return (
        "An installer Yu'lon started earlier is still working in this folder, and Yu'lon will "
        "not start a second one beside it. Wait a moment and press Update now again; if it is "
        "still refused, close Yu'lon and open it again."
    )


def stamp_holds(install: Install, nonce: str) -> bool:
    """Is the helper's stamp THIS attempt's? A stale one answers False.

    Read rather than merely existing (round 4). Nothing removes the stamp on
    the helper's own failure paths, so one left behind by a give-up made the
    next attempt believe a helper was running when nothing had started.
    """
    if not nonce:
        return False
    try:
        return nonce in helper_stamp(install).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


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
        return path.is_dir() and all(p.name in BOOKKEEPING for p in path.iterdir())
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

    **A live pid is not enough** (round 4): pids are reused, and a marker
    naming one that now belongs to somebody's text editor refused every future
    update with no way out. So the pid must also BE this app where that can be
    asked, and where it cannot, a marker older than `STALE_MARKER_SECONDS` is
    treated as abandoned.
    """
    marker = read_marker(install, NEW_NAME)
    if marker is None or not another_copy_is_working(install, marker, our_pid=our_pid):
        return None
    return (
        "Another copy of Yu'lon is already installing an update into this folder. Close it, or "
        f"wait for it to finish — if you are sure no other copy is open, delete the {NEW_NAME} "
        "folder beside Yu'lon and try again."
    )


def another_copy_is_working(install: Install, marker: Marker, *, our_pid: int) -> bool:
    """Is the copy of Yu'lon that wrote this marker still running? **The one rule.**

    Both places that ask have to ask the same way, and for a round they did
    not: this file checked identity and age before staging, while
    `cleanup.finish_previous_update` asked `pid_is_alive` alone — so at start a
    marker naming a REUSED pid made the startup report silently empty, which is
    the failure that rule was written to stop (round 5, N1).

    Liveness, then identity where it can be asked (`/proc`,
    `QueryFullProcessImageNameW`), then the marker's age where it cannot.
    """
    if marker.pid in (0, our_pid):
        return False
    if not pid_is_alive(marker.pid):
        return False
    target = install.target
    exe = target / install.executable if target is not None and install.executable else None
    identity = pid_is_this_app(marker.pid, exe)
    if identity is False:
        logger.info(f"self-update: pid {marker.pid} is alive but is not Yu'lon; carrying on")
        return False
    if identity is None and now() - marker.stamp > STALE_MARKER_SECONDS:
        logger.info(f"self-update: the marker naming pid {marker.pid} is too old to believe")
        return False
    return True


def pid_is_alive(pid: int, *, windows: bool | None = None) -> bool:
    """Is there a process with this id? Best effort, and False when it cannot tell.

    **`os.kill(pid, 0)` is not a liveness probe on Windows and is dangerous
    there** (round 4). CPython maps signal 0 on Windows to
    `GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)`: it does not ask whether the
    process exists, and against a console process group it SENDS a Ctrl-C. So
    Windows asks the kernel directly — `OpenProcess` with
    `PROCESS_QUERY_LIMITED_INFORMATION`, then `GetExitCodeValue == STILL_ACTIVE`
    — and an access-denied answer counts as alive, because a process another
    user owns is a process.

    `windows` is injected so the branch can be tested from either platform.
    """
    if pid <= 0:
        return False
    on_windows = os.name == "nt" if windows is None else windows
    if on_windows:
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # somebody else's process, and therefore alive
        return True
    except OSError:
        return False
    return True


_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ACCESS_DENIED = 5


def _kernel32() -> Any:  # pragma: no cover - Windows only
    """`kernel32` with the signatures declared, which on 64-bit Windows is not optional.

    **A HANDLE is a pointer and ctypes assumes a C `int`** (round 5, W1). With
    no `restype` the top 32 bits of every handle `OpenProcess` returns are
    thrown away, so `CloseHandle` is given a number that is not the handle and
    the truncated value can even test falsy for a process that really is there.
    `use_last_error=True` plus `ctypes.get_last_error()` for the same reason in
    the other direction: `kernel32.GetLastError()` through ctypes reads the
    error of whatever ctypes itself did last, so an access-denied answer — a
    process another user owns, which IS alive — read as dead.

    `getattr`, not `ctypes.WinDLL` by attribute: the name exists only on
    Windows, and a `type: ignore` for it is itself an error under
    `mypy --platform win32`, which CI runs (the convention `platform.py` uses).
    """
    import ctypes
    from ctypes import wintypes  # noqa: F401 - Windows-only module

    windll = getattr(ctypes, "WinDLL")  # noqa: B009 - Windows-only attribute
    kernel32 = windll("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _windows_pid_is_alive(pid: int) -> bool:  # pragma: no cover - Windows only
    """`OpenProcess` + `GetExitCodeProcess`, which is the question actually being asked."""
    import ctypes
    from ctypes import wintypes

    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Access denied means it exists and is not ours to look at.
        # `getattr` again: `ctypes.get_last_error` is Windows-only in the
        # stubs, so naming it directly is an error under the other two
        # platforms CI type-checks.
        last_error = getattr(ctypes, "get_last_error")  # noqa: B009 - Windows-only
        return bool(last_error() == _ERROR_ACCESS_DENIED)
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return bool(code.value == _STILL_ACTIVE)
    finally:
        kernel32.CloseHandle(handle)


STALE_MARKER_SECONDS = 15 * 60
"""How old a marker must be before a live pid it names is disbelieved.

**A pid is reused** (round 4), and a marker naming one that now belongs to a
text editor made the app refuse every update with "another copy of Yu'lon is
already installing" and no way out. Where the pid's identity can be checked it
is; where it cannot, a quarter of an hour is longer than any update this app
has ever taken and short enough that a player is not stuck.
"""


def pid_is_this_app(pid: int, executable: Path | None) -> bool | None:
    """Is that pid running OUR executable? None means "cannot tell here".

    Linux reads `/proc/<pid>/exe`; Windows asks
    `QueryFullProcessImageNameW`. Anywhere else, and on any error, the answer
    is None and the caller falls back to the marker's age.
    """
    if executable is None or pid <= 0:
        return None
    try:
        wanted = executable.resolve()
    except OSError:
        return None
    if os.name == "posix":
        link = Path("/proc") / str(pid) / "exe"
        if not link.parent.is_dir():
            return None
        try:
            return same_file(link.resolve(), wanted)
        except OSError:
            return None
    return _windows_pid_is_this_app(pid, wanted)  # pragma: no cover - Windows only


def _windows_pid_is_this_app(pid: int, wanted: Path) -> bool | None:  # pragma: no cover
    import ctypes
    from ctypes import wintypes

    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return same_file(Path(buffer.value), wanted)
    except OSError:
        return None
    finally:
        kernel32.CloseHandle(handle)


def same_file(one: Path, other: Path) -> bool:
    """Are these two paths the same file? Spelling is not the question.

    **The kernel answers with whatever spelling the process was started with**
    (round 5, W2): an 8.3 short path (`C:\\PROGRA~1\\Yulon\\yulon.exe`), a
    `subst` drive or a junction all name the file a resolved `Path` spells
    differently, and a plain `==` between them answered False — which read as
    "that pid is not Yu'lon" and cleared the other copy's staging directory.

    `os.path.samefile` asks the filesystem, which is the real question; when
    either path is gone there is nothing to ask, so the spellings are compared
    case-folded and normalised, which is as close as anything can get.
    """
    try:
        return os.path.samefile(one, other)
    except OSError:
        pass
    return os.path.normcase(os.path.normpath(one)) == os.path.normcase(os.path.normpath(other))


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
