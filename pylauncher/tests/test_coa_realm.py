"""T812 C3a: realm types, the engine (no Qt).

The ACTIVE type lives under the plain `acore_auth`/`acore_characters`/
`acore_playerbots` names; inactive types are parked as `<db>_<id>` schemas and
a switch is ONE atomic `RENAME TABLE`, with the servers stopped. A fake `sql`
models the databases the switch touches: tables per schema, the marker row
that travels with its table, and the `account`/`account_access` rows with
their keys (AUTO_INCREMENT ids, unique usernames, the `(id, RealmID)` primary
key). A rename is validated whole before anything moves, as MySQL's is, and
any statement shape the fake does not model fails the test: no daemon, no
database, and no statement that passes only because nobody read it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from yulon import coa_realm
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.resources import installers_dir

PLAIN = ("acore_auth", "acore_characters", "acore_playerbots")
MARKER = coa_realm.MARKER_TABLE


def coa() -> CatalogEntry:
    return load_catalog().get("wow-coa")


class FakeSql(coa_realm.SqlRunner):
    """The databases a switch touches, and a record of every write."""

    def __init__(self) -> None:
        self.tables: dict[str, list[str]] = {}
        self.schemata: dict[str, tuple[str, str]] = {}
        self.executed: list[str] = []
        self.queries: list[str] = []
        self.fail_on: str = ""
        """A substring: an `execute()` containing it raises."""
        self.fail_query_on: str = ""
        """A substring: a `query()` containing it raises."""
        self.marker_rows: dict[str, str] = {}
        """The `yulon_realm_type` row per schema, traveling with its table."""
        self.rows: dict[tuple[str, str], list[dict[str, object]]] = {}
        """`account`/`account_access` rows per (schema, table), traveling with the table."""

    @property
    def marker(self) -> str | None:
        return self.marker_rows.get("acore_characters")

    # -- writes ------------------------------------------------------------

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
            self._rename(statement)
        elif upper.startswith("DROP DATABASE"):
            name = statement.split("`")[1]
            self.tables.pop(name, None)
            self.schemata.pop(name, None)
            self.marker_rows.pop(name, None)
            for key in [key for key in self.rows if key[0] == name]:
                del self.rows[key]
        elif upper.startswith("DROP TABLE"):
            parts = statement.split("`")
            for schema, table in zip(parts[1::4], parts[3::4], strict=True):
                if table not in self.tables.get(schema, []):
                    raise RuntimeError(f"Unknown table '{schema}.{table}'")
                self.tables[schema].remove(table)
                self.rows.pop((schema, table), None)
                if table == MARKER:
                    self.marker_rows.pop(schema, None)
        elif upper.startswith("CREATE TABLE IF NOT EXISTS") and MARKER in statement:
            schema = statement.split("`")[1]
            if schema not in self.tables:
                raise RuntimeError(f"Unknown database '{schema}'")
            if MARKER not in self.tables[schema]:
                self.tables[schema].append(MARKER)
                self.tables[schema].sort()
        elif upper.startswith("INSERT IGNORE INTO") and MARKER in statement:
            schema = statement.split("`")[1]
            if MARKER not in self.tables.get(schema, []):
                raise RuntimeError(f"Table '{schema}.{MARKER}' doesn't exist")
            value = statement.split("'")[1]
            held = self.marker_rows.get(schema)
            if held is None:
                self.marker_rows[schema] = value
            elif value not in held.split("\n"):
                self.marker_rows[schema] = f"{held}\n{value}"
        elif self._copy_account(statement) or self._copy_access(statement):
            pass
        else:
            raise AssertionError(f"a statement the fake does not model: {statement}")

    def _rename(self, statement: str) -> None:
        body = statement[len("RENAME TABLE") :].strip().rstrip(";").strip()
        if not body:
            raise RuntimeError("You have an error in your SQL syntax near ''")
        moves = []
        for move in body.split(","):
            old, _, new = move.strip().partition(" TO ")
            old_schema, old_table = (part.strip(" `") for part in old.split("`.`"))
            new_schema, new_table = (part.strip(" `") for part in new.split("`.`"))
            moves.append((old_schema, old_table, new_schema, new_table))
        # Validated whole first, in statement order as MySQL runs it (a name freed by an
        # earlier move may be taken by a later one): every table moves or none does.
        names = {(schema, table) for schema, tables in self.tables.items() for table in tables}
        for old_schema, old_table, new_schema, new_table in moves:
            if (old_schema, old_table) not in names:
                raise RuntimeError(f"Table '{old_schema}.{old_table}' doesn't exist")
            if new_schema not in self.tables:
                raise RuntimeError(f"Unknown database '{new_schema}'")
            if (new_schema, new_table) in names:
                raise RuntimeError(f"Table '{new_table}' already exists")
            names.remove((old_schema, old_table))
            names.add((new_schema, new_table))
        for old_schema, old_table, new_schema, new_table in moves:
            self.tables[old_schema].remove(old_table)
            self.tables[new_schema].append(new_table)
            self.tables[new_schema].sort()
            if (old_schema, old_table) in self.rows:
                self.rows[(new_schema, new_table)] = self.rows.pop((old_schema, old_table))
            if old_table == MARKER and old_schema in self.marker_rows:
                self.marker_rows[new_schema] = self.marker_rows.pop(old_schema)

    def _need(self, schema: str, table: str) -> list[dict[str, object]]:
        if table not in self.tables.get(schema, []):
            raise RuntimeError(f"Table '{schema}.{table}' doesn't exist")
        return self.rows.setdefault((schema, table), [])

    def _copy_account(self, statement: str) -> bool:
        found = re.fullmatch(
            r"INSERT INTO `(\w+)`\.account \(([\w, ]+)\) SELECT ([\w, ]+) "
            r"FROM `(\w+)`\.account WHERE id = (\d+);",
            statement,
        )
        if found is None:
            return False
        target, into, picked, source, key = found.groups()
        assert into == picked, statement
        columns = [column.strip() for column in into.split(",")]
        assert "id" not in columns, "the target allocates its own id"
        rows = self._need(target, "account")
        for row in self._need(source, "account"):
            if row["id"] != int(key):
                continue
            new = {column: row[column] for column in columns}
            if any(other["username"] == new["username"] for other in rows):
                raise RuntimeError(f"Duplicate entry '{new['username']}' for key 'idx_username'")
            new["id"] = max((int(str(other["id"])) for other in rows), default=0) + 1
            rows.append(new)
        return True

    def _copy_access(self, statement: str) -> bool:
        found = re.fullmatch(
            r"INSERT INTO `(\w+)`\.account_access \(id, gmlevel, RealmID\) "
            r"SELECT (\d+), gmlevel, RealmID FROM `(\w+)`\.account_access WHERE id = (\d+);",
            statement,
        )
        if found is None:
            return False
        target, new_key, source, key = found.groups()
        rows = self._need(target, "account_access")
        for row in self._need(source, "account_access"):
            if row["id"] != int(key):
                continue
            if any(o["id"] == int(new_key) and o["RealmID"] == row["RealmID"] for o in rows):
                raise RuntimeError("Duplicate entry for key 'PRIMARY'")
            rows.append({"id": int(new_key), "gmlevel": row["gmlevel"], "RealmID": row["RealmID"]})
        return True

    # -- reads -------------------------------------------------------------

    def query(self, statement: str) -> str:
        if self.fail_query_on and self.fail_query_on in statement:
            raise RuntimeError(f"the database did not answer: {statement[:60]}")
        self.queries.append(statement)
        if "information_schema.SCHEMATA" in statement:
            schema = statement.split("'")[-2]
            if schema not in self.tables:
                return ""
            charset, collation = self.schemata.get(schema, ("utf8mb4", "utf8mb4_unicode_ci"))
            return f"{charset}\t{collation}\n"
        if "information_schema.TABLES" in statement:
            schema = statement.split("'")[1]
            return "".join(f"{t}\n" for t in sorted(self.tables.get(schema, [])))
        if statement.startswith("SELECT id FROM `") and f"`.`{MARKER}`" in statement:
            schema = statement.split("`")[1]
            if MARKER not in self.tables.get(schema, []):
                raise RuntimeError(f"Table '{schema}.{MARKER}' doesn't exist")
            held = self.marker_rows.get(schema)
            return f"{held}\n" if held is not None else ""
        found = re.fullmatch(
            r"SELECT username FROM `(\w+)`\.account WHERE UPPER\(username\) NOT LIKE "
            r"'([^']*)' ESCAPE '!' AND username <> '(\w+)';",
            statement,
        )
        if found is not None:
            schema, pattern, ahbot = found.groups()
            like = _like(pattern)
            return "".join(
                f"{row['username']}\n"
                for row in self._need(schema, "account")
                if not like.fullmatch(str(row["username"]).upper()) and row["username"] != ahbot
            )
        found = re.fullmatch(r"SELECT id FROM `(\w+)`\.account WHERE username = '(.+)';", statement)
        if found is not None:
            schema, name = found.groups()
            return "".join(
                f"{row['id']}\n" for row in self._need(schema, "account") if row["username"] == name
            )
        found = re.fullmatch(
            r"SELECT COUNT\(\*\) FROM `(\w+)`\.account t JOIN `(\w+)`\.account s ON "
            r"s\.username = t\.username AND s\.salt = t\.salt AND s\.verifier = t\.verifier "
            r"WHERE t\.id = (\w+) AND s\.id = (\d+);",
            statement,
        )
        if found is not None:
            target, source, tid, sid = found.groups()
            same = [
                1
                for t in self._need(target, "account")
                for s in self._need(source, "account")
                if str(t["id"]) == tid
                and str(s["id"]) == sid
                and all(t[c] == s[c] for c in ("username", "salt", "verifier"))
            ]
            return f"{len(same)}\n"
        found = re.fullmatch(
            r"SELECT COUNT\(\*\) FROM `(\w+)`\.account_access WHERE id = (\d+);", statement
        )
        if found is not None:
            schema, key = found.groups()
            return (
                f"{sum(1 for r in self._need(schema, 'account_access') if str(r['id']) == key)}\n"
            )
        found = re.fullmatch(
            r"SELECT COUNT\(\*\) FROM `(\w+)`\.account_access t JOIN `(\w+)`\.account_access s "
            r"ON s\.RealmID = t\.RealmID AND s\.gmlevel = t\.gmlevel "
            r"WHERE t\.id = (\d+) AND s\.id = (\d+);",
            statement,
        )
        if found is not None:
            target, source, tid, sid = found.groups()
            same = [
                1
                for t in self._need(target, "account_access")
                for s in self._need(source, "account_access")
                if str(t["id"]) == tid
                and str(s["id"]) == sid
                and t["RealmID"] == s["RealmID"]
                and t["gmlevel"] == s["gmlevel"]
            ]
            return f"{len(same)}\n"
        raise AssertionError(f"a query the fake does not model: {statement}")


def _like(pattern: str) -> re.Pattern[str]:
    """A MySQL `LIKE` pattern with `ESCAPE '!'`, as a regular expression."""
    out = []
    chars = iter(pattern)
    for ch in chars:
        if ch == "!":
            out.append(re.escape(next(chars)))
        elif ch == "%":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return re.compile("".join(out), re.DOTALL)


def seed_accounts(sql: FakeSql, schema: str, accounts: Mapping[str, int | None]) -> None:
    """`account` rows in `schema` (ids from 11, unlike any fresh target's), with GM rows."""
    rows = sql.rows.setdefault((schema, "account"), [])
    access = sql.rows.setdefault((schema, "account_access"), [])
    for name, gm in accounts.items():
        key = max((int(str(row["id"])) for row in rows), default=10) + 1
        rows.append(
            {
                "id": key,
                "username": name,
                "salt": f"salt-{name}".encode(),
                "verifier": f"verifier-{name}".encode(),
                "email": f"{name.lower()}@example.invalid",
                "reg_mail": "",
                "joindate": "2026-10-01 12:00:00",
                "expansion": 2,
            }
        )
        if gm is not None:
            access.append({"id": key, "gmlevel": gm, "RealmID": -1})


PLAYERS = {"Player1": 3, "rndbot7": None, "AHBOT": 2, "Player2": None}
"""The coa type's accounts: two players (one a GM), a bot and the auction house bot."""


def coa_schemas(sql: FakeSql, *, marker: str | None = "coa", parks: tuple[str, ...] = ()) -> None:
    """The plain schemas holding the coa set (marked unless `marker` is None)."""
    sql.tables.update(
        {
            "acore_auth": ["account", "account_access", "realmlist"],
            "acore_characters": ["characters"] + ([MARKER] if marker is not None else []),
            "acore_playerbots": ["playerbots_account_type"],
        }
    )
    for park in parks:
        for schema in PLAIN:
            sql.tables[f"{schema}_{park}"] = []
    if marker is not None:
        sql.marker_rows["acore_characters"] = marker
    seed_accounts(sql, "acore_auth", PLAYERS)
    for schema in sql.tables:
        sql.schemata[schema] = ("utf8mb4", "utf8mb4_unicode_ci")


def parked_set(sql: FakeSql, type_id: str, *, marker: str | None = None) -> None:
    """A whole parked set of `type_id`, marked as `marker` (default: `type_id`)."""
    sql.tables[f"acore_auth_{type_id}"] = ["account", "account_access", "realmlist"]
    sql.tables[f"acore_characters_{type_id}"] = ["characters", MARKER]
    sql.tables[f"acore_playerbots_{type_id}"] = ["playerbots_account_type"]
    sql.marker_rows[f"acore_characters_{type_id}"] = marker or type_id
    seed_accounts(sql, f"acore_auth_{type_id}", {f"{type_id.title()}1": None})
    for schema in PLAIN:
        sql.schemata[f"{schema}_{type_id}"] = ("utf8mb4", "utf8mb4_unicode_ci")


def fresh_import(sql: FakeSql) -> Iterator[str]:
    """The db-import one-shot over empty plain schemas: AzerothCore's base tables, no accounts."""
    sql.tables["acore_auth"].extend(["account", "account_access", "realmlist"])
    sql.tables["acore_characters"].append("characters")
    sql.tables["acore_playerbots"].append("playerbots_account_type")
    for schema in PLAIN:
        sql.tables[schema].sort()
    yield "the import ran"


def write_file(tmp_path: Path, type_id: str) -> None:
    (tmp_path / coa_realm.ACTIVE_FILE).write_text(
        json.dumps({"version": 1, "active": type_id}) + "\n", encoding="utf-8"
    )


def write_crash(tmp_path: Path, target: str, *, imported: bool, source: str = "coa") -> None:
    (tmp_path / coa_realm.SWITCH_MARKER_FILE).write_text(
        json.dumps({"version": 2, "target": target, "imported": imported, "source": source}) + "\n",
        encoding="utf-8",
    )


def file_type(tmp_path: Path) -> str:
    return json.loads((tmp_path / coa_realm.ACTIVE_FILE).read_text(encoding="utf-8"))["active"]


def switch_lines(
    tmp_path: Path,
    entry: CatalogEntry,
    target: str,
    sql: FakeSql,
    *,
    file: str | None = "coa",
    run_import=None,  # type: ignore[no-untyped-def]
    bot_prefix: str = "rndbot",
) -> list[str]:
    if file is not None:
        write_file(tmp_path, file)
    wrote: list[Mapping[str, str]] = []

    def _row(name: str, port: str) -> None:
        sql.executed.append(f"REALM ROW {name} {port}")

    lines = list(
        coa_realm.switch(
            entry,
            tmp_path,
            target,
            sql=sql,
            servers_running=lambda: False,
            run_import=run_import or (lambda: fresh_import(sql)),
            set_realm_row=_row,
            write_override=lambda env: wrote.append(dict(env)),
            bot_prefix=bot_prefix,
        )
    )
    lines.extend(f"OVERRIDE {sorted(env)}" for env in wrote)
    return lines


def drops(sql: FakeSql) -> list[str]:
    return [s for s in sql.executed if s.startswith("DROP TABLE")]


def renames(sql: FakeSql) -> list[str]:
    return [s for s in sql.executed if s.startswith("RENAME TABLE")]


def usernames(sql: FakeSql, schema: str) -> list[str]:
    return sorted(str(row["username"]) for row in sql.rows.get((schema, "account"), []))


# -- the render -----------------------------------------------------------------


def test_wildcard_env_has_the_wildcard_keys_and_coa_does_not(tmp_path: Path) -> None:
    entry = coa()
    write_file(tmp_path, "wc")
    plan = composegen.render(
        entry, tmp_path, templates_root=installers_dir(), platform_id=lambda: "linux"
    )
    override = yaml.safe_load(plan.override)
    world = next(
        service for name, service in override["services"].items() if name.endswith("worldserver")
    )
    assert world["environment"]["AC_CO_ACHALLENGES_GAME_MODES_REALM"] == "WildCard"
    write_file(tmp_path, "coa")
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
    write_file(tmp_path, "coa")
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
    write_file(tmp_path, "nope")
    assert coa_realm.active(coa(), tmp_path).id == "coa"


# -- the switch -----------------------------------------------------------------


def test_the_switch_renames_both_ways_in_one_statement(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    # A second switch: the plain set holds hero, and coa is parked whole.
    coa_schemas(sql, marker="hero", parks=("hero",))
    parked_set(sql, "coa")
    lines = switch_lines(tmp_path, entry, "coa", sql, file="hero")
    assert len(renames(sql)) == 1, sql.executed
    assert renames(sql)[0].count(" TO ") == 12, renames(sql)[0]
    assert "`acore_auth`.`account` TO `acore_auth_hero`.`account`" in renames(sql)[0]
    assert "`acore_auth_coa`.`realmlist` TO `acore_auth`.`realmlist`" in renames(sql)[0]
    assert len([s for s in sql.executed if s.startswith("CREATE DATABASE")]) == 3
    assert (tmp_path / coa_realm.ACTIVE_FILE).read_text(encoding="utf-8") == json.dumps(
        {"version": 1, "active": "coa"}, indent=2
    ) + "\n"
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()
    assert sql.marker == "coa"
    assert sql.marker_rows["acore_characters_hero"] == "hero"
    assert any("coa" in line for line in lines)


def test_switching_back_restores_every_table(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    parked_set(sql, "hero")
    before = {schema: sorted(sql.tables[schema]) for schema in PLAIN}
    accounts = usernames(sql, "acore_auth")
    switch_lines(tmp_path, entry, "hero", sql)
    switch_lines(tmp_path, entry, "coa", sql, file=None)
    assert {schema: sorted(sql.tables[schema]) for schema in PLAIN} == before
    assert usernames(sql, "acore_auth") == accounts
    assert sql.marker == "coa" and file_type(tmp_path) == "coa"


def test_a_pre_marker_install_switches_there_and_back_and_keeps_every_table(
    tmp_path: Path,
) -> None:
    """The review's reproduced data loss: no marker, coa -> hero -> coa, three presses.

    Before the fix the switch back refused in `_verify` (the parked coa set had
    no marker), and the third press read it as an interrupted first import and
    DROPPED the plain tables: the real coa characters. The switch now marks a
    pre-marker set with the file's type before it parks it.
    """
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql, marker=None)
    accounts = usernames(sql, "acore_auth")
    lines = switch_lines(tmp_path, entry, "hero", sql, file=None)
    assert any("Marked the databases as the coa realm type" in line for line in lines)
    assert sql.marker_rows["acore_characters_coa"] == "coa", "the parked set traveled marked"
    assert file_type(tmp_path) == "hero"
    switch_lines(tmp_path, entry, "coa", sql, file=None)
    assert file_type(tmp_path) == "coa"
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()
    switch_lines(tmp_path, entry, "coa", sql, file=None)
    assert drops(sql) == []
    assert sorted(sql.tables["acore_characters"]) == ["characters", MARKER]
    assert usernames(sql, "acore_auth") == accounts
    assert usernames(sql, "acore_auth_hero") == ["Player1", "Player2"]


def test_a_marker_absent_over_no_characters_table_refuses_the_switch(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql, marker=None)
    sql.tables["acore_characters"] = []
    with pytest.raises(coa_realm.RealmSwitchError, match="no characters table"):
        switch_lines(tmp_path, entry, "hero", sql)
    assert sql.executed == []


def test_a_running_server_is_never_switched(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
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
    with pytest.raises(coa_realm.RealmSwitchError, match="'nope' is not one of"):
        switch_lines(tmp_path, entry, "nope", sql)
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
    coa_schemas(sql, parks=("coa",))
    sql.tables["acore_auth_coa"].append("orphan")
    with pytest.raises(coa_realm.RealmSwitchError, match="not empty.*orphan"):
        switch_lines(tmp_path, entry, "hero", sql)
    assert renames(sql) == []
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_a_target_park_of_another_type_refuses_before_any_rename(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    parked_set(sql, "hero", marker="wc")
    with pytest.raises(coa_realm.RealmSwitchError, match="do not read as the hero"):
        switch_lines(tmp_path, entry, "hero", sql)
    assert renames(sql) == []
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_a_file_split_for_the_marker_type_refuses_the_switch(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql, marker="hero")
    with pytest.raises(coa_realm.RealmSwitchError, match="Switch realm type to hero"):
        switch_lines(tmp_path, entry, "wc", sql)
    assert renames(sql) == []


def test_switching_back_to_the_marker_type_repairs_without_renaming(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql, marker="hero")
    parked_set(sql, "hero")
    lines = switch_lines(tmp_path, entry, "hero", sql)
    assert renames(sql) == []
    assert (tmp_path / coa_realm.ACTIVE_FILE).read_text(encoding="utf-8") == json.dumps(
        {"version": 1, "active": "hero"}, indent=2
    ) + "\n"
    assert any("no database was touched" in line for line in lines)


def test_the_repair_clears_a_stale_crash_note(tmp_path: Path) -> None:
    """A switch that renamed and verified but died before its file: the repair finishes it."""
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql, marker="hero")
    write_crash(tmp_path, "hero", imported=False)
    switch_lines(tmp_path, entry, "hero", sql)
    assert file_type(tmp_path) == "hero"
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_the_file_is_written_only_after_the_rename_succeeds(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.fail_on = "RENAME TABLE"
    with pytest.raises(coa_realm.RealmSwitchError, match="could not be renamed"):
        switch_lines(tmp_path, entry, "hero", sql)
    assert file_type(tmp_path) == "coa"
    assert (tmp_path / coa_realm.SWITCH_MARKER_FILE).is_file()


def test_a_failed_rename_leaves_no_park_behind_and_the_retry_switches(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.fail_on = "RENAME TABLE"
    with pytest.raises(coa_realm.RealmSwitchError, match="could not be renamed"):
        switch_lines(tmp_path, entry, "hero", sql)
    assert [s for s in sql.executed if s.startswith("CREATE DATABASE")]
    assert not [s for s in sql.executed if s.startswith("DROP DATABASE")]
    sql.fail_on = ""
    switch_lines(tmp_path, entry, "hero", sql, file=None)
    assert file_type(tmp_path) == "hero" and sql.marker == "hero"
    assert drops(sql) == []


def test_a_first_switch_runs_the_import_and_names_the_row(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    lines = switch_lines(tmp_path, entry, "hero", sql)
    assert any("for the first time" in line for line in lines)
    assert "the import ran" in lines
    assert any("REALM ROW CoA Free Pick 8087" in s for s in sql.executed)
    assert sql.marker == "hero"


def test_the_marker_is_written_only_after_the_accounts_are_copied(tmp_path: Path) -> None:
    """The marker is the first import's commit: written last, so a set without it is partial."""
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    switch_lines(tmp_path, entry, "hero", sql)
    marks = [
        i for i, s in enumerate(sql.executed) if s.startswith("INSERT IGNORE") and "'hero'" in s
    ]
    copies = [i for i, s in enumerate(sql.executed) if s.startswith("INSERT INTO `acore_auth`.")]
    assert marks and copies and max(copies) < marks[0]


def test_bot_and_ahbot_accounts_are_not_copied(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    lines = switch_lines(tmp_path, entry, "hero", sql)
    assert usernames(sql, "acore_auth") == ["Player1", "Player2"]
    assert "Copied 2 player accounts into the new realm type's databases." in lines


def test_accounts_get_the_targets_own_ids_and_their_gm_rights(tmp_path: Path) -> None:
    """Explicit columns, fresh ids, and `account_access` (keyed `id`) under the TARGET id."""
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    source = {row["username"]: row for row in sql.rows[("acore_auth", "account")]}

    def seeded_import() -> Iterator[str]:
        yield from fresh_import(sql)
        seed_accounts(sql, "acore_auth", {"SEEDED": None})  # takes id 11 in the fresh target

    switch_lines(tmp_path, entry, "hero", sql, run_import=seeded_import)
    target = {row["username"]: row for row in sql.rows[("acore_auth", "account")]}
    assert target["Player1"]["id"] != source["Player1"]["id"]
    assert target["Player1"]["salt"] == source["Player1"]["salt"]
    assert target["Player1"]["verifier"] == source["Player1"]["verifier"]
    assert sql.rows[("acore_auth", "account_access")] == [
        {"id": target["Player1"]["id"], "gmlevel": 3, "RealmID": -1}
    ]


def test_a_username_the_target_already_holds_refuses_and_marks_nothing(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)

    def colliding_import() -> Iterator[str]:
        yield from fresh_import(sql)
        seed_accounts(sql, "acore_auth", {"Player2": None})

    with pytest.raises(coa_realm.RealmSwitchError, match="already hold an account named Player2"):
        switch_lines(tmp_path, entry, "hero", sql, run_import=colliding_import)
    assert sql.marker is None, "no marker: the next press puts the partial set back"
    assert json.loads((tmp_path / coa_realm.SWITCH_MARKER_FILE).read_text())["imported"] is True
    assert file_type(tmp_path) == "coa"


def test_an_account_that_does_not_arrive_intact_refuses(tmp_path: Path) -> None:
    entry = coa()

    class Mangling(FakeSql):
        def execute(self, statement: str) -> None:
            super().execute(statement)
            if statement.startswith("INSERT INTO `acore_auth`.account "):
                self.rows[("acore_auth", "account")][-1]["verifier"] = b"mangled"

    sql = Mangling()
    coa_schemas(sql)
    with pytest.raises(coa_realm.RealmSwitchError, match="password intact"):
        switch_lines(tmp_path, entry, "hero", sql)
    assert sql.marker is None and file_type(tmp_path) == "coa"


def test_gm_rights_that_do_not_arrive_refuse(tmp_path: Path) -> None:
    entry = coa()

    class Dropping(FakeSql):
        def execute(self, statement: str) -> None:
            if "account_access (id, gmlevel, RealmID)" in statement:
                self.executed.append(statement)
                return
            super().execute(statement)

    sql = Dropping()
    coa_schemas(sql)
    with pytest.raises(coa_realm.RealmSwitchError, match="GM rights did not all arrive"):
        switch_lines(tmp_path, entry, "hero", sql)
    assert sql.marker is None


def test_the_bot_prefix_matches_only_itself_in_like(tmp_path: Path) -> None:
    """`_` in a prefix is a LIKE wildcard unless escaped: `rnd_bot` must not hide `rndXbot1`."""
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.rows[("acore_auth", "account")].clear()
    sql.rows[("acore_auth", "account_access")].clear()
    seed_accounts(sql, "acore_auth", {"RNDXBOT1": None, "RND_BOT2": None, "Player9": None})
    switch_lines(tmp_path, entry, "hero", sql, bot_prefix="rnd_bot")
    assert usernames(sql, "acore_auth") == ["Player9", "RNDXBOT1"]


def test_an_unsafe_bot_prefix_refuses_the_copy(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    with pytest.raises(coa_realm.RealmSwitchError, match="bot account prefix"):
        switch_lines(tmp_path, entry, "hero", sql, bot_prefix="a'b")


def test_an_unsafe_account_name_is_skipped_and_named(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    seed_accounts(sql, "acore_auth", {"evil'name": None})
    lines = switch_lines(tmp_path, entry, "hero", sql)
    assert usernames(sql, "acore_auth") == ["Player1", "Player2"]
    assert not any("evil" in s for s in sql.executed)
    assert any("Left 1 accounts uncopied" in line for line in lines)


def test_an_account_list_that_fails_refuses_the_copy(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.fail_query_on = "SELECT username FROM"
    with pytest.raises(coa_realm.RealmSwitchError, match="player accounts could not be listed"):
        switch_lines(tmp_path, entry, "hero", sql)
    assert sql.marker is None


def test_an_unreadable_source_schema_refuses_before_any_rename(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.fail_query_on = "SCHEMA_NAME = 'acore_auth'"
    with pytest.raises(coa_realm.RealmSwitchError, match="could not be read"):
        switch_lines(tmp_path, entry, "hero", sql)
    assert renames(sql) == []
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_a_collation_that_is_no_name_refuses_before_any_rename(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.schemata["acore_auth"] = ("utf8mb4", "utf8mb4_bin;DROP")
    with pytest.raises(coa_realm.RealmSwitchError, match="collation"):
        switch_lines(tmp_path, entry, "hero", sql)
    assert renames(sql) == []
    assert not [s for s in sql.executed if s.startswith("CREATE DATABASE")]


def test_a_plain_table_whose_name_is_no_name_refuses_before_any_rename(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.tables["acore_playerbots"].append("bad`name")
    with pytest.raises(coa_realm.RealmSwitchError, match="not a schema or table name"):
        switch_lines(tmp_path, entry, "hero", sql)
    assert renames(sql) == []


def test_an_unreadable_target_park_refuses_and_imports_no_fresh_set(tmp_path: Path) -> None:
    """Codex: a listing error read as an empty park imported a fresh set over the real one."""
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    parked_set(sql, "hero")
    sql.fail_query_on = "TABLE_SCHEMA = 'acore_auth_hero'"
    with pytest.raises(coa_realm.DatabaseUnreadable, match="acore_auth_hero"):
        switch_lines(tmp_path, entry, "hero", sql)
    assert renames(sql) == []
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_an_unreadable_marker_refuses_the_switch(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    sql.fail_query_on = f"`.`{MARKER}`"
    with pytest.raises(coa_realm.DatabaseUnreadable):
        switch_lines(tmp_path, entry, "hero", sql)
    assert sql.executed == []


# -- verification ---------------------------------------------------------------


class AfterRename(FakeSql):
    """Runs `then` once, right after the first RENAME TABLE: the state verify must catch."""

    def __init__(self, then) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self.then = then
        self.done = False

    def execute(self, statement: str) -> None:
        super().execute(statement)
        if statement.startswith("RENAME TABLE") and not self.done:
            self.done = True
            self.then(self)


def _second_switch(sql: FakeSql) -> None:
    coa_schemas(sql)
    parked_set(sql, "hero")


def test_verify_refuses_a_target_park_left_non_empty(tmp_path: Path) -> None:
    sql = AfterRename(lambda s: s.tables["acore_auth_hero"].append("late"))
    _second_switch(sql)
    with pytest.raises(coa_realm.RealmSwitchError, match="acore_auth_hero schema is not empty"):
        switch_lines(tmp_path, coa(), "hero", sql)
    assert file_type(tmp_path) == "coa"
    assert (tmp_path / coa_realm.SWITCH_MARKER_FILE).is_file()


def test_verify_refuses_plain_databases_of_another_type(tmp_path: Path) -> None:
    sql = AfterRename(lambda s: s.marker_rows.__setitem__("acore_characters", "wc"))
    _second_switch(sql)
    with pytest.raises(coa_realm.RealmSwitchError, match="plain databases do not read as the hero"):
        switch_lines(tmp_path, coa(), "hero", sql)
    assert file_type(tmp_path) == "coa"
    assert (tmp_path / coa_realm.SWITCH_MARKER_FILE).is_file()


def test_verify_refuses_a_previous_set_parked_unmarked(tmp_path: Path) -> None:
    sql = AfterRename(lambda s: s.marker_rows.pop("acore_characters_coa"))
    _second_switch(sql)
    with pytest.raises(coa_realm.RealmSwitchError, match="parked coa databases do not read"):
        switch_lines(tmp_path, coa(), "hero", sql)
    assert file_type(tmp_path) == "coa"


def test_verify_refuses_when_the_target_park_cannot_be_listed(tmp_path: Path) -> None:
    sql = AfterRename(lambda s: setattr(s, "fail_query_on", "TABLE_SCHEMA = 'acore_auth_hero'"))
    _second_switch(sql)
    with pytest.raises(coa_realm.DatabaseUnreadable):
        switch_lines(tmp_path, coa(), "hero", sql)
    assert file_type(tmp_path) == "coa"
    assert (tmp_path / coa_realm.SWITCH_MARKER_FILE).is_file()


# -- an interrupted first switch -------------------------------------------------


def _killed_during_import(sql: FakeSql) -> None:
    """A first switch to hero killed during its import: coa parked whole, hero half-imported."""
    coa_schemas(sql)
    sql.tables.update(
        {
            "acore_auth_coa": sql.tables["acore_auth"],
            "acore_characters_coa": sql.tables["acore_characters"],
            "acore_playerbots_coa": sql.tables["acore_playerbots"],
            "acore_auth": ["half_imported"],
            "acore_characters": ["half_imported"],
            "acore_playerbots": [],
        }
    )
    sql.marker_rows = {"acore_characters_coa": "coa"}
    sql.rows = {("acore_auth_coa", table): rows for (_, table), rows in sql.rows.items()}
    for schema in sql.tables:
        sql.schemata[schema] = ("utf8mb4", "utf8mb4_unicode_ci")


def test_an_interrupted_first_switch_retries_from_scratch(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    _killed_during_import(sql)
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=True)
    lines = switch_lines(tmp_path, entry, "hero", sql, file=None)
    assert any("interrupted" in line for line in lines)
    assert drops(sql) == [
        "DROP TABLE `acore_auth`.`half_imported`;",
        "DROP TABLE `acore_characters`.`half_imported`;",
    ]
    assert sorted(sql.tables["acore_auth_coa"]) == ["account", "account_access", "realmlist"]
    assert sql.marker == "hero"
    assert sql.marker_rows.get("acore_characters_coa") == "coa"
    assert usernames(sql, "acore_auth") == ["Player1", "Player2"]
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_switching_to_the_file_type_undoes_an_interrupted_first_switch(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    _killed_during_import(sql)
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=True)
    lines = switch_lines(tmp_path, entry, "coa", sql, file=None)
    assert lines[-1] == "The server is already on the coa realm type; nothing was switched."
    assert sql.marker == "coa"
    assert usernames(sql, "acore_auth") == ["AHBOT", "Player1", "Player2", "rndbot7"]
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_a_plain_set_no_switch_began_importing_is_never_dropped(tmp_path: Path) -> None:
    """The crash note says the import never began: the plain tables are not this switch's."""
    entry = coa()
    sql = FakeSql()
    _killed_during_import(sql)
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=False)
    with pytest.raises(coa_realm.RealmSwitchError, match="no import of that switch wrote"):
        switch_lines(tmp_path, entry, "hero", sql, file=None)
    assert drops(sql) == [] and renames(sql) == []


def test_an_empty_plain_set_is_put_back_without_the_import_note(tmp_path: Path) -> None:
    """Killed between the rename and the import's note: nothing to drop, the park goes back."""
    entry = coa()
    sql = FakeSql()
    _killed_during_import(sql)
    for schema in PLAIN:
        sql.tables[schema] = []
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=False)
    switch_lines(tmp_path, entry, "coa", sql, file=None)
    assert drops(sql) == []
    assert sql.marker == "coa"


def test_a_press_for_another_target_leaves_an_interrupted_switch_alone(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    _killed_during_import(sql)
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=True)
    with pytest.raises(coa_realm.RealmSwitchError, match="Switch realm type to hero again"):
        switch_lines(tmp_path, entry, "wc", sql, file=None)
    assert drops(sql) == [] and renames(sql) == []


def test_a_parked_set_of_another_type_is_never_put_back(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    _killed_during_import(sql)
    sql.marker_rows["acore_characters_coa"] = "wc"
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=True)
    with pytest.raises(coa_realm.RealmSwitchError, match="nothing was dropped"):
        switch_lines(tmp_path, entry, "hero", sql, file=None)
    assert drops(sql) == [] and renames(sql) == []


def test_a_marked_plain_set_under_a_crash_note_is_never_dropped(tmp_path: Path) -> None:
    """Committed (the marker is the import's last write) but killed before the file."""
    entry = coa()
    sql = FakeSql()
    _killed_during_import(sql)
    sql.tables["acore_characters"] = ["characters", MARKER]
    sql.marker_rows["acore_characters"] = "hero"
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=True)
    lines = switch_lines(tmp_path, entry, "hero", sql, file=None)
    assert drops(sql) == [] and renames(sql) == []
    assert any("no database was touched" in line for line in lines)
    assert file_type(tmp_path) == "hero"


def test_an_unreadable_crash_note_refuses_the_switch(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    coa_schemas(sql)
    (tmp_path / coa_realm.SWITCH_MARKER_FILE).write_text("{torn", encoding="utf-8")
    with pytest.raises(coa_realm.RealmSwitchError, match="cannot be read"):
        switch_lines(tmp_path, entry, "hero", sql)
    assert sql.executed == []


# -- the start gate --------------------------------------------------------------


def test_a_marker_mismatch_refuses_the_start(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql, marker="wc")
    write_file(tmp_path, "coa")
    assert coa_realm.refusal(coa(), tmp_path, sql) == (
        "The databases hold the wc realm type but this server is set to coa. "
        "Switch realm type to wc on the Server tab to put it right."
    )


def test_a_matching_marker_starts(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql)
    write_file(tmp_path, "coa")
    assert coa_realm.refusal(coa(), tmp_path, sql) is None
    assert sql.executed == []


def test_no_marker_starts_and_marks_the_files_type(tmp_path: Path) -> None:
    """A pre-marker install: marked at its next start with the file's type, read back."""
    sql = FakeSql()
    coa_schemas(sql, marker=None)
    assert coa_realm.refusal(coa(), tmp_path, sql) is None
    assert sql.marker == "coa"


def test_no_marker_over_no_characters_table_starts_and_marks_nothing(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql, marker=None)
    sql.tables["acore_characters"] = []
    assert coa_realm.refusal(coa(), tmp_path, sql) is None
    assert sql.executed == []


def test_a_marker_that_will_not_write_refuses_the_start(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql, marker=None)
    sql.fail_on = MARKER
    assert coa_realm.refusal(coa(), tmp_path, sql) == coa_realm.MARK_START_REFUSAL


def test_a_marker_that_does_not_read_back_refuses_the_start(tmp_path: Path) -> None:
    class Deaf(FakeSql):
        def execute(self, statement: str) -> None:
            if statement.startswith("INSERT IGNORE"):
                self.executed.append(statement)  # answered OK, wrote nothing
                return
            super().execute(statement)

    sql = Deaf()
    coa_schemas(sql, marker=None)
    assert coa_realm.refusal(coa(), tmp_path, sql) == coa_realm.MARK_START_REFUSAL


def test_a_crash_note_refuses_the_start_before_any_read(tmp_path: Path) -> None:
    """A first switch killed mid-import leaves plain schemas with no marker to refuse on."""
    sql = FakeSql()
    write_crash(tmp_path, "hero", imported=True)
    assert coa_realm.refusal(coa(), tmp_path, sql) == coa_realm.CRASH_START_REFUSAL
    assert coa_realm.refusal(coa(), tmp_path, None) == coa_realm.CRASH_START_REFUSAL
    assert sql.queries == []


def test_an_unreadable_marker_refuses_the_start_once_a_switch_was_made(tmp_path: Path) -> None:
    """Codex: a transient error read as 'no marker' let a mismatched start by."""
    sql = FakeSql()
    coa_schemas(sql, marker="wc")
    sql.fail_query_on = "information_schema.TABLES"
    assert coa_realm.refusal(coa(), tmp_path, sql) is None, "no file: only the default ever ran"
    write_file(tmp_path, "coa")
    assert coa_realm.refusal(coa(), tmp_path, sql) == coa_realm.UNREADABLE_START_REFUSAL
    assert coa_realm.refusal(coa(), tmp_path, None) == coa_realm.UNREADABLE_START_REFUSAL


def test_the_controller_gate_starts_the_database_before_it_reads(tmp_path: Path) -> None:
    order: list[str] = []

    class Ordered(FakeSql):
        def query(self, statement: str) -> str:
            order.append("query")
            return super().query(statement)

    sql = Ordered()
    coa_schemas(sql, marker="hero")
    write_file(tmp_path, "coa")
    gate = coa_realm.before_servers_gate(
        coa(), tmp_path, start_db=lambda: order.append("start_db"), sql=sql
    )
    assert gate is not None
    assert gate() == coa_realm.mismatch_sentence("hero", "coa")
    assert order[0] == "start_db" and "query" in order


def test_the_controller_gate_refuses_a_database_that_will_not_start(tmp_path: Path) -> None:
    def refuse() -> None:
        raise RuntimeError("the database did not become healthy")

    sql = FakeSql()
    write_file(tmp_path, "coa")
    gate = coa_realm.before_servers_gate(coa(), tmp_path, start_db=refuse, sql=sql)
    assert gate is not None and gate() == coa_realm.UNREADABLE_START_REFUSAL
    write_crash(tmp_path, "hero", imported=False)
    started: list[int] = []
    gate = coa_realm.before_servers_gate(
        coa(), tmp_path, start_db=lambda: started.append(1), sql=sql
    )
    assert gate is not None and gate() == coa_realm.CRASH_START_REFUSAL
    assert started == [], "a crash note refuses before the database is started"


def test_a_marker_answer_that_is_no_realm_type_id_reads_as_absent(tmp_path: Path) -> None:
    """A canned test double answering every query with a count (C4a's) must not refuse a start.

    The listing reads no marker table, so the marker is absent; and with no
    characters table there is nothing to mark.
    """
    canned = coa_realm.FnSql(run=lambda statement: None, ask=lambda statement: "20000\n")
    assert coa_realm.read_marker(canned.query, coa()) is None
    assert coa_realm.refusal(coa(), tmp_path, canned) is None


def test_a_marker_reading_two_types_is_unreadable(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql)
    sql.marker_rows["acore_characters"] = "coa\nhero"
    with pytest.raises(coa_realm.DatabaseUnreadable, match="not one realm type"):
        coa_realm.read_marker(sql.query, coa())


def test_an_entry_without_realm_types_has_no_gate(tmp_path: Path) -> None:
    from tests.support_native import ENTRY

    write_crash(tmp_path, "hero", imported=True)
    assert coa_realm.refusal(ENTRY, tmp_path, None) is None
    assert coa_realm.has_realm_types(ENTRY) is False
    assert (
        coa_realm.before_servers_gate(ENTRY, tmp_path, start_db=lambda: None, sql=FakeSql()) is None
    )


# -- the helpers' own guards ------------------------------------------------------


def test_a_non_empty_schema_is_never_dropped(tmp_path: Path) -> None:
    sql = FakeSql()
    sql.tables = {"acore_auth_hero": ["account"], "acore_auth_wc": []}
    coa_realm._drop_if_empty(sql, "acore_auth_hero")
    coa_realm._drop_if_empty(sql, "acore_auth_wc")
    assert sql.executed == ["DROP DATABASE IF EXISTS `acore_auth_wc`;"]
    sql.fail_query_on = "acore_auth_hero"
    coa_realm._drop_if_empty(sql, "acore_auth_hero")
    assert sql.tables["acore_auth_hero"] == ["account"], "an unreadable schema is left standing"


def test_a_rename_with_nothing_to_move_refuses(tmp_path: Path) -> None:
    entry = coa()
    sql = FakeSql()
    sql.tables = {schema: [] for schema in PLAIN}
    parked = coa_realm._parked(entry, "coa")
    target = coa_realm._parked(entry, "hero")
    with pytest.raises(coa_realm.RealmSwitchError, match="hold no tables to park"):
        list(coa_realm._rename_both_ways(entry, sql, "coa", "hero", parked, target, [[], [], []]))
    assert sql.executed == []


def test_names_and_ids_are_checked_before_they_reach_sql(tmp_path: Path) -> None:
    for bad in ("", "a`b", "a b", "a;b", "a-b"):
        with pytest.raises(coa_realm.RealmSwitchError, match="not a schema or table name"):
            coa_realm._check_name(bad)
    coa_realm._check_name("acore_auth_hero")
    for bad_id in ("Hero", "h", "hero'", "h-ro", "abcdefghijk"):
        with pytest.raises(coa_realm.RealmSwitchError, match="not a realm type id"):
            coa_realm.write_active(tmp_path, bad_id)
    assert not (tmp_path / coa_realm.ACTIVE_FILE).exists()


def test_a_quoted_realm_name_is_never_written_into_sql() -> None:
    for name in ("O'Brien", 'say "hi"', "back\\slash"):
        with pytest.raises(coa_realm.RealmSwitchError, match="cannot be written"):
            coa_realm.realm_name_update(coa(), name)
    assert "SET name='CoA Free Pick'" in coa_realm.realm_name_update(coa(), "CoA Free Pick")


# -- the engine's glue ------------------------------------------------------------


def _engine():  # type: ignore[no-untyped-def]
    from tests.support_native import Recorder
    from yulon.catalog.families.azerothcore import AzerothCoreInstaller

    rec = Recorder()
    return AzerothCoreInstaller(
        coa(),
        installers_root=installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(),
    )


def test_the_engine_asks_the_realm_gate_once_per_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[Path] = []
    real = coa_realm.refusal

    def counting(entry: CatalogEntry, server_dir: Path, sql: object) -> str | None:
        asked.append(server_dir)
        return real(entry, server_dir, sql)  # type: ignore[arg-type]

    monkeypatch.setattr(coa_realm, "refusal", counting)
    _engine().start_refusal(tmp_path)  # the gate lets it by: the rest is asked after it
    assert asked == [tmp_path]
    asked.clear()
    write_crash(tmp_path, "hero", imported=True)
    assert _engine().start_refusal(tmp_path) == coa_realm.CRASH_START_REFUSAL
    assert asked == [tmp_path]


def test_a_realm_refusal_never_forgets_an_owed_start(tmp_path: Path) -> None:
    """T223: only a family refusal its own finish clears by starting lets an owed start go."""
    from yulon.catalog import native

    engine = _engine()
    write_crash(tmp_path, "hero", imported=True)
    assert native.owe_start(tmp_path, why=native.UNTESTED_BUILD) == ""
    assert engine.family_start_refusal(tmp_path) == coa_realm.CRASH_START_REFUSAL, "the ground"
    assert engine.owed_start_left_to_the_family(tmp_path) is False


def test_a_marker_the_install_cannot_write_fails_the_import_stage(tmp_path: Path) -> None:
    from dataclasses import replace

    from yulon.catalog.installer import InstallerError

    engine = _engine()

    def refuse(*args: object, **kwargs: object) -> str:
        raise RuntimeError("the database refused")

    engine._seams = replace(engine._seams, sql_query=refuse)
    ctx = SimpleNamespace(server_dir=tmp_path, secrets=SimpleNamespace(db_password="pw"))
    with pytest.raises(InstallerError, match="could not be marked as the coa realm type"):
        list(engine._mark_realm_type(ctx))  # type: ignore[arg-type]


def _wired_controller(monkeypatch: pytest.MonkeyPatch, server_dir: Path, sql: FakeSql) -> object:
    """CoA's real Server-tab Controller, its database seams routed to `sql`."""
    from yulon import apply, docker
    from yulon.ui import controller_view

    realm: list[str] = []

    def run_statement(self: object, db: str, statement: str) -> None:
        if "realmlist" in statement:
            realm.append(statement)
        else:
            sql.execute(statement)

    def query(self: object, db: str, statement: str) -> str:
        return f"{coa().ports.world}\n" if "realmlist" in statement else sql.query(statement)

    monkeypatch.setattr(apply.DockerSql, "run_statement", run_statement)
    monkeypatch.setattr(apply.DockerSql, "query", query)
    monkeypatch.setattr(docker, "start_database", lambda *_a, **_k: realm.append("start-db"))
    services = controller_view._for_wotlk(coa(), server_dir, None, None)
    services.realm = realm  # type: ignore[attr-defined]
    return services


def test_the_server_tab_start_asks_the_realm_gate_before_the_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Controller's start reaches the gate: a mismatch refuses before the realm row moves."""
    sql = FakeSql()
    coa_schemas(sql, marker="wc")
    write_file(tmp_path, "coa")
    services = _wired_controller(monkeypatch, tmp_path, sql)
    controller = services.controller  # type: ignore[attr-defined]
    assert controller.before_servers() == coa_realm.mismatch_sentence("wc", "coa")
    assert services.realm == ["start-db"], "the database alone, and no port write"  # type: ignore[attr-defined]
    sql.marker_rows["acore_characters"] = "coa"
    assert controller.before_servers() is None
    assert any("realmlist" in s for s in services.realm)  # type: ignore[attr-defined]


# -- round 3: an import under an unfinished switch, the publish order, the reads --


def _seamed_engine(sql: FakeSql):  # type: ignore[no-untyped-def]
    """`_engine()` whose `sql_query` seam is `sql`: SELECTs read it, the rest write it."""
    from dataclasses import replace

    engine = _engine()

    def seam(*args: object, **_kwargs: object) -> str:
        statement = str(args[-1])
        if statement.lstrip().upper().startswith("SELECT"):
            return sql.query(statement)
        sql.execute(statement)
        return ""

    engine._seams = replace(engine._seams, sql_query=seam)
    return engine


def _ctx(server_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(server_dir=server_dir, secrets=SimpleNamespace(db_password="pw"))


def _killed_after_rename(sql: FakeSql) -> None:
    """A first switch coa -> hero killed right after its rename: coa parked whole, plain empty."""
    parked_set(sql, "coa")
    for schema in PLAIN:
        sql.tables[schema] = []
        sql.schemata[schema] = ("utf8mb4", "utf8mb4_unicode_ci")
    seed_accounts(sql, "acore_auth_coa", {"Player1": 3})


def test_the_import_refuses_under_an_unfinished_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opus round 3, reproduced: Install marked a fresh plain set coa beside the parked one.

    Install and "Repair the database" both run the `import` stage; neither may
    import or mark while the crash note stands.
    """
    from yulon.catalog.families.azerothcore import AzerothCoreInstaller
    from yulon.catalog.installer import InstallerError

    sql = FakeSql()
    _killed_after_rename(sql)
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=False)
    imported: list[int] = []
    monkeypatch.setattr(
        AzerothCoreInstaller,
        "stage_import",
        lambda self, ctx, gate, service: iter(imported.append(1) or ["imported"]),
    )
    engine = _seamed_engine(sql)
    with pytest.raises(InstallerError, match="no database import was run"):
        list(engine._import(_ctx(tmp_path)))
    with pytest.raises(InstallerError, match="no database import was run"):
        list(engine._mark_realm_type(_ctx(tmp_path)))
    assert imported == [] and sql.executed == []
    assert "import" in [stage.name for stage in engine.repair_database_stages()], "the ground"
    # Pressing coa then undoes the switch: every account is back in the plain set.
    switch_lines(tmp_path, coa(), "coa", sql, file=None)
    assert usernames(sql, "acore_auth") == ["Coa1", "Player1"]
    assert sql.marker == "coa"
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_the_server_tabs_repair_import_refuses_under_an_unfinished_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import docker

    ran: list[int] = []
    monkeypatch.setattr(docker, "repair_import", lambda *_a, **_k: ran.append(1) or True)
    sql = FakeSql()
    _killed_after_rename(sql)
    write_crash(tmp_path, "hero", imported=False)
    services = _wired_controller(monkeypatch, tmp_path, sql)
    with pytest.raises(docker.DockerRefusal, match="no database import was run"):
        services.controller.repair_import()  # type: ignore[attr-defined]
    assert ran == []


def test_the_import_marks_and_is_marked_by_the_import_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon.catalog.families.azerothcore import AzerothCoreInstaller

    sql = FakeSql()
    coa_schemas(sql, marker=None)
    monkeypatch.setattr(
        AzerothCoreInstaller, "stage_import", lambda self, ctx, gate, service: iter(["imported"])
    )
    lines = list(_seamed_engine(sql)._import(_ctx(tmp_path)))
    assert sql.marker == "coa"
    assert lines[-1] == "The databases are marked as the coa realm type."


def test_the_import_never_overwrites_a_marker(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql, marker="hero")
    lines = list(_seamed_engine(sql)._mark_realm_type(_ctx(tmp_path)))
    assert sql.marker == "hero" and sql.executed == []
    assert lines == ["The databases are already marked as the hero realm type."]


def test_an_import_beside_a_parked_set_keeps_the_crash_note(tmp_path: Path) -> None:
    """The state the import refusal prevents, reached anyway: the note is not called stale."""
    sql = FakeSql()
    _killed_after_rename(sql)
    sql.tables["acore_characters"] = ["characters", MARKER]
    sql.marker_rows["acore_characters"] = "coa"
    write_crash(tmp_path, "hero", imported=False)
    with pytest.raises(coa_realm.RealmSwitchError, match="also parked"):
        switch_lines(tmp_path, coa(), "coa", sql)
    assert (tmp_path / coa_realm.SWITCH_MARKER_FILE).is_file()
    assert drops(sql) == [] and renames(sql) == []
    assert usernames(sql, "acore_auth_coa") == ["Coa1", "Player1"]


def test_a_stale_note_whose_other_park_is_of_another_type_is_kept(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql, marker="hero")
    parked_set(sql, "coa", marker="wc")
    write_file(tmp_path, "hero")
    write_crash(tmp_path, "hero", imported=False, source="coa")
    with pytest.raises(coa_realm.RealmSwitchError, match="parked coa databases do not read"):
        switch_lines(tmp_path, coa(), "hero", sql, file=None)
    assert (tmp_path / coa_realm.SWITCH_MARKER_FILE).is_file()


def test_the_switch_publishes_override_then_file_then_clears_the_note(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql)
    seen: list[tuple[str, bool]] = []

    def override(env: Mapping[str, str]) -> None:
        seen.append((file_type(tmp_path), (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()))
        assert dict(env) == coa_realm.switch_env_for(coa(), "hero")

    write_file(tmp_path, "coa")
    list(
        coa_realm.switch(
            coa(),
            tmp_path,
            "hero",
            sql=sql,
            servers_running=lambda: False,
            run_import=lambda: fresh_import(sql),
            set_realm_row=lambda name, port: None,
            write_override=override,
            bot_prefix="rndbot",
        )
    )
    assert seen == [("coa", True)], "the override first, while the old file and the note stand"
    assert file_type(tmp_path) == "hero"
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def _killed_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sql: FakeSql, step: str
) -> list[Mapping[str, str]]:
    """A second switch coa -> hero killed right after `step` of its publication."""
    coa_schemas(sql)
    parked_set(sql, "hero")
    real = {"write_active": coa_realm.write_active, "_clear_crash": coa_realm._clear_crash}
    wrote: list[Mapping[str, str]] = []

    def die(*_args: object) -> None:
        raise OSError("killed")

    if step == "override":
        monkeypatch.setattr(coa_realm, "write_active", die)
    elif step == "active":
        monkeypatch.setattr(coa_realm, "_clear_crash", die)
    write_file(tmp_path, "coa")
    with pytest.raises(OSError, match="killed"):
        list(
            coa_realm.switch(
                coa(),
                tmp_path,
                "hero",
                sql=sql,
                servers_running=lambda: False,
                run_import=lambda: fresh_import(sql),
                set_realm_row=lambda name, port: None,
                write_override=lambda env: wrote.append(dict(env)),
                bot_prefix="rndbot",
            )
        )
    for name, function in real.items():
        monkeypatch.setattr(coa_realm, name, function)
    return wrote


@pytest.mark.parametrize("step", ["override", "active"])
def test_a_switch_killed_while_publishing_is_finished_by_the_next_press(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    sql = FakeSql()
    wrote = _killed_publishing(tmp_path, monkeypatch, sql, step)
    assert wrote == [coa_realm.switch_env_for(coa(), "hero")]
    assert (tmp_path / coa_realm.SWITCH_MARKER_FILE).is_file()
    assert coa_realm.refusal(coa(), tmp_path, sql) == coa_realm.CRASH_START_REFUSAL
    assert file_type(tmp_path) == ("coa" if step == "override" else "hero")
    lines = switch_lines(tmp_path, coa(), "hero", sql, file=None)
    assert lines[-1] == f"OVERRIDE {sorted(coa_realm.switch_env_for(coa(), 'hero'))}"
    assert file_type(tmp_path) == "hero"
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()
    assert len(renames(sql)) == 1, "finished, never switched twice"
    assert coa_realm.refusal(coa(), tmp_path, sql) is None


def test_the_repair_rewrites_the_override(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql, marker="hero")
    lines = switch_lines(tmp_path, coa(), "hero", sql)
    assert lines[-1] == f"OVERRIDE {sorted(coa_realm.switch_env_for(coa(), 'hero'))}"


def test_a_switch_rewrites_the_override_for_its_target(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql)
    lines = switch_lines(tmp_path, coa(), "wc", sql)
    assert lines[-1] == f"OVERRIDE {sorted(coa_realm.switch_env_for(coa(), 'wc'))}"
    assert "AC_CO_ACHALLENGES_GAME_MODES_REALM" in coa_realm.switch_env_for(coa(), "wc")


def test_a_second_switch_drops_the_emptied_target_park(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql)
    parked_set(sql, "hero")
    switch_lines(tmp_path, coa(), "hero", sql)
    assert not any(schema.endswith("_hero") for schema in sql.tables), sql.tables


def test_the_gate_refuses_any_unreadable_database_file_or_no_file(tmp_path: Path) -> None:
    """Codex round 3: no realm-type file is no licence to start on a database nobody read."""

    def refuse() -> None:
        raise RuntimeError("no database")

    sql = FakeSql()
    coa_schemas(sql, marker="hero")  # would disagree with the default
    gate = coa_realm.before_servers_gate(coa(), tmp_path, start_db=refuse, sql=sql)
    assert gate is not None and gate() == coa_realm.UNREADABLE_START_REFUSAL
    sql.fail_query_on = "information_schema.TABLES"
    gate = coa_realm.before_servers_gate(coa(), tmp_path, start_db=lambda: None, sql=sql)
    assert gate is not None and gate() == coa_realm.UNREADABLE_START_REFUSAL
    sql.fail_query_on = ""
    assert gate() == coa_realm.mismatch_sentence("hero", "coa")


def test_the_server_tab_gate_writes_the_marker_through_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sql = FakeSql()
    coa_schemas(sql, marker=None)
    services = _wired_controller(monkeypatch, tmp_path, sql)
    assert services.controller.before_servers() is None  # type: ignore[attr-defined]
    assert sql.marker == "coa"


def test_the_engine_gate_without_secrets_refuses_once_a_switch_was_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine()

    def no_secrets(server_dir: Path) -> object:
        raise OSError("no .env")

    monkeypatch.setattr(engine, "resolve_secrets", no_secrets)
    assert engine.family_start_refusal(tmp_path) is None, "no file, no secrets: the default"
    write_file(tmp_path, "coa")
    assert engine.family_start_refusal(tmp_path) == coa_realm.UNREADABLE_START_REFUSAL


# -- reads that cannot be made refuse ------------------------------------------------


def test_recovery_refuses_an_unreadable_plain_marker(tmp_path: Path) -> None:
    sql = FakeSql()
    _killed_during_import(sql)
    sql.tables["acore_characters"].append(MARKER)
    sql.fail_query_on = f"SELECT id FROM `acore_characters`.`{MARKER}`"
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=True)
    with pytest.raises(coa_realm.DatabaseUnreadable):
        switch_lines(tmp_path, coa(), "hero", sql, file=None)
    assert drops(sql) == [] and renames(sql) == []


def test_recovery_refuses_an_unreadable_park_marker(tmp_path: Path) -> None:
    sql = FakeSql()
    _killed_during_import(sql)
    sql.fail_query_on = f"SELECT id FROM `acore_characters_coa`.`{MARKER}`"
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=True)
    with pytest.raises(coa_realm.DatabaseUnreadable):
        switch_lines(tmp_path, coa(), "hero", sql, file=None)
    assert drops(sql) == [] and renames(sql) == []


def test_recovery_refuses_unreadable_plain_schemas(tmp_path: Path) -> None:
    sql = FakeSql()
    _killed_during_import(sql)
    sql.fail_query_on = "TABLE_SCHEMA = 'acore_auth'"
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=False)
    with pytest.raises(coa_realm.DatabaseUnreadable):
        switch_lines(tmp_path, coa(), "coa", sql, file=None)
    assert drops(sql) == [] and renames(sql) == []


def test_a_recovery_that_put_the_set_back_clears_its_note_even_if_the_switch_then_fails(
    tmp_path: Path,
) -> None:
    sql = FakeSql()
    _killed_during_import(sql)
    sql.fail_on = "CREATE DATABASE"
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=True)
    with pytest.raises(RuntimeError, match="CREATE DATABASE"):
        switch_lines(tmp_path, coa(), "hero", sql, file=None)
    assert sql.marker == "coa"
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()
    assert coa_realm.refusal(coa(), tmp_path, sql) is None


def test_an_undo_drops_the_emptied_parks(tmp_path: Path) -> None:
    sql = FakeSql()
    _killed_during_import(sql)
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=True)
    switch_lines(tmp_path, coa(), "coa", sql, file=None)
    assert not any(schema.endswith("_coa") for schema in sql.tables), sql.tables


def test_an_unreadable_target_park_marker_refuses_before_any_rename(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql)
    parked_set(sql, "hero")
    sql.fail_query_on = f"SELECT id FROM `acore_characters_hero`.`{MARKER}`"
    with pytest.raises(coa_realm.DatabaseUnreadable):
        switch_lines(tmp_path, coa(), "hero", sql)
    assert renames(sql) == []


def test_verify_refuses_an_unreadable_plain_marker(tmp_path: Path) -> None:
    sql = AfterRename(
        lambda s: setattr(s, "fail_query_on", f"SELECT id FROM `acore_characters`.`{MARKER}`")
    )
    _second_switch(sql)
    with pytest.raises(coa_realm.DatabaseUnreadable):
        switch_lines(tmp_path, coa(), "hero", sql)
    assert file_type(tmp_path) == "coa"
    assert (tmp_path / coa_realm.SWITCH_MARKER_FILE).is_file()


class Deaf(FakeSql):
    """Answers every `INSERT IGNORE` (the marker) with success and writes nothing."""

    def execute(self, statement: str) -> None:
        if statement.startswith("INSERT IGNORE"):
            self.executed.append(statement)
            return
        super().execute(statement)


def test_a_pre_marker_mark_that_does_not_read_back_refuses_the_switch(tmp_path: Path) -> None:
    sql = Deaf()
    coa_schemas(sql, marker=None)
    with pytest.raises(coa_realm.RealmSwitchError, match="could not be marked"):
        switch_lines(tmp_path, coa(), "hero", sql)
    assert renames(sql) == []


def test_an_unreadable_second_listing_at_start_refuses(tmp_path: Path) -> None:
    class SecondFails(FakeSql):
        listed = 0

        def query(self, statement: str) -> str:
            if "TABLE_SCHEMA = 'acore_characters'" in statement:
                self.listed += 1
                if self.listed == 2:
                    raise RuntimeError("gone")
            return super().query(statement)

    sql = SecondFails()
    coa_schemas(sql, marker=None)
    write_file(tmp_path, "coa")
    assert coa_realm.refusal(coa(), tmp_path, sql) == coa_realm.UNREADABLE_START_REFUSAL
    assert sql.executed == []


# -- the account checks ------------------------------------------------------------


class Answering(FakeSql):
    """Answers the queries containing a key with a fixed text instead of the model."""

    def __init__(self, answers: Mapping[str, str]) -> None:
        super().__init__()
        self.answers = dict(answers)

    def query(self, statement: str) -> str:
        for key, answer in self.answers.items():
            if key in statement:
                self.queries.append(statement)
                return answer
        return super().query(statement)


def test_an_account_gone_from_its_source_refuses(tmp_path: Path) -> None:
    sql = Answering({"FROM `acore_auth_coa`.account WHERE username = 'Player1'": ""})
    coa_schemas(sql)
    with pytest.raises(coa_realm.RealmSwitchError, match="Player1 account is gone"):
        switch_lines(tmp_path, coa(), "hero", sql)
    assert sql.marker is None


def test_an_account_that_matches_twice_refuses(tmp_path: Path) -> None:
    sql = Answering({"JOIN `acore_auth_coa`.account s": "2\n"})
    coa_schemas(sql)
    with pytest.raises(coa_realm.RealmSwitchError, match="password intact"):
        switch_lines(tmp_path, coa(), "hero", sql)
    assert sql.marker is None


def test_an_id_that_reads_as_no_number_is_unreadable(tmp_path: Path) -> None:
    sql = Answering({"FROM `acore_auth`.account WHERE username = 'Player1'": "NULL\n"})
    coa_schemas(sql)
    with pytest.raises(coa_realm.DatabaseUnreadable, match="Player1 account reads"):
        switch_lines(tmp_path, coa(), "hero", sql)
    assert usernames(sql, "acore_auth") == []
