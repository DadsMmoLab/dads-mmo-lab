"""A ready-to-play client: a per-server folder built from the player's own WoW client (T181a).

Why a separate folder at all: pointing the player's own client at a Yu'lon
server means writing its `realmlist.wtf` (and, in later T181 steps, patching
`Wow.exe`), and then the client no longer reaches whatever it reached before.
A folder per server keeps the original untouched.

Why hard links: a 3.3.5a client is ~15-17 GB, almost all of it in `*.MPQ`
archives that WoW only ever reads. Hard-linking those (and the `*.dll`s, also
read-only) makes a second folder cost 50-300 MB. Everything else is a real
copy, because WoW writes to it (`WTF/`, `Interface/`, every `realmlist.wtf`),
and a write through a hard link would land in the original too. So the rule is
by suffix and nothing else: a file is linked only when its suffix is `.mpq` or
`.dll`. Where the filesystem can clone (btrfs, XFS: `FICLONE`), a clone is used
instead of a link - same space saving, no shared inode at all.

Links cannot cross volumes. A target on another drive therefore means a full
copy, which only happens once the player agreed to its size
(`allow_full_copy`); until then it is refused with the size in the message.

Creation is all-or-nothing: everything is built in `<target>.yulon-partial`,
whose marker is written before any client file, and renamed into place at the
end. Any failure removes the partial folder; a crash leaves one carrying a
marker, which the next attempt recognises as Yu'lon's own and removes. Nothing
is ever deleted from a folder without that marker.

Afterwards the folder is kept in step, not rebuilt: a patcher that replaces an
archive in the original breaks the hard link, and `stale()`/`refresh()` find and
re-share exactly those (and `Wow.exe`), never touching `WTF/` or `Interface/`.
`delete()` removes the folder only when its marker names the same game and server.

No Qt here: the dialog that asks and the progress it shows live in the view.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import sys
import time
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from yulon.log import get_logger
from yulon.steam import client_executable

logger = get_logger(__name__)

MARKER = ".yulon-client.json"
LEFT_OUT = frozenset({"cache", "wdb", "logs", "errors", "screenshots"})
"""Top-level folder names (lower-case) not carried over: WoW recreates them, and a
stale cache from another server confuses the client. 3.3.5a keeps it in `Cache/`
(`Cache/WDB`); Vanilla and Tortoise keep it at the top level as `WDB/`."""
LINKED_SUFFIXES = frozenset({".mpq", ".dll"})
PARTIAL_SUFFIX = ".yulon-partial"

_FICLONE = 0x40049409  # linux/fs.h: _IOW(0x94, 9, int)

_CANNOT_LINK = frozenset({errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK})
"""`link()` errors that mean THIS drive cannot share files (exFAT/FAT32, some network
shares, a file at its link limit). Same answer as another drive: a full copy, asked."""
_ERROR_INVALID_FUNCTION = 1  # Windows' answer from a filesystem without hard links
_ERROR_NOT_SUPPORTED = 50  # Windows' answer from an SMB share that cannot hard-link

_RENAME_TRIES = 10
_RENAME_DELAY = 0.5
"""How long a rename or removal refused with PermissionError is tried again: ten
tries over about five seconds. On Windows a folder cannot be renamed or emptied
while any file in it is open, and Defender or the search indexer opens a freshly
written file for a moment; a refusal that outlives this is a real one."""

ONEDRIVE_VARIABLES = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
"""The environment variables Windows sets to the folders OneDrive syncs."""

# Windows reparse points. Defined here, not taken from `stat`: there they exist only
# on Windows builds, and the link test below must be exercisable everywhere.
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # a junction
IO_REPARSE_TAG_SYMLINK = 0xA000000C
_NAME_SURROGATE = 0x20000000  # winnt.h IsReparseTagNameSurrogate: "this names another file"

_lstat = os.lstat  # a seam: tests stand in Windows' answer for a junction


def _stat_is_link(st: os.stat_result) -> bool:
    """`_is_link` for a look already taken, so the caller acts on the same answer."""
    if stat.S_ISLNK(st.st_mode):
        return True
    if not getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    tag = getattr(st, "st_reparse_tag", None)
    return not tag or bool(tag & _NAME_SURROGATE)


def _is_link(path: str | os.PathLike[str]) -> bool:
    """A symlink, or a Windows junction: something never walked into, only removed.

    On Windows a junction is not a symlink to `os.walk(followlinks=False)`,
    `DirEntry.is_symlink()` or `Path.is_symlink()`, on any Python version, and
    `os.path.isjunction` is 3.12+. What gives it away is the reparse-point
    attribute with a tag whose name-surrogate bit is set: Windows' own mark for
    "this entry stands for another file or folder" (junctions, symlinks, WSL
    symlinks, and any later kind). Other reparse points are NOT links:
    OneDrive's files-on-demand placeholders and deduplicated files carry the
    attribute too, and treating them as links would drop the player's archives
    from the build. A reparse point with no tag reported is treated as a link,
    the cautious answer.

    A path that cannot be looked at answers False here, for `plan()`, whose next
    step reads the same path and fails loudly. The remover does not use this: it
    takes one look per entry and lets a failed look stop it.
    """
    try:
        return _stat_is_link(_lstat(path))
    except OSError:
        return False


class Marker(BaseModel):
    """`.yulon-client.json`: what says a folder is Yu'lon's to change or delete."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    game: str
    server_dir: Path
    source_client_dir: Path
    created_at: datetime
    full_copy: bool = False
    """Its archives were copied, not shared (another drive, or one that cannot share).

    Recorded because nothing on disk tells a full copy from a clone: neither
    shares an inode with the original. `refresh()` re-copies a full copy's
    archives without asking (the player agreed to its size once already), and
    refuses to turn a shared client into one. Absent in a marker means shared.
    """


class PlayClientError(RuntimeError):
    """A refusal or failure whose message is shown to the player as it is.

    Every message says what happened, what was left as it was, and what to do next.
    """


@dataclass(frozen=True)
class BuildPlan:
    """What `create()` would do, for the dialog to show before it does it."""

    linked: tuple[Path, ...]
    copied: tuple[Path, ...]
    shared_bytes: int
    own_bytes: int
    same_volume: bool
    skipped_links: tuple[Path, ...] = ()
    """Folders and files of the original left out because they are links (symlinks,
    junctions) to somewhere else, relative: e.g. `Interface/AddOns` shared between
    two clients by a junction. The dialog names them."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def default_target(original: Path, game_display_name: str, server_dir: Path | None = None) -> Path:
    """A sibling of the original, on the same drive, named after the game.

    Two servers of the same game would want the same name. When the plain name
    is taken by anything but this server's own ready-to-play client, and the
    server is known, its folder name is added to tell them apart.
    """
    plain = original.parent / f"{original.name} (Yu'lon \u2013 {game_display_name})"
    if server_dir is None or not plain.exists():
        return plain
    marker = read_marker(plain)
    if marker is not None and marker.server_dir == server_dir:
        return plain
    return original.parent / (
        f"{original.name} (Yu'lon \u2013 {game_display_name}, {server_dir.name})"
    )


def _partial(target: Path) -> Path:
    return target.with_name(target.name + PARTIAL_SUFFIX)


def _norm(path: Path) -> Path:
    return Path(os.path.normcase(path.resolve()))


def _existing_ancestor(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return path


def _gb(size: int) -> str:
    return f"{size / 1024**3:.1f}"


def read_marker(folder: Path) -> Marker | None:
    """The folder's marker, or None if it has none or it cannot be read."""
    try:
        raw = (folder / MARKER).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        return Marker.model_validate_json(raw)
    except ValidationError:
        return None


def plan(original: Path, target: Path, *, server_dir: Path | None = None) -> BuildPlan:
    """Walk `original` and sort its files into linked and copied; refuse a bad target.

    Symlinks are neither followed nor reproduced: following one could pull in a
    folder outside the client (or the target itself), and recreating one could
    point a file WoW writes back into the original.

    With `server_dir`, a target that is another server's ready-to-play client is
    refused as such.
    """
    orig_n, target_n = _norm(original), _norm(target)
    if target_n == orig_n:
        raise PlayClientError(
            f"{target} is your own client, so it cannot also be the ready-to-play "
            "client. Nothing was created. Choose another folder, next to it."
        )
    if orig_n in target_n.parents:
        raise PlayClientError(
            f"{target} is inside your own client {original}; building there would "
            "copy the client into itself. Nothing was created. Choose a folder "
            "outside it, for example next to it."
        )
    if _is_link(original / "Data"):
        raise PlayClientError(
            f"The Data folder of {original} is a link to another folder, so its game files "
            "cannot be shared from here. Nothing was created. Point Yu'lon at a real client "
            "folder: the one that holds Wow.exe and the Data folder itself."
        )
    if not (original / "Data").is_dir():
        raise PlayClientError(
            f"{original} has no Data folder, so it does not look like a WoW client. "
            "Nothing was created. Point Yu'lon at the folder that holds Wow.exe and Data."
        )
    if target.exists():
        existing = read_marker(target)
        if existing is None:
            raise PlayClientError(
                f"{target} already exists and was not made by Yu'lon, so it was left as it "
                "was. Choose another folder, or move that one away first."
            )
        if server_dir is not None and existing.server_dir != server_dir:
            raise PlayClientError(
                f"{target} belongs to the ready-to-play client of another server at "
                f"{existing.server_dir}, so it was left as it was. Choose another folder."
            )

    linked: list[Path] = []
    copied: list[Path] = []
    skipped: list[Path] = []
    shared = own = 0
    for dirpath, dirnames, filenames in os.walk(original, followlinks=False, onerror=_unreadable):
        here = Path(dirpath)
        rel_dir = here.relative_to(original)
        kept = []
        for name in dirnames:
            if _is_link(here / name):
                logger.info("ready-to-play client: skipping linked folder %s", here / name)
                skipped.append(rel_dir / name)
            elif rel_dir == Path(".") and name.lower() in LEFT_OUT:
                continue
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            path = here / name
            if _is_link(path):
                logger.info("ready-to-play client: skipping symlink %s", path)
                skipped.append(rel_dir / name)
                continue
            st = path.lstat()
            rel = rel_dir / name
            if rel == Path(MARKER):
                continue  # a ready-to-play client used as the source: its marker is not ours
            if path.suffix.lower() in LINKED_SUFFIXES:
                linked.append(rel)
                shared += st.st_size
            else:
                copied.append(rel)
                own += st.st_size

    if not any(rel.suffix.lower() == ".mpq" for rel in linked):
        raise PlayClientError(
            f"{original} holds no .MPQ game archives, so it does not look like a WoW client. "
            "Nothing was created. Point Yu'lon at a real client folder: the one that holds "
            "Wow.exe and the Data folder with the game's .MPQ files."
        )
    same_volume = os.stat(original).st_dev == os.stat(_existing_ancestor(target.parent)).st_dev
    return BuildPlan(
        linked=tuple(linked),
        copied=tuple(copied),
        shared_bytes=shared,
        own_bytes=own,
        same_volume=same_volume,
        skipped_links=tuple(skipped),
    )


def _unreadable(exc: OSError) -> None:
    """`os.walk`'s `onerror` for `plan()`: a folder that cannot be read stops the plan.

    By default `os.walk` skips it in silence, and the client built would lack
    whatever it held, with nothing said.
    """
    raise PlayClientError(
        f"{exc.filename} could not be read ({exc.strerror or exc}), so nothing was created. "
        "Check that you can open that folder, then try again."
    ) from exc


def onedrive_folder(target: Path, *, env: Mapping[str, str], os_name: str) -> Path | None:
    """The OneDrive folder `target` would be inside, on Windows; else None.

    OneDrive uploads every file put in it, a client's gigabytes included, and its
    files-on-demand may later take the local copies away, so the dialog
    suggests a folder outside it.
    """
    if os_name != "windows":
        return None
    where = os.path.normcase(os.path.abspath(target))
    for name in ONEDRIVE_VARIABLES:
        root = env.get(name)
        if not root:
            continue
        top = os.path.normcase(os.path.abspath(root))
        if where == top or where.startswith(top.rstrip("\\/") + os.sep):
            return Path(root)
    return None


def try_reflink(src: Path, dst: Path) -> bool:
    """Clone `src` to a new `dst` with Linux `FICLONE`; False wherever that is not possible.

    Never raises: a filesystem without clones (ext4, NTFS, another filesystem
    than the source's) is the normal case, and the caller then hard-links. A
    failed attempt removes the `dst` it created, so the link that follows does
    not trip over it.
    """
    if sys.platform != "linux":
        return False
    import fcntl

    created = False
    try:
        with open(src, "rb") as fsrc, open(dst, "xb") as fdst:
            created = True
            fcntl.ioctl(fdst.fileno(), _FICLONE, fsrc.fileno())
    except OSError:
        if created:
            try:
                os.unlink(dst)
            except OSError:
                pass
        return False
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass
    return True


def remove_folder(
    folder: Path,
    *,
    original: Path | None = None,
    unlink: Callable[[Path], None] = os.unlink,
) -> None:
    """Delete a ready-to-play client folder without changing the original through a link.

    `rmtree.remove_tree` clears read-only flags with `chmod` when a delete is
    refused (Windows). On a hard-linked `*.MPQ` that flag lives on the inode the
    ORIGINAL shares, so clearing it changes the player's own client. Here a file
    with more than one link has its mode recorded, is made writable only if the
    delete actually needs it, and has the recorded mode put back on the name
    that survives in `original` (the same inode at the same relative path).

    Directories are this folder's own, never shared, so they are made writable
    freely. Raises OSError if something cannot be removed.
    """
    if _stat_is_link(_lstat(folder)):  # a look that fails stops it: never fail open
        raise OSError(
            errno.EPERM,
            f"{folder} is a link, not a folder Yu'lon may delete through; nothing was changed",
        )
    _empty(folder, folder, original, unlink)
    _remove_dir(folder, parent_is_ours=False)  # its parent is the player's, not ours


def _empty(here: Path, folder: Path, original: Path | None, unlink: Callable[[Path], None]) -> None:
    """Remove everything inside `here`, bottom up, never entering a link.

    One `lstat` per entry decides both "is it a link" and "is it a folder": a
    second, following look (`is_dir()`) could see a link that replaced the entry
    in between and send the walk through it. A look that fails stops the removal
    rather than guessing, so what could not be seen is never entered.

    At the top, the marker goes last: a removal that stops half way leaves a
    folder that still says it is Yu'lon's, so it can be finished later.
    """
    with os.scandir(here) as entries:
        children = [Path(entry.path) for entry in entries]
    if here == folder:
        children.sort(key=lambda child: child.name == MARKER)  # stable: marker last
    for child in children:
        st = _lstat(child)
        if _stat_is_link(st):
            _remove_link(child, unlink)
        elif stat.S_ISDIR(st.st_mode):
            _empty(child, folder, original, unlink)
            _remove_dir(child)
        else:
            survivor = original / child.relative_to(folder) if original is not None else None
            _remove_file(child, survivor, unlink)


def _remove_link(path: Path, unlink: Callable[[Path], None]) -> None:
    """Remove a symlink or junction itself, never what it points at.

    `unlink` removes a symlink, and on Windows a directory symlink or junction
    too; `rmdir` is the other spelling Windows accepts for a directory link, and
    it refuses a non-empty real directory, so it cannot empty anything either.
    """
    try:
        unlink(path)
    except OSError as first:
        try:
            os.rmdir(path)
        except OSError:
            raise first from None


def _make_writable(path: Path) -> None:
    try:
        os.chmod(path, path.lstat().st_mode | stat.S_IWRITE)
    except OSError:
        pass


def _remove_file(path: Path, survivor: Path | None, unlink: Callable[[Path], None]) -> None:
    """Delete one file, clearing its own read-only flag only if nothing else helps.

    First the directory (this folder's own: on POSIX its write bit is what
    refuses an unlink), and only then the file itself (Windows' read-only
    attribute), because on a hard link that flag is the original's too. The
    recorded mode is put back on `survivor`, the name in the player's client
    this file may share its inode with, if it still does.
    """
    st = path.lstat()
    try:
        unlink(path)
        return
    except PermissionError:
        _make_writable(path.parent)
    try:
        unlink(path)
        return
    except PermissionError:
        if stat.S_ISLNK(st.st_mode):
            raise
    os.chmod(path, st.st_mode | stat.S_IWRITE)
    try:
        unlink(path)
    except OSError:
        try:
            os.chmod(path, stat.S_IMODE(st.st_mode))
        except OSError:
            logger.warning(
                "ready-to-play client: %s could not be made read-only again and may be "
                "left writable",
                path,
                exc_info=True,
            )
        raise
    if st.st_nlink <= 1:
        return
    try:
        if survivor is not None:
            now = survivor.lstat()
            if (now.st_dev, now.st_ino) == (st.st_dev, st.st_ino):
                os.chmod(survivor, stat.S_IMODE(st.st_mode))
                return
    except OSError:
        pass
    logger.warning(
        "ready-to-play client: %s was shared with another file whose read-only flag "
        "could not be put back",
        path,
    )


def _remove_dir(path: Path, *, parent_is_ours: bool = True) -> None:
    try:
        os.rmdir(path)
    except PermissionError:
        _make_writable(path)
        if parent_is_ours:
            _make_writable(path.parent)
        os.rmdir(path)


def _retrying(action: Callable[[], None], *, sleep: Callable[[float], None]) -> None:
    """`action()`, tried again on PermissionError for a few seconds (`_RENAME_TRIES`)."""
    for attempt in range(1, _RENAME_TRIES + 1):
        try:
            action()
            return
        except PermissionError:
            if attempt == _RENAME_TRIES:
                raise
            logger.info("ready-to-play client: refused (attempt %d), trying again", attempt)
            sleep(_RENAME_DELAY)


def _discard(
    partial: Path, original: Path | None, *, sleep: Callable[[float], None] = time.sleep
) -> bool:
    """Remove an unfinished build; False (logged) if it could not be removed."""
    try:
        _retrying(lambda: remove_folder(partial, original=original), sleep=sleep)
    except OSError:
        logger.warning("ready-to-play client: could not remove %s", partial, exc_info=True)
        return False
    return True


def _whose(marker: Marker, *, game: str, server_dir: Path) -> str | None:
    """Whose folder a marked folder is, when it is not `game`'s at `server_dir`; else None.

    The one rule for every change to a marked folder: the marker must name this
    game AND this server. A marker is not a licence to touch any folder Yu'lon
    ever made, because another server's client is the player's too.
    """
    if marker.game == game and marker.server_dir == server_dir:
        return None
    if marker.server_dir == server_dir:
        return f"another game, {marker.game}, on this server"
    if marker.game == game:
        return f"another server at {marker.server_dir}"
    return f"another game, {marker.game}, on another server at {marker.server_dir}"


def _remove_partial(partial: Path, marker: Marker) -> None:
    logger.info("ready-to-play client: removing an unfinished earlier attempt at %s", partial)
    try:
        remove_folder(partial, original=marker.source_client_dir)
    except OSError as exc:
        raise PlayClientError(
            f"An unfinished earlier attempt at {partial} could not be removed: {exc}. "
            "Nothing new was created and your client was left as it was. Delete that "
            "folder yourself, then try again."
        ) from exc


def _remove_leftover_partial(partial: Path, *, game: str, server_dir: Path) -> None:
    """For `create()`: clear the way, or say why the folder in it is not Yu'lon's to clear."""
    if not partial.exists():
        return
    marker = read_marker(partial)
    if marker is None:
        raise PlayClientError(
            f"{partial} is in the way and was not made by Yu'lon, so it was left as it "
            "was. Move it away or choose another folder, then try again."
        )
    whose = _whose(marker, game=game, server_dir=server_dir)
    if whose is not None:
        raise PlayClientError(
            f"{partial} is the unfinished ready-to-play client of {whose}, so it was left "
            "as it was. Choose another folder."
        )
    _remove_partial(partial, marker)


def clean_partials(target: Path, *, game: str, server_dir: Path) -> bool:
    """Remove `<target>.yulon-partial` left by a crashed build; True if it was removed.

    Only a leftover whose marker names this game and server is removed (the same
    rule `create()` applies); one without a marker, or another server's, is left
    and answers False, and `create()` then refuses with the reason. A removal
    that fails raises `PlayClientError`.
    """
    partial = _partial(target)
    if not partial.exists():
        return False
    marker = read_marker(partial)
    if marker is None or _whose(marker, game=game, server_dir=server_dir) is not None:
        logger.info("ready-to-play client: leaving %s, it is not this server's", partial)
        return False
    _remove_partial(partial, marker)
    return True


_ACROSS_DRIVES_ADVICE = (
    "Choose a folder on a drive that can share them (the one your client is on), or "
    "agree to the full copy."
)


class _Stop(Exception):
    """A build that stops: what happened, and what to do next (the middle is the cleanup)."""

    def __init__(self, what: str, next_step: str) -> None:
        super().__init__(what)
        self.what = what
        self.next_step = next_step


def _cannot_link(exc: OSError) -> bool:
    return exc.errno in _CANNOT_LINK or getattr(exc, "winerror", None) in (
        _ERROR_INVALID_FUNCTION,
        _ERROR_NOT_SUPPORTED,
    )


def _share(
    src: Path,
    dst: Path,
    *,
    link: Callable[[Path, Path], None],
    reflink: Callable[[Path, Path], bool],
) -> None:
    """Make a new `dst` hold `src`'s bytes at no extra space: a clone, else a hard link.

    The one linking path for `create()` and `refresh()`. Raises the link's
    OSError; which of those mean "this drive cannot share" is `_cannot_link`'s
    and EXDEV's to say, and what to do about it is the caller's.
    """
    if not reflink(src, dst):
        link(src, dst)


def create(
    original: Path,
    target: Path,
    *,
    game: str,
    server_dir: Path,
    allow_full_copy: bool,
    link: Callable[[Path, Path], None] = os.link,
    reflink: Callable[[Path, Path], bool] = try_reflink,
    now: Callable[[], datetime] = utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> Marker:
    """Build the ready-to-play client for `game` at `server_dir` in `target`.

    Linked-class files are cloned where the filesystem can, else hard-linked;
    across volumes they are copied only when `allow_full_copy`. The folder
    appears at `target` complete or not at all.

    The final rename, and the removal of a failed build, are tried again for a
    few seconds when refused with PermissionError (`_RENAME_TRIES`): on Windows
    Defender or the indexer briefly opens a freshly written file, and a folder
    holding an open file can be neither renamed nor emptied.
    """
    build = plan(original, target, server_dir=server_dir)
    if target.exists():
        raise PlayClientError(
            f"{target} already holds this server's ready-to-play client, so it was left "
            "as it was. Refresh it or delete it from Yu'lon instead of making it again."
        )
    partial = _partial(target)
    _remove_leftover_partial(partial, game=game, server_dir=server_dir)
    full_size = _gb(build.shared_bytes + build.own_bytes)

    full_copy = False
    made = False
    try:
        partial.mkdir(parents=True)
        made = True
        marker = Marker(
            game=game, server_dir=server_dir, source_client_dir=original, created_at=now()
        )
        (partial / MARKER).write_text(marker.model_dump_json(indent=2), encoding="utf-8")

        for rel in build.linked:
            src, dst = original / rel, partial / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not full_copy:
                try:
                    _share(src, dst, link=link, reflink=reflink)
                    continue
                except OSError as exc:
                    if exc.errno == errno.EXDEV:
                        what = (
                            f"{target} is on another drive than your client {original}, so "
                            "its game files cannot be shared"
                        )
                        advice = _ACROSS_DRIVES_ADVICE
                    elif not _cannot_link(exc):
                        raise
                    elif build.same_volume:
                        # exFAT/FAT32: no folder on this drive can share, and no folder on
                        # another drive either (links never cross drives).
                        what = (
                            f"The drive your client {original} is on cannot share files, "
                            "so no folder on it can share its game files"
                        )
                        advice = (
                            "The only way is the full copy: agree to it, or move your "
                            "client to a drive that can share files (NTFS, ext4, btrfs)."
                        )
                    else:
                        what = (
                            f"The drive holding {target} cannot share files with your "
                            "client, so its game files cannot be shared"
                        )
                        advice = _ACROSS_DRIVES_ADVICE
                    if not allow_full_copy:
                        raise _Stop(
                            f"{what} and a full copy needs {full_size} GB.", advice
                        ) from exc
                    full_copy = True
                    logger.info("ready-to-play client: %s (%s), copying in full", what, exc)
            shutil.copy2(src, dst)

        for rel in build.copied:
            dst = partial / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original / rel, dst)

        if full_copy:
            marker = marker.model_copy(update={"full_copy": True})
            (partial / MARKER).write_text(marker.model_dump_json(indent=2), encoding="utf-8")
        for attempt in range(1, _RENAME_TRIES + 1):
            try:
                os.replace(partial, target)
                break
            except PermissionError:
                if attempt == _RENAME_TRIES:
                    raise
                logger.info("ready-to-play client: renaming %s refused, trying again", partial)
                sleep(_RENAME_DELAY)
    except BaseException as exc:
        cleaned = _discard(partial, original, sleep=sleep) if made else None
        if not isinstance(exc, Exception):
            raise
        if isinstance(exc, _Stop):
            stop = exc
        elif isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            logger.error("ready-to-play client: building %s ran out of space: %s", target, exc)
            needed = build.own_bytes + (build.shared_bytes if full_copy else 0)
            stop = _Stop(
                f"The drive holding {target} ran out of space while building the "
                f"ready-to-play client: it needs {_gb(needed)} GB.",
                "Free some space or choose another folder, then try again.",
            )
        elif isinstance(exc, OSError):
            logger.error("ready-to-play client: building %s failed", target, exc_info=True)
            stop = _Stop(
                f"Building the ready-to-play client in {target} failed: {exc}.",
                "Fix the cause and try again.",
            )
        else:
            logger.exception("ready-to-play client: building %s failed", target)
            stop = _Stop(
                f"Building the ready-to-play client in {target} failed: {exc}.",
                "Try again; if it fails the same way, send the Yu'lon log.",
            )
        if cleaned is None:  # the folder itself could not be made
            outcome = "Nothing was created and your client was left as it was."
        elif cleaned:
            outcome = "The unfinished folder was removed and your client was left as it was."
        else:
            outcome = (
                f"The unfinished folder {partial} could not be removed; it keeps Yu'lon's "
                "marker, so the next attempt removes it. Your client was left as it was."
            )
        raise PlayClientError(f"{stop.what} {outcome} {stop.next_step}") from exc

    logger.info(
        "ready-to-play client for %s built at %s (%d linked, %d copied%s)",
        game,
        target,
        len(build.linked),
        len(build.copied),
        ", full copy" if full_copy else "",
    )
    return marker


# -- keeping it in step with the original, and removing it --------------------

PLAYERS_OWN = frozenset({"wtf", "interface"})
"""Top-level folders (lower-case) refresh never touches: the player's settings and
add-ons live there, and they are this client's own from the moment it is built."""
REFRESH_SUFFIX = ".yulon-refresh"
_SAME_TIME_NS = 2 * 10**9
"""How far apart two modification times may be and still be the same file. FAT32
keeps times to two seconds, so a full copy there never matches to the nanosecond
and an exact test would offer Refresh at every Play. A patched archive is newer
by far more than that."""
_CHUNK = 1024 * 1024


def _players_own(rel: Path) -> bool:
    return bool(rel.parts) and rel.parts[0].lower() in PLAYERS_OWN


def _kept(keep: Collection[Path]) -> Callable[[Path], bool]:
    """Whether a relative path is one of `keep`, compared the way the OS compares names."""
    names = {os.path.normcase(os.fspath(rel)) for rel in keep}
    return lambda rel: os.path.normcase(os.fspath(rel)) in names


def _linked_files(play_dir: Path) -> Iterator[Path]:
    """The `*.MPQ`/`*.dll` of a ready-to-play client, relative, outside WTF/ and Interface/.

    Never through a link: `plan()` puts none there, so one found later is not
    Yu'lon's to compare or replace.
    """
    for dirpath, dirnames, filenames in os.walk(play_dir, followlinks=False):
        here = Path(dirpath)
        rel_dir = here.relative_to(play_dir)
        dirnames[:] = [
            name
            for name in dirnames
            if not _is_link(here / name) and not _players_own(rel_dir / name)
        ]
        for name in filenames:
            if Path(name).suffix.lower() in LINKED_SUFFIXES and not _is_link(here / name):
                yield rel_dir / name


def _original_file(path: Path) -> os.stat_result | None:
    """The original's file as it is now, or None when it is gone (or no longer a file)."""
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    return st if stat.S_ISREG(st.st_mode) else None


def _out_of_date(mine: Path, theirs: Path) -> bool:
    """True when `theirs` is another file than `mine`, with another size or time.

    No `theirs` is never out of date: an archive only the ready-to-play client
    has (a module's patch, the player's own, one the original's patcher removed)
    may be its only copy, and nothing in the original can replace it.

    The same file (a hard link that still shares its inode) is up to date by
    definition, whatever was written into it. Otherwise size and time decide,
    which is all a full copy or a clone can be compared by without reading
    gigabytes at every Play.
    """
    now = _original_file(theirs)
    if now is None:
        return False
    st = mine.lstat()
    if os.path.samestat(st, now):
        return False
    return st.st_size != now.st_size or abs(st.st_mtime_ns - now.st_mtime_ns) > _SAME_TIME_NS


def _same_bytes(a: Path, b: Path) -> bool:
    """Byte for byte, sizes first. Not `filecmp`: its cache answers from size and time,
    which is exactly what a same-size patched `Wow.exe` with its time kept defeats."""
    if a.stat().st_size != b.stat().st_size:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            chunk = fa.read(_CHUNK)
            if chunk != fb.read(_CHUNK):
                return False
            if not chunk:
                return True


def stale(play_dir: Path, original: Path, *, keep: Collection[Path] = ()) -> tuple[Path, ...]:
    """What in `play_dir` no longer matches the original client, relative and sorted.

    An `*.MPQ`/`*.dll` whose original is no longer the same file and differs in
    size or time (a patcher writes a new file, which breaks the hard link); and
    `Wow.exe` when its bytes differ, since it is always a copy. Nothing
    under WTF/ or Interface/, which `refresh()` would not touch either: listing
    what it will not fix would offer Refresh at every Play.

    An original that no longer looks like a client (no Data folder: moved, or a
    drive not plugged in) answers nothing: the ready-to-play client still plays
    from its own names. A file with no counterpart in the original is never
    listed (see `_out_of_date`). A file that cannot be compared is logged and
    left out.

    `keep` (relative paths) is never listed: a module's patch in the ready-to-play
    client under a name the original also has is that module's file, and
    listing it would have Refresh put the original's back over it.
    """
    is_kept = _kept(keep)
    if not (original / "Data").is_dir():
        logger.warning(
            "ready-to-play client %s: its original %s has no Data folder, not compared",
            play_dir,
            original,
        )
        return ()
    found: list[Path] = []
    for rel in _linked_files(play_dir):
        if is_kept(rel):
            continue
        try:
            if _out_of_date(play_dir / rel, original / rel):
                found.append(rel)
        except OSError:
            logger.warning(
                "ready-to-play client: could not compare %s", play_dir / rel, exc_info=True
            )
    mine, theirs = client_executable(play_dir), client_executable(original)
    try:
        if (
            not is_kept(Path(mine.name))
            and mine.is_file()
            and theirs.is_file()
            and not _same_bytes(mine, theirs)
        ):
            found.append(Path(mine.name))
    except OSError:
        logger.warning("ready-to-play client: could not compare %s", mine, exc_info=True)
    return tuple(sorted(found))


def _drop_temp(tmp: Path, survivor: Path | None) -> None:
    """Remove a refresh's temporary file (this run's, or a crashed one's); raise if in the way."""
    try:
        st = _lstat(tmp)
    except FileNotFoundError:
        return
    if _stat_is_link(st):
        _remove_link(tmp, os.unlink)
    elif stat.S_ISDIR(st.st_mode):
        raise IsADirectoryError(errno.EISDIR, "a folder is in the way", str(tmp))
    else:
        _remove_file(tmp, survivor, os.unlink)


def _replace(tmp: Path, dst: Path) -> None:
    """Put `tmp` in `dst`'s place in one step, so no moment leaves the name missing.

    Windows refuses to replace a file whose read-only attribute is set. When that
    file is this folder's alone the attribute is cleared and the replace tried
    again; when another folder still shares it (a second ready-to-play client made
    from the same original), clearing it would change that folder too, so it is
    refused instead.
    """
    try:
        os.replace(tmp, dst)
        return
    except PermissionError:
        st = dst.lstat()
        if st.st_mode & stat.S_IWRITE:
            raise
    if st.st_nlink > 1:
        raise _Stop(
            f"{dst} is read-only and another folder shares it (another ready-to-play "
            "client made from the same client), so it could not be replaced without "
            "making that one writable too.",
            "Delete the other ready-to-play client made from the same client (Yu'lon can "
            "make it again), then refresh this one again.",
        )
    os.chmod(dst, st.st_mode | stat.S_IWRITE)
    os.replace(tmp, dst)


def refresh(
    play_dir: Path,
    original: Path,
    *,
    game: str,
    server_dir: Path,
    link: Callable[[Path, Path], None] = os.link,
    reflink: Callable[[Path, Path], bool] = try_reflink,
    keep: Collection[Path] = (),
) -> tuple[Path, ...]:
    """Bring what `stale()` lists back in step with the original; return what was changed.

    Only this game's and server's ready-to-play client, made from `original`.
    Each file is made beside the old one under a temporary name and then put in
    its place, so an interrupted refresh leaves every name holding a whole file,
    old or new. Nothing is ever removed, and WTF/ and Interface/ are never touched.

    Archives are shared the same way `create()` shares them. A full copy (its
    marker says so) is re-copied without asking: the player agreed to its size
    when it was made. A shared or cloned client that cannot share any more (the
    original moved to another drive) is refused rather than silently turned
    into a copy of many gigabytes.

    `keep` (relative paths, a module's files) is never touched, whatever `stale()`
    answers: the filter is applied here, to its list, so no list can reach past it.
    """
    marker = read_marker(play_dir)
    if marker is None:
        raise PlayClientError(
            f"{play_dir} was not made by Yu'lon (it has no {MARKER}), so nothing in it was "
            "changed. Make a ready-to-play client from Yu'lon instead."
        )
    whose = _whose(marker, game=game, server_dir=server_dir)
    if whose is not None:
        raise PlayClientError(
            f"{play_dir} is the ready-to-play client of {whose}, so nothing in it was "
            "changed. Refresh it from that server instead."
        )
    if _norm(original) != _norm(marker.source_client_dir):
        raise PlayClientError(
            f"{play_dir} was made from your client at {marker.source_client_dir}, not "
            f"{original}, so nothing in it was changed. Refresh it from "
            f"{marker.source_client_dir}, or delete it and make it again from {original}."
        )
    if not (original / "Data").is_dir():
        raise PlayClientError(
            f"{original} has no Data folder any more, so there is nothing to refresh "
            "from. Nothing was changed. Put your client back there, or delete the "
            "ready-to-play client and make it again from where your client is now."
        )
    is_kept = _kept(keep)
    todo = [rel for rel in stale(play_dir, original) if not _players_own(rel) and not is_kept(rel)]
    exe = client_executable(original)
    done: list[Path] = []
    for rel in todo:
        archive = rel.suffix.lower() in LINKED_SUFFIXES
        src, dst = (original / rel if archive else exe), play_dir / rel
        tmp = dst.with_name(dst.name + REFRESH_SUFFIX)
        try:
            if _original_file(src) is None:
                continue  # gone since stale() looked: keep what the client has
            _drop_temp(tmp, src)
            if archive and not marker.full_copy:
                try:
                    _share(src, tmp, link=link, reflink=reflink)
                except OSError as exc:
                    if exc.errno != errno.EXDEV and not _cannot_link(exc):
                        raise
                    raise _Stop(
                        f"{rel} could not be shared with your client {original} any more "
                        f"({exc}), and refreshing it would need a full copy.",
                        "Delete the ready-to-play client and make it again, agreeing to "
                        "a full copy.",
                    ) from exc
            else:
                shutil.copy2(src, tmp)
            _replace(tmp, dst)
        except Exception as exc:
            try:
                _drop_temp(tmp, src)
            except OSError:
                logger.warning("ready-to-play client: could not remove %s", tmp, exc_info=True)
            if isinstance(exc, _Stop):
                stop = exc
            elif isinstance(exc, OSError):
                logger.error("ready-to-play client: refreshing %s failed", dst, exc_info=True)
                stop = _Stop(f"Refreshing {dst} failed: {exc}.", "Fix the cause and try again.")
            else:
                raise
            kept = (
                "Nothing in the ready-to-play client was changed"
                if not done
                else f"{len(done)} file(s) were refreshed before it and the rest were left "
                "as they were"
            )
            raise PlayClientError(
                f"{stop.what} {kept}; your own client was left as it was. {stop.next_step}"
            ) from exc
        done.append(rel)
    if done:
        logger.info("ready-to-play client %s refreshed: %s", play_dir, ", ".join(map(str, done)))
    return tuple(done)


def _on_windows() -> bool:
    """A seam: the delete message names what only Windows does (an open file cannot go)."""
    return sys.platform == "win32"


def delete(play_dir: Path, *, game: str, server_dir: Path, can_try_again: bool = True) -> None:
    """Delete this server's ready-to-play client; the original keeps every shared file.

    Only a folder whose marker names this game and server. A folder that is
    already gone is not an error: what Delete promises is that it is not there.

    `can_try_again` is False when the press that asked cannot be made again (an
    Uninstall: the server, and its tab, are gone): a delete that stops part way
    then says to delete what is left by hand, rather than to try again.
    """
    if not os.path.lexists(play_dir):
        logger.info("ready-to-play client %s is already gone", play_dir)
        return
    try:
        is_link = _stat_is_link(_lstat(play_dir))
    except OSError as exc:
        raise PlayClientError(
            f"{play_dir} could not be looked at ({exc}), so it was left as it was. Check "
            "that you can open that folder, then try again."
        ) from exc
    if is_link:
        raise PlayClientError(
            f"{play_dir} is a link to another folder, so it was left as it was. Delete "
            "the link yourself if you no longer want it."
        )
    marker = read_marker(play_dir)
    if marker is None:
        raise PlayClientError(
            f"{play_dir} was not made by Yu'lon (it has no {MARKER}), so it was left as it "
            "was. Delete it yourself if you no longer want it."
        )
    whose = _whose(marker, game=game, server_dir=server_dir)
    if whose is not None:
        raise PlayClientError(
            f"{play_dir} is the ready-to-play client of {whose}, so it was left as it "
            "was. Delete it from that server instead."
        )
    try:
        remove_folder(play_dir, original=marker.source_client_dir)
    except OSError as exc:
        logger.warning("ready-to-play client: could not delete %s", play_dir, exc_info=True)
        if _on_windows():
            holder = (
                "close World of Warcraft if it is running from that folder, or from your own "
                "client: the two share their game files, and Windows cannot delete a file "
                "that either one has open"
            )
        else:
            holder = "close World of Warcraft if it is running from that folder"
        if not can_try_again:
            rest = f"Delete what is left by hand at {play_dir} ({holder} first)."
        elif read_marker(play_dir) is not None:
            rest = f"What is left still carries Yu'lon's marker: {holder}, then try again."
        else:
            rest = f"Delete what is left by hand at {play_dir} ({holder} first)."
        raise PlayClientError(
            f"{play_dir} could not be deleted completely: {exc}. Your own client was "
            f"left as it was. {rest}"
        ) from exc
    logger.info("ready-to-play client for %s at %s deleted", game, play_dir)


def left_out_archives(play_dir: Path, original: Path) -> tuple[Path, ...]:
    """The original's `*.MPQ`/`*.dll` that the ready-to-play client has no file for, sorted.

    Refresh never adds these (an archive that appeared in the original may be
    another server's module patch, and this folder is one server's), so Play and
    Refresh name them instead, and making the client again takes them in.
    Walked the way `plan()` walks: never through a link, never into the
    top-level folders it leaves out, nor into WTF/ or Interface/, which are the
    player's own here. An original without a Data folder answers nothing.
    """
    if not (original / "Data").is_dir():
        return ()
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(original, followlinks=False):
        here = Path(dirpath)
        rel_dir = here.relative_to(original)
        dirnames[:] = [
            name
            for name in dirnames
            if not _is_link(here / name)
            and not (rel_dir == Path(".") and name.lower() in LEFT_OUT)
            and not _players_own(rel_dir / name)
        ]
        for name in filenames:
            rel = rel_dir / name
            if (
                Path(name).suffix.lower() in LINKED_SUFFIXES
                and not _is_link(here / name)
                and not os.path.lexists(play_dir / rel)
            ):
                found.append(rel)
    return tuple(sorted(found))
