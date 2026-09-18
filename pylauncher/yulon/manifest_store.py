"""Loading manifests from disk and refreshing them from GitHub (roadmap 2.3).

Game-agnostic on purpose (style-guide §4): a `ManifestStore` reads one game's
`manifests/<game>/` tree (the bundled copy, or the refreshed cache under
`platform.config_dir()`), and a `ManifestFetcher` mirrors that tree from a raw
GitHub base URL into the cache with ETag/`If-None-Match` revalidation
(README §11). Per-game `controller_<acronym>/modules.py` binds these to its
game id and adds the apply step; nothing here knows what a module *does*.

A store may be given a second, USER tree over the first (`user_root`): manifests
this app derived from a link or a folder the user supplied, written by
`yulon.module_source` under the config directory. The layout is identical, the
bundled layer always wins a contested id, and the fetcher never reads or writes
there — it mirrors the project's tree into its own cache root and nothing else.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from yulon.log import get_logger
from yulon.manifest import Index, Manifest, ManifestType, parse_index, parse_manifest
from yulon.platform import verify_context

logger = get_logger(__name__)

# Index filename per family (`<type>` → `<family>.json`, and the item subdir).
FAMILY_FILES: dict[ManifestType, str] = {
    "module": "modules",
    "ale": "ale",
    "mod": "mods",
    "keg": "kegs",
}

_ETAG_SUFFIX = ".etag"
_TIMEOUT_SECONDS = 20.0


class ManifestError(RuntimeError):
    """A manifest or index could not be read/validated (wraps the cause message)."""


class ManifestStore:
    """Read-only view of one game's manifest tree: indexes + per-item files.

    Optionally TWO trees. `root` is what the app ships (or the refreshed cache
    over it); `user_root`, when given, is a second tree under the app's own
    config directory holding manifests this app DERIVED from a link or a folder
    the user chose (`yulon.module_source`). Its layout is byte-for-byte this
    class's own — `<user_root>/<game>/<family>.json` plus
    `<user_root>/<game>/<family>/<id>.json` — so the user layer is read by the
    class that already exists rather than by a second parser.

    **A user file can never shadow a shipped id.** The bundled layer comes first
    in `load_all()` and wins in `load()`, and a user file naming a shipped id is
    skipped with a warning that names the path. A shipped manifest is reviewed —
    its repo passed README §3a, its conf keys and SQL steps were written by hand;
    a derived one was built from a repository basename. Letting the second
    replace the first would be a way to substitute an arbitrary repo for a
    catalogued module by dropping a file into a directory, and the file can
    arrive by hand as easily as by a press.
    """

    def __init__(self, root: Path, game: str, user_root: Path | None = None) -> None:
        self.root = root
        self.game = game
        self.user_root = user_root

    @property
    def game_dir(self) -> Path:
        """`<root>/<game>/`."""
        return self.root / self.game

    def index_path(self, kind: ManifestType) -> Path:
        """`<root>/<game>/<family>.json`."""
        return self.game_dir / f"{FAMILY_FILES[kind]}.json"

    def item_path(self, kind: ManifestType, item_id: str) -> Path:
        """`<root>/<game>/<family>/<id>.json`."""
        return self.game_dir / FAMILY_FILES[kind] / f"{item_id}.json"

    def user_index_path(self, kind: ManifestType) -> Path:
        """`<user_root>/<game>/<family>.json`; raises if there is no user layer."""
        return self._user_game_dir / f"{FAMILY_FILES[kind]}.json"

    def user_item_path(self, kind: ManifestType, item_id: str) -> Path:
        """`<user_root>/<game>/<family>/<id>.json`; raises if there is no user layer."""
        return self._user_game_dir / FAMILY_FILES[kind] / f"{item_id}.json"

    @property
    def _user_game_dir(self) -> Path:
        if self.user_root is None:
            raise ManifestError("this store has no user layer")
        return self.user_root / self.game

    def load_index(self, kind: ManifestType) -> Index:
        """Parse the family index; raises `ManifestError` if missing/invalid."""
        return self._index_at(self.index_path(kind), kind)

    def _index_at(self, path: Path, kind: ManifestType) -> Index:
        index = parse_index(_read_json(path))
        if index.game != self.game or index.type != kind:
            raise ManifestError(
                f"{path} is for {index.game}/{index.type}, expected {self.game}/{kind}"
            )
        return index

    def user_index_items(self, kind: ManifestType) -> tuple[str, ...]:
        """The ids the user index lists, or `()` when there is no user layer or no index.

        A MISSING index is an empty second layer, never an error: on a machine
        that has never derived a module there is nothing to read, and raising
        would draw `!! could not load modules: …` over the whole tab and take
        every shipped module down with it. An index that IS there and does not
        parse is a real error — something wrote a file this app reads back, and
        answering "absent" would hide a custom module the user believes is there.
        """
        if self.user_root is None:
            return ()
        path = self.user_index_path(kind)
        if not path.is_file():
            return ()
        return self._index_at(path, kind).items

    def load(self, kind: ManifestType, item_id: str) -> Manifest:
        """Parse one item; raises `ManifestError` if missing/invalid or mismatched.

        The bundled file when the bundled index lists the id, the user file
        otherwise. With no user layer this is what it always was, byte for byte.
        """
        path = self.item_path(kind, item_id)
        if self.user_root is not None and item_id not in self.load_index(kind).items:
            path = self.user_item_path(kind, item_id)
        return self._load_at(path, kind, item_id)

    def _load_at(self, path: Path, kind: ManifestType, item_id: str) -> Manifest:
        manifest = load_manifest(path)
        if manifest.id != item_id or manifest.type != kind or manifest.game != self.game:
            raise ManifestError(
                f"{path} declares {manifest.game}/{manifest.type}/{manifest.id}, "
                f"expected {self.game}/{kind}/{item_id}"
            )
        return manifest

    def load_all(
        self, kind: ManifestType, *, skipped: list[str] | None = None
    ) -> Iterator[Manifest]:
        """Every bundled item in index order, then every user item that shadows none.

        A BUNDLED item that will not load raises. It is a file this app ships
        and tests, so it failing is an app bug, and a shipped catalog that does
        not parse is not a condition to render politely around.

        A USER item that will not load is skipped, logged, and appended to
        `skipped` as one sentence naming it. That layer holds manifests this app
        DERIVED from a link or a folder the user chose, and both callers force
        this generator whole inside one `try` at FAMILY scope -- so before T46 a
        single unparseable file there replaced every shipped module of the
        family with one `!!` line, and in `module_updates()` dropped every
        branch silently, which reads on the tab as "nothing to update".

        The store already made this argument one method up, in
        `user_index_items()`, and never applied it to the items the index lists.
        The user INDEX itself still raises: it is the list of what to load, and
        answering "absent" for a file that IS there would hide a custom module
        the user believes in.
        """
        bundled = self.load_index(kind).items
        for item_id in bundled:
            yield self._load_at(self.item_path(kind, item_id), kind, item_id)
        shipped = set(bundled)
        for item_id in self.user_index_items(kind):
            if item_id in shipped:
                logger.warning(
                    f"ignoring {self.user_item_path(kind, item_id)}: "
                    f"{item_id} is a {kind} this app ships, and a user file never replaces one"
                )
                continue
            path = self.user_item_path(kind, item_id)
            try:
                yield self._load_at(path, kind, item_id)
            except USER_ITEM_UNREADABLE as exc:
                reason = _skip_reason(path, exc)
                logger.warning(f"skipping {reason}")
                if skipped is not None:
                    skipped.append(USER_ITEM_SKIPPED.format(item_id=item_id, reason=reason))

    def relative_files(self, kind: ManifestType) -> list[str]:
        """Paths (relative to `<root>`) the fetcher must mirror for a family."""
        index = self.load_index(kind)
        files = [f"{self.game}/{FAMILY_FILES[kind]}.json"]
        files.extend(f"{self.game}/{FAMILY_FILES[kind]}/{item_id}.json" for item_id in index.items)
        return files


USER_ITEM_SKIPPED = "{item_id} was skipped: {reason}"
"""One user manifest that would not load, said where the family is drawn.

Per ITEM, not per family. The id leads because that is what the user typed or
picked; `_skip_reason()` supplies the file and what was wrong with it.
"""

USER_ITEM_UNREADABLE = (ManifestError, ValidationError, OSError, UnicodeDecodeError)
"""Every way reading ONE user manifest fails without the app being at fault.

Caught at the skip site in `load_all()` and deliberately not inside
`load_manifest()`, whose four `controller_wow_*/modules.py` callers document
that they get `pydantic.ValidationError` and handle it themselves.

`ManifestError` is this module's own wrapper -- a missing file, JSON that will
not decode, or a manifest declaring the wrong game/type/id. It is NOT the whole
list: `_read_json()` wraps `FileNotFoundError` and `json.JSONDecodeError` and
nothing else, so a permission error or a directory in the file's place arrives
as a bare `OSError`, a file that is not UTF-8 as a `UnicodeDecodeError`, and --
the realistic one, because this app persisted these files itself and an older
build's shape is the likeliest bad file on disk -- valid JSON that fails the
schema arrives as `ValidationError`. Each of those is one user-derived file,
and each of them used to cost the whole family.
"""


def _skip_reason(path: Path, exc: Exception) -> str:
    """One line naming the file and what was wrong with it.

    ONE line because the tab's report box is read a refusal per line, and a
    `pydantic.ValidationError` spells itself over five or more of them.

    The path is prepended only when it is not in the message already:
    `ManifestError` and `OSError` both name the file they failed on, and a
    `ValidationError` names the FIELD and never the file -- which, unprefixed,
    is a complaint the user cannot trace back to anything.
    """
    reason = " ".join(str(exc).split())
    return reason if str(path) in reason else f"{path}: {reason}"


def load_manifest(path: Path) -> Manifest:
    """Read + validate a single manifest file into a typed `Manifest`."""
    return parse_manifest(_read_json(path))


def _read_json(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        raise ManifestError(f"manifest file missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{path} is not valid JSON: {exc}") from exc


# ---------------------------------------------------------------- fetching


@dataclass(frozen=True)
class HttpResponse:
    """What the fetcher needs from one GET: status, the ETag (if any), the body."""

    status: int
    etag: str | None
    body: bytes


class HttpGet(Protocol):
    """Minimal HTTP GET seam so tests never touch the network."""

    def __call__(self, url: str, etag: str | None) -> HttpResponse: ...


def urllib_get(url: str, etag: str | None) -> HttpResponse:
    """Default `HttpGet`: stdlib urllib with `If-None-Match`; 304 → empty body.

    The context comes from `platform.verify_context()` rather than urllib's
    default because these bytes decide what gets cloned and applied to a
    server: a manifest names a git repo and the SQL/DBC steps to run from it.
    urllib's default reads OpenSSL's snapshot of the Windows root store, which
    on a fresh install is a fraction of what the OS would fetch on demand
    (measured, 2026-08-22: 18 roots) — so a refresh that "just works" against
    raw.githubusercontent.com today is luck about which chain that host uses,
    not a property of the code.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "yulon"})
    if etag:
        request.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(
            request, timeout=_TIMEOUT_SECONDS, context=verify_context()
        ) as resp:
            return HttpResponse(resp.status, resp.headers.get("ETag"), resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return HttpResponse(304, etag, b"")
        raise


@dataclass(frozen=True)
class RefreshResult:
    """Per-file outcome of a refresh: what changed, what was already current."""

    updated: tuple[str, ...]
    unchanged: tuple[str, ...]


def _parse_index_bytes(body: bytes) -> object:
    """Validate raw index bytes before they are allowed to replace a cached file."""
    return parse_index(json.loads(body.decode("utf-8")))


def _parse_manifest_bytes(body: bytes) -> object:
    """Validate raw manifest bytes before they are allowed to replace a cached file."""
    return parse_manifest(json.loads(body.decode("utf-8")))


class ManifestFetcher:
    """Mirror a game's manifest family from `base_url` into `cache_root`, with ETags.

    `base_url` is the raw prefix that `<game>/<family>.json` is joined onto
    (e.g. `https://raw.githubusercontent.com/<owner>/<repo>/<branch>/pylauncher/manifests`).
    Each cached file gets a `<file>.etag` sidecar; a 304 keeps the cached copy.
    A refresh is atomic per file (write to `.tmp`, then replace) and validates
    the downloaded index/items before they are used, so a half-fetched or
    broken upstream never overwrites a good cache.
    """

    def __init__(self, base_url: str, cache_root: Path, http: HttpGet = urllib_get) -> None:
        self.base_url = base_url.rstrip("/")
        self.cache_root = cache_root
        self.http = http

    def refresh(self, game: str, kind: ManifestType) -> RefreshResult:
        """Fetch the family index, then every item it lists. Raises on HTTP/validation error.

        Every download is parsed BEFORE it replaces the cached file, so a
        truncated or invalid upstream response leaves the previous good cache
        untouched - what this module always promised (review finding,
        2026-08-21).
        """
        index_rel = f"{game}/{FAMILY_FILES[kind]}.json"
        updated: list[str] = []
        unchanged: list[str] = []
        self._fetch_one(index_rel, updated, unchanged, _parse_index_bytes)
        index = parse_index(_read_json(self.cache_root / index_rel))
        for item_id in index.items:
            rel = f"{game}/{FAMILY_FILES[kind]}/{item_id}.json"
            self._fetch_one(rel, updated, unchanged, _parse_manifest_bytes)
        logger.info(
            f"manifests refreshed: {game}/{kind} updated={len(updated)} unchanged={len(unchanged)}"
        )
        return RefreshResult(tuple(updated), tuple(unchanged))

    def _fetch_one(
        self,
        rel: str,
        updated: list[str],
        unchanged: list[str],
        validate: Callable[[bytes], object],
    ) -> None:
        target = self.cache_root / rel
        etag_file = target.with_name(target.name + _ETAG_SUFFIX)
        cached_etag = etag_file.read_text(encoding="utf-8").strip() if etag_file.is_file() else None
        resp = self.http(f"{self.base_url}/{rel}", cached_etag if target.is_file() else None)
        if resp.status == 304 and target.is_file():
            unchanged.append(rel)
            return
        if resp.status != 200:
            raise ManifestError(f"GET {rel} returned HTTP {resp.status}")
        try:
            validate(resp.body)
        except (ValueError, ManifestError) as exc:
            raise ManifestError(f"GET {rel} returned data that is not a manifest: {exc}") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(resp.body)
        tmp.replace(target)
        if resp.etag:
            etag_file.write_text(resp.etag, encoding="utf-8")
        elif etag_file.exists():
            etag_file.unlink()
        updated.append(rel)
