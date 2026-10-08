"""T555 T5: what the Server tab says about a module's health (`yulon.module_health`).

The sentence is pure, so each row of the plan's table is asserted on its text. Nothing here
starts a widget or a container: the database and the log are fakes.
"""

from __future__ import annotations

import copy
import json
import re

import pytest

from yulon import docker, module_health
from yulon.catalog.catalog import CATALOG_FILE, AzerothCoreData, load_catalog, parse_catalog
from yulon.module_health import Switch

UNBOUND = load_catalog().get("wow-unbound")
BLOCK = UNBOUND.install.native.azerothcore  # type: ignore[union-attr]
HEALTH = BLOCK.health
CHECKS = BLOCK.sql_checks
SCHEMAS = UNBOUND.databases.schema_map()
MARKERS = (
    "[UNBOUND] free reagents: off\n[UNBOUND] instant summons: off\n[UNBOUND] Prereq map built.\n"
)
OFF = (
    Switch("free reagents", running=False, conf=False),
    Switch("instant summons", running=False, conf=False),
    Switch("#buffs", running=False, conf=False),
)


class _Db:
    """`apply.DockerSql.query` over a fake world database: which tables it has and their counts."""

    def __init__(
        self,
        *,
        missing: tuple[str, ...] = (),
        counts: dict[str, int] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.tables = [c.table for c in CHECKS if c.table not in missing]
        self.counts = {c.table: c.at_least for c in CHECKS} | (counts or {})
        self.error = error
        self.asked: list[tuple[str, str]] = []

    def query(self, db: str, statement: str) -> str:
        self.asked.append((db, statement))
        if self.error is not None:
            raise self.error
        if "information_schema.tables" in statement:
            return "".join(f"acore_world\t{table}\n" for table in sorted(set(self.tables)))
        table = re.search(r"FROM `\w+`\.`(\w+)`", statement)
        assert table is not None, statement
        if table.group(1) not in self.tables:
            raise RuntimeError("ERROR 1146 (42S02): Table doesn't exist")
        return f"{self.counts[table.group(1)]}\n"


def _line(
    db: _Db | None = None,
    log: str = MARKERS,
    switches: tuple[Switch, ...] = OFF,
) -> str:
    assert HEALTH is not None
    got = module_health.reading(HEALTH, CHECKS, SCHEMAS, db or _Db(), log, switches)
    return module_health.sentence(got)


# -- the catalog's block ---------------------------------------------------------------


def test_the_unbound_entry_carries_its_health_block_and_wotlk_has_none() -> None:
    assert HEALTH is not None
    assert HEALTH.name == "Unbound"
    assert HEALTH.log_markers == (
        "[UNBOUND] Prereq map built.",
        "[UNBOUND] free reagents:",
        "[UNBOUND] instant summons:",
    )
    assert (HEALTH.count_label, HEALTH.count_table) == ("Mentor", "creature")
    assert load_catalog().get("wow-wotlk").install.native.azerothcore.health is None  # type: ignore[union-attr]


def _with_health(**health: object) -> dict:  # type: ignore[type-arg]
    data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    game = next(g for g in data["games"] if g["id"] == "wow-unbound")
    block = copy.deepcopy(game["install"]["native"]["azerothcore"])
    block["health"] = {"name": "Unbound", "log_markers": ["[UNBOUND] x"], **health}
    return block


def test_a_counted_table_must_be_one_the_install_checks() -> None:
    AzerothCoreData.model_validate(_with_health(count_label="Mentor", count_table="creature"))
    for bad in (
        {"count_label": "Mentor", "count_table": "not_a_checked_table"},
        {"count_label": "Mentor"},
        {"count_table": "creature"},
    ):
        with pytest.raises(ValueError, match="count"):
            AzerothCoreData.model_validate(_with_health(**bad))


def test_a_health_block_needs_a_marker_to_look_for() -> None:
    with pytest.raises(ValueError):
        AzerothCoreData.model_validate(_with_health(log_markers=[]))


def test_the_catalog_still_loads_with_the_block() -> None:
    data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    assert parse_catalog(data).get("wow-unbound").install.native is not None


# -- the sentence, one row of the table each ---------------------------------------------


def test_all_good_names_the_mentors_and_every_switch() -> None:
    assert _line() == (
        "Unbound loaded: Mentor in 9 places; free reagents off, instant summons off, #buffs off"
    )


def test_a_switch_changed_but_not_restarted_says_when_it_lands() -> None:
    switches = (
        Switch("free reagents", running=False, conf=True),
        Switch("instant summons", running=True, conf=False),
        Switch("#buffs", running=True, conf=True),
    )
    assert _line(switches=switches).endswith(
        "free reagents off (on at the next start), "
        "instant summons on (off at the next start), #buffs on"
    )


def test_a_switch_the_log_never_said_is_not_said_and_never_off() -> None:
    switches = (Switch("free reagents", running=None, conf=False),) + OFF[1:]
    text = _line(switches=switches)
    assert "free reagents not said" in text and "free reagents off" not in text
    said_on = (Switch("free reagents", running=None, conf=True),) + OFF[1:]
    assert "free reagents not said (the settings file says on)" in _line(switches=said_on)


def test_tables_missing_names_them_and_the_press_and_counts_nothing() -> None:
    text = _line(_Db(missing=("unbound_milestones",)))
    assert text.startswith("Unbound tables missing: unbound_milestones.")
    assert "Rebuild the server" in text
    assert not any(ch.isdigit() for ch in text), "a missing table must not read as a count"


def test_a_short_table_says_its_real_count() -> None:
    text = _line(_Db(counts={"unbound_class_catalog": 12}))
    assert text == (
        "Unbound data incomplete: unbound_class_catalog has 12 rows, at least 1000 expected"
    )


def test_a_short_mentor_count_says_how_many_of_how_many() -> None:
    assert (
        _line(_Db(counts={"creature": 7}))
        == "Unbound loaded, but the Mentor stands in 7 of 9 places"
    )


# What e8022b44 prints on the very first start of a fresh install: the characters table is
# empty, so UnboundSystem.cpp:270 skips the orphan sweep and PresentGuidKeyedTables() (the
# "Character cleanup covers" line, :89-101) is never called.
FIRST_START_LOG = (
    "[UNBOUND] free reagents: off\n"
    "[UNBOUND] instant summons: off\n"
    "[UNBOUND] Orphan sweep skipped: the characters table is empty, so every Unbound row "
    "would look orphaned.\n"
    "[UNBOUND] Prereq map built.\n"
    "[dml_autobuff] off (Unbound.AutoBuff = 0)\n"
    "AzerothCore rev. 1 ready...\n"
)


def test_a_first_start_with_an_empty_characters_table_still_reads_loaded() -> None:
    """Cold review: the cleanup line is not printed on every start, so it is no proof of load."""
    assert _line(log=FIRST_START_LOG, switches=OFF).startswith("Unbound loaded:")


@pytest.mark.parametrize("on", [False, True])
def test_the_markers_are_lines_the_module_prints_at_every_start(on: bool) -> None:
    """Each marker is in a log whether the switches are on or off, and none is the cleanup line."""
    log = FIRST_START_LOG
    if on:
        log = log.replace(
            "free reagents: off", "free reagents: on (stripped casting reagents from 41 spells)"
        )
        log = log.replace(
            "instant summons: off", "instant summons: on (12 summon spells now instant)"
        )
    assert all(marker in log for marker in HEALTH.log_markers)
    assert not any("cleanup" in marker.lower() for marker in HEALTH.log_markers)


def test_a_marker_missing_from_this_runs_log_says_unbound_did_not_load() -> None:
    text = _line(log="[UNBOUND] free reagents: off\n[UNBOUND] instant summons: off\nready...\n")
    assert text == (
        'Unbound did not load: the world log has no "[UNBOUND] Prereq map built." line '
        "this run. Open the console log"
    )


def test_tables_missing_is_said_before_a_missing_marker() -> None:
    """Missing tables are why the module did not load; the cause comes first."""
    text = _line(_Db(missing=("unbound_milestones",)), log="ready...\n")
    assert text.startswith("Unbound tables missing: unbound_milestones.")


def test_a_database_that_cannot_answer_is_could_not_be_checked_with_no_number() -> None:
    text = _line(_Db(error=RuntimeError("connection refused")))
    assert text == (
        "Unbound could not be checked: "
        "the database did not say which tables it has (connection refused)"
    )
    assert not any(ch.isdigit() for ch in text)


def test_an_unreadable_log_is_could_not_be_checked_not_did_not_load() -> None:
    text = _line(log="")
    assert text.startswith("Unbound could not be checked:") and "log" in text
    assert "did not load" not in text


def test_a_count_the_database_garbles_is_could_not_be_checked() -> None:
    class Garbled(_Db):
        def query(self, db: str, statement: str) -> str:
            if "information_schema" in statement:
                return super().query(db, statement)
            return "lots\n"

    text = _line(Garbled())
    assert text.startswith("Unbound could not be checked:") and "Unbound loaded" not in text


def test_a_database_error_never_escapes_as_an_exception() -> None:
    for error in (
        docker.DockerCommandError("docker said no"),
        OSError("pipe broke"),
        RuntimeError("anything"),
    ):
        assert _line(_Db(error=error)).startswith("Unbound could not be checked:")


def test_a_block_with_no_counted_table_and_no_switches_is_just_loaded() -> None:
    assert HEALTH is not None
    plain = HEALTH.model_copy(update={"count_label": "", "count_table": ""})
    got = module_health.reading(plain, CHECKS, SCHEMAS, _Db(), MARKERS, ())
    assert module_health.sentence(got) == "Unbound loaded"


def test_an_unreadable_log_asks_the_database_nothing() -> None:
    db = _Db()
    assert _line(db, log="").startswith("Unbound could not be checked:")
    assert db.asked == []


def test_an_unreadable_settings_file_is_said_and_never_read_as_off() -> None:
    switches = (
        Switch("free reagents", running=True, conf=None),
        Switch("instant summons", running=None, conf=None),
    )
    assert _line(switches=switches).endswith(
        "free reagents on (the settings file could not be read), "
        "instant summons not said (the settings file could not be read)"
    )
