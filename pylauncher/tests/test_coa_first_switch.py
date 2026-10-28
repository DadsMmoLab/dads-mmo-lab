"""T812 C8: a FIRST switch to a new realm type, as the live run on yulon-ubuntu2 found it.

The engine had passed three review rounds on fakes that accepted a stopped
database, a missing client folder and an import that refused on the switch's
own crash note. The live run (`.notes/gates/t812-coa-yulon-ubuntu2-2026-10-28`)
could not complete a first switch to any type:

* D5 the tab's Stop also stops the database, and the engine's first read fails;
* D6 the import ran the installer, which refused for a client folder AFTER the
  259-table rename;
* D7 the import refused on the crash note the switch had just written;
* D8 the installer ran on into `up` and its marking stage labelled the fresh
  set with the default type, after which the switch could not be undone.

The fakes here model each of those: `FakeSql.stop_database()` is the tab's
Stop (statements on a stopped database fail as `docker exec` does), and the
first switch's import is the product's own `coa_switch.switch_import()` over
fake docker seams, so its crash-note check, its pre-checks and its "marks
nothing" are the real ones.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.test_coa_realm import (
    PLAIN,
    FakeSql,
    coa,
    coa_schemas,
    file_type,
    fresh_import,
    switch_lines,
    usernames,
    write_crash,
    write_file,
)
from yulon import coa_realm, coa_switch, docker

SERVICE = "coa-db-import"


class _Hold:
    @contextmanager
    def __call__(self, press: str, budget: float | None = None) -> Iterator[None]:
        yield


class _Docker:
    """The db-import one-shot and its probe over a `FakeSql`, as `ImportSeams`."""

    def __init__(self, sql: FakeSql, *, returncode: int = 0) -> None:
        self.sql = sql
        self.returncode = returncode
        self.ran: list[str] = []

    def probe(self) -> docker.ImportState:
        self.sql._need_up()
        filled = all(self.sql.tables.get(schema) for schema in PLAIN)
        return docker.ImportState("imported" if filled else "partial", "", complete=filled)

    def one_shot(self, service: str, folder: Path, sink: docker.OutputSink) -> docker.AttachedRun:
        self.ran.append(service)
        self.sql._need_up()
        sink("Updating Auth database...")
        if self.returncode == 0:
            # The base tables only: the db-import writes no realm type marker.
            self.sql.tables["acore_auth"].extend(["account", "account_access", "realmlist"])
            self.sql.tables["acore_characters"].append("characters")
            self.sql.tables["acore_playerbots"].append("playerbots_account_type")
            for schema in PLAIN:
                self.sql.tables[schema].sort()
        return docker.AttachedRun(self.returncode, ("ERROR 1146",) if self.returncode else ())

    def seams(self, *, service: str | None = SERVICE) -> coa_switch.ImportSeams:
        return coa_switch.ImportSeams(
            service=service,
            probe=self.probe,
            end_one_shot=lambda _service, _folder: None,
            one_shot=self.one_shot,
            verify=docker.verify_import,
        )


def _switch(
    tmp_path: Path,
    sql: FakeSql,
    fake: _Docker,
    *,
    events: list[str] | None = None,
    start_db: Any = None,
    seams: coa_switch.ImportSeams | None = None,
) -> coa_switch.RealmSwitch:
    """The Server tab's switch with the product's own import over `fake`."""
    events = events if events is not None else []
    seams = seams or fake.seams()
    entry = coa()

    def stop() -> bool:
        events.append("stop")
        return sql.stop_database()

    def start() -> None:
        events.append("start")
        sql.start_database()

    return coa_switch.RealmSwitch(
        entry,
        tmp_path,
        hold_server=_Hold(),
        sql=sql,
        servers_running=lambda: False,
        run_import=lambda context: coa_switch.switch_import(entry, tmp_path, context, seams),
        check_import=lambda: coa_switch.check_switch_import(tmp_path, seams),
        set_realm_row=lambda name, port: events.append(f"row:{name}"),
        write_override=lambda env: events.append("override"),
        stop=stop,
        start_db=start_db if start_db is not None else sql.start_database,
        start=start,
        bot_prefix="rndbot",
    )


def _tables(sql: FakeSql) -> dict[str, list[str]]:
    return {schema: sorted(tables) for schema, tables in sql.tables.items() if tables}


# -- D5: the Stop stops the database; the switch starts it alone ----------------


def test_the_fake_stop_stops_the_database_as_the_live_run_showed(tmp_path: Path) -> None:
    """The ground: with nothing starting the database again, the switch's first read fails."""
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    events: list[str] = []
    switch = _switch(tmp_path, sql, _Docker(sql), events=events, start_db=lambda: None)
    with pytest.raises(coa_realm.RealmSwitchError, match="not running"):
        list(switch.switch_to("hero"))
    assert sql.executed == [] and file_type(tmp_path) == "coa"


def test_a_first_switch_starts_the_database_alone_after_the_stop(tmp_path: Path) -> None:
    """D5. Mutation: drop `self._start_db()` from `switch_to()`: the first read fails."""
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    events: list[str] = []
    fake = _Docker(sql)
    lines = list(_switch(tmp_path, sql, fake, events=events).switch_to("hero"))
    assert events[0] == "stop" and events[-1] == "start", events
    assert file_type(tmp_path) == "hero"
    assert sql.marker == "hero"
    assert fake.ran == [SERVICE]
    assert any("now runs the hero realm type" in line for line in lines)


def test_the_tab_wires_the_database_start_after_its_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tab's switch starts `coa-database` alone through `docker.start_database`."""
    from yulon.ui import controller_view

    started: list[str] = []
    monkeypatch.setattr(
        docker, "start_database", lambda spec, *_a, **_k: started.append(spec.db) or True
    )
    switch = controller_view._realm_switch(
        coa(), tmp_path, controller_view.DockerSql("coa-database", "pw"), wsl_distro=None
    )
    assert switch is not None
    switch._start_db()
    assert started == [coa().container_spec().db]


# -- D6: every refusal knowable before the rename is asked before it --------------


def test_an_unfinished_earlier_import_refuses_before_the_stop_and_the_rename(
    tmp_path: Path,
) -> None:
    """D6. Mutation: drop `check_import()` from `pre_check()` AND `switch()`: the rename runs."""
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    docker.mark_import_under_way(tmp_path, SERVICE)
    events: list[str] = []
    fake = _Docker(sql)
    with pytest.raises(coa_realm.RealmSwitchError, match="did not finish"):
        list(_switch(tmp_path, sql, fake, events=events).switch_to("hero"))
    assert events == [], "refused before the Stop"
    assert not any(s.startswith("RENAME TABLE") for s in sql.executed)
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()
    assert fake.ran == []


def test_the_engine_asks_the_import_refusals_before_its_crash_note_and_rename(
    tmp_path: Path,
) -> None:
    """D6 in the engine alone. Mutation: drop the `check_import()` call in `switch()`."""
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")

    def refuse() -> None:
        raise coa_realm.RealmSwitchError("no import service; nothing was switched")

    with pytest.raises(coa_realm.RealmSwitchError, match="no import service"):
        list(
            coa_realm.switch(
                coa(),
                tmp_path,
                "hero",
                sql=sql,
                servers_running=lambda: False,
                run_import=lambda context: fresh_import(sql, context),
                set_realm_row=lambda name, port: None,
                write_override=lambda env: None,
                bot_prefix="rndbot",
                check_import=refuse,
            )
        )
    assert not any(s.startswith("RENAME TABLE") for s in sql.executed)
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_a_server_naming_no_import_service_refuses_before_the_stop(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    events: list[str] = []
    fake = _Docker(sql)
    switch = _switch(tmp_path, sql, fake, events=events, seams=fake.seams(service=None))
    with pytest.raises(coa_realm.RealmSwitchError, match="names no database import service"):
        list(switch.switch_to("hero"))
    assert events == [] and sql.executed == []


def test_a_second_switch_to_a_parked_type_never_asks_the_import(tmp_path: Path) -> None:
    """A type with its own park imports nothing, so its import's refusals do not apply."""
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    fake = _Docker(sql)
    list(_switch(tmp_path, sql, fake).switch_to("hero"))
    docker.mark_import_under_way(tmp_path, SERVICE)
    list(_switch(tmp_path, sql, fake).switch_to("coa"))
    assert file_type(tmp_path) == "coa"


def test_the_tab_runs_no_installer_and_needs_no_client_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D6/D8: the tab's first-switch import is the one-shot alone, never `engine.run()`."""
    from yulon import install_wiring
    from yulon.ui import controller_view

    def no_installer(*_a: object, **_k: object) -> object:
        raise AssertionError("the switch ran the installer")

    monkeypatch.setattr(install_wiring, "installer_for_app", no_installer)
    sql = FakeSql()
    for schema in PLAIN:  # right after the switch's rename: the plain schemas are empty
        sql.tables[schema] = []
    write_file(tmp_path, "coa")
    ran: list[str] = []

    def one_shot(service: str, folder: Path, **kwargs: object) -> docker.AttachedRun:
        ran.append(service)
        return _Docker(sql).one_shot(service, folder, kwargs["sink"])  # type: ignore[arg-type]

    monkeypatch.setattr(docker, "run_one_shot", one_shot)
    monkeypatch.setattr(docker, "end_one_shot", lambda *_a, **_k: None)
    monkeypatch.setattr(docker, "verify_import", lambda *_a, **_k: docker.ImportState("imported"))
    seams = controller_view._realm_import_seams(coa(), tmp_path, wsl_distro=None)
    assert seams.service == coa().container_spec().import_service
    assert seams.probe is not None
    write_crash(tmp_path, "hero", imported=True)
    lines = list(
        coa_switch.switch_import(
            coa(), tmp_path, coa_realm.SwitchImport(server_dir=tmp_path, target="hero"), seams
        )
    )
    assert ran == [seams.service]
    assert sql.marker is None, "the import marks nothing: the switch marks last"
    assert lines[-1] == "The hero databases now read as imported."


# -- D7: only the switch's own import passes its crash note -----------------------


def test_the_switchs_own_import_passes_its_crash_note(tmp_path: Path) -> None:
    write_crash(tmp_path, "hero", imported=True)
    context = coa_realm.SwitchImport(server_dir=tmp_path, target="hero")
    assert coa_realm.import_refusal(coa(), tmp_path, switch=context) is None


@pytest.mark.parametrize(
    ("target", "imported", "elsewhere"),
    [("wc", True, False), ("hero", False, False), ("hero", True, True)],
)
def test_any_other_import_context_still_refuses(
    tmp_path: Path, target: str, imported: bool, elsewhere: bool
) -> None:
    """Another target, a note whose import has not begun, or another folder: refused."""
    write_crash(tmp_path, "hero", imported=imported)
    folder = tmp_path / "other" if elsewhere else tmp_path
    context = coa_realm.SwitchImport(server_dir=folder, target=target)
    assert coa_realm.import_refusal(coa(), tmp_path, switch=context) == coa_realm.IMPORT_REFUSAL


def test_install_and_repair_still_refuse_on_the_switchs_own_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bypass is the switch's alone. Mutation: let `import_refusal()` pass a note with
    `imported` true for any caller: the installer's import stage runs."""
    from tests.test_coa_realm import _ctx, _seamed_engine
    from yulon.catalog.families.azerothcore import AzerothCoreInstaller
    from yulon.catalog.installer import InstallerError

    write_crash(tmp_path, "hero", imported=True)
    assert coa_realm.import_refusal(coa(), tmp_path) == coa_realm.IMPORT_REFUSAL
    ran: list[int] = []
    monkeypatch.setattr(
        AzerothCoreInstaller,
        "stage_import",
        lambda self, ctx, gate, service: iter(ran.append(1) or ["imported"]),
    )
    sql = FakeSql()
    with pytest.raises(InstallerError, match="no database import was run"):
        list(_seamed_engine(sql)._import(_ctx(tmp_path)))
    assert ran == []


def test_a_first_switch_completes_through_the_real_import(tmp_path: Path) -> None:
    """D7. Mutation: call `run_import` before the second `_write_crash` (or make
    `switch_import` ask `import_refusal()` with no context): the import refuses."""
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    list(_switch(tmp_path, sql, _Docker(sql)).switch_to("hero"))
    assert file_type(tmp_path) == "hero"
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()
    assert usernames(sql, "acore_auth") == ["Player1", "Player2"]


def test_a_failed_one_shot_leaves_the_note_and_the_undo_puts_everything_back(
    tmp_path: Path,
) -> None:
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    before = copy.deepcopy(_tables(sql))
    with pytest.raises(coa_realm.RealmSwitchError, match="The server is stopped"):
        list(_switch(tmp_path, sql, _Docker(sql, returncode=1)).switch_to("hero"))
    assert (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()
    list(_switch(tmp_path, sql, _Docker(sql)).switch_to("coa"))
    assert _tables(sql) == before
    assert sql.marker == "coa"


# -- D8: the switch can be undone after a completed first switch ----------------


def test_coa_to_hero_to_coa_keeps_every_table(tmp_path: Path) -> None:
    """D8. Mutation: make `_Docker.one_shot` mark the fresh set coa, as the installer's
    `_mark_realm_type` did live: the switch refuses and the way back is lost. The
    product's import cannot mark: `switch_import()` holds no SQL seam at all."""
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    coa_set = copy.deepcopy(_tables(sql))
    coa_accounts = copy.deepcopy(sql.rows)
    list(_switch(tmp_path, sql, _Docker(sql)).switch_to("hero"))
    hero_set = {schema: tables for schema, tables in _tables(sql).items() if schema in PLAIN}
    list(_switch(tmp_path, sql, _Docker(sql)).switch_to("coa"))
    assert file_type(tmp_path) == "coa"
    assert sql.marker == "coa"
    plain = {schema: tables for schema, tables in _tables(sql).items() if schema in PLAIN}
    assert plain == coa_set
    assert {key: rows for key, rows in sql.rows.items() if key[0] in PLAIN} == {
        key: rows for key, rows in coa_accounts.items() if key[0] in PLAIN
    }
    parked = {
        schema.removesuffix("_hero"): tables
        for schema, tables in _tables(sql).items()
        if schema.endswith("_hero")
    }
    assert parked == hero_set
    assert sql.marker_rows["acore_characters_hero"] == "hero"
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_an_import_that_marks_the_fresh_set_breaks_the_switch(tmp_path: Path) -> None:
    """The ground for D8: the live run's state, reproduced on the fake."""

    def marking(context: coa_realm.SwitchImport) -> Iterator[str]:
        yield from fresh_import(sql, context)
        coa_realm.ensure_marker(sql, coa(), "coa")

    sql = FakeSql()
    coa_schemas(sql)
    with pytest.raises(coa_realm.RealmSwitchError):
        switch_lines(tmp_path, coa(), "hero", sql, run_import=marking)
    assert (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()
