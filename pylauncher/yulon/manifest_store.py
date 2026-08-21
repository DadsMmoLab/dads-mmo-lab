"""Loading manifests from disk and refreshing them from GitHub (roadmap 2.3).

Game-agnostic on purpose (style-guide §4): a `ManifestStore` reads one game's
`manifests/<game>/` tree (the bundled copy, or the refreshed cache under
`platform.config_dir()`), and a `ManifestFetcher` mirrors that tree from a raw
GitHub base URL into the cache with ETag/`If-None-Match` revalidation
(README §11). Per-game `controller_<acronym>/modules.py` binds these to its
game id and adds the apply step; nothing here knows what a module *does*.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from yulon.log import get_logger
from yulon.manifest import Index, Manifest, ManifestType, parse_index, parse_manifest

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
    """Read-only view of one game's manifest tree: indexes + per-item files."""

    def __init__(self, root: Path, game: str) -> None:
        self.root = root
        self.game = game

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

    def load_index(self, kind: ManifestType) -> Index:
        """Parse the family index; raises `ManifestError` if missing/invalid."""
        index = parse_index(_read_json(self.index_path(kind)))
        if index.game != self.game or index.type != kind:
            raise ManifestError(
                f"{self.index_path(kind)} is for {index.game}/{index.type}, "
                f"expected {self.game}/{kind}"
            )
        return index

    def load(self, kind: ManifestType, item_id: str) -> Manifest:
        """Parse one item; raises `ManifestError` if missing/invalid or mismatched."""
        path = self.item_path(kind, item_id)
        manifest = load_manifest(path)
        if manifest.id != item_id or manifest.type != kind or manifest.game != self.game:
            raise ManifestError(
                f"{path} declares {manifest.game}/{manifest.type}/{manifest.id}, "
                f"expected {self.game}/{kind}/{item_id}"
            )
        return manifest

    def load_all(self, kind: ManifestType) -> Iterator[Manifest]:
        """Every item the family index lists, in index order."""
        for item_id in self.load_index(kind).items:
            yield self.load(kind, item_id)

    def relative_files(self, kind: ManifestType) -> list[str]:
        """Paths (relative to `<root>`) the fetcher must mirror for a family."""
        index = self.load_index(kind)
        files = [f"{self.game}/{FAMILY_FILES[kind]}.json"]
        files.extend(f"{self.game}/{FAMILY_FILES[kind]}/{item_id}.json" for item_id in index.items)
        return files


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
    """Default `HttpGet`: stdlib urllib with `If-None-Match`; 304 → empty body."""
    request = urllib.request.Request(url, headers={"User-Agent": "yulon"})
    if etag:
        request.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
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
