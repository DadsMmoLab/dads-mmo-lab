"""An owner-only DACL on the folders Yu'lon keeps its secrets in, on Windows (T151).

**The defect.** Every secret this app writes into its own config directory --
the command channel's GM password (`credentials/`, and `credentials/pending/`
since T138) and a kept database password (`db-secrets/`) -- is created with
POSIX mode 0o600, and on Windows that mode does nothing to the file's DACL
(measured by K.3 on 2026-09-01, `families.conf._write`'s docstring: the mode
and the `icacls` output are the same whatever mode is asked for). The files
took the ACL `%APPDATA%` hands down, which in an ordinary profile is this
account, SYSTEM and Administrators -- private -- and in a redirected or
hand-loosened profile (`Users` given read) is not.

**The shape.** The FOLDER gets an explicit, protected DACL, and every file
created inside inherits it: a file is never narrowed after it holds the
secret, because it is created under the right ACL in the first place. Asked
for when the folder is made and checked again before every secret is written
into it (a hand-loosened folder is repaired then), and on every read of a
secret, because a credential is written once and then only on a rotation: an
install from before T151 would otherwise never get it.

**The trustees** (`trustees()`): this account, SYSTEM and Administrators,
each with full control, inherited by files and subfolders. Administrators
stays, deliberately: the profile's own default grants it, an administrator
can take ownership of the folder and read it whatever its DACL says (so
removing the entry protects nothing from one), and removing it costs a backup
tool running as an administrator and an elevated Yu'lon their access. What
this closes is every OTHER account: `Users`, `Authenticated Users`,
`Everyone`, anything a profile or a person added.

**The Win32 API through `ctypes`, not `icacls`.** `pywin32` is not a
dependency (`requirements.txt`). `icacls` would need the account's SID from a
second program, prints its answer in the machine's language (so reading it
back is a parse of translated text), and flashes a console from a windowed
app unless every call carries the right creation flags. The API takes and
gives SDDL, which is the same in every locale, and each of the calls is
a seam a test replaces (`_user_sid`, `_sid_of`, `_read_dacl`, `_apply_dacl`).

Measured on Windows 11 25H2 with CPython 3.11 on 2026-09-28, with none of those
replaced: the SID, the read, the apply and the read-back all worked; Windows
rendered the result `D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;<SID>)`,
which `is_owner_only` accepted; a folder loosened with `BUILTIN\\Users:(OI)(CI)(RX)`
and the file already in it both lost `Users`, the file keeping the three as
`(I)(F)`; and a second call changed nothing. The account was an ordinary one,
so `_sid_of` on an alias (`LA`) was not reached there.

**A failure is a warning, and the secret is still written.** The caller that
matters most writes a GM password the server has ALREADY been given: a
refused write leaves that password only in this process, so the next launch
mints another and every round trip is a 401. A file written without the DACL
is exactly as private as it was before T151, and the warning names the
folder. A profile on a network share can refuse `WRITE_DAC`, which is the
likeliest way this happens.

**What it does not cover**, and why: a secret written into a SERVER folder
(`.env`, `.db_password`, a conf holding the database password, and the
`.bak`/`.repair.bak` copies of one). That folder is where the person put the
server, its ACL is theirs, and the containers read the confs in it through
Docker Desktop's file sharing -- whether a protected DACL there leaves them
readable is unmeasured. Narrowing only the copies while the conf beside them
holds the same password would protect nothing, so that is a decision of its
own rather than a corner of this one.
"""

from __future__ import annotations

import re
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from yulon.log import get_logger

logger = get_logger(__name__)

SYSTEM_SID = "S-1-5-18"
"""`NT AUTHORITY\\SYSTEM`: the services Windows itself runs as, a backup among them."""

ADMINISTRATORS_SID = "S-1-5-32-544"
"""`BUILTIN\\Administrators`. Kept, for the reasons the module docstring gives."""

_ALIASES = {"SY": SYSTEM_SID, "BA": ADMINISTRATORS_SID}
"""How Windows spells those two when it renders a DACL as SDDL.

Only the fallback `is_owner_only` reads trustees with when it is handed no
`sid_of`. On Windows `secure_folder` hands it `_sid_of`, which asks Windows for
every alias (round 2): the two above are not the only ones it renders. The
built-in Administrator of this PC comes back as `LA` and its Guest as `LG`, and
compared as text the owner's own entry never matched, so every read re-applied
the DACL and warned.
"""

_FULL_CONTROL = frozenset({"FA", "0X1F01FF"})
"""`FILE_ALL_ACCESS`: Windows renders it `FA`, and a hand-made DACL can carry the number."""

_HANDED_DOWN = frozenset({"OI", "CI"})
"""Object- and container-inherit: every file and subfolder made inside gets the entry."""

_ACE = re.compile(r"\(([^()]*)\)")


def trustees(user_sid: str) -> tuple[str, ...]:
    """Who a secret folder is for, in the order its DACL lists them."""
    return (SYSTEM_SID, ADMINISTRATORS_SID, user_sid)


def owner_only_sddl(user_sid: str) -> str:
    """The DACL asked for: protected (`P`), full control for `trustees()`, handed down (`OICI`)."""
    aces = "".join(f"(A;OICI;FA;;;{sid})" for sid in trustees(user_sid))
    return f"D:P{aces}"


def _alias_sid(trustee: str) -> str:
    """`_ALIASES`' fold: the two it knows become SIDs, anything else is kept as spelt."""
    return _ALIASES.get(trustee, trustee)


def is_owner_only(sddl: str, user_sid: str, *, sid_of: Callable[[str], str] | None = None) -> bool:
    """Is this DACL, as Windows renders it, the one `owner_only_sddl` asks for?

    Not a string comparison: the read-back spells well-known accounts by alias
    (`SY`, `BA`, and this PC's built-in Administrator as `LA`), adds `AI` beside
    `P`, and may order the flags differently. So it is read for what it means:
    protected, exactly one allow entry of full control for each trustee, handed
    down to files and subfolders, and nothing else at all. Trustees are
    compared as SIDs, each turned into one by `sid_of` (`_sid_of` on Windows).
    """
    resolve = sid_of if sid_of is not None else _alias_sid
    if not sddl.startswith("D:"):
        return False
    flags, _, rest = sddl[2:].partition("(")
    if "P" not in flags:
        return False
    seen: list[str] = []
    for body in _ACE.findall("(" + rest):
        fields = body.split(";")
        if len(fields) < 6:
            return False
        kind, ace_flags, rights, trustee = fields[0], fields[1], fields[2], fields[5]
        handed = {ace_flags[i : i + 2] for i in range(0, len(ace_flags), 2)}
        if kind != "A" or handed != _HANDED_DOWN or rights.upper() not in _FULL_CONTROL:
            return False
        seen.append(resolve(trustee))
    return sorted(seen) == sorted(trustees(user_sid))


_checked_on_read: set[str] = set()
"""The folders this launch has already asked Windows about (round 2).

`live_channel()` reads the credential on every GM press, and the tab builds its
channel on the GUI thread, so a read asks Windows about its folder only the
first time in a launch. A write always asks again, since it is the moment the
secret lands, and its answer counts for the reads after it. A failed attempt is
remembered too: a profile that refuses `WRITE_DAC` warns once a launch, not on
every press. Keyed by the absolute path."""

_checked_lock = threading.Lock()


def secure_folder(folder: Path, *, reading: bool = False) -> None:
    """Give `folder` the owner-only DACL on Windows, unless it already has it. Never raises.

    `reading=True` for a read of a secret: once a launch per folder
    (`_checked_on_read`). Every write asks, before the secret is created.

    A no-op anywhere else: there the file's own 0o600 mode is real and is what
    keeps it private. A folder that is not there is left alone -- the writers
    make it first, and a reader with no folder has nothing to protect.

    Read first, applied only if it differs, then read back: the INFO line says
    what Windows now holds, not what was asked for. Every failure -- a refused
    `WRITE_DAC`, a network profile, a `ctypes` signature that does not match
    (`ctypes.ArgumentError` is not an `OSError`) -- is a warning naming the
    folder, for the module docstring's reason.
    """
    if not _on_windows() or not folder.is_dir():
        return
    key = str(folder.absolute())
    with _checked_lock:
        if reading and key in _checked_on_read:
            return
        _checked_on_read.add(key)
    try:
        user_sid = _user_sid()
        before = _read_dacl(folder)
        if is_owner_only(before, user_sid, sid_of=_sid_of):
            return
        _apply_dacl(folder, owner_only_sddl(user_sid))
        after = _read_dacl(folder)
    except Exception as exc:  # noqa: BLE001 - a narrowing never costs the write it guards
        logger.warning(
            f"could not make {folder} readable by this account only "
            f"({type(exc).__name__}: {exc}); what is saved there keeps the permissions "
            "it inherits from the folder above it"
        )
        return
    if is_owner_only(after, user_sid, sid_of=_sid_of):
        logger.info(f"made {folder} readable by this account only (T151); it was {before}")
    else:
        logger.warning(
            f"asked Windows to make {folder} readable by this account only, and it "
            f"still reads {after}"
        )


# -- the calls into Windows --------------------------------------------------
#
# Each is its own function so a test can stand in for it: no box this suite runs
# on has the Win32 security API. `getattr` for `ctypes.WinDLL`, `WinError` and
# `get_last_error`, which exist only on Windows, for the reason
# `platform._mapped_network_drive()` gives: naming them is an error under the
# `mypy --platform linux` CI runs, and a `type: ignore` for it is an error under
# `--platform win32`.


def _on_windows() -> bool:
    return sys.platform == "win32"


_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x4
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SDDL_REVISION_1 = 1
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
"""`TokenUser` in `TOKEN_INFORMATION_CLASS`: the account the process runs as, elevated or not."""


def _dlls() -> tuple[Any, Any]:  # pragma: no cover - Windows only
    """`advapi32` and `kernel32` with every signature this module calls declared.

    Declared rather than left to ctypes' `int` default for the reason
    `selfupdate.layout._kernel32()` gives: a pointer passed through an `int`
    loses its top half on 64-bit Windows. Every buffer these calls hand back is
    a `c_void_p` freed with `LocalFree`, which is what they document.
    """
    import ctypes
    from ctypes import wintypes

    windll = getattr(ctypes, "WinDLL")  # noqa: B009 - Windows-only attribute
    advapi32 = windll("advapi32", use_last_error=True)
    kernel32 = windll("kernel32", use_last_error=True)
    out = ctypes.POINTER(ctypes.c_void_p)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, out]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, out]
    advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        out,
        out,
        out,
        out,
        out,
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        out,
        ctypes.POINTER(wintypes.ULONG),
    ]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        out,
        ctypes.POINTER(wintypes.ULONG),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        out,
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    return advapi32, kernel32


def _failed(call: str, code: int | None = None) -> OSError:  # pragma: no cover - Windows only
    """The `OSError` Windows' own message makes, naming the call that failed."""
    import ctypes

    if code is None:
        code = getattr(ctypes, "get_last_error")()  # noqa: B009 - Windows-only
    error: OSError = getattr(ctypes, "WinError")(code)  # noqa: B009 - Windows-only
    error.strerror = f"{call}: {error.strerror}"
    return error


def _wide_string(address: int | None) -> str:  # pragma: no cover - Windows only
    """The string a call that succeeded handed back; a NULL one is refused, not read."""
    import ctypes

    if not address:
        raise OSError("Windows answered success and handed back no string")
    return ctypes.wstring_at(address)


def _user_sid() -> str:  # pragma: no cover - Windows only
    """The SID of the account this process runs as, `S-1-5-21-...`, from its own token."""
    import ctypes
    from ctypes import wintypes

    advapi32, kernel32 = _dlls()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
    ):
        raise _failed("OpenProcessToken")
    try:
        size = wintypes.DWORD(0)
        # Asked once with no buffer, for the size: that call "fails" with
        # ERROR_INSUFFICIENT_BUFFER by design, so only the second is checked.
        advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, _TOKEN_USER, buffer, size, ctypes.byref(size)):
            raise _failed("GetTokenInformation")
        # TOKEN_USER begins with SID_AND_ATTRIBUTES, which begins with the PSID.
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents
        text = ctypes.c_void_p()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise _failed("ConvertSidToStringSidW")
        try:
            return _wide_string(text.value)
        finally:
            kernel32.LocalFree(text)
    finally:
        kernel32.CloseHandle(token)


def _sid_of(trustee: str) -> str:  # pragma: no cover - Windows only
    """A DACL entry's trustee as a SID string: an alias (`SY`, `LA`, ...) as Windows maps it.

    `ConvertStringSidToSidW` takes either a SID string or any SDDL alias and
    answers the SID -- Windows' own table, so `LA` becomes THIS PC's account
    domain plus RID 500, which is what it means. Guessing it from the RID was
    the alternative, and it is wrong for a domain account that also ends in
    -500. A SID string is handed back as it is, without a call.
    """
    if trustee.upper().startswith("S-"):
        return trustee
    import ctypes

    advapi32, kernel32 = _dlls()
    sid = ctypes.c_void_p()
    if not advapi32.ConvertStringSidToSidW(trustee, ctypes.byref(sid)):
        raise _failed(f"ConvertStringSidToSidW({trustee})")
    try:
        text = ctypes.c_void_p()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise _failed("ConvertSidToStringSidW")
        try:
            return _wide_string(text.value)
        finally:
            kernel32.LocalFree(text)
    finally:
        kernel32.LocalFree(sid)


def _read_dacl(folder: Path) -> str:  # pragma: no cover - Windows only
    """`folder`'s DACL as SDDL, `D:PAI(A;OICI;FA;;;SY)...`, the way Windows renders it."""
    import ctypes

    advapi32, kernel32 = _dlls()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    code = advapi32.GetNamedSecurityInfoW(
        str(folder),
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if code:
        raise _failed("GetNamedSecurityInfoW", code)
    try:
        text = ctypes.c_void_p()
        if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, _SDDL_REVISION_1, _DACL_SECURITY_INFORMATION, ctypes.byref(text), None
        ):
            raise _failed("ConvertSecurityDescriptorToStringSecurityDescriptorW")
        try:
            return _wide_string(text.value)
        finally:
            kernel32.LocalFree(text)
    finally:
        kernel32.LocalFree(descriptor)


def _apply_dacl(folder: Path, sddl: str) -> None:  # pragma: no cover - Windows only
    """Set `folder`'s DACL to `sddl`, protected from what its parent hands down.

    `PROTECTED_DACL_SECURITY_INFORMATION` is what turns inheritance off: the
    `P` in the SDDL alone does not reach `SetNamedSecurityInfoW`. The call also
    carries the folder's inheritable entries down to what is already inside it,
    replacing the ones those files inherited, so a secret saved before T151 is
    narrowed with its folder.
    """
    import ctypes
    from ctypes import wintypes

    advapi32, kernel32 = _dlls()
    descriptor = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), None
    ):
        raise _failed("ConvertStringSecurityDescriptorToSecurityDescriptorW")
    try:
        present = wintypes.BOOL()
        defaulted = wintypes.BOOL()
        dacl = ctypes.c_void_p()
        if not advapi32.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
        ):
            raise _failed("GetSecurityDescriptorDacl")
        if not present.value or not dacl.value:
            # A NULL DACL means "everyone may do everything": never hand that on.
            raise OSError(f"the DACL built from {sddl} came back empty")
        code = advapi32.SetNamedSecurityInfoW(
            str(folder),
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )
        if code:
            raise _failed("SetNamedSecurityInfoW", code)
    finally:
        kernel32.LocalFree(descriptor)
