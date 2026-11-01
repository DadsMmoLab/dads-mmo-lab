"""A pinned download, and an unpack of exactly the files a catalog names.

Two steps more than one engine runs, kept in one place so they cannot drift:

* `download_verified()` -- fetch a url to a path and refuse it unless its size and
  sha256 are the pin's, with the retry-once rule `ProcessInstaller` carried (T850-B
  lifted it here): a file already at the path that fails the check is deleted and
  fetched ONCE more, because a download seam reuses a completed file as it finds it
  and a transfer cut off by a lost connection is complete by its own lights.
* `unzip_members()` -- extract EXACTLY the members a catalog lists, each of which
  must be a regular file at the zip's ROOT. A release asset laid out flat (MoP's
  four `.sql` files, which is why T850-B exists) has no top-level folder to filter
  on, so the member list IS the fence and a name nobody listed is never written.

Both are plain functions whose seams are handed in, the shape `platform`'s own
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

from yulon.catalog.installer import InstallerError

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
