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

No Qt here: the dialog that asks and the progress it shows live in the view.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from yulon.log import get_logger

logger = get_logger(__name__)

MARKER = ".yulon-client.json"
LEFT_OUT = frozenset({"cache", "logs", "errors", "screenshots"})
"""Top-level folder names (lower-case) not carried over: WoW recreates them, and a
stale `Cache/` from another server confuses the client."""
LINKED_SUFFIXES = frozenset({".mpq", ".dll"})
PARTIAL_SUFFIX = ".yulon-partial"

_FICLONE = 0x40049409  # linux/fs.h: _IOW(0x94, 9, int)

_CANNOT_LINK = frozenset({errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK})
"""`link()` errors that mean THIS drive cannot share files (exFAT/FAT32, some network
shares, a file at its link limit). Same answer as another drive: a full copy, asked."""
_ERROR_INVALID_FUNCTION = 1  # Windows' answer from a filesystem without hard links


class Marker(BaseModel):
    """`.yulon-client.json`: what says a folder is Yu'lon's to change or delete."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    game: str
    server_dir: Path
    source_client_dir: Path
    created_at: datetime


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
    shared = own = 0
    for dirpath, dirnames, filenames in os.walk(original, followlinks=False):
        here = Path(dirpath)
        rel_dir = here.relative_to(original)
        kept = []
        for name in dirnames:
            if (here / name).is_symlink():
                logger.info("ready-to-play client: skipping symlinked folder %s", here / name)
            elif rel_dir == Path(".") and name.lower() in LEFT_OUT:
                continue
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            path = here / name
            if path.is_symlink():
                logger.info("ready-to-play client: skipping symlink %s", path)
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

    same_volume = os.stat(original).st_dev == os.stat(_existing_ancestor(target.parent)).st_dev
    return BuildPlan(
        linked=tuple(linked),
        copied=tuple(copied),
        shared_bytes=shared,
        own_bytes=own,
        same_volume=same_volume,
    )


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
    for dirpath, dirnames, filenames in os.walk(folder, topdown=False, followlinks=False):
        here = Path(dirpath)
        for name in filenames + [d for d in dirnames if (here / d).is_symlink()]:
            _remove_file(here / name, folder, original, unlink)
        for name in dirnames:
            sub = here / name
            if not sub.is_symlink():
                _remove_dir(sub)
    _remove_dir(folder, parent_is_ours=False)  # its parent is the player's, not ours


def _make_writable(path: Path) -> None:
    try:
        os.chmod(path, path.lstat().st_mode | stat.S_IWRITE)
    except OSError:
        pass


def _remove_file(
    path: Path, folder: Path, original: Path | None, unlink: Callable[[Path], None]
) -> None:
    """Delete one file, clearing its own read-only flag only if nothing else helps.

    First the directory (this folder's own: on POSIX its write bit is what
    refuses an unlink), and only then the file itself (Windows' read-only
    attribute), because on a hard link that flag is the original's too.
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
        os.chmod(path, stat.S_IMODE(st.st_mode))
        raise
    if st.st_nlink <= 1:
        return
    survivor = original / path.relative_to(folder) if original is not None else None
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


def _discard(partial: Path, original: Path | None) -> bool:
    """Remove an unfinished build; False (logged) if it could not be removed."""
    try:
        remove_folder(partial, original=original)
    except OSError:
        logger.warning("ready-to-play client: could not remove %s", partial, exc_info=True)
        return False
    return True


def _remove_leftover_partial(partial: Path, *, game: str, server_dir: Path) -> None:
    if not partial.exists():
        return
    marker = read_marker(partial)
    if marker is None:
        raise PlayClientError(
            f"{partial} is in the way and was not made by Yu'lon, so it was left as it "
            "was. Move it away or choose another folder, then try again."
        )
    if marker.game != game or marker.server_dir != server_dir:
        raise PlayClientError(
            f"{partial} is the unfinished ready-to-play client of another server at "
            f"{marker.server_dir}, so it was left as it was. Choose another folder."
        )
    logger.info("ready-to-play client: removing an unfinished earlier attempt at %s", partial)
    try:
        remove_folder(partial, original=marker.source_client_dir)
    except OSError as exc:
        raise PlayClientError(
            f"An unfinished earlier attempt at {partial} could not be removed: {exc}. "
            "Nothing new was created and your client was left as it was. Delete that "
            "folder yourself, then try again."
        ) from exc


class _Stop(Exception):
    """A build that stops: what happened, and what to do next (the middle is the cleanup)."""

    def __init__(self, what: str, next_step: str) -> None:
        super().__init__(what)
        self.what = what
        self.next_step = next_step


def _cannot_link(exc: OSError) -> bool:
    return exc.errno in _CANNOT_LINK or getattr(exc, "winerror", None) == _ERROR_INVALID_FUNCTION


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
) -> Marker:
    """Build the ready-to-play client for `game` at `server_dir` in `target`.

    Linked-class files are cloned where the filesystem can, else hard-linked;
    across volumes they are copied only when `allow_full_copy`. The folder
    appears at `target` complete or not at all.
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
                if reflink(src, dst):
                    continue
                try:
                    link(src, dst)
                    continue
                except OSError as exc:
                    if exc.errno == errno.EXDEV:
                        why = f"{target} is on another drive than your client {original}"
                    elif _cannot_link(exc):
                        why = f"The drive holding {target} cannot share files with your client"
                    else:
                        raise
                    if not allow_full_copy:
                        raise _Stop(
                            f"{why}, so its game files cannot be shared and a full copy "
                            f"needs {full_size} GB.",
                            "Choose a folder on a drive that can share them (the one your "
                            "client is on), or agree to the full copy.",
                        ) from exc
                    full_copy = True
                    logger.info("ready-to-play client: %s (%s), copying in full", why, exc)
            shutil.copy2(src, dst)

        for rel in build.copied:
            dst = partial / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original / rel, dst)

        os.replace(partial, target)
    except BaseException as exc:
        cleaned = _discard(partial, original) if made else True
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
        if cleaned:
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
