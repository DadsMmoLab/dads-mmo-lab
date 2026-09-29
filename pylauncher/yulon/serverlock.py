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

**When, and which folders.** At install, before each stage
(`native.StagedInstaller.run()`), which is before the first byte of any
secret; checked again before every stage, not only once, because WotLK's
`clone-core` clones INTO the server folder and both clone seams begin by
removing a destination with no `.git` (`as_the_clone_seam_does()` in the
tests) -- the folder the clone leaves is a new one, with the inherited DACL.

The install narrows a folder on its own only when everything it takes away is
a BROAD default group (`BROAD_GROUPS`: `Users`, `Authenticated Users`,
`Everyone` and their like), which is what a folder under `C:\\` inherits and
nobody chose. A folder that also grants a SPECIFIC account or group -- a
person on this PC, a domain group, a backup service -- was set up that way by
somebody, and is left as it is: the install says so, and Repair server files…
offers the lock with a question that names who would lose access (the lead's
decision, round 2).

For an install made before this, Repair server files… offers it only when the
folder is `exposed`: an account other than this one, SYSTEM and Administrators
can read it (`check()`). A folder under the profile inherits exactly those
three and is `private`, and nothing is offered for it: locking it would change
nothing anyone could read.

**A failure is a warning, never a stop** (T151's rule). An install that could
not lock its folder writes the same files it wrote before T174 and says so
once. It asks again when a later stage finds a different folder at the path
(Codex, rounds 2 and 3): WotLK's clone makes the folder anew, and a folder that
refused before the clone may not refuse after it, while the same folder would
only refuse again, paying for another walk of everything in it. Repair's
press says why it did not. Off Windows nothing here does anything: there the
0o600 modes are real.

**Not a server inside a WSL distro.** That folder lives on the distro's own
filesystem, whose permissions are Linux's and are not a Windows DACL; the
Yu'lon on the Windows side is not offered the lock for it (`route_for_app()`),
and an install never lands there (`platform.server_dir_problem()` refuses a
`\\\\wsl.localhost\\` folder).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yulon import winacl
from yulon.log import get_logger

logger = get_logger(__name__)


class FolderLockError(OSError):
    """The folder could not be locked; the message says why, in Windows' own words."""


class FolderLockRefused(FolderLockError):
    """`SetNamedSecurityInfoW` itself refused, or took the DACL and did not keep it.

    The one failure an install remembers (`InstallLock`, round 4): it is what
    costs a walk of the whole folder, and the same folder answers it the same
    way. A token, a read or a name that failed is asked again at the next stage.
    """


BROAD_GROUPS: dict[str, str] = {
    "S-1-1-0": "Everyone",
    "S-1-2-0": "LOCAL",
    "S-1-2-1": "CONSOLE LOGON",
    "S-1-5-2": "NETWORK",
    "S-1-5-4": "INTERACTIVE",
    "S-1-5-7": "ANONYMOUS LOGON",
    "S-1-5-11": "Authenticated Users",
    "S-1-5-32-545": "Users",
    "S-1-5-32-546": "Guests",
    "S-1-15-2-1": "ALL APPLICATION PACKAGES",
    "S-1-15-2-2": "ALL RESTRICTED APPLICATION PACKAGES",
}
"""Well-known groups that mean "anybody", and the name each is shown by if Windows gives none.

Every one is a fixed SID, the same on every PC, that Windows itself hands down
(`C:\\` gives `Users` and `Authenticated Users`) rather than one a person adds
for somebody. `Domain Users` is deliberately not here: its SID carries a
domain's own number, so it cannot be told from a group somebody made, and a
domain PC's folder is one an administrator may have set up on purpose."""

_NOBODY_ELSE = frozenset({"S-1-3-0", "S-1-3-4"})
"""`CREATOR OWNER` and `OWNER RIGHTS`: placeholders for whoever owns a file, not another reader."""

_RIGHTS = {
    "CC": 0x1,
    "DC": 0x2,
    "LC": 0x4,
    "SW": 0x8,
    "RP": 0x10,
    "WP": 0x20,
    "DT": 0x40,
    "LO": 0x80,
    "CR": 0x100,
    "SD": 0x10000,
    "RC": 0x20000,
    "WD": 0x40000,
    "WO": 0x80000,
    "GA": 0x10000000,
    "GX": 0x20000000,
    "GW": 0x40000000,
    "GR": 0x80000000,
    "FA": 0x1F01FF,
    "FR": 0x120089,
    "FW": 0x120116,
    "FX": 0x1200A0,
}
"""SDDL's two-letter rights, as the bits they stand for on a file. `CC` is `FILE_READ_DATA`."""

_READS = 0x1 | 0x10000000 | 0x80000000
"""`FILE_READ_DATA` (list, on a folder), `GENERIC_ALL`, `GENERIC_READ`: rights that show content."""


def _can_read(rights: str) -> bool:
    """Does this ACE's rights field let its trustee read a file's content? Unknown means yes."""
    text = rights.upper()
    if text.startswith("0X"):
        try:
            return bool(int(text, 16) & _READS)
        except ValueError:
            return True
    tokens = [text[i : i + 2] for i in range(0, len(text), 2)]
    if not tokens or any(token not in _RIGHTS for token in tokens):
        return True
    mask = 0
    for token in tokens:
        mask |= _RIGHTS[token]
    return bool(mask & _READS)


def readers(sddl: str, user_sid: str) -> tuple[str, ...]:
    """Every trustee but this account, SYSTEM and Administrators that this DACL lets read.

    As SIDs, in the DACL's order, each once. An allow entry counts whether it
    applies to the folder or only to what is made inside it (`IO`): the secrets
    are what is made inside. A deny entry takes access away and never counts.
    A trustee Windows cannot turn into a SID is kept as it is spelt: somebody
    nobody can name is somebody else.
    """
    mine = {*winacl.trustees(user_sid), *_NOBODY_ELSE}
    seen: list[str] = []
    for body in winacl._ACE.findall(sddl):
        fields = body.split(";")
        if len(fields) < 6 or fields[0] != "A" or not _can_read(fields[2]):
            continue
        try:
            sid = winacl._sid_of(fields[5])
        except Exception:  # noqa: BLE001 - an entry nobody can name is an answer
            sid = fields[5]
        if sid not in mine and sid not in seen:
            seen.append(sid)
    return tuple(seen)


LockState = Literal["locked", "private", "exposed", "unknown"]


@dataclass(frozen=True)
class FolderLockCheck:
    """Who can read this server folder today, beside this account. A reading; never raises.

    `locked`: the owner-only DACL. `private`: not that DACL, but nobody beside
    this account, SYSTEM and Administrators can read it -- a folder under the
    profile, inherited. `exposed`, the one state Repair server files… offers the
    lock on: `readers` names who else can read it, and `chosen` those of them
    that are not a broad default group, whom somebody gave access on purpose.
    `unknown` carries `why` -- the folder is not there, or Windows would not
    say -- and offers nothing.
    """

    state: LockState
    why: str = ""
    readers: tuple[str, ...] = ()
    chosen: tuple[str, ...] = ()


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
    """`winacl.is_owner_only`, with Windows' own alias table (`_sid_of`), as `secure_folder` asks.

    Without `sid_of` the check folds only `SY` and `BA`, so a folder of this
    PC's built-in Administrator, whose entry Windows spells `LA`, never read as
    locked, and every stage would have locked it again (cold review, round 2).
    """
    return winacl.is_owner_only(sddl, user_sid, sid_of=winacl._sid_of)


def name_of(sid: str) -> str:
    """How the player knows a trustee: Windows' own account name, or a broad group's, or the SID."""
    try:
        return _account_name(sid)
    except Exception:  # noqa: BLE001 - a name is a courtesy; the SID is still the truth
        return BROAD_GROUPS.get(sid, sid)


def check(folder: Path) -> FolderLockCheck:
    """Read `folder`'s DACL and say who, beside this account, can read it. Never raises."""
    if not applies():
        return FolderLockCheck("unknown", "only a Windows folder has a DACL to lock")
    if not folder.is_dir():
        return FolderLockCheck("unknown", f"{folder} is not there")
    try:
        user_sid = winacl._user_sid()
        dacl = winacl._read_dacl(folder)
        if _owner_only(dacl, user_sid):
            return FolderLockCheck("locked")
        others = readers(dacl, user_sid)
    except Exception as exc:  # noqa: BLE001 - a reading must never cost the tab
        return FolderLockCheck("unknown", f"Windows would not say who may open {folder}: {exc}")
    if not others:
        return FolderLockCheck("private")
    return FolderLockCheck(
        "exposed",
        readers=tuple(name_of(sid) for sid in others),
        chosen=tuple(name_of(sid) for sid in others if sid not in BROAD_GROUPS),
    )


def lock(folder: Path) -> bool:
    """Give `folder` the owner-only DACL, handed down to all it holds. True if it changed.

    Read first and left alone when it is already locked, so a second press or a
    second stage asks Windows nothing but one read. Read back after the apply:
    Windows answering success is not the same as the folder being locked.

    Raises:
        FolderLockRefused: the apply refused, or was accepted and did not keep.
        FolderLockError: anything before or after it -- the token, a read, a
            name. Any failure of the calls is wrapped, `ctypes.ArgumentError`
            included, for `winacl.secure_folder`'s reason.
    """
    try:
        user_sid = winacl._user_sid()
        before = winacl._read_dacl(folder)
        if _owner_only(before, user_sid):
            return False
        sddl = winacl.owner_only_sddl(user_sid)
    except Exception as exc:  # noqa: BLE001 - every failure is the same sentence
        raise FolderLockError(f"{type(exc).__name__}: {exc}") from exc
    try:
        winacl._apply_dacl(folder, sddl)
    except Exception as exc:  # noqa: BLE001 - as above
        raise FolderLockRefused(f"{type(exc).__name__}: {exc}") from exc
    try:
        after = winacl._read_dacl(folder)
        kept = _owner_only(after, user_sid)
    except Exception as exc:  # noqa: BLE001 - as above
        raise FolderLockError(f"{type(exc).__name__}: {exc}") from exc
    if not kept:
        raise FolderLockRefused(f"Windows accepted the change and the folder still reads {after}")
    logger.info(f"locked {folder} to this account (T174); it was {before}")
    return True


LOCKED_LINE = (
    "Locked {folder} to your Windows account: only you, SYSTEM and Administrators can open it "
    "or anything in it, so the database password this install writes there is not readable by "
    "the computer's other accounts."
)

NOT_LOCKED_LINE = (
    "Could not lock {folder} to your Windows account ({reason}). The install carries on, and "
    "tries again before each step; until it succeeds, the files it writes there take the "
    "permissions the folder above hands down, and a folder made directly on a drive such as "
    "C:\\ lets every account on this computer read them. Once the server is installed, Repair "
    "server files… on its Server tab offers the lock again."
)

LEFT_LINE = (
    "{folder} lets {chosen} read it, which somebody set up on this PC, so the install leaves "
    "its permissions as they are. Repair server files… on the server's Server tab can lock it "
    "to your Windows account, and says first who would lose access."
)


class InstallLock:
    """The lock one install run keeps on its folder: asked before every stage, each line said once.

    The first lock that changes anything is said; a later one -- WotLK's folder,
    made again by its clone -- is logged only. A folder that grants somebody
    specific is left, and said once.

    A failure is said once. A REFUSAL of the apply (`FolderLockRefused`) is
    remembered against the folder's IDENTITY (`_identity()`), and asked again
    only when a later stage finds a different folder at the path (round 3,
    Codex): the same folder refuses the same way, and each attempt costs
    `SetNamedSecurityInfoW`'s walk of everything in it, while a folder WotLK's
    clone made anew is a new folder, and may not refuse (round 2). Any other
    failure -- the token, a read, a name -- costs next to nothing and is asked
    again at the next stage, the same folder included (round 4, Codex): a
    one-off read that failed must not leave the folder unlocked for the rest of
    the install. A lock that held is checked again with one read, as before.
    """

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._said: set[str] = set()
        self._refused_by: tuple[int, int] | None = None

    def _once(self, key: str, line: str) -> Iterator[str]:
        if key not in self._said:
            self._said.add(key)
            yield line

    def ensure(self) -> Iterator[str]:
        """Lock the folder if it is not locked and may be; yield what the player should read."""
        if not applies() or not self.folder.is_dir():
            return
        try:
            identity = _identity(self.folder)
        except OSError:
            identity = None
        if identity is not None and identity == self._refused_by:
            return
        try:
            user_sid = winacl._user_sid()
            dacl = winacl._read_dacl(self.folder)
            if _owner_only(dacl, user_sid):
                self._refused_by = None
                return
            others = readers(dacl, user_sid)
            chosen = [sid for sid in others if sid not in BROAD_GROUPS]
            if chosen:
                names = ", ".join(name_of(sid) for sid in chosen)
                if "left" not in self._said:
                    logger.info(f"left {self.folder} as it is: it also lets {names} read it")
                yield from self._once("left", LEFT_LINE.format(folder=self.folder, chosen=names))
                return
            changed = lock(self.folder)
        except Exception as exc:  # noqa: BLE001 - a lock never costs the install
            if isinstance(exc, FolderLockRefused):
                self._refused_by = identity
            if "failed" not in self._said:
                logger.warning(f"could not lock {self.folder} to this account: {exc}")
            yield from self._once("failed", NOT_LOCKED_LINE.format(folder=self.folder, reason=exc))
            return
        self._refused_by = None
        if changed:
            yield from self._once("locked", LOCKED_LINE.format(folder=self.folder))


def _identity(folder: Path) -> tuple[int, int]:
    """Which folder this is, not where: its file id and its volume (`st_ino`, `st_dev`).

    On Windows CPython fills them from the file's NTFS id and the volume's
    serial number. An NTFS id carries the MFT record's sequence number, which
    Windows bumps whenever the record is reused, so a folder removed and made
    again at the same path -- what WotLK's clone does -- has a different id.
    """
    info = os.stat(folder)
    return (info.st_ino, info.st_dev)


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


def _account_name(sid: str) -> str:  # pragma: no cover - Windows only
    """`DOMAIN\\name` for a SID string, from `LookupAccountSidW`; a well-known group has no domain.

    Windows' own names, in the PC's language, which is how the player sees them
    in the folder's Security tab.
    """
    import ctypes
    from ctypes import wintypes

    advapi32, kernel32 = winacl._dlls()
    advapi32.LookupAccountSidW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_int),
    ]
    advapi32.LookupAccountSidW.restype = wintypes.BOOL
    psid = ctypes.c_void_p()
    if not advapi32.ConvertStringSidToSidW(sid, ctypes.byref(psid)):
        raise winacl._failed(f"ConvertStringSidToSidW({sid})")
    try:
        name = ctypes.create_unicode_buffer(256)
        domain = ctypes.create_unicode_buffer(256)
        name_size = wintypes.DWORD(256)
        domain_size = wintypes.DWORD(256)
        use = ctypes.c_int()
        if not advapi32.LookupAccountSidW(
            None,
            psid,
            name,
            ctypes.byref(name_size),
            domain,
            ctypes.byref(domain_size),
            ctypes.byref(use),
        ):
            raise winacl._failed(f"LookupAccountSidW({sid})")
        return f"{domain.value}\\{name.value}" if domain.value else name.value
    finally:
        kernel32.LocalFree(psid)
