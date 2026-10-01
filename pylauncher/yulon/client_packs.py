"""Getting and verifying a server's client packs (T181 b): the zips a ready-to-play client needs.

A catalog entry's `client.packs` names zips of patch files, addons and DLLs
(`catalog.ClientPack`). This module only GETS them and proves them; unpacking
into a ready-to-play client is a later step's job. Two sources:

* **The server's own checkout** (`fetch_checkout`). Centurion keeps its client
  patches in its repo at the pinned commit, so they always match the server
  build and nothing is downloaded. GitHub refuses files over 100 MB, so the
  large zips are split into `<name>.zip.partNN`; where the plain file is absent
  the parts are joined, in name order, into Yu'lon's cache. The join is checked
  against the pack's checksum (Centurion publishes `patches.md5`), because a
  joined set of parts is exactly where a missing piece goes unnoticed.
* **A URL on the server's own site** (`fetch_url`), for optional packs such as
  HD textures that are too large for the repo. Downloaded into the cache under
  `<entry>/<pack>/<version>/`, where the version is the text the site publishes
  beside the zip, so a changed pack lands in a new folder instead of being
  mistaken for the old one. Resumable: an interrupted download keeps its
  `.part` and the next attempt asks for the rest with an HTTP `Range`.

**What proves a file.** The pack's checksum where it has one; a URL pack may
have none, because the server's site may publish none, and then the size the
server declared, the zip's own CRCs (`ZipFile.testzip`) and the recorded
version stand in. Nothing that has not passed that check ever wears the cache
name: everything lands as a `.part`/`.joining` file first and is renamed only
once proved.

**Where requests may go.** https only, to the hosts the catalog entry names
(`Client.hosts()`), checked before the first request and again on every
redirect (`selfupdate.fetch._https_only_opener(hosts=…)`). The bounds on a
connection are the self-updater's: a watchdog that shuts down a socket that
sends nothing for `STALL_SECONDS`, `read1()` so the clock is consulted, and a
read never more than one byte past the size the server declared.

No Qt here: progress and cancel are callables the view supplies.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from yulon import __version__, platform
from yulon.catalog.catalog import ClientPack
from yulon.log import get_logger
from yulon.selfupdate import fetch
from yulon.update import _can_only_go_where_it_says, _Deadline

logger = get_logger(__name__)

CHUNK_BYTES = fetch.CHUNK_BYTES
STALL_SECONDS = fetch.STALL_SECONDS

VERSION_MAX_BYTES = 64
"""How much of a `.version` file is read. Centurion's are seven bytes (`1.00155`)."""

_VERSION_FORBIDDEN = frozenset('/\\<>:"|?*')
"""Centurion's launcher refuses version text holding any of these, and so does Yu'lon.

The version names a cache folder here, so these are also the characters that
would let remote text re-point a path (`/`, `\\`) or that Windows refuses in a
file name.
"""

_PART = re.compile(r"\.part(\d+)$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")


class PackError(RuntimeError):
    """A refusal worded for the player, naming what to do next. Nothing else leaves this module."""


class Cancelled(PackError):
    """The player pressed Cancel. What arrived is kept, so the next try continues from there."""


@dataclass(frozen=True)
class Fetched:
    """A pack's zip, proved: where it is, the version it was fetched at, and its SHA-256.

    `version` is the text the server published (a URL pack's `version_url`, a
    checkout pack's `<name>.version` beside the zip), or None where there is
    none. `sha256` is always the file's own, whatever checksum the catalog gave,
    so the install record can name one kind of digest for every pack.
    """

    path: Path
    version: str | None
    sha256: str


Progress = Callable[[int, int], None]
"""`(bytes so far, total)`; the total is what the server declared for the whole file."""


class Response(Protocol):
    """The little of an HTTP response this module uses, so a test can be one."""

    status: int

    def read1(self, amount: int, /) -> bytes: ...

    def getheader(self, name: str, default: str | None = None, /) -> str | None: ...

    def close(self) -> None: ...


class Opener(Protocol):
    """`(url, watchdog, method=, headers=, hosts=) -> Response`; `hosts` bounds redirects."""

    def __call__(
        self,
        url: str,
        watcher: _Deadline,
        *,
        method: str,
        headers: Mapping[str, str],
        hosts: frozenset[str],
    ) -> Response: ...


def _open(
    url: str,
    watcher: _Deadline,
    *,
    method: str,
    headers: Mapping[str, str],
    hosts: frozenset[str],
) -> Response:
    """The real opener: verified TLS, the watchdog attached, redirects only to `hosts`."""
    request = urllib.request.Request(
        url, method=method, headers={"User-Agent": f"yulon/{__version__}", **headers}
    )
    opener = fetch._https_only_opener(watcher, platform.verify_context(), hosts=hosts)
    opened = opener.open(request, timeout=fetch._SOCKET_TIMEOUT)
    return opened  # type: ignore[no-any-return]


def cache_dir() -> Path:
    """Where proved pack zips are kept: `<config dir>/client-packs`."""
    return platform.config_dir() / "client-packs"


def _size_text(size: int) -> str:
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} GB"
    if size >= 1024**2:
        return f"{size / 1024**2:.0f} MB"
    return f"{size} bytes"


def _free_bytes(folder: Path) -> int:
    """Free space on `folder`'s volume. A seam, so a test can be a full disk."""
    return shutil.disk_usage(folder).free


def _refuse_without_room(pack: ClientPack, folder: Path, needed: int) -> None:
    """Refuse BEFORE writing anything when the cache's volume cannot hold `needed` more bytes."""
    free = _free_bytes(folder)
    if free < needed:
        raise PackError(
            f"{pack.label} needs {_size_text(needed)} of free space in {folder}, and that drive "
            f"has {_size_text(free)}. Free some space and try again."
        )


def _digests(path: Path) -> tuple[str, str]:
    """The file's SHA-256 and MD5, in one pass so a 1.4 GB zip is read once."""
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            sha256.update(chunk)
            md5.update(chunk)
    return sha256.hexdigest(), md5.hexdigest()


def _prove(pack: ClientPack, path: Path) -> str:
    """The file's SHA-256, once it matches the pack's checksum (or, with none, its zip CRCs).

    Raises `PackError` and leaves the file where it is: the caller decides
    whether it may be deleted (a cache file may; a file of the server's own
    checkout may not).
    """
    sha256, md5 = _digests(path)
    expected = pack.sha256 or pack.md5
    if expected is not None:
        actual = sha256 if pack.sha256 is not None else md5
        if actual != expected:
            raise PackError(
                f"{pack.label} does not match its published checksum ({actual[:12]}… instead "
                f"of {expected[:12]}…), so Yu'lon did not use it. Try again; if it happens "
                "again, the server's makers have changed the file."
            )
        return sha256
    try:
        with zipfile.ZipFile(path) as archive:
            broken = archive.testzip()
    except (zipfile.BadZipFile, OSError, EOFError) as exc:
        raise PackError(
            f"{pack.label} is not a readable zip ({exc}), so Yu'lon did not use it. Try again."
        ) from exc
    if broken is not None:
        raise PackError(
            f"{pack.label} arrived damaged ({broken} fails its check), so Yu'lon did not use "
            "it. Try again."
        )
    return sha256


def _checked_version(pack: ClientPack, raw: bytes, where: str) -> str:
    """Version text trimmed, or refused: it names a cache folder, so it must be one name."""
    try:
        text = raw.decode("utf-8-sig").strip()
    except UnicodeDecodeError:
        text = ""
    if (
        not text
        or len(raw) > VERSION_MAX_BYTES
        or text in (".", "..")
        or text.endswith(".")
        or any(ch in _VERSION_FORBIDDEN or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)
    ):
        raise PackError(
            f"{pack.label}: the version published at {where} is not a version Yu'lon can use "
            f"({raw[:40]!r}). Try again later; if it stays, tell the server's makers."
        )
    return text


# --- From the server's own checkout --------------------------------------------------------


def _parts_of(path: Path) -> list[Path]:
    """`<path>.partNN` beside `path`, in order: part10 after part09, part2 before part10."""
    try:
        siblings = list(path.parent.iterdir())
    except OSError:
        return []
    found: list[tuple[int, str, Path]] = []
    for sibling in siblings:
        if not sibling.name.startswith(path.name + ".part") or not sibling.is_file():
            continue
        match = _PART.search(sibling.name)
        if match and sibling.name == f"{path.name}.part{match.group(1)}":
            found.append((int(match.group(1)), sibling.name, sibling))
    return [part for _, _, part in sorted(found)]


def _checkout_version(pack: ClientPack, path: Path) -> str | None:
    """`<stem>.version` beside the zip (Centurion: `patch-Y.version`, `1.00148`), if any."""
    beside = path.with_name(PurePosixPath(path.name).stem + ".version")
    if not beside.is_file():
        return None
    with beside.open("rb") as handle:
        raw = handle.read(VERSION_MAX_BYTES + 1)
    return _checked_version(pack, raw, str(beside))


def fetch_checkout(pack: ClientPack, server_dir: Path) -> Fetched:
    """A checkout pack's zip, proved against its checksum.

    The plain file when the checkout has it, verified where it is (and never
    deleted: it is the server's own file). Otherwise its `.partNN` pieces
    joined in order into `cache_dir()/checkout/<checksum>/` through a
    `.joining` file that is renamed only once the join matches the checksum;
    a join that does not match is removed. A joined file already in the cache
    is proved again before it is used, and joined afresh if it fails.
    """
    source = pack.source
    if source.kind != "checkout" or source.path is None:
        raise PackError(f"{pack.label} does not come from the server's checkout.")
    path = server_dir / source.path
    version = _checkout_version(pack, path)
    if path.is_file():
        try:
            return Fetched(path, version, _prove(pack, path))
        except PackError as exc:
            raise PackError(
                f"{exc} The file is {path}; updating the server's sources fetches it again."
            ) from exc
    parts = _parts_of(path)
    if not parts:
        raise PackError(
            f"{pack.label}: this server's checkout has no {source.path} (nor its .partNN "
            "pieces) at the commit it is on, so Yu'lon cannot make its client. Update the "
            "server, or return it to the tested pin, and try again."
        )
    digest = pack.sha256 or pack.md5
    assert digest is not None  # catalog validation: a checkout pack always carries a checksum
    folder = cache_dir() / "checkout" / digest
    dest = folder / path.name
    if dest.is_file():
        try:
            return Fetched(dest, version, _prove(pack, dest))
        except PackError:
            logger.info(f"client-packs: cached {dest} no longer proves; joining it again")
            dest.unlink(missing_ok=True)
    folder.mkdir(parents=True, exist_ok=True)
    _refuse_without_room(pack, folder, sum(part.stat().st_size for part in parts))
    joining = dest.with_name(dest.name + ".joining")
    try:
        with joining.open("wb") as out:
            for part in parts:
                with part.open("rb") as piece:
                    shutil.copyfileobj(piece, out, CHUNK_BYTES)
        sha256 = _prove(pack, joining)
        os.replace(joining, dest)
    except BaseException:
        joining.unlink(missing_ok=True)
        raise
    logger.info(f"client-packs: joined {len(parts)} parts of {source.path} into {dest}")
    return Fetched(dest, version, sha256)


# --- From the server's site ----------------------------------------------------------------


def _refuse_unlisted(pack: ClientPack, url: str, allowed_hosts: frozenset[str]) -> None:
    host = urllib.parse.urlsplit(url).hostname
    if host is None or host not in allowed_hosts or not _can_only_go_where_it_says(url, host):
        raise PackError(
            f"{pack.label} would be fetched from {host or url!r}, which is not a plain https "
            "address on a host this server's catalog entry names, so Yu'lon did not ask it."
        )


def _request(
    pack: ClientPack,
    opener: Opener,
    url: str,
    watcher: _Deadline,
    *,
    method: str,
    headers: Mapping[str, str],
    hosts: frozenset[str],
) -> Response:
    """Open `url`, turning every failure into a `PackError` the player can act on."""
    try:
        response = opener(url, watcher, method=method, headers=headers, hosts=hosts)
    except urllib.error.HTTPError as exc:
        raise PackError(
            f"{pack.label} is not available from the server's site right now (HTTP {exc.code} "
            f"for {url}). Try again later."
        ) from exc
    except fetch.UpdateError as exc:
        raise PackError(f"{pack.label}: {exc}") from exc
    except Exception as exc:
        raise PackError(
            f"{pack.label}: the server's site could not be reached ({exc}). Check the "
            "connection and try again."
        ) from exc
    if not 200 <= response.status < 300:
        _close(response)
        raise PackError(
            f"{pack.label} is not available from the server's site right now (HTTP "
            f"{response.status} for {url}). Try again later."
        )
    return response


def _close(response: Response) -> None:
    try:
        response.close()
    except Exception as exc:  # noqa: BLE001 - closing is best effort
        logger.debug(f"client-packs: closing a connection raised {exc}")


def _fetch_version(pack: ClientPack, url: str, opener: Opener, hosts: frozenset[str]) -> str:
    """The version text the site publishes, read no further than `VERSION_MAX_BYTES` + 1."""
    data = b""
    with _Deadline(STALL_SECONDS) as watcher:
        response = _request(pack, opener, url, watcher, method="GET", headers={}, hosts=hosts)
        try:
            while len(data) <= VERSION_MAX_BYTES:
                try:
                    chunk = response.read1(VERSION_MAX_BYTES + 1 - len(data))
                except Exception as exc:
                    raise PackError(
                        f"{pack.label}: its version could not be read ({exc}). Try again."
                    ) from exc
                if watcher.fired:
                    raise PackError(f"{pack.label}: its version did not arrive. Try again.")
                if not chunk:
                    break
                data += chunk
        finally:
            _close(response)
    return _checked_version(pack, data, url)


def _declared_size(pack: ClientPack, url: str, opener: Opener, hosts: frozenset[str]) -> int:
    """The zip's size from a HEAD request: the bound every read below is held to."""
    with _Deadline(STALL_SECONDS) as watcher:
        response = _request(pack, opener, url, watcher, method="HEAD", headers={}, hosts=hosts)
        declared = response.getheader("Content-Length")
        _close(response)
    if declared is None or not declared.strip().isdigit():
        raise PackError(
            f"{pack.label}: the server's site did not say how large it is, so Yu'lon cannot "
            "download it safely. Try again later."
        )
    return int(declared.strip())


_CONTENT_RANGE = re.compile(r"^\s*bytes\s+(\d+)-(\d+)/(\d+|\*)\s*$")


class _Discard(PackError):
    """A refusal after which the `.part` is worthless and is deleted, not resumed."""


def _download(
    pack: ClientPack,
    url: str,
    part: Path,
    have: int,
    total: int,
    *,
    opener: Opener,
    hosts: frozenset[str],
    progress: Progress | None,
    cancelled: Callable[[], bool],
) -> None:
    """Bring `part` from `have` bytes to `total`, asking only for the rest.

    A cut connection, a stall and a cancel keep the `.part`, so the next call
    continues from its size. A body that contradicts the declared size, or a
    range answer that does not start where the `.part` ends, deletes it: what
    is on disk can no longer be trusted to be the start of this file.
    """
    headers = {"Range": f"bytes={have}-"} if have else {}
    done = have
    try:
        with _Deadline(STALL_SECONDS) as watcher:
            response = _request(
                pack, opener, url, watcher, method="GET", headers=headers, hosts=hosts
            )
            try:
                if have and response.status == 206:
                    match = _CONTENT_RANGE.match(response.getheader("Content-Range") or "")
                    if (
                        match is None
                        or int(match.group(1)) != have
                        or match.group(3) not in ("*", str(total))
                    ):
                        raise _Discard(
                            f"{pack.label}: the server's site answered the resumed download "
                            "with a different part of the file. Try again; it starts over."
                        )
                    mode = "ab"
                else:
                    # A 200 to a Range request is the whole file from byte 0:
                    # the server does not resume, so neither does this.
                    done, mode = 0, "wb"
                declared = response.getheader("Content-Length")
                if declared is not None and declared.strip().isdigit():
                    if int(declared) != total - done:
                        raise _Discard(
                            f"{pack.label}: the server's site sent {declared} bytes where it "
                            f"had said {total - done}, so Yu'lon stopped. Try again."
                        )
                with part.open(mode) as handle:
                    while True:
                        if cancelled():
                            raise Cancelled(
                                f"The download of {pack.label} was cancelled; what arrived is "
                                "kept, and the next try continues from there."
                            )
                        try:
                            chunk = response.read1(min(CHUNK_BYTES, total - done + 1))
                        except Exception as exc:
                            raise PackError(
                                f"The download of {pack.label} stopped at "
                                f"{_size_text(done)} of {_size_text(total)} ({exc}). Try "
                                "again; it continues from there."
                            ) from exc
                        if watcher.fired:
                            raise PackError(
                                f"The download of {pack.label} sent nothing for "
                                f"{STALL_SECONDS:.0f}s and was stopped at {_size_text(done)}. "
                                "Try again; it continues from there."
                            )
                        if not chunk:
                            break
                        watcher.restart(STALL_SECONDS)
                        handle.write(chunk)
                        done += len(chunk)
                        if done > total:
                            raise _Discard(
                                f"{pack.label} is longer than the server's site said "
                                f"({total} bytes), so Yu'lon stopped. Try again."
                            )
                        if progress is not None:
                            progress(done, total)
            finally:
                _close(response)
    except _Discard:
        part.unlink(missing_ok=True)
        raise
    if done < total:
        raise PackError(
            f"The download of {pack.label} stopped at {_size_text(done)} of "
            f"{_size_text(total)}. Try again; it continues from there."
        )


def _file_name(url: str) -> str:
    name = PurePosixPath(urllib.parse.urlsplit(url).path).name
    return name if _SAFE_NAME.match(name) else "pack.zip"


def fetch_url(
    pack: ClientPack,
    *,
    entry_id: str,
    allowed_hosts: frozenset[str],
    opener: Opener = _open,
    progress: Progress | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> Fetched:
    """A URL pack's zip in `cache_dir()/<entry>/<pack>/<version>/`, downloaded if need be, proved.

    Every host is checked before the first request. The version is fetched
    first, because it names the folder; a cached zip there is proved again and
    used. Otherwise the size comes from a HEAD request, the drive is checked
    for the bytes still missing, and the rest is downloaded onto `<file>.part`
    (resumed from its size), proved, and renamed into place. A `.part` that
    fails its proof is deleted, so the next attempt starts clean.
    """
    source = pack.source
    if source.kind != "url" or source.url is None:
        raise PackError(f"{pack.label} does not come from the server's site.")
    for url in (source.url, source.version_url):
        if url is not None:
            _refuse_unlisted(pack, url, allowed_hosts)
    for name in (entry_id, pack.id):
        if not _SAFE_NAME.match(name):
            raise PackError(f"{pack.label}: {name!r} cannot name a cache folder.")
    version = (
        _fetch_version(pack, source.version_url, opener, allowed_hosts)
        if source.version_url is not None
        else None
    )
    folder = cache_dir() / entry_id / pack.id / (version or pack.sha256 or pack.md5 or "current")
    dest = folder / _file_name(source.url)
    if dest.is_file():
        try:
            return Fetched(dest, version, _prove(pack, dest))
        except PackError:
            logger.info(f"client-packs: cached {dest} no longer proves; downloading it again")
            dest.unlink(missing_ok=True)
    folder.mkdir(parents=True, exist_ok=True)
    total = _declared_size(pack, source.url, opener, allowed_hosts)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    if have > total:
        part.unlink()
        have = 0
    _refuse_without_room(pack, folder, total - have)
    if have < total:
        _download(
            pack,
            source.url,
            part,
            have,
            total,
            opener=opener,
            hosts=allowed_hosts,
            progress=progress,
            cancelled=cancelled,
        )
    try:
        sha256 = _prove(pack, part)
    except PackError:
        part.unlink(missing_ok=True)
        raise
    os.replace(part, dest)
    logger.info(f"client-packs: {pack.id} {version or ''} downloaded to {dest}")
    return Fetched(dest, version, sha256)
