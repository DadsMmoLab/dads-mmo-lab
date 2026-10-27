"""T770: a Tortoise update must not run a migration again because upstream changed its bytes.

Tortoise's worldserver keys each migration it ran by `<module>:<SHA-1 of the file>`
(`AutoUpdater.cpp` `GetMigrationKey()`, `ProcessTargetUpdates()`): a file whose bytes
changed under the same name is a new key, so the next start runs it again with the new
bytes, and a migration that cannot run twice stops the world. "Update the server to
latest…" and a forward "Return to the tested pin…" must therefore refuse, by name and
before anything is built, when the target changes a migration the database already ran.

Every test drives the real `update_to_latest()` on an installed Tortoise folder, with git's
trees at the `tree_bytes` seam and the `migrations` ledger at the `sql_query` seam, as
`tests/test_return_newer_migrations.py` does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import Recorder
from tests.test_return_newer_migrations import (
    BOTS,
    BOTS_PIN,
    BOTS_WORLD,
    CORE,
    CORE_AUTH,
    CORE_PIN,
    CORE_WORLD,
    OLDER,
    OLDER_NAME,
    TORTOISE,
    _asked,
    _built,
    _hash,
    _ready,
    _return,
)
from tests.test_update_to_latest import (  # noqa: F401 - `_gated` is an autouse fixture
    NEW,
    OLD,
    _gated,
)
from yulon.catalog import native

RAN = b"INSERT INTO `t` VALUES (5);\n"
"""The bytes the running server applied (a plain INSERT: it cannot run twice)."""
EDITED = b"INSERT INTO `t` VALUES (5);\nINSERT INTO `t` VALUES (6);\n"
"""The same file as the target ships it."""
assert RAN != OLDER, "the ran bytes must not be the unchanged file's"
BOTS_NAME = "20260918120000_world.sql"
"""TortoiseBots changed this file between v26 and v27 (T168's gate README, T600 section 5)."""

PRESSES = pytest.mark.parametrize("to_pin", [False, True], ids=["update-to-latest", "return"])


def _target(to_pin: bool, *, bots: bool = False) -> str:
    if to_pin:
        return BOTS_PIN if bots else CORE_PIN
    return NEW


def _forward(rec: Recorder, server_dir: Path, to_pin: bool) -> None:
    """The target descends from the running commit (git says so), for both sources."""
    rec.ancestors.add((server_dir / CORE.dest, OLD, _target(to_pin)))
    rec.ancestors.add((server_dir / BOTS.dest, OLD, _target(to_pin, bots=True)))


def _core_changed(rec: Recorder, server_dir: Path, to_pin: bool, *, held: bool = True) -> None:
    """The running commit ships `OLDER_NAME` as `RAN`; the target ships it as `EDITED`."""
    dest = server_dir / CORE.dest
    rec.byte_trees[(dest, OLD, CORE_WORLD)] = {OLDER_NAME: RAN}
    rec.byte_trees[(dest, _target(to_pin), CORE_WORLD)] = {
        OLDER_NAME: EDITED,
        "20261201000000_world.sql": b"-- a new one\n",
    }
    if held:
        rec.migrations["tw_world"] = f":{_hash(RAN)}\n"
    _forward(rec, server_dir, to_pin)


def _refused_clean(rec: Recorder, server_dir: Path) -> None:
    assert {rec.heads[server_dir / s.dest] for s in TORTOISE.emulator.sources} == {OLD}
    assert not _built(rec), rec.calls


def _made_without_allowlist(rec: Recorder) -> native.StagedInstaller:
    """An engine over TORTOISE with no re-runnable modules: the pre-T813 baseline.

    The shipped entry now lists TortoiseBots (T813), so a test pinning the
    refusal itself drives an entry without the listing; the T813 file pins what
    the listing does.
    """
    from tests.test_families_cmangos import engine as tbc_engine
    from tests.support_native import FakeSnapshot

    assert TORTOISE.install.native is not None and TORTOISE.install.native.cmangos is not None
    data = TORTOISE.install.native.cmangos.model_copy(update={"rerunnable_modules": ()})
    native_block = TORTOISE.install.native.model_copy(update={"cmangos": data})
    plain = TORTOISE.model_copy(
        update={"install": TORTOISE.install.model_copy(update={"native": native_block})}
    )
    made = tbc_engine(rec, entry=plain, world_running=lambda container: False)
    made._snapshot = FakeSnapshot(rec)  # type: ignore[attr-defined]
    return made


# -- the refusal ----------------------------------------------------------------------------


@PRESSES
def test_a_changed_migration_the_database_ran_refuses_by_name(tmp_path: Path, to_pin: bool) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_changed(rec, server_dir, to_pin)

    said, raised = _return(made, server_dir, to_pin=to_pin)

    assert raised is not None, "the press went on to run a changed migration again"
    message = str(raised)
    assert f"tw_world: {OLDER_NAME}" in message, message
    assert "would run it again with the new bytes" in message, message
    assert "has no safe way to skip a migration or mark it done" in message, message
    assert "Nothing was built, stopped or changed" in message
    assert native.SOURCES_PUT_BACK_NOTE in message
    _refused_clean(rec, server_dir)
    assert rec.calls.index("start-db") < rec.calls.index("query")
    assert any("asking the databases whether they already ran it" in line for line in said), said


def test_update_to_latest_remembers_the_refused_commit(tmp_path: Path) -> None:
    """A second press cannot change what this upstream commit brings (T179's `UpdateRefused`)."""
    rec, server_dir, made = _ready(tmp_path)
    _core_changed(rec, server_dir, False)

    _said, raised = _return(made, server_dir, to_pin=False)

    assert raised is not None
    record = native.read_state(server_dir, valid=made.stage_names())
    assert record is not None and (CORE.repo, NEW) in record.refused_updates, record


@PRESSES
def test_a_module_migration_is_named_with_its_module(tmp_path: Path, to_pin: bool) -> None:
    rec, server_dir, _made = _ready(tmp_path)
    made = _made_without_allowlist(rec)
    bots = server_dir / BOTS.dest
    rec.byte_trees[(bots, OLD, BOTS_WORLD)] = {BOTS_NAME: RAN}
    rec.byte_trees[(bots, _target(to_pin, bots=True), BOTS_WORLD)] = {BOTS_NAME: EDITED}
    if not to_pin:
        from tests.test_return_newer_migrations import BOTS_CMAKE, CMAKE

        rec.blobs[(bots, NEW, BOTS_CMAKE)] = CMAKE
    rec.migrations["tw_world"] = f"TortoiseBots:{_hash(RAN)}\n"
    _forward(rec, server_dir, to_pin)

    _said, raised = _return(made, server_dir, to_pin=to_pin)

    assert raised is not None
    assert f"tw_world: TortoiseBots/{BOTS_NAME}" in str(raised), raised
    _refused_clean(rec, server_dir)


def test_the_same_bytes_under_another_module_are_not_what_the_database_ran(
    tmp_path: Path,
) -> None:
    """The ledger key carries the module: the core's key for those bytes is not TortoiseBots'."""
    rec, server_dir, made = _ready(tmp_path)
    _core_changed(rec, server_dir, False, held=False)
    rec.migrations["tw_world"] = f"TortoiseBots:{_hash(RAN)}\n"

    _said, raised = _return(made, server_dir, to_pin=False)

    assert raised is None, raised
    assert _asked(rec), "the database was never asked"


def test_the_world_file_the_image_rewrites_is_asked_by_both_spellings(tmp_path: Path) -> None:
    """The Dockerfile's `sed` makes `INSERT IGNORE INTO`; the ledger holds that copy's hash."""
    rec, server_dir, made = _ready(tmp_path)
    dest = server_dir / CORE.dest
    sedded = "20260903063722_world.sql"
    rewritten = RAN.replace(b"INSERT INTO", b"INSERT IGNORE INTO")
    rec.byte_trees[(dest, OLD, CORE_WORLD)] = {sedded: RAN}
    rec.byte_trees[(dest, NEW, CORE_WORLD)] = {sedded: EDITED}
    rec.migrations["tw_world"] = f":{_hash(rewritten)}\n"

    _said, raised = _return(made, server_dir, to_pin=False)

    assert raised is not None and f"tw_world: {sedded}" in str(raised), raised


def test_every_changed_file_of_every_database_is_named(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    dest = server_dir / CORE.dest
    auth = b"ALTER TABLE `account` ADD `y` INT;\n"
    rec.byte_trees[(dest, OLD, CORE_AUTH)] = {"20261003_auth.sql": auth}
    rec.byte_trees[(dest, NEW, CORE_AUTH)] = {"20261003_auth.sql": auth + b"-- edited\n"}
    _core_changed(rec, server_dir, False)
    rec.migrations["tw_logon"] = f":{_hash(auth)}\n"

    _said, raised = _return(made, server_dir, to_pin=False)

    message = str(raised)
    assert "tw_logon: 20261003_auth.sql" in message, message
    assert f"tw_world: {OLDER_NAME}" in message, message
    assert "changes 2 database migrations your server already ran" in message, message
    assert "would run them again with the new bytes" in message, message


# -- what goes on ---------------------------------------------------------------------------


@PRESSES
def test_an_unchanged_migration_asks_nothing(tmp_path: Path, to_pin: bool) -> None:
    rec, server_dir, made = _ready(tmp_path)
    dest = server_dir / CORE.dest
    rec.byte_trees[(dest, _target(to_pin), CORE_WORLD)] = {
        OLDER_NAME: OLDER,
        "20261201000000_world.sql": b"-- a new one\n",
    }
    rec.migrations["tw_world"] = f":{_hash(OLDER)}\n"
    _forward(rec, server_dir, to_pin)

    _said, raised = _return(made, server_dir, to_pin=to_pin)

    assert raised is None, raised
    assert not _asked(rec), "the database was asked although no migration changed"
    assert "start-db" not in rec.calls


@PRESSES
def test_a_changed_migration_the_database_never_ran_goes_on(tmp_path: Path, to_pin: bool) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_changed(rec, server_dir, to_pin, held=False)
    rec.migrations["tw_world"] = f":{_hash(OLDER)}\n"

    said, raised = _return(made, server_dir, to_pin=to_pin)

    assert raised is None, raised
    assert _asked(rec), "the database was never asked"
    assert any("so no migration runs a second time" in line for line in said), said


def test_new_bytes_the_database_already_ran_go_on(tmp_path: Path) -> None:
    """The ledger holds the target's key too: the server skips that file at its next start."""
    rec, server_dir, made = _ready(tmp_path)
    _core_changed(rec, server_dir, False)
    rec.migrations["tw_world"] = f":{_hash(RAN)}\n:{_hash(EDITED)}\n"

    _said, raised = _return(made, server_dir, to_pin=False)

    assert raised is None, raised


def test_a_migration_upstream_deleted_is_not_a_changed_one(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    dest = server_dir / CORE.dest
    rec.byte_trees[(dest, OLD, CORE_WORLD)] = {OLDER_NAME: OLDER, "20260601000000_world.sql": RAN}
    rec.byte_trees[(dest, NEW, CORE_WORLD)] = {OLDER_NAME: OLDER}
    rec.migrations["tw_world"] = f":{_hash(RAN)}\n"

    _said, raised = _return(made, server_dir, to_pin=False)

    assert raised is None, raised
    assert not _asked(rec)


def test_the_old_bytes_moved_to_another_name_are_not_run_again(tmp_path: Path) -> None:
    """The target ships the ran bytes under a new name: that key is still in the ledger."""
    rec, server_dir, made = _ready(tmp_path)
    dest = server_dir / CORE.dest
    rec.byte_trees[(dest, OLD, CORE_WORLD)] = {OLDER_NAME: RAN}
    rec.byte_trees[(dest, NEW, CORE_WORLD)] = {OLDER_NAME: EDITED, "20260101_moved.sql": RAN}
    rec.migrations["tw_world"] = f":{_hash(RAN)}\n"

    _said, raised = _return(made, server_dir, to_pin=False)

    assert raised is None, raised


def test_a_backward_return_keeps_the_newer_migrations_refusal(tmp_path: Path) -> None:
    """Going back, the changed file is one the target lacks (T632), and that refusal says so."""
    rec, server_dir, made = _ready(tmp_path)
    dest = server_dir / CORE.dest
    rec.byte_trees[(dest, OLD, CORE_WORLD)] = {OLDER_NAME: RAN}
    rec.byte_trees[(dest, CORE_PIN, CORE_WORLD)] = {OLDER_NAME: EDITED}
    rec.migrations["tw_world"] = f":{_hash(RAN)}\n"

    _said, raised = _return(made, server_dir, to_pin=True)

    assert raised is not None
    message = str(raised)
    assert "tw_world has 1 migration the tested commit does not have" in message, message
    assert "would run it again" not in message


# -- the database is put back, and git that cannot say refuses ------------------------------


@pytest.mark.parametrize("refused", [True, False], ids=["refused", "went-on"])
def test_a_database_the_check_started_goes_down_again(tmp_path: Path, refused: bool) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_changed(rec, server_dir, False, held=refused)
    rec.db_was_up = False

    _said, raised = _return(made, server_dir, to_pin=False)

    assert (raised is not None) is refused, raised
    stops = [c for c in rec.calls if c.startswith("stop-db:")]
    assert stops and rec.calls.index("start-db") < rec.calls.index(stops[0]), rec.calls


def test_a_database_that_was_up_is_left_up(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_changed(rec, server_dir, False)
    rec.db_was_up = True

    _said, raised = _return(made, server_dir, to_pin=False)

    assert raised is not None
    assert not any(c.startswith("stop-db:") for c in rec.calls), rec.calls


def test_a_database_that_cannot_answer_refuses(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_changed(rec, server_dir, False)
    rec.updates_error = "ERROR 2002: cannot connect"

    _said, raised = _return(made, server_dir, to_pin=False)

    assert raised is not None
    message = str(raised)
    assert "Yu'lon could not ask tw_world which of them it already has" in message, message
    _refused_clean(rec, server_dir)


def test_git_that_cannot_list_a_folder_refuses_an_update_in_its_own_words(
    tmp_path: Path,
) -> None:
    rec, server_dir, made = _ready(tmp_path)
    rec.byte_trees[(server_dir / CORE.dest, NEW, CORE_AUTH)] = None

    _said, raised = _return(made, server_dir, to_pin=False)

    assert raised is not None
    message = str(raised)
    assert "Yu'lon could not read which database migrations this update changes" in message
    assert "going back" not in message, message
    assert "git could not list sql/database_updates/auth" in message
    _refused_clean(rec, server_dir)
    assert "start-db" not in rec.calls


# -- round 2: no other press offered, how long it lasts, every source, one ask ---------------


@PRESSES
def test_the_refusal_offers_no_other_press_as_a_way_forward(tmp_path: Path, to_pin: bool) -> None:
    """Either press moves to code that still carries the edited file, so it refuses too."""
    from yulon import server_build_presses

    rec, server_dir, made = _ready(tmp_path)
    _core_changed(rec, server_dir, to_pin)

    _said, raised = _return(made, server_dir, to_pin=to_pin)

    assert raised is not None
    message = str(raised)
    for press in (server_build_presses.UPDATE_TO_LATEST, server_build_presses.RETURN_TO_PIN):
        assert server_build_presses.under_server_build(press) not in message, message


@PRESSES
def test_the_refusal_says_it_lasts_until_upstream_puts_the_old_contents_back(
    tmp_path: Path, to_pin: bool
) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_changed(rec, server_dir, to_pin)

    _said, raised = _return(made, server_dir, to_pin=to_pin)

    message = str(raised)
    assert (
        "until upstream ships the old contents of that file again or removes it; until then, "
        "keep the build you have." in message
    ), message


def test_both_refused_sources_are_remembered(tmp_path: Path) -> None:
    """The core and TortoiseBots both change a migration: neither commit is offered again."""
    from tests.test_return_newer_migrations import BOTS_CMAKE, CMAKE

    rec, server_dir, _made = _ready(tmp_path)
    made = _made_without_allowlist(rec)
    _core_changed(rec, server_dir, False)
    bots = server_dir / BOTS.dest
    rec.byte_trees[(bots, OLD, BOTS_WORLD)] = {BOTS_NAME: RAN}
    rec.byte_trees[(bots, NEW, BOTS_WORLD)] = {BOTS_NAME: EDITED}
    rec.blobs[(bots, NEW, BOTS_CMAKE)] = CMAKE
    rec.migrations["tw_world"] = f":{_hash(RAN)}\nTortoiseBots:{_hash(RAN)}\n"

    _said, raised = _return(made, server_dir, to_pin=False)

    assert raised is not None
    assert f"TortoiseBots/{BOTS_NAME}" in str(raised) and OLDER_NAME in str(raised)
    record = native.read_state(server_dir, valid=made.stage_names())
    assert record is not None
    assert {(CORE.repo, NEW), (BOTS.repo, NEW)} <= set(record.refused_updates), record


def test_a_return_asks_a_stopped_database_once_for_both_checks(tmp_path: Path) -> None:
    """Going back, the changed file is also one the target lacks: one start, one stop."""
    rec, server_dir, made = _ready(tmp_path)
    dest = server_dir / CORE.dest
    rec.byte_trees[(dest, OLD, CORE_WORLD)] = {OLDER_NAME: RAN}
    rec.byte_trees[(dest, CORE_PIN, CORE_WORLD)] = {OLDER_NAME: EDITED}
    rec.migrations["tw_world"] = f":{_hash(OLDER)}\n"
    rec.db_was_up = False

    said, raised = _return(made, server_dir, to_pin=True)

    assert raised is None, raised
    assert any("None of them was applied" in line for line in said), said
    assert any("so no migration runs a second time" in line for line in said), said
    assert rec.calls.count("start-db") == 1, rec.calls
    assert len([c for c in rec.calls if c.startswith("stop-db:")]) == 1, rec.calls
