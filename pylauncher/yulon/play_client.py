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
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from yulon import rmtree
from yulon.log import get_logger

logger = get_logger(__name__)

MARKER = ".yulon-client.json"
LEFT_OUT = frozenset({"cache", "logs", "errors", "screenshots"})
"""Top-level folder names (lower-case) not carried over: WoW recreates them, and a
stale `Cache/` from another server confuses the client."""
LINKED_SUFFIXES = frozenset({".mpq", ".dll"})
PARTIAL_SUFFIX = ".yulon-partial"

_FICLONE = 0x40049409  # linux/fs.h: _IOW(0x94, 9, int)


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


def default_target(original: Path, game_display_name: str) -> Path:
    """A sibling of the original, on the same drive, named after the game."""
    return original.parent / f"{original.name} (Yu'lon – {game_display_name})"


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


def plan(original: Path, target: Path) -> BuildPlan:
    """Walk `original` and sort its files into linked and copied; refuse a bad target.

    Symlinks are neither followed nor reproduced: following one could pull in a
    folder outside the client (or the target itself), and recreating one could
    point a file WoW writes back into the original.
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
    if target.exists() and read_marker(target) is None:
        raise PlayClientError(
            f"{target} already exists and was not made by Yu'lon, so it was left as it "
            "was. Choose another folder, or move that one away first."
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


def _remove_leftover_partial(partial: Path) -> None:
    if not partial.exists():
        return
    if read_marker(partial) is None:
        raise PlayClientError(
            f"{partial} is in the way and was not made by Yu'lon, so it was left as it "
            "was. Move it away or choose another folder, then try again."
        )
    logger.info("ready-to-play client: removing an unfinished earlier attempt at %s", partial)
    try:
        rmtree.remove_tree(partial)
    except OSError as exc:
        raise PlayClientError(
            f"An unfinished earlier attempt at {partial} could not be removed: {exc}. "
            "Nothing new was created and your client was left as it was. Delete that "
            "folder yourself, then try again."
        ) from exc


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
    build = plan(original, target)
    if target.exists():
        raise PlayClientError(
            f"{target} already holds a ready-to-play client, so it was left as it was. "
            "Refresh it or delete it from Yu'lon instead of making it again."
        )
    partial = _partial(target)
    _remove_leftover_partial(partial)

    full_copy = False
    made = finished = False
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
                    if exc.errno != errno.EXDEV:
                        raise
                    if not allow_full_copy:
                        raise PlayClientError(
                            f"{target} is on another drive than your client {original}, "
                            "so its game files cannot be shared and a full copy needs "
                            f"{_gb(build.shared_bytes + build.own_bytes)} GB. Nothing was "
                            "created and your client was left as it was. Choose a folder "
                            "on the same drive, or agree to the full copy."
                        ) from exc
                    full_copy = True
                    logger.info("ready-to-play client: %s is on another volume, copying", target)
            shutil.copy2(src, dst)

        for rel in build.copied:
            dst = partial / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original / rel, dst)

        os.replace(partial, target)
        finished = True
    except PlayClientError:
        raise
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            needed = build.own_bytes + (build.shared_bytes if full_copy else 0)
            raise PlayClientError(
                f"The drive holding {target} ran out of space while building the "
                f"ready-to-play client: it needs {_gb(needed)} GB. The unfinished folder "
                "was removed and your client was left as it was. Free some space or "
                "choose another folder, then try again."
            ) from exc
        raise PlayClientError(
            f"Building the ready-to-play client in {target} failed: {exc}. The "
            "unfinished folder was removed and your client was left as it was. Fix "
            "the cause and try again."
        ) from exc
    except Exception as exc:
        logger.exception("ready-to-play client: building %s failed", target)
        raise PlayClientError(
            f"Building the ready-to-play client in {target} failed: {exc}. The "
            "unfinished folder was removed and your client was left as it was. Try "
            "again; if it fails the same way, send the Yu'lon log."
        ) from exc
    finally:
        if made and not finished:
            try:
                rmtree.remove_tree(partial)
            except OSError:
                # It carries the marker, so the next attempt removes it (T49 shapes).
                logger.warning("ready-to-play client: could not remove %s", partial, exc_info=True)

    logger.info(
        "ready-to-play client for %s built at %s (%d linked, %d copied%s)",
        game,
        target,
        len(build.linked),
        len(build.copied),
        ", full copy" if full_copy else "",
    )
    return marker
