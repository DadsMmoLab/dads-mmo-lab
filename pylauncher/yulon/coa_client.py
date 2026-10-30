"""Keeping the player's own Conquest of Azeroth client current (T825).

The CoA project publishes its client as a manifest -- a JSON list of every file
with its size and SHA-256 (`catalog.ClientUpdate.manifest_url`) -- and serves
each file by that digest (`<objects_url><sha256>`). This module reads the
manifest, compares the player's client folder against it, and brings the folder
in step: only the files that differ, each one downloaded beside its target,
proved by size and SHA-256, the old file moved into a backup, and the new one
renamed into place. Nothing unproved ever wears a client file's name.

**What is never written.** Logs (`*.log`), `realmlist.wtf`, the `WTF/`,
`Cache/`, `Errors/` and `Logs/` folders and `Interface/AddOns/CoaBotUI/` (our
own add-on's place). The player's own settings and graphics wrappers
(`d3d8/d3d9/d3d10core/d3d11/dxgi.dll`, `dxvk.conf`, every `*.ini/*.conf/*.cfg/*.wtf`)
are only created when missing, never replaced. A differing file is replaced only
when Yu'lon recorded it and the player has not changed it since; any other (every
file on a first update, or one the player changed) is kept unless the player
chooses to replace it (the owner's decision, T825). A Keep is
remembered against the manifest's digest for that file, so the same update does
not ask twice.

**What is kept beside the client.** `STATE_FILE`: the manifest version the folder
was brought to (None while an update is unfinished), what Yu'lon verified of
each file, and the Keep choices; written LAST, so it never claims a version the
files are not at. `BACKUP_DIR/<version>/`: every file an update replaced, plus a
journal of what it replaced and added, which `rollback()` reads to put the
folder back as it was.

The state file is NOT `play_client.MARKER` (`.yulon-client.json`): that name
marks a ready-to-play folder Yu'lon owns and may delete from, and the player's
own client must never wear it.

Network only through an `client_packs.Opener` (https only, the entry's hosts,
the self-updater's stall watchdog), so tests pass a fake. No Qt here.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import re
import shutil
import sys
import threading
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Literal, TypeGuard
from urllib.error import HTTPError
from urllib.parse import urlsplit

from yulon import client_names, client_packs, play_client
from yulon.after_stop import StopTookEffect
from yulon.log import get_logger
from yulon.play_client import _is_link
from yulon.selfupdate import fetch
from yulon.update import _can_only_go_where_it_says, _Deadline

logger = get_logger(__name__)

MANIFEST_MAX_BYTES = 16 * 1024 * 1024
"""The most of a manifest that is read: the CoA client's lists 201 files in 34 KB (2026-10-29)."""

SUPPORTED_SCHEMAS = frozenset({1})
"""Manifest `schema` values this build understands; any other is refused, never guessed at."""

CHECK_SECONDS = 10.0
"""How long the check at tab open may wait for the manifest before it says it could not check."""

STALL_SECONDS = fetch.STALL_SECONDS
CHUNK_BYTES = fetch.CHUNK_BYTES

STATE_FILE = play_client.CLIENT_UPDATE_STATE
BACKUP_DIR = play_client.CLIENT_UPDATE_BACKUP
JOURNAL = ".yulon-journal.json"
CONFLICTS = ".yulon-conflicts"
"""In `BACKUP_DIR/<version>/`: files the player changed after that update, moved aside by Undo.

A `.yulon-` name, which `safe_rel` refuses in any manifest path, so no client file
backed up beside it can ever wear it."""
"""In `BACKUP_DIR/<version>/`: what that update replaced and added, for `rollback()`."""
PART_SUFFIX = ".yulon-part"
_STATE_VERSION = 1

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FORBIDDEN_CHARS = frozenset('\\:<>"|?*')
_WINDOWS_DEVICES = client_packs._WINDOWS_DEVICES

_EXCLUDED_FOLDERS = frozenset({"wtf", "cache", "errors", "logs"})
_EXCLUDED_NAMES = frozenset({"realmlist.wtf"})
_EXCLUDED_SUFFIXES = frozenset({".log"})
_EXCLUDED_TREES = (("interface", "addons", "coabotui"),)
_PLAYER_NAMES = frozenset(
    {"d3d8.dll", "d3d9.dll", "d3d10core.dll", "d3d11.dll", "dxgi.dll", "dxvk.conf"}
)
_PLAYER_SUFFIXES = frozenset({".ini", ".conf", ".cfg", ".wtf"})

Action = Literal["ok", "add", "replace", "keep-edited", "skip-player-owned"]
Kind = Literal["excluded", "player-owned", "normal"]
Progress = Callable[[str], None]


class ClientUpdateError(RuntimeError):
    """A refusal worded for the player, naming what to do next."""


class ManifestError(ClientUpdateError):
    """The published manifest is not one Yu'lon can trust: nothing was changed."""


class Cancelled(ClientUpdateError, StopTookEffect):
    """The player pressed Cancel. Downloads in progress are kept, so the next try continues."""


def _never() -> bool:
    return False


def _quiet(_text: str) -> None:
    return None


# --- The manifest ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    """A parsed manifest. `digest` is the SHA-256 of the bytes it was parsed from."""

    schema: int
    version: str
    published_at: str | None
    files: tuple[ManifestFile, ...]
    digest: str

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)


def safe_rel(raw: object) -> PurePosixPath:
    """A manifest path as a relative POSIX path that cannot leave the client folder, or refused.

    Refused: anything not a string, empty, absolute (`/x`), a drive (`C:`) or a
    stream (`a:b`), a backslash, NUL or another control character, a `.` or `..`
    or empty component, a component ending in a dot or space (Windows drops them,
    so two names would be one file), a Windows device name (`NUL.txt`), and
    anything named like Yu'lon's own files (`.yulon-*`).
    """
    if not isinstance(raw, str) or not raw or len(raw) > 1024:
        raise ManifestError(f"The client's file list names a path Yu'lon cannot use ({raw!r}).")
    bad = (
        raw.startswith("/")
        or any(ch in _FORBIDDEN_CHARS or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw)
        or raw.endswith("/")
    )
    parts = raw.split("/")
    for part in parts:
        if (
            part in ("", ".", "..")
            or part.endswith((".", " "))
            or part.split(".")[0].strip().upper() in _WINDOWS_DEVICES
            or ".yulon-" in part.casefold()
        ):
            bad = True
    if bad:
        raise ManifestError(
            f"The client's file list names {raw!r}, a path outside the client folder or one "
            "Windows cannot hold, so Yu'lon changed nothing. Try again later; if it stays, "
            "tell the Conquest of Azeroth project."
        )
    return PurePosixPath(*parts)


def classify(rel: PurePosixPath | str) -> Kind:
    """Whether Yu'lon may write `rel`: never (`excluded`), only when missing, or freely."""
    parts = tuple(part.casefold() for part in PurePosixPath(rel).parts)
    name = parts[-1]
    suffix = PurePosixPath(name).suffix
    if (
        parts[0] in _EXCLUDED_FOLDERS
        or name in _EXCLUDED_NAMES
        or suffix in _EXCLUDED_SUFFIXES
        or any(parts[: len(tree)] == tree for tree in _EXCLUDED_TREES)
    ):
        return "excluded"
    if name in _PLAYER_NAMES or suffix in _PLAYER_SUFFIXES:
        return "player-owned"
    return "normal"


def _strict_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_manifest(raw: bytes) -> Manifest:
    """The manifest's bytes as a `Manifest`, every field checked, or `ManifestError`."""
    if len(raw) > MANIFEST_MAX_BYTES:
        raise ManifestError(
            "The client's file list is larger than any Yu'lon accepts, so it was not read. "
            "Try again later."
        )
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ManifestError(
            f"The client's file list could not be read ({exc}). Try again later."
        ) from exc
    if not isinstance(data, dict):
        raise ManifestError("The client's file list is not a list Yu'lon knows. Try again later.")
    schema = data.get("schema")
    if not _strict_int(schema) or schema not in SUPPORTED_SCHEMAS:
        raise ManifestError(
            f"The client's file list is in a form this Yu'lon does not know (schema {schema!r}). "
            "Update Yu'lon, then try again."
        )
    version = data.get("version")
    if not isinstance(version, str) or not _VERSION.match(version):
        raise ManifestError(
            f"The client's file list carries a version Yu'lon cannot use ({version!r}). Try "
            "again later."
        )
    published = data.get("publishedAt")
    if published is not None and not isinstance(published, str):
        raise ManifestError("The client's file list carries a date Yu'lon cannot read.")
    files = data.get("files")
    if not isinstance(files, list):
        raise ManifestError("The client's file list has no list of files. Try again later.")
    parsed: list[ManifestFile] = []
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ManifestError("The client's file list holds an entry that is not a file.")
        rel = safe_rel(item.get("path"))
        size, digest = item.get("size"), item.get("sha256")
        if not _strict_int(size) or size < 0:
            raise ManifestError(f"The client's file list gives {rel} no usable size.")
        if not isinstance(digest, str) or not _HEX64.match(digest):
            raise ManifestError(f"The client's file list gives {rel} no usable SHA-256.")
        key = rel.as_posix().casefold()
        if key in seen:
            raise ManifestError(
                f"The client's file list names {rel} twice (in some spelling), so Yu'lon "
                "changed nothing."
            )
        seen.add(key)
        parsed.append(ManifestFile(rel.as_posix(), size, digest.lower()))
    return Manifest(
        schema=schema,
        version=version,
        published_at=published,
        files=tuple(parsed),
        digest=hashlib.sha256(raw).hexdigest(),
    )


def _refuse_unlisted(url: str, hosts: frozenset[str]) -> None:
    host = urlsplit(url).hostname
    if host is None or host not in hosts or not _can_only_go_where_it_says(url, host):
        raise ClientUpdateError(
            f"The client update would be fetched from {host or url!r}, which is not a plain "
            "https address this server names, so Yu'lon did not ask it."
        )


def _open(
    opener: client_packs.Opener,
    url: str,
    watcher: _Deadline,
    *,
    headers: Mapping[str, str],
    hosts: frozenset[str],
) -> client_packs.Response:
    """Open `url` with GET, every failure worded for the player."""
    try:
        response = opener(url, watcher, method="GET", headers=headers, hosts=hosts)
    except HTTPError as exc:
        raise ClientUpdateError(
            f"The Conquest of Azeroth site answered HTTP {exc.code} for {url}. Try again later."
        ) from exc
    except fetch.UpdateError as exc:
        raise ClientUpdateError(str(exc)) from exc
    except AssertionError:
        raise  # a test's refusal (conftest), never worded as "could not be reached"
    except Exception as exc:
        raise ClientUpdateError(
            f"The Conquest of Azeroth site could not be reached ({exc}). Check the connection "
            "and try again."
        ) from exc
    if not 200 <= response.status < 300:
        client_packs._close(response)
        raise ClientUpdateError(
            f"The Conquest of Azeroth site answered HTTP {response.status} for {url}. Try "
            "again later."
        )
    return response


def _network(
    url: str,
    watcher: _Deadline,
    *,
    method: str,
    headers: Mapping[str, str],
    hosts: frozenset[str],
) -> client_packs.Response:
    """The real opener (`client_packs._open`). Looked up at call time, so a test can refuse it."""
    return client_packs._open(url, watcher, method=method, headers=headers, hosts=hosts)


def _opener(given: client_packs.Opener | None) -> client_packs.Opener:
    return given if given is not None else _network


def fetch_manifest(
    url: str,
    *,
    hosts: frozenset[str],
    opener: client_packs.Opener | None = None,
    seconds: float = STALL_SECONDS,
) -> Manifest:
    """GET the manifest, read no more than `MANIFEST_MAX_BYTES` + 1, and parse it."""
    _refuse_unlisted(url, hosts)
    opener = _opener(opener)
    data = bytearray()
    with _Deadline(seconds) as watcher:
        response = _open(opener, url, watcher, headers={}, hosts=hosts)
        try:
            while len(data) <= MANIFEST_MAX_BYTES:
                try:
                    chunk = response.read1(min(CHUNK_BYTES, MANIFEST_MAX_BYTES + 1 - len(data)))
                except Exception as exc:
                    raise ClientUpdateError(
                        f"The client's file list stopped arriving ({exc}). Try again."
                    ) from exc
                if watcher.fired:
                    raise ClientUpdateError("The client's file list did not arrive. Try again.")
                if not chunk:
                    break
                data += chunk
        finally:
            client_packs._close(response)
    return parse_manifest(bytes(data))


# --- The state beside the client --------------------------------------------------------------


@dataclass(frozen=True)
class FileFact:
    """What Yu'lon last verified of one client file: its size, mtime (ns) and SHA-256."""

    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class State:
    """`STATE_FILE`'s content. `version` None: an update was started and not finished."""

    version: str | None
    manifest_sha256: str | None
    files: Mapping[str, FileFact] = field(default_factory=dict)
    kept: Mapping[str, str] = field(default_factory=dict)
    """Path -> the manifest SHA-256 the player chose to keep their own file against."""


def _state_dict(state: State) -> dict[str, Any]:
    return {
        "state_version": _STATE_VERSION,
        "version": state.version,
        "manifest_sha256": state.manifest_sha256,
        "files": {rel: asdict(fact) for rel, fact in sorted(state.files.items())},
        "kept": dict(sorted(state.kept.items())),
    }


def _state_from(raw: object) -> State | None:
    if not isinstance(raw, dict) or raw.get("state_version") != _STATE_VERSION:
        return None
    version = raw.get("version")
    digest = raw.get("manifest_sha256")
    files_raw, kept_raw = raw.get("files"), raw.get("kept")
    if not (version is None or isinstance(version, str)) or not isinstance(files_raw, dict):
        return None
    if not isinstance(kept_raw, dict):
        return None
    files: dict[str, FileFact] = {}
    for rel, fact in files_raw.items():
        if not isinstance(fact, dict):
            return None
        size, mtime, sha = fact.get("size"), fact.get("mtime_ns"), fact.get("sha256")
        if not (_strict_int(size) and _strict_int(mtime) and isinstance(sha, str)):
            return None
        files[str(rel)] = FileFact(size, mtime, sha)
    kept = {str(rel): str(sha) for rel, sha in kept_raw.items() if isinstance(sha, str)}
    return State(version, digest if isinstance(digest, str) else None, files, kept)


def read_state(client_dir: Path) -> State | None:
    """The state file, or None: absent (never updated by Yu'lon), unreadable or malformed."""
    path = client_dir / STATE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning(f"coa-client: {path} could not be read ({exc}); treating it as absent")
        return None
    try:
        state = _state_from(json.loads(text))
    except ValueError:
        state = None
    if state is None:
        logger.warning(f"coa-client: {path} is not a state Yu'lon wrote; treating it as absent")
    return state


def write_state(client_dir: Path, state: State) -> None:
    """Atomic: a temporary beside it, then `os.replace`."""
    path = client_dir / STATE_FILE
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(_state_dict(state), indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


# --- The check the tab shows -----------------------------------------------------------------

Status = Literal["current", "outdated", "unknown"]


def status(client_dir: Path, manifest: Manifest) -> Status:
    """The folder against the manifest by the recorded version alone: no file is read.

    `unknown` for a folder Yu'lon never updated: its version cannot be told without
    reading every file, which only Update client does. An update that did not
    finish (version None) is `outdated`.
    """
    state = read_state(client_dir)
    if state is None:
        return "unknown"
    return "current" if state.version == manifest.version else "outdated"


CHECK_FAILED = "Could not check for a client update."


def check_sentence(client_dir: Path, manifest: Manifest) -> tuple[Status, str]:
    """The status and the plain sentence the Server tab shows beside Update client."""
    found = status(client_dir, manifest)
    if found == "current":
        return found, f"Your client is up to date (version {manifest.version})."
    if found == "outdated":
        return found, (
            f"A client update is available (version {manifest.version}). Update client "
            "brings your client folder up to date; Play still works without it."
        )
    return found, (
        f"Client version unknown: Yu'lon has not updated this client folder before. Update "
        f"client checks every file against version {manifest.version} and lists what "
        "differs before changing anything."
    )


# --- Which client this is (T827) --------------------------------------------------------------

KEY_FILES = ("Extensions.dll", "Ascension.exe")
"""The files that tell the CoA client from the stock Ascension download (T827).

A stock Ascension client logs in to a CoA server and is then disconnected at
realm select (`malformed packet ... cryptInitialized: true`): the CoA builds of
these two files change the world socket's header cipher, the stock ones do not.
Both are small (7 MB), so reading them is quick; the 46 GB of MPQs are never read
here. The owner's working client keeps the stock copies beside them as
`*.ORIGINAL`; that is not used as evidence, the manifest's digests are.
"""

ClientKind = Literal["coa", "stock", "unknown"]

KIND_COA = "Client files: the Conquest of Azeroth client."
STOCK_WARNING = (
    "This client is the stock Ascension download. Conquest of Azeroth needs the CoA client "
    "files, otherwise you are disconnected when you pick the realm. Choose Update client."
)

_KEY_SHA: dict[tuple[str, int, int], str] = {}
_KEY_SHA_LOCK = threading.Lock()


def _key_sha(path: Path, st: os.stat_result) -> str:
    """`path`'s SHA-256, remembered by path, size and mtime so a recheck reads nothing."""
    key = (os.fspath(path), st.st_size, st.st_mtime_ns)
    with _KEY_SHA_LOCK:
        known = _KEY_SHA.get(key)
    if known is not None:
        return known
    digest = _sha256(path)
    with _KEY_SHA_LOCK:
        _KEY_SHA[key] = digest
    return digest


def client_kind(client_dir: Path, manifest: Manifest | None, state: State | None) -> ClientKind:
    """Whether `client_dir` holds the CoA client files (`KEY_FILES`), the stock ones, or unknown.

    `coa`: every key file the manifest lists (or, with no manifest, every one Yu'lon
    recorded) is in the folder and equals the manifest's entry or what Yu'lon
    recorded when it verified that file (an older CoA version is still the CoA
    client). `stock`: with a manifest in hand, a key file is missing or equals
    neither -- the stock Ascension download, or a changed one. `unknown`: no key
    file is there, the folder cannot be read, or there is no manifest and nothing
    recorded to compare with. Reads only the key files, and hashes one only when
    its size equals what it is compared with. Blocking: never on the GUI thread.
    """
    listed = {item.path.casefold(): item for item in manifest.files} if manifest else {}
    facts = {rel.casefold(): fact for rel, fact in (state.files if state else {}).items()}
    present = 0
    every_match = True
    for name in KEY_FILES:
        entry, fact = listed.get(name.casefold()), facts.get(name.casefold())
        if entry is None and fact is None:
            continue  # nothing to compare this one with
        try:
            target = _target(client_dir, PurePosixPath(name))
            st = target.stat()
            if not target.is_file():
                return "unknown"
            present += 1
            same_as_recorded = fact is not None and (
                (fact.size, fact.mtime_ns) == (st.st_size, st.st_mtime_ns)
                or (fact.size == st.st_size and _key_sha(target, st) == fact.sha256)
            )
            same_as_published = (
                entry is not None
                and entry.size == st.st_size
                and _key_sha(target, st) == entry.sha256
            )
        except FileNotFoundError:
            every_match = False
            continue
        except (OSError, ClientUpdateError) as exc:
            logger.info(f"coa-client: could not read {name} in {client_dir} ({exc})")
            return "unknown"
        if not (same_as_recorded or same_as_published):
            every_match = False
    if present == 0:
        return "unknown"
    if every_match:
        return "coa"
    return "stock" if manifest is not None else "unknown"


def kind_sentence(kind: ClientKind) -> str:
    """The line the Server tab shows for `kind`; empty for `unknown` (nothing to say)."""
    return {"coa": KIND_COA, "stock": STOCK_WARNING}.get(kind, "")


# --- Planning ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanItem:
    path: str
    action: Action
    size: int
    sha256: str
    remembered: bool = False
    """For `keep-edited`: the player already chose Keep against this manifest digest."""


def _target(client_dir: Path, rel: PurePosixPath) -> Path:
    """`rel` under `client_dir` as the disk spells it, refused if a link is on the way."""
    spelled = client_names.on_disk(client_dir, rel)
    here = client_dir
    for part in spelled.parts:
        here = here / part
        if os.path.lexists(here) and _is_link(here):
            raise ClientUpdateError(
                f"{here} is a link, so Yu'lon will not write through it. Replace it with the "
                "real file or folder, then try Update client again."
            )
    return here


def _sha256(path: Path, cancelled: Callable[[], bool] = _never) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            if cancelled():
                raise Cancelled("The client check was cancelled; nothing was changed.")
            digest.update(chunk)
    return digest.hexdigest()


def _current_sha(
    path: Path, st: os.stat_result, fact: FileFact | None, cancelled: Callable[[], bool]
) -> str:
    """The file's SHA-256, taken from the state when its size and mtime are what Yu'lon saw."""
    if fact is not None and fact.size == st.st_size and fact.mtime_ns == st.st_mtime_ns:
        return fact.sha256
    return _sha256(path, cancelled)


def plan(
    client_dir: Path,
    manifest: Manifest,
    state: State | None,
    *,
    cancelled: Callable[[], bool] = _never,
    progress: Progress = _quiet,
) -> list[PlanItem]:
    """What Update client would do to each manifest file; excluded files are not listed.

    A differing file is `replace` only when Yu'lon recorded it and it still holds
    what was recorded (the player did not touch it). One Yu'lon has no record of
    (every file on a first update) or the player changed since is `keep-edited`:
    kept unless the player chooses to replace it (owner's decision, T825).
    """
    items: list[PlanItem] = []
    files = state.files if state is not None else {}
    kept = state.kept if state is not None else {}
    total = len(manifest.files)
    for index, entry in enumerate(manifest.files, start=1):
        if cancelled():
            raise Cancelled("The client check was cancelled; nothing was changed.")
        rel = PurePosixPath(entry.path)
        kind = classify(rel)
        if kind == "excluded":
            continue
        target = _target(client_dir, rel)
        progress(f"Checking client files: {index} of {total} ({entry.path})")
        try:
            st = target.stat()
        except FileNotFoundError:
            items.append(PlanItem(entry.path, "add", entry.size, entry.sha256))
            continue
        if not target.is_file():
            raise ClientUpdateError(
                f"{target} is a folder where the client update has a file. Move it away, then "
                "try Update client again."
            )
        if kind == "player-owned":
            items.append(PlanItem(entry.path, "skip-player-owned", entry.size, entry.sha256))
            continue
        fact = files.get(entry.path)
        same = st.st_size == entry.size and (
            _current_sha(target, st, fact, cancelled) == entry.sha256
        )
        if same:
            items.append(PlanItem(entry.path, "ok", entry.size, entry.sha256))
            continue
        if kept.get(entry.path) == entry.sha256:
            items.append(PlanItem(entry.path, "keep-edited", entry.size, entry.sha256, True))
            continue
        edited = fact is None or _current_sha(target, st, fact, cancelled) != fact.sha256
        action: Action = "keep-edited" if edited else "replace"
        items.append(PlanItem(entry.path, action, entry.size, entry.sha256))
    return items


def size_text(size: int) -> str:
    return client_packs._size_text(size)


def summary(items: Iterable[PlanItem]) -> str:
    """One plain sentence: how many files change and how much is downloaded."""
    items = list(items)
    write = [i for i in items if i.action in ("add", "replace")]
    edited = [i for i in items if i.action == "keep-edited" and not i.remembered]
    if not write and not edited:
        return "Your client already matches the update; nothing needs downloading."
    said = (
        f"{len(write)} file{'s' if len(write) != 1 else ''} to download "
        f"({size_text(sum(i.size for i in write))})."
    )
    if edited:
        one = len(edited) == 1
        said += (
            f" {len(edited)} file{'' if one else 's'} "
            f"({', '.join(i.path for i in edited[:3])}{', …' if len(edited) > 3 else ''}) "
            f"differ{'s' if one else ''} from the published one{'' if one else 's'}; "
            f"{'it is' if one else 'they are'} kept unless you choose to replace "
            f"{'it' if one else 'them'} ({size_text(sum(i.size for i in edited))} more)."
        )
    return said


# --- Applying ---------------------------------------------------------------------------------


@dataclass
class Outcome:
    written: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    version_recorded: bool = False

    def sentence(self, version: str) -> str:
        if not self.failed:
            return (
                f"Your client is up to date (version {version}): {len(self.written)} file"
                f"{'s' if len(self.written) != 1 else ''} updated, the old ones kept in "
                f"{BACKUP_DIR}."
            )
        path, why = self.failed[0]
        more = f" ({len(self.failed) - 1} more)" if len(self.failed) > 1 else ""
        return (
            f"The client update did not finish: {path}{more}: {why} The files that could not "
            "be updated were left as they were."
        )


def _free_bytes(folder: Path) -> int:
    return shutil.disk_usage(folder).free


def _part_of(target: Path) -> Path:
    return target.with_name(target.name + PART_SUFFIX)


class _Mismatch(ClientUpdateError):
    """The downloaded bytes are not the manifest's: the `.part` is deleted, the old file kept."""


_CONTENT_RANGE = re.compile(r"^\s*bytes\s+(\d+)-(\d+)/(\d+|\*)\s*$")


def _open_part(part: Path, *, append: bool) -> BinaryIO:
    """`part` opened for writing, never through a link where the OS can refuse one.

    `O_NOFOLLOW` (POSIX) makes a link planted after `_download`'s check fail the
    open instead of writing to what it points at; Windows has no such flag, so
    there the check before the open is what stands.
    """
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    flags |= os.O_APPEND if append else os.O_TRUNC
    fd = os.open(part, flags, 0o666)
    return os.fdopen(fd, "ab" if append else "wb")


def _download(
    url: str,
    part: Path,
    size: int,
    *,
    opener: client_packs.Opener,
    hosts: frozenset[str],
    cancelled: Callable[[], bool],
    progress: Callable[[int], None],
) -> None:
    """Bring `part` to `size` bytes, resuming from what it holds (HTTP Range).

    A file the manifest lists with size 0 has nothing to fetch: `part` is made
    empty here, without asking the site, and `_prove` checks its digest like any
    other (T838: before, nothing was made and the file stayed missing forever).
    """
    if os.path.lexists(part) and _is_link(part):
        # Left for the player: neither the link nor what it points at is touched.
        raise ClientUpdateError(
            f"a link is in the way at {part.name}; nothing was written. Remove it, then "
            "choose Update client again."
        )
    if size == 0:
        _open_part(part, append=False).close()
        return
    have = part.stat().st_size if part.is_file() else 0
    if have > size:
        part.unlink()
        have = 0
    if have == size:
        return
    headers = {"Range": f"bytes={have}-"} if have else {}
    done = have
    with _Deadline(STALL_SECONDS) as watcher:
        response = _open(opener, url, watcher, headers=headers, hosts=hosts)
        try:
            if have and response.status == 206:
                match = _CONTENT_RANGE.match(response.getheader("Content-Range") or "")
                if match is None or int(match.group(1)) != have:
                    part.unlink(missing_ok=True)
                    raise ClientUpdateError(
                        "The Conquest of Azeroth site answered a resumed download with another "
                        "part of the file. Try again; it starts over."
                    )
                mode = "ab"
            else:
                done, mode = 0, "wb"
            with _open_part(part, append=mode == "ab") as handle:
                while True:
                    if cancelled():
                        raise Cancelled(
                            "The client update was cancelled; what arrived is kept and the "
                            "next Update client continues from there."
                        )
                    try:
                        chunk = response.read1(min(CHUNK_BYTES, size - done + 1))
                    except Exception as exc:
                        raise ClientUpdateError(
                            f"the download stopped ({exc}); the next try continues from there."
                        ) from exc
                    if watcher.fired:
                        raise ClientUpdateError(
                            "the download sent nothing for a while; the next try continues "
                            "from there."
                        )
                    if not chunk:
                        break
                    watcher.restart(STALL_SECONDS)
                    handle.write(chunk)
                    done += len(chunk)
                    if done > size:
                        break
                    progress(len(chunk))
        finally:
            client_packs._close(response)
    if done < size:
        raise ClientUpdateError("the download stopped part-way; the next try continues from there.")


def _prove(part: Path, entry: PlanItem, cancelled: Callable[[], bool]) -> None:
    """The `.part` holds exactly the manifest's bytes, or it is deleted and `_Mismatch` raised."""
    actual_size = part.stat().st_size
    actual = _sha256(part, cancelled) if actual_size == entry.size else None
    if actual != entry.sha256:
        part.unlink(missing_ok=True)
        raise _Mismatch(
            f"the downloaded file does not match the update's size and SHA-256 "
            f"({actual_size} bytes, expected {entry.size}), so the old file was kept. Try again."
        )


def _in_use(exc: OSError) -> bool:
    """A Windows sharing violation, or a refusal there: the game (or a scanner) holds the file."""
    return getattr(exc, "winerror", None) in (5, 32, 33) or (
        sys.platform == "win32" and isinstance(exc, PermissionError)
    )


def _os_sentence(exc: OSError) -> str:
    if _in_use(exc):
        return (
            "the file is in use. Close the game (and anything else using the client's files), "
            "then choose Update client again."
        )
    if exc.errno == errno.ENOSPC:
        return "the drive is full. Free some space, then choose Update client again."
    return f"Yu'lon could not write it ({exc.strerror or exc}). Choose Update client again."


def _no_link_below(client_dir: Path, path: Path) -> Path:
    """`path`, refused when any existing part of it below `client_dir` is a link.

    A symlink, junction or other name-surrogate reparse point (`play_client._is_link`)
    in `BACKUP_DIR`, a version folder or anywhere under it would carry the journal and
    the player's original files outside the client folder. Asked again right before
    every journal write and every move, not only once per update.

    The limit, accepted (T825 review round 2): backup containment is checked
    immediately before each move; a process that races the folder between the
    check and the move (same user, same client folder) is not defended against.
    Closing that needs handle-relative no-follow moves (`dir_fd` + `O_NOFOLLOW`),
    which Python offers on POSIX only, not on Windows.
    """
    here = client_dir
    for part in path.relative_to(client_dir).parts:
        here = here / part
        if os.path.lexists(here) and _is_link(here):
            raise ClientUpdateError(
                f"{here} is a link, so Yu'lon will not keep backups through it. Replace it "
                "with a real folder (or remove it), then try again."
            )
    return path


def _backup_root(client_dir: Path, version: str) -> Path:
    if not _VERSION.match(version):
        raise ManifestError(f"The update's version {version!r} cannot name a backup folder.")
    return _no_link_below(client_dir, client_dir / BACKUP_DIR / version)


def _read_journal(root: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads((root / JOURNAL).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ClientUpdateError(
            f"The backup record in {root} could not be read ({exc}), so Yu'lon changed nothing."
        ) from exc
    if not (
        isinstance(raw, dict)
        and isinstance(raw.get("replaced"), dict)
        and isinstance(raw.get("added"), dict)
    ):
        raise ClientUpdateError(f"The backup record in {root} is not one Yu'lon wrote.")
    for rel, record in list(raw["replaced"].items()):
        if isinstance(record, str):  # an early journal: only the backup's place
            raw["replaced"][rel] = {"backup": record, "original": None, "installed": None}
        elif not (isinstance(record, dict) and isinstance(record.get("backup"), str)):
            raise ClientUpdateError(f"The backup record in {root} is not one Yu'lon wrote.")
    undone = raw.setdefault("undone", {})
    if not isinstance(undone, dict):
        raise ClientUpdateError(f"The backup record in {root} is not one Yu'lon wrote.")
    return raw


def _write_journal(client_dir: Path, root: Path, journal: Mapping[str, Any]) -> None:
    """Atomic rewrite of the journal, its folder checked for links first (every time)."""
    _no_link_below(client_dir, root)
    root.mkdir(parents=True, exist_ok=True)
    _no_link_below(client_dir, root)
    path = _no_link_below(client_dir, root / JOURNAL)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(journal, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _fresh(path: Path) -> Path:
    """`path`, or `path.<n>` when a backup of the same file from an earlier try is there."""
    if not os.path.lexists(path):
        return path
    number = 1
    while os.path.lexists(path.with_name(f"{path.name}.{number}")):
        number += 1
    return path.with_name(f"{path.name}.{number}")


def _put_in_place(
    client_dir: Path,
    root: Path,
    journal: dict[str, Any],
    item: PlanItem,
    target: Path,
    part: Path,
) -> None:
    """Back the old file up (journal first), then rename the proved `.part` onto `target`."""
    if os.path.lexists(target):
        if classify(item.path) == "player-owned":
            part.unlink(missing_ok=True)
            raise ClientUpdateError("it is yours to keep, so Yu'lon left it.")
        backup = _fresh(root.joinpath(*PurePosixPath(item.path).parts))
        _no_link_below(client_dir, backup)
        backup.parent.mkdir(parents=True, exist_ok=True)
        journal["replaced"].setdefault(
            item.path,
            {
                "backup": backup.relative_to(root).as_posix(),
                "original": _sha256(target),
                "installed": item.sha256,
            },
        )
        _write_journal(client_dir, root, journal)
        _no_link_below(client_dir, backup)
        os.replace(target, backup)
        try:
            os.replace(part, target)
        except OSError:
            os.replace(backup, target)
            raise
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        journal["added"][item.path] = item.sha256
        _write_journal(client_dir, root, journal)
        os.replace(part, target)


def _fact(path: Path, sha256: str) -> FileFact:
    st = path.stat()
    return FileFact(st.st_size, st.st_mtime_ns, sha256)


def apply(
    client_dir: Path,
    manifest: Manifest,
    items: Collection[PlanItem],
    *,
    objects_url: str,
    hosts: frozenset[str],
    replace_edited: bool = False,
    opener: client_packs.Opener | None = None,
    progress: Progress = _quiet,
    cancelled: Callable[[], bool] = _never,
    free_bytes: Callable[[Path], int] = _free_bytes,
) -> Outcome:
    """Bring `client_dir` to `manifest` as `items` (from `plan()`) say. The state file LAST.

    `replace_edited`: the player said Replace for the files they changed (each is
    backed up like any other). A file whose download does not prove is left as
    it was and reported; the rest go on. A Cancel keeps every `.part` for the
    next try, records what was done, and raises `Cancelled`.
    """
    _refuse_unlisted(objects_url, hosts)
    opener = _opener(opener)
    root = _backup_root(client_dir, manifest.version)
    previous = read_state(client_dir)
    todo = [
        item
        for item in items
        if item.action in ("add", "replace")
        or (item.action == "keep-edited" and replace_edited and not item.remembered)
    ]
    for item in todo:
        if classify(item.path) == "excluded":
            raise ClientUpdateError(f"{item.path} is never written by Yu'lon; nothing changed.")
        if classify(item.path) == "player-owned" and item.action != "add":
            raise ClientUpdateError(f"{item.path} is yours to keep; nothing changed.")
    targets = {item.path: _target(client_dir, PurePosixPath(item.path)) for item in todo}
    needed = 0
    for item in todo:
        part = _part_of(targets[item.path])
        have = part.stat().st_size if part.is_file() else 0
        needed += max(item.size - have, 0)
    free = free_bytes(client_dir)
    if free < needed:
        raise ClientUpdateError(
            f"The client update needs {size_text(needed)} of free space in {client_dir}, and "
            f"that drive has {size_text(free)}. Free some space and try again; nothing was "
            "changed."
        )
    journal = _read_journal(root) or {
        "version": manifest.version,
        "previous_state": _state_dict(previous) if previous is not None else None,
        "replaced": {},
        "added": {},
    }
    outcome = Outcome()
    total = max(sum(item.size for item in todo), 1)
    got = 0

    def moved(count: int) -> None:
        nonlocal got
        got += count
        progress(f"Updating the client: {size_text(got)} of {size_text(total)}")

    stop: Cancelled | None = None
    for item in todo:
        if cancelled():
            stop = Cancelled(
                "The client update was cancelled; what arrived is kept and the next Update "
                "client continues from there."
            )
            break
        target = targets[item.path]
        part = _part_of(target)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            _download(
                objects_url + item.sha256,
                part,
                item.size,
                opener=opener,
                hosts=hosts,
                cancelled=cancelled,
                progress=moved,
            )
            _prove(part, item, cancelled)
            _put_in_place(client_dir, root, journal, item, target, part)
        except Cancelled as exc:
            stop = exc
            break
        except ClientUpdateError as exc:
            outcome.failed.append((item.path, str(exc)))
            continue
        except OSError as exc:
            outcome.failed.append((item.path, _os_sentence(exc)))
            continue
        outcome.written.append(item.path)
    _record(client_dir, manifest, items, previous, outcome, replace_edited, finished=stop is None)
    if stop is not None:
        raise stop
    return outcome


def _record(
    client_dir: Path,
    manifest: Manifest,
    items: Collection[PlanItem],
    previous: State | None,
    outcome: Outcome,
    replace_edited: bool,
    *,
    finished: bool,
) -> None:
    """Write the state file: what is now verified, the Keep choices, and the version if done."""
    files = dict(previous.files) if previous is not None else {}
    kept = dict(previous.kept) if previous is not None else {}
    written = set(outcome.written)
    failed = {path for path, _ in outcome.failed}
    for item in items:
        target = client_dir.joinpath(*client_names.on_disk(client_dir, item.path).parts)
        if item.path in written or item.action == "ok":
            with contextlib.suppress(OSError):
                files[item.path] = _fact(target, item.sha256)
            kept.pop(item.path, None)
        elif item.action == "keep-edited" and (item.remembered or not replace_edited):
            kept[item.path] = item.sha256
            outcome.kept.append(item.path)
    pending = [
        i
        for i in items
        if i.path not in written
        and (
            i.action in ("add", "replace")
            or (i.action == "keep-edited" and replace_edited and not i.remembered)
        )
    ]
    done = finished and not failed and not pending
    write_state(
        client_dir,
        State(
            manifest.version if done else None,
            manifest.digest,
            files,
            kept,
        ),
    )
    outcome.version_recorded = done


# --- Rolling back -----------------------------------------------------------------------------


def backups(client_dir: Path) -> list[str]:
    """The versions whose backups are kept, the most recently written journal last."""
    folder = client_dir / BACKUP_DIR
    try:
        found = [
            ((p / JOURNAL).stat().st_mtime_ns, p.name)
            for p in folder.iterdir()
            if p.is_dir() and (p / JOURNAL).is_file()
        ]
    except OSError:
        return []
    return [name for _, name in sorted(found)]


@dataclass
class Undone:
    """What `rollback()` did: what is still to put back, and the player's files moved aside."""

    version: str
    left: list[str] = field(default_factory=list)
    kept_changed: int = 0
    conflicts_dir: Path | None = None

    def sentence(self) -> str:
        kept = ""
        if self.kept_changed:
            many = self.kept_changed != 1
            kept = (
                f" {self.kept_changed} file{'s' if many else ''} you changed after the update "
                f"{'were' if many else 'was'} kept in {self.conflicts_dir}."
            )
        if not self.left:
            return f"The client update {self.version} was undone.{kept}"
        more = f" ({len(self.left) - 1} more)" if len(self.left) > 1 else ""
        return (
            f"The client update {self.version} was only partly undone: {self.left[0]}{more}. "
            f"Its backup was kept; Undo again continues from there.{kept}"
        )


def _aside(client_dir: Path, root: Path, rel: PurePosixPath, target: Path) -> Path:
    """Move the player's current `target` into `<root>/CONFLICTS/<rel>` (a fresh name)."""
    place = _fresh(root.joinpath(CONFLICTS, *rel.parts))
    _no_link_below(client_dir, place)
    place.parent.mkdir(parents=True, exist_ok=True)
    _no_link_below(client_dir, place)
    os.replace(target, place)
    return place


def rollback(client_dir: Path, version: str) -> Undone:
    """Put the client back as it was before the update to `version`.

    Each file is one step, checkpointed in the journal (`undone`) as soon as it is
    done, so an Undo stopped by a locked file continues where it stopped. A file the
    update replaced comes back from the backup; if the player changed it after the
    update (it no longer holds the update's digest) their file is moved to
    `CONFLICTS` first. A file the update added is removed, or moved there when the
    player changed it. A step whose backup is gone counts as done only when the
    file already holds the ORIGINAL digest the journal recorded. The state file and
    the backup folder are put back and removed only when every step is done; the
    `CONFLICTS` folder is never removed.
    """
    root = _backup_root(client_dir, version)
    journal = _read_journal(root)
    if journal is None:
        raise ClientUpdateError(f"There is no backup of the client update {version} to undo.")
    undone: dict[str, Any] = journal["undone"]
    result = Undone(version, conflicts_dir=root / CONFLICTS)

    def checkpoint(key: str) -> None:
        undone[key] = True
        _write_journal(client_dir, root, journal)

    for rel_raw, sha in journal["added"].items():
        key = f"added:{rel_raw}"
        if key in undone:
            continue
        rel = safe_rel(rel_raw)
        try:
            target = _target(client_dir, rel)
            if not os.path.lexists(target):
                pass
            elif target.is_file() and _sha256(target) == sha:
                target.unlink()
            else:
                _aside(client_dir, root, rel, target)
                result.kept_changed += 1
            checkpoint(key)
        except OSError as exc:
            result.left.append(f"{rel} ({_os_sentence(exc)})")
    for rel_raw, record in journal["replaced"].items():
        key = f"replaced:{rel_raw}"
        if key in undone:
            continue
        rel = safe_rel(rel_raw)
        try:
            backup = _no_link_below(client_dir, root.joinpath(*safe_rel(record["backup"]).parts))
            target = _target(client_dir, rel)
            original, installed = record.get("original"), record.get("installed")
            if not os.path.lexists(backup):
                if original is not None and target.is_file() and _sha256(target) == original:
                    checkpoint(key)  # put back by an Undo that stopped before its checkpoint
                else:
                    result.left.append(f"{rel} (its backup is missing, so it was not put back)")
                continue
            if os.path.lexists(target) and (
                installed is None or not target.is_file() or _sha256(target) != installed
            ):
                _aside(client_dir, root, rel, target)
                result.kept_changed += 1
            if original is None:
                # An early journal never recorded the original's digest: record it from
                # the backup BEFORE it moves, so an Undo that stops after the move and
                # before its checkpoint can still tell the file was put back.
                record["original"] = _sha256(backup)
                _write_journal(client_dir, root, journal)
            _no_link_below(client_dir, backup)
            os.replace(backup, target)
            checkpoint(key)
        except OSError as exc:
            result.left.append(f"{rel} ({_os_sentence(exc)})")
    if result.left:
        return result
    previous = _state_from(journal.get("previous_state"))
    if previous is None:
        (client_dir / STATE_FILE).unlink(missing_ok=True)
    else:
        write_state(client_dir, previous)
    for child in list(root.iterdir()):
        if child.name == CONFLICTS:
            continue
        if child.is_dir() and not _is_link(child):
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        root.rmdir()  # only when nothing was kept aside
    return result


# --- After an update: the server's map data --------------------------------------------------


def _changed_map_file_sentence(name: str) -> str:
    """The one sentence both map-data questions end in, with the file that changed."""
    return (
        f"The client update changed {name}, a file the server's map data was extracted from, so "
        "the server's map data no longer matches your client. The server keeps working with the "
        "map data it has; extract it again from the updated client to use what the update changed."
    )


def _map_archive_sentence(client_dir: Path, data_dir: Path) -> str | None:
    """The map archive the last extraction read changed since, by size and date (T837).

    `surgical.read_sidecar()` records what the extract stage actually read; a
    replaced archive with the same size and date is not caught here on purpose —
    the stage's own hash catches that one, and this question is asked by the UI
    on every draw. None: no sidecar (an install from before it), or a match.
    """
    from yulon.catalog.families import surgical

    recorded = surgical.read_sidecar(data_dir)
    if recorded is None:
        return None
    name, size, mtime_ns = recorded
    try:
        stat = client_dir.joinpath(*name.split("/")).stat()
    except OSError:
        return None
    if stat.st_size == size and stat.st_mtime_ns == mtime_ns:
        return None
    return _changed_map_file_sentence(PurePosixPath(name).name)


def map_data_sentence(entry: Any, server_dir: Path, client_dir: Path) -> str | None:
    """Say so when the server's map data was extracted from this client before it changed.

    The extract stage's own evidence (`extract.read_evidence`), compared on the
    client facts it records (the folder, and the required file's size and date)
    against what the folder holds now. T837: the map archive the extraction read
    (`surgical.SIDECAR_NAME`) is part of the question too — its size and date, not
    its bytes, because this is drawn by the UI and reading ~2 GB would freeze it;
    the extract stage's hash is what catches a replacement that keeps both.
    None: no evidence, another folder, or a match.
    Never runs anything: re-extracting takes hours and is the player's call.
    """
    from yulon.catalog.families import extract

    native = entry.install.native
    block = native.azerothcore.client_extract if native and native.azerothcore else None
    if block is None:
        return None
    evidence = extract.read_evidence(server_dir / "data")
    if evidence is None:
        return None
    now = extract.expected_evidence(block.extract, client_dir, block.client.required_file)
    if evidence.client_path != now.client_path:
        return None
    if not (now.client_facts_complete and extract._same_required_file(evidence, now)):
        return _changed_map_file_sentence(block.client.required_file)
    return _map_archive_sentence(client_dir, server_dir / "data")
