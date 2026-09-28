"""A server folder locked to this Windows account, at install and by Repair (T174).

**The defect.** Yu'lon writes the database password into a server folder in
several files: `.env` (`platform.write_private_atomically`), `.db_password`
(`cmangos._write_secret`), the confs (`conf._write`, `tuning._atomic_write`)
and every `.bak` / `.repair.bak` copy of one. Each asks for POSIX mode 0o600,
which on Windows does nothing to a DACL (K.3's measurement, `conf._write`'s
docstring), so each file takes what its folder hands down. A folder made
directly under `C:\\` hands down `BUILTIN\\Users:(RX)` and `Authenticated
Users:(M)` (measured on yulon-win11 2026-09-28), so on a server installed at
`C:\\Servers\\...` every account on the PC could read the password.

**The shape: the whole folder, not each file** (the owner's decision,
2026-09-28). The server folder gets T151's owner-only DACL -- protected, full
control for this account, SYSTEM and Administrators, handed down to every file
and subfolder (`winacl.owner_only_sddl`). Every writer above creates its file,
or the temp it renames, in the folder the file lands in, with no security
attributes of its own (`os.open`, `tempfile.mkstemp`), so the file takes the
folder's inherited entries at creation; and a rename within one NTFS volume
keeps the security descriptor the file already has, which is that same
inherited one. `shutil.copystat` copies a mode bit and times and no ACL. So no
writer needs to change: a folder that is locked keeps what lands in it locked.

Measured on yulon-win11 (Docker Desktop 29.7.2, WSL2 backend) before this was
built, `.notes/gates/t174-docker-desktop-owner-only-2026-09-28/`: with the DACL
on a folder, a container (root, and uid 999 in a folder whose emulated mode is
777) read the conf and wrote files and a subfolder through the bind; each file
the container made inherited the owner-only DACL; a `:ro` sub-bind stayed read
only. So a running server needs no recreate after the lock.

**What is already inside when the lock lands.** `SetNamedSecurityInfoW`
carries the folder's handed-down entries into everything under it that
inherits, replacing what those files inherited before (`winacl._apply_dacl`'s
docstring; T151's measurement saw a file already in a loosened folder lose
`Users`). A file or folder that carries entries of its OWN keeps them, and one
whose own DACL is protected is not changed at all: a file copied in with its
ACL kept (`robocopy /SEC`, say) can stay readable to whoever that ACL names.
Yu'lon writes no such file. Walking a large server folder (a WotLK checkout
and its client data) takes as long as Windows needs to rewrite each file's
descriptor; nothing else waits on it.

**When.** At install, before each stage (`native.StagedInstaller.run()`),
which is before the first byte of any secret; checked again before every
stage, not only once, because WotLK's `clone-core` clones INTO the server
folder and both clone seams begin by removing a destination with no `.git`
(`as_the_clone_seam_does()` in the tests) -- the folder the clone leaves is a
new one, with the inherited DACL. For an install made before this, Repair
server files… offers it (`route_for_app()`), because changing an existing
folder's permissions is the player's choice to make.

**A failure is a warning, never a stop** (T151's rule). An install that could
not lock its folder writes the same files it wrote before T174, and says so;
Repair's press says why it did not. Off Windows nothing here does anything:
there the 0o600 modes are real.

**Not a server inside a WSL distro.** That folder lives on the distro's own
filesystem, whose permissions are Linux's and are not a Windows DACL; the
Yu'lon on the Windows side is not offered the lock for it (`route_for_app()`),
and an install never lands there (`platform.server_dir_problem()` refuses a
`\\\\wsl.localhost\\` folder).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yulon import winacl
from yulon.log import get_logger

logger = get_logger(__name__)


class FolderLockError(OSError):
    """The folder could not be locked; the message says why, in Windows' own words."""


LockState = Literal["locked", "open", "unknown"]


@dataclass(frozen=True)
class FolderLockCheck:
    """Is this server folder locked to this account? A reading; never raises.

    `open` is the one state Repair server files… offers the lock on: the folder
    takes its permissions from the folder above it, or has any other DACL than
    the owner-only one. `unknown` carries `why` -- the folder is not there, or
    Windows would not say -- and offers nothing.
    """

    state: LockState
    why: str = ""


@dataclass(frozen=True)
class FolderLockRoute:
    """The reading and the press behind Repair server files…'s lock, bound to one folder.

    `lock` answers True when it changed the DACL and False when the folder was
    already locked; it raises `FolderLockError` with the reason otherwise.
    """

    folder: Path
    check: Callable[[], FolderLockCheck]
    lock: Callable[[], bool]


def applies() -> bool:
    """Only Windows has a DACL to set; everywhere else the files' own modes are real."""
    return winacl._on_windows()


def _owner_only(sddl: str, user_sid: str) -> bool:
    """`winacl.is_owner_only`, the one place this module asks it.

    Once `is_owner_only` takes T151 round 2's `sid_of`, this passes
    `sid_of=winacl._sid_of` as `secure_folder` does; without it a folder of
    this PC's built-in Administrator, which Windows spells `LA`, never reads
    as locked and every stage would lock it again.
    """
    return winacl.is_owner_only(sddl, user_sid)


def check(folder: Path) -> FolderLockCheck:
    """Read `folder`'s DACL and say whether it is the owner-only one. Never raises."""
    if not applies():
        return FolderLockCheck("unknown", "only a Windows folder has a DACL to lock")
    if not folder.is_dir():
        return FolderLockCheck("unknown", f"{folder} is not there")
    try:
        user_sid = winacl._user_sid()
        dacl = winacl._read_dacl(folder)
    except Exception as exc:  # noqa: BLE001 - a reading must never cost the tab
        return FolderLockCheck("unknown", f"Windows would not say who may open {folder}: {exc}")
    return FolderLockCheck("locked" if _owner_only(dacl, user_sid) else "open")


def lock(folder: Path) -> bool:
    """Give `folder` the owner-only DACL, handed down to all it holds. True if it changed.

    Read first and left alone when it is already locked, so a second press or a
    second stage asks Windows nothing but one read. Read back after the apply:
    Windows answering success is not the same as the folder being locked.

    Raises:
        FolderLockError: Windows refused, or accepted and did not keep it. Any
            failure of the calls is wrapped, `ctypes.ArgumentError` included,
            for `winacl.secure_folder`'s reason.
    """
    try:
        user_sid = winacl._user_sid()
        before = winacl._read_dacl(folder)
        if _owner_only(before, user_sid):
            return False
        winacl._apply_dacl(folder, winacl.owner_only_sddl(user_sid))
        after = winacl._read_dacl(folder)
    except Exception as exc:  # noqa: BLE001 - every failure is the same sentence
        raise FolderLockError(f"{type(exc).__name__}: {exc}") from exc
    if not _owner_only(after, user_sid):
        raise FolderLockError(f"Windows accepted the change and the folder still reads {after}")
    logger.info(f"locked {folder} to this account (T174); it was {before}")
    return True


LOCKED_LINE = (
    "Locked {folder} to your Windows account: only you, SYSTEM and Administrators can open it "
    "or anything in it, so the database password this install writes there is not readable by "
    "the computer's other accounts."
)

NOT_LOCKED_LINE = (
    "Could not lock {folder} to your Windows account ({reason}). The install carries on without "
    "it: the files it writes there take the permissions the folder above hands down, and a "
    "folder made directly on a drive such as C:\\ lets every account on this computer read "
    "them. Once the server is installed, Repair server files… on its Server tab offers the lock "
    "again."
)


class InstallLock:
    """The lock one install run keeps on its folder: asked before every stage, said once.

    The first lock that changes anything is said; a later one -- WotLK's folder,
    made again by its clone -- is logged only. The first failure is said and
    ends the asking for this run, so a folder that refuses is one warning, not
    one per stage.
    """

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._said = False
        self._gave_up = False

    def ensure(self) -> Iterator[str]:
        """Lock the folder if it is not locked; yield what the player should read, if anything."""
        if self._gave_up or not applies() or not self.folder.is_dir():
            return
        try:
            changed = lock(self.folder)
        except FolderLockError as exc:
            self._gave_up = True
            logger.warning(f"could not lock {self.folder} to this account: {exc}")
            yield NOT_LOCKED_LINE.format(folder=self.folder, reason=exc)
            return
        if changed and not self._said:
            self._said = True
            yield LOCKED_LINE.format(folder=self.folder)


def route_for_app(server_dir: Path, *, wsl_distro: str | None = None) -> FolderLockRoute | None:
    """Repair server files…'s lock for this install, or None where it is not offered.

    None off Windows, and None for a server inside a WSL distro: that folder's
    permissions are the distro's, and a Windows DACL is not what guards it.
    """
    if wsl_distro is not None or not applies():
        return None
    return FolderLockRoute(
        folder=server_dir,
        check=lambda: check(server_dir),
        lock=lambda: lock(server_dir),
    )
