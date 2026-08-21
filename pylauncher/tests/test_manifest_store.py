"""Tests for `yulon.manifest_store` (roadmap 2.3: load/validate/fetch).

The store is exercised against the real bundled tree (so every shipped
manifest loads typed) and against tmp trees for the error paths; the fetcher
is driven through a fake `HttpGet`, so no network is involved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from yulon.controller_wow_wotlk import modules
from yulon.manifest_store import (
    FAMILY_FILES,
    HttpResponse,
    ManifestError,
    ManifestFetcher,
    ManifestStore,
)

BUNDLED = Path(__file__).resolve().parents[1] / "manifests"


def test_bundled_store_loads_every_family_typed() -> None:
    """Every shipped WotLK manifest loads as a typed `Manifest` via the store."""
    store = modules.store()
    total = 0
    for kind in FAMILY_FILES:
        for item in store.load_all(kind):
            assert item.type == kind and item.game == "wow-wotlk"
            total += 1
    assert total >= 40


def test_load_module_rejects_invalid_repo(tmp_path: Path) -> None:
    """Roadmap 2.3 DoD: a manifest whose repo is not an allowed source is rejected."""
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "id": "bad",
                "name": "Bad",
                "type": "module",
                "game": "wow-wotlk",
                "source": {"repo": "https://warez.example/mod.git"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="allowed"):
        modules.load_module(bad)


def test_store_errors_are_specific(tmp_path: Path) -> None:
    """Missing file, invalid JSON, and id/type/game mismatch each raise `ManifestError`."""
    store = ManifestStore(tmp_path, "wow-wotlk")
    with pytest.raises(ManifestError, match="missing"):
        store.load_index("module")
    game_dir = tmp_path / "wow-wotlk"
    game_dir.mkdir()
    (game_dir / "modules.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError, match="valid JSON"):
        store.load_index("module")
    (game_dir / "modules.json").write_text(
        json.dumps({"schema_version": 1, "game": "wow-wotlk", "type": "ale", "items": []}),
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="expected wow-wotlk/module"):
        store.load_index("module")
    (game_dir / "modules").mkdir()
    (game_dir / "modules" / "x.json").write_text(
        json.dumps({"id": "y", "name": "Y", "type": "mod", "game": "wow-wotlk"}), encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="declares wow-wotlk/mod/y"):
        store.load("module", "x")


class _FakeHttp:
    """Serves a dict of url → (etag, body); honours If-None-Match with 304."""

    def __init__(self, files: dict[str, tuple[str, bytes]]) -> None:
        self.files = files
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, url: str, etag: str | None) -> HttpResponse:
        self.calls.append((url, etag))
        if url not in self.files:
            return HttpResponse(404, None, b"")
        current_etag, body = self.files[url]
        if etag == current_etag:
            return HttpResponse(304, etag, b"")
        return HttpResponse(200, current_etag, body)


def _index(items: list[str]) -> bytes:
    return json.dumps(
        {"schema_version": 1, "game": "wow-wotlk", "type": "module", "items": items}
    ).encode()


def _item(item_id: str, name: str = "X") -> bytes:
    return json.dumps(
        {
            "id": item_id,
            "name": name,
            "type": "module",
            "game": "wow-wotlk",
            "source": {"repo": "azerothcore/mod-x"},
        }
    ).encode()


def test_fetcher_mirrors_index_and_items_then_revalidates_with_etags(tmp_path: Path) -> None:
    """First refresh downloads everything; the second is all 304s and changes nothing."""
    base = "https://example.test/manifests"
    http = _FakeHttp(
        {
            f"{base}/wow-wotlk/modules.json": ("e-idx", _index(["mod-x"])),
            f"{base}/wow-wotlk/modules/mod-x.json": ("e-x", _item("mod-x")),
        }
    )
    fetcher = ManifestFetcher(base, tmp_path, http)

    first = fetcher.refresh("wow-wotlk", "module")
    assert first.updated == ("wow-wotlk/modules.json", "wow-wotlk/modules/mod-x.json")
    assert first.unchanged == ()
    store = ManifestStore(tmp_path, "wow-wotlk")
    assert store.load("module", "mod-x").name == "X"

    second = fetcher.refresh("wow-wotlk", "module")
    assert second.updated == () and len(second.unchanged) == 2
    # The revalidation requests carried the stored ETags.
    assert http.calls[-2][1] == "e-idx" and http.calls[-1][1] == "e-x"

    # An upstream change to one file updates only that file.
    http.files[f"{base}/wow-wotlk/modules/mod-x.json"] = ("e-x2", _item("mod-x", "X2"))
    third = fetcher.refresh("wow-wotlk", "module")
    assert third.updated == ("wow-wotlk/modules/mod-x.json",)
    assert store.load("module", "mod-x").name == "X2"


def test_fetcher_refuses_a_broken_upstream_without_clobbering_the_cache(tmp_path: Path) -> None:
    """An item that fails validation raises, but the previously good files stay usable."""
    base = "https://example.test/manifests"
    http = _FakeHttp(
        {
            f"{base}/wow-wotlk/modules.json": ("e-idx", _index(["mod-x"])),
            f"{base}/wow-wotlk/modules/mod-x.json": ("e-x", _item("mod-x")),
        }
    )
    fetcher = ManifestFetcher(base, tmp_path, http)
    fetcher.refresh("wow-wotlk", "module")

    http.files[f"{base}/wow-wotlk/modules.json"] = ("e-idx2", _index(["mod-x", "mod-gone"]))
    with pytest.raises(ManifestError, match="HTTP 404"):
        fetcher.refresh("wow-wotlk", "module")
    # mod-x is still loadable from the cache.
    assert ManifestStore(tmp_path, "wow-wotlk").load("module", "mod-x").id == "mod-x"


def test_a_truncated_download_never_replaces_a_good_cached_file(tmp_path: Path) -> None:
    """Validation happens BEFORE the replace, so the previous good cache survives."""
    base = "https://example.test/manifests"
    http = _FakeHttp(
        {
            f"{base}/wow-wotlk/modules.json": ("e-idx", _index(["mod-x"])),
            f"{base}/wow-wotlk/modules/mod-x.json": ("e-x", _item("mod-x")),
        }
    )
    fetcher = ManifestFetcher(base, tmp_path, http)
    fetcher.refresh("wow-wotlk", "module")
    good = (tmp_path / "wow-wotlk" / "modules" / "mod-x.json").read_bytes()

    # Upstream now serves half a file under a new ETag.
    http.files[f"{base}/wow-wotlk/modules/mod-x.json"] = ("e-x-bad", b'{"id": "mod-x", "nam')
    with pytest.raises(ManifestError, match="not a manifest"):
        fetcher.refresh("wow-wotlk", "module")
    assert (tmp_path / "wow-wotlk" / "modules" / "mod-x.json").read_bytes() == good
    assert ManifestStore(tmp_path, "wow-wotlk").load("module", "mod-x").name == "X"

    # A well-formed JSON file that is not a manifest is refused just the same.
    http.files[f"{base}/wow-wotlk/modules.json"] = ("e-idx-bad", b'{"hello": "world"}')
    with pytest.raises(ManifestError, match="not a manifest"):
        fetcher.refresh("wow-wotlk", "module")
    assert ManifestStore(tmp_path, "wow-wotlk").load_index("module").items == ("mod-x",)
