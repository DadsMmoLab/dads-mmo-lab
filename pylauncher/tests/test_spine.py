"""Tests for the game-free install spine (`yulon.catalog.native.StagedInstaller`, roadmap 7.1).

What is asserted here is true of EVERY family: the state file and its hint
semantics, the guard, ask-forwarding, streaming, and what a stage tuple may
and may not contain. Anything AzerothCore-shaped lives in
`test_families_azerothcore.py`. The machine double is shared:
`tests/support_native.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

from yulon.catalog import native

ORDER = ("clone-sources", "build", "import", "up")


# -- the state file ---------------------------------------------------------


def test_the_state_file_round_trips_the_family(tmp_path: Path) -> None:
    """`family` is the 7.1 key: a folder installed as one family is never read as another."""
    native.write_state(
        tmp_path,
        native.InstallState(
            game_id="wow-wotlk", install_id="abcd1234", family="azerothcore", completed=("build",)
        ),
    )
    state = native.read_state(tmp_path, valid=ORDER)
    assert state is not None
    assert state.family == "azerothcore"
    assert state.completed == ("build",)
    assert state.game_id == "wow-wotlk"
    assert json.loads((tmp_path / native.STATE_FILE).read_text(encoding="utf-8"))["family"] == (
        "azerothcore"
    )


def test_a_state_file_written_before_family_existed_reads_as_an_empty_family(
    tmp_path: Path,
) -> None:
    """`version` stays 1: the key is additive, and an old file is not a refusal."""
    (tmp_path / native.STATE_FILE).write_text(
        json.dumps(
            {"version": 1, "game_id": "wow-wotlk", "install_id": "abc", "completed": ["build"]}
        ),
        encoding="utf-8",
    )
    state = native.read_state(tmp_path, valid=ORDER)
    assert state is not None
    assert state.family == ""
    assert state.version == 1


def test_a_family_that_is_not_a_string_reads_as_an_empty_family(tmp_path: Path) -> None:
    """A junk `family` must degrade to "unknown", never to a family name.

    `""` is the one value that means "this file does not say", and the guard is
    what turns that into "trust the entry". A non-string that survived as, say,
    `None` would type-lie to every reader downstream.
    """
    (tmp_path / native.STATE_FILE).write_text(
        json.dumps({"version": 1, "game_id": "wow-wotlk", "install_id": "abc", "family": 7}),
        encoding="utf-8",
    )
    state = native.read_state(tmp_path, valid=ORDER)
    assert state is not None
    assert state.family == ""


def test_read_state_keeps_only_the_stage_names_the_entry_has(tmp_path: Path) -> None:
    """Per-entry validation replaces the global `STAGE_ORDER` filter (7.1).

    A file naming a stage this entry does not have must not become a skip —
    and the entry, not a module constant, is what says which names exist.
    """
    (tmp_path / native.STATE_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "game_id": "wow-tbc",
                "install_id": "abc",
                "completed": ["build", "clone-core", "invent-a-stage"],
            }
        ),
        encoding="utf-8",
    )
    state = native.read_state(tmp_path, valid=ORDER)
    assert state is not None
    assert state.completed == ("build",)


def test_the_same_file_reads_differently_for_two_entries_stage_tuples(tmp_path: Path) -> None:
    """`valid` is per entry, so "known stage" is not a property of the module.

    The rejection above must be the `valid` argument doing the work and not a
    surviving module-level filter: `clone-core` is a real AzerothCore stage
    name, and it is dropped only because the tuple passed in has no such name.
    """
    (tmp_path / native.STATE_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "game_id": "wow-tbc",
                "install_id": "abc",
                "completed": ["build", "clone-core"],
            }
        ),
        encoding="utf-8",
    )
    narrow = native.read_state(tmp_path, valid=ORDER)
    wide = native.read_state(tmp_path, valid=("clone-core", "build"))
    assert narrow is not None and wide is not None
    assert narrow.completed == ("build",)
    # `valid` filters; it does not sort. The file's own order is kept, because
    # `with_stage` is what wrote that order and is what normalises it again.
    assert wide.completed == ("build", "clone-core")


def test_with_stage_orders_by_the_entry_tuple_and_never_records_twice() -> None:
    fresh = native.InstallState(game_id="wow-tbc", install_id="abc", family="cmangos")
    once = fresh.with_stage("import", ORDER).with_stage("clone-sources", ORDER)
    assert once.completed == ("clone-sources", "import")
    assert once.with_stage("import", ORDER) is once
    # A name outside the entry's tuple is dropped, the rule `read_state()` applies too.
    assert once.with_stage("invent-a-stage", ORDER).completed == ("clone-sources", "import")
    assert once.with_stage("build", ORDER).last_error == ""


def test_with_stage_keeps_the_family_it_was_given() -> None:
    """Recording progress must not quietly drop the ownership claim it carries."""
    state = native.InstallState(game_id="wow-tbc", install_id="abc", family="cmangos")
    assert state.with_stage("build", ORDER).family == "cmangos"
