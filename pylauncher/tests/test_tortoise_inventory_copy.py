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
    _COLUMNS = re.compile(
        r"SELECT column_name, column_type FROM information_schema\.columns WHERE "
        r"table_schema='(\w+)' AND table_name='(\w+)' ORDER BY ordinal_position"
    )

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

    def query(
        self,
        container: str,
        client: str,
        password: str,
        schema: str | None,
        statement: str,
        *,
        wsl_distro: str | None = None,
    ) -> str:
        """`information_schema.columns` as sqlite has it: `PRAGMA table_info`, in `cid` order.

        The second translation this stand-in makes, and the same kind as the
        first: the question `sqlplan.check_same_columns()` asks, answered off
        the tables sqlite really holds (name and declared type, in order).
        """
        asked = self._COLUMNS.fullmatch(statement)
        if asked is None:
            return super().query(
                container, client, password, schema, statement, wsl_distro=wsl_distro
            )
        rows = self.rows(asked.group(1), f"PRAGMA table_info({asked.group(2)})")
        return "".join(f"{row[1]}\t{row[2]}\n" for row in rows)


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

    def stop(self, containers: list[str], **kwargs: object) -> None:
        self.events.append(f"stop:{','.join(containers)}")
        self.kwargs = kwargs
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

    def refuses(containers: list[str], **_kwargs: object) -> None:
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


# -- a copy table that is already there, and not the right one (Codex, T159) ---


@pytest.mark.parametrize(
    "made_by_hand",
    [
        "CREATE TABLE character_inventory_copy (guid INT, item INT);\n",
        "CREATE TABLE character_inventory_copy (item INT PRIMARY KEY, guid INT, bag INT, slot INT, "
        "item_template INT);\n",
    ],
    ids=["other-columns", "same-columns-other-order"],
)
def test_a_copy_table_not_built_like_the_original_is_not_recorded_and_the_press_says_what_to_do(
    tmp_path: Path, made_by_hand: str
) -> None:
    """`IF NOT EXISTS` lands over it; the check after it does not call the step landed.

    Each fixture violates one rule: the first has other columns, the second the
    right columns in another order -- which `INSERT ... SELECT *` fills wrongly
    or refuses just the same.

    Catches the step recorded on its statement alone, and a check that compares
    column sets rather than the ordered list.
    """
    db = _TortoiseDb(tmp_path)
    marked_by_a_release(db)
    db.exec_stdin_raw("tw_char", made_by_hand)
    engine = an_engine(db)
    options = folder(tmp_path)
    with pytest.raises(InstallerError) as caught:
        list(engine.apply_corrections(engine.correction_check(options), options))
    message = str(caught.value)
    assert "tw_char.character_inventory_copy is already there but is not built like" in message
    assert "RENAME TABLE tw_char.character_inventory_copy TO" in message
    assert "Nothing was recorded" in message
    assert engine.correction_check(options).offered == (PHASE,), "the step was recorded"


def test_once_the_wrong_copy_is_renamed_the_next_press_makes_the_right_one(tmp_path: Path) -> None:
    """The remedy the refusal names, followed: the step lands and is recorded."""
    db = _TortoiseDb(tmp_path)
    marked_by_a_release(db)
    db.exec_stdin_raw("tw_char", "CREATE TABLE character_inventory_copy (guid INT, item INT);\n")
    engine = an_engine(db)
    options = folder(tmp_path)
    with pytest.raises(InstallerError):
        list(engine.apply_corrections(engine.correction_check(options), options))
    db.exec_stdin_raw(
        "tw_char", "ALTER TABLE character_inventory_copy RENAME TO character_inventory_copy_old;\n"
    )
    list(engine.apply_corrections(engine.correction_check(options), options))
    assert engine.correction_check(options).state == "current"
    db.exec_stdin_raw("tw_char", BACKUP)


# -- the stop is T158's: a loading world is waited for, a looping one is not ----


@pytest.fixture
def fast_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """T158's `_fast`: no real pause between the load wait's looks."""
    monkeypatch.setattr(docker, "_cwd_is_missing", lambda cwd: False)
    monkeypatch.setattr(docker, "_LOAD_POLL_SECONDS", 0.001)
    monkeypatch.setattr(docker.time, "sleep", lambda _seconds: None)


def _through_docker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, frames: list[object], fake_class: type = None  # type: ignore[assignment]
) -> tuple[object, _TortoiseDb, CmangosInstaller]:
    """The shipped engine whose world stop is the REAL `docker.stop_containers`, over T158's fake.

    Only `runner.run` is faked for the stop, as `test_stop_waits_for_the_world.py`
    fakes it: a docker whose world state and signal mask the frames script,
    recording every look and the stop in order.
    """
    from tests.test_stop_waits_for_the_world import _Docker
    from yulon import runner

    fake = (fake_class or _Docker)(TORTOISE.container_spec(), frames)
    monkeypatch.setattr(runner, "run", fake)
    db = _TortoiseDb(tmp_path)
    marked_by_a_release(db)
    engine = an_engine(
        db,
        world_running=lambda container: container in fake.running,
        stop_world=docker.stop_containers,
    )
    return fake, db, engine


def test_a_healthy_world_still_loading_is_waited_for_then_stopped_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fast_waits: None
) -> None:
    """T158's wait, through this press: no stop while mangosd cannot hear SIGTERM.

    Catches the press stopping by name without `known=` (the world would be
    signalled mid-load and SIGKILLed when the grace ends), and the wait's lines
    not reaching the panel.
    """
    from tests.test_stop_waits_for_the_world import LOADED, LOADED_MASK, LOADING, LOADING_MASK

    fake, db, engine = _through_docker(
        monkeypatch,
        tmp_path,
        [("running", LOADING, LOADING_MASK), ("running", LOADED, LOADED_MASK)],
    )
    options = folder(tmp_path)
    said = list(engine.apply_corrections(engine.correction_check(options), options))
    assert fake.events == ["look 1", "look 2", "stop"], fake.events  # type: ignore[attr-defined]
    assert docker.WORLD_STILL_LOADING in said and docker.WORLD_FINISHED_LOADING in said, said
    assert native.CORRECTIONS_WAIT_HINT in said
    assert "character_inventory_copy" in db.tables("tw_char")


def test_a_crash_looping_world_is_stopped_without_waiting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fast_waits: None
) -> None:
    """In restart backoff there is no process to wait for: one look, then the stop.

    Catches a wait that held a crash-looping world as if it were loading.
    """
    fake, db, engine = _through_docker(monkeypatch, tmp_path, [("restarting", "", None)])
    options = folder(tmp_path)
    said = list(engine.apply_corrections(engine.correction_check(options), options))
    assert fake.events == ["look 1", "stop"], fake.events  # type: ignore[attr-defined]
    assert docker.WORLD_STILL_LOADING not in said
    assert "character_inventory_copy" in db.tables("tw_char")


def test_stop_pressed_while_the_world_loads_sends_nothing_and_applies_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fast_waits: None
) -> None:
    """The press's Cancel is the wait's `abandon`: nothing touched, the world left running.

    Catches the Cancel not wired into the stop's control (the wait would run on
    until the load ended and then stop a world the person had asked to keep).
    """
    from tests.test_stop_waits_for_the_world import LOADING, LOADING_MASK

    fake, db, engine = _through_docker(monkeypatch, tmp_path, [("running", LOADING, LOADING_MASK)])
    cancel = docker.CancelWithForce()
    fake.on_look = cancel.set  # type: ignore[attr-defined]
    options = folder(tmp_path)
    sent_before = len(db.scripts)
    with pytest.raises(InstallerError, match="still loading") as caught:
        list(engine.apply_corrections(engine.correction_check(options), options, cancel=cancel))
    assert "nothing was applied" in str(caught.value)
    assert "stop" not in fake.events  # type: ignore[attr-defined]
    assert db.scripts[sent_before:] == []


def test_stop_now_anyway_stops_the_loading_world_and_goes_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fast_waits: None
) -> None:
    """The panel's "Stop now anyway" rides on the Cancel (`CancelWithForce.anyway`)."""
    from tests.test_stop_waits_for_the_world import LOADING, LOADING_MASK

    fake, db, engine = _through_docker(monkeypatch, tmp_path, [("running", LOADING, LOADING_MASK)])
    cancel = docker.CancelWithForce()
    fake.on_look = cancel.anyway.set  # type: ignore[attr-defined]
    options = folder(tmp_path)
    said = list(engine.apply_corrections(engine.correction_check(options), options, cancel=cancel))
    assert docker.WORLD_STOPPED_ANYWAY in said
    assert fake.events[-1] == "stop"  # type: ignore[attr-defined]
    assert "character_inventory_copy" in db.tables("tw_char")


def test_a_stop_that_does_not_return_is_given_up_and_nothing_is_applied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fast_waits: None
) -> None:
    """Codex [high]: the by-name stop has a process deadline past the grace, and past it the
    outcome is unknown -- said so, and nothing written.

    Catches the stop run with no deadline (the press would hang with the daemon), one shorter
    than the grace (a slow save would be cut off), and a timeout read as a stop that worked.
    """
    from tests.test_stop_waits_for_the_world import _completed, _Docker

    deadlines: list[float | None] = []

    class _Wedged(_Docker):  # type: ignore[misc]
        def __call__(self, cmd: list[str], cwd: Path | None = None, timeout: float | None = None):  # type: ignore[no-untyped-def]
            if cmd[1:2] == ["stop"]:
                deadlines.append(timeout)
                return subprocess.CompletedProcess(cmd, 124, "", f"timed out after {timeout}s")
            return super().__call__(cmd, cwd, timeout)

    del _completed
    fake, db, engine = _through_docker(
        monkeypatch, tmp_path, [("restarting", "", None)], fake_class=_Wedged
    )
    options = folder(tmp_path)
    sent_before = len(db.scripts)
    with pytest.raises(InstallerError, match="may still be running") as caught:
        list(engine.apply_corrections(engine.correction_check(options), options))
    assert "not known" in str(caught.value)
    assert deadlines and deadlines[0] is not None
    assert deadlines[0] > docker.STOP_GRACE_SECONDS
    assert db.scripts[sent_before:] == []


def test_a_crash_loop_caught_mid_start_is_stopped_at_its_first_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fast_waits: None
) -> None:
    """The live shape (2026-09-28): `running` and deaf for ~20 s a cycle, `restarting` ~1 s.

    Every look finds it running its start-up without a SIGTERM handler; the
    second finds a new run. The press stops it there instead of waiting on the
    new run as T158's other stops do, because this world never finishes loading.

    Catches the corrections stop left on T158's default (it would wait on run
    after run until a look happened to land in the one-second backoff).
    """
    from tests.test_stop_waits_for_the_world import (
        LOADING,
        LOADING_MASK,
        RESTARTED,
        STARTED,
        _Docker,
    )

    class _Looping(_Docker):  # type: ignore[misc]
        def __init__(self, spec: docker.ContainerSpec, frames: list[object]) -> None:
            super().__init__(spec, frames, started=[STARTED, RESTARTED, "2026-09-27T18:37:00Z"])

    fake, db, engine = _through_docker(
        monkeypatch, tmp_path, [("running", LOADING, LOADING_MASK)], fake_class=_Looping
    )
    options = folder(tmp_path)
    said = list(engine.apply_corrections(engine.correction_check(options), options))
    assert fake.events == ["look 1", "look 2", "stop"], fake.events  # type: ignore[attr-defined]
    assert docker.WORLD_RESTARTED_STOPPING in said
    assert "character_inventory_copy" in db.tables("tw_char")


def test_the_other_stops_still_wait_on_a_restarted_world(
    monkeypatch: pytest.MonkeyPatch, fast_waits: None
) -> None:
    """T158's rule is unchanged where the flag is off: a new run is waited on in its turn."""
    from tests.test_stop_waits_for_the_world import (
        LOADED,
        LOADED_MASK,
        LOADING,
        LOADING_MASK,
        RESTARTED,
        STARTED,
        _Docker,
    )
    from yulon import runner

    fake = _Docker(
        TORTOISE.container_spec(),
        [
            ("running", LOADING, LOADING_MASK),
            ("running", LOADING, LOADING_MASK),
            ("running", LOADED, LOADED_MASK),
        ],
        started=[STARTED, RESTARTED, RESTARTED],
    )
    monkeypatch.setattr(runner, "run", fake)
    docker.stop_containers([WORLD], known=(TORTOISE.container_spec(),))
    assert fake.events == ["look 1", "look 2", "look 3", "stop"], fake.events


def test_a_column_check_is_refused_where_nothing_would_ask_it() -> None:
    """`same_columns` on a phase T11's route re-runs: that route records nothing and asks nothing.

    Catches the validator dropped, which would let the catalog promise a check
    no press makes.
    """
    with pytest.raises(ValueError, match="corrections press only"):
        SqlPhase(
            name="copy",
            into="tw_char",
            statements=(STATEMENT,),
            rerun_on_marked=True,
            same_columns=(("character_inventory_copy", "character_inventory"),),
        )
    with pytest.raises(ValueError, match="needs `into`"):
        SqlPhase(
            name="copy",
            statements=(STATEMENT,),
            same_columns=(("character_inventory_copy", "character_inventory"),),
        )


def test_a_copy_whose_columns_cannot_be_read_is_not_called_built_right(tmp_path: Path) -> None:
    """A question nobody answered is a failure, never a pass (`check_update_levels()`'s rule).

    Catches the unreadable case falling through to "no difference".
    """
    from yulon.catalog.families import sqlplan

    def refuses(*_args: object, **_kwargs: object) -> str:
        raise docker.DockerCommandError("Error response from daemon: container is restarting")

    run = sqlplan.PhaseRun(_phase(), "tw_char", None, STATEMENT, False, "statement 1")
    failed = sqlplan.check_same_columns(
        (run,), container="tortoise-db", client="mariadb", password="pw", sql_query=refuses
    )
    assert len(failed) == 1 and "could not be read" in failed[0]
