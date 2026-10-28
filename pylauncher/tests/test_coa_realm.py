"""T812 C3a: realm types, the engine (no Qt).

The ACTIVE type lives under the plain `acore_auth`/`acore_characters`/
`acore_playerbots` names; inactive types are parked as `<db>_<id>` schemas and
a switch is ONE atomic `RENAME TABLE`, with the servers stopped. A fake `sql`
records the statements: no daemon, no database.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from yulon import coa_realm
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.resources import installers_dir


def coa() -> CatalogEntry:
    return load_catalog().get("wow-coa")


class FakeSql(coa_realm.SqlRunner):
    """The databases as statement-keyed answers, and a record of the writes."""

    def __init__(self) -> None:
        self.tables: dict[str, list[str]] = {}
        self.schemata: dict[str, tuple[str, str]] = {}
        self.executed: list[str] = []
        self.fail_on: str = ""
        self.marker_rows: dict[str, str] = {}
        """The `yulon_realm_type` row per characters schema, traveling with its tables."""

    @property
    def marker(self) -> str | None:
        return self.marker_rows.get("acore_characters")

    @marker.setter
    def marker(self, value: str | None) -> None:
        if value is None:
            self.marker_rows.pop("acore_characters", None)
        else:
            self.marker_rows["acore_characters"] = value

    def _schema_of(self, statement: str, prefix: str) -> str | None:
        for token in statement.replace("`", " ").replace("(", " ").split():
            if token.startswith(prefix):
                return token
        return None

    def execute(self, statement: str) -> None:
        if self.fail_on and self.fail_on in statement:
            raise RuntimeError(f"the database refused: {statement[:60]}")
        self.executed.append(statement)
        upper = statement.strip().upper()
        if upper.startswith("CREATE DATABASE"):
            name = statement.split("`")[1]
            self.tables.setdefault(name, [])
            self.schemata.setdefault(name, ("utf8mb4", "utf8mb4_unicode_ci"))
        elif upper.startswith("RENAME TABLE"):
            for move in statement[len("RENAME TABLE") :].strip().rstrip(";").split(","):
                old, _, new = move.strip().partition(" TO ")
                old_schema = old.strip().split("`.`")[0].strip(" `")
                old_table = old.strip().split("`.`")[1].strip(" `")
                new_schema = new.strip().split("`.`")[0].strip(" `")
                new_table = new.strip().split("`.`")[1].strip(" `")
                assert old_table == new_table
                self.tables.setdefault(old_schema, [])
                self.tables.setdefault(new_schema, [])
                assert old_table in self.tables[old_schema], (
                    old_table,
                    old_schema,
                    self.tables[old_schema],
                )
                self.tables[old_schema].remove(old_table)
                self.tables[new_schema].append(old_table)
                self.tables[new_schema].sort()
                if old_table == "yulon_realm_type" and old_schema in self.marker_rows:
                    self.marker_rows[new_schema] = self.marker_rows.pop(old_schema)
        elif upper.startswith("DROP DATABASE"):
            name = statement.split("`")[1]
            self.tables[name] = []
            self.marker_rows.pop(name, None)
        elif upper.startswith("DROP TABLE"):
            parts = statement.split("`")
            for schema, table in zip(parts[1::4], parts[3::4]):
                if table in self.tables.get(schema, []):
                    self.tables[schema].remove(table)
                if table == "yulon_realm_type":
                    self.marker_rows.pop(schema, None)
        elif "YULON_REALM_TYPE" in statement.upper() and "CREATE TABLE" in upper:
            schema = statement.split("`")[1]
            if "yulon_realm_type" not in self.tables.setdefault(schema, []):
                self.tables[schema].append("yulon_realm_type")
        elif "YULON_REALM_TYPE" in statement.upper() and "INSERT" in upper:
            schema = statement.split("`")[1]
            self.marker_rows[schema] = statement.split("'")[1]

    def query(self, statement: str) -> str:
        if "information_schema.SCHEMATA" in statement:
            schema = statement.split("'")[-2]
            charset, collation = self.schemata.get(schema, ("utf8mb4", "utf8mb4_unicode_ci"))
            return f"{charset}\t{collation}\n"
        if statement.startswith("SELECT id FROM `"):
            schema = statement.split("`")[1]
            return (self.marker_rows.get(schema, "") + "\n") if schema in self.marker_rows else ""
        if "information_schema.TABLES" in statement:
            schema = statement.split("'")[1]
            return "".join(f"{t}\n" for t in sorted(self.tables.get(schema, [])))
        if ".account" in statement and statement.strip().upper().startswith("SELECT"):
            schema = statement.split("`")[1]
            if "account" not in self.tables.get(schema, []):
                return ""
            names = {
                "acore_auth_coa": ["Player1", "rndbot7", "AHBOT", "Player2"],
                "acore_auth_hero": ["Hero1", "rndbot3", "AHBOT"],
            }.get(schema, [])
            upper = statement.upper()
            if "NOT LIKE" in upper:
                prefix = upper.split("NOT LIKE")[1].split("'")[1].rstrip("%")
                names = [n for n in names if not n.upper().startswith(prefix)]
            if "<> 'AHBOT'" in upper:
                names = [n for n in names if n != "AHBOT"]
            return "".join(f"{n}\n" for n in names)


def running_sql(entry: CatalogEntry, sql: FakeSql, schemas: Mapping[str, list[str]]) -> None:
    for schema, tables in schemas.items():
        sql.tables[schema] = sorted(tables)


def coa_schemas(sql: FakeSql) -> None:
    running_sql(
        coa(),
        sql,
        {
            "acore_auth": ["account", "account_access", "realmlist"],
            "acore_characters": ["characters", "yulon_realm_type"],
            "acore_playerbots": ["playerbots_account_type"],
            "acore_auth_coa": [],
            "acore_characters_coa": [],
            "acore_playerbots_coa": [],
        },
    )
    sql.marker_rows["acore_characters"] = "coa"
    for schema in sql.tables:
        sql.schemata[schema] = ("utf8mb4", "utf8mb4_unicode_ci")


def switch_lines(
    tmp_path: Path,
    entry: CatalogEntry,
    target: str,
    sql: FakeSql,
    *,
    file_type: str = "coa",
    run_import=None,  # type: ignore[no-untyped-def]
    bot_prefix: str = "rndbot",
) -> list[str]:
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": file_type}) + "\n", encoding="utf-8"
    )
    rows = {"Conquest of Azeroth": "1", "CoA Free Pick": "1"}.get(
        entry.install.native.azerothcore.realm_types[0].realm_name, "1"
    )
    del rows
    types = {realm.id: realm for realm in entry.install.native.azerothcore.realm_types}  # type: ignore[union-attr]
    wrote: list[Mapping[str, str]] = []

    def _import() -> Iterator[str]:
        sql.tables.setdefault("acore_auth", []).extend(["account", "account_access"])
        sql.tables.setdefault("acore_characters", []).extend(["characters", "yulon_realm_type"])
        sql.tables.setdefault("acore_playerbots", []).append("playerbots_account_type")
        for schema in ("acore_auth", "acore_characters", "acore_playerbots"):
            sql.schemata[schema] = ("utf8mb4", "utf8mb4_unicode_ci")
        yield "the import ran"
        return
        yield  # make this a generator even when the test never iterates past the first line

    def _row(name: str, port: str) -> None:
        sql.executed.append(f"REALM ROW {name} {port}")

    def _override(env: Mapping[str, str]) -> None:
        wrote.append(dict(env))

    lines = list(
        coa_realm.switch(
            entry,
            tmp_path,
            target,
            sql=sql,
            servers_running=lambda: False,
            run_import=run_import or _import,
            set_realm_row=_row,
            write_override=_override,
            bot_prefix=bot_prefix,
        )
    )
    lines.extend(f"OVERRIDE {sorted(env)}" for env in wrote)
    return lines


# -- the render -----------------------------------------------------------------


def test_wildcard_env_has_the_wildcard_keys_and_coa_does_not(tmp_path: Path) -> None:
    entry = coa()
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "wc"}) + "\n", encoding="utf-8"
    )
    plan = composegen.render(
        entry, tmp_path, templates_root=installers_dir(), platform_id=lambda: "linux"
    )
    override = yaml.safe_load(plan.override)
    world = next(
        service
        for name, service in override["services"].items()
        if name.endswith("worldserver")
    )
    assert world["environment"]["AC_CO_ACHALLENGES_GAME_MODES_REALM"] == "WildCard"
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )
    plan = composegen.render(
        entry, tmp_path, templates_root=installers_dir(), platform_id=lambda: "linux"
    )
    world = next(
        service
        for name, service in yaml.safe_load(plan.override)["services"].items()
        if name.endswith("worldserver")
    )
    assert "AC_CO_ACHALLENGES_GAME_MODES_REALM" not in world["environment"]
    assert world["environment"]["AC_CO_A_CLASS_MODEL"] == "coa"


def test_the_bot_count_still_wins_over_the_realm_type(tmp_path: Path) -> None:
    entry = coa()
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )
    plan = composegen.render(
        entry,
        tmp_path,
        templates_root=installers_dir(),
        platform_id=lambda: "linux",
        world_env={"AC_AI_PLAYERBOT_MIN_RANDOM_BOTS": "3"},
    )
    world = next(
        service
        for name, service in yaml.safe_load(plan.override)["services"].items()
        if name.endswith("worldserver")
    )
    assert world["environment"]["AC_AI_PLAYERBOT_MIN_RANDOM_BOTS"] == "3"
    assert world["environment"]["AC_CO_A_CLASS_MODEL"] == "coa"


def test_a_missing_file_reads_as_the_default(tmp_path: Path) -> None:
    assert coa_realm.active(coa(), tmp_path).id == "coa"


def test_an_unreadable_file_reads_as_the_default(tmp_path: Path) -> None:
    (tmp_path / coa_realm.ACTIVE_FILE).write_text("{no json", encoding="utf-8")
    assert coa_realm.active(coa(), tmp_path).id == "coa"


def test_an_unknown_file_id_reads_as_the_default(tmp_path: Path) -> None:
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "nope"}) + "\n", encoding="utf-8"
    )
    assert coa_realm.active(coa(), tmp_path).id == "coa"


# -- the switch -----------------------------------------------------------------


def test_the_switch_renames_both_ways_in_one_statement(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    running_sql(
        entry,
        sql,
        {
            # A second switch: the plain set holds hero, and coa is parked full.
            "acore_auth": ["account", "account_access", "realmlist"],
            "acore_characters": ["characters", "yulon_realm_type"],
            "acore_playerbots": ["playerbots_account_type"],
            "acore_auth_coa": ["account", "account_access", "realmlist"],
            "acore_characters_coa": ["characters", "yulon_realm_type"],
            "acore_playerbots_coa": ["playerbots_account_type"],
            "acore_auth_hero": [],
            "acore_characters_hero": [],
            "acore_playerbots_hero": [],
        },
    )
    sql.marker_rows["acore_characters"] = "hero"
    sql.marker_rows["acore_characters_coa"] = "coa"
    for schema in sql.tables:
        sql.schemata[schema] = ("utf8mb4", "utf8mb4_unicode_ci")
    lines = switch_lines(tmp_path, entry, "coa", sql, file_type="hero")
    renames = [s for s in sql.executed if s.startswith("RENAME TABLE")]
    assert len(renames) == 1, sql.executed
    assert renames[0].count(" TO ") == 12, renames[0]
    assert "`acore_auth`.`account` TO `acore_auth_hero`.`account`" in renames[0]
    assert "`acore_auth_coa`.`realmlist` TO `acore_auth`.`realmlist`" in renames[0]
    creates = [s for s in sql.executed if s.startswith("CREATE DATABASE")]
    assert len(creates) == 3
    assert (tmp_path / coa_realm.ACTIVE_FILE).read_text(
        encoding="utf-8"
    ) == json.dumps({"version": 1, "active": "coa"}, indent=2) + "\n"
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()
    assert any("coa" in line for line in lines)


def test_switching_back_restores_every_table(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    running_sql(
        entry,
        sql,
        {
            "acore_auth": ["account", "account_access", "realmlist"],
            "acore_characters": ["characters", "yulon_realm_type"],
            "acore_playerbots": ["playerbots_account_type"],
            "acore_auth_hero": ["account", "account_access", "realmlist"],
            "acore_characters_hero": ["characters", "yulon_realm_type"],
            "acore_playerbots_hero": ["playerbots_account_type"],
        },
    )
    sql.marker_rows["acore_characters"] = "coa"
    sql.marker_rows["acore_characters_hero"] = "hero"
    sql.schemata.update(
        {name: ("utf8mb4", "utf8mb4_unicode_ci") for name in sql.tables}
    )
    switch_lines(tmp_path, entry, "hero", sql)
    before = {schema: sorted(tables) for schema, tables in sql.tables.items()}
    switch_lines(tmp_path, entry, "coa", sql, file_type="hero")
    assert sorted(sql.tables["acore_auth"]) == sorted(before["acore_auth_coa"]), sql.tables
    assert sorted(sql.tables["acore_characters"]) == sorted(before["acore_characters_coa"])
    assert sorted(sql.tables["acore_playerbots"]) == sorted(before["acore_playerbots_coa"])


def test_a_running_server_is_never_switched(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(coa_realm.RealmSwitchError, match="Stop the server"):
        list(
            coa_realm.switch(
                entry,
                tmp_path,
                "hero",
                sql=sql,
                servers_running=lambda: True,
                run_import=lambda: iter(()),
                set_realm_row=lambda name, port: None,
                write_override=lambda env: None,
                bot_prefix="rndbot",
            )
        )
    assert sql.executed == []
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_an_unknown_target_is_refused_before_anything_runs(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(coa_realm.RealmSwitchError, match="'nope' is not one of"):
        list(
            coa_realm.switch(
                entry,
                tmp_path,
                "nope",
                sql=sql,
                servers_running=lambda: False,
                run_import=lambda: iter(()),
                set_realm_row=lambda name, port: None,
                write_override=lambda env: None,
                bot_prefix="rndbot",
            )
        )
    assert sql.executed == []


def test_switching_to_the_active_type_switches_nothing(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    lines = switch_lines(tmp_path, entry, "coa", sql)
    assert lines == ["The server is already on the coa realm type; nothing was switched."]
    assert sql.executed == []


def test_a_non_empty_park_refuses_before_any_rename(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.tables["acore_auth_coa"].append("orphan")
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(coa_realm.RealmSwitchError, match="not empty.*orphan"):
        list(
            coa_realm.switch(
                entry,
                tmp_path,
                "hero",
                sql=sql,
                servers_running=lambda: False,
                run_import=lambda: iter(()),
                set_realm_row=lambda name, port: None,
                write_override=lambda env: None,
                bot_prefix="rndbot",
            )
        )
    assert not [s for s in sql.executed if s.startswith("RENAME TABLE")]
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_a_marker_split_for_another_type_refuses_the_start(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.marker_rows["acore_characters"] = "hero"
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )
    refused = coa_realm.refusal(entry, tmp_path, coa_realm.read_marker(sql.query, entry))
    assert refused is not None
    assert "hero" in refused and "coa" in refused
    assert "Server tab" in refused


def test_a_file_split_for_the_marker_type_refuses_the_switch(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.marker_rows["acore_characters"] = "hero"
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(coa_realm.RealmSwitchError, match="Switch realm type to hero"):
        list(
            coa_realm.switch(
                entry,
                tmp_path,
                "wc",
                sql=sql,
                servers_running=lambda: False,
                run_import=lambda: iter(()),
                set_realm_row=lambda name, port: None,
                write_override=lambda env: None,
                bot_prefix="rndbot",
            )
        )
    assert not [s for s in sql.executed if s.startswith("RENAME TABLE")]


def test_a_marker_mismatch_refuses_the_start(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.marker_rows["acore_characters"] = "wc"
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )
    refused = coa_realm.refusal(entry, tmp_path, coa_realm.read_marker(sql.query, entry))
    assert refused == (
        "The databases hold the wc realm type but this server is set to coa. "
        "Switch realm type to wc on the Server tab to put it right."
    )


def test_a_matching_marker_starts(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.marker_rows["acore_characters"] = "coa"
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )
    assert coa_realm.refusal(entry, tmp_path, coa_realm.read_marker(sql.query, entry)) is None


def test_no_marker_starts_and_marks_nothing(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.marker_rows.pop("acore_characters", None)
    sql.tables["acore_characters"] = ["characters"]
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )
    assert coa_realm.refusal(entry, tmp_path, coa_realm.read_marker(sql.query, entry)) is None
    assert sql.executed == []


def test_switching_back_to_the_marker_type_repairs_without_renaming(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.marker_rows["acore_characters"] = "hero"
    sql.tables["acore_auth_hero"] = ["account", "account_access"]
    sql.tables["acore_characters_hero"] = ["characters", "yulon_realm_type"]
    sql.tables["acore_playerbots_hero"] = ["playerbots_account_type"]
    lines = switch_lines(tmp_path, entry, "hero", sql)
    assert not [s for s in sql.executed if s.startswith("RENAME TABLE")]
    assert (tmp_path / coa_realm.ACTIVE_FILE).read_text(
        encoding="utf-8"
    ) == json.dumps({"version": 1, "active": "hero"}, indent=2) + "\n"
    assert any("no database was touched" in line for line in lines)


def test_the_file_is_written_only_after_the_rename_succeeds(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.marker_rows["acore_characters"] = "coa"
    sql.fail_on = "RENAME TABLE"
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(coa_realm.RealmSwitchError, match="could not be renamed"):
        list(
            coa_realm.switch(
                entry,
                tmp_path,
                "hero",
                sql=sql,
                servers_running=lambda: False,
                run_import=lambda: iter(()),
                set_realm_row=lambda name, port: None,
                write_override=lambda env: None,
                bot_prefix="rndbot",
            )
        )
    assert json.loads((tmp_path / coa_realm.ACTIVE_FILE).read_text(encoding="utf-8")) == {
        "version": 1,
        "active": "coa",
    }
    assert (tmp_path / coa_realm.SWITCH_MARKER_FILE).is_file()


def test_a_failed_rename_leaves_no_park_behind(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.marker_rows["acore_characters"] = "coa"
    sql.fail_on = "RENAME TABLE"
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(coa_realm.RealmSwitchError, match="could not be renamed"):
        list(
            coa_realm.switch(
                entry,
                tmp_path,
                "hero",
                sql=sql,
                servers_running=lambda: False,
                run_import=lambda: iter(()),
                set_realm_row=lambda name, port: None,
                write_override=lambda env: None,
                bot_prefix="rndbot",
            )
        )
    assert [s for s in sql.executed if s.startswith("CREATE DATABASE")]
    assert not [s for s in sql.executed if s.startswith("DROP DATABASE")]


def test_a_first_switch_runs_the_import_and_names_the_row(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    running_sql(
        entry,
        sql,
        {
            "acore_auth": ["account", "account_access", "realmlist"],
            "acore_characters": ["characters", "yulon_realm_type"],
            "acore_playerbots": ["playerbots_account_type"],
            "acore_auth_hero": [],
            "acore_characters_hero": [],
            "acore_playerbots_hero": [],
        },
    )
    sql.marker_rows["acore_characters"] = "coa"
    sql.schemata.update(
        {
            name: ("utf8mb4", "utf8mb4_unicode_ci")
            for name in (
                "acore_auth",
                "acore_characters",
                "acore_playerbots",
                "acore_auth_coa",
                "acore_characters_coa",
                "acore_playerbots_coa",
            )
        }
    )
    lines = switch_lines(tmp_path, entry, "hero", sql)
    assert any("for the first time" in line for line in lines)
    assert any("REALM ROW CoA Free Pick 8087" in s for s in sql.executed)
    assert sql.marker == "hero"


def test_bot_and_ahbot_accounts_are_not_copied(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    running_sql(
        entry,
        sql,
        {
            "acore_auth": ["account", "account_access", "realmlist"],
            "acore_characters": ["characters", "yulon_realm_type"],
            "acore_playerbots": ["playerbots_account_type"],
            "acore_auth_hero": [],
            "acore_characters_hero": [],
            "acore_playerbots_hero": [],
        },
    )
    sql.marker_rows["acore_characters"] = "coa"
    sql.schemata.update(
        {name: ("utf8mb4", "utf8mb4_unicode_ci") for name in sql.tables}
    )
    switch_lines(tmp_path, entry, "hero", sql)
    copied = [s for s in sql.executed if "INTO `acore_auth`.account SELECT" in s]
    assert any("Player1" in s for s in copied)
    assert any("Player2" in s for s in copied)
    assert not any("rndbot" in s for s in copied)
    assert not any("AHBOT" in s for s in copied)


def test_an_unsafe_bot_prefix_refuses_the_copy(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    running_sql(
        entry,
        sql,
        {
            "acore_auth": ["account", "account_access", "realmlist"],
            "acore_characters": ["characters", "yulon_realm_type"],
            "acore_playerbots": ["playerbots_account_type"],
            "acore_auth_hero": [],
            "acore_characters_hero": [],
            "acore_playerbots_hero": [],
        },
    )
    sql.marker_rows["acore_characters"] = "coa"
    with pytest.raises(coa_realm.RealmSwitchError, match="bot account prefix"):
        switch_lines(tmp_path, entry, "hero", sql, bot_prefix="a'b")


def test_an_unsafe_account_name_is_skipped_and_named(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()

    class EvilSql(FakeSql):
        def query(self, statement: str) -> str:
            if "FROM `acore_auth_coa`.account" in statement:
                return "GoodName\nevil'name\n"
            return super().query(statement)

    evil = EvilSql()
    running_sql(
        entry,
        evil,
        {
            "acore_auth": ["account", "account_access", "realmlist"],
            "acore_characters": ["characters", "yulon_realm_type"],
            "acore_playerbots": ["playerbots_account_type"],
            "acore_auth_hero": [],
            "acore_characters_hero": [],
            "acore_playerbots_hero": [],
        },
    )
    evil.marker_rows["acore_characters"] = "coa"
    lines = switch_lines(tmp_path, entry, "hero", evil)
    copied = [s for s in evil.executed if "INTO `acore_auth`.account SELECT" in s]
    assert any("GoodName" in s for s in copied)
    assert not any("evil" in s for s in copied)
    assert any("Left 1 accounts uncopied" in line for line in lines)


def test_an_unreadable_source_schema_refuses_before_any_rename(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.marker_rows["acore_characters"] = "coa"
    del sql.tables["acore_auth"]
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )

    class BlindSql(FakeSql):
        def query(self, statement: str) -> str:
            if "information_schema.SCHEMATA" in statement and "acore_auth'" in statement:
                raise RuntimeError("gone")
            return super().query(statement)

    blind = BlindSql()
    blind.tables = dict(sql.tables)
    blind.schemata = dict(sql.schemata)
    blind.marker_rows["acore_characters"] = "coa"
    with pytest.raises(coa_realm.RealmSwitchError, match="could not be read"):
        list(
            coa_realm.switch(
                entry,
                tmp_path,
                "hero",
                sql=blind,
                servers_running=lambda: False,
                run_import=lambda: iter(()),
                set_realm_row=lambda name, port: None,
                write_override=lambda env: None,
                bot_prefix="rndbot",
            )
        )
    assert not [s for s in blind.executed if s.startswith("RENAME TABLE")]
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_a_dead_database_reads_no_marker_and_starts_nothing(tmp_path: Path) -> None:
    entry = coa()

    class DeadSql(FakeSql):
        def query(self, statement: str) -> str:
            raise RuntimeError("the database is down")

    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )
    assert coa_realm.read_marker(DeadSql().query, entry) is None


def test_a_marker_answer_that_is_no_realm_type_id_reads_as_absent(tmp_path: Path) -> None:
    """A canned test double answering every query with a count (C4a's) must not refuse a start.

    `read_marker` takes the first id-shaped value and reads anything else as
    absent: a map count or a row count is not a verdict about the realm type.
    """
    entry = coa()
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )
    assert coa_realm.read_marker(lambda statement: "20000\n", entry) is None
    assert coa_realm.refusal(entry, tmp_path, None) is None


def test_an_interrupted_first_switch_retries_from_scratch(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    running_sql(
        entry,
        sql,
        {
            "acore_auth": ["half_imported"],
            "acore_characters": ["half_imported"],
            "acore_playerbots": [],
            "acore_auth_coa": ["account", "account_access", "realmlist"],
            "acore_characters_coa": ["characters", "yulon_realm_type"],
            "acore_playerbots_coa": ["playerbots_account_type"],
        },
    )
    sql.marker_rows.pop("acore_characters", None)
    sql.schemata.update(
        {name: ("utf8mb4", "utf8mb4_unicode_ci") for name in sql.tables}
    )
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": "coa"}) + "\n", encoding="utf-8"
    )
    (tmp_path / coa_realm.SWITCH_MARKER_FILE).write_text(
        json.dumps({"version": 1, "target": "hero"}) + "\n", encoding="utf-8"
    )

    parked = FakeSql()
    parked.tables = {k: list(v) for k, v in sql.tables.items()}
    parked.schemata = dict(sql.schemata)
    parked.marker_rows["acore_characters_coa"] = "coa"
    renames = [s for s in parked.executed if s.startswith("RENAME TABLE")]
    assert renames == []
    wrote: list[Mapping[str, str]] = []
    lines = list(
        coa_realm.switch(
            entry,
            tmp_path,
            "hero",
            sql=parked,
            servers_running=lambda: False,
            run_import=lambda: iter(["the import ran"]),
            set_realm_row=lambda name, port: None,
            write_override=wrote.append,
            bot_prefix="rndbot",
        )
    )
    assert any("interrupted" in line for line in lines)
    # The interrupted partial import was dropped and the parked set put back first
    # (the recovery's own rename), so the fresh switch parked the preserved coa
    # set again and the plain schemas hold the new hero import's marker.
    assert sorted(parked.tables["acore_auth_coa"]) == ["account", "account_access", "realmlist"]
    assert parked.marker_rows.get("acore_characters") == "hero"
    assert parked.marker_rows.get("acore_characters_coa") == "coa"
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


# -- the guards -----------------------------------------------------------------


def test_an_entry_without_realm_types_has_no_gate(tmp_path: Path) -> None:
    from tests.support_native import ENTRY

    assert coa_realm.refusal(ENTRY, tmp_path, "coa") is None
    assert coa_realm.has_realm_types(ENTRY) is False
