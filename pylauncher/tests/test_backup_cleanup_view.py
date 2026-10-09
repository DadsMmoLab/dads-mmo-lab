"""T604: the Maintenance tab's Backups box lists what made each file and deletes or cleans up.

The deleting itself is `backup_shelf`'s, tested in `test_backup_shelf.py`. Here: what the
list says, when Delete and Clean up are greyed and why, that Delete asks exactly once and
names the file, that Clean up's dialog shows the total on its button and never deletes by
itself, and that the automatic keep follows a Back up now and nothing else.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QDialog

from tests.test_backup_shelf import GAME, dump_of, folder_of
from tests.test_backup_shelf import put as _put
from tests.test_controller_view import WOTLK, _FakeMaintenance, _Ps, _services, ps  # noqa: F401
from yulon import backup_shelf, docker
from yulon.ui import controller_view as view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets import clean_up_dialog
from yulon.ui.widgets.clean_up_dialog import CleanUpDialog


def put(server: Path, *args: Any, **kwargs: Any) -> Path:
    folder_of(server).mkdir(parents=True, exist_ok=True)
    return _put(server, *args, **kwargs)


@pytest.fixture
def made(tmp_path: Path) -> _FakeMaintenance:
    return _FakeMaintenance()


@pytest.fixture
def keep_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "cfg"
    monkeypatch.setattr(backup_shelf.platform, "config_dir", lambda: cfg)
    monkeypatch.setattr(backup_shelf.docker, "reservation_name", lambda _s: "yulon-busy-abc123")
    return cfg


def make_view(
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    made: _FakeMaintenance,
    monkeypatch: pytest.MonkeyPatch,
    *,
    seam: Any,
) -> ControllerView:
    (tmp_path / "sql_scripts" / "backups").mkdir(parents=True, exist_ok=True)
    services = dataclasses.replace(_services(ps, tmp_path, [], made), shelf=seam)
    view = ControllerView(WOTLK, services, status_poll_ms=0)

    def run_in_line(fn: Any, done: Any, failed: Any) -> None:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - what the runner hands `failed`
            failed(exc)
        else:
            done(result)

    monkeypatch.setattr(view, "_run", run_in_line)
    return view


@pytest.fixture
def view(
    qapp: object,
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    made: _FakeMaintenance,
    monkeypatch: pytest.MonkeyPatch,
) -> ControllerView:
    return make_view(ps, tmp_path, made, monkeypatch, seam=backup_shelf.Seam(tmp_path, GAME))


class Asked:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.texts: list[str] = []

    def __call__(self, parent: object, title: str, text: str) -> bool:
        self.texts.append(text)
        return self.answer


def ask(monkeypatch: pytest.MonkeyPatch, answer: bool) -> Asked:
    asked = Asked(answer)
    monkeypatch.setattr(view_module, "ask_yes_no", asked)
    return asked


def select(view: ControllerView, name: str) -> None:
    for i in range(view.backup_list.count()):
        if view.backup_list.item(i).data(view_module.Qt.ItemDataRole.UserRole + 1) == name:
            view.backup_list.setCurrentRow(i)
            return
    raise AssertionError(f"{name} is not listed: {texts(view)}")


def texts(view: ControllerView) -> list[str]:
    return [view.backup_list.item(i).text() for i in range(view.backup_list.count())]


# ------------------------------------------------------------------- the list


def test_the_list_says_when_what_how_big_what_made_it_and_which_game(
    view: ControllerView, tmp_path: Path
) -> None:
    put(tmp_path, "20261001_103000", "acore_characters", label="pre-restore", pad=2 * 1024 * 1024)
    view.refresh_backups()
    (line,) = texts(view)
    assert line.startswith("2026-10-01 10:30 · acore_characters · 2.0 MB · ")
    assert "taken before a restore" in line
    assert "WoW WotLK" in line


def test_a_protected_file_is_marked_kept_and_muted(view: ControllerView, tmp_path: Path) -> None:
    only = put(tmp_path, "20261001_103000", "acore_world")
    view.refresh_backups()
    item = view.backup_list.item(0)
    assert item.text().endswith("kept")
    assert "newest good copy" in item.toolTip()
    assert item.data(view_module.Qt.ItemDataRole.UserRole) == str(folder_of(tmp_path) / only.name)
    assert item.foreground().color().name().lower() == view_module.COLOR_TEXT_MUTED.lower()


def test_another_games_file_and_a_cut_short_one_are_labelled(
    view: ControllerView, tmp_path: Path
) -> None:
    put(tmp_path, "20261001_100000", "acore_world", game="wow-tbc")
    put(tmp_path, "20261002_100000", "acore_world", whole=False)
    (folder_of(tmp_path) / "20261003_100000_acore_world.sql.partial").write_bytes(b"x")
    view.refresh_backups()
    joined = "\n".join(texts(view))
    assert "(another game)" in joined
    assert "cut short or unreadable" in joined
    assert "a backup that was cut short" in joined


def test_without_a_shelf_the_tab_lists_as_before_and_has_no_delete(
    qapp: object,
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    made: _FakeMaintenance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = make_view(ps, tmp_path, made, monkeypatch, seam=None)
    (folder_of(tmp_path) / "chars.sql").write_bytes(b"-- dump\n")
    plain.refresh_backups()
    assert texts(plain)[0].startswith("chars.sql")
    assert plain.delete_backup_button.isHidden()
    assert plain.clean_up_button.isHidden()


# ----------------------------------------------------------------- the buttons


def test_delete_is_greyed_until_a_deletable_row_is_picked_and_says_why(
    view: ControllerView, tmp_path: Path
) -> None:
    put(tmp_path, "20261001_100000", "acore_world")
    newest = put(tmp_path, "20261003_100000", "acore_world")
    view.refresh_backups()
    view.backup_list.setCurrentRow(-1)
    assert not view.delete_backup_button.isEnabled()
    assert view_module.DELETE_PICK in view.delete_backup_button.toolTip()

    select(view, newest.name)
    assert not view.delete_backup_button.isEnabled()
    assert "newest good copy" in view.delete_backup_button.toolTip()
    assert "newest good copy" in view.restore_reasons.text()

    select(view, "20261001_100000_acore_world.sql")
    assert view.delete_backup_button.isEnabled()
    assert view.delete_backup_button.toolTip() == ""


def test_the_buttons_wait_while_a_backup_runs(view: ControllerView, tmp_path: Path) -> None:
    put(tmp_path, "20261001_100000", "acore_world")
    put(tmp_path, "20261003_100000", "acore_world")
    view.refresh_backups()
    select(view, "20261001_100000_acore_world.sql")
    assert view.delete_backup_button.isEnabled()
    assert view.clean_up_button.isEnabled()
    view._backup_running = True
    view._settle_shelf_buttons()
    assert not view.delete_backup_button.isEnabled()
    assert not view.clean_up_button.isEnabled()
    assert "the backup is running" in view.clean_up_button.toolTip()
    view._backup_running = False
    view._settle_shelf_buttons()
    assert view.clean_up_button.isEnabled()


def test_an_empty_folder_greys_both_with_a_reason(view: ControllerView) -> None:
    view.refresh_backups()
    assert not view.delete_backup_button.isEnabled()
    assert not view.clean_up_button.isEnabled()
    assert "no backups" in view.clean_up_button.toolTip()


def test_a_folder_linked_out_of_the_install_greys_both(
    view: ControllerView, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    other = tmp_path_factory.mktemp("otherdrive")
    (other / "20261001_100000_acore_world.sql").write_bytes(dump_of("acore_world"))
    folder = folder_of(tmp_path)
    folder.rmdir()
    folder.symlink_to(other, target_is_directory=True)
    view.refresh_backups()
    assert view.backup_list.count() == 1
    select(view, "20261001_100000_acore_world.sql")
    assert not view.delete_backup_button.isEnabled()
    assert not view.clean_up_button.isEnabled()
    assert "leads out of the server's own folder" in view.delete_backup_button.toolTip()


# ---------------------------------------------------------------------- delete


def test_delete_asks_once_naming_the_file_and_its_size_then_removes_it(
    view: ControllerView, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = put(tmp_path, "20261001_100000", "acore_world", pad=3 * 1024 * 1024)
    put(tmp_path, "20261003_100000", "acore_world")
    view.refresh_backups()
    select(view, old.name)
    asked = ask(monkeypatch, True)
    view.delete_selected_backup()
    assert len(asked.texts) == 1
    assert old.name in asked.texts[0]
    assert "3.0 MB" in asked.texts[0]
    assert "does not go to the trash" in asked.texts[0]
    assert not old.exists()
    assert old.name in view.maintenance_report.toPlainText()
    assert view.backup_list.count() == 1  # re-listed: the deleted row is gone


def test_saying_no_deletes_nothing(
    view: ControllerView, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = put(tmp_path, "20261001_100000", "acore_world")
    put(tmp_path, "20261003_100000", "acore_world")
    view.refresh_backups()
    select(view, old.name)
    ask(monkeypatch, False)
    view.delete_selected_backup()
    assert old.exists()


def test_a_read_only_file_is_named_as_such_in_the_question(
    view: ControllerView, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = put(tmp_path, "20261001_100000", "acore_world")
    put(tmp_path, "20261003_100000", "acore_world")
    old.chmod(0o444)
    view.refresh_backups()
    select(view, old.name)
    asked = ask(monkeypatch, True)
    view.delete_selected_backup()
    assert "read-only" in asked.texts[0]
    assert not old.exists()


def test_a_backup_running_in_another_yulon_refuses_the_delete_and_says_who(
    qapp: object,
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    made: _FakeMaintenance,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seam = backup_shelf.Seam(tmp_path, GAME, spec=object())  # type: ignore[arg-type]
    view = make_view(ps, tmp_path, made, monkeypatch, seam=seam)
    old = put(tmp_path, "20261001_100000", "acore_world")
    put(tmp_path, "20261003_100000", "acore_world")
    view.refresh_backups()
    select(view, old.name)
    monkeypatch.setattr(
        docker,
        "reservation_holder",
        lambda *a, **k: docker.ServerHolder(
            "yulon-busy-x", "c1", press="Backup", who="pk@THEIR-PC (Windows)"
        ),
    )
    ask(monkeypatch, True)
    view.delete_selected_backup()
    assert old.exists()
    shown = view.maintenance_report.toPlainText()
    assert "Another Yu'lon" in shown
    assert "pk@THEIR-PC" in shown
    assert "Nothing was changed" in shown


def test_a_backup_running_in_this_yulon_refuses_the_delete(
    view: ControllerView, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = put(tmp_path, "20261001_100000", "acore_world")
    put(tmp_path, "20261003_100000", "acore_world")
    view.refresh_backups()
    select(view, old.name)
    ask(monkeypatch, True)
    with docker.maintenance_lease(tmp_path, "A backup of this server is running."):
        view.delete_selected_backup()
    assert old.exists()
    assert "A backup of this server is running" in view.maintenance_report.toPlainText()


# -------------------------------------------------------------------- clean up


def three_worlds(tmp_path: Path) -> list[Path]:
    return [put(tmp_path, f"2026100{d}_100000", "acore_world", pad=1024) for d in (1, 2, 3)]


def test_the_clean_up_dialog_puts_the_total_on_the_button(qapp: object, tmp_path: Path) -> None:
    files = three_worlds(tmp_path)
    shelf = backup_shelf.read_shelf(tmp_path, game_id=GAME, installed={})
    dialog = CleanUpDialog(shelf, keep_now=None)
    dialog.keep_spin.setValue(1)
    assert dialog.go.text() == "Delete 2 files"
    assert dialog.go.isEnabled()
    freed = files[0].stat().st_size + files[1].stat().st_size
    assert dialog.summary.text() == (
        f"2 files would be deleted, freeing {backup_shelf.size_text(freed)}."
    )
    assert dialog.plan is not None
    assert dialog.plan.freed == freed
    assert dialog.files.count() == 2
    dialog.keep_spin.setValue(3)
    assert dialog.summary.text() == clean_up_dialog.NOTHING
    assert not dialog.go.isEnabled()
    assert dialog.plan is not None
    assert dialog.plan.names == ()
    dialog.deleteLater()


def test_the_dialog_never_offers_to_keep_nothing(qapp: object, tmp_path: Path) -> None:
    three_worlds(tmp_path)
    dialog = CleanUpDialog(
        backup_shelf.read_shelf(tmp_path, game_id=GAME, installed={}), keep_now=None
    )
    assert dialog.keep_spin.minimum() == 1
    dialog.deleteLater()


def test_the_dialog_counts_unusable_files_and_adds_them_on_request(
    qapp: object, tmp_path: Path
) -> None:
    put(tmp_path, "20261003_100000", "acore_world")
    put(tmp_path, "20261001_100000", "acore_world", whole=False)
    shelf = backup_shelf.read_shelf(tmp_path, game_id=GAME, installed={})
    dialog = CleanUpDialog(shelf, keep_now=None)
    assert "(1)" in dialog.unusable_check.text()
    assert dialog.plan is not None
    assert dialog.plan.names == ()
    dialog.unusable_check.setChecked(True)
    assert dialog.plan is not None
    assert dialog.plan.names == ("20261001_100000_acore_world.sql",)
    dialog.deleteLater()


def test_the_automatic_keep_is_off_by_default_and_follows_the_keep_rule_only(
    qapp: object, tmp_path: Path
) -> None:
    three_worlds(tmp_path)
    shelf = backup_shelf.read_shelf(tmp_path, game_id=GAME, installed={})
    dialog = CleanUpDialog(shelf, keep_now=None)
    assert not dialog.auto_check.isChecked()
    assert dialog.keep_wanted() == (False, None)
    dialog.auto_check.setChecked(True)
    dialog.keep_spin.setValue(2)
    assert dialog.keep_wanted() == (True, 2)
    dialog.age_radio.setChecked(True)
    assert not dialog.auto_check.isEnabled()
    assert dialog.keep_wanted() == (False, None)
    dialog.deleteLater()
    on = CleanUpDialog(shelf, keep_now=4)
    assert on.auto_check.isChecked()
    assert on.keep_spin.value() == 4
    assert on.keep_wanted() == (False, 4)
    on.auto_check.setChecked(False)
    assert on.keep_wanted() == (True, None)
    on.deleteLater()


def accept_with(monkeypatch: pytest.MonkeyPatch, setup: Any) -> list[CleanUpDialog]:
    seen: list[CleanUpDialog] = []

    def exec_(self: CleanUpDialog) -> int:
        seen.append(self)
        setup(self)
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(CleanUpDialog, "exec", exec_)
    return seen


def test_clean_up_removes_what_the_dialog_showed_and_reports_it(
    view: ControllerView, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keep_store: Path
) -> None:
    a, b, c = three_worlds(tmp_path)
    view.refresh_backups()
    accept_with(monkeypatch, lambda d: d.keep_spin.setValue(1))
    view.clean_up_backups()
    assert not a.exists()
    assert not b.exists()
    assert c.exists()
    report = view.maintenance_report.toPlainText()
    assert "Deleted 2 files" in report
    assert a.name in report
    assert backup_shelf.Seam(tmp_path, GAME).keep() is None


def test_cancelling_the_dialog_deletes_and_saves_nothing(
    view: ControllerView, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keep_store: Path
) -> None:
    files = three_worlds(tmp_path)

    def setup(d: CleanUpDialog) -> None:
        d.keep_spin.setValue(1)
        d.auto_check.setChecked(True)

    monkeypatch.setattr(CleanUpDialog, "exec", lambda self: (setup(self), 0)[1])
    view.clean_up_backups()
    assert all(f.exists() for f in files)
    assert backup_shelf.Seam(tmp_path, GAME).keep() is None


def test_agreeing_with_the_automatic_keep_ticked_stores_it_per_install(
    view: ControllerView, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keep_store: Path
) -> None:
    three_worlds(tmp_path)

    def setup(d: CleanUpDialog) -> None:
        d.keep_spin.setValue(2)
        d.auto_check.setChecked(True)

    accept_with(monkeypatch, setup)
    view.clean_up_backups()
    assert backup_shelf.Seam(tmp_path, GAME).keep() == 2
    assert (keep_store / "backup-keep" / "abc123.json").is_file()


def test_clean_up_refuses_everything_when_the_folder_changed_behind_the_dialog(
    view: ControllerView, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b, c = three_worlds(tmp_path)

    def setup(d: CleanUpDialog) -> None:
        d.keep_spin.setValue(1)
        put(tmp_path, "20261004_100000", "acore_world")  # a Backup finished while it was open

    accept_with(monkeypatch, setup)
    view.clean_up_backups()
    assert all(f.exists() for f in (a, b, c))
    assert "changed" in view.maintenance_report.toPlainText()


# ------------------------------------------------------------ the automatic keep


def a_backup_that_adds_a_file(made: _FakeMaintenance, tmp_path: Path) -> None:
    def back_up() -> Any:
        made.backups += 1
        put(tmp_path, "20261009_100000", "acore_world")
        from yulon.controller_wow_wotlk.maintenance import BackupReport

        return BackupReport(directory=folder_of(tmp_path), dumps=())

    made.back_up = back_up  # type: ignore[method-assign]


def test_after_back_up_now_the_automatic_keep_removes_the_older_copies(
    qapp: object,
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    made: _FakeMaintenance,
    monkeypatch: pytest.MonkeyPatch,
    keep_store: Path,
) -> None:
    seam = backup_shelf.Seam(tmp_path, GAME)
    a_backup_that_adds_a_file(made, tmp_path)
    view = make_view(ps, tmp_path, made, monkeypatch, seam=seam)
    files = three_worlds(tmp_path)
    seam.set_keep(2)
    monkeypatch.setattr(ControllerView, "_refused_during_bot_rebuild", lambda self: False)
    monkeypatch.setattr(
        docker, "maintenance_lease", lambda *a, **k: __import__("contextlib").nullcontext()
    )
    view.back_up()
    assert made.backups == 1
    assert [f.exists() for f in files] == [False, False, True]
    assert (folder_of(tmp_path) / "20261009_100000_acore_world.sql").exists()
    assert "removed 2 older files" in view.maintenance_report.toPlainText()


def test_with_the_automatic_keep_off_a_backup_deletes_nothing(
    qapp: object,
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    made: _FakeMaintenance,
    monkeypatch: pytest.MonkeyPatch,
    keep_store: Path,
) -> None:
    a_backup_that_adds_a_file(made, tmp_path)
    view = make_view(ps, tmp_path, made, monkeypatch, seam=backup_shelf.Seam(tmp_path, GAME))
    files = three_worlds(tmp_path)
    monkeypatch.setattr(ControllerView, "_refused_during_bot_rebuild", lambda self: False)
    view.back_up()
    assert all(f.exists() for f in files)
    assert (folder_of(tmp_path) / "20261009_100000_acore_world.sql").exists()  # it did take one


def test_the_backup_taken_before_an_update_never_triggers_the_keep(
    qapp: object,
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    made: _FakeMaintenance,
    monkeypatch: pytest.MonkeyPatch,
    keep_store: Path,
) -> None:
    """The update takes the server's reservation next; a clean-up in between would refuse it."""
    seam = backup_shelf.Seam(tmp_path, GAME)
    view = make_view(ps, tmp_path, made, monkeypatch, seam=seam)
    files = three_worlds(tmp_path)
    seam.set_keep(1)
    from yulon.controller_wow_wotlk.maintenance import BackupReport

    view._backup_done(BackupReport(directory=folder_of(tmp_path), dumps=()))
    assert all(f.exists() for f in files)


def test_a_failed_backup_does_not_leave_the_keep_armed_for_the_next_update_copy(
    qapp: object,
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    made: _FakeMaintenance,
    monkeypatch: pytest.MonkeyPatch,
    keep_store: Path,
) -> None:
    seam = backup_shelf.Seam(tmp_path, GAME)
    view = make_view(ps, tmp_path, made, monkeypatch, seam=seam)
    files = three_worlds(tmp_path)
    seam.set_keep(1)
    view._retain_after_backup = True
    view._backup_failed(RuntimeError("no database"))
    from yulon.controller_wow_wotlk.maintenance import BackupReport

    view._backup_done(BackupReport(directory=folder_of(tmp_path), dumps=()))
    assert all(f.exists() for f in files)


_ = os


def test_the_seam_retains_nothing_while_the_keep_is_off(tmp_path: Path, keep_store: Path) -> None:
    files = three_worlds(tmp_path)
    seam = backup_shelf.Seam(tmp_path, GAME)
    assert seam.retain() is None
    assert all(f.exists() for f in files)


def test_clean_up_in_a_folder_linked_out_opens_no_dialog(
    view: ControllerView,
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = tmp_path_factory.mktemp("otherdrive")
    (other / "20261001_100000_acore_world.sql").write_bytes(dump_of("acore_world"))
    folder = folder_of(tmp_path)
    folder.rmdir()
    folder.symlink_to(other, target_is_directory=True)
    opened: list[object] = []
    monkeypatch.setattr(CleanUpDialog, "exec", lambda self: opened.append(self) or 1)
    view.clean_up_backups()
    assert not opened
    assert "leads out of the server's own folder" in view.maintenance_report.toPlainText()


def test_delete_and_clean_up_wait_while_one_of_them_runs(
    view: ControllerView, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = put(tmp_path, "20261001_100000", "acore_world")
    put(tmp_path, "20261003_100000", "acore_world")
    view.refresh_backups()
    select(view, old.name)
    held: list[Any] = []
    monkeypatch.setattr(view, "_run", lambda fn, done, failed: held.append((fn, done, failed)))
    ask(monkeypatch, True)
    view.delete_selected_backup()
    assert held
    assert not view.delete_backup_button.isEnabled()
    assert not view.clean_up_button.isEnabled()
    assert "the delete is running" in view.clean_up_button.toolTip()
    fn, done, _failed = held[0]
    done(fn())
    assert view.clean_up_button.isEnabled()


@pytest.mark.parametrize("game_id", sorted(view_module._FACTORIES))
def test_every_game_wires_the_shelf_to_its_own_folder_and_game(
    game_id: str, tmp_path: Path, qapp: object
) -> None:
    from yulon.catalog.catalog import load_catalog

    entry = load_catalog().get(game_id)
    services = view_module.ControllerServices.for_entry(entry, tmp_path)
    seam = services.shelf
    assert seam is not None
    assert seam.server_dir == tmp_path
    assert seam.game_id == game_id
    assert seam.spec == entry.container_spec()
    assert services.backups_dir() == backup_shelf.backups_dir(tmp_path)
