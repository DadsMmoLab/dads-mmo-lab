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
    its main loop.

    **The whole path, not its last component.** The first version asserted
    `endswith("database_updates")`, which a review pointed out accepts any
    parent at all: `/opt/tortoise/database_updates/` -- one directory too HIGH,
    the same class of mistake in the other direction -- passed it. The path
    below is where the 125 files were counted in the image that booted, so it
    is a measurement and it belongs here whole.
    """
    keys = _native().cmangos.conf.files["mangosd.conf"].keys  # type: ignore[union-attr]
    path = keys["Database.AutoUpdate.Path"].strip('"')
    assert path.rstrip("/") == "/opt/tortoise/sql/database_updates", (
        f"Database.AutoUpdate.Path is {path!r}; the migrations were counted at "
        "/opt/tortoise/sql/database_updates/world (125 files) in the image that booted, and "
        "the updater skips a missing directory in silence"
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

    # WHICH DATABASE each phase loads into, which the phase names do not say.
    # A review moved `playerbots world` to `tw_char` and every assertion above
    # still held: the module's tables land in the characters schema, the
    # server's boot query still finds no `tw_world.ai_playerbot_weightscales`,
    # and the crash quoted in this docstring comes back unchanged.
    assert phases["playerbots world"].into == "tw_world", (
        "the bots' boot query names `tw_world.ai_playerbot_weightscales`; a phase that loads "
        f"into {phases['playerbots world'].into!r} imports the files and still leaves that "
        "table missing"
    )
    assert (
        phases["playerbots characters"].into == "tw_char"
    ), "the character-side module tables belong in the characters schema"

    # The exact glob. `"classic" in pattern` was also true of
    # `.../world/classical/*.sql` -- a directory that does not exist, matching
    # no file, importing nothing, and passing.
    patterns = phases["playerbots world"].files or ()
    assert any(pattern.endswith("/sql/world/classic/*.sql") for pattern in patterns), (
        "the `classic` subfolder holds `ai_playerbot_weightscales` and the rest of the "
        f"tables the bots query on boot; the phase lists only {list(patterns)}"
    )
    assert any(pattern.endswith("/sql/world/*.sql") for pattern in patterns), (
        "the module's top-level world SQL is imported alongside the classic subfolder, as "
        f"wow-vanilla does; the phase lists only {list(patterns)}"
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
    rule = bots[0]
    assert rule.min >= 10, f"a threshold of {rule.min} would pass on an empty import"

    # WHICH database it counts in. A review pointed the rule at `tw_char`: the
    # query still mentions `ai_playerbot`, the threshold is still 10, and the
    # check now passes against the characters schema while the world import it
    # exists to verify goes unexamined -- the exact failure this docstring is
    # about, restored by a one-word edit the assertion above cannot see.
    assert rule.db == "tw_world", f"the rule runs against {rule.db!r}, not the world database"
    assert "table_schema='tw_world'" in rule.query, (
        f"the rule counts tables in whichever schema {rule.query!r} names; the bots read "
        "theirs out of tw_world"
    )


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
    # The exclusion is anchored on the sentence that says the line is harmless,
    # not on the filename in it. The first version keyed on "bot log file",
    # which is the phrase this one boot happened to print; a review pointed out
    # that the playerbots module writes a family of optional logs and any
    # sibling message would have gone straight back to declaring a healthy
    # server dead. This one is the same shape with a different subject, and it
    # must be excluded too.
    sibling = (
        "Could not open combat log ../logs/other.csv (No such file or directory). "
        "Logging to it is off for this run."
    )
    assert not re.search(
        fatal, sibling
    ), f"{fatal!r} fires on an optional log this particular boot did not happen to print"
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


def test_tortoise_is_no_longer_marked_as_work_in_progress() -> None:
    """`wip` outlived its evidence, and the peer level is the honest one.

    The entry sat at `wip` because its installer had never been driven with a
    real client. On 2026-09-03 it was, on `yulon-ubuntu`: 2133 mmap files, a
    clean import into empty schemas with all three verify rules green, 125
    migrations applied by the core, `World server is up and running!`, ports
    3724 and 8090 listening, and an account created through the app's own
    `sql_for_install()` + `create_account()`.

    `beta` and not `stable`, deliberately: what is proven is that the app can
    install and start this server, and its two CMaNGOS siblings are at `beta`
    on exactly that evidence. What is NOT yet proven for any of the three is a
    human logging in with a game client. Promoting past `beta` is the owner's
    call and wants that first.
    """
    from yulon.catalog.catalog import load_catalog

    catalog = load_catalog()
    assert (
        catalog.get("wow-tortoise").status != "wip"
    ), "the Tortoise installer has been driven end to end; `wip` now understates it"
    peers = {game: catalog.get(game).status for game in ("wow-tbc", "wow-vanilla", "wow-tortoise")}
    assert len(set(peers.values())) == 1, (
        f"the three CMaNGOS entries carry the same class of evidence and should carry the "
        f"same status until one of them earns more: {peers}"
    )


def test_every_tortoise_source_is_pinned_to_a_commit_not_a_moving_branch() -> None:
    """A branch is not a pin, and this entry's whole calibration depends on one.

    The core was cloned by BRANCH (`playerbots-integration-gh`) while Eluna
    beside it carried a `rev`. Six defects were fixed on 2026-09-03 by reading
    what that tree does -- which exit status MoveMapGen returns, where the
    migrations live, what the ready banner says, which conf file the bots want,
    which SQL they read, which log line is harmless. Every one of those answers
    is a property of a particular commit. With the source on a branch tip, the
    next install could clone a tree where any of them has changed, and the
    catalog would still claim the old answers.

    `Source.rev`'s own description already says this ("a pinned server must
    rebuild the same bytes"); the entry simply had not used it. Pinned to
    7c0fb278, the commit that was built, extracted, migrated, booted and logged
    into on `yulon-ubuntu`.

    Asserted over ALL sources rather than the one that was wrong, so a third
    source added later cannot arrive unpinned.
    """
    from yulon.catalog.catalog import load_catalog

    sources = load_catalog().get("wow-tortoise").emulator.sources
    unpinned = [s.repo for s in sources if not s.rev]
    assert not unpinned, (
        f"{unpinned} are cloned from a moving ref; this entry's catalog encodes measurements "
        "taken from one commit, and a branch tip is free to invalidate all of them"
    )
    # Truthiness is a declaration, and a review noted that any wrong hash
    # satisfied it. The core's rev is asserted BY VALUE because every other
    # fact in this file was measured against that tree: moving the pin means
    # taking those measurements again, and this line is where that decision has
    # to be written down rather than discovered on a four-hour install. The
    # rest are held to the SHAPE of a full commit id, because an abbreviation
    # is a prefix and a prefix can stop being unique.
    core = next(s for s in sources if s.repo.endswith("tortoise-wow"))
    assert core.rev == "7c0fb278f3f8966422f219e6f5035cb09b76ada7", (
        f"the core is pinned to {core.rev!r}; every measurement in this file -- the exit "
        "status, the migrations path, the ready banner, the bot conf, the SQL globs, the "
        "harmless log line -- was taken from 7c0fb278. Moving the pin means taking them again"
    )
    for source in sources:
        assert re.fullmatch(
            r"[0-9a-f]{40}", source.rev or ""
        ), f"{source.repo} is pinned to {source.rev!r}, which is not a full commit id"


def test_both_boot_patterns_are_read_as_regular_expressions_not_literal_text() -> None:
    """One flag holds up both of the measurements above, and nothing here watched it.

    `ready.regex` is what decides whether the two patterns are compiled or
    `re.escape`d. Every other test in this file reads the pattern STRINGS, so a
    review's flip of `regex` to `false` left all of them green while retiring
    both facts at once:

    * `ready.world` is four alternatives -- three of them from before the banner
      was measured -- so as literal text it matches a line containing a `|`
      character, which nothing prints. A healthy server would wait out the full
      3600 seconds and be reported as never ready, which is the original defect.
    * `ready.fatal` is an alternation carrying a negative lookahead. Escaped, it
      stops matching `Correct *.map files not found` at all, so an install with
      no maps runs its whole timeout instead of failing in seconds -- and the
      lookahead that keeps a healthy server's 4,854 harmless bot-log lines from
      reading as fatal becomes literal text too.

    The suite does catch the flip elsewhere (`test_controller_wow_tortoise.py`),
    which is exactly why it belongs here as well: this file is the one a person
    edits when they change one of these patterns, and the flag they must not
    touch while doing it should fail in front of them.
    """
    ready = _native().ready
    assert ready.regex is True, (
        "both boot patterns are alternations and one carries a negative lookahead; read as "
        "literal text neither can match anything this server prints"
    )
    assert ready.world is not None and ready.fatal is not None
    assert re.escape(ready.world) != ready.world, (
        "the ready marker has no regex syntax left in it -- if that is deliberate, this test "
        "and the `regex` flag both need revisiting"
    )
    assert not re.search(re.escape(ready.world), "World server is up and running!"), (
        "read literally the marker does not match the banner this core prints, which is what "
        "`regex: false` would do to it"
    )
    assert re.search(
        ready.fatal, "Correct *.map files not found"
    ), "the no-maps line is the one fatal this entry can hit in its first seconds"
    assert not re.search(re.escape(ready.fatal), "Correct *.map files not found"), (
        "read literally the fatal pattern misses the failure it exists for, and the install "
        "spends its whole 3600-second budget before saying so"
    )
