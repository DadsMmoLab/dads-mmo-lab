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

import contextlib
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from yulon import (
    __version__,
    client_config,
    client_names,
    platform,
    play_client,
    server_build_presses,
)
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

_WINDOWS_DEVICES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{n}" for n in range(1, 10)),
        *(f"LPT{n}" for n in range(1, 10)),
    }
)
"""Names Windows treats as devices, with or without an extension: `NUL.txt` is `NUL`."""

_PART = re.compile(r"\.part(\d+)$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")


class PackError(RuntimeError):
    """A refusal worded for the player, naming what to do next. Nothing else leaves this module."""


class PackUnavailable(PackError):
    """The pack's address answered 404 or 410: the server's site no longer has it.

    The view shows such a pack as "unavailable" instead of a failure to retry.
    """


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
    """Where proved pack zips are kept.

    On Windows `%LOCALAPPDATA%\\yulon\\client-packs`: the zips are gigabytes of
    regenerable downloads, which must not ride in the Roaming profile that
    `platform.config_dir()` (`%APPDATA%`) names. Where `LOCALAPPDATA` is unset,
    and on Linux and macOS, `<config dir>/client-packs`.
    """
    if platform.detect() == "windows":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / platform.APP_DIR_NAME / "client-packs"
    return platform.config_dir() / "client-packs"


def _os_refusal(pack: ClientPack, exc: OSError) -> PackError:
    """A `PackError` for a failed read, write or folder: what failed, where, and what to do."""
    where = exc.filename or cache_dir()
    reason = exc.strerror or str(exc)
    return PackError(
        f"{pack.label}: Yu'lon could not use {where} ({reason}). Free some space and check "
        "that Yu'lon may write there, then press Play again; a download continues from what "
        "has already arrived."
    )


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


def _never() -> bool:
    return False


def _stop_if_asked(pack: ClientPack, cancelled: Callable[[], bool]) -> None:
    """`Cancelled` once the caller's Stop is set: asked between chunks of every long read (T303).

    Laying a pack into the map-data copy reads the pack's zip end to end to prove
    it and copies its archives, which took 47 s for one pack on yulon-win11
    (2026-10-05); a Stop that waited for that went on for 37 s. Asked per chunk,
    a Stop costs at most one chunk, and the copy is left as `install()` leaves a
    refusal: nothing of the pack renamed in, its temporaries removed.
    """
    if cancelled():
        raise Cancelled(f"Laying {pack.label} was stopped part-way; nothing of it was installed.")


def _copy(source: Any, out: Any, pack: ClientPack, cancelled: Callable[[], bool]) -> None:
    """`shutil.copyfileobj()` that asks `cancelled` before every chunk (T303)."""
    while chunk := source.read(CHUNK_BYTES):
        _stop_if_asked(pack, cancelled)
        out.write(chunk)


def _digests(
    path: Path, pack: ClientPack | None = None, cancelled: Callable[[], bool] = _never
) -> tuple[str, str]:
    """The file's SHA-256 and MD5, in one pass so a 1.4 GB zip is read once."""
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            if pack is not None:
                _stop_if_asked(pack, cancelled)
            sha256.update(chunk)
            md5.update(chunk)
    return sha256.hexdigest(), md5.hexdigest()


_RETRY = "Try again; if it happens again, the server's makers have changed the file."
_FROM_SOURCES = (
    f"Press {server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)} "
    "to fetch the server's sources again; if it stays, the server's makers have changed "
    "the file."
)


Expected = tuple[str, str]
"""`("sha256" | "md5", hex)`: what a file must hash to, from the catalog or the checkout."""


def _pinned(pack: ClientPack) -> Expected | None:
    """The checksum the catalog itself carries for `pack`, if any."""
    if pack.sha256 is not None:
        return ("sha256", pack.sha256)
    if pack.md5 is not None:
        return ("md5", pack.md5)
    return None


def _prove(
    pack: ClientPack,
    path: Path,
    advice: str = _RETRY,
    *,
    expected: Expected | None = None,
    cancelled: Callable[[], bool] = _never,
) -> str:
    """The file's SHA-256, once it matches the pack's checksum (or, with none, its zip CRCs).

    `expected` is the checksum to hold the file to; by default the one the
    catalog pins (a checkout pack whose md5 is read from the checkout passes it
    in, `_expected_from_checkout`).

    Raises `PackError` and leaves the file where it is: the caller decides
    whether it may be deleted (a cache file may; a file of the server's own
    checkout may not).
    """
    sha256, md5 = _digests(path, pack, cancelled)
    want = expected if expected is not None else _pinned(pack)
    if want is not None:
        kind, expected_hex = want
        actual = sha256 if kind == "sha256" else md5
        if actual != expected_hex.lower():
            raise PackError(
                f"{pack.label} does not match its published checksum ({actual[:12]}… instead "
                f"of {expected_hex[:12]}…), so Yu'lon did not use it. {advice}"
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
        or text.split(".")[0].strip().upper() in _WINDOWS_DEVICES
        or any(ch in _VERSION_FORBIDDEN or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)
    ):
        raise PackError(
            f"{pack.label}: the version published at {where} is not a version Yu'lon can use "
            f"({raw[:40]!r}). Try again later; if it stays, tell the server's makers."
        )
    return text


# --- From the server's own checkout --------------------------------------------------------


def _numbered_parts(path: Path) -> list[tuple[int, Path]]:
    """`<path>.partNN` beside `path` as `(number, file)`, in numeric order (part2 before part10)."""
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
    return [(number, part) for number, _, part in sorted(found)]


def _parts_of(path: Path) -> list[Path]:
    """`<path>.partNN` beside `path`, in order: part10 after part09, part2 before part10."""
    return [part for _, part in _numbered_parts(path)]


def _whole_parts(pack: ClientPack, path: Path) -> list[Path]:
    """The pieces in order, refused if the numbering has a duplicate or a gap.

    A set of pieces missing one in the middle joins into a file that fails its
    checksum with no hint why; this names the piece, before anything is joined.
    The numbering starts at 0 or 1 (what `split` tools do).
    """
    numbered = _numbered_parts(path)
    for (before, _), (number, _) in zip(numbered, numbered[1:], strict=False):
        if number == before:
            raise PackError(
                f"{pack.label}: the server's checkout has more than one piece numbered {number} "
                f"of {path.name}. {_FROM_SOURCES}"
            )
    expected = numbered[0][0] if numbered and numbered[0][0] in (0, 1) else 0
    for index, (number, part) in enumerate(numbered):
        if number != expected + index:
            digits = len(_PART.search(part.name).group(1))  # type: ignore[union-attr]
            missing = f"{path.name}.part{expected + index:0{digits}d}"
            raise PackError(
                f"{pack.label}: the server's checkout is missing {missing} (it has "
                f"{part.name}), so the pieces cannot be joined. {_FROM_SOURCES}"
            )
    return [part for _, part in numbered]


def _checkout_version(pack: ClientPack, path: Path) -> str | None:
    """`<stem>.version` beside the zip (Centurion: `patch-Y.version`, `1.00148`), if any."""
    beside = path.with_name(PurePosixPath(path.name).stem + ".version")
    if not beside.is_file():
        return None
    with beside.open("rb") as handle:
        raw = handle.read(VERSION_MAX_BYTES + 1)
    return _checked_version(pack, raw, str(beside))


MD5_FILE_MAX_BYTES = 1 << 20
"""How much of a checksum file is read. Centurion's `patches.md5` is 399 bytes."""

_MD5_LINE = re.compile(r"^([0-9A-Fa-f]{32}) [ *](.+)$")
"""One `md5sum` line: the digest, a space, then ` ` (text mode) or `*` (binary), then the name."""


def _expected_from_checkout(pack: ClientPack, server_dir: Path) -> Expected:
    """The md5 `pack.md5_file` gives for the pack's zip, at the commit the checkout is on.

    The file is `md5sum` output (Centurion's `centurion/patches/patches.md5`,
    checked by its own `join.sh` with `md5sum -c`), so its names are relative to
    its own folder: the pack's zip is looked up by its path relative to that
    folder, exactly, `./` and Windows separators aside. Refused -- naming the
    file, and pointing at the Server build menu -- when the file is missing, has
    no line for the zip, or has two lines for it that disagree: with no
    checksum to hold the zip to, a join missing a piece would go unnoticed.
    """
    assert pack.md5_file is not None and pack.source.path is not None
    file = server_dir / pack.md5_file
    try:
        with file.open("rb") as handle:
            raw = handle.read(MD5_FILE_MAX_BYTES + 1)
    except FileNotFoundError:
        raise PackError(
            f"{pack.label}: this server's checkout has no {pack.md5_file} at the commit it is "
            "on, and that file holds the checksum Yu'lon checks the pack against, so it was not "
            f"used. {_FROM_SOURCES}"
        ) from None
    if len(raw) > MD5_FILE_MAX_BYTES:
        raise PackError(
            f"{pack.label}: {pack.md5_file} in this server's checkout is larger than a checksum "
            f"list can be, so Yu'lon did not read it. {_FROM_SOURCES}"
        )
    name = PurePosixPath(pack.source.path).relative_to(PurePosixPath(pack.md5_file).parent)
    found: set[str] = set()
    for line in raw.decode("utf-8-sig", errors="replace").splitlines():
        match = _MD5_LINE.match(line.strip())
        if match is None:
            continue
        listed = match.group(2).replace("\\", "/")
        if listed.startswith("./"):
            listed = listed[2:]
        if listed == name.as_posix():
            found.add(match.group(1).lower())
    if not found:
        raise PackError(
            f"{pack.label}: {pack.md5_file} in this server's checkout has no line for "
            f"{name.as_posix()}, so Yu'lon has no checksum to check the pack against and did "
            f"not use it. {_FROM_SOURCES}"
        )
    if len(found) > 1:
        raise PackError(
            f"{pack.label}: {pack.md5_file} in this server's checkout names {name.as_posix()} "
            f"more than once, with different checksums, so Yu'lon did not use it. {_FROM_SOURCES}"
        )
    return ("md5", found.pop())


def _expected_checkout(pack: ClientPack, server_dir: Path) -> Expected:
    """A checkout pack's checksum: pinned in the catalog, or read from its `md5_file`."""
    if pack.md5_file is not None:
        return _expected_from_checkout(pack, server_dir)
    pinned = _pinned(pack)
    assert pinned is not None  # catalog validation: a checkout pack always carries a checksum
    return pinned


def checkout_checksum(pack: ClientPack, server_dir: Path) -> str:
    """The hex checksum a checkout pack's zip must have in `server_dir`'s checkout, as it is now.

    For whatever needs to notice a changed pack without fetching it (the map
    data's evidence names the required packs by checksum): with `md5_file`
    the catalog alone no longer says, the checkout does. Raises `PackError`.
    """
    try:
        return _expected_checkout(pack, server_dir)[1]
    except OSError as exc:
        raise _os_refusal(pack, exc) from exc


def _fetch_checkout(
    pack: ClientPack, server_dir: Path, cancelled: Callable[[], bool] = _never
) -> Fetched:
    """A checkout pack's zip, proved against its checksum.

    The plain file when the checkout has it, verified where it is (and never
    deleted: it is the server's own file). Otherwise its `.partNN` pieces
    joined in order into `cache_dir()/checkout/<checksum>/` through a
    `.joining` file that is renamed only once the join matches the checksum;
    a join that does not match is removed. A joined file already in the cache
    is proved again before it is used, and joined afresh if it fails.

    The zip (or its pieces) is looked for BEFORE its checksum is resolved (T179
    Task 7 review): a checkout without the zip is named as missing the zip, not
    as an `md5_file` with no line for it.
    """
    source = pack.source
    if source.kind != "checkout" or source.path is None:
        raise PackError(f"{pack.label} does not come from the server's checkout.")
    path = server_dir / source.path
    plain = path.is_file()
    parts = [] if plain else _whole_parts(pack, path)
    if not plain and not parts:
        raise PackError(
            f"{pack.label}: this server's checkout has no {source.path} (nor its .partNN "
            "pieces) at the commit it is on, so Yu'lon cannot make its client. Update the "
            "server, or return it to the tested pin, and try again."
        )
    expected = _expected_checkout(pack, server_dir)
    version = _checkout_version(pack, path)
    if plain:
        try:
            return Fetched(
                path,
                version,
                _prove(pack, path, _FROM_SOURCES, expected=expected, cancelled=cancelled),
            )
        except Cancelled:
            raise
        except PackError as exc:
            raise PackError(f"{exc} The file is {path}.") from exc
    folder = cache_dir() / "checkout" / expected[1]
    dest = folder / path.name
    if dest.is_file():
        try:
            return Fetched(
                dest, version, _prove(pack, dest, expected=expected, cancelled=cancelled)
            )
        except Cancelled:
            raise
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
                    _copy(piece, out, pack, cancelled)
        sha256 = _prove(pack, joining, _FROM_SOURCES, expected=expected, cancelled=cancelled)
        os.replace(joining, dest)
    except BaseException:
        joining.unlink(missing_ok=True)
        raise
    logger.info(f"client-packs: joined {len(parts)} parts of {source.path} into {dest}")
    return Fetched(dest, version, sha256)


def fetch_checkout(
    pack: ClientPack, server_dir: Path, *, cancelled: Callable[[], bool] = _never
) -> Fetched:
    """`_fetch_checkout`, with an operating-system failure worded as a `PackError`.

    `cancelled` is asked between chunks of the proof and the join; once it answers
    True this raises `Cancelled` (T303). A join cut short leaves no `.joining` file.
    """
    try:
        return _fetch_checkout(pack, server_dir, cancelled)
    except OSError as exc:
        raise _os_refusal(pack, exc) from exc


# --- From the server's site ----------------------------------------------------------------


def _refuse_unlisted(pack: ClientPack, url: str, allowed_hosts: frozenset[str]) -> None:
    host = urllib.parse.urlsplit(url).hostname
    if host is None or host not in allowed_hosts or not _can_only_go_where_it_says(url, host):
        raise PackError(
            f"{pack.label} would be fetched from {host or url!r}, which is not a plain https "
            "address on a host this server's catalog entry names, so Yu'lon did not ask it."
        )


_GONE = frozenset({404, 410})


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
        raise (PackUnavailable if exc.code in _GONE else PackError)(
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
        raise (PackUnavailable if response.status in _GONE else PackError)(
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
    """`_fetch_url`, with an operating-system failure worded as a `PackError`.

    A write that fails (a full disk) keeps the `.part`, so pressing Play again resumes.
    """
    try:
        return _fetch_url(
            pack,
            entry_id=entry_id,
            allowed_hosts=allowed_hosts,
            opener=opener,
            progress=progress,
            cancelled=cancelled,
        )
    except OSError as exc:
        raise _os_refusal(pack, exc) from exc


def _fetch_url(
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


# --- Installing, recording and removing packs in a ready-to-play client ------------------------
#
# The next steps work on a folder step (a) made (`play_client`, its marker), never on the player's
# own client: every function below is gated on that marker and on the folder being this game's
# and this server's, and writes only through a temporary name that is renamed into place, so a
# name that is a hard link to the player's own file is replaced (the link dropped) and never
# written through.

RECORD = ".yulon-client-packs.json"
"""The file that records what Yu'lon installed: per pack, its version and each file's SHA-256."""

_STAGING = ".yulon-pack-tmp"
_ASIDE = ".yulon-pack-old"
"""A target's previous file, kept beside it until every file of a pack is in place."""
_STRAY = re.compile(r"\.yulon-pack-(?:tmp|old)(?:\.\d+)?$", re.IGNORECASE)
"""A name Yu'lon's own swap may use, with the number a fresh aside name takes."""
_PROTECTED = ".yulon-"
"""A file name starting so is Yu'lon's own (the marker, this record): no zip may write one."""
_RECORD_VERSION = 1
_KEEP_DEPTH = 3
"""A folder a removal leaves behind empty is removed only from this many levels down
(`Interface/AddOns/<addon>`): `Data`, `Interface/AddOns` and the like are the client's own."""


@dataclass(frozen=True)
class PackRecord:
    """The record file's content.

    `packs` maps a pack id to `{"version", "sha256", "files": {relative path: sha256}}`;
    `exe` is the Wow.exe patch record (a later step's); `choices` is the player's picks,
    `{"packs": {id: bool}, "exe_options": {name: bool}}`; `config_seeded` is whether Play has
    merged Config.wtf into this client once (the seed keys are written on that first merge only);
    `launcher` is the per-server launcher window's picks (T187), see `clean_launcher`.
    """

    packs: dict[str, dict[str, Any]]
    exe: dict[str, Any] | None
    choices: dict[str, Any]
    config_seeded: bool = False
    launcher: dict[str, Any] = field(default_factory=dict)


WINDOW_MODES = ("fullscreen", "windowed", "maximized", "borderless")
_RESOLUTION = re.compile(r"[1-9][0-9]{2,4}x[1-9][0-9]{2,4}")
# Letters, digits, dots, dashes and colons, with at least one letter or digit and
# no dot at either end: "10.0." or "." is an address half typed, not one the game
# can use (T187 final review).
_ADDRESS = re.compile(r"(?!\.)(?=.*[A-Za-z0-9])[A-Za-z0-9._:\-]{1,253}(?<!\.)")
ACCOUNT_MAX = 32


def clean_account(value: object) -> str | None:
    """An account name WoW can hold in Config.wtf (upper-cased), or None when it cannot.

    Never a quote, a control character (a CR or LF would start a new `SET` line) or
    more than 32 characters.
    """
    if not isinstance(value, str):
        return None
    name = value.strip().upper()
    if not name or len(name) > ACCOUNT_MAX:
        return None
    if '"' in name or "\\" in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        return None
    return name


def clean_launcher(raw: object) -> dict[str, Any]:
    """The launcher picks in `raw`, keeping only well-formed known keys.

    `{"display": {"window": one of WINDOW_MODES, "resolution": "1920x1080"},
    "account": "NAME" | None, "realm_address": "127.0.0.1"}`; anything else is dropped.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    display = raw.get("display")
    if isinstance(display, dict):
        shown: dict[str, str] = {}
        window = display.get("window")
        if isinstance(window, str) and window in WINDOW_MODES:
            shown["window"] = window
        resolution = display.get("resolution")
        if isinstance(resolution, str) and _RESOLUTION.fullmatch(resolution):
            shown["resolution"] = resolution
        if shown:
            out["display"] = shown
    if "account" in raw:
        account = clean_account(raw["account"])
        if account is not None or raw["account"] is None:
            out["account"] = account
    address = raw.get("realm_address")
    if isinstance(address, str) and _ADDRESS.fullmatch(address):
        out["realm_address"] = address
    elif "realm_address" in raw and address is None:
        # "Use this computer" said so (T187 fix 1): Play takes a typed address back out.
        out["realm_address"] = None
    return out


def launcher_config_keys(
    launcher: Mapping[str, Any],
    *,
    catalog_always: Mapping[str, str],
    default_address: str | None = None,
) -> dict[str, str]:
    """The Config.wtf keys the launcher picks set; a key the catalog's `always` sets is dropped.

    Display: `gxWindow`/`gxMaximize`/`gxResolution`; the account: `accountName`.
    A picks dict that is not well formed contributes nothing for the bad part.

    The one exception (lead ruling, T187): a typed realm address sets `realmList`
    and `patchList` (Centurion's own launcher writes both from one address) even
    where the catalog's `always` sets them, so the caller lets these keys win.

    `default_address` is for a client whose Config.wtf is the only place the game
    finds its address (no realmlist.wtf, no catalog `realmList`): "Use this
    computer" (`realm_address` saved as None) then writes it there, because
    taking the typed lines out would leave the game with no address, and
    leaving them would send it to the old one (T187 final review).
    """
    picks = clean_launcher(launcher)
    keys: dict[str, str] = {}
    display = picks.get("display", {})
    window = display.get("window")
    if window == "fullscreen":
        keys["gxWindow"] = "0"
    elif window == "windowed":
        keys.update(gxWindow="1", gxMaximize="0")
    elif window in ("maximized", "borderless"):
        keys.update(gxWindow="1", gxMaximize="1")
    if "resolution" in display:
        keys["gxResolution"] = display["resolution"]
    if picks.get("account"):
        keys["accountName"] = picks["account"]
    fixed = {key.casefold() for key in catalog_always}
    keys = {k: v for k, v in keys.items() if k.casefold() not in fixed}
    address = picks.get("realm_address")
    if address:
        keys.update(realmList=address, patchList=address)
    elif "realm_address" in picks and default_address:
        keys.update(realmList=default_address, patchList=default_address)
    return keys


def launcher_config_removals(
    launcher: Mapping[str, Any],
    *,
    catalog_always: Mapping[str, str],
    default_address_written: bool = False,
) -> tuple[str, ...]:
    """The Config.wtf keys the launcher's picks take out.

    * `accountName` for "Ask in the game": only an `account` saved as None says
      so; no `account` at all leaves the file as it is.
    * `realmList` and `patchList` for "Use this computer" (`realm_address` saved
      as None, T187 fix 1), when `default_address_written`: this computer's
      address reaches the game another way -- the realmlist.wtf Play writes, or
      the catalog's own `realmList` -- so the lines a typed address put in
      earlier would only send the game to the old address.

    A key the catalog's `always` sets is the catalog's, and is never taken out.
    """
    picks = clean_launcher(launcher)
    fixed = {key.casefold() for key in catalog_always}
    out: list[str] = []
    if "account" in picks and picks["account"] is None and "accountname" not in fixed:
        out.append("accountName")
    if default_address_written and "realm_address" in picks and picks["realm_address"] is None:
        out += [key for key in ("realmList", "patchList") if key.casefold() not in fixed]
    return tuple(out)


def launcher_exe_options(
    launcher: Mapping[str, Any],
    chosen: Mapping[str, bool],
    patch_options: Collection[str],
    *,
    catalog_always: Mapping[str, str],
) -> dict[str, bool]:
    """`chosen` exe options with `borderless` following the launcher's window pick.

    Only where the catalog's exe patch has a `borderless` option and the launcher has
    picked a window mode: borderless is on for "borderless" and off for any other mode.
    Not where the catalog's `always` sets `gxWindow` or `gxMaximize`: the server
    decides the window then, `launcher_config_keys` drops the pick, and the pick
    says nothing about borderless either (T187 final review).
    """
    window = clean_launcher(launcher).get("display", {}).get("window")
    out = dict(chosen)
    fixed = {key.casefold() for key in catalog_always}
    if fixed & {"gxwindow", "gxmaximize"}:
        return out
    if window is not None and "borderless" in patch_options:
        out["borderless"] = window == "borderless"
    return out


def launcher_following_borderless(launcher: Mapping[str, Any], borderless: bool) -> dict[str, Any]:
    """The launcher picks with the window mode brought in step with a `borderless` exe choice.

    The other half of `launcher_exe_options` (T187): "Client options…" ticking
    Borderless makes a saved window pick "borderless", and unticking it makes a
    saved "borderless" pick "windowed" -- else the next Play would put back what
    the player just changed, because the window pick decides that exe option.
    No window pick saved leaves the picks as they are: the exe option alone
    decides then.
    """
    picks = clean_launcher(launcher)
    display = dict(picks.get("display", {}))
    window = display.get("window")
    if window is None:
        return picks
    if borderless and window != "borderless":
        display["window"] = "borderless"
    elif not borderless and window == "borderless":
        display["window"] = "windowed"
    return {**picks, "display": display}


def _empty_record() -> PackRecord:
    return PackRecord(packs={}, exe=None, choices={"packs": {}, "exe_options": {}})


def _bool_map(raw: object) -> dict[str, bool]:
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, bool)}


def _entry_or_none(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    files = raw.get("files")
    if not isinstance(files, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in files.items()
    ):
        return None
    return dict(raw)


def read_record(play_dir: Path) -> PackRecord:
    """The folder's record; an absent, unreadable or malformed one reads as empty.

    Anything in it that is not the shape `write_record` writes is dropped rather
    than trusted: what names a file to delete later must be well formed. The
    NAMES are checked again where they are used (`_clean_rel`).
    """
    try:
        raw = json.loads((play_dir / RECORD).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return _empty_record()
    if not isinstance(raw, dict):
        return _empty_record()
    packs: dict[str, dict[str, Any]] = {}
    raw_packs = raw.get("packs")
    if isinstance(raw_packs, dict):
        for pack_id, raw_entry in raw_packs.items():
            entry = _entry_or_none(raw_entry)
            if isinstance(pack_id, str) and entry is not None:
                packs[pack_id] = entry
    exe = raw.get("exe")
    raw_choices = raw.get("choices")
    choices = raw_choices if isinstance(raw_choices, dict) else {}
    return PackRecord(
        packs=packs,
        exe=dict(exe) if isinstance(exe, dict) else None,
        choices={
            "packs": _bool_map(choices.get("packs")),
            "exe_options": _bool_map(choices.get("exe_options")),
        },
        config_seeded=raw.get("config_seeded") is True,
        launcher=clean_launcher(raw.get("launcher")),
    )


def _clean_rel(value: object) -> PurePosixPath | None:
    """A relative path that stays inside the client folder, as a POSIX path; else None."""
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    if "\\" in value or ":" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.as_posix() != value:
        return None
    if any(part.endswith((".", " ")) for part in path.parts):
        return None  # Windows reads `Wow.exe.` as `Wow.exe`
    return path


def pack_files(play_dir: Path) -> frozenset[Path]:
    """Every file the record says a pack installed, relative to `play_dir`.

    Step (a) treats these like a module's files: never stale, never refreshed,
    never named as left out. A name that could leave the folder is not listed.
    """
    found: set[Path] = set()
    for entry in read_record(play_dir).packs.values():
        for rel in entry["files"]:
            clean = _clean_rel(rel)
            if clean is not None:
                found.add(Path(*clean.parts))
    return frozenset(found)


def wanted(client: Any, choices: Mapping[str, Any]) -> tuple[ClientPack, ...]:
    """Every required pack, and each optional one switched on (its `default` where unchosen)."""
    chosen = _bool_map(choices.get("packs"))
    return tuple(
        pack for pack in client.packs if not pack.optional or chosen.get(pack.id, pack.default)
    )


def _gate(play_dir: Path, *, game: str, server_dir: Path, what: str) -> None:
    """Refuse a folder that is a link, has no step (a) marker, or is another game's or server's."""
    if play_client._is_link(play_dir):
        raise PackError(
            f"{play_dir} is a link to another folder, so {what} was left as it was. Make the "
            "ready-to-play client again from the server's Client settings."
        )
    marker = play_client.read_marker(play_dir)
    if marker is None:
        raise PackError(
            f"{play_dir} is not a ready-to-play client made by Yu'lon (it has no "
            f"{play_client.MARKER} marker), so {what} was left as it was. Make the "
            "ready-to-play client again from the server's Client settings."
        )
    whose = play_client._whose(marker, game=game, server_dir=server_dir)
    if whose is not None:
        raise PackError(
            f"{play_dir} is the ready-to-play client of {whose}, so {what} was left as it was. "
            "Use that server's own Client settings instead."
        )


def _write_refusal(what: str, exc: OSError) -> PackError:
    where = exc.filename or "the ready-to-play client"
    if isinstance(exc, PermissionError):
        return PackError(
            f"{what}: {where} was refused ({exc.strerror or exc}), which usually means a "
            "program has the file open. Your own WoW client was not changed. Close World of "
            "Warcraft (and any program using the client's files), then press Play again."
        )
    return PackError(
        f"{what}: Yu'lon could not write {where} ({exc.strerror or exc}). Your own WoW client "
        "was not changed. Free some space and check that Yu'lon may write there, then press "
        "Play again."
    )


def write_record(
    play_dir: Path,
    record: PackRecord,
    *,
    game: str,
    server_dir: Path,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Write `record` into `play_dir` through a new temporary file renamed into place.

    Only into a folder whose step (a) marker names `game` and `server_dir`
    (`PackError` otherwise, nothing written). The temporary name is created new
    (`xb`) after any leftover of that name is removed, so a leftover that is a
    link is never written through.
    """
    _gate(play_dir, game=game, server_dir=server_dir, what="its record of installed packs")
    payload = {
        "version": _RECORD_VERSION,
        "packs": {
            pack_id: {k: v for k, v in entry.items() if k != "left_behind"}
            for pack_id, entry in record.packs.items()
        },
        "exe": record.exe,
        "choices": record.choices,
        "config_seeded": record.config_seeded,
        "launcher": clean_launcher(record.launcher),
    }
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    target = play_dir / RECORD
    tmp = target.with_name(f"{target.name}.{os.getpid()}.yulon-tmp")
    try:
        tmp.unlink(missing_ok=True)
        with open(tmp, "xb") as handle:
            handle.write(data)
        play_client._retrying(lambda: os.replace(tmp, target), sleep=sleep)
    except OSError as exc:
        _unlink_quietly(tmp)
        raise _write_refusal("The record of installed packs", exc) from exc
    except BaseException:
        _unlink_quietly(tmp)
        raise


def _unlink_quietly(path: Path) -> None:
    """Remove a temporary file while another error is on its way out; log, never raise."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("client-packs: could not remove %s", path, exc_info=True)


# --- Install ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Item:
    info: zipfile.ZipInfo
    rel: PurePosixPath


def _unsafe_member(pack: ClientPack, name: str) -> PackError:
    return PackError(
        f"{pack.label}: {name!r} in the zip would leave the client folder, so Yu'lon "
        "unpacked nothing from it. The server's makers have a damaged or unsafe pack; tell them."
    )


def _is_symlink_member(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _plan(pack: ClientPack, archive: zipfile.ZipFile) -> list[_Item]:
    """Which member goes where, refused whole if any one is unsafe. Writes nothing."""
    files = [info for info in archive.infolist() if not info.is_dir()]
    by_name = {info.filename: info for info in files}
    items: list[_Item] = []
    for rule in pack.install:
        if rule.member == "*":
            assert rule.to_dir is not None  # catalog validation
            for info in files:
                items.append(_Item(info, _target(pack, rule.to_dir, info.filename)))
            continue
        assert rule.to is not None  # catalog validation
        found = by_name.get(rule.member)
        if found is None:
            folded = [i for i in files if i.filename.casefold() == rule.member.casefold()]
            found = folded[0] if len(folded) == 1 else None
        if found is None:
            raise PackError(
                f"{pack.label} has no {rule.member} in it, so Yu'lon cannot install it. The "
                "server's makers changed the pack; press "
                f"{server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)} "
                "to fetch it again, and if it stays, tell them."
            )
        items.append(_Item(found, _target(pack, None, found.filename, to=rule.to)))
    seen: dict[str, str] = {}
    for item in items:
        if _is_symlink_member(item.info):
            raise PackError(
                f"{pack.label}: {item.info.filename!r} is a link, not a file, so Yu'lon "
                "unpacked nothing from the zip."
            )
        key = item.rel.as_posix().casefold()
        if key in seen:
            raise PackError(
                f"{pack.label}: {item.info.filename!r} and {seen[key]!r} would both be "
                f"installed as {item.rel.as_posix()}, so Yu'lon unpacked nothing from it."
            )
        seen[key] = item.info.filename
    return items


def _target(
    pack: ClientPack, to_dir: str | None, member: str, *, to: str | None = None
) -> PurePosixPath:
    """Where one member lands, or a refusal for a member name that could go elsewhere."""
    if _clean_rel(member) is None:
        raise _unsafe_member(pack, member)
    rel = PurePosixPath(to) if to is not None else PurePosixPath(to_dir or ".") / member
    if _clean_rel(rel.as_posix()) is None:
        raise _unsafe_member(pack, member)
    if rel.name.startswith(_PROTECTED):
        raise PackError(
            f"{pack.label}: {member!r} would replace {rel.name}, one of Yu'lon's own files, so "
            "Yu'lon unpacked nothing from the zip."
        )
    if _STRAY.search(rel.name):
        raise PackError(
            f"{pack.label}: {member!r} would be mistaken for one of Yu'lon's own temporary "
            "files, so Yu'lon unpacked nothing from the zip."
        )
    return rel


def _check_path(play_dir: Path, rel: PurePosixPath) -> None:
    """Refuse a target whose folder (or itself) is a link or is in the way as the wrong kind."""
    here = play_dir
    for part in rel.parts[:-1]:
        here = here / part
        if play_client._is_link(here):
            raise PackError(
                f"{here} is a link to another folder, so nothing was installed into it: "
                "writing there could change your own WoW client. Make the ready-to-play "
                "client again from the server's Client settings."
            )
        if os.path.lexists(here) and not here.is_dir():
            raise PackError(f"{here} is a file where a folder is needed, so nothing was installed.")
    target = play_dir / Path(*rel.parts)
    if not play_client._is_link(target) and target.is_dir():
        raise PackError(f"{target} is a folder where a file is needed, so nothing was installed.")


def _make_dirs(folder: Path, stop: Path) -> list[Path]:
    """Create `folder` (and parents below `stop`); the ones created, outermost first."""
    missing: list[Path] = []
    here = folder
    while here != stop and not os.path.lexists(here):
        missing.append(here)
        here = here.parent
    for path in reversed(missing):
        path.mkdir()
    return list(reversed(missing))


class _Tee:
    """A write target that also hashes what passes through."""

    def __init__(self, out: Any) -> None:
        self.out = out
        self.sha256 = hashlib.sha256()

    def write(self, data: bytes) -> int:
        self.sha256.update(data)
        return int(self.out.write(data))


def _extracted_prefix(pack: ClientPack, server_dir: Path) -> str:
    """Names this server's copies of this pack: the server (by its folder) and the pack."""
    owner = hashlib.sha256(os.fspath(server_dir).encode("utf-8", "replace")).hexdigest()[:10]
    return f"{owner}~{pack.id}~"


def _extracted_dir(pack: ClientPack, fetched: Fetched, server_dir: Path) -> Path:
    return cache_dir() / "extracted" / f"{_extracted_prefix(pack, server_dir)}{fetched.sha256[:16]}"


def _extracted_copy(
    pack: ClientPack,
    fetched: Fetched,
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    server_dir: Path,
    cancelled: Callable[[], bool] = _never,
) -> tuple[Path, str]:
    """An `.MPQ` member extracted once into the cache, with its SHA-256.

    A copy already there is used when its size and CRC-32 are the member's own.
    Never rewritten in place: a client's file may be a hard link to it, so a new
    copy is written beside it and renamed over it.
    """
    folder = _extracted_dir(pack, fetched, server_dir)
    leaf = re.sub(r"[^A-Za-z0-9._-]", "_", PurePosixPath(info.filename).name)
    dest = folder / f"{info.CRC:08x}-{info.file_size}-{leaf}"
    if dest.is_file() and not play_client._is_link(dest) and dest.stat().st_size == info.file_size:
        sha256, crc = hashlib.sha256(), 0
        with dest.open("rb") as handle:
            while chunk := handle.read(CHUNK_BYTES):
                _stop_if_asked(pack, cancelled)
                sha256.update(chunk)
                crc = zlib.crc32(chunk, crc)
        if crc == info.CRC:
            return dest, sha256.hexdigest()
    folder.mkdir(parents=True, exist_ok=True)
    _refuse_without_room(pack, folder, info.file_size)
    part = dest.with_name(dest.name + ".part")
    part.unlink(missing_ok=True)
    try:
        with archive.open(info) as source, open(part, "xb") as out:
            tee = _Tee(out)
            _copy(source, tee, pack, cancelled)
        os.replace(part, dest)
    except BaseException:
        _unlink_quietly(part)
        raise
    return dest, tee.sha256.hexdigest()


def _stage(
    pack: ClientPack,
    fetched: Fetched,
    archive: zipfile.ZipFile,
    item: _Item,
    play_dir: Path,
    server_dir: Path,
    cancelled: Callable[[], bool] = _never,
) -> tuple[Path, Path, str]:
    """Make `item`'s file under a temporary name beside its target; `(tmp, target, sha256)`."""
    target = play_dir / Path(*item.rel.parts)
    tmp = target.with_name(target.name + _STAGING)
    tmp.unlink(missing_ok=True)
    if item.rel.suffix.lower() == ".mpq":
        cached, sha256 = _extracted_copy(pack, fetched, archive, item.info, server_dir, cancelled)
        try:
            os.link(cached, tmp)
        except OSError as exc:
            if exc.errno != errno.EXDEV and not play_client._cannot_link(exc):
                raise
            try:
                with cached.open("rb") as source, open(tmp, "xb") as out:
                    _copy(source, out, pack, cancelled)
            except BaseException:
                _unlink_quietly(tmp)
                raise
        return tmp, target, sha256
    try:
        with archive.open(item.info) as source, open(tmp, "xb") as out:
            tee = _Tee(out)
            _copy(source, tee, pack, cancelled)
    except BaseException:
        _unlink_quietly(tmp)
        raise
    return tmp, target, tee.sha256.hexdigest()


def install(
    play_dir: Path,
    pack: ClientPack,
    fetched: Fetched,
    *,
    game: str,
    server_dir: Path,
    previous: Mapping[str, Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    cancelled: Callable[[], bool] = _never,
) -> dict[str, Any]:
    """Unpack `pack`'s mapped members into `play_dir`; the record entry for what was written.

    Only into a folder whose marker names `game` and `server_dir`. Everything is
    checked first (member names, symlink members, links in the way, free space)
    and every file is made under a temporary name before any is renamed into
    place, so a refusal or a damaged member leaves the client as it was. The swap
    is all or nothing: each existing target is first renamed aside
    (`<name>.yulon-pack-old`), then the new file is renamed in; if any step fails
    every target is put back from its aside name and the temporaries are removed,
    and only after every target succeeded are the aside files deleted. A name that
    is a hard link to the player's own file is moved aside and replaced, never
    written through. `*.MPQ` members are extracted once into the cache and
    hard-linked from there where the volume allows, else copied. Once the pack is
    installed, older cached versions of it are removed (`prune_cache`).

    `previous` is this pack's record entry from the install being replaced. Only
    after every rename succeeded, the files it lists that the new version does not
    install are removed (a name that case-folds onto a new one is never removed, so
    a case-only rename keeps its file), each only if it still has the recorded
    hash, and folders that leaves empty go too. `remove_when_off` is not applied.
    A dropped file the player edited is kept and named in the returned entry's
    `"left_behind"` (absent when there is none).

    `cancelled` is asked between chunks while the files are made, before any is
    renamed in; once it answers True this raises `Cancelled` and the folder is as
    it was (T303).

    The caller records the returned entry (`write_record`). Raises only
    `PackError`; `PartialInstall` (a subclass) when the rollback itself failed.
    """
    try:
        return _install(
            play_dir,
            pack,
            fetched,
            game=game,
            server_dir=server_dir,
            previous=previous,
            sleep=sleep,
            cancelled=cancelled,
        )
    except OSError as exc:
        raise _write_refusal(pack.label, exc) from exc
    except (
        zipfile.BadZipFile,
        EOFError,
        zlib.error,
        NotImplementedError,
        RuntimeError,
        ValueError,
    ) as exc:
        if isinstance(exc, PackError):
            raise
        raise PackError(
            f"{pack.label} arrived damaged or in a form Yu'lon cannot read ({exc}), so nothing "
            "from it was installed. Try again."
        ) from exc


class PartialInstall(PackError):
    """A swap failed and putting the old files back failed too.

    `entry` is a record entry (`version` None, so the next Play installs the pack
    again) naming the files that now hold the new bytes, so the caller can record
    it and a later switch-off still finds every file. Files that were put back,
    or never touched, are not in it.
    """

    def __init__(self, message: str, entry: dict[str, Any]) -> None:
        super().__init__(message)
        self.entry = entry


def _remove_aside(path: Path) -> bool:
    """Delete a swap's aside or stray file; False (logged) if it cannot go.

    `client_config._remove_own`'s rule: a read-only file with one name is made
    writable first, one with more names (a hard link to the player's own file) is
    never chmodded and is left where it is.
    """
    try:
        client_config._remove_own(path)
    except FileNotFoundError:
        return True
    except OSError:
        logger.warning("client-packs: could not remove %s, leaving it", path, exc_info=True)
        return False
    return True


def _fresh_aside(target: Path) -> Path:
    """A name beside `target` to move its old file to, that nothing is at.

    The plain `.yulon-pack-old` when it is free or its leftover can be removed;
    otherwise `.yulon-pack-old.<n>`, so a swap never depends on deleting a leftover
    a read-only flag (Windows) or a sharing violation keeps in place.
    """
    base = target.with_name(target.name + _ASIDE)
    if os.path.lexists(base) and not _remove_aside(base):
        number = 1
        while os.path.lexists(base.with_name(f"{base.name}.{number}")):
            number += 1
        return base.with_name(f"{base.name}.{number}")
    return base


def _strays(target: Path) -> list[Path]:
    """Aside files of `target` a crashed install left, plain name first."""
    stem = target.name + _ASIDE
    found = [
        sibling
        for sibling in target.parent.iterdir()
        if sibling.name == stem or re.fullmatch(re.escape(stem) + r"\.\d+", sibling.name)
    ]
    return sorted(found, key=lambda p: (len(p.name), p.name))


def _recover_asides(targets: list[Path], sleep: Callable[[float], None]) -> None:
    """Before staging: an install that crashed mid-swap left asides; settle them.

    A target that is MISSING gets its first aside put back (it was the file before
    the crash); any other aside is deleted as `_remove_aside` can.
    """
    for target in targets:
        if not target.parent.is_dir() or play_client._is_link(target.parent):
            continue
        strays = _strays(target)
        if strays and not os.path.lexists(target):
            play_client._retrying(partial(os.replace, strays[0], target), sleep=sleep)
            strays = strays[1:]
        for stray in strays:
            _remove_aside(stray)


def restore_asides(
    play_dir: Path,
    rels: Collection[str],
    *,
    game: str,
    server_dir: Path,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Put back the file a stuck swap moved aside, for each of `rels` that is now missing.

    The rule an install uses before staging (`_recover_asides`): a missing target gets
    its first `.yulon-pack-old*` aside back, and any other aside is deleted. For
    a pack whose half-installed files were removed: the file it replaced (maybe the
    player's shared archive) must be there when the game starts.
    """
    _gate(play_dir, game=game, server_dir=server_dir, what="putting back a replaced file")
    targets = [_removal_path(play_dir, rel)[1] for rel in rels]
    try:
        _recover_asides(targets, sleep)
    except OSError as exc:
        raise _write_refusal("A replaced file", exc) from exc


def _swap(
    play_dir: Path,
    staged: list[tuple[Path, Path, str]],
    pack: ClientPack,
    sleep: Callable[[float], None],
) -> None:
    """Rename every staged file into place, or put everything back and raise.

    Raises the OSError that stopped the swap once the rollback succeeded, and
    `PartialInstall` when it did not.
    """
    done: list[tuple[Path, Path | None]] = []  # (target, its file moved aside, or None)
    try:
        for tmp, target, _ in staged:
            moved: Path | None = None
            if os.path.lexists(target):
                aside = _fresh_aside(target)
                play_client._retrying(partial(os.replace, target, aside), sleep=sleep)
                moved = aside
            done.append((target, moved))
            play_client._retrying(partial(os.replace, tmp, target), sleep=sleep)
    except BaseException as failure:
        stuck: list[Path] = []
        for target, moved in reversed(done):
            try:
                if moved is not None:
                    play_client._retrying(partial(os.replace, moved, target), sleep=sleep)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                logger.warning("client-packs: could not put %s back", target, exc_info=True)
                stuck.append(target)
        for tmp, _, _ in staged:
            _unlink_quietly(tmp)
        if stuck and isinstance(failure, OSError):
            now = {
                target.relative_to(play_dir).as_posix(): sha256
                for _, target, sha256 in staged
                if target in stuck and _holds(target, sha256)
            }
            raise PartialInstall(
                f"{pack.label} could only be partly installed ({failure.strerror or failure}) "
                "and Yu'lon could not put every file back. Close World of Warcraft (and any "
                "program using the client's files), then press Play again: the pack is "
                "installed again from the start.",
                {"version": None, "sha256": None, "files": now},
            ) from failure
        raise
    for _, moved in done:
        if moved is not None:
            _remove_aside(moved)


def _holds(path: Path, sha256: str) -> bool:
    try:
        return path.is_file() and _file_sha256(path) == sha256
    except OSError:
        return False


def _install(
    play_dir: Path,
    pack: ClientPack,
    fetched: Fetched,
    *,
    game: str,
    server_dir: Path,
    previous: Mapping[str, Any] | None,
    sleep: Callable[[float], None],
    cancelled: Callable[[], bool] = _never,
) -> dict[str, Any]:
    _gate(play_dir, game=game, server_dir=server_dir, what=f"the pack {pack.label}")
    with zipfile.ZipFile(fetched.path) as archive:
        # Onto the name already in the folder, whatever its case (T227): a
        # `patch-X.MPQ` written beside the player's `patch-x.mpq` would be a
        # second archive that differs only in case, and the game would load
        # one of the two.
        items = [
            _Item(item.info, client_names.on_disk(play_dir, item.rel))
            for item in _plan(pack, archive)
        ]
        for item in items:
            _check_path(play_dir, item.rel)
        _recover_asides([play_dir / Path(*item.rel.parts) for item in items], sleep)
        plain = sum(i.info.file_size for i in items if i.rel.suffix.lower() != ".mpq")
        if plain:
            _refuse_without_room(pack, play_dir, plain)
        new = {item.rel.as_posix().casefold() for item in items}
        drops = _drops(play_dir, previous, new)
        staged: list[tuple[Path, Path, str]] = []
        made: list[Path] = []
        try:
            for item in items:
                made += _make_dirs((play_dir / Path(*item.rel.parts)).parent, play_dir)
                staged.append(_stage(pack, fetched, archive, item, play_dir, server_dir, cancelled))
            # Once more before anything is renamed in: a Stop after the last chunk
            # (Codex review, T303) is answered here, not by a pack laid in anyway.
            _stop_if_asked(pack, cancelled)
        except BaseException:
            for tmp, _, _ in staged:
                _unlink_quietly(tmp)
            for folder in reversed(made):
                with contextlib.suppress(OSError):
                    folder.rmdir()
            raise
    _swap(play_dir, staged, pack, sleep)
    files = {t.relative_to(play_dir).as_posix(): sha256 for _, t, sha256 in staged}
    entry: dict[str, Any] = {"version": fetched.version, "sha256": fetched.sha256, "files": files}
    left = _drop_old(play_dir, drops)
    if left:
        entry["left_behind"] = left
    logger.info("client-packs: installed %s into %s (%d files)", pack.id, play_dir, len(files))
    _prune_after_install(pack, fetched, server_dir)
    return entry


def _drops(
    play_dir: Path, previous: Mapping[str, Any] | None, new: set[str]
) -> list[tuple[str, Path, str]]:
    """What the previous install wrote that the new one does not: `(rel, path, sha256)`.

    Checked BEFORE anything is written, so a record naming a path outside the folder
    refuses the install instead of surprising it halfway.
    """
    files = previous.get("files") if previous else None
    if not isinstance(files, dict):
        return []
    found: list[tuple[str, Path, str]] = []
    for rel, sha in files.items():
        checked, path = _removal_path(play_dir, rel)
        if checked.casefold() not in new:
            found.append((checked, path, str(sha)))
    return found


def _drop_old(play_dir: Path, drops: list[tuple[str, Path, str]]) -> list[str]:
    """Remove the dropped files that still match their record; the paths left, sorted."""
    left: list[str] = []
    removed: list[Path] = []
    for rel, path, sha in drops:
        try:
            st = path.lstat()
            if stat.S_ISLNK(st.st_mode) or stat.S_ISDIR(st.st_mode) or _file_sha256(path) != sha:
                left.append(rel)
                continue
            client_config._remove_own(path)
        except FileNotFoundError:
            continue
        except OSError:
            logger.warning("client-packs: could not remove old %s", path, exc_info=True)
            left.append(rel)
            continue
        removed.append(path)
    for path in removed:
        _remove_empty_parents(play_dir, path)
    return sorted(left)


# --- The cache of old versions ---------------------------------------------------------------


def _remove_tree(path: Path) -> None:
    """Remove a cache entry: a link's own name only, a folder with what is in it. Best effort."""
    try:
        if play_client._is_link(path) or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
    except OSError:
        logger.warning("client-packs: could not remove old cache entry %s", path, exc_info=True)


def prune_cache(entry_id: str, pack_id: str, keep_version: str) -> None:
    """Remove every cached version of one pack but `keep_version`, and orphaned `.part` files.

    Looks only inside `<cache>/<entry>/<pack>/`, only at names that are one plain
    folder name, and never follows a link out of the cache. The kept version's own
    `.part` (a download in progress) stays. Best effort: a failure is logged, since
    a cache that could not be tidied is no reason to refuse Play.
    """
    if not all(_SAFE_NAME.match(name) for name in (entry_id, pack_id, keep_version)):
        return
    folder = cache_dir() / entry_id / pack_id
    try:
        children = list(folder.iterdir())
    except OSError:
        return
    for child in children:
        if child.name == keep_version:
            continue
        if play_client._is_link(child) or child.is_dir() or child.name.endswith(".part"):
            _remove_tree(child)


def _prune_after_install(pack: ClientPack, fetched: Fetched, server_dir: Path) -> None:
    """Tidy the cache after a pack installed: its older versions, and older extracted copies."""
    version = fetched.path.parent
    pack_folder, entry_folder = version.parent, version.parent.parent
    if pack_folder.name == pack.id and entry_folder.parent == cache_dir():
        prune_cache(entry_folder.name, pack.id, version.name)
    keep = _extracted_dir(pack, fetched, server_dir).name
    prefix = _extracted_prefix(pack, server_dir)
    extracted = cache_dir() / "extracted"
    try:
        for child in extracted.iterdir():
            if child.name.startswith(prefix) and child.name != keep:
                _remove_tree(child)
    except OSError:
        return


# --- Remove ----------------------------------------------------------------------------------


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _removal_path(play_dir: Path, rel: object) -> tuple[str, Path]:
    clean = _clean_rel(rel)
    if clean is None or clean.name.startswith(_PROTECTED):
        raise PackError(
            f"The record of installed packs names {rel!r}, which is outside the ready-to-play "
            "client or one of Yu'lon's own files, so nothing was removed. Make the "
            "ready-to-play client again from the server's Client settings."
        )
    # The name in the folder, whatever its case (T227): `remove_when_off` names
    # `Data/patch-Y.MPQ`, and a client named in lower case holds `Data/patch-y.mpq`.
    clean = client_names.on_disk(play_dir, clean)
    here = play_dir
    for part in clean.parts[:-1]:
        here = here / part
        if play_client._is_link(here):
            raise PackError(
                f"{here} is a link to another folder, so nothing was removed through it. "
                "Make the ready-to-play client again from the server's Client settings."
            )
    return clean.as_posix(), play_dir / Path(*clean.parts)


def remove(
    play_dir: Path,
    rec_entry: Mapping[str, Any],
    pack: ClientPack | None,
    *,
    game: str,
    server_dir: Path,
    when_off: bool = True,
) -> tuple[str, ...]:
    """Delete what a pack installed, when it is switched off or gone from the catalog.

    `when_off=False` leaves the pack's `remove_when_off` files alone: for a pack only
    skipped this once, which is not "switched off".

    A recorded file goes only if its SHA-256 still matches the record: one the
    player edited is left, and its relative path is returned so the caller can say
    so. The pack's `remove_when_off` files go whatever their content (that is what
    they are for). Only a name is ever removed: a name that is a hard link to the
    player's own file leaves the player's file as it is. Folders the pack's files
    leave empty are removed from `_KEEP_DEPTH` levels down. Nothing is removed if
    the record or the catalog names a path outside the folder, or one through a link.
    """
    _gate(play_dir, game=game, server_dir=server_dir, what=f"the removal of {pack_label(pack)}")
    try:
        left = _remove(play_dir, rec_entry, pack, when_off)
    except OSError as exc:
        raise _write_refusal(pack_label(pack), exc) from exc
    name = pack.id if pack is not None else "a pack no longer in the catalog"
    logger.info("client-packs: removed %s from %s", name, play_dir)
    return left


def pack_label(pack: ClientPack | None) -> str:
    return pack.label if pack is not None else "a pack no longer in the catalog"


def _delete_own(rel: str, path: Path) -> None:
    """`client_config._remove_own`, with its deliberate refusal worded for the player.

    It raises EPERM for a read-only file that another name shares (your own WoW
    client's file): that is not a program holding a file open, so it must not say
    to close WoW.
    """
    try:
        client_config._remove_own(path)
    except PermissionError as exc:
        if exc.errno != errno.EPERM:
            raise
        raise PackError(
            f"{rel} is read-only and shared with your own WoW client (or another "
            "ready-to-play client), so Yu'lon left it in place and removed nothing more. "
            "Delete that file from the ready-to-play client yourself if you no longer want it."
        ) from exc


def _remove(
    play_dir: Path, rec_entry: Mapping[str, Any], pack: ClientPack | None, when_off: bool = True
) -> tuple[str, ...]:
    recorded = rec_entry.get("files")
    recorded = recorded if isinstance(recorded, dict) else {}
    todo = [(*_removal_path(play_dir, rel), sha) for rel, sha in recorded.items()]
    extra = [
        _removal_path(play_dir, rel) for rel in (pack.remove_when_off if pack and when_off else ())
    ]
    left: list[str] = []
    removed: list[Path] = []
    for rel, path, sha in todo:
        try:
            st = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(st.st_mode) or stat.S_ISDIR(st.st_mode) or _file_sha256(path) != sha:
            left.append(rel)
            continue
        _delete_own(rel, path)
        removed.append(path)
    for rel, path in extra:
        if path.is_dir() and not play_client._is_link(path):
            continue
        if os.path.lexists(path):
            _delete_own(rel, path)
            removed.append(path)
    for path in removed:
        _remove_empty_parents(play_dir, path)
    if left:
        logger.warning("client-packs: left %s, which was edited since it was installed", left)
    return tuple(sorted(left))


def _remove_empty_parents(play_dir: Path, path: Path) -> None:
    parent = path.parent
    while len(parent.relative_to(play_dir).parts) >= _KEEP_DEPTH:
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent
