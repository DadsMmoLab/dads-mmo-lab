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
    """Every id each family index lists loads as a typed `Manifest`, and none is skipped.

    The count is taken from the INDEX rather than compared against a number
    written here. `assert total >= 40` was the floor until 2026-09-13, and it
    was doing two jobs badly: it guarded against the loop iterating nothing,
    which is worth guarding, and it encoded "we ship about forty things", which
    is not a fact about correctness and rots on every catalog edit. Dropping
    two broken entries in one evening took the total 41 -> 39 and tripped it,
    having warned one removal earlier that it would.

    Comparing against the index instead is strictly stronger: a truncated index
    and a manifest that will not load both still fail, and adding or removing a
    module never does.
    """
    store = modules.store()
    for kind in FAMILY_FILES:
        listed = list(store.load_index(kind).items)
        for item in store.load_all(kind):
            assert item.type == kind and item.game == "wow-wotlk"
        # The index cannot check itself: `load_all()` is DRIVEN by it, so a
        # truncated index yields fewer items and every index-vs-loaded
        # comparison still agrees. Measured -- dropping the last id from the
        # module index left such a comparison green (T52 mutation M1). The
        # directory is the independent witness.
        on_disk = sorted(
            f.stem for f in (BUNDLED / "wow-wotlk" / FAMILY_FILES[kind]).glob("*.json")
        )
        assert sorted(listed) == on_disk, (
            f"the {kind} index lists {sorted(listed)} but {FAMILY_FILES[kind]}/ holds {on_disk} — "
            f"a manifest was added or removed without its index entry"
        )


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


# -- the user layer --------------------------------------------------------


def _write_layer(root: Path, items: list[str]) -> None:
    """A manifest tree at `root/wow-wotlk/`: the modules index plus one file each."""
    game_dir = root / "wow-wotlk"
    (game_dir / "modules").mkdir(parents=True, exist_ok=True)
    (game_dir / "modules.json").write_bytes(_index(items))
    for item_id in items:
        (game_dir / "modules" / f"{item_id}.json").write_bytes(_item(item_id, item_id.upper()))


def test_user_items_follow_bundled_items_and_a_user_file_never_shadows_a_shipped_id(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Two layers, one order, and the bundled one always wins the name.

    The user layer holds modules this app DERIVED from a link or a folder the
    user chose; the bundled layer is what the project ships. A user file that
    claims a shipped id would replace a reviewed manifest — its repo, its conf
    keys, its SQL — with one this app wrote from a basename, and the file can
    arrive by hand as easily as by a press. So it is skipped, and the skip is
    logged with the path, because a silently ignored file is a bug report nobody
    can answer.
    """
    bundled, user = tmp_path / "bundled", tmp_path / "user"
    _write_layer(bundled, ["mod-shipped", "mod-other"])
    _write_layer(user, ["mod-shipped", "mod-custom"])

    store = ManifestStore(bundled, "wow-wotlk", user_root=user)

    with caplog.at_level("WARNING"):
        assert [m.id for m in store.load_all("module")] == [
            "mod-shipped",
            "mod-other",
            "mod-custom",
        ]
    assert str(user / "wow-wotlk" / "modules" / "mod-shipped.json") in caplog.text

    # `load()` prefers the bundled file for a shadowed id and reaches the user
    # layer for one the bundled index does not list.
    assert store.load("module", "mod-shipped").name == "MOD-SHIPPED"
    assert (bundled / "wow-wotlk" / "modules" / "mod-shipped.json").read_bytes() == _item(
        "mod-shipped", "MOD-SHIPPED"
    )
    assert store.load("module", "mod-custom").name == "MOD-CUSTOM"

    # Without a user root the store is exactly what it was: one layer.
    assert [m.id for m in ManifestStore(bundled, "wow-wotlk").load_all("module")] == [
        "mod-shipped",
        "mod-other",
    ]


def test_a_missing_user_index_is_an_empty_layer_and_a_broken_one_is_an_error(
    tmp_path: Path,
) -> None:
    """Nothing persisted yet is the ordinary first start, not a failure.

    The distinction matters because the tab draws a `ManifestError` as
    `!! could not load modules: …` with no list at all. On a machine that has
    never used the feature there is no user index, and that must not take the
    shipped modules down with it. A user index that IS there and does not parse
    is a real error: something wrote a file this app reads back, and pretending
    it is absent would hide a custom module the user believes is installed.
    """
    bundled, user = tmp_path / "bundled", tmp_path / "user"
    _write_layer(bundled, ["mod-other"])

    store = ManifestStore(bundled, "wow-wotlk", user_root=user)
    assert [m.id for m in store.load_all("module")] == ["mod-other"]
    assert store.load_all("ale") is not None  # a family with no layer at all

    (user / "wow-wotlk").mkdir(parents=True)
    (user / "wow-wotlk" / "modules.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError, match="valid JSON"):
        list(store.load_all("module"))

    (user / "wow-wotlk" / "modules.json").write_bytes(
        json.dumps({"schema_version": 1, "game": "wow-wotlk", "type": "ale", "items": []}).encode()
    )
    with pytest.raises(ManifestError, match="expected wow-wotlk/module"):
        list(store.load_all("module"))
