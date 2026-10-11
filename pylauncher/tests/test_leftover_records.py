"""T682: leftover records no longer block a re-run of Uninstall, or a folder a move finished.

Lifecycle audit L14, L18 and L15.

* L14: the folder's removal went in listing order, so the install record (and `.env`, the
  password file) usually went before `src/` and `.git`, where a removal stops. The second
  Uninstall then found no record and refused the folder as "not Yu'lon's".
* L18: a finished whole-server move kept `.yulon-move-in.json` for good, so "Use existing..."
  and Install refused the folder, and a second Bring-in with the same file installed again.
* L15: an unticked uninstall left the command channel's saved credential, whose account row
  went with the database; a reinstall into the same folder (same install id) loaded it and
  asked for Repair.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_move_server_flow import Move
from tests.test_purge import GAME, Recorder, _recorder
from yulon import channel_setup, move_server, purge
from yulon.catalog import composegen, native
from yulon.move_flows import MoveError
from yulon.ui import catalog_view

HELD = "held.pack"
"""The one file the faked OS refuses to unlink while `held` is on: a file a program holds open."""


def _owned_server(tmp_path: Path) -> Recorder:
    """A server folder with Yu'lon's real install record, judged by the real ownership claim."""
    rec = _recorder(tmp_path, claim=None)  # type: ignore[arg-type]
    rec.claim = purge._default_claim  # type: ignore[method-assign]
    folder = rec.server_dir
    for i in range(12):
        sub = folder / f"dir{i:02d}" / ".git" / "objects"
        sub.mkdir(parents=True)
        (sub / "pack.idx").write_text("x", encoding="utf-8")
    (folder / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (folder / ".env").write_text("COMPOSE_PROJECT_NAME=yulon-wow-wotlk-deadbeef\n", "utf-8")
    native.write_state(
        folder,
        native.InstallState(
            game_id=GAME, install_id=composegen.install_id(folder), family="azerothcore"
        ),
    )
    return rec


def _hold_a_file_listed_after_the_record(rec: Recorder) -> Path:
    """Put the held file in the first folder the OS lists AFTER the install record.

    `shutil.rmtree` works in listing order, so with the old order the record is certain to
    be gone before the removal reaches the held file. The listing order is this filesystem's
    own; the test reads it rather than assuming one.
    """
    listing = os.listdir(rec.server_dir)
    after = [n for n in listing[listing.index(native.STATE_FILE) + 1 :] if n.startswith("dir")]
    assert after, f"this filesystem lists the record after every folder: {listing}"
    held = rec.server_dir / after[0] / HELD
    held.write_text("open in another program", encoding="utf-8")
    return held


@pytest.fixture
def held_open(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    """While `state["on"]`, unlinking `HELD` fails as Windows fails a file held open."""
    state = {"on": True}
    real = os.unlink

    def unlink(path: object, *args: object, **kwargs: object) -> None:
        if state["on"] and os.path.basename(os.fsdecode(path)) == HELD:  # type: ignore[arg-type]
            raise PermissionError(13, "The process cannot access the file", os.fsdecode(path))  # type: ignore[arg-type]
        real(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "unlink", unlink)
    return state


# -- L14: a second Uninstall finishes what the first could not ---------------------------------


def test_a_second_uninstall_finishes_a_folder_the_first_could_not_delete(
    tmp_path: Path, held_open: dict[str, bool]
) -> None:
    """The real removal and the real ownership claim; only Docker and the held file are faked.

    Mutation: remove the folder with plain `rmtree.remove_tree` again (listing order).
    """
    rec = _owned_server(tmp_path)
    _hold_a_file_listed_after_the_record(rec)

    with pytest.raises(purge.PurgeRefusal) as first:
        rec.uninstaller(remove_folder=None).run(keep_characters=False)
    assert "could not be deleted" in str(first.value)
    assert "the uninstall can be run again" in str(first.value)
    assert rec.forgotten == 0
    assert (rec.server_dir / native.STATE_FILE).is_file(), "the record went before the stop"
    assert (rec.server_dir / ".env").is_file(), "the compose project's pin went before the stop"

    held_open["on"] = False  # the program holding the file was closed
    report = rec.uninstaller(remove_folder=None).run(keep_characters=False)

    assert report.folder_removed and report.record_forgotten
    assert not rec.server_dir.exists()
    assert rec.forgotten == 1


def test_the_records_a_second_press_reads_are_removed_last_and_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every folder, then the other files, then `.env`, the folder id and the record LAST.

    Mutation: drop `last` from the removal (each name in listing order).
    """
    rec = _owned_server(tmp_path)
    (rec.server_dir / ".db_password").write_text("secret\n", encoding="utf-8")
    (rec.server_dir / ".yulon-folder-id").write_text("abc\n", encoding="utf-8")
    (rec.server_dir / "zz-notes.txt").write_text("mine\n", encoding="utf-8")
    seen: list[str] = []
    real = os.unlink

    def unlink(path: object, *args: object, **kwargs: object) -> None:
        seen.append(os.path.basename(os.fsdecode(path)))  # type: ignore[arg-type]
        real(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "unlink", unlink)

    purge.remove_tree(rec.server_dir)

    assert not rec.server_dir.exists()
    assert seen[-4:] == list(purge.REMOVED_LAST), seen
    assert seen.index("zz-notes.txt") > seen.index("pack.idx"), "a file went before the folders"


def test_a_folder_yulon_never_installed_is_still_refused(tmp_path: Path) -> None:
    """The order changes nothing about who may be removed: no record, no removal."""
    rec = _recorder(tmp_path, claim=None)  # type: ignore[arg-type]
    rec.claim = purge._default_claim  # type: ignore[method-assign]
    (rec.server_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(purge.PurgeRefusal, match="Nothing here says Yu'lon installed it"):
        rec.uninstaller(remove_folder=None).run(keep_characters=False)
    assert rec.server_dir.is_dir()


# -- L15: a reinstall after an unticked uninstall makes a fresh credential --------------------


def _saved_credential(server_dir: Path) -> Path:
    install_id = composegen.install_id(server_dir)
    channel_setup.save_credential(
        channel_setup.Verified(account=channel_setup.account_name(install_id), password="old"),
        game=GAME,
        install_id=install_id,
        host="127.0.0.1",
        port=7878,
        namespace="urn:AC",
    )
    path = channel_setup.credential_path(GAME, install_id)
    assert path.is_file()
    return path


def test_an_unticked_uninstall_removes_the_channel_credential_with_its_database(
    tmp_path: Path,
) -> None:
    """Its row went with the auth database; a reinstall here must not load it.

    Mutation: drop the `_forget_credential()` call from the unticked branch.
    """
    rec = _recorder(tmp_path)
    path = _saved_credential(rec.server_dir)

    report = rec.uninstaller().run(keep_characters=False)

    assert not path.exists()
    install_id = composegen.install_id(rec.server_dir)
    assert channel_setup.load_credential(GAME, install_id) is None
    assert not [w for w in report.warnings if "credential" in w], report.warnings


def test_a_ticked_uninstall_keeps_the_credential_of_the_database_it_kept(tmp_path: Path) -> None:
    rec = _recorder(tmp_path)
    path = _saved_credential(rec.server_dir)
    rec.uninstaller().run(keep_characters=True)
    assert path.is_file()


def test_a_credential_that_cannot_be_removed_is_named_and_does_not_stop_the_uninstall(
    tmp_path: Path,
) -> None:
    rec = _recorder(tmp_path)

    def refuses() -> None:
        raise PermissionError(13, "Access is denied")

    report = rec.uninstaller(forget_credential=refuses).run(keep_characters=False)

    assert report.folder_removed
    said = [w for w in report.warnings if "saved credential" in w]
    assert len(said) == 1 and "Access is denied" in said[0], report.warnings


def test_the_dialog_no_longer_says_an_unticked_uninstall_keeps_the_credential() -> None:
    line = purge.LEFT_BEHIND[-1]
    assert line.index("if you keep your characters") < line.index("stored server credential")


# -- L18: a finished move-in is a server, not a move ------------------------------------------


def test_a_finished_move_in_removes_its_record(tmp_path: Path) -> None:
    """Mutation: drop `record.forget()` from the end of `MovedInInstall.run()`."""
    mv = Move(tmp_path)
    mv.run()
    assert not (mv.target.server_dir / move_server.MOVE_IN_FILE).exists()
    assert not move_server.unfinished_move_in(mv.target.server_dir)


def test_a_second_bring_in_of_a_finished_move_is_refused_as_a_server(tmp_path: Path) -> None:
    """Also for a record a finished move left before T682: its steps are all done.

    Mutation: drop `not move_in_finished(marker)` from `folder_refusal()`.
    """
    mv = Move(tmp_path)
    mv.run()
    digest = move_server.package_digest(mv.plan.manifest)  # type: ignore[arg-type]
    move_server.write_marker(mv.target.server_dir, digest, list(move_server.STEPS))
    assert move_server.folder_refusal(mv.target.server_dir, digest) == (
        move_server.holds_a_server(mv.target.server_dir),
        False,
    )
    installs = mv.engine.runs
    with pytest.raises(MoveError):
        mv.run()
    assert mv.engine.runs == installs, "the finished server was installed again"


@pytest.mark.parametrize(
    ("done", "unfinished"),
    [
        (list(move_server.STEPS), False),
        (list(move_server.STEPS[:-1]), True),
        ([], True),
        (None, True),  # a record that cannot be read still says a move was building here
    ],
    ids=["all-done", "data-left", "nothing-done", "unreadable"],
)
def test_the_catalog_reads_a_finished_record_as_a_server(
    tmp_path: Path, done: list[str] | None, unfinished: bool
) -> None:
    """ "Use existing..." and Install ask `_unfinished_move_in()`, not whether the file is there.

    Mutation: make `unfinished_move_in()` answer True for any record.
    """
    folder = tmp_path / "server"
    folder.mkdir()
    if done is None:
        (folder / move_server.MOVE_IN_FILE).write_text("{not json", encoding="utf-8")
    else:
        move_server.write_marker(folder, "f" * 64, done)
    assert catalog_view._unfinished_move_in(folder) is unfinished
    assert move_server.unfinished_move_in(folder) is unfinished


def test_a_folder_with_no_record_is_no_move(tmp_path: Path) -> None:
    assert not catalog_view._unfinished_move_in(tmp_path)


def test_use_existing_attaches_a_folder_a_finished_move_left_its_record_in(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The press itself: before T682 it said "A move that did not finish" for good.

    Mutation: ask whether the record is there (`MOVE_IN_FILE).exists()`) again.
    """
    from tests.test_move_in_catalog import CATALOG, WOTLK, Mover, _FakeInstaller
    from yulon.ui.widgets.log_panel import LogPanel

    shown: list[str] = []
    monkeypatch.setattr(catalog_view, "show_information", lambda _p, title, _t: shown.append(title))
    folder = tmp_path / "moved-in"
    folder.mkdir()
    move_server.write_marker(folder, "f" * 64, list(move_server.STEPS))
    (folder / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    built = catalog_view.CatalogView(
        CATALOG,
        lambda e: _FakeInstaller(e, []),
        LogPanel(),
        pick_dir=lambda _p, _t, _s: folder,
        home=tmp_path,
        move_in=Mover(tmp_path).move_in(),
        platform_id=lambda: "linux",
    )
    built.attach_existing(WOTLK)
    assert "A move that did not finish" not in shown, shown


def test_a_cut_off_load_is_finished_by_bring_in_without_starting_the_server(
    tmp_path: Path,
) -> None:
    """T681/L19 on the move's side: the carry-on loads again and never starts on half data.

    The load of acore_characters fails part-way and leaves the restore's marker. The next
    press skips the install (its own start would run the server on the half-replaced
    databases); the data step then puts every database in again, which clears the marker.
    Mutation: run the install again whatever `load_cut_off()` says.
    """
    from tests.test_move_flows import Db
    from yulon.controller_wow_wotlk import maintenance

    fresh = Db([], realm="10.0.0.7\t127.0.0.1", fail_load_of="acore_characters")
    mv = Move(tmp_path, fresh_db=fresh)
    with pytest.raises(MoveError):
        mv.run()
    record = maintenance.interrupted_restore(mv.target.server_dir)
    assert record is not None, "the cut-off load left no marker"
    said = maintenance.unfinished_restore_refusal(mv.target.server_dir)
    assert said is not None and "Bring from another computer again" in said, said
    mv.target.db.fail_load_of = None
    installs = mv.engine.runs

    import time

    time.sleep(1.1)  # the engine's safety copies are named by the second
    lines = mv.run()

    assert mv.engine.runs == installs, "the install (and its start) ran on half-loaded data"
    assert any("not started on them" in line for line in lines), lines
    assert maintenance.interrupted_restore(mv.target.server_dir) is None
    assert not (mv.target.server_dir / move_server.MOVE_IN_FILE).exists()
