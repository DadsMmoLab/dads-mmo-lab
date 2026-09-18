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


def test_one_bad_user_item_costs_its_own_row_and_never_the_family(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A user manifest that will not load is skipped and named; the shipped rows stay (T46).

    The store already argued this one method up, for the user INDEX:

        raising would draw `!! could not load modules: ...` over the whole tab
        and take every shipped module down with it.

    It was never applied to the items that index lists. `load_all()` is forced
    whole by both of its callers and both catch at FAMILY scope, so one
    unparseable file in the layer this app DERIVES from user input replaced
    every shipped module of that family with a single `!!` line.

    The line this draws is who wrote the file, not how bad it is: a bundled
    manifest that will not load is an app bug and stays loud (asserted below),
    because a shipped catalog that does not parse is not a condition to render
    politely around.
    """
    bundled, user = tmp_path / "bundled", tmp_path / "user"
    _write_layer(bundled, ["mod-shipped", "mod-other"])
    # The four bad ones are listed BEFORE `mod-custom`, so a pass that stops at
    # the first failure loses the good row rather than merely the bad ones.
    _write_layer(user, ["mod-shape", "mod-undecodable", "mod-foreign", "mod-garbage", "mod-custom"])
    # A real file on disk for each way a user item goes wrong -- NOT a `Manifest`
    # injected into a builder, which is coverage of a row nothing can produce.
    user_items = user / "wow-wotlk" / "modules"
    # Valid JSON that fails the SCHEMA, which `_read_json()` never wraps: it comes
    # out of pydantic. This is the likeliest bad file on a real machine, because
    # the app wrote these itself and an older build's shape is what ages badly.
    (user_items / "mod-shape.json").write_bytes(
        json.dumps(
            {
                "id": "mod-shape",
                "name": 5,  # a `str` field given an int
                "type": "module",
                "game": "wow-wotlk",
                "source": {},
            }
        ).encode()
    )
    # Bytes that are not UTF-8: `open(encoding="utf-8")` raises before `json` sees
    # them, so this one is a `UnicodeDecodeError` and not a `ManifestError` either.
    (user_items / "mod-undecodable.json").write_bytes(b'{"id": "\xff\xfe"}')
    (user_items / "mod-foreign.json").write_bytes(
        json.dumps(
            {
                "id": "mod-foreign",
                "name": "MOD-FOREIGN",
                "type": "module",
                "game": "wow-tbc",  # a manifest for a different game
                "source": {"repo": "azerothcore/mod-x"},
            }
        ).encode()
    )
    (user_items / "mod-garbage.json").write_text("{not json", encoding="utf-8")

    store = ManifestStore(bundled, "wow-wotlk", user_root=user)
    skipped: list[str] = []
    with caplog.at_level("WARNING"):
        got = [m.id for m in store.load_all("module", skipped=skipped)]

    # The family survives: every shipped row, plus the user row that does load.
    assert got == ["mod-shipped", "mod-other", "mod-custom"]
    # Every failure is named, each in its own sentence, each naming its id, and
    # each on ONE line -- a `ValidationError` spells itself over five, and the
    # report box this feeds is read a refusal per line.
    assert len(skipped) == 4
    assert all("\n" not in line for line in skipped)
    assert any("mod-foreign" in line and "wow-tbc" in line for line in skipped)
    assert any("mod-garbage" in line and "JSON" in line for line in skipped)
    # Each reason is the one only ITS failure produces, not a phrase they share:
    # the schema complaint names the field, the decode failure names the codec.
    assert any("mod-shape" in line and "name" in line for line in skipped)
    assert any("mod-undecodable" in line and "utf-8" in line for line in skipped)
    # And every one names the file, which a bare `ValidationError` does not.
    for item_id in ("mod-shape", "mod-undecodable", "mod-foreign", "mod-garbage"):
        path = str(user_items / f"{item_id}.json")
        assert any(path in line for line in skipped), item_id
    # And logged, because a silently ignored file is a bug report nobody can answer.
    assert "mod-garbage" in caplog.text

    # A caller that does not ask for the skips still gets the surviving family
    # rather than an exception -- the panel and `module_updates()` differ only
    # in whether they have somewhere to print them.
    assert [m.id for m in store.load_all("module")] == ["mod-shipped", "mod-other", "mod-custom"]

    # The bundled pass is NOT softened, and not for one exception type either:
    # this app's own file failing is an app bug however it fails.
    bundled_items = bundled / "wow-wotlk" / "modules"
    (bundled_items / "mod-other.json").write_bytes(
        json.dumps({"id": "mod-other", "name": 5, "type": "module", "game": "wow-wotlk"}).encode()
    )
    with pytest.raises(ValidationError):
        list(store.load_all("module", skipped=[]))
    (bundled_items / "mod-other.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError, match="valid JSON"):
        list(store.load_all("module", skipped=[]))


class _AskedBehind:
    """A `BehindReader` that records the (folder, branch) pair it was asked about."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str | None]] = []

    def commits_behind(self, dest: Path, branch: str | None) -> int | None:
        self.asked.append((dest.name, branch))
        return 0


def test_a_bad_user_manifest_no_longer_costs_the_branches_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`module_updates()` keeps reading branches past a manifest that will not load (T46).

    This is the claim item 1 makes "for free", proved on the real seam rather
    than asserted: `modules.module_updates()` builds its branch table by forcing
    `store().load_all("module")` inside one `try`. The dict is built OUTSIDE that
    try, so entries added before a raise always survived -- the loss was the
    user items AFTER the bad one, silently, with no error drawn anywhere. The
    branch matters because it is the ref an update fetches, and a module counted
    against the wrong ref reads as up to date when it is not.
    """
    bundled, user = tmp_path / "bundled", tmp_path / "user"
    server = tmp_path / "server"
    _write_layer(bundled, ["mod-shipped"])
    _write_layer(user, ["mod-aaa-bad", "mod-zzz-good"])
    items = user / "wow-wotlk" / "modules"
    (items / "mod-aaa-bad.json").write_text("{not json", encoding="utf-8")
    (items / "mod-zzz-good.json").write_bytes(
        json.dumps(
            {
                "id": "mod-zzz-good",
                "name": "Z",
                "type": "module",
                "game": "wow-wotlk",
                "source": {"repo": "azerothcore/mod-x", "branch": "wotlk"},
            }
        ).encode()
    )
    # Both are installed on disk; the enumeration is directory names, not the catalog.
    for name in ("mod-shipped", "mod-zzz-good"):
        (server / "modules" / name / ".git").mkdir(parents=True)

    monkeypatch.setattr(
        modules, "store", lambda *a, **k: ManifestStore(bundled, "wow-wotlk", user_root=user)
    )
    git = _AskedBehind()
    modules.module_updates(server, git=git)

    # The bad file sorts FIRST in the user index, so before T46 its raise ended the
    # loop and `mod-zzz-good` was counted against `origin HEAD` instead of `wotlk`.
    assert ("mod-zzz-good", "wotlk") in git.asked
    assert ("mod-shipped", None) in git.asked
