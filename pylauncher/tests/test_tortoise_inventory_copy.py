"""T159: Tortoise's honor maintenance truncates a table no SQL on the Penqle core creates.

`HonorMaintenancer::DoMaintenance()` runs inside `World::SetInitialWorldSettings()`
on the first start after a running world has marked the week's honor day
(`saved_variables.honorMaintenanceMarker`). With `BackupCharacterInventory = 1`,
the dist conf's value and one the catalog leaves alone, it calls
`ObjectMgr::BackupCharacterInventory()`: `TRUNCATE character_inventory_copy`, then
`INSERT INTO character_inventory_copy SELECT * FROM character_inventory`. Nothing
at the pinned core (187af788) creates that table -- not `create_databases.sql`,
not `database_updates/`, not TortoiseBots' `data/sql` -- so the TRUNCATE fails
with 1146, `HandleMySQLError` ASSERTs (a `std::runtime_error` nothing catches)
and the world dies. The marker is only cleared by a maintenance that finished,
so every restart dies at the same statement: a crash loop under
`restart: unless-stopped`, measured once already on the retired fork's C++
(`.notes/gates/7.9-rerun-m910q-2026-09-09/README.md`, finding 1).

The fix is data: one `wow-tortoise` phase that creates the table
`LIKE character_inventory`, marked `reapply_when_changed`, so a new install gets
it with the import and an install made before it is offered T129's "Apply
database corrections…". The part of this file that is not about data is the
press itself: the install this matters most for is one whose world is ALREADY
crash-looping, and T129's press refused a world that read as running -- which a
container in restart backoff always does -- while the Server tab's Stop takes the
database down with it, and the banner goes with the database. So the press now
stops the world server itself, after the person said Yes to a dialog that says
so, and leaves it stopped.

The database is `test_plan_corrections._Mariadb` (sqlite, one file per schema,
every script EXECUTED), with one more translation beside that fake's own:
sqlite has no `CREATE TABLE ... LIKE`, so `_TortoiseDb` spells it
`CREATE TABLE ... AS SELECT * FROM <source> WHERE 0`, which copies the columns
and not the keys. What that proves is that the statement lands, once, in
`tw_char`, and that the core's own copy statement then works against it; the
MyISAM engine and keys `LIKE` also copies are MariaDB's, and are the live
check's to see. Unit tests only: no box was pressed for this change.
"""

from __future__ import annotations

import io
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

import pytest

from tests.support_native import Recorder
from tests.test_plan_corrections import _answer, _Mariadb, _Route, _view, ps  # noqa: F401
from yulon import docker, resources
from yulon.catalog import native, released_plans
from yulon.catalog.catalog import SqlPhase, load_catalog
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError, InstallOptions

CATALOG = load_catalog()
TORTOISE = CATALOG.get("wow-tortoise")
PHASE = "character_inventory_copy table"
STATEMENT = "CREATE TABLE IF NOT EXISTS `character_inventory_copy` LIKE `character_inventory`"
SCHEMAS = ("tw_world", "tw_char", "tw_logon", "tw_logs")
WORLD = "tortoise-mangosd"
RELEASED_TORTOISE = "6f0ae5810a40956a"
"""The plan hash every public release from v0.8.4 marked a Tortoise install with (T129)."""

CRASH = (
    "Making copy of character_inventory table.\n"
    "SQL: TRUNCATE `character_inventory_copy`\n"
    "[1146] Table 'tw_char.character_inventory_copy' doesn't exist\n"
    "Your database structure is not up to date. Please make sure you have executed all the "
    "queries in the sql/updates folders.\n"
    "/src/src/shared/Database/DatabaseMysql.cpp:190: Error: Assertion in HandleMySQLError "
    "failed: false\n"
)
"""The world server's last words, verbatim from the 2026-09-09 crash (7.9 finding 1)."""

BACKUP = (
    "DELETE FROM character_inventory_copy;\n"
    "INSERT INTO character_inventory_copy SELECT * FROM character_inventory;\n"
)
"""`ObjectMgr::BackupCharacterInventory()`'s copy, in sqlite (`TRUNCATE` is `DELETE FROM`).

The two statements that decide whether maintenance survives; the DISABLE/ENABLE
KEYS pair between them is MyISAM's and has no sqlite spelling."""


class _TortoiseDb(_Mariadb):
    """`_Mariadb` over Tortoise's four schemas, with `character_inventory` as the core makes it.

    The one addition is `LIKE`: see the module docstring for what the stand-in
    spelling does and does not prove.
    """

    _LIKE = re.compile(r"CREATE TABLE IF NOT EXISTS (`?\w+`?) LIKE (`?\w+`?)", flags=re.IGNORECASE)

    def __init__(self, root: Path) -> None:
        super().__init__(root, SCHEMAS)
        self.exec_stdin_raw(
            "tw_char",
            "CREATE TABLE character_inventory (guid INT, bag INT, slot INT, item INT PRIMARY KEY, "
            "item_template INT);\n"
            "INSERT INTO character_inventory VALUES (1, 0, 23, 101, 6948), "
            "(1, 0, 24, 102, 2508);\n",
        )

    def exec_stdin_raw(self, schema: str, text: str) -> None:
        result = super().exec_stdin(
            "tortoise-db",
            ["mariadb", "-u", "root", schema],
            io.BytesIO(text.encode("utf-8")),
            env={},
        )
        assert result.returncode == 0, result.stderr

    def exec_stdin(
        self,
        container: str,
        argv: Sequence[str],
        source: BinaryIO,
        *,
        env: Mapping[str, str],
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        text = self._LIKE.sub(
            r"CREATE TABLE IF NOT EXISTS \1 AS SELECT * FROM \2 WHERE 0",
            source.read().decode("utf-8"),
        )
        return super().exec_stdin(
            container, argv, io.BytesIO(text.encode("utf-8")), env=env, wsl_distro=wsl_distro
        )


def marked_by_a_release(db: _TortoiseDb) -> None:
    """The marker a v0.8.4-v0.8.90 import wrote: the row, in `tw_world`, and nothing beside it."""
    db.exec_stdin_raw(
        "tw_world",
        "CREATE TABLE IF NOT EXISTS yulon_install "
        "(plan_hash CHAR(16) NOT NULL, finished_unix BIGINT NOT NULL);\n"
        f"INSERT INTO yulon_install (plan_hash, finished_unix) VALUES ('{RELEASED_TORTOISE}', "
        "1757000000);\n",
    )


class _LoopingWorld:
    """A world container under `restart: unless-stopped` that dies at every start.

    It reads as running whenever it is asked -- `docker.world_running()` counts
    `restarting` as up, and a crash-looping mangosd spends most of each cycle
    `running` its startup -- until something stops it. `obeys` False is the
    container that is back again after the stop.
    """

    def __init__(self, events: list[str], *, obeys: bool = True) -> None:
        self.events = events
        self.obeys = obeys
        self.stopped = False
        self.asked = 0

    def running(self, container: str) -> bool | None:
        assert container == WORLD, container
        self.asked += 1
        return not self.stopped

    def stop(self, containers: list[str]) -> None:
        self.events.append(f"stop:{','.join(containers)}")
        self.stopped = self.obeys


def an_engine(
    db: _TortoiseDb,
    *,
    world_running: Callable[[str], bool | None] = lambda container: False,
    stop_world: Callable[[list[str]], None] | None = None,
    events: list[str] | None = None,
) -> CmangosInstaller:
    """The shipped Tortoise entry, its databases `db`, and its world whatever `world_running` says.

    `events`, when given, gets `"sql"` for every script sent, so a test can
    order the writes against the stop.
    """
    rec = Recorder()
    rec.db_started = True

    def exec_stdin(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if events is not None:
            events.append("sql")
        return db.exec_stdin(*args, **kwargs)  # type: ignore[arg-type]

    overrides: dict[str, object] = {
        "platform_id": lambda: "linux",
        "exec_stdin": exec_stdin,
        "sql_query": db.query,
        "world_running": world_running,
        "db_running": lambda container: True,
    }
    if stop_world is not None:
        overrides["stop_world"] = stop_world
    return CmangosInstaller(
        TORTOISE, installers_root=resources.installers_dir(), seams=rec.seams(**overrides)
    )


def folder(tmp_path: Path) -> InstallOptions:
    server_dir = tmp_path / "tortoise-wow-server"
    server_dir.mkdir(exist_ok=True)
    (server_dir / ".db_password").write_text("tortoise-0123456789abcdef\n", encoding="utf-8")
    return InstallOptions(server_dir=server_dir)


def _phase() -> SqlPhase:
    data = TORTOISE.install.native.cmangos  # type: ignore[union-attr]
    return next(phase for phase in data.sql.phases if phase.name == PHASE)


# -- the plan ------------------------------------------------------------------


def test_the_tortoise_plan_creates_the_table_honor_maintenance_truncates() -> None:
    """One phase, one statement, into the characters schema, after it exists and before the realm.

    Catches the phase missing, pointed at another schema (the core truncates
    `tw_char`'s), spelled without `IF NOT EXISTS` (a second press, or a server
    whose table was made by hand, would fail the phase), and placed before
    `schemas` -- the phase that creates `character_inventory` for `LIKE` to copy.
    """
    phase = _phase()
    assert phase.into == TORTOISE.databases.characters == "tw_char"
    assert phase.statements == (STATEMENT,)
    assert phase.on_error == "fail", "a warn phase would restore the silence this ends"
    names = [p.name for p in TORTOISE.install.native.cmangos.sql.phases]  # type: ignore[union-attr]
    assert names.index("schemas") < names.index(PHASE) < names.index("realm row"), names


def test_the_phase_is_offered_to_a_server_already_imported_and_not_run_on_every_press() -> None:
    """`reapply_when_changed`, the T129 route with a dialog, and not T11's silent re-run.

    Catches the flag missing (every existing server stays one honor day from
    the loop) and `rerun_on_marked` chosen instead, which the catalog-wide guard
    in `test_tortoise_boot_facts.py` exists to make deliberate.
    """
    phase = _phase()
    assert phase.reapply_when_changed is True
    assert phase.rerun_on_marked is False
    assert native.correction_phases(TORTOISE) == (phase,)
    assert native.update_phases(TORTOISE) == ()


def test_a_fresh_import_is_not_called_finished_without_the_table() -> None:
    """The `verify` rule: the marker is written only after the table is there.

    Catches the rule missing, and one that asks another schema.
    """
    rules = TORTOISE.install.native.cmangos.sql.verify  # type: ignore[union-attr]
    mine = [rule for rule in rules if "character_inventory_copy" in rule.query]
    assert len(mine) == 1, rules
    assert mine[0].min == 1
    assert "table_schema='tw_char'" in mine[0].query


def test_every_released_tortoise_install_is_offered_exactly_this_phase() -> None:
    """Through the comparison the banner uses, against the release table.

    Catches the phase added under the name of one a release already recorded
    (it would read `current` and never be offered).
    """
    from yulon.catalog.families import sqlplan

    drift = sqlplan.phase_drift(
        TORTOISE.install.native.cmangos.sql,  # type: ignore[union-attr]
        released_plans.RELEASED_PHASE_DIGESTS[RELEASED_TORTOISE],
    )
    assert (drift.offered, drift.withheld) == ((PHASE,), ())


# -- the press, through the shipped entry and the real family engine -----------


def test_an_install_from_a_release_is_offered_the_table_and_the_press_makes_it_once(
    tmp_path: Path,
) -> None:
    """Read, press, read again: the table lands in `tw_char`, is recorded, and is offered no more.

    And the core's own copy then works on it, which is the whole point.

    Catches the statement sent to the wrong schema, the record not written (the
    banner would stay for the life of the install) and a second press that sent
    the statement again.
    """
    db = _TortoiseDb(tmp_path)
    marked_by_a_release(db)
    engine = an_engine(db)
    options = folder(tmp_path)
    check = engine.correction_check(options)
    assert (check.state, check.offered, check.withheld) == ("stale", (PHASE,), ()), check
    said = list(engine.apply_corrections(check, options))
    assert "character_inventory_copy" in db.tables("tw_char"), said
    assert "character_inventory_copy" not in db.tables("tw_world")
    assert engine.correction_check(options).state == "current"
    db.exec_stdin_raw("tw_char", BACKUP)
    assert db.rows("tw_char", "SELECT COUNT(*) FROM character_inventory_copy") == [(2,)]


def test_a_server_whose_table_was_made_by_hand_keeps_what_is_in_it(tmp_path: Path) -> None:
    """The retired fork's servers, and anyone who followed its SQL comment: the rows stay.

    Catches the statement spelled as a DROP-and-create.
    """
    db = _TortoiseDb(tmp_path)
    marked_by_a_release(db)
    db.exec_stdin_raw(
        "tw_char",
        "CREATE TABLE character_inventory_copy AS SELECT * FROM character_inventory;\n",
    )
    engine = an_engine(db)
    list(engine.apply_corrections(engine.correction_check(folder(tmp_path)), folder(tmp_path)))
    assert db.rows("tw_char", "SELECT COUNT(*) FROM character_inventory_copy") == [(2,)]


# -- a world that is crash-looping when the person presses ---------------------


def test_a_crash_looping_world_is_stopped_first_and_then_the_table_is_made(
    tmp_path: Path,
) -> None:
    """The case this ticket is about: the banner is up because the database is, and so is the loop.

    The world is stopped by this press, before any statement is sent, and is
    left stopped; the transcript says so and says what to press next.

    Catches the press refusing a world that reads as running (the T129 shape,
    which left no way to press it at all from the Server tab), and the stop
    happening after the first write.
    """
    events: list[str] = []
    db = _TortoiseDb(tmp_path)
    marked_by_a_release(db)
    looping = _LoopingWorld(events)
    engine = an_engine(db, world_running=looping.running, stop_world=looping.stop, events=events)
    options = folder(tmp_path)
    check = engine.correction_check(options)
    sent_before = len(db.scripts)
    said = list(engine.apply_corrections(check, options))
    assert events[0] == f"stop:{WORLD}", events
    assert events.count(f"stop:{WORLD}") == 1, events
    assert "sql" in events[1:], events
    assert len(db.scripts) > sent_before
    assert "character_inventory_copy" in db.tables("tw_char")
    assert looping.stopped
    assert any("stopped" in line.lower() and "Start" in line for line in said[-2:]), said


def test_a_world_that_is_back_after_the_stop_is_refused_and_nothing_is_sent(
    tmp_path: Path,
) -> None:
    """A stop that did not take: the reading before the write still refuses.

    Catches the press trusting the stop instead of asking again.
    """
    events: list[str] = []
    db = _TortoiseDb(tmp_path)
    marked_by_a_release(db)
    looping = _LoopingWorld(events, obeys=False)
    engine = an_engine(db, world_running=looping.running, stop_world=looping.stop)
    options = folder(tmp_path)
    check = engine.correction_check(options)
    sent_before = len(db.scripts)
    with pytest.raises(InstallerError, match="Nothing was applied"):
        list(engine.apply_corrections(check, options))
    assert db.scripts[sent_before:] == []
    assert "character_inventory_copy" not in db.tables("tw_char")


def test_a_world_that_cannot_be_stopped_is_refused_and_nothing_is_sent(tmp_path: Path) -> None:
    """The stop raising is a refusal in words, not a traceback and not a write."""
    db = _TortoiseDb(tmp_path)
    marked_by_a_release(db)

    def refuses(containers: list[str]) -> None:
        raise docker.DockerCommandError("Error response from daemon: cannot stop container")

    engine = an_engine(db, world_running=lambda container: True, stop_world=refuses)
    options = folder(tmp_path)
    check = engine.correction_check(options)
    sent_before = len(db.scripts)
    with pytest.raises(InstallerError, match="could not stop") as caught:
        list(engine.apply_corrections(check, options))
    assert "Nothing was applied" in str(caught.value)
    assert db.scripts[sent_before:] == []


def test_a_running_world_is_not_stopped_for_a_reading_that_no_longer_stands(
    tmp_path: Path,
) -> None:
    """A dialog from before the table landed: nothing to apply, so the world is left running.

    The family refuses such a press anyway; this is about the stop, which would
    otherwise be the one thing that press did.

    Catches the stop taken before the offer is read again.
    """
    events: list[str] = []
    db = _TortoiseDb(tmp_path)
    marked_by_a_release(db)
    options = folder(tmp_path)
    old = an_engine(db).correction_check(options)
    list(an_engine(db).apply_corrections(old, options))
    running = _LoopingWorld(events)
    engine = an_engine(db, world_running=running.running, stop_world=running.stop)
    sent_before = len(db.scripts)
    with pytest.raises(InstallerError, match="changed since") as caught:
        list(engine.apply_corrections(old, options))
    assert "Nothing was stopped" in str(caught.value)
    assert events == []
    assert db.scripts[sent_before:] == []


def test_an_unreadable_world_is_still_refused_without_a_stop(tmp_path: Path) -> None:
    """`None` is "could not ask", and a stop sent on it would be a guess about docker.

    Catches the stop taken on anything but an explicit running.
    """
    events: list[str] = []
    db = _TortoiseDb(tmp_path)
    marked_by_a_release(db)
    looping = _LoopingWorld(events)
    engine = an_engine(db, world_running=lambda container: None, stop_world=looping.stop)
    options = folder(tmp_path)
    check = engine.correction_check(options)
    with pytest.raises(InstallerError, match="could not tell"):
        list(engine.apply_corrections(check, options))
    assert events == []


def test_the_dialog_says_the_press_stops_the_world_and_leaves_it_stopped(tmp_path: Path) -> None:
    """What the person agrees to includes the stop, because the press now does it.

    Catches the T129 wording ("press Stop first ... this press refuses while it
    is up") surviving a change that made it false.
    """
    db = _TortoiseDb(tmp_path)
    marked_by_a_release(db)
    engine = an_engine(db)
    options = folder(tmp_path)
    text = engine.correction_confirmation(engine.correction_check(options), options)
    assert PHASE in text
    assert "STOPS" in text and "press Start" in text, text
    assert "refuses while it is up" not in text


# -- what a failed start says --------------------------------------------------


class _DeadWorld:
    """The ready wait's two seams over a world that printed `text` and will never be ready.

    `looping` makes every look find the container restarted five more times, so
    the wait's crash-loop verdict answers before its fatal one does.
    """

    def __init__(self, text: str, *, looping: bool = False) -> None:
        self.text = text
        self.now = 0.0
        self.looping = looping
        self.restarts = 2

    def wait_ready(self, spec: object, ready: docker.ReadySpec, **_kwargs: object) -> bool:
        self.now += 1.0
        return False

    def output(self, spec: object, **_kwargs: object) -> native.WorldOutput:
        if self.looping:
            self.restarts += 5
        return native.WorldOutput(text=self.text, restarts=self.restarts, status="restarting")

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _refusal(entry: object, text: str, *, looping: bool = False) -> str:
    world = _DeadWorld(text, looping=looping)
    rec = Recorder()
    installer = CmangosInstaller(
        entry,  # type: ignore[arg-type]
        installers_root=resources.installers_dir(),
        seams=rec.seams(
            wait_ready=world.wait_ready,
            world_output=world.output,
            monotonic=world.clock,
            sleep=world.sleep,
        ),
    )
    ctx = native.StageContext(
        server_dir=Path("/srv/tortoise"),
        client_dir=None,
        state=native.InstallState("wow-tortoise", "abc", "cmangos"),
        cancel=None,
        secrets=native.Secrets("hunter2"),
    )
    with pytest.raises(InstallerError) as caught:
        list(installer.wait_for_ready(ctx, TORTOISE.install.native.ready))  # type: ignore[union-attr]
    return str(caught.value)


def test_a_start_that_dies_on_the_missing_table_names_the_button_that_makes_it() -> None:
    """Tortoise's own `fatal` already catches this line; the refusal now says what to press.

    Catches the hint missing, and the T63 hint (Apply module SQL, a Modules-tab
    remedy for a module's table) given for a table the install plan creates.
    """
    message = _refusal(TORTOISE, CRASH)
    assert "[1146] Table 'tw_char.character_inventory_copy' doesn't exist" in message
    assert native.CORRECTIONS_BUTTON_LABEL in message
    assert PHASE in message
    assert "Apply module SQL" not in message


def test_a_start_seen_as_a_crash_loop_names_the_button_too() -> None:
    """The other verdict this crash reaches: the restarts counted before the line was read.

    Catches the hint on the fatal sentence only.
    """
    message = _refusal(TORTOISE, CRASH, looping=True)
    assert "crash loop" in message
    assert native.CORRECTIONS_BUTTON_LABEL in message and PHASE in message


def test_a_server_that_dies_after_its_banner_gets_the_step_rather_than_the_module_remedy() -> None:
    """T71's after-ready sentence carries T63's Modules-tab hint, which is wrong for this table.

    Catches the after-ready path left on `MODULE_SQL_HINT` for a table the
    install plan's offered step makes, and the module hint lost for the table
    it was written for.
    """
    hint = native._missing_table_hint(CRASH, TORTOISE)
    assert native.CORRECTIONS_BUTTON_LABEL in hint and "Apply module SQL" not in hint
    module = "[1146] Table 'acore_world.city_bot_poi' doesn't exist"
    assert native._missing_table_hint(module, CATALOG.get("wow-wotlk")) == native.MODULE_SQL_HINT


def test_a_missing_table_no_correction_creates_gets_no_such_hint() -> None:
    """The bots' table from T30's first run: the plan does not make it, so no button is named.

    Catches a hint keyed on "a table is missing" rather than on "this plan's
    offered step creates exactly that table", and one that matches the table
    the statement copies FROM.
    """
    for missing in ("tw_char.ai_playerbot_equip_cache", "tw_char.character_inventory"):
        message = _refusal(TORTOISE, f"[1146] Table '{missing}' doesn't exist\n")
        assert native.CORRECTIONS_BUTTON_LABEL not in message, missing


# -- the Server tab ------------------------------------------------------------


def test_the_banner_and_its_press_do_not_wait_for_the_world_to_be_down(
    qapp: object,
    ps: object,  # noqa: F811 - the fixture imported above, as pytest resolves it
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the world listed beside the database -- up, or looping -- the banner is up and presses.

    The tab asks nothing about the world: the press stops it, after the dialog.
    This is the whole route a crash-looping server has, since Stop would take
    the database, and so the banner, down with it.

    Catches a world-state gate added to the tab, which would make the
    crash-looping server's one remedy unreachable again.
    """
    from tests.conftest import pump_until

    route = _Route("stale")
    view = _view(ps, tmp_path, route)
    ps.names = "ac-database\nac-worldserver\nac-authserver\n"  # type: ignore[attr-defined]
    view.refresh_status()  # type: ignore[attr-defined]
    assert not view.corrections_banner.isHidden()  # type: ignore[attr-defined]
    assert "stops the world server first" in view.corrections_banner_label.text()  # type: ignore[attr-defined]
    _answer(monkeypatch, yes=True)
    view.corrections_banner_button.click()  # type: ignore[attr-defined]
    pump_until(
        lambda: "applied and recorded" in view.rebuild_log.text(),  # type: ignore[attr-defined]
        "the corrections press's output reached the panel",
    )
    assert len(route.pressed) == 1
