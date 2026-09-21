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

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from yulon.log import get_logger
from yulon.selfupdate.detect import Install, InstallKind

logger = get_logger(__name__)

NEW_NAME = ".yulon-new"
OLD_NAME = ".yulon-old"
MARKER_NAME = ".yulon-marker"
"""The three fixed names. **Constants, never anything computed from an argument.**

The helper scripts spell these themselves rather than taking them as arguments,
so the only path either of them can remove is one of these two under a
directory it has already validated.
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
            },
            indent=2,
        )


def is_entry_name(name: object) -> bool:
    """Is `name` a single path segment — something that can only be INSIDE the target?

    The one rule both the Python side and the two helper scripts apply to every
    entry. `a/b`, `a\\b`, `.`, `..` and `""` are all refused, so an entry can
    never name anything outside the folder it is an entry of.
    """
    return (
        isinstance(name, str)
        and bool(name)
        and "/" not in name
        and "\\" not in name
        and name not in (".", "..")
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


def write_marker(install: Install, name: str, marker: Marker) -> None:
    """Write the marker for one working directory. Raises `OSError` on failure."""
    path = marker_path(install, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(marker.as_json() + "\n", encoding="utf-8")


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


def is_ours(install: Install, name: str) -> bool:
    """Does `work_dir(install, name)` exist AND carry a marker this app wrote?

    The gate on every delete. A `.yulon-old` a player made by hand answers
    False here, and is then left exactly where it is.
    """
    return work_dir(install, name).exists() and read_marker(install, name) is not None


def discard_ours(install: Install, name: str) -> bool:
    """Remove one MARKED working directory (or file). False if it was not ours.

    Never raises. The only delete in this package, and it can only ever reach
    `<target>/.yulon-new`, `<target>/.yulon-old` or the AppImage's two
    siblings — and only when the marker says so.
    """
    if name not in (NEW_NAME, OLD_NAME):
        raise ValueError(f"{name!r} is not one of this app's working directories")
    path = work_dir(install, name)
    if not path.exists():
        return True
    if read_marker(install, name) is None:
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
