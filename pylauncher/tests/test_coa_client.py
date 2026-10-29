"""T825: keeping the player's Conquest of Azeroth client current (`yulon.coa_client`).

No network: every request goes to `_Site`, a fake `client_packs.Opener` serving
bytes by URL with single `bytes=N-` ranges.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from yulon import coa_client
from yulon.coa_client import ClientUpdateError, ManifestError, PlanItem

MANIFEST_URL = "https://launcher-api.example/downloads/client/latest.json"
OBJECTS = "https://launcher-api.example/downloads/client/objects/"
HOSTS = frozenset({"launcher-api.example"})


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class _Response:
    status: int
    headers: dict[str, str]
    body: bytes
    cut_after: int | None = None
    _pos: int = 0

    def read1(self, amount: int, /) -> bytes:
        if self.cut_after is not None and self._pos >= self.cut_after:
            raise ConnectionResetError("connection reset by peer")
        end = min(len(self.body), self._pos + min(amount, 4096))
        if self.cut_after is not None:
            end = min(end, self.cut_after)
        data = self.body[self._pos : end]
        self._pos = end
        return data

    def getheader(self, name: str, default: str | None = None, /) -> str | None:
        return self.headers.get(name, default)

    def close(self) -> None:
        return None


@dataclass
class _Site:
    files: dict[str, bytes]
    requests: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    cut_next: dict[str, int] = field(default_factory=dict)

    def __call__(
        self,
        url: str,
        watcher: object,
        *,
        method: str,
        headers: Mapping[str, str],
        hosts: frozenset[str],
    ) -> _Response:
        assert method == "GET" and hosts == HOSTS
        self.requests.append((url, dict(headers)))
        if url not in self.files:
            return _Response(404, {}, b"")
        data = self.files[url]
        start, status, reply = 0, 200, {}
        wanted = headers.get("Range")
        if wanted:
            start = int(wanted.removeprefix("bytes=").removesuffix("-"))
            status = 206
            reply["Content-Range"] = f"bytes {start}-{len(data) - 1}/{len(data)}"
        return _Response(status, reply, data[start:], cut_after=self.cut_next.pop(url, None))

    def object_gets(self) -> list[tuple[str, dict[str, str]]]:
        return [r for r in self.requests if r[0].startswith(OBJECTS)]


def _manifest(files: Mapping[str, bytes], version: str = "2026-10-28-01") -> bytes:
    return json.dumps(
        {
            "schema": 1,
            "version": version,
            "publishedAt": "2026-10-28T13:51:18.879Z",
            "files": [
                {"path": path, "size": len(data), "sha256": _sha(data)}
                for path, data in files.items()
            ],
        }
    ).encode()


def _site(files: Mapping[str, bytes], version: str = "2026-10-28-01") -> _Site:
    served = {MANIFEST_URL: _manifest(files, version)}
    served.update({OBJECTS + _sha(data): data for data in files.values()})
    return _Site(served)


def _client(tmp_path: Path, files: Mapping[str, bytes]) -> Path:
    root = tmp_path / "client"
    root.mkdir()
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def _update(
    client: Path, site: _Site, *, replace_edited: bool = False, **kw: object
) -> coa_client.Outcome:
    manifest = coa_client.fetch_manifest(MANIFEST_URL, hosts=HOSTS, opener=site)
    items = coa_client.plan(client, manifest, coa_client.read_state(client))
    return coa_client.apply(
        client,
        manifest,
        items,
        objects_url=OBJECTS,
        hosts=HOSTS,
        replace_edited=replace_edited,
        opener=site,
        free_bytes=lambda _p: 10**12,
        **kw,  # type: ignore[arg-type]
    )


def _actions(items: list[PlanItem]) -> dict[str, str]:
    return {item.path: item.action for item in items}


# --- the manifest ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../outside.dll",
        "Data/../../outside.dll",
        "C:/Windows/evil.dll",
        "Data\\patch.MPQ",
        "Data/./patch.MPQ",
        "Data//patch.MPQ",
        "a\x00b",
        "NUL.txt",
        "Data/patch.MPQ.",
        "a:stream",
        ".yulon-client-update.json",
        "Data/patch.MPQ.yulon-part",
    ],
)
def test_a_path_that_could_leave_the_client_folder_is_refused(path: str) -> None:
    raw = json.dumps(
        {"schema": 1, "version": "v1", "files": [{"path": path, "size": 1, "sha256": "a" * 64}]}
    ).encode()
    with pytest.raises(ManifestError):
        coa_client.parse_manifest(raw)


def test_a_traversal_path_is_refused_before_any_file_is_written(tmp_path: Path) -> None:
    client = _client(tmp_path, {"Ascension.exe": b"old"})
    site = _site({"Ascension.exe": b"new"})
    site.files[MANIFEST_URL] = json.dumps(
        {
            "schema": 1,
            "version": "v2",
            "files": [
                {"path": "Ascension.exe", "size": 3, "sha256": _sha(b"new")},
                {"path": "../escaped.dll", "size": 3, "sha256": _sha(b"new")},
            ],
        }
    ).encode()
    with pytest.raises(ManifestError):
        _update(client, site)
    assert (client / "Ascension.exe").read_bytes() == b"old"
    assert not (tmp_path / "escaped.dll").exists()
    assert site.object_gets() == []


@pytest.mark.parametrize(
    "change",
    [
        {"schema": 2},
        {"schema": True},
        {"version": "a/b"},
        {"version": ""},
        {"files": {}},
    ],
)
def test_a_manifest_of_an_unknown_shape_is_refused(change: dict[str, object]) -> None:
    raw = {"schema": 1, "version": "v1", "publishedAt": None, "files": []}
    raw.update(change)
    with pytest.raises(ManifestError):
        coa_client.parse_manifest(json.dumps(raw).encode())


@pytest.mark.parametrize("digest", ["a" * 63, "g" * 64, 5])
def test_a_file_without_a_64_hex_sha256_is_refused(digest: object) -> None:
    raw = {"schema": 1, "version": "v1", "files": [{"path": "a", "size": 1, "sha256": digest}]}
    with pytest.raises(ManifestError):
        coa_client.parse_manifest(json.dumps(raw).encode())


def test_two_paths_differing_only_in_case_are_refused() -> None:
    files = [{"path": p, "size": 1, "sha256": "a" * 64} for p in ("Data/x.MPQ", "data/X.mpq")]
    with pytest.raises(ManifestError, match="twice"):
        coa_client.parse_manifest(
            json.dumps({"schema": 1, "version": "v1", "files": files}).encode()
        )


def test_a_manifest_over_16_mb_is_not_read_past_the_cap() -> None:
    big = b" " * (coa_client.MANIFEST_MAX_BYTES + 10)
    site = _Site({MANIFEST_URL: big})
    with pytest.raises(ManifestError, match="larger"):
        coa_client.fetch_manifest(MANIFEST_URL, hosts=HOSTS, opener=site)


def test_a_manifest_from_a_host_the_entry_does_not_name_is_never_asked() -> None:
    site = _Site({})
    with pytest.raises(ClientUpdateError, match="not a plain"):
        coa_client.fetch_manifest("https://elsewhere.example/x.json", hosts=HOSTS, opener=site)
    assert site.requests == []


# --- status ------------------------------------------------------------------------------------


def test_a_folder_yulon_never_updated_reads_as_unknown_not_outdated(tmp_path: Path) -> None:
    client = _client(tmp_path, {"Ascension.exe": b"x"})
    manifest = coa_client.parse_manifest(_manifest({"Ascension.exe": b"x"}))
    found, said = coa_client.check_sentence(client, manifest)
    assert found == "unknown"
    assert "unknown" in said and "outdated" not in said


def test_an_updated_folder_is_current_then_outdated_at_a_new_version(tmp_path: Path) -> None:
    client = _client(tmp_path, {"Ascension.exe": b"old"})
    _update(client, _site({"Ascension.exe": b"new"}, "v2"))
    assert coa_client.status(client, coa_client.parse_manifest(_manifest({}, "v2"))) == "current"
    assert coa_client.status(client, coa_client.parse_manifest(_manifest({}, "v3"))) == "outdated"


# --- planning and applying ---------------------------------------------------------------------


def test_only_differing_files_are_downloaded_and_each_replaced_one_is_backed_up(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, {"Ascension.exe": b"old", "Data/patch-A.MPQ": b"same"})
    site = _site(
        {"Ascension.exe": b"new", "Data/patch-A.MPQ": b"same", "Data/patch-WB3.MPQ": b"added"}
    )
    outcome = _update(client, site)
    assert sorted(outcome.written) == ["Ascension.exe", "Data/patch-WB3.MPQ"]
    assert (client / "Ascension.exe").read_bytes() == b"new"
    assert (client / "Data/patch-WB3.MPQ").read_bytes() == b"added"
    backup = client / coa_client.BACKUP_DIR / "2026-10-28-01"
    assert (backup / "Ascension.exe").read_bytes() == b"old"
    assert [u for u, _ in site.object_gets()] == [
        OBJECTS + _sha(b"new"),
        OBJECTS + _sha(b"added"),
    ]
    assert outcome.version_recorded


def test_excluded_and_player_owned_files_are_never_written(tmp_path: Path) -> None:
    mine = {
        "WTF/Config.wtf": b"my settings",
        "Data/enUS/realmlist.wtf": b"set realmlist mine",
        "Cache/WDB/x.wdb": b"cache",
        "Logs/a.txt": b"log",
        "Errors/e.txt": b"err",
        "Interface/AddOns/CoaBotUI/CoaBotUI.toc": b"ours",
        "Ascension_d3d9.log": b"my log",
        "d3d9.dll": b"my reshade",
        "dxvk.conf": b"my dxvk",
        "Data/custom.ini": b"my ini",
    }
    client = _client(tmp_path, mine)
    update = {path: b"from the update " + path.encode() for path in mine}
    update["dxgi.dll"] = b"missing wrapper"
    site = _site(update)
    manifest = coa_client.fetch_manifest(MANIFEST_URL, hosts=HOSTS, opener=site)
    items = coa_client.plan(client, manifest, None)
    actions = _actions(items)
    assert {p for p, a in actions.items() if a == "skip-player-owned"} == {
        "d3d9.dll",
        "dxvk.conf",
        "Data/custom.ini",
    }
    assert actions["dxgi.dll"] == "add"
    for excluded in (
        "WTF/Config.wtf",
        "Data/enUS/realmlist.wtf",
        "Cache/WDB/x.wdb",
        "Logs/a.txt",
        "Errors/e.txt",
        "Interface/AddOns/CoaBotUI/CoaBotUI.toc",
        "Ascension_d3d9.log",
    ):
        assert excluded not in actions
    coa_client.apply(
        client,
        manifest,
        items,
        objects_url=OBJECTS,
        hosts=HOSTS,
        replace_edited=True,
        opener=site,
        free_bytes=lambda _p: 10**12,
    )
    for rel, data in mine.items():
        assert (client / rel).read_bytes() == data, rel
    assert (client / "dxgi.dll").read_bytes() == b"missing wrapper"


def test_apply_refuses_an_excluded_path_even_when_a_plan_names_it(tmp_path: Path) -> None:
    client = _client(tmp_path, {"WTF/Config.wtf": b"mine"})
    manifest = coa_client.parse_manifest(_manifest({"WTF/Config.wtf": b"theirs"}))
    forged = [PlanItem("WTF/Config.wtf", "replace", 6, _sha(b"theirs"))]
    with pytest.raises(ClientUpdateError, match="never written"):
        coa_client.apply(
            client,
            manifest,
            forged,
            objects_url=OBJECTS,
            hosts=HOSTS,
            opener=_site({"WTF/Config.wtf": b"theirs"}),
            free_bytes=lambda _p: 10**12,
        )
    assert (client / "WTF/Config.wtf").read_bytes() == b"mine"


def test_a_file_the_player_edited_since_the_last_update_is_kept(tmp_path: Path) -> None:
    client = _client(tmp_path, {"Data/patch-A.MPQ": b"v1"})
    _update(client, _site({"Data/patch-A.MPQ": b"v1"}, "v1"))
    (client / "Data/patch-A.MPQ").write_bytes(b"my edit")
    site = _site({"Data/patch-A.MPQ": b"v2"}, "v2")
    manifest = coa_client.fetch_manifest(MANIFEST_URL, hosts=HOSTS, opener=site)
    items = coa_client.plan(client, manifest, coa_client.read_state(client))
    assert _actions(items) == {"Data/patch-A.MPQ": "keep-edited"}
    outcome = coa_client.apply(
        client,
        manifest,
        items,
        objects_url=OBJECTS,
        hosts=HOSTS,
        opener=site,
        free_bytes=lambda _p: 10**12,
    )
    assert (client / "Data/patch-A.MPQ").read_bytes() == b"my edit"
    assert outcome.kept == ["Data/patch-A.MPQ"]
    # Remembered against this digest: the same update does not ask again.
    again = coa_client.plan(client, manifest, coa_client.read_state(client))
    assert again[0].action == "keep-edited" and again[0].remembered
    assert "differ" not in coa_client.summary(again)


def test_an_edited_file_is_replaced_only_when_the_player_says_replace(tmp_path: Path) -> None:
    client = _client(tmp_path, {"Data/patch-A.MPQ": b"v1"})
    _update(client, _site({"Data/patch-A.MPQ": b"v1"}, "v1"))
    (client / "Data/patch-A.MPQ").write_bytes(b"my edit")
    _update(client, _site({"Data/patch-A.MPQ": b"v2"}, "v2"), replace_edited=True)
    assert (client / "Data/patch-A.MPQ").read_bytes() == b"v2"
    assert (client / coa_client.BACKUP_DIR / "v2" / "Data/patch-A.MPQ").read_bytes() == b"my edit"


def test_a_hash_mismatch_keeps_the_old_file_and_is_reported(tmp_path: Path) -> None:
    client = _client(tmp_path, {"Ascension.exe": b"old", "Data/a.MPQ": b"a1"})
    site = _site({"Ascension.exe": b"new", "Data/a.MPQ": b"a2"})
    site.files[OBJECTS + _sha(b"new")] = b"bad"  # same size, wrong bytes
    outcome = _update(client, site)
    assert (client / "Ascension.exe").read_bytes() == b"old"
    assert not (client / ("Ascension.exe" + coa_client.PART_SUFFIX)).exists()
    assert [path for path, _ in outcome.failed] == ["Ascension.exe"]
    assert (client / "Data/a.MPQ").read_bytes() == b"a2"
    assert not outcome.version_recorded
    assert coa_client.read_state(client).version is None  # type: ignore[union-attr]


def test_a_cut_download_resumes_from_what_arrived(tmp_path: Path) -> None:
    big = bytes(range(256)) * 64
    client = _client(tmp_path, {"Data/a.MPQ": b"old"})
    site = _site({"Data/a.MPQ": big})
    site.cut_next[OBJECTS + _sha(big)] = 5000
    first = _update(client, site)
    assert first.failed and (client / "Data/a.MPQ").read_bytes() == b"old"
    part = client / ("Data/a.MPQ" + coa_client.PART_SUFFIX)
    assert part.stat().st_size == 5000
    second = _update(client, site)
    assert second.written == ["Data/a.MPQ"] and not second.failed
    assert (client / "Data/a.MPQ").read_bytes() == big
    assert site.object_gets()[-1][1] == {"Range": "bytes=5000-"}


def test_cancel_keeps_the_part_and_records_no_version(tmp_path: Path) -> None:
    client = _client(tmp_path, {"Data/a.MPQ": b"old"})
    site = _site({"Data/a.MPQ": b"new"})
    with pytest.raises(coa_client.Cancelled):
        _update(client, site, cancelled=lambda: True)
    assert (client / "Data/a.MPQ").read_bytes() == b"old"
    assert coa_client.read_state(client).version is None  # type: ignore[union-attr]


def test_a_full_disk_refuses_before_anything_is_downloaded(tmp_path: Path) -> None:
    client = _client(tmp_path, {"Data/a.MPQ": b"old"})
    site = _site({"Data/a.MPQ": b"new bytes"})
    manifest = coa_client.fetch_manifest(MANIFEST_URL, hosts=HOSTS, opener=site)
    items = coa_client.plan(client, manifest, None)
    with pytest.raises(ClientUpdateError, match="free space"):
        coa_client.apply(
            client,
            manifest,
            items,
            objects_url=OBJECTS,
            hosts=HOSTS,
            opener=site,
            free_bytes=lambda _p: 3,
        )
    assert site.object_gets() == []
    assert not (client / coa_client.STATE_FILE).exists()


def test_a_file_in_use_says_close_the_game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, {"Ascension.exe": b"old"})
    real = os.replace

    def busy(src: object, dst: object) -> None:
        if str(src).endswith("Ascension.exe"):
            exc = PermissionError(13, "The process cannot access the file")
            exc.winerror = 32  # type: ignore[attr-defined,unused-ignore]
            raise exc
        real(src, dst)  # type: ignore[arg-type,unused-ignore]

    monkeypatch.setattr(os, "replace", busy)
    outcome = _update(client, _site({"Ascension.exe": b"new"}))
    assert "Close the game" in outcome.failed[0][1]
    assert (client / "Ascension.exe").read_bytes() == b"old"


def test_rollback_restores_replaced_files_removes_added_ones_and_the_state(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, {"Ascension.exe": b"v1"})
    _update(client, _site({"Ascension.exe": b"v1"}, "v1"))
    before = (client / coa_client.STATE_FILE).read_text()
    _update(client, _site({"Ascension.exe": b"v2", "Data/new.MPQ": b"n"}, "v2"))
    assert coa_client.backups(client) == ["v2"]
    assert coa_client.rollback(client, "v2") == []
    assert (client / "Ascension.exe").read_bytes() == b"v1"
    assert not (client / "Data/new.MPQ").exists()
    assert json.loads((client / coa_client.STATE_FILE).read_text()) == json.loads(before)
    assert not (client / coa_client.BACKUP_DIR / "v2").exists()


def test_rollback_keeps_an_added_file_the_player_changed_since(tmp_path: Path) -> None:
    client = _client(tmp_path, {})
    _update(client, _site({"Data/new.MPQ": b"n"}, "v2"))
    (client / "Data/new.MPQ").write_bytes(b"changed")
    left = coa_client.rollback(client, "v2")
    assert left and "changed" in left[0]
    assert (client / "Data/new.MPQ").read_bytes() == b"changed"


def test_the_state_file_is_written_last(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No file is put in place after the state that names the new version is written."""
    client = _client(tmp_path, {"a.exe": b"1", "Data/b.MPQ": b"1"})
    order: list[str] = []
    real = os.replace

    def watch(src: object, dst: object) -> None:
        order.append(Path(str(dst)).name)
        real(src, dst)  # type: ignore[arg-type,unused-ignore]

    monkeypatch.setattr(os, "replace", watch)
    _update(client, _site({"a.exe": b"2", "Data/b.MPQ": b"2", "Data/c.MPQ": b"3"}))
    assert order[-1] == coa_client.STATE_FILE
    assert order.count(coa_client.STATE_FILE) == 1
    assert {"a.exe", "b.MPQ", "c.MPQ"} <= set(order[:-1])


def test_a_link_on_the_way_is_refused(tmp_path: Path) -> None:
    client = _client(tmp_path, {})
    outside = tmp_path / "outside"
    outside.mkdir()
    (client / "Data").symlink_to(outside, target_is_directory=True)
    manifest = coa_client.parse_manifest(_manifest({"Data/a.MPQ": b"x"}))
    with pytest.raises(ClientUpdateError, match="link"):
        coa_client.plan(client, manifest, None)


def test_the_state_file_is_not_the_ready_to_play_marker() -> None:
    from yulon import play_client

    assert coa_client.STATE_FILE != play_client.MARKER


def test_the_summary_counts_files_and_size() -> None:
    items = [
        PlanItem("a", "add", 2 * 1024**3, "a" * 64),
        PlanItem("b", "replace", 1024**3, "b" * 64),
        PlanItem("c", "ok", 5, "c" * 64),
    ]
    assert coa_client.summary(items) == "2 files to download (3.0 GB)."


# --- the map data after an update --------------------------------------------------------------


def test_a_changed_required_file_says_the_map_data_needs_extracting_again(tmp_path: Path) -> None:
    from yulon.catalog.catalog import load_catalog
    from yulon.catalog.families import extract

    entry = next(e for e in load_catalog().games if e.id == "wow-coa")
    native = entry.install.native
    assert native is not None and native.azerothcore is not None
    block = native.azerothcore.client_extract
    assert block is not None
    client = _client(tmp_path, {"Data/patch-WB2.MPQ": b"before"})
    server = tmp_path / "server"
    evidence = extract.expected_evidence(block.extract, client, block.client.required_file)
    extract.write_evidence(server / "data", evidence)
    assert coa_client.map_data_sentence(entry, server, client) is None
    (client / "Data/patch-WB2.MPQ").write_bytes(b"after the update")
    said = coa_client.map_data_sentence(entry, server, client)
    assert said is not None and "extract it again" in said


def test_a_ready_to_play_copy_leaves_the_update_record_and_backups_out(tmp_path: Path) -> None:
    from yulon import play_client

    client = _client(tmp_path, {"Ascension.exe": b"old", "Data/patch-A.MPQ": b"a"})
    _update(client, _site({"Ascension.exe": b"new", "Data/patch-A.MPQ": b"a"}))
    assert (client / coa_client.BACKUP_DIR).is_dir() and (client / coa_client.STATE_FILE).is_file()
    built = play_client.plan(client, tmp_path / "copy")
    names = {p.parts[0] for p in (*built.linked, *built.copied)}
    assert coa_client.BACKUP_DIR not in names
    assert coa_client.STATE_FILE not in names
    assert "Ascension.exe" in names
