"""Three facts about the Tortoise core, each measured on a running server.

Every one of these was wrong in `catalog.json` on 2026-09-03, and every one was
invisible to the whole test suite because each is a fact about a THIRD-PARTY
BINARY -- what it prints, where it looks, which file it reads. A unit test
cannot discover any of them; only running the thing can. What a unit test CAN
do is stop a measured answer from being edited back to a guess, and that is all
this file is for.

The gate that found them (7.6, `yulon-ubuntu`) is written up in
`pyplan/checklist.md`. The short version of each is on its own test.
"""

from __future__ import annotations

import re

import pytest

from yulon.catalog.catalog import load_catalog

TORTOISE = "wow-tortoise"


def _native(game: str = TORTOISE):
    entry = load_catalog().get(game)
    native = entry.install.native
    assert native is not None, f"{game} carries no install.native block"
    return native


def test_the_ready_marker_matches_what_this_core_actually_prints() -> None:
    """Tortoise announces itself with a line no other CMaNGOS entry uses.

    The marker was `World initialized|MaNGOS.*started up successfully|Ready to
    login`. Measured against a Tortoise worldserver that had finished booting
    and was serving its main loop: NONE of the three appears in its log, not
    once. What it prints is

        World server is up and running! Loading time: 0 minutes 32 seconds

    So a perfectly healthy Tortoise install waited out the full 1800-second
    timeout and then told the user "The server started but never reported
    ready." The install could not have passed on any machine.

    Its two siblings are not a guide here and that is the point: `wow-vanilla`
    and `wow-tbc` both use `Avg Diff:`, the world-tick line, and a `grep -c` for
    it over this core's whole log returns 0. Same family, same engine, different
    build, different words -- the same shape as MoveMapGen's exit code.
    """
    marker = _native().ready.world
    assert marker is not None
    banner = "World server is up and running! Loading time: 0 minutes 32 seconds"
    assert re.search(marker, banner), (
        f"the ready marker {marker!r} does not match the line this core prints when it is "
        f"ready ({banner!r}), so the install waits out its timeout on a healthy server"
    )
    assert not re.search(marker, "Avg Diff: 100ms"), (
        "matching the siblings' tick line would be a coincidence, not a reading: this core "
        "never prints it"
    )


def test_the_auto_updater_is_pointed_at_the_directory_that_holds_the_migrations() -> None:
    """One directory too high, and the updater says nothing when it finds nothing.

    The core applies its own SQL migrations from
    `Database.AutoUpdate.Path/<WorldUpdateName>` and records each in a
    `migrations` table. The catalog said `/opt/tortoise/sql/`, so it looked for
    `/opt/tortoise/sql/world`. The image has them at
    `/opt/tortoise/sql/database_updates/world` -- 125 files.

    `ProcessTargetUpdates` SKIPS a missing directory without an error, so the
    only symptom was the worldserver dying later, on its first query against a
    column a migration adds:

        SELECT DISTINCT(script_name) FROM spell_template
        [1054] Unknown column 'script_name' in 'SELECT'
        Your database structure is not up to date.

    With the path corrected the updater applied all 125 and the server reached
    its main loop. Asserted as the trailing path component rather than the whole
    string so a future image layout change is a deliberate edit here.
    """
    keys = _native().cmangos.conf.files["mangosd.conf"].keys  # type: ignore[union-attr]
    path = keys["Database.AutoUpdate.Path"].strip('"')
    assert path.rstrip("/").endswith("database_updates"), (
        f"Database.AutoUpdate.Path is {path!r}; the migrations live in a `database_updates` "
        "directory and the updater skips a missing one in silence"
    )


def test_tortoise_materialises_the_playerbot_conf_its_siblings_do() -> None:
    """The bots this build is named for were disabled by a file that was never written.

    `8176a2ec` compiled playerbots into the Tortoise image on the owner's
    decision. The conf table then materialised `mangosd.conf` and `realmd.conf`
    and not `aiplayerbot.conf`, though the image ships `aiplayerbot.conf.dist`
    beside the other two, so the server said:

        AI Playerbot is Disabled. No configuration file at
        /opt/tortoise/etc/aiplayerbot.conf

    Compiled in, shipped, and off. Checked against the siblings rather than
    against a literal list, because "Vanilla and TBC write this file and
    Tortoise does not" is the whole finding, and a fifth CMaNGOS game should
    inherit the question rather than repeat the omission.
    """
    wanted = "aiplayerbot.conf"
    for game in ("wow-vanilla", "wow-tbc"):
        assert wanted in _native(game).cmangos.conf.files, (  # type: ignore[union-attr]
            f"{game} no longer writes {wanted}; this test compares against it"
        )
    assert wanted in _native().cmangos.conf.files, (  # type: ignore[union-attr]
        f"wow-tortoise does not write {wanted}, so the playerbots compiled into its image "
        "never load"
    )


@pytest.mark.parametrize("game", ["wow-tortoise", "wow-vanilla", "wow-tbc"])
def test_every_cmangos_game_asks_for_the_bot_population_the_owner_set(game: str) -> None:
    """500, in whichever file each entry writes it.

    Not a Tortoise fact, but the assertion that stops the file above being added
    empty: a conf table can name `aiplayerbot.conf` and set nothing, and the
    server would start the default population instead of the owner's.
    """
    conf = _native(game).cmangos.conf.files["aiplayerbot.conf"]  # type: ignore[union-attr]
    assert conf.keys.get("AiPlayerbot.MinRandomBots") == "500"
    assert conf.keys.get("AiPlayerbot.MaxRandomBots") == "500"


def test_tortoise_imports_the_playerbot_sql_its_own_bots_query() -> None:
    """Compiled in, configured, and then dead on a missing table.

    Enabling `aiplayerbot.conf` moved the crash rather than removing it: the
    bots initialise, load their area levels, and then die on

        select id, name, class from ai_playerbot_weightscales
        [1146] Table 'tw_world.ai_playerbot_weightscales' doesn't exist

    Nothing had ever imported the module's SQL. Vanilla's plan has carried a
    `playerbots characters` and a `playerbots world` phase all along -- the
    second listing both `sql/world/*.sql` and `sql/world/classic/*.sql`,
    because the classic subfolder is where the tables the bots read actually
    live -- and Tortoise's plan had neither.

    This is the third time on this entry that a thing was shipped and then not
    switched on: the bots were compiled into the image, then the conf that
    loads them was not written, then the SQL they read was not imported. Each
    step revealed the next only by running the server.
    """
    from yulon.catalog.catalog import load_catalog

    sql = _native().cmangos.sql  # type: ignore[union-attr]
    phases = {phase.name: phase for phase in sql.phases}
    assert "playerbots world" in phases, "the module's world SQL is never imported"
    assert "playerbots characters" in phases, "the module's character SQL is never imported"

    patterns = phases["playerbots world"].files or ()
    assert any("classic" in pattern for pattern in patterns), (
        "the `classic` subfolder holds `ai_playerbot_weightscales` and the rest of the "
        f"tables the bots query on boot; the phase lists only {list(patterns)}"
    )

    vanilla = load_catalog().get("wow-vanilla").install.native
    assert vanilla is not None and vanilla.cmangos is not None
    sibling = {phase.name for phase in vanilla.cmangos.sql.phases}
    assert {
        "playerbots world",
        "playerbots characters",
    } <= sibling, "wow-vanilla no longer carries the phases this test compares against"


def test_the_import_is_verified_by_the_tables_the_bots_need() -> None:
    """A table COUNT is not a schema check, and that is how this got through.

    The only world-side verification was `COUNT(*) FROM information_schema.tables
    >= 150`. The database that crashed the server on boot had 285 tables, so it
    passed comfortably while missing every table the bots read and 125
    migrations besides. A count answers "did something get imported", never
    "did the right things".

    The `ai_playerbot%` count is the same check `wow-vanilla` has carried since
    its own import was fixed, and it fails on exactly the database this gate
    produced.
    """
    checks = _native().cmangos.sql.verify  # type: ignore[union-attr]
    bots = [c for c in checks if "ai_playerbot" in c.query]
    assert bots, "nothing verifies that the playerbot tables arrived"
    assert bots[0].min >= 10, f"a threshold of {bots[0].min} would pass on an empty import"


def test_the_ready_budget_covers_a_measured_first_boot_not_a_round_number() -> None:
    """1718 seconds of loading, and a budget that missed it by about two minutes.

    Tortoise's first boot builds `ai_playerbot_equip_cache` -- one row per
    class/spec/level/slot/quality/item -- and it settled at **1,334,079 rows**
    at roughly a thousand inserts a second. The worldserver then printed

        World server is up and running! Loading time: 28 minutes 38 seconds

    with `RestartCount=0`: nothing was wrong, it was simply slow. The install
    had already given up. The budget was 1800 s and the load took 1718 s, which
    sounds like it fits and does not: the `ready` stage's clock starts when the
    stage does, and the world container started 213 s later -- compose recreate,
    then the database health wait -- so the stage needed about 1931 s.

    The floor asserted here is the MEASUREMENT plus that gap, not the number
    that happens to be shipped. `1b88d49d` set the same precedent for TBC after
    a 793 s boot timed out at 600 s: a test that pins the shipped value only
    records what someone typed, and goes green on a value chosen carelessly.

    Its siblings stay at 1800 s and that is not an oversight -- Vanilla reached
    ready in about nine minutes with the same 500 bots, because its Bots module
    builds no such cache. This is a per-fork cost, like the exit code and the
    banner.
    """
    ready = _native().ready
    measured_load = 28 * 60 + 38
    container_start_gap = 213
    assert ready.timeout_s >= measured_load + container_start_gap, (
        f"the ready budget is {ready.timeout_s}s; a first boot measured {measured_load}s of "
        f"loading and the stage's clock starts ~{container_start_gap}s before the container's"
    )
    for game in ("wow-vanilla", "wow-tbc"):
        assert (
            _native(game).ready.timeout_s < ready.timeout_s
        ), f"{game} does not pay Tortoise's equip-cache cost and should not carry its budget"


def test_the_fatal_pattern_does_not_fire_on_a_line_that_says_it_is_harmless() -> None:
    r"""`Could not open` matched 4,854 lines that end "Logging to it is off for this run."

    `ready.fatal` exists to end the wait early instead of burning the whole
    timeout on a server that will never be ready. Tortoise is the only entry
    that declares one, and as written it read

        Correct \*.map files not found|Could not open|Database .* not found

    A booted, healthy Tortoise worldserver prints, thousands of times:

        Could not open bot log file ../logs/bot_events.csv (No such file or
        directory). Logging to it is off for this run.

    The line explains in its own second sentence that nothing is wrong. The
    install read the first three words, declared the server dead, and gave up
    about a hundred seconds in -- on a server that went on to report itself up.
    Every "Could not open" in that log, all 4,854 of them, was this one form,
    which is why the exclusion can be this specific rather than a guess.

    Both directions are asserted. A pattern narrowed until it matches nothing
    would pass the first half and quietly retire the fast-fail this field is
    for.
    """
    fatal = _native().ready.fatal
    assert fatal is not None
    benign = (
        "Could not open bot log file ../logs/bot_events.csv (No such file or directory). "
        "Logging to it is off for this run."
    )
    assert not re.search(fatal, benign), (
        f"the fatal pattern {fatal!r} fires on a line whose own text says logging was simply "
        "turned off; the install then reports a healthy server as dead"
    )
    for real in (
        "Could not open the configuration file",
        "Correct *.map files not found",
        "Database tw_world not found",
    ):
        assert re.search(fatal, real), f"the pattern no longer catches a real failure: {real!r}"
