"""T755: a CMaNGOS or Centurion install stopped during `import` is neither adopted nor started.

These games have no import service, so T658/T678's import record and Start's probe
never reached them, and their login tables read `present` once the auth part of the
SQL plan ran, long before the world content. The Stop popup said "Use existing…,
nothing is lost"; "Use existing…" took the folder and Start ran mangosd on a
half-imported world. The install record's `completed` list is the evidence: `import`
goes into it only once the plan verified. Start and "Use existing…" now refuse while
it is missing, and the Stop popup no longer offers the adoption.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon.catalog import composegen, native
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.families import family_for
from yulon.catalog.installer import (
    cancelled_install_message,
    unfinished_import,
    unfinished_import_start_refusal,
)
from yulon.controller import Controller, StartRefused

CATALOG = load_catalog()


def _entry(game: str) -> CatalogEntry:
    return CATALOG.get(game)


def _record(folder: Path, entry: CatalogEntry, *, through: str, game: str = "") -> None:
    """The record an install of `entry` writes, its `completed` list ending at `through`."""
    stages = family_for(entry).STAGE_NAMES
    done = stages[: stages.index(through) + 1]
    native.write_state(
        folder,
        native.InstallState(
            game_id=game or entry.id,
            install_id="id",
            family=entry.install.native.family if entry.install.native else "",
            completed=done,
        ),
    )


def _controller(entry: CatalogEntry, folder: Path) -> Controller:
    controller = Controller(entry.container_spec(), folder)
    controller.entry = entry
    return controller


@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla", "wow-tortoise", "wow-centurion"])
def test_start_refuses_an_install_stopped_before_its_import_finished(
    tmp_path: Path, game: str
) -> None:
    """Stopped in `import`: everything up to `conf` recorded, `import` not.

    Mutation this catches: dropping the refusal from `refuse_start()`.
    """
    entry = _entry(game)
    _record(tmp_path, entry, through="conf")
    with pytest.raises(StartRefused) as refused:
        _controller(entry, tmp_path).refuse_start()
    said = str(refused.value)
    assert "database import" in said and str(tmp_path) in said
    assert "Remove from Yu'lon…" in said and "Install" in said, said


@pytest.mark.parametrize("game", ["wow-tbc", "wow-centurion"])
def test_a_finished_import_is_not_refused(tmp_path: Path, game: str) -> None:
    """Stopped in `up` or `ready` (unrecorded): `import` is in the record; nothing refuses.

    Mutation this catches: refusing on the record's presence rather than its `import`.
    """
    entry = _entry(game)
    _record(tmp_path, entry, through="import")
    assert unfinished_import(entry, tmp_path) is None
    assert unfinished_import_start_refusal(entry, tmp_path) is None


def test_a_folder_without_a_record_is_not_refused(tmp_path: Path) -> None:
    """A bash-era or hand-made server has no record: nothing Yu'lon wrote says it is unfinished."""
    assert unfinished_import(_entry("wow-tbc"), tmp_path) is None


def test_another_games_record_is_not_this_refusal(tmp_path: Path) -> None:
    """A Vanilla record in a folder opened as TBC is the guard's and the family check's to say."""
    _record(tmp_path, _entry("wow-tbc"), through="conf", game="wow-vanilla")
    assert unfinished_import(_entry("wow-tbc"), tmp_path) is None


def test_an_azerothcore_server_keeps_its_own_import_record(tmp_path: Path) -> None:
    """WotLK/Unbound are refused by T658/T678's record and probe, which offer Repair.

    Mutation this catches: dropping the import-service check, which would send a WotLK
    player to remove and reinstall where Repair the database… finishes the job.
    """
    entry = _entry("wow-wotlk")
    _record(tmp_path, entry, through="client-data")
    assert unfinished_import(entry, tmp_path) is None


def _ours(folder: Path) -> None:
    for name in composegen.COMPOSE_FILES:
        (folder / name).write_text(f"{composegen.GENERATED_MARKER}\nservices: {{}}\n", "utf-8")


def test_the_stop_popup_does_not_offer_use_existing_over_an_unfinished_import(
    tmp_path: Path,
) -> None:
    """installer.py's "Use existing… nothing is lost", said after a Stop in `import`.

    Mutation this catches: leaving `promised_nothing_lost` blind to the record.
    """
    entry = _entry("wow-tbc")
    _ours(tmp_path)
    _record(tmp_path, entry, through="conf")
    said = cancelled_install_message(entry, tmp_path)
    assert "nothing is lost" not in said, said
    assert "database import had not finished" in said
    assert f"Press Install again and choose {tmp_path}" in said


def test_the_stop_popup_still_offers_use_existing_once_the_import_finished(
    tmp_path: Path,
) -> None:
    """Stopped while the server came up: the import is done, and the adoption is right."""
    entry = _entry("wow-tbc")
    _ours(tmp_path)
    _record(tmp_path, entry, through="import")
    said = cancelled_install_message(entry, tmp_path)
    assert "Use existing" in said and "nothing is lost" in said


def test_use_existing_refuses_an_install_stopped_in_its_import(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tile's "Use existing…": refused with the sentence, nothing attached.

    Mutation this catches: dropping the refusal from `attach_existing()`.
    """
    from PySide6.QtWidgets import QMessageBox

    from yulon.ui.catalog_view import CatalogView
    from yulon.ui.widgets.log_panel import LogPanel

    entry = _entry("wow-tbc")
    _ours(tmp_path)
    _record(tmp_path, entry, through="conf")
    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))  # type: ignore[attr-defined]
    view = CatalogView(
        CATALOG,
        lambda e: None,  # type: ignore[arg-type,return-value]
        LogPanel(),
        pick_dir=lambda *_: tmp_path,
        home=tmp_path,
    )
    got: list[object] = []
    view.installed.connect(lambda *a: got.append(a))
    assert view.attach_existing(entry) is False
    assert got == []
    assert warned and "database import finished" in warned[0], warned
    assert "Press Install on this tile" in warned[0]
