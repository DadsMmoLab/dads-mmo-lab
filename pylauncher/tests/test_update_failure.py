"""`yulon.update_failure`: the plain sentence for a world that stopped at a failed update (T600).

The log shapes are the measured ones (`tortoise-reimport-rehearsal-m910q-2026-09-08/
rehearsal.log:126-130`, plus `AutoUpdater.cpp:285-287` at the pin for the `Attempting`
line, which names the module).
"""

from __future__ import annotations

from yulon import update_failure

HASH = "34F86966897E9206E13773D73C2232677DA2FFED"
CORE_FAILURE = (
    "[DB Auto-Updater] Attempting to execute update 20260903063722_world, hash " + HASH + ".\n"
    "[0 ms] SQL:       UPDATE `spell_template` SET `script_name` = 'x' WHERE `entry` = 44070;\n"
    "SQL:   INSERT INTO `spell_proc_event` (`entry`) VALUES (44070);\n"
    "[1062] Duplicate entry '44070' for key 'PRIMARY'\n"
    "[DB Auto-Updater] Migration 20260903063722_world with hash " + HASH + " failed to apply.\n"
)
MODULE_FAILURE = (
    "[DB Auto-Updater] Attempting to execute update 20261007120000_char for module "
    "TortoiseBots, hash " + HASH + ".\n"
    "[1091] Can't DROP INDEX `idx`; check that column/key exists\n"
    "[DB Auto-Updater] Migration 20261007120000_char with hash " + HASH + " failed to apply.\n"
)


def test_a_core_update_names_its_file_and_what_mariadb_said() -> None:
    said = update_failure.explain(CORE_FAILURE)
    assert "20260903063722_world.sql" in said
    assert "[1062] Duplicate entry '44070' for key 'PRIMARY'" in said
    assert "core" in said
    assert "stopped at a database update it could not apply" in said


def test_a_module_update_names_the_module() -> None:
    said = update_failure.explain(MODULE_FAILURE)
    assert "20261007120000_char.sql" in said and "TortoiseBots" in said
    assert "[1091] Can't DROP INDEX" in said


def test_the_error_is_the_one_from_this_update_and_not_an_earlier_one() -> None:
    log = (
        "[DB Auto-Updater] Attempting to execute update 20260101000000_world, hash AA.\n"
        "[1062] Duplicate entry 'old' for key 'PRIMARY'\n" + CORE_FAILURE.replace(
            "[1062] Duplicate entry '44070' for key 'PRIMARY'\n", ""
        )
    )
    said = update_failure.explain(log)
    assert "Duplicate entry 'old'" not in said
    assert "20260903063722_world.sql" in said


def test_without_the_error_line_it_still_names_the_file_and_points_at_the_log() -> None:
    said = update_failure.explain(
        "[DB Auto-Updater] Migration 20260903063722_world with hash " + HASH + " failed to apply.\n"
    )
    assert "20260903063722_world.sql" in said and "world log" in said


def test_a_log_with_only_the_closing_line_still_says_an_update_failed() -> None:
    said = update_failure.explain("DB AutoUpdater FAILED, cancelling server.\n")
    assert "database update" in said and "world log" in said


def test_a_healthy_start_says_nothing() -> None:
    log = (
        "[DB Auto-Updater] Found 5 possible migrations for character.\n"
        "[DB Auto-Updater] Migration 20260918120000_world with hash 0123 for module TortoiseBots "
        "exists in DB but not as file, old migration?\n"
        "[DB Auto-Updater] Migration with hash 0123 was migrated with name a but now has name b.\n"
        "World server is up and running!\n"
    )
    assert update_failure.explain(log) == ""
    assert update_failure.explain("") == ""


def test_the_sentence_never_promises_that_nothing_changed() -> None:
    """DDL commits implicitly in MariaDB, so a failed file can leave its ALTERs applied."""
    said = update_failure.explain(CORE_FAILURE)
    assert "nothing changed" not in said.lower() and "rolled back" not in said.lower()
