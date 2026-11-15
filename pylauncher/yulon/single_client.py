"""The player's own client, found where its installer left it: registry, version, writable folders.

For a single server whose client Windows' registry names (`catalog.ClientRegistry`):
FFXI's installer writes its folder into HKLM, and xiloader, the loader Play starts in
place of PlayOnline, reads it from there itself. So the tab never asks for a folder;
it reads the same value to say which client will start, how old it is next to the
version the pinned server accepts (`catalog.ClientVersion`), and whether the folders
the game writes at play are writable by the player (`SinglePlay.writable`).

That last one is the one change Yu'lon makes to the client, and only on the player's
yes: an install under Program Files leaves `USER`, `SYS` and `TEMP` to administrators,
and the game stops with FFXI-3117 without them (measured 2026-10-17). The first Play
grants the player Modify on those folders, making any that are missing, behind ONE
administrator prompt (`platform.elevated_admin_batch()`); every Play after that needs
none. Nothing else in the client folder is touched, and its files stay administrators'.

Reading is cheap and has no side effects; the grant is the only write, and it goes
through `grant()` alone.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from yulon import platform, runner, winacl
from yulon.catalog.catalog import SingleEntry
from yulon.log import get_logger

logger = get_logger(__name__)

VERSION = re.compile(r"(?<!\d)(\d{8})_(\d+)(?!\d)")
"""A client version as FFXI's patch list spells it: `30261006_0`."""

PROBE = ".yulon-write-probe"
"""The file `writable()` creates and removes to learn whether the player may write a folder.

A real write rather than `os.access()`: on Windows `os.access()` reads the read-only
attribute and not the folder's DACL, so it says yes to a Program Files folder the
player cannot write.
"""

GRANT = "(OI)(CI)M"
"""What `icacls` grants the player on each folder: Modify, inherited by its files and subfolders."""

NOT_FOUND = (
    "{name}'s client is not installed on this computer: Windows has no PlayOnline entry for "
    "it. Install it with Square Enix's installer, start it once through PlayOnline so it "
    "updates, then come back here. Yu'lon never downloads a client."
)


@dataclass(frozen=True)
class FoundClient:
    """A client install the registry names: its folder, its loader language and its region."""

    folder: Path
    lang: str
    label: str


@dataclass(frozen=True)
class GrantReport:
    """What `grant()` did: the folders made writable, and what went wrong instead."""

    granted: tuple[Path, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems


RegistryReader = Callable[[str, str, str], str | None]
"""(key, subkey, value) under HKLM, 32-bit view -> the value's text, or None if absent."""


def read_hklm_32(key: str, subkey: str, value: str) -> str | None:  # pragma: no cover - Windows
    """One string value from HKLM's 32-bit view (`WOW6432Node` on 64-bit Windows), or None.

    The 32-bit view because PlayOnline's installer is a 32-bit program and writes
    there; asked through `KEY_WOW64_32KEY` rather than by spelling `WOW6432Node`,
    which a 32-bit Windows does not have.
    """
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            f"{key}\\{subkey}",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
        ) as handle:
            found, kind = winreg.QueryValueEx(handle, value)
    except OSError:
        return None
    return str(found) if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and found else None


def find_client(
    entry: SingleEntry,
    *,
    os_name: str | None = None,
    read: RegistryReader = read_hklm_32,
) -> FoundClient | None:
    """The client install the registry names for `entry`, first region first; None if none.

    None off Windows, for an entry with no registry block, and when the folder the
    registry names is gone (an uninstall that left its key behind).
    """
    play = entry.play
    registry = play.registry if play is not None else None
    if registry is None or (os_name if os_name is not None else platform.detect()) != "windows":
        return None
    for region in registry.regions:
        text = read(region.key, registry.subkey, registry.value)
        if not text:
            continue
        folder = Path(text.strip().rstrip("\\/"))
        if folder.is_dir():
            return FoundClient(folder=folder, lang=region.lang, label=region.label)
        logger.info(f"{entry.id}: the registry names {folder}, which is not a folder")
    return None


def client_version(entry: SingleEntry, folder: Path) -> str | None:
    """The client's version: the newest `YYYYMMDD_N` in its patch list, or None if unreadable."""
    play = entry.play
    if play is None or play.version is None:
        return None
    try:
        text = (folder / play.version.file).read_bytes().decode("latin-1")
    except OSError:
        return None
    found = [(int(m.group(1)), int(m.group(2))) for m in VERSION.finditer(text)]
    if not found:
        return None
    date, number = max(found)
    return f"{date:08d}_{number}"


def _key(version: str) -> tuple[int, int]:
    date, _, number = version.partition("_")
    return int(date), int(number)


def version_problem(entry: SingleEntry, folder: Path) -> str | None:
    """Why the client is too old for the pinned server, or None (including when unknown).

    Unknown is not a refusal: the server itself says so at login if it is, and a
    patch list this reader cannot parse must not keep a working client from starting.
    """
    play = entry.play
    if play is None or play.version is None:
        return None
    have = client_version(entry, folder)
    if have is None or _key(have) >= _key(play.version.minimum):
        return None
    return (
        f"Your {entry.name} client is version {have}, and this server accepts "
        f"{play.version.minimum} or newer. Start it once through PlayOnline so it updates, "
        "then press Play again."
    )


def describe(
    entry: SingleEntry, *, find: Callable[[SingleEntry], FoundClient | None] = find_client
) -> str:
    """One line for the server's tab: which client Play will start, or why there is none."""
    found = find(entry)
    if found is None:
        return NOT_FOUND.format(name=entry.name)
    version = client_version(entry, found.folder)
    said = f"Client: {found.folder} ({found.label}" + (f", version {version})" if version else ")")
    problem = version_problem(entry, found.folder)
    return f"{said}. {problem}" if problem else said


def writable(folder: Path) -> bool:
    """Whether this account can create a file in `folder` (`PROBE`); False if it is missing."""
    if not folder.is_dir():
        return False
    probe = folder / PROBE
    try:
        with probe.open("x", encoding="utf-8") as handle:
            handle.write("Yu'lon checks it can write here, then deletes this file.\n")
    except FileExistsError:
        pass
    except OSError:
        return False
    try:
        probe.unlink()
    except OSError:
        logger.warning(f"could not remove {probe}")
    return True


def unwritable(entry: SingleEntry, client: Path) -> tuple[Path, ...]:
    """The folders the game writes at play that the player cannot write yet, in catalog order."""
    play = entry.play
    if play is None:
        return ()
    return tuple(client / name for name in play.writable if not writable(client / name))


def grant_commands(folders: Sequence[Path], user_sid: str) -> list[list[str]]:
    """The commands that make `folders` the player's to write: the missing made, then icacls.

    Named by SID (`*S-1-5-21-...`), which `icacls` reads the same in every locale.

    A folder that is missing is made by .NET `CreateDirectory` in PowerShell, one literal
    argument and no `cmd.exe` in the way (T888): `cmd /c mkdir` rejoined its arguments into a command line
    it parsed again, so `C:\\a&b` ran `b` as Administrator; refusing every path that holds
    a `cmd` metacharacter refused `C:\\Program Files (x86)`, FFXI's default. Inside a
    single-quoted PowerShell literal nothing is parsed, `''` spells one `'`, so the path
    goes whole.
    """
    commands: list[list[str]] = []
    for folder in folders:
        if not folder.is_dir():
            commands.append(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    "[System.IO.Directory]::CreateDirectory("
                    + platform.powershell_literal(str(folder))
                    + ") | Out-Null",
                ]
            )
        commands.append(["icacls", str(folder), "/grant", f"*{user_sid}:{GRANT}"])
    return commands


def grant(
    entry: SingleEntry,
    client: Path,
    *,
    os_name: str | None = None,
    user_sid: Callable[[], str] = winacl.user_sid,
    run: Callable[[list[str]], subprocess.CompletedProcess[str]] = runner.run,
    batch: Callable[[list[list[str]]], tuple[list[str], Path] | None] = (
        platform.elevated_admin_batch
    ),
) -> GrantReport:
    """Let the player write the folders the game writes at play, behind one administrator prompt.

    Only what `unwritable()` names, and only on Windows: a folder that is already
    the player's is left exactly as it is. An administrator Yu'lon runs the commands
    as they are; otherwise `batch` wraps them in one UAC prompt the player may decline.
    """
    if (os_name if os_name is not None else platform.detect()) != "windows":
        return GrantReport()
    folders = unwritable(entry, client)
    if not folders:
        return GrantReport()
    commands = grant_commands(folders, user_sid())
    wrapped = batch(commands)
    if wrapped is None:
        failed = []
        for command in commands:
            proc = run(command)
            if proc.returncode != 0:
                failed.append(f"{platform.command_text(command)}: {proc.stdout.strip()}")
        return _report(entry, client, folders, failed)
    argv, results_path = wrapped
    try:
        try:
            proc = run(argv)
        except OSError as exc:
            proc = subprocess.CompletedProcess(argv, 1, "", str(exc))
        if proc.returncode == platform.UAC_DECLINED:
            return GrantReport(problems=(DECLINED,))
        results = platform.read_batch_results(results_path, len(commands))
    finally:
        try:
            results_path.unlink()
        except OSError:
            pass
    if proc.returncode != 0 or results is None:
        return GrantReport(
            problems=(
                "The Windows administrator prompt did not say how it went. Run these in an "
                "administrator terminal, then press Play again:\n"
                + "\n".join(platform.command_text(c) for c in commands),
            )
        )
    failed = [
        f"{platform.command_text(command)}: {out}"
        for command, (rc, out) in zip(commands, results, strict=True)
        if rc != 0
    ]
    return _report(entry, client, folders, failed)


DECLINED = (
    "You said No to the Windows administrator prompt, so the game's settings folders were not "
    "made writable and nothing was started. Press Play again and choose Yes; it is asked once."
)


def _report(
    entry: SingleEntry, client: Path, folders: Sequence[Path], failed: Sequence[str]
) -> GrantReport:
    """What a grant achieved, checked the way Play will need it: by writing to each folder."""
    still = unwritable(entry, client)
    granted = tuple(f for f in folders if f not in still)
    problems = list(failed)
    if still and not failed:
        problems.append(
            "These folders are still not writable after the grant: "
            + ", ".join(str(f) for f in still)
        )
    return GrantReport(granted=granted, problems=tuple(problems))
