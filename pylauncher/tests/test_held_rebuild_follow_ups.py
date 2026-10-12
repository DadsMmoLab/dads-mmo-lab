"""T741: what T739's held Rebuild left to settle (its round-2 cold review, ACCEPT with notes).

1. The Rebuild question said the held text only for the record from empty. Under T658's kill
   record the press holds too (unless the databases hold accounts or characters), and for a
   server inside WSL the question cannot read the record at all (T133), so it says the
   condition instead of promising a copy, the updates and a start the press will not run.
2. A held success lets go of the old build's `-rollback` names (pinned here; the reason they
   are not kept to the first Start is in `StagedInstaller.rebuild()`).
3. Repair's importer after a held Rebuild: measured on m910q, 2026-10-12, Compose 2.10.0 (the
   floor) and 5.6.0, `compose up --no-deps --exit-code-from <svc> <svc>` RECREATES a one-shot
   whose tag moved and runs the new image (.notes/gates/t741-compose-recreate-m910q-2026-10-12/).
   No code; `test_docker.test_repair_import_names_only_the_one_shot_service` pins that argv.
4. A held press whose removal of an old server container fails with the build gone: the same
   measurement shows a Start's `up -d --no-deps` recreates a container whose tag moved, so the
   old build cannot run; what the press SAID was false twice ("a later Start could run the
   old one", "the containers now run the new build").

Every press is the real `rebuild()` through `tests.support_native.Recorder`, on the in-memory
databases of `test_unfinished_import_never_starts`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import ENTRY
from tests.test_ended_import_is_not_trusted import _done, _Server
from tests.test_unfinished_import_never_starts import (
    AUTH,
    CHARACTERS,
    KILLED,
    SERVICE,
    WORLD,
    _crashed_first_import,
    _machine,
    _no_world_started,
    _press,
    _repair,
)
from tests.test_update_rollback_database import TBC
from yulon import docker
from yulon.catalog import native
from yulon.catalog.installer import InstallerError, rebuild_confirmation

HELD_TEXTS = (
    native.HELD_CONFIRMATION,
    native.HELD_CONFIRMATION_IF_CUT_OFF,
    native.HELD_CONFIRMATION_IF_UNREAD,
)


def _held_texts_in(asked: str) -> list[str]:
    return [text for text in HELD_TEXTS if text in asked]


# -- 1. the question says what the press will do --------------------------------------------


def test_the_question_under_a_cut_off_import_says_when_the_press_holds(tmp_path: Path) -> None:
    """T658's kill record: the press holds unless the databases hold accounts or characters
    (`unfinished_import_refusal(strict=True)`), which the GUI thread cannot ask, so the
    question says exactly that condition, and the press then does what it said."""
    server = _Server({AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    docker.one_shot_ended_marker(server_dir, SERVICE).write_text(KILLED, encoding="utf-8")

    asked = rebuild_confirmation(ENTRY, server_dir)

    assert _held_texts_in(asked) == [native.HELD_CONFIRMATION_IF_CUT_OFF], asked
    assert "accounts or characters" in native.HELD_CONFIRMATION_IF_CUT_OFF
    assert "cannot be read" in native.HELD_CONFIRMATION_IF_CUT_OFF
    # Imported, no account: the condition is met and the press holds, as the question said.
    said = _press(made, server_dir)
    assert said[-1] == native.held_rebuild_ending(ENTRY.name, server_dir), said[-3:]
    _no_world_started(rec)


def test_the_cut_off_question_over_a_players_rows_and_the_press_agree(tmp_path: Path) -> None:
    """Same record over a player's rows: the question is the same (it cannot ask), and its
    condition is not met, so the press imports and starts as the rest of the question says."""
    server = _Server(
        {AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")},
        counts={f"{AUTH}.account": 3, f"{CHARACTERS}.characters": 7},
    )
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    docker.one_shot_ended_marker(server_dir, SERVICE).write_text(KILLED, encoding="utf-8")

    asked = rebuild_confirmation(ENTRY, server_dir)

    assert _held_texts_in(asked) == [native.HELD_CONFIRMATION_IF_CUT_OFF], asked
    assert "copies your databases" in asked, "the press's own path is still described"
    said = _press(made, server_dir)
    assert rec.calls.count("recreate") == 1, rec.calls
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"


@pytest.mark.parametrize("record", ["none", "from-empty", "kill"])
def test_a_wsl_servers_question_says_the_condition_without_reading_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record: str
) -> None:
    """`kept_build` False: a server inside WSL, whose record a read would boot the distro to
    reach (T133). The question reads nothing and says when the press holds."""
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    if record == "from-empty":
        docker.mark_import_under_way(server_dir, SERVICE)
    elif record == "kill":
        docker.one_shot_ended_marker(server_dir, SERVICE).write_text(KILLED, encoding="utf-8")

    def no_read(*_a: object, **_kw: object) -> object:
        raise AssertionError("the record was read for a server inside WSL")

    monkeypatch.setattr(docker, "one_shot_ended_marker", no_read)
    monkeypatch.setattr(docker, "import_record_is_from_empty", no_read)

    asked = rebuild_confirmation(ENTRY, server_dir, kept_build=False)

    assert _held_texts_in(asked) == [native.HELD_CONFIRMATION_IF_UNREAD], asked
    assert "copies your databases" in asked


def test_the_held_texts_are_said_only_where_a_press_can_hold(tmp_path: Path) -> None:
    """No record on a local server, and a family with no import one-shot (TBC) anywhere:
    none of the held texts. The record from empty keeps T739's own text alone."""
    rec_dir = tmp_path / "wow"
    rec_dir.mkdir()
    assert _held_texts_in(rebuild_confirmation(ENTRY, rec_dir)) == []
    tbc_dir = tmp_path / "tbc"
    tbc_dir.mkdir()
    assert TBC is not None and not TBC.containers.db_import
    assert _held_texts_in(rebuild_confirmation(TBC, tbc_dir, kept_build=False)) == []
    assert _held_texts_in(rebuild_confirmation(TBC, tbc_dir)) == []
    docker.mark_import_under_way(rec_dir, SERVICE)
    assert _held_texts_in(rebuild_confirmation(ENTRY, rec_dir)) == [native.HELD_CONFIRMATION]


# -- 2. a held success lets the old build's rollback names go ----------------------------------


def test_a_held_success_lets_go_of_every_rollback_name(tmp_path: Path) -> None:
    """The decision (T741 item 2): not kept to the first Start. Nothing after the press can
    put them back, and Repair clears and re-imports with the NEW importer, so the old build
    would start on databases no rollback ever pairs it with."""
    server = _Server({})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    _crashed_first_import(server_dir, server)
    rec.ready = False

    said = _press(made, server_dir)

    assert said[-1] == native.held_rebuild_ending(ENTRY.name, server_dir), said[-3:]
    kept = [
        c.split("->", 1)[1]
        for c in rec.calls
        if c.startswith("tag:") and c.endswith(native.ROLLBACK_TAG_SUFFIX)
    ]
    assert kept, "the ground: the build was there, so a rollback was kept"
    let_go = {c.split(":", 1)[1] for c in rec.calls if c.startswith(("rmi:", "rmi -f:"))}
    assert set(kept) <= let_go, (kept, rec.calls)


# -- 4. a removal refused with the build gone -------------------------------------------------


def test_a_refused_removal_with_the_build_gone_says_what_is_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The world's container could not be removed and no rollback was kept (T170). Nothing
    was started; the record still refuses Start; Repair still works. The sentence no longer
    says a Start could run the old build (Compose recreates a container whose tag moved,
    measured) nor that the containers run the new build (nothing started)."""
    server = _Server({})
    rec, server_dir, made, fake = _machine(tmp_path, server)
    _crashed_first_import(server_dir, server)
    record = docker.one_shot_ended_marker(server_dir, SERVICE)
    before = record.read_text(encoding="utf-8")
    rec.images = False
    rec.ready = False
    spec = ENTRY.container_spec()
    world = spec.service_for(spec.world)
    rec.remove_service_refused = {world}

    with pytest.raises(InstallerError) as raised:
        _press(made, server_dir, missing_images_ok=True)

    said = str(raised.value)
    assert f"remove_service:{world}" in rec.calls, "the ground: the removal was asked and refused"
    assert native.HELD_NO_ROLLBACK in said, said
    assert native.NO_ROLLBACK_BUILT not in said, said
    assert "could run the old one" not in said, said
    assert world in said, "the container is named"
    _no_world_started(rec)
    assert not fake.taken
    assert record.read_text(encoding="utf-8") == before
    assert made.start_refusal(server_dir) == native.UNFINISHED_IMPORT_REFUSAL
    assert _repair(monkeypatch, server_dir, server) is True
    assert not record.exists()


def test_a_refused_removal_with_a_rollback_kept_puts_it_back_stopped(tmp_path: Path) -> None:
    """The build there: the usual rollback, left STOPPED by the record (T739), unchanged."""
    server = _Server({})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    _crashed_first_import(server_dir, server)
    rec.ready = False
    spec = ENTRY.container_spec()
    rec.remove_service_refused = {spec.service_for(spec.world)}

    with pytest.raises(InstallerError) as raised:
        _press(made, server_dir)

    said = str(raised.value)
    assert "left STOPPED" in said and native.UNFINISHED_IMPORT_REFUSAL in said, said
    assert native.HELD_NO_ROLLBACK not in said
    assert "could run the old one" not in said, said
    _no_world_started(rec)
