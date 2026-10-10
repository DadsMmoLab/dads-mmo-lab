"""Bringing a client add-on into a staging folder safely: from a zip, a zip link or a folder (T613).

The other half of `addon_layout`: that module reads a folder, this one makes
sure the folder it reads is one Yu'lon would put into a game client. No Qt;
cancel and progress are callables the view supplies.

* **A local zip** (`stage_zip`) is unpacked into a NEW, empty folder under
  `client_packs.cache_dir()/addons/staging/`, never into the client.
* **A zip link** (`stage_link`) is downloaded first, into its own new folder
  under `.../addons/downloads/`, as a `.part` renamed once whole, then unpacked
  the same way and the download removed. Only https, and only to GitHub, GitLab
  and Codeberg plus GitHub's own download hosts (`ALLOWED_HOSTS`, owner
  2026-10-09 Q2), checked before the first request and again on every redirect
  by the self-updater's opener (`selfupdate.fetch._https_only_opener`), with its
  stall watchdog and a size bound.
* **A folder** the player chose, or a clone, is not copied here (the installer's
  copy is `module_source.copy_folder`); `check_folder` holds it to the same caps
  first.

**Everything is checked before anything is written, and a refusal leaves
nothing behind.** A zip is refused whole for one bad member: a name that leaves
the folder (`client_packs._clean_rel`), a link, two names that fold to one, a
file where a folder is needed, a password, too deep, too many files, too large
unpacked (the declared sizes are added up before a byte is unpacked, and the
bytes are counted again while writing), a large member that compresses too well
(a zip bomb), or a program file: by suffix, or by its content whatever its
suffix (a Windows PE header, ELF, Mach-O; `program_kind`). A zip inside the zip
is copied as a file and never opened. A zip whose names hold no `/` at all was
made by an old Windows tool, and its `\\` is read as the separator; a zip that
mixes both is refused.
"""

from __future__ import annotations

import hashlib
import lzma
import os
import re
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from yulon import client_packs, links, rmtree
from yulon.addon_layout import NOTHING_CHANGED, is_windows_device
from yulon.after_stop import StopTookEffect
from yulon.log import get_logger
from yulon.selfupdate import fetch
from yulon.update import _can_only_go_where_it_says, _Deadline

logger = get_logger(__name__)

ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "github.com",
        "gitlab.com",
        "codeberg.org",
        "codeload.github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)
"""Where an add-on zip may be fetched from, and every redirect may go (owner 2026-10-09, Q2).

The three forges `manifest.ALLOWED_REPO_HOSTS` names, plus the hosts GitHub sends
a download on to: an archive zip (`/archive/refs/heads/<b>.zip`) to codeload, a
release asset (`/releases/download/...`) to its object store under either name
GitHub has used. No CurseForge CDN and no Wago: anything else the player
downloads in a browser and gives Yu'lon as a zip."""

MAX_DOWNLOAD_BYTES = 100 * 1024**2
MAX_UNPACKED_BYTES = 500 * 1024**2
MAX_FILES = 20_000
MAX_DEPTH = 16
"""Folders above a file inside the source: `a/b/c.lua` is two deep."""
BOMB_MIN_BYTES = 10 * 1024**2
BOMB_RATIO = 200
"""A member over `BOMB_MIN_BYTES` unpacked that is more than `BOMB_RATIO` times its packed size."""

PROGRAM_SUFFIXES = frozenset(
    {".exe", ".dll", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".scr", ".msi", ".sys", ".so"}
    | {".dylib", ".lnk", ".js", ".hta", ".reg", ".jar"}
)
"""What a WoW add-on never carries: it is `.toc`, `.lua`, `.xml` and media.

A `.dll` beside the client is how Centurion's own `client-tweaks` changes the
game (`dinput8.dll`), which is exactly why a download must never place one."""

HEAD_BYTES = 1024
"""How much of each file's start is read to tell a program by its content, whatever its name."""

_MACHO = frozenset(
    {
        bytes.fromhex(magic)[::order]
        for magic in ("feedface", "feedfacf", "cafebabe")
        for order in (1, -1)
    }
)
"""Mach-O (32/64-bit) and universal-binary magic, in both byte orders."""


def program_kind(head: bytes, size: int) -> str:
    """What program `head` (a file's first `HEAD_BYTES`, of `size` in all) is, or empty.

    A Windows program is `MZ` whose e_lfanew (the 4 bytes at 0x3C) points inside the
    file at `PE\\0\\0`: `MZ` alone is not enough, since `MZ = 'Mozambique'` is Lua. A
    header past the bytes read but inside the file is taken for a program, the
    cautious answer: text cannot put one there without being hundreds of MB long.
    """
    if head.startswith(b"\x7fELF"):
        return "a Linux program inside"
    if head[:4] in _MACHO:
        return "a macOS program inside"
    if head.startswith(b"MZ") and len(head) >= 0x40:
        pe = int.from_bytes(head[0x3C:0x40], "little")
        if pe + 4 <= size and (pe + 4 > len(head) or head[pe : pe + 4] == b"PE\0\0"):
            return "a Windows program inside"
    return ""


_WINDOWS_FORBIDDEN = frozenset('<>"|?*')
"""Characters Windows refuses in a name that `_clean_rel` does not already refuse (`:` and `\\`)."""

_REDIRECTED_TO = re.compile(r"redirected (?:to '([^']*)'|off https, to '([^']*)')")


class AddonRefusal(RuntimeError):
    """A refusal worded for the player, ending with `NOTHING_CHANGED`. Nothing else leaves here."""


class AddonCancelled(AddonRefusal, StopTookEffect):
    """The player pressed Cancel; what arrived was removed. The player's own press (T250)."""


@dataclass(frozen=True)
class Staged:
    """An add-on source unpacked into its own staging folder, and the zip's SHA-256."""

    root: Path
    sha256: str

    def discard(self) -> None:
        """Remove the staging folder, once its add-ons are installed or the player said no."""
        _remove(self.root)


@dataclass(frozen=True)
class Tree:
    """What a folder holds, as the caps count it: its files and their bytes, `.git` aside."""

    files: int
    bytes: int


def staging_dir() -> Path:
    return client_packs.cache_dir() / "addons" / "staging"


def downloads_dir() -> Path:
    return client_packs.cache_dir() / "addons" / "downloads"


STALE_SECONDS = 60 * 60
"""How old a staging or download folder must be before the start-up sweep removes it.

An hour: no unpack or download of an add-on within the caps takes that long, so a
folder this old was left by a Yu'lon that stopped (a crash, a power cut) and not
by one still writing into it."""


def _never() -> bool:
    return False


def sweep_stale(*, now: float | None = None) -> list[Path]:
    """Remove the staging and download folders a stopped Yu'lon left; the folders removed.

    T613 PR-2: every press removes its own folder whatever happens, but a crash or a
    power cut between the unpack and that removal leaves one under
    `<cache>/addons/`, up to 500 MB, that nothing would ever remove. Run once at
    start, off the GUI thread. A folder younger than `STALE_SECONDS` is left: it may
    belong to a press still running. Never raises; what could not be removed is logged.
    """
    when = time.time() if now is None else now
    removed: list[Path] = []
    for parent in (staging_dir(), downloads_dir()):
        try:
            children = sorted(parent.iterdir()) if parent.is_dir() else []
        except OSError:
            logger.warning("addon staging: could not list %s", parent, exc_info=True)
            continue
        for child in children:
            try:
                if when - child.lstat().st_mtime < STALE_SECONDS:
                    continue
            except OSError:
                continue
            _remove(child)
            if not os.path.lexists(child):
                removed.append(child)
    return removed


# ------------------------------------------------------------------ a local zip


def stage_zip(path: Path, *, cancelled: Callable[[], bool] = _never) -> Staged:
    """`path` unpacked into a new staging folder, or `AddonRefusal` with nothing left behind."""
    return _stage(path, label=path.name, cancelled=cancelled)


def _stage(path: Path, *, label: str, cancelled: Callable[[], bool]) -> Staged:
    try:
        digest = _sha256(path)
        if not zipfile.is_zipfile(path):
            raise AddonRefusal(f"{label} is not a zip file Yu'lon can read. {NOTHING_CHANGED}")
        with zipfile.ZipFile(path) as archive:
            members = _plan(archive, label)
            total = _declared_total(members)
            parent = staging_dir()
            parent.mkdir(parents=True, exist_ok=True)
            _refuse_without_room(label, parent, total)
            staging = Path(tempfile.mkdtemp(dir=parent))
            try:
                _unpack(archive, members, staging, label=label, cancelled=cancelled)
            except BaseException:
                _remove(staging)
                raise
    except (zipfile.BadZipFile, zlib.error, lzma.LZMAError, EOFError, NotImplementedError) as exc:
        raise AddonRefusal(
            f"{label} is damaged or uses a kind of zip Yu'lon cannot read ({exc}). Download it "
            f"again, or unpack it yourself and choose the folder. {NOTHING_CHANGED}"
        ) from exc
    except OSError as exc:
        raise AddonRefusal(_os_sentence(exc, path)) from exc
    return Staged(root=staging, sha256=digest)


@dataclass(frozen=True)
class _Member:
    info: zipfile.ZipInfo
    rel: PurePosixPath


def _plan(archive: zipfile.ZipFile, label: str) -> list[_Member]:
    """Which member is unpacked where, refused whole if any one is unsafe. Writes nothing.

    Names are read from `orig_filename`, the bytes the zip holds: on Windows
    `ZipInfo.filename` has already turned `\\` into `/`, which would hide a mix.
    """
    infos = archive.infolist()
    raw = [info.orig_filename for info in infos]
    slash = any("/" in name for name in raw)
    backslash = any("\\" in name for name in raw)
    if slash and backslash:
        raise AddonRefusal(
            f"{label} mixes / and \\ in its names, so Yu'lon cannot tell where its files go. "
            f"{NOTHING_CHANGED}"
        )
    members: list[_Member] = []
    files: dict[str, str] = {}
    folders: dict[str, str] = {}
    for info in infos:
        name = info.orig_filename
        spelled = name.replace("\\", "/") if backslash else name
        is_dir = spelled.endswith("/")
        rel = client_packs._clean_rel(spelled.rstrip("/")) if spelled.rstrip("/") else None
        if rel is None:
            raise AddonRefusal(
                f"{name!r} in the zip would land outside the add-on's folder. {NOTHING_CHANGED}"
            )
        if any(ch in _WINDOWS_FORBIDDEN or ord(ch) < 0x20 for ch in rel.as_posix()) or any(
            is_windows_device(part) for part in rel.parts
        ):
            raise AddonRefusal(
                f"{name!r} in the zip has a name a game client's folder on Windows cannot hold. "
                f"{NOTHING_CHANGED}"
            )
        if client_packs._is_symlink_member(info):
            raise AddonRefusal(
                f"{name!r} in the zip is a link, not a file; Yu'lon copies only real files. "
                f"{NOTHING_CHANGED}"
            )
        if info.flag_bits & 0x1:
            raise AddonRefusal(
                f"{name!r} in the zip is locked with a password, which no add-on is. "
                f"{NOTHING_CHANGED}"
            )
        for depth in range(1, len(rel.parts)):
            folders.setdefault(PurePosixPath(*rel.parts[:depth]).as_posix().casefold(), name)
        if is_dir:
            continue
        if len(rel.parts) - 1 > MAX_DEPTH:
            raise AddonRefusal(
                f"{name!r} in the zip is {len(rel.parts) - 1} folders deep; Yu'lon takes add-ons "
                f"up to {MAX_DEPTH} folders deep. {NOTHING_CHANGED}"
            )
        _refuse_program_name(rel.as_posix(), rel.suffix)
        if info.file_size > BOMB_MIN_BYTES and info.file_size > info.compress_size * BOMB_RATIO:
            raise AddonRefusal(
                f"{name!r} in the zip unpacks to {_size(info.file_size)} from "
                f"{_size(info.compress_size)}, which no add-on does. {NOTHING_CHANGED}"
            )
        key = rel.as_posix().casefold()
        if key in files:
            raise AddonRefusal(
                f"{files[key]!r} and {name!r} in the zip differ only in case, and a game "
                f"client's folder on Windows cannot hold both. {NOTHING_CHANGED}"
            )
        files[key] = name
        members.append(_Member(info, rel))
    for key, name in files.items():
        if key in folders:
            raise AddonRefusal(
                f"{name!r} in the zip is a file where {folders[key]!r} needs a folder of that "
                f"name. {NOTHING_CHANGED}"
            )
    total = _declared_total(members)
    if len(members) > MAX_FILES or total > MAX_UNPACKED_BYTES:
        raise AddonRefusal(_too_big(label, _size(total), len(members)))
    return members


def _declared_total(members: list[_Member]) -> int:
    """What the members say they unpack to, added up before anything is written."""
    return sum(member.info.file_size for member in members)


def _unpack(
    archive: zipfile.ZipFile,
    members: list[_Member],
    staging: Path,
    *,
    label: str,
    cancelled: Callable[[], bool],
) -> None:
    written = 0
    for member in members:
        if cancelled():
            raise AddonCancelled(f"Unpacking {label} was cancelled. {NOTHING_CHANGED}")
        target = staging.joinpath(*member.rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        written = _write_member(archive, member, target, written, label=label, cancelled=cancelled)


def _write_member(
    archive: zipfile.ZipFile,
    member: _Member,
    target: Path,
    written: int,
    *,
    label: str,
    cancelled: Callable[[], bool] = _never,
) -> int:
    """Unpack one member NEW onto `target`, counting its bytes; the running total after it.

    Cancel is asked before every chunk as well as before every member (T613 PR-2):
    one 400 MB member is otherwise a wait the player cannot stop.
    """
    own = 0
    with archive.open(member.info) as source, target.open("xb") as out:
        head = b""
        judged = False
        while chunk := source.read(client_packs.CHUNK_BYTES):
            if cancelled():
                raise AddonCancelled(f"Unpacking {label} was cancelled. {NOTHING_CHANGED}")
            if not judged:
                head += chunk[: HEAD_BYTES - len(head)]
                if len(head) >= HEAD_BYTES:
                    judged = True
                    _refuse_program_content(member.rel.as_posix(), head, member.info.file_size)
            own += len(chunk)
            written += len(chunk)
            if own > member.info.file_size:
                # Untested defence: CPython's reader stops at the declared size and ends a
                # member that lied about it with "Bad CRC-32", so this cannot be reached
                # through `zipfile` today; it stays for a reader that behaves otherwise.
                raise AddonRefusal(
                    f"{member.info.orig_filename!r} in the zip unpacks to more than it says, "
                    f"which no add-on does. {NOTHING_CHANGED}"
                )
            if written > MAX_UNPACKED_BYTES:
                raise AddonRefusal(
                    f"{label} would unpack to more than {_size(MAX_UNPACKED_BYTES)}; Yu'lon takes "
                    f"add-ons up to {_size(MAX_UNPACKED_BYTES)} and {MAX_FILES:,} files. "
                    f"{NOTHING_CHANGED}"
                )
            out.write(chunk)
        if not judged:
            _refuse_program_content(member.rel.as_posix(), head, own)
    return written


# ------------------------------------------------------------------ a zip link


def check_link(url: str) -> None:
    """Refuse a zip link before any request: plain https, on a host `ALLOWED_HOSTS` names."""
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        host = None
    if host is None or not _can_only_go_where_it_says(url, host):
        raise AddonRefusal(
            f"{url[:200]} is not a plain https link (no port, no login, no .. in it), so Yu'lon "
            "did not ask it. Yu'lon downloads add-ons only from GitHub, GitLab and Codeberg, "
            f"over https. Download it in your browser, then choose the zip. {NOTHING_CHANGED}"
        )
    if host not in ALLOWED_HOSTS:
        raise AddonRefusal(
            f"Yu'lon downloads add-ons only from GitHub, GitLab and Codeberg, and {host} is none "
            f"of them. Download it in your browser, then choose the zip. {NOTHING_CHANGED}"
        )


def stage_link(
    url: str,
    *,
    opener: client_packs.Opener = client_packs._open,
    progress: client_packs.Progress | None = None,
    cancelled: Callable[[], bool] = _never,
) -> Staged:
    """The zip at `url` downloaded and unpacked into a new staging folder; the download removed."""
    check_link(url)
    label = _link_label(url)
    try:
        parent = downloads_dir()
        parent.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(dir=parent))
    except OSError as exc:
        raise AddonRefusal(_os_sentence(exc, downloads_dir())) from exc
    try:
        try:
            zipped = _download(
                url, folder, label=label, opener=opener, progress=progress, cancelled=cancelled
            )
        except OSError as exc:
            raise AddonRefusal(_os_sentence(exc, folder)) from exc
        if not zipfile.is_zipfile(zipped):
            raise AddonRefusal(
                f"{url[:200]} did not send a zip file. A link to a repository's page is added as "
                f"a git link instead; a file's link must end in .zip. {NOTHING_CHANGED}"
            )
        return _stage(zipped, label=label, cancelled=cancelled)
    finally:
        _remove(folder)


def _download(
    url: str,
    folder: Path,
    *,
    label: str,
    opener: client_packs.Opener,
    progress: client_packs.Progress | None,
    cancelled: Callable[[], bool],
) -> Path:
    """The body at `url` written to `<folder>/<name>.part`, bounded, then renamed whole."""
    dest = folder / _file_name(url)
    part = dest.with_name(dest.name + ".part")
    with _Deadline(client_packs.STALL_SECONDS) as watcher:
        response = _request(url, watcher, opener=opener, label=label)
        try:
            header = response.getheader("Content-Length")
            declared = int(header) if header is not None and header.strip().isdigit() else None
            if declared is not None and declared > MAX_DOWNLOAD_BYTES:
                raise AddonRefusal(
                    f"{label} is {_size(declared)}; Yu'lon downloads add-ons up to "
                    f"{_size(MAX_DOWNLOAD_BYTES)}. {NOTHING_CHANGED}"
                )
            limit = declared if declared is not None else MAX_DOWNLOAD_BYTES
            _refuse_without_room(label, folder, limit)
            done = 0
            with part.open("xb") as handle:
                while True:
                    if cancelled():
                        raise AddonCancelled(
                            f"The download of {label} was cancelled. {NOTHING_CHANGED}"
                        )
                    try:
                        chunk = response.read1(min(client_packs.CHUNK_BYTES, limit - done + 1))
                    except Exception as exc:
                        raise AddonRefusal(
                            f"The download of {label} stopped at {_size(done)} ({exc}). Try "
                            f"again. {NOTHING_CHANGED}"
                        ) from exc
                    if watcher.fired:
                        raise AddonRefusal(
                            f"The download of {label} sent nothing for "
                            f"{client_packs.STALL_SECONDS:.0f}s, so Yu'lon gave up on it. "
                            f"Try again. {NOTHING_CHANGED}"
                        )
                    if not chunk:
                        break
                    watcher.restart(client_packs.STALL_SECONDS)
                    handle.write(chunk)
                    done += len(chunk)
                    if done > limit:
                        raise AddonRefusal(
                            f"{label} is larger than "
                            f"{_size(limit) if declared is not None else _size(MAX_DOWNLOAD_BYTES)}"
                            f"{' (what its site said)' if declared is not None else ''}; Yu'lon "
                            f"downloads add-ons up to {_size(MAX_DOWNLOAD_BYTES)}. "
                            f"{NOTHING_CHANGED}"
                        )
                    if progress is not None:
                        progress(done, limit)
        finally:
            client_packs._close(response)
    if declared is not None and done < declared:
        raise AddonRefusal(
            f"The download of {label} stopped at {_size(done)} of {_size(declared)}. Try again. "
            f"{NOTHING_CHANGED}"
        )
    os.replace(part, dest)
    return dest


def _request(
    url: str, watcher: _Deadline, *, opener: client_packs.Opener, label: str
) -> client_packs.Response:
    """Open `url` with `ALLOWED_HOSTS` bounding every redirect; every failure a sentence."""
    host = urllib.parse.urlsplit(url).hostname
    try:
        response = opener(url, watcher, method="GET", headers={}, hosts=ALLOWED_HOSTS)
    except urllib.error.HTTPError as exc:
        raise AddonRefusal(_status_sentence(url, exc.code)) from exc
    except fetch.UpdateError as exc:
        found = _REDIRECTED_TO.search(str(exc))
        where = (found[1] or found[2]) if found else "another site"
        raise AddonRefusal(
            f"{label} sent Yu'lon on to {where[:200]}. Yu'lon downloads add-ons only from "
            "GitHub, GitLab and Codeberg. Download it in your browser, then choose the zip. "
            f"{NOTHING_CHANGED}"
        ) from exc
    except Exception as exc:
        raise AddonRefusal(
            f"Yu'lon could not reach {host} ({exc}). Check the connection and try again. "
            f"{NOTHING_CHANGED}"
        ) from exc
    if not 200 <= response.status < 300:
        client_packs._close(response)
        raise AddonRefusal(_status_sentence(url, response.status))
    return response


def _status_sentence(url: str, status: int) -> str:
    gone = " the file is not there; check the link, or" if status in (404, 410) else ""
    return (
        f"{url[:200]} answered HTTP {status}:{gone} download it in your browser and choose the "
        f"zip. {NOTHING_CHANGED}"
    )


def _link_label(url: str) -> str:
    name = PurePosixPath(urllib.parse.urlsplit(url).path).name
    return name or url[:200]


def _file_name(url: str) -> str:
    name = PurePosixPath(urllib.parse.urlsplit(url).path).name
    return name if client_packs._SAFE_NAME.match(name) else "addon.zip"


# ------------------------------------------------------------------ a folder


def check_folder(root: Path, *, label: str | None = None) -> Tree:
    """`root` held to the zip's caps and rules before it is copied: no links, no programs.

    `.git` is left out, as the copy leaves it out. The chosen folder itself may
    be a link (that is where the player keeps it); nothing inside it may be.
    """
    label = label or root.name
    files = size = 0
    try:
        for folder, dirs, names, linked in links.walk(root):
            dirs[:] = [name for name in dirs if name != ".git"]
            here = Path(folder)
            for name in sorted(n for n in linked if n != ".git"):
                path = here / name
                raise AddonRefusal(
                    f"{path.relative_to(root).as_posix()} in the folder is a link to "
                    f"{_link_target(path)}; Yu'lon copies only real files. {NOTHING_CHANGED}"
                )
            for name in names:
                path = here / name
                if not stat.S_ISREG(path.lstat().st_mode):
                    continue  # a FIFO, socket or device: never opened (a FIFO blocks the read)
                rel = path.relative_to(root)
                if len(rel.parts) - 1 > MAX_DEPTH:
                    raise AddonRefusal(
                        f"{rel.as_posix()} is {len(rel.parts) - 1} folders deep; Yu'lon takes "
                        f"add-ons up to {MAX_DEPTH} folders deep. {NOTHING_CHANGED}"
                    )
                _refuse_program_name(rel.as_posix(), path.suffix)
                length = path.stat().st_size
                with path.open("rb") as handle:
                    _refuse_program_content(rel.as_posix(), handle.read(HEAD_BYTES), length)
                files += 1
                size += length
                if files > MAX_FILES or size > MAX_UNPACKED_BYTES:
                    raise AddonRefusal(_too_big(label, f"more than {_size(size)}", files))
    except OSError as exc:
        raise AddonRefusal(_os_sentence(exc, root)) from exc
    return Tree(files=files, bytes=size)


# ------------------------------------------------------------------ shared


def _refuse_program_name(rel: str, suffix: str) -> None:
    if suffix.casefold() in PROGRAM_SUFFIXES:
        raise AddonRefusal(_program(rel, suffix))


def _refuse_program_content(rel: str, head: bytes, size: int) -> None:
    kind = program_kind(head, size)
    if kind:
        raise AddonRefusal(_program(rel, "", kind=kind))


def _program(rel: str, suffix: str, *, kind: str = "") -> str:
    kind = kind or f"a {suffix} file"
    return (
        f"{rel} is a program file ({kind}); WoW add-ons never carry one, so Yu'lon will not "
        f"put it in your game client. {NOTHING_CHANGED}"
    )


def _too_big(label: str, size: str, files: int) -> str:
    return (
        f"{label} would unpack to {size} ({files:,} files); Yu'lon takes add-ons up to "
        f"{_size(MAX_UNPACKED_BYTES)} and {MAX_FILES:,} files. {NOTHING_CHANGED}"
    )


def _refuse_without_room(label: str, folder: Path, needed: int) -> None:
    free = client_packs._free_bytes(folder)
    if free < needed:
        raise AddonRefusal(
            f"{label} needs {_size(needed)} of free space in {folder}, and that drive has "
            f"{_size(free)}. Free some space and try again. {NOTHING_CHANGED}"
        )


def _os_sentence(exc: OSError, where: Path) -> str:
    return (
        f"Yu'lon could not use {exc.filename or where} ({exc.strerror or exc}). If it is in "
        "OneDrive, make it available offline in OneDrive; otherwise free some space and check "
        f"that Yu'lon may read and write there, then try again. {NOTHING_CHANGED}"
    )


def _link_target(path: Path) -> str:
    try:
        return os.readlink(path)
    except (OSError, ValueError):
        return "somewhere Yu'lon could not read"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(client_packs.CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _size(size: int) -> str:
    if size >= 1024**2:
        return f"{size / 1024**2:.0f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} bytes"


def _remove(path: Path) -> None:
    """Remove a staging or download folder this module made; logged, never raised."""
    try:
        if path.exists():
            rmtree.remove_tree(path)
    except (OSError, rmtree.TreeRemovalError):
        logger.warning("addon staging: could not remove %s", path, exc_info=True)
