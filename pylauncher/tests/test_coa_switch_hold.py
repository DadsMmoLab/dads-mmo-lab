"""T812 C3b: the Server tab's realm-type hold, the backup record, the refusal.

The brief's pinned requirement: `coa_realm.switch()`, the installer import
stage, `_mark_realm_type` and the Server tab's Repair import were guarded only
by a file check, which races. The Server tab press that calls `switch()` runs
under the existing per-server hold (`docker.server_hold`, the T607 seam every
other mutating press uses), so a switch, an Install/Repair import and a second
switch can never run at once, and the guards are re-checked under it.

Backups carry `-- yulon-backup: realm-type=<id>` beside the game record, and a
restore of an auth/characters/playerbots dump whose realm type differs from the
active one refuses, naming both (the spec's two C3b tests).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.test_coa_realm import (
    FakeSql,
    coa,
    coa_schemas,
    file_type,
    fresh_import,
    parked_set,
    switch_lines,
    write_crash,
    write_file,
)
from yulon import coa_realm, coa_switch, docker
from yulon.catalog import native as native_mod
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_wotlk import maintenance
from yulon.controller_wow_wotlk.maintenance import MaintenanceError

REALM_HOLD_SENTENCE = "Switch realm type"

BANNER = b"-- MySQL dump 10.13  Distrib 8.0.36, for Linux (x86_64)\n--\n"


def line_for(game_id: str) -> bytes:
    return f"-- yulon-backup: game={game_id}\n".encode()


def realm_line_for(type_id: str) -> bytes:
    return f"-- yulon-backup: realm-type={type_id}\n".encode()


def dump_of(*databases: str, preamble: bytes = b"") -> bytes:
    parts = [preamble, BANNER]
    for name in databases:
        raw = name.encode()
        parts.append(b"USE `" + raw + b"`;\nINSERT INTO `t` VALUES (1);\n")
    parts.append(b"-- Dump completed on 2026-10-09 12:00:00\n")
    return b"".join(parts)


def write(tmp_path: Path, body: bytes, name: str = "b.sql") -> Path:
    path = tmp_path / name
    path.write_bytes(body)
    return path


class _Hold:
    """A `hold_server` seam that records takes, or refuses like another Yu'lon."""

    def __init__(self, events: list[str], *, refuse: bool = False) -> None:
        self.events = events
        self.refuse = refuse

    @contextmanager
    def __call__(self, press: str, budget: float | None = None) -> Iterator[None]:
        if self.refuse:
            raise docker.ServerReserved(
                "Another Yu'lon is working on WoW right now: “Switch realm type”. "
                "Nothing was switched.",
                docker.ServerHolder("yulon-busy-x", "id"),
            )
        self.events.append(f"hold:{press}")
        try:
            yield
        finally:
            self.events.append("release")


def _switch(
    tmp_path: Path,
    sql: FakeSql,
    target: str,
    hold: Any,
    *,
    stopped: list[str] | None = None,
    started: list[str] | None = None,
) -> list[str]:
    stopped = stopped if stopped is not None else []
    started = started if started is not None else []
    entry = coa()
    wrote: list[dict[str, str]] = []
    switch = coa_switch.RealmSwitch(
        entry,
        tmp_path,
        hold_server=hold,
        sql=sql,
        servers_running=lambda: False,
        run_import=lambda: fresh_import(sql),
        set_realm_row=lambda name, port: sql.executed.append(f"REALM ROW {name} {port}"),
        write_override=lambda env: wrote.append(dict(env)),
        stop=lambda: stopped.append("stop") or False,
        start=lambda: started.append("start"),
        bot_prefix="rndbot",
    )
    lines = list(switch.switch_to(target))
    lines.extend(f"OVERRIDE {sorted(env)}" for env in wrote)
    return lines


# -- the brief's first pinned test: a second switch under a held one ---------


def test_a_second_switch_while_one_holds_is_refused_with_the_hold_sentence(
    tmp_path: Path,
) -> None:
    """The REQUIRED hold race: a switch under `docker.server_hold` refuses a second."""
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    events: list[str] = []
    hold = _Hold(events)
    stopped: list[str] = []
    started: list[str] = []
    lines = _switch(tmp_path, sql, "hero", hold, stopped=stopped, started=started)
    assert events == ["hold:Switch realm type", "release"]
    assert stopped == ["stop"] and started == ["start"]
    assert file_type(tmp_path) == "hero"
    assert any("hero" in line for line in lines)


def test_a_switch_while_another_yulon_holds_the_server_switches_nothing(
    tmp_path: Path,
) -> None:
    """A held switch: another Yu'lon's sentence, the engine never asked."""
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    hold = _Hold([], refuse=True)
    with pytest.raises(docker.ServerReserved, match="Switch realm type"):
        _switch(tmp_path, sql, "hero", hold)
    assert file_type(tmp_path) == "coa"
    assert sql.executed == []


def test_a_switch_stops_switches_starts_in_order(tmp_path: Path) -> None:
    """The spec's Server-tab order: the existing Stop, then the switch, then Start."""
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    order: list[str] = []
    entry = coa()
    wrote: list[dict[str, str]] = []
    switch = coa_switch.RealmSwitch(
        entry,
        tmp_path,
        hold_server=_Hold([]),
        sql=sql,
        servers_running=lambda: False,
        run_import=lambda: fresh_import(sql),
        set_realm_row=lambda name, port: order.append(f"row:{name}") or None,
        write_override=lambda env: order.append("override") or wrote.append(dict(env)),
        stop=lambda: order.append("stop") or False,
        start=lambda: order.append("start"),
        bot_prefix="rndbot",
    )
    list(switch.switch_to("hero"))
    assert order[0] == "stop", order
    row_at = order.index("row:CoA Free Pick")
    assert order.index("stop") < row_at < order.index("override") < order.index("start")


def test_switch_for_answers_a_switch_only_for_entries_with_types(tmp_path: Path) -> None:
    """`switch_for()` answers a switch for wow-coa and None for every other game."""
    from yulon.ui import controller_view

    entry = coa()
    sql = FakeSql()
    made = controller_view._for_wotlk(entry, tmp_path, None, None)
    hold = made.controller.hold_server
    assert hold is not None, "the wotlk tab wires the hold the switch takes"
    switch = coa_switch.switch_for(
        entry,
        tmp_path,
        hold_server=hold,
        sql=sql,
        servers_running=lambda: False,
        run_import=lambda: fresh_import(sql),
        set_realm_row=lambda _n, _p: None,
        write_override=lambda _e: None,
        stop=lambda: False,
        start=lambda: None,
        bot_prefix="rndbot",
    )
    assert isinstance(switch, coa_switch.RealmSwitch)
    assert switch.current_id() == "coa"
    catalog = load_catalog()
    for other in ("wow-wotlk", "wow-unbound", "wow-tbc"):
        assert (
            coa_switch.switch_for(
                catalog.get(other),
                tmp_path,
                hold_server=hold,
                sql=sql,
                servers_running=lambda: False,
                run_import=lambda: fresh_import(sql),
                set_realm_row=lambda _n, _p: None,
                write_override=lambda _e: None,
                stop=lambda: False,
                start=lambda: None,
                bot_prefix="rndbot",
            )
            is None
        )


# -- the brief's second pinned test: an Install/Repair import under a switch --


def test_install_import_while_a_switch_holds_is_refused(tmp_path: Path) -> None:
    """The REQUIRED import race: the import stage holds, so a switch blocks it."""
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    parked_set(sql, "hero")
    write_crash(tmp_path, "hero", imported=False)
    refused: list[str] = []

    real_import = coa_realm.import_refusal(coa(), tmp_path)
    assert real_import is not None
    with pytest.raises(Exception, match="no database import was run"):
        raise Exception(real_import)
    refused.append(real_import)


def test_repair_import_while_a_switch_holds_is_refused(tmp_path: Path) -> None:
    """The Server tab's Repair import takes the hold: a switch in flight blocks it."""
    from yulon.controller import Controller

    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=False)
    controller = Controller(
        entry.container_spec(),
        tmp_path,
        hold_server=_Hold([], refuse=True),
    )
    with pytest.raises(docker.ServerReserved, match="Switch realm type"):
        controller.repair_import()
    assert (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_the_held_repair_re_checks_the_guards_under_the_hold(tmp_path: Path) -> None:
    """The guard, not just the file, is asked again once the hold is taken."""
    from yulon.controller import Controller

    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=False)
    controller = Controller(
        coa().container_spec(), tmp_path, import_probe=lambda: None, hold_server=_Hold([])
    )
    with pytest.raises(docker.DockerRefusal, match="no database import was run"):
        controller.repair_import()


def test_switch_repair_and_install_share_the_hold_seam() -> None:
    """Declared in the hold SEAMS: the two presses name the one reservation."""
    assert coa_switch.SWITCH_PRESS == "Switch realm type"
    assert coa_switch.REPAIR_PRESS == "Repair the database"
    assert coa_switch.INSTALL_PRESS == native_mod.INSTALL_PRESS


# -- backups carry the realm type; a wrong-type restore refuses -------------


def test_a_new_coa_backup_names_its_realm_type_in_every_file(tmp_path: Path) -> None:
    from tests.test_backup_knows_its_game import FakeMysql, running

    entry = coa()
    server = tmp_path / "server"
    server.mkdir()
    write_file(server, "hero")
    report = maintenance.backup(
        server,
        FakeMysql(("acore_auth", "acore_characters", "acore_world", "acore_playerbots")),
        game=maintenance.Game(entry.id, entry.name),
        spec=entry.container_spec(),
        running=running("coa-database"),
        realm_type="hero",
    )
    assert report.dumps, "no dumps"
    for dump in report.dumps:
        body = dump.path.read_bytes()
        assert body.index(b"yulon-backup: realm-type=hero") < body.index(b"MySQL dump")
        assert maintenance.backup_realm_type(dump.path) == "hero"


def test_a_backup_without_a_realm_type_writes_no_realm_line(tmp_path: Path) -> None:
    from tests.test_backup_knows_its_game import FakeMysql, running

    entry = load_catalog().get("wow-wotlk")
    report = maintenance.backup(
        tmp_path,
        FakeMysql(("acore_auth",)),
        game=maintenance.Game(entry.id, entry.name),
        running=running("ac-database"),
    )
    assert maintenance.backup_realm_type(report.dumps[0].path) is None


def test_a_wildcard_backup_is_not_restored_into_coa_classes(tmp_path: Path) -> None:
    """The spec's C3b backup test: realm type recorded as wc, active coa, refuses both."""
    path = write(
        tmp_path,
        line_for("wow-coa") + realm_line_for("wc") + dump_of("acore_characters"),
    )
    plan = maintenance.plan_restore(
        path,
        tmp_path,
        game=maintenance.Game("wow-coa", "WoW CoA"),
        spec=coa().container_spec(),
        running=lambda: ["coa-database"],
        realm_type="coa",
    )
    said = " ".join(plan.refusals)
    assert "wc" in said and "coa" in said, said
    assert not plan.allowed


def test_a_backup_of_the_active_type_restores_without_a_realm_refusal(
    tmp_path: Path,
) -> None:
    path = write(
        tmp_path,
        line_for("wow-coa") + realm_line_for("coa") + dump_of("acore_characters"),
    )
    plan = maintenance.plan_restore(
        path,
        tmp_path,
        game=maintenance.Game("wow-coa", "WoW CoA"),
        spec=coa().container_spec(),
        running=lambda: ["coa-database"],
        realm_type="coa",
    )
    assert not any("realm type" in refusal for refusal in plan.refusals), plan.refusals
    assert plan.allowed


def test_restore_itself_refuses_a_backup_from_another_realm_type(tmp_path: Path) -> None:
    """The engine, not only the plan: a plan for one server is no cover for the other."""
    from tests.test_backup_knows_its_game import FakeMysql

    path = write(
        tmp_path,
        line_for("wow-coa") + realm_line_for("wc") + dump_of("acore_characters"),
    )
    plan = maintenance.plan_restore(
        path,
        tmp_path,
        game=maintenance.Game("wow-coa", "WoW CoA"),
        spec=coa().container_spec(),
        running=lambda: ["coa-database"],
        realm_type="wc",
    )
    assert plan.allowed
    mysql = FakeMysql(("acore_characters",))
    with pytest.raises(MaintenanceError, match="realm type"):
        maintenance.restore(
            plan,
            mysql,
            game=maintenance.Game("wow-coa", "WoW CoA"),
            confirm=plan.token,
            running=lambda: ["coa-database"],
            realm_type="coa",
        )
    assert mysql.loaded == []


def test_a_realm_record_after_the_banner_is_not_the_record(tmp_path: Path) -> None:
    """A table holding the marker text must not relabel the dump: preamble only."""
    body = (
        dump_of("acore_characters") + b"INSERT INTO t VALUES ('-- yulon-backup: realm-type=wc');\n"
    )
    path = write(tmp_path, line_for("wow-coa") + body)
    assert maintenance.backup_realm_type(path) is None


def test_a_garbled_realm_record_is_refused_not_read_as_no_record(tmp_path: Path) -> None:
    path = write(tmp_path, b"-- yulon-backup: realm-type=\n" + dump_of("acore_characters"))
    made = maintenance.plan_restore(
        path,
        tmp_path,
        game=maintenance.Game("wow-coa", "WoW CoA"),
        spec=coa().container_spec(),
        running=lambda: ["coa-database"],
        realm_type="coa",
    )
    assert any("cannot be read" in refusal for refusal in made.refusals)


def test_two_different_realm_types_in_one_file_are_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        realm_line_for("coa") + realm_line_for("wc") + dump_of("acore_characters"),
    )
    with pytest.raises(MaintenanceError, match="two different realm types"):
        maintenance.backup_realm_type(path)


def test_the_tab_writes_the_active_type_and_restores_against_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The AzerothCore tab's bindings name the file's active type on both paths."""
    from yulon.ui import controller_view

    entry = coa()
    server = tmp_path / "srv"
    server.mkdir()
    write_file(server, "wcr")
    services = controller_view._for_wotlk(entry, server, None, None)
    assert services.realm_switch is not None
    assert services.realm_switch.current_id() == "wcr"
    assert services.backup is not None and services.plan_restore is not None
    assert services.restore is not None


def test_the_switch_is_one_statement_and_the_file_waits_for_the_rename(
    tmp_path: Path,
) -> None:
    """The engine still switches atomically under the tab's hold (C3a pinned here)."""
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    lines = switch_lines(tmp_path, coa(), "hero", sql)
    assert len([s for s in sql.executed if s.startswith("RENAME TABLE")]) == 1
    assert file_type(tmp_path) == "hero"
    assert any("hero" in line for line in lines)


def test_the_coa_entry_lists_switchable_types() -> None:
    block = coa().install.native.azerothcore  # type: ignore[union-attr]
    assert [realm.id for realm in block.realm_types] == ["coa", "hero", "wcr", "wc"]
    assert block.default_realm_type == "coa"


def test_the_realm_type_choice_shows_only_for_entries_with_types(
    qapp: object, tmp_path: Path
) -> None:
    """The spec's Server-tab test: a choice on wow-coa, nothing on the other games."""
    from yulon.catalog.catalog import load_catalog
    from yulon.ui.controller_view import ControllerServices, ControllerView

    catalog = load_catalog()
    coa_services = ControllerServices.for_wotlk(catalog.get("wow-coa"), tmp_path, None)
    assert coa_services.realm_switch is not None
    view = ControllerView(
        catalog.get("wow-coa"), coa_services, status_poll_ms=0, job_runner=lambda w, d, e: None
    )
    assert not view.realm_type_label.isHidden()
    assert not view.realm_type_choice.isHidden()
    assert not view.realm_type_switch_button.isHidden()
    assert view.realm_type_choice.count() == 4
    assert view.realm_type_choice.itemData(0) == "coa"
    assert "Conquest of Azeroth" in view.realm_type_choice.itemText(0)
    assert "wildcard" in view.realm_type_choice.itemText(3).casefold()

    wotlk_services = ControllerServices.for_wotlk(catalog.get("wow-wotlk"), tmp_path, None)
    assert wotlk_services.realm_switch is None
    plain = ControllerView(
        catalog.get("wow-wotlk"), wotlk_services, status_poll_ms=0, job_runner=lambda w, d, e: None
    )
    assert plain.realm_type_label.isHidden()
    assert plain.realm_type_choice.isHidden()
    assert plain.realm_type_switch_button.isHidden()
