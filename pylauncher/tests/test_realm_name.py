"""T808: the realm name players see in the game's realm list.

No docker and no host reads: the SQL seam is a recording double and the
"server folder" is `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import realm_name
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.realm_name import RealmNameError, RealmNames

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
TORTOISE = CATALOG.get("wow-tortoise")
TBC = CATALOG.get("wow-tbc")


class FakeSql:
    """A realm row that remembers the name it was given, in the shape the real seam answers."""

    def __init__(self, name: str = "Seeded Realm", *, down: str = "", takes: bool = True) -> None:
        self.name = name
        self.down = down
        self.takes = takes
        self.statements: list[str] = []
        self.queries: list[str] = []

    def run_statement(self, db: str, statement: str) -> None:
        assert db == "auth"
        if self.down:
            raise RuntimeError(self.down)
        self.statements.append(statement)
        if self.takes:
            hex_part = statement.split("X'")[1].split("'")[0]
            self.name = bytes.fromhex(hex_part).decode("utf-8")

    def query(self, db: str, statement: str) -> str:
        assert db == "auth"
        if self.down:
            raise RuntimeError(self.down)
        self.queries.append(statement)
        return self.name.encode("utf-8").hex().upper() + "\n"


# -- validation ----------------------------------------------------------


def test_a_name_of_one_to_thirty_two_characters_is_accepted() -> None:
    assert realm_name.check("A") == "A"
    assert realm_name.check("x" * 32) == "x" * 32


def test_surrounding_blanks_are_removed_before_the_name_is_used() -> None:
    assert realm_name.check("  Jerra Realm \t") == "Jerra Realm"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_an_empty_name_is_refused(blank: str) -> None:
    with pytest.raises(RealmNameError, match="empty"):
        realm_name.check(blank)


def test_a_name_over_thirty_two_characters_is_refused_and_the_count_is_in_characters() -> None:
    with pytest.raises(RealmNameError, match="at most 32"):
        realm_name.check("x" * 33)
    # 32 multi-byte characters are 32 characters (and 64 bytes): accepted.
    assert realm_name.check("é" * 32) == "é" * 32


@pytest.mark.parametrize("bad", ["a\nb", "a\tb", "a\x00b", "a\x7fb", "a\x1bb", "a​b"])
def test_control_and_invisible_characters_are_refused(bad: str) -> None:
    with pytest.raises(RealmNameError, match="control or invisible"):
        realm_name.check(bad)


def test_a_character_outside_the_three_byte_range_is_refused() -> None:
    with pytest.raises(RealmNameError, match="basic multilingual plane"):
        realm_name.check("Realm \U0001f600")


@pytest.mark.parametrize("name", ["Jerra's Realm", "a\\b", "a;b", "x'; DROP TABLE realmlist; --"])
def test_sql_metacharacters_are_a_legal_name_because_they_never_reach_sql_as_text(
    name: str,
) -> None:
    cleaned = realm_name.check(name[:32])
    assert cleaned == name[:32]


# -- the statement -------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["O'Brien", "back\\slash", "semi;colon", "dash -- dash", "x' OR '1'='1", "Zürich 世界", "%_"],
)
def test_the_update_carries_the_name_as_hex_and_never_as_text(name: str) -> None:
    statement = realm_name.name_sql(WOTLK, name)

    hex_part = statement.split("X'")[1].split("'")[0]
    assert bytes.fromhex(hex_part).decode("utf-8") == name
    body = statement.replace(hex_part, "")
    # Nothing a person typed is left in the statement outside the hex blob.
    for piece in ("OBrien", "slash", "colon", "OR", "Zürich", "世"):
        assert piece not in body
    assert body.count("'") == 2, "a quote reached the statement outside the hex literal"
    assert "\\" not in body and ";" not in body[:-1] and "--" not in body


def test_the_update_is_restricted_to_the_one_realm_row_of_the_auth_database() -> None:
    for entry in (WOTLK, TORTOISE, TBC):
        statement = realm_name.name_sql(entry, "Hello")
        rl = entry.realmlist
        assert statement.startswith(
            f"UPDATE {entry.databases.auth}.{rl.table} SET name=_utf8mb4 X'"
        ), statement
        assert statement.endswith(f" WHERE id={rl.realm_id};"), statement
        assert statement.count("UPDATE") == 1 and statement.count(";") == 1


def test_a_refused_name_never_becomes_a_statement() -> None:
    for bad in ("", "x" * 33, "a\nb"):
        with pytest.raises(RealmNameError):
            realm_name.name_sql(WOTLK, bad)


def test_the_read_back_asks_for_the_same_row_in_hex() -> None:
    query = realm_name.name_query(TORTOISE)
    assert query == (
        f"SELECT HEX(CONVERT(name USING utf8mb4)) FROM {TORTOISE.databases.auth}."
        f"{TORTOISE.realmlist.table} WHERE id={TORTOISE.realmlist.realm_id};"
    )


def test_an_answer_is_read_only_when_it_is_exactly_one_row_of_hex() -> None:
    assert realm_name.name_from_answer("4A65727261") == "Jerra"
    assert realm_name.name_from_answer("") is None
    assert realm_name.name_from_answer("41\n42\n") is None
    assert realm_name.name_from_answer("not hex") is None


# -- the saved choice ----------------------------------------------------


def test_a_saved_name_is_read_back_exactly(tmp_path: Path) -> None:
    assert realm_name.record_name(tmp_path, "Zürich 世界 's") == ""
    assert realm_name.read_saved_name(tmp_path) == "Zürich 世界 's"


def test_nothing_saved_reads_as_none(tmp_path: Path) -> None:
    assert realm_name.read_saved_name(tmp_path) is None
    assert realm_name.read_saved_name(tmp_path / "missing") is None


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", '{"name": 5}', '{"name": ""}', '{"name": "' + "x" * 40 + '"}', "{}"],
)
def test_a_damaged_record_reads_as_no_name_and_is_never_sent_to_the_database(
    tmp_path: Path, content: str
) -> None:
    (tmp_path / realm_name.REALM_NAME_FILE).write_text(content, encoding="utf-8")
    assert realm_name.read_saved_name(tmp_path) is None


def test_recording_a_refused_name_writes_nothing(tmp_path: Path) -> None:
    with pytest.raises(RealmNameError):
        realm_name.record_name(tmp_path, "x" * 33)
    assert not (tmp_path / realm_name.REALM_NAME_FILE).exists()


def test_the_record_is_one_of_the_files_this_app_owns() -> None:
    from yulon.catalog import native

    assert realm_name.REALM_NAME_FILE in native.OUR_OWN_FILES


# -- save and current ----------------------------------------------------


def test_save_with_the_database_up_updates_the_row_remembers_it_and_says_so(
    tmp_path: Path,
) -> None:
    sql = FakeSql()
    result = RealmNames(WOTLK, tmp_path, sql).save("  New Name ")

    assert sql.statements == [realm_name.name_sql(WOTLK, "New Name")]
    assert realm_name.read_saved_name(tmp_path) == "New Name"
    assert result.ok and result.applied and result.remembered
    assert "New Name" in result.text and "realm list" in result.text


def test_save_with_the_database_down_remembers_the_name_for_the_next_start(
    tmp_path: Path,
) -> None:
    sql = FakeSql(down="connection refused")
    result = RealmNames(WOTLK, tmp_path, sql).save("Later")

    assert sql.statements == []
    assert realm_name.read_saved_name(tmp_path) == "Later"
    assert result.ok and result.remembered and not result.applied
    assert "next time the server starts" in result.text


def test_save_of_a_refused_name_sends_nothing_and_writes_nothing(tmp_path: Path) -> None:
    sql = FakeSql()
    for bad in ("", "y" * 40, "a\nb"):
        result = RealmNames(WOTLK, tmp_path, sql).save(bad)
        assert not result.ok and not result.applied and not result.remembered
        assert result.text
    assert sql.statements == [] and sql.queries == []
    assert not (tmp_path / realm_name.REALM_NAME_FILE).exists()


def test_save_does_not_claim_success_when_the_row_did_not_take_the_name(tmp_path: Path) -> None:
    sql = FakeSql(takes=False)
    result = RealmNames(WOTLK, tmp_path, sql).save("Wanted")

    assert not result.applied
    assert "did not take" in result.text
    assert realm_name.read_saved_name(tmp_path) == "Wanted"


def test_save_says_when_the_choice_could_not_be_kept(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")
    sql = FakeSql()
    result = RealmNames(WOTLK, blocker, sql).save("Fine")

    assert result.applied and not result.remembered
    assert "could not be saved" in result.text


def test_current_prefers_the_database_then_the_saved_name_then_says_nothing(
    tmp_path: Path,
) -> None:
    up = RealmNames(WOTLK, tmp_path, FakeSql("From Database")).current()
    assert (up.shown, up.from_database) == ("From Database", True)

    realm_name.record_name(tmp_path, "Saved One")
    down = RealmNames(WOTLK, tmp_path, FakeSql(down="no")).current()
    assert (down.shown, down.from_database) == ("Saved One", False)
    assert "did not answer" in down.note

    empty = RealmNames(WOTLK, tmp_path / "other", FakeSql(down="no")).current()
    assert (empty.shown, empty.from_database) == ("", False)


def test_a_name_with_a_backslash_survives_a_round_trip_through_the_database(
    tmp_path: Path,
) -> None:
    sql = FakeSql()
    names = RealmNames(WOTLK, tmp_path, sql)
    assert names.save("a\\b'c").applied
    assert names.current().shown == "a\\b'c"


# -- the start step ------------------------------------------------------


def test_the_start_step_passes_on_the_refusal_before_it_and_does_nothing_else(
    tmp_path: Path,
) -> None:
    realm_name.record_name(tmp_path, "Saved")
    sql = FakeSql()
    up: list[str] = []
    step = realm_name.keep_at_start(
        WOTLK, tmp_path, sql, lambda: up.append("db"), lambda: "port refused"
    )

    assert step() == "port refused"
    assert sql.statements == [] and up == []


def test_the_start_step_with_nothing_saved_does_not_even_start_the_database(
    tmp_path: Path,
) -> None:
    sql = FakeSql()
    up: list[str] = []
    step = realm_name.keep_at_start(WOTLK, tmp_path, sql, lambda: up.append("db"), None)

    assert step() is None
    assert sql.statements == [] and up == []


def test_the_start_step_applies_the_saved_name_after_bringing_the_database_up(
    tmp_path: Path,
) -> None:
    realm_name.record_name(tmp_path, "Saved")
    sql = FakeSql()
    order: list[str] = []
    step = realm_name.keep_at_start(
        TORTOISE,
        tmp_path,
        sql,
        lambda: order.append("db"),
        lambda: order.append("previous"),
    )

    assert step() is None
    assert order == ["previous", "db"]
    assert sql.statements == [realm_name.name_sql(TORTOISE, "Saved")]


def test_the_start_step_never_refuses_a_start_over_a_name(tmp_path: Path) -> None:
    realm_name.record_name(tmp_path, "Saved")

    def broken() -> None:
        raise RuntimeError("docker is down")

    assert realm_name.keep_at_start(WOTLK, tmp_path, FakeSql(), broken, None)() is None
    step = realm_name.keep_at_start(WOTLK, tmp_path, FakeSql(down="x"), lambda: None, None)
    assert step() is None


def test_every_catalog_game_has_a_realm_row_the_name_can_be_written_to() -> None:
    games = [e for e in CATALOG.games if isinstance(e, CatalogEntry)]
    assert games, "the catalog has no WoW game to test"
    for entry in games:
        assert entry.realmlist.table
        assert realm_name.name_sql(entry, "X").startswith(
            f"UPDATE {entry.databases.auth}.{entry.realmlist.table} SET name="
        )
