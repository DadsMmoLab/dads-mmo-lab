"""T825: keeping the player's Conquest of Azeroth client current (`yulon.coa_client`).

No network: every request goes to `_Site`, a fake `client_packs.Opener` serving
bytes by URL with single `bytes=N-` ranges.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    outcome = _update(client, site, replace_edited=True)
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


def test_a_differing_file_yulon_has_no_record_of_is_kept(tmp_path: Path) -> None:
    """Owner's decision (T825): on a first update a differing file stays the player's."""
    client = _client(tmp_path, {"Data/patch-A.MPQ": b"old"})
    site = _site({"Data/patch-A.MPQ": b"published", "Data/new.MPQ": b"n"})
    manifest = coa_client.fetch_manifest(MANIFEST_URL, hosts=HOSTS, opener=site)
    items = coa_client.plan(client, manifest, None)
    assert _actions(items) == {"Data/patch-A.MPQ": "keep-edited", "Data/new.MPQ": "add"}
    said = coa_client.summary(items)
    assert said.startswith("1 file to download")
    assert "1 file (Data/patch-A.MPQ) differs from the published one" in said
    assert "kept unless you choose to replace it" in said
    outcome = _update(client, site)
    assert (client / "Data/patch-A.MPQ").read_bytes() == b"old"
    assert outcome.written == ["Data/new.MPQ"] and outcome.kept == ["Data/patch-A.MPQ"]
    assert OBJECTS + _sha(b"published") not in [u for u, _ in site.object_gets()]


def test_the_replace_choice_replaces_an_unrecorded_file_and_backs_it_up(tmp_path: Path) -> None:
    client = _client(tmp_path, {"Data/patch-A.MPQ": b"old"})
    outcome = _update(client, _site({"Data/patch-A.MPQ": b"published"}, "v1"), replace_edited=True)
    assert outcome.written == ["Data/patch-A.MPQ"]
    assert (client / "Data/patch-A.MPQ").read_bytes() == b"published"
    assert (client / coa_client.BACKUP_DIR / "v1" / "Data/patch-A.MPQ").read_bytes() == b"old"


def test_a_recorded_file_the_player_did_not_change_is_replaced_without_asking(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, {"Data/patch-A.MPQ": b"v1"})
    _update(client, _site({"Data/patch-A.MPQ": b"v1"}, "v1"))
    site = _site({"Data/patch-A.MPQ": b"v2"}, "v2")
    manifest = coa_client.fetch_manifest(MANIFEST_URL, hosts=HOSTS, opener=site)
    items = coa_client.plan(client, manifest, coa_client.read_state(client))
    assert _actions(items) == {"Data/patch-A.MPQ": "replace"}
    _update(client, site)
    assert (client / "Data/patch-A.MPQ").read_bytes() == b"v2"
    assert (client / coa_client.BACKUP_DIR / "v2" / "Data/patch-A.MPQ").read_bytes() == b"v1"


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
    outcome = _update(client, site, replace_edited=True)
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
    first = _update(client, site, replace_edited=True)
    assert first.failed and (client / "Data/a.MPQ").read_bytes() == b"old"
    part = client / ("Data/a.MPQ" + coa_client.PART_SUFFIX)
    assert part.stat().st_size == 5000
    second = _update(client, site, replace_edited=True)
    assert second.written == ["Data/a.MPQ"] and not second.failed
    assert (client / "Data/a.MPQ").read_bytes() == big
    assert site.object_gets()[-1][1] == {"Range": "bytes=5000-"}


def test_cancel_keeps_the_part_and_records_no_version(tmp_path: Path) -> None:
    client = _client(tmp_path, {"Data/a.MPQ": b"old"})
    site = _site({"Data/a.MPQ": b"new"})
    with pytest.raises(coa_client.Cancelled):
        _update(client, site, cancelled=lambda: True, replace_edited=True)
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
            replace_edited=True,
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
    outcome = _update(client, _site({"Ascension.exe": b"new"}), replace_edited=True)
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
    undone = coa_client.rollback(client, "v2")
    assert undone.left == [] and undone.kept_changed == 0
    assert (client / "Ascension.exe").read_bytes() == b"v1"
    assert not (client / "Data/new.MPQ").exists()
    assert json.loads((client / coa_client.STATE_FILE).read_text()) == json.loads(before)
    assert not (client / coa_client.BACKUP_DIR / "v2").exists()


def test_rollback_keeps_an_added_file_the_player_changed_since(tmp_path: Path) -> None:
    """Kept, moved aside into the conflicts folder (T825 review), so the Undo can finish."""
    client = _client(tmp_path, {})
    _update(client, _site({"Data/new.MPQ": b"n"}, "v2"))
    (client / "Data/new.MPQ").write_bytes(b"changed")
    undone = coa_client.rollback(client, "v2")
    assert undone.left == [] and undone.kept_changed == 1
    conflicts = client / coa_client.BACKUP_DIR / "v2" / coa_client.CONFLICTS
    assert (conflicts / "Data/new.MPQ").read_bytes() == b"changed"
    assert not (client / "Data/new.MPQ").exists()
    assert "1 file you changed after the update was kept in" in undone.sentence()


def _replace_journal(client: Path, version: str) -> dict[str, object]:
    root = client / coa_client.BACKUP_DIR / version
    return json.loads((root / coa_client.JOURNAL).read_text())  # type: ignore[no-any-return]


def test_a_backup_folder_that_is_a_link_is_refused(tmp_path: Path) -> None:
    client = _client(tmp_path, {"Ascension.exe": b"old"})
    outside = tmp_path / "outside"
    outside.mkdir()
    (client / coa_client.BACKUP_DIR).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ClientUpdateError, match="link"):
        _update(client, _site({"Ascension.exe": b"new"}), replace_edited=True)
    assert list(outside.iterdir()) == []
    assert (client / "Ascension.exe").read_bytes() == b"old"


def test_a_version_folder_that_is_a_link_is_refused_before_any_move(tmp_path: Path) -> None:
    client = _client(tmp_path, {"Ascension.exe": b"old"})
    outside = tmp_path / "outside"
    outside.mkdir()
    (client / coa_client.BACKUP_DIR).mkdir()
    (client / coa_client.BACKUP_DIR / "2026-10-28-01").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ClientUpdateError, match="link"):
        _update(client, _site({"Ascension.exe": b"new"}), replace_edited=True)
    assert list(outside.iterdir()) == []
    assert (client / "Ascension.exe").read_bytes() == b"old"


def test_a_link_planted_under_the_version_folder_mid_update_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checked again right before each journal write and move, not only at the start."""
    client = _client(tmp_path, {"a.exe": b"1", "Data/b.MPQ": b"1"})
    outside = tmp_path / "outside"
    outside.mkdir()
    real = coa_client._write_journal
    planted: list[bool] = []

    def plant(client_dir: Path, root: Path, journal: dict[str, dict[str, object]]) -> None:
        real(client_dir, root, journal)
        if not planted and "Data/b.MPQ" in journal["replaced"]:
            # After the checks before the journal write: swap the real folder for a link.
            (root / "Data").rmdir()
            (root / "Data").symlink_to(outside, target_is_directory=True)
            planted.append(True)

    monkeypatch.setattr(coa_client, "_write_journal", plant)
    outcome = _update(client, _site({"a.exe": b"2", "Data/b.MPQ": b"2"}), replace_edited=True)
    assert planted
    assert list(outside.iterdir()) == []
    assert (client / "Data/b.MPQ").read_bytes() == b"1"
    assert any("link" in why for _, why in outcome.failed)


@pytest.mark.skipif(sys.platform != "win32", reason="a junction exists only on Windows")
def test_a_backup_folder_that_is_a_junction_is_refused(tmp_path: Path) -> None:
    """Windows only: `play_client._is_link` reads a junction by its reparse tag."""
    import subprocess

    client = _client(tmp_path, {"Ascension.exe": b"old"})
    outside = tmp_path / "outside"
    outside.mkdir()
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(client / coa_client.BACKUP_DIR), str(outside)],
        check=True,
    )
    with pytest.raises(ClientUpdateError, match="link"):
        _update(client, _site({"Ascension.exe": b"new"}), replace_edited=True)
    assert list(outside.iterdir()) == []


def test_undo_keeps_an_edit_made_after_the_update_and_still_restores_the_original(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, {"Ascension.exe": b"original"})
    _update(client, _site({"Ascension.exe": b"updated"}, "v2"), replace_edited=True)
    (client / "Ascension.exe").write_bytes(b"edited after")
    undone = coa_client.rollback(client, "v2")
    assert undone.left == [] and undone.kept_changed == 1
    assert (client / "Ascension.exe").read_bytes() == b"original"
    conflicts = client / coa_client.BACKUP_DIR / "v2" / coa_client.CONFLICTS
    assert (conflicts / "Ascension.exe").read_bytes() == b"edited after"
    assert "1 file you changed after the update was kept in" in undone.sentence()


def test_the_journal_records_the_original_and_installed_digests(tmp_path: Path) -> None:
    client = _client(tmp_path, {"Ascension.exe": b"original"})
    _update(client, _site({"Ascension.exe": b"updated"}, "v2"), replace_edited=True)
    record = _replace_journal(client, "v2")["replaced"]["Ascension.exe"]  # type: ignore[index]
    assert record == {
        "backup": "Ascension.exe",
        "original": _sha(b"original"),
        "installed": _sha(b"updated"),
    }


def test_an_undo_stopped_by_a_locked_file_continues_on_the_next_undo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {"a.exe": b"a1", "Data/b.MPQ": b"b1", "Data/c.MPQ": b"c1"}
    client = _client(tmp_path, files)
    _update(client, _site(files, "v1"))
    before = (client / coa_client.STATE_FILE).read_text()
    _update(client, _site({"a.exe": b"a2", "Data/b.MPQ": b"b2", "Data/c.MPQ": b"c2"}, "v2"))
    real = os.replace
    failed: list[str] = []

    def locked_once(src: object, dst: object) -> None:
        if str(dst).endswith("b.MPQ") and coa_client.BACKUP_DIR in str(src) and not failed:
            failed.append(str(dst))
            exc = PermissionError(13, "The process cannot access the file")
            exc.winerror = 32  # type: ignore[attr-defined,unused-ignore]
            raise exc
        real(src, dst)  # type: ignore[arg-type,unused-ignore]

    monkeypatch.setattr(os, "replace", locked_once)
    first = coa_client.rollback(client, "v2")
    assert failed and len(first.left) == 1 and "Data/b.MPQ" in first.left[0]
    assert "Close the game" in first.left[0]
    assert (client / "a.exe").read_bytes() == b"a1"
    assert (client / "Data/c.MPQ").read_bytes() == b"c1"
    assert (client / "Data/b.MPQ").read_bytes() == b"b2"
    assert "partly undone" in first.sentence()
    # Each finished step is checkpointed in the journal at once.
    assert set(_replace_journal(client, "v2")["undone"]) == {  # type: ignore[arg-type]
        "replaced:a.exe",
        "replaced:Data/c.MPQ",
    }
    # Not consumed: the state is still the update's, the backup still there.
    assert (client / coa_client.BACKUP_DIR / "v2" / "Data/b.MPQ").read_bytes() == b"b1"
    second = coa_client.rollback(client, "v2")
    assert second.left == [] and second.kept_changed == 0
    assert {rel: (client / rel).read_bytes() for rel in files} == files
    assert json.loads((client / coa_client.STATE_FILE).read_text()) == json.loads(before)
    assert not (client / coa_client.BACKUP_DIR / "v2").exists()


def test_a_restore_whose_checkpoint_was_lost_is_done_only_if_it_holds_the_original(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, {"Data/b.MPQ": b"b1"})
    _update(client, _site({"Data/b.MPQ": b"b1"}, "v1"))
    _update(client, _site({"Data/b.MPQ": b"b2"}, "v2"))
    root = client / coa_client.BACKUP_DIR / "v2"
    # A crash after the move and before the checkpoint: backup gone, original in place.
    os.replace(root / "Data/b.MPQ", client / "Data/b.MPQ")
    assert coa_client.rollback(client, "v2").left == []
    # The same, but the file is NOT the original: never counted as put back.
    _update(client, _site({"Data/b.MPQ": b"b3"}, "v3"))
    root = client / coa_client.BACKUP_DIR / "v3"
    (root / "Data/b.MPQ").unlink()
    undone = coa_client.rollback(client, "v3")
    assert undone.left and "backup is missing" in undone.left[0]
    assert (root / coa_client.JOURNAL).is_file()


def test_the_state_file_is_written_last(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No file is put in place after the state that names the new version is written."""
    client = _client(tmp_path, {"a.exe": b"1", "Data/b.MPQ": b"1"})
    order: list[str] = []
    real = os.replace

    def watch(src: object, dst: object) -> None:
        order.append(Path(str(dst)).name)
        real(src, dst)  # type: ignore[arg-type,unused-ignore]

    monkeypatch.setattr(os, "replace", watch)
    _update(
        client, _site({"a.exe": b"2", "Data/b.MPQ": b"2", "Data/c.MPQ": b"3"}), replace_edited=True
    )
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
    _update(client, _site({"Ascension.exe": b"new", "Data/patch-A.MPQ": b"a"}), replace_edited=True)
    assert (client / coa_client.BACKUP_DIR).is_dir() and (client / coa_client.STATE_FILE).is_file()
    built = play_client.plan(client, tmp_path / "copy")
    names = {p.parts[0] for p in (*built.linked, *built.copied)}
    assert coa_client.BACKUP_DIR not in names
    assert coa_client.STATE_FILE not in names
    assert "Ascension.exe" in names


def test_a_legacy_journal_undo_that_stops_before_its_checkpoint_completes_next_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An early journal (string entries, no digests): the original's digest is taken from
    the backup and saved BEFORE the move, so a lost checkpoint does not strand the Undo."""
    client = _client(tmp_path, {"Ascension.exe": b"original"})
    _update(client, _site({"Ascension.exe": b"original"}, "v1"))
    _update(client, _site({"Ascension.exe": b"updated"}, "v2"))
    root = client / coa_client.BACKUP_DIR / "v2"
    journal = json.loads((root / coa_client.JOURNAL).read_text())
    journal["replaced"] = {"Ascension.exe": "Ascension.exe"}  # the early, string form
    journal.pop("undone", None)
    (root / coa_client.JOURNAL).write_text(json.dumps(journal))
    real = coa_client._write_journal
    failed: list[bool] = []

    def lose_the_checkpoint(client_dir: Path, root_: Path, journal_: dict[str, Any]) -> None:
        if "replaced:Ascension.exe" in journal_.get("undone", {}) and not failed:
            failed.append(True)
            raise OSError(5, "simulated crash before the checkpoint")
        real(client_dir, root_, journal_)

    monkeypatch.setattr(coa_client, "_write_journal", lose_the_checkpoint)
    first = coa_client.rollback(client, "v2")
    assert failed and first.left
    assert (client / "Ascension.exe").read_bytes() == b"original"
    assert not (root / "Ascension.exe").exists()
    second = coa_client.rollback(client, "v2")
    assert second.left == []
    assert (client / "Ascension.exe").read_bytes() == b"original"
    assert coa_client.read_state(client).version == "v1"  # type: ignore[union-attr]


# --- zero-byte files (T838) --------------------------------------------------------------------

EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
EMPTY = {
    "Data/Content/TransmogrificationItemData.json": b"",
    "HD_Patches_Description.txt": b"",
}


def test_the_empty_file_digest_is_the_one_the_manifest_carries() -> None:
    assert _sha(b"") == EMPTY_SHA


def test_a_zero_byte_file_is_created_through_the_journal_without_asking_the_site(
    tmp_path: Path,
) -> None:
    """The CoA manifest 2026-10-29-01 lists four empty files; each must end up on disk."""
    client = _client(tmp_path, {"Ascension.exe": b"old"})
    site = _site({"Ascension.exe": b"new", **EMPTY})
    outcome = _update(client, site, replace_edited=True)
    assert outcome.failed == []
    assert sorted(outcome.written) == sorted(["Ascension.exe", *EMPTY])
    for rel in EMPTY:
        assert (client / rel).is_file() and (client / rel).read_bytes() == b""
        assert not (client / (rel + coa_client.PART_SUFFIX)).exists()
    assert [u for u, _ in site.object_gets()] == [OBJECTS + _sha(b"new")]
    journal = _replace_journal(client, "2026-10-28-01")
    assert journal["added"] == {rel: EMPTY_SHA for rel in EMPTY}
    assert outcome.version_recorded
    state = coa_client.read_state(client)
    assert state is not None and state.version == "2026-10-28-01"
    assert all(state.files[rel].size == 0 and state.files[rel].sha256 == EMPTY_SHA for rel in EMPTY)


def test_a_second_update_finds_the_empty_files_ok_and_writes_nothing(tmp_path: Path) -> None:
    client = _client(tmp_path, {})
    site = _site({"Ascension.exe": b"new", **EMPTY})
    _update(client, site)
    manifest = coa_client.fetch_manifest(MANIFEST_URL, hosts=HOSTS, opener=site)
    items = coa_client.plan(client, manifest, coa_client.read_state(client))
    assert set(_actions(items).values()) == {"ok"}
    assert coa_client.summary(items).startswith("Your client already matches")
    site.requests.clear()
    again = _update(client, site)
    assert again.written == [] and again.failed == [] and again.version_recorded
    assert site.object_gets() == []


def test_an_empty_file_already_there_with_no_record_is_ok_on_a_first_update(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, dict(EMPTY))
    manifest = coa_client.parse_manifest(_manifest(EMPTY))
    assert set(_actions(coa_client.plan(client, manifest, None)).values()) == {"ok"}


def test_rollback_removes_an_added_empty_file_and_keeps_one_the_player_filled(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, {})
    _update(client, _site(dict(EMPTY), "v2"))
    filled = "HD_Patches_Description.txt"
    (client / filled).write_bytes(b"my notes")
    undone = coa_client.rollback(client, "v2")
    assert undone.left == [] and undone.kept_changed == 1
    assert not (client / "Data/Content/TransmogrificationItemData.json").exists()
    assert not (client / filled).exists()
    conflicts = client / coa_client.BACKUP_DIR / "v2" / coa_client.CONFLICTS
    assert (conflicts / filled).read_bytes() == b"my notes"
    assert not (client / coa_client.STATE_FILE).exists()


def test_a_zero_byte_entry_with_another_digest_is_refused_and_nothing_is_left(
    tmp_path: Path,
) -> None:
    """No bytes can match a size-0 entry whose SHA-256 is not the empty file's."""
    client = _client(tmp_path, {})
    site = _site({"Ascension.exe": b"new"})
    site.files[MANIFEST_URL] = json.dumps(
        {
            "schema": 1,
            "version": "v2",
            "files": [
                {"path": "Ascension.exe", "size": 3, "sha256": _sha(b"new")},
                {"path": "Data/odd.json", "size": 0, "sha256": "a" * 64},
            ],
        }
    ).encode()
    outcome = _update(client, site)
    assert outcome.written == ["Ascension.exe"]
    assert [path for path, _ in outcome.failed] == ["Data/odd.json"]
    assert "does not match" in outcome.failed[0][1]
    assert not (client / "Data/odd.json").exists()
    assert not (client / ("Data/odd.json" + coa_client.PART_SUFFIX)).exists()
    assert not outcome.version_recorded
    assert site.object_gets() == [(OBJECTS + _sha(b"new"), {})]


def test_a_non_empty_file_where_the_update_has_an_empty_one_is_kept_unless_replaced(
    tmp_path: Path,
) -> None:
    rel = "HD_Patches_Description.txt"
    client = _client(tmp_path, {rel: b"something"})
    site = _site({rel: b""})
    kept = _update(client, site)
    assert kept.written == [] and kept.kept == [rel]
    assert (client / rel).read_bytes() == b"something"
    (tmp_path / "other").mkdir()
    other = _client(tmp_path / "other", {rel: b"something"})
    replaced = _update(other, site, replace_edited=True)
    assert replaced.written == [rel] and replaced.version_recorded
    assert (other / rel).read_bytes() == b""
    backup = other / coa_client.BACKUP_DIR / "2026-10-28-01" / rel
    assert backup.read_bytes() == b"something"


def test_an_empty_file_yulon_recorded_and_the_update_now_fills_is_replaced(
    tmp_path: Path,
) -> None:
    rel = "HD_Patches_Description.txt"
    client = _client(tmp_path, {})
    _update(client, _site({rel: b""}, "v1"))
    manifest = coa_client.parse_manifest(_manifest({rel: b"filled"}, "v2"))
    items = coa_client.plan(client, manifest, coa_client.read_state(client))
    assert _actions(items) == {rel: "replace"}
    _update(client, _site({rel: b"filled"}, "v2"))
    assert (client / rel).read_bytes() == b"filled"
    assert (client / coa_client.BACKUP_DIR / "v2" / rel).read_bytes() == b""


@pytest.mark.parametrize(
    "path", ["NUL.json", "Data/Content/x.json.", "Data/Content/x.json ", "a:b.json", "Data\\x.json"]
)
def test_a_zero_byte_entry_at_a_path_windows_cannot_hold_is_refused(path: str) -> None:
    raw = json.dumps(
        {"schema": 1, "version": "v1", "files": [{"path": path, "size": 0, "sha256": EMPTY_SHA}]}
    ).encode()
    with pytest.raises(ManifestError):
        coa_client.parse_manifest(raw)


def test_an_empty_file_on_disk_in_another_case_is_ok_and_no_second_spelling_is_made(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, {"data/content/transmogrificationitemdata.json": b""})
    outcome = _update(client, _site({"Data/Content/TransmogrificationItemData.json": b""}))
    assert outcome.written == [] and outcome.version_recorded
    assert os.listdir(client / "data" / "content") == ["transmogrificationitemdata.json"]
    assert sorted(os.listdir(client)) == sorted(["data", coa_client.STATE_FILE])


def test_an_empty_file_is_not_written_through_a_linked_folder(tmp_path: Path) -> None:
    client = _client(tmp_path, {})
    outside = tmp_path / "outside"
    outside.mkdir()
    (client / "Data").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ClientUpdateError, match="link"):
        _update(client, _site({"Data/Content/TransmogrificationItemData.json": b""}))
    assert list(outside.iterdir()) == []


def _can_symlink(tmp_path: Path) -> bool:
    probe = tmp_path / "probe-link"
    try:
        probe.symlink_to(tmp_path / "probe-target")
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


@pytest.mark.parametrize(
    ("rel", "data", "partial"),
    [
        ("HD_Patches_Description.txt", b"", False),  # a zero-byte entry
        ("Data/patch-B.MPQ", b"new-bytes", True),  # a normal entry, resuming
    ],
)
def test_a_part_file_that_is_a_link_is_refused_and_what_it_points_at_is_untouched(
    tmp_path: Path, rel: str, data: bytes, partial: bool
) -> None:
    if not _can_symlink(tmp_path):
        pytest.skip("this system cannot make a symlink without elevation")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"new" if partial else b"not a client file")
    before = outside.read_bytes()
    client = _client(tmp_path, {})
    part = client / (rel + coa_client.PART_SUFFIX)
    part.parent.mkdir(parents=True, exist_ok=True)
    part.symlink_to(outside)
    site = _site({rel: data, "Ascension.exe": b"exe"})
    outcome = _update(client, site)
    assert [path for path, _ in outcome.failed] == [rel]
    assert "a link is in the way" in outcome.failed[0][1]
    assert outside.read_bytes() == before
    assert part.is_symlink()  # left for the player
    assert not (client / rel).exists()
    assert outcome.written == ["Ascension.exe"]
    assert not outcome.version_recorded
    assert site.object_gets() == [(OBJECTS + _sha(b"exe"), {})]


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="no O_NOFOLLOW on this platform")
def test_a_link_planted_as_the_part_after_the_check_is_not_written_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The open itself refuses to follow a link (POSIX), not only the check before it."""
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"keep me")
    part = tmp_path / ("x.json" + coa_client.PART_SUFFIX)
    part.symlink_to(outside)
    monkeypatch.setattr(coa_client, "_is_link", lambda _p: False)
    with pytest.raises(OSError):
        coa_client._download(
            OBJECTS + EMPTY_SHA,
            part,
            0,
            opener=_Site({}),
            hosts=HOSTS,
            cancelled=lambda: False,
            progress=lambda _n: None,
        )
    assert outside.read_bytes() == b"keep me"
