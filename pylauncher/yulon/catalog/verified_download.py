"""A pinned download, and an unpack of exactly the files a catalog names.

Three steps more than one engine runs, kept in one place so they cannot drift:

* `fetch_bounded()` -- stream a url to a path through a caller's `Opener`, held to a
  byte bound and a Stop. `platform.download_verified` is the wrong tool for a release
  asset (T850-B, review round 1): its `curl --location` follows a redirect to ANY
  https host and reads without a bound, where this goes through `client_packs._open`
  (verified TLS, the stall watchdog, redirects only to the hosts the caller names).
* `download_verified()` -- fetch a url to a path and refuse it unless its size and
  sha256 are the pin's, with the retry-once rule `ProcessInstaller` carried (T850-B
  lifted it here): a file already at the path that fails the check is deleted and
  fetched ONCE more, because a download seam reuses a completed file as it finds it
  and a transfer cut off by a lost connection is complete by its own lights.
* `unzip_members()` -- extract EXACTLY the members a catalog lists, each of which
  must be a regular file at the zip's ROOT. A release asset laid out flat (MoP's
  four `.sql` files, which is why T850-B exists) has no top-level folder to filter
  on, so the member list IS the fence and a name nobody listed is never written.

All are plain functions whose seams are handed in, the shape `platform`'s own
helpers use: the caller owns the network call and the cancellation, this module
owns the checking.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path, PurePosixPath

from yulon.catalog.installer import InstallerError, InstallStopped
from yulon.catalog.native import _cancelled_message
from yulon.client_packs import STALL_SECONDS, Opener
from yulon.update import _Deadline

CHUNK = 1 << 20
"""Bytes copied at a time: hashing and unpacking both stream, and these files are hundreds of MB."""

UNPACKED = ".yulon-unpacked"
"""The note a folder this app unpacked carries: the url and sha256 it was unpacked from.

Written LAST, in the partial folder, so it arrives with the rename: a folder holding
it is a folder this app finished unpacking, and the next press reads it to skip the
download. `unpacked_note()` is its only reader.
"""


def sha256_of(path: Path) -> str:
    """The file's sha256, read in chunks: the archives are tens of megabytes."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def download_verified(
    url: str,
    path: Path,
    *,
    size: int,
    sha256: str,
    download: Callable[[str, Path], Path],
) -> Iterator[str]:
    """Fetch `url` to `path` and refuse it unless its size and sha256 are the pin's.

    A file already at `path` that fails the check is deleted and fetched once
    more: the download seam reuses a completed file as it finds it, and a download
    cut off by a lost connection is complete by its own lights.
    """
    for attempt in (1, 2):
        if not path.is_file():
            yield f"Downloading {url}"
        try:
            download(url, path)
        except OSError as exc:
            raise InstallerError(f"{url} could not be downloaded: {exc}") from exc
        actual = path.stat().st_size
        if actual == size and sha256_of(path) == sha256:
            yield f"{path.name} matches its pinned size and sha256."
            return
        path.unlink(missing_ok=True)
        if attempt == 2:
            raise InstallerError(
                f"{url} did not match the size and sha256 this version of Yu'lon "
                f"pins ({actual} bytes, expected {size}). It was deleted and nothing "
                "was unpacked."
            )
        yield "The download did not match its pinned sha256; fetching it again."


def fetch_bounded(
    url: str,
    path: Path,
    *,
    size: int,
    hosts: frozenset[str],
    opener: Opener,
    cancelled: Callable[[], bool],
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Stream `url` to `path` through `opener`, held to `size` bytes and a Stop.

    The download a release asset needs and `platform.download_verified` cannot give
    it (T850-B, review round 1): that one's `curl --location` follows a redirect to
    ANY https host and reads without a byte bound. Here the opener is
    `client_packs._open`, so TLS is verified, the stall watchdog rides each connection
    (`_Deadline`, restarted on every chunk), and a redirect may go only to `hosts`.

    The bytes land in `<path>.part` and are renamed onto `path` only when the body
    ends. A body of MORE than `size` bytes stops the fetch the moment `size + 1` have
    arrived, and the part is DELETED: GitHub answers a release download with a signed
    redirect a second request cannot resume, so a half file is worth nothing (MoP's
    zip is 142 MB). `cancelled()` is asked between chunks and raises the stage's stop
    the way `_check_cancel` does, the part deleted for the same reason.
    """
    part = path.with_name(path.name + ".part")
    path.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    try:
        with _Deadline(STALL_SECONDS) as watcher:
            try:
                response = opener(url, watcher, method="GET", headers={}, hosts=hosts)
            except InstallerError:
                raise
            except Exception as exc:  # noqa: BLE001 -- worded for the player just below
                raise InstallerError(f"{url} could not be downloaded: {exc}") from exc
            try:
                with part.open("wb") as out:
                    while True:
                        if cancelled():
                            raise InstallStopped(_cancelled_message("the install"))
                        try:
                            chunk = response.read1(min(CHUNK, size - done + 1))
                        except Exception as exc:  # noqa: BLE001 -- the same wording
                            raise InstallerError(f"{url} could not be downloaded: {exc}") from exc
                        if watcher.fired:
                            raise InstallerError(
                                f"{url} sent nothing for {STALL_SECONDS:.0f}s and the download "
                                "was stopped; nothing was unpacked."
                            )
                        if not chunk:
                            break
                        watcher.restart(STALL_SECONDS)
                        out.write(chunk)
                        done += len(chunk)
                        if done > size:
                            raise InstallerError(
                                f"{url} sent more than the {size} bytes this version of Yu'lon "
                                "pins; nothing was unpacked."
                            )
                        if progress is not None:
                            progress(done, size)
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    part.replace(path)
    return path


def unzip_members(
    zip_path: Path,
    partial: Path,
    members: Sequence[str],
    *,
    cancel_check: Callable[[], None],
) -> None:
    """Extract exactly `members` from `zip_path` into `partial`, streaming each one.

    Every listed name must be a regular file at the zip's root: a name the zip does
    not hold there is refused BY NAME, and nothing the list does not name is ever
    extracted, whatever else the zip holds. A listed name that could land outside
    `partial` is refused too -- the catalog's own rule for these names (a plain file
    name, no `/`, `\\`, `..` or `:`) is checked a second time here, where the path
    is built.

    `partial` is created only once every listed member has been found, so a refused
    zip leaves no half-folder behind. `cancel_check` is called before each member: a
    Stop between two of them leaves the partial folder for the next run to remove.
    """
    for name in members:
        rel = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or ":" in name
            or rel.is_absolute()
            or ".." in rel.parts
            or len(rel.parts) != 1
        ):
            raise InstallerError(
                f"{zip_path.name} was asked for {name!r}, which is not a plain file name at its "
                "root, so nothing was unpacked."
            )
    with zipfile.ZipFile(zip_path) as zipped:
        found: dict[str, zipfile.ZipInfo] = {}
        for info in zipped.infolist():
            if info.filename in members and not info.is_dir():
                found.setdefault(info.filename, info)
        missing = [name for name in members if name not in found]
        if missing:
            raise InstallerError(
                f"{zip_path.name} does not hold {missing[0]} at its root, so nothing was unpacked."
            )
        partial.mkdir(parents=True, exist_ok=True)
        for name in members:
            cancel_check()
            with zipped.open(found[name]) as source, (partial / name).open("wb") as out:
                shutil.copyfileobj(source, out, CHUNK)


def unpacked_note(folder: Path) -> dict[str, str] | None:
    """The note `folder` carries when this app unpacked it, or None.

    None for a folder with no note, one that cannot be read, and one whose note is
    not the `{url, sha256}` pair every note this app writes holds: a folder that
    cannot say what it is is somebody else's, and is never treated as this app's.
    """
    try:
        note = json.loads((folder / UNPACKED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(note, dict):
        return None
    url, sha256 = note.get("url"), note.get("sha256")
    if not isinstance(url, str) or not isinstance(sha256, str) or not url or not sha256:
        return None
    return {"url": url, "sha256": sha256}


def write_unpacked(folder: Path, *, url: str, sha256: str) -> None:
    """Write `folder`'s note: the asset this app unpacked it from (`UNPACKED`)."""
    (folder / UNPACKED).write_text(
        json.dumps({"url": url, "sha256": sha256}, indent=2) + "\n", encoding="utf-8"
    )
