"""T827: telling the CoA client from the stock Ascension download (`coa_client.client_kind`).

A stock Ascension client is disconnected at realm select on a CoA server; the CoA
client files (`coa_client.KEY_FILES`) are what differ. No network: the manifest is
built here, or read from `fixtures/coa-manifest-sample.json` (the published list's
shape, four entries, made-up digests).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.test_coa_client import _client, _manifest, _site, _update
from tests.test_coa_client_update_view import _control
from yulon import coa_client
from yulon.catalog.catalog import load_catalog
from yulon.ui import client_update
from yulon.ui.client_update import Choice
from yulon.ui.widgets.job import run_inline

SAMPLE = Path(__file__).parent / "fixtures" / "coa-manifest-sample.json"

COA = {"Extensions.dll": b"coa-ext", "Ascension.exe": b"coa-exe", "Data/patch-B.MPQ": b"mpq"}
STOCK = {"Extensions.dll": b"stock-extensions-larger", "Ascension.exe": b"stock-exe"}


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    coa_client._KEY_SHA.clear()
    yield
    coa_client._KEY_SHA.clear()


def _parsed(files: dict[str, bytes], version: str = "2026-10-28-01") -> coa_client.Manifest:
    return coa_client.parse_manifest(_manifest(files, version))


def _kind(client: Path, manifest: coa_client.Manifest | None) -> str:
    return coa_client.client_kind(client, manifest, coa_client.read_state(client))


def test_the_sample_has_the_published_shape_and_names_both_key_files() -> None:
    manifest = coa_client.parse_manifest(SAMPLE.read_bytes())
    names = {item.path for item in manifest.files}
    assert set(coa_client.KEY_FILES) <= names
    assert 3 <= len(manifest.files) <= 5


def test_key_files_equal_to_the_manifest_are_the_coa_client(tmp_path: Path) -> None:
    client = _client(tmp_path, COA)
    assert _kind(client, _parsed(COA)) == "coa"


def test_key_files_that_differ_with_no_record_are_the_stock_client(tmp_path: Path) -> None:
    client = _client(tmp_path, {**COA, **STOCK})
    assert _kind(client, _parsed(COA)) == "stock"


def test_one_differing_key_file_of_the_same_size_is_found_by_its_digest(tmp_path: Path) -> None:
    client = _client(tmp_path, {**COA, "Extensions.dll": b"COA-EXT"})  # same size, other bytes
    assert _kind(client, _parsed(COA)) == "stock"


def test_a_missing_key_file_beside_the_other_is_not_the_coa_client(tmp_path: Path) -> None:
    client = _client(tmp_path, {"Ascension.exe": COA["Ascension.exe"]})
    assert _kind(client, _parsed(COA)) == "stock"


def test_the_sample_manifest_reads_the_stock_folder_as_stock(tmp_path: Path) -> None:
    client = _client(tmp_path, STOCK)
    assert _kind(client, coa_client.parse_manifest(SAMPLE.read_bytes())) == "stock"


@pytest.mark.parametrize("case", ["no-manifest", "empty-folder", "no-folder", "unreadable"])
def test_unknown_when_it_cannot_be_told(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest: coa_client.Manifest | None = _parsed(COA)
    if case == "no-manifest":
        client, manifest = _client(tmp_path, STOCK), None
    elif case == "empty-folder":
        client = _client(tmp_path, {"Data/patch-B.MPQ": b"mpq"})
    elif case == "no-folder":
        client = tmp_path / "absent"
    else:
        client = _client(tmp_path, COA)
        real = Path.stat

        def refuse(self: Path, **kw: Any) -> os.stat_result:
            if self.name in coa_client.KEY_FILES:
                raise PermissionError(13, "Permission denied", str(self))
            return real(self, **kw)

        monkeypatch.setattr(Path, "stat", refuse)
    assert _kind(client, manifest) == "unknown"


def test_a_folder_yulon_brought_to_an_older_coa_version_is_still_the_coa_client(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, {})
    outcome = _update(client, _site(COA, "v1"))
    assert outcome.version_recorded
    newer = _parsed({**COA, "Extensions.dll": b"coa-ext-v2"}, "v2")
    assert _kind(client, newer) == "coa"
    assert _kind(client, None) == "coa"  # offline: what Yu'lon recorded is enough
    (client / "Extensions.dll").write_bytes(b"stock-extensions-larger")
    assert _kind(client, newer) == "stock"
    assert _kind(client, None) == "unknown"


def test_only_the_key_files_are_hashed_and_each_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, COA)
    hashed: list[str] = []
    real = coa_client._sha256

    def counting(path: Path, *a: Any) -> str:
        hashed.append(path.name)
        return real(path, *a)

    monkeypatch.setattr(coa_client, "_sha256", counting)
    manifest = _parsed(COA)
    assert _kind(client, manifest) == "coa"
    assert sorted(hashed) == sorted(coa_client.KEY_FILES)
    assert _kind(client, manifest) == "coa"
    assert len(hashed) == 2  # the second check read nothing: cached by size and mtime
    hashed.clear()
    (tmp_path / "s").mkdir()
    stock = _client(tmp_path / "s", STOCK)
    assert _kind(stock, manifest) == "stock"
    assert hashed == []  # a size that differs needs no digest


# --- The Server tab ----------------------------------------------------------------------------


def test_the_stock_client_shows_the_warning_and_the_fix_press(qapp: object, tmp_path: Path) -> None:
    control, _client_dir, _ = _control(tmp_path, _site(COA), {**COA, **STOCK})
    control.check()
    assert control.kind == "stock"
    assert control.kind_label.text() == coa_client.STOCK_WARNING
    assert control.kind_label.isVisibleTo(control._keep_parent)  # type: ignore[attr-defined]
    assert control.fix_button.isVisibleTo(control._keep_parent)  # type: ignore[attr-defined]
    assert control.fix_button.text() == client_update.FIX_LABEL


def test_the_coa_client_shows_no_warning(qapp: object, tmp_path: Path) -> None:
    control, _client_dir, _ = _control(tmp_path, _site(COA), COA)
    control.check()
    assert control.kind == "coa"
    assert control.kind_label.text() == coa_client.KIND_COA
    assert not control.fix_button.isVisibleTo(control._keep_parent)  # type: ignore[attr-defined]


def test_a_failed_check_shows_no_warning(qapp: object, tmp_path: Path) -> None:
    def broken(*_a: object, **_k: object) -> None:
        raise OSError("no route to host")

    control, _client_dir, _ = _control(tmp_path, broken, {**COA, **STOCK})
    control.check()
    assert control.kind == "unknown"
    assert not control.kind_label.isVisibleTo(control._keep_parent)  # type: ignore[attr-defined]
    assert not control.fix_button.isVisibleTo(control._keep_parent)  # type: ignore[attr-defined]


def _confirming(control: Any, answer: bool) -> list[str]:
    said: list[str] = []

    def confirm(_parent: object, _title: str, text: str) -> bool:
        said.append(text)
        return answer

    control._confirm = confirm
    return said


def test_the_fix_asks_once_then_makes_the_stock_client_the_coa_client(
    qapp: object, tmp_path: Path
) -> None:
    control, client, asked = _control(tmp_path, _site(COA), {**COA, **STOCK})
    control.check()
    said = _confirming(control, True)
    control.fix_pressed()
    assert len(said) == 1 and asked == []  # one Yes/No, not the Keep/Replace question
    assert (client / "Extensions.dll").read_bytes() == COA["Extensions.dll"]
    assert (client / "Ascension.exe").read_bytes() == COA["Ascension.exe"]
    backups = client / coa_client.BACKUP_DIR / "2026-10-28-01"
    assert (backups / "Extensions.dll").read_bytes() == STOCK["Extensions.dll"]
    assert control.kind == "coa"
    assert not control.fix_button.isVisibleTo(control._keep_parent)  # type: ignore[attr-defined]


def test_no_to_the_fix_changes_nothing(qapp: object, tmp_path: Path) -> None:
    control, client, _ = _control(tmp_path, _site(COA), {**COA, **STOCK})
    control.check()
    _confirming(control, False)
    control.fix_pressed()
    assert (client / "Extensions.dll").read_bytes() == STOCK["Extensions.dll"]
    assert "nothing was changed" in control.label.text()
    assert control.kind == "stock"


def test_the_fix_replaces_a_key_file_the_player_chose_to_keep_before(
    qapp: object, tmp_path: Path
) -> None:
    files = {**COA, **STOCK}
    control, client, _ = _control(tmp_path, _site(COA), files, answer=Choice.KEEP)
    control.update_pressed()  # first: Update, keep my changed files
    assert (client / "Extensions.dll").read_bytes() == STOCK["Extensions.dll"]
    state = coa_client.read_state(client)
    assert state is not None and "Extensions.dll" in state.kept
    control.check()
    assert control.kind == "stock"  # keeping the stock file keeps the warning
    _confirming(control, True)
    control.fix_pressed()
    assert (client / "Extensions.dll").read_bytes() == COA["Extensions.dll"]
    assert str(control.kind) == "coa"


def test_the_client_files_are_read_off_the_gui_thread(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every `client_kind` (it hashes) runs inside a job's work, never in a slot."""
    in_work = threading.local()
    calls: list[bool] = []
    real = coa_client.client_kind

    def watched(*a: Any) -> Any:
        calls.append(getattr(in_work, "on", False))
        return real(*a)

    monkeypatch.setattr(coa_client, "client_kind", watched)

    def jobs(work: Any, on_done: Any, on_error: Any) -> None:
        in_work.on = True
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - the runner's contract
            in_work.on = False
            on_error(exc)
            return
        in_work.on = False
        on_done(result)

    control, _client_dir, _ = _control(tmp_path, _site(COA), {**COA, **STOCK})
    control._jobs = jobs
    control.check()
    _confirming(control, True)
    control.fix_pressed()
    assert calls and all(calls)


def test_the_server_tab_warns_for_a_stock_client_and_play_stays_open(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_coa_client import _Response
    from yulon.ui.controller_view import ControllerServices, ControllerView

    entry = load_catalog().get("wow-coa")
    update = entry.client.update
    assert update is not None
    body = _manifest(COA)

    def site(url: str, _w: object, **_k: object) -> _Response:
        assert url == update.manifest_url
        return _Response(200, {}, body)

    monkeypatch.setattr(coa_client, "_network", site)
    client = _client(tmp_path, {**COA, **STOCK})
    services = ControllerServices.for_entry(entry, tmp_path / "coa", client)
    services.set_play_client_dir = lambda _p: None
    view = ControllerView(entry, services, status_poll_ms=0, job_runner=run_inline)
    control = view.client_update
    assert control is not None
    assert control.kind_label.text() == coa_client.STOCK_WARNING
    assert control.fix_button.isVisibleTo(view)
    assert view.play_button is not None and view.play_button.isEnabled()
