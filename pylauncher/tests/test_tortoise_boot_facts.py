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


# `test_tortoise_imports_the_playerbot_sql_its_own_bots_query` and
# `test_the_import_is_verified_by_the_tables_the_bots_need` stood here. Both were
# about the fork's vendored `modules/mod-playerbots`: the first that the plan
# imported its `sql/characters` and `sql/world` (including the `world/classic`
# subfolder, where `ai_playerbot_weightscales` actually lived), the second that
# the import verified the result with a `LIKE 'ai_playerbot%'` count rather than
# a table total.
#
# Both directories went with the fork. On the Penqle core the bots' SQL belongs
# to the module and is applied by the core's own updater at world start, which
# is after the import's verify has run -- so the phases are gone, the rule is
# gone, and the two facts that replace them are
# `test_the_bots_own_sql_is_applied_by_the_core_and_the_plan_says_which_folders`
# and `test_the_import_verifies_only_what_its_own_files_create`, further down.


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


def test_the_ready_budget_also_covers_the_windows_first_boot_measured_over_9p() -> None:
    """3702 seconds from `up` to `finished` on native Windows, against a budget of 3600.

    The Linux floor above is not the largest one this entry has been measured to
    need. On `yulon-win11-gate` (2026-09-05, `pyplan/gates/7.7-win11-tortoise/`)
    the same first boot -- same 500 bots, same equip cache -- read its world over
    Docker Desktop's 9p share instead of a native disk, and the worldserver printed

        World server is up and running! Loading time: 59 minutes 18 seconds

    3558 s of loading, 3559 s after the container's `StartedAt`, `RestartCount=0`.
    The `ready` stage's own wall clock is what the budget is spent against, and
    the transcript brackets it exactly: `start_staged()` logged at 23:41:37 and
    `install of wow-tortoise finished` at 00:43:19 box-local, **3702 s** -- the
    banner plus the health wait before it and the poll that noticed it after.

    The repo's budget at the time was 3600 s. That run finished only because the
    copy on the box had been given 10800 s beforehand, on the TBC measurement
    that 9p roughly doubles a boot (7.7's note); under 3600 the stage would have
    expired 102 s before it observed the banner and reported a complete, correct
    install as `never reported ready` -- the TBC verdict of the night before,
    repeated. So the number asserted here is the measured stage wall, not the
    loading time and not the value that happens to be shipped; a smaller budget
    that fits Linux fails this test in front of whoever shrinks it.

    One confound, recorded rather than hidden: a 9.98 GB client re-download
    (`tortoise-dl.log`, second entry, 23:59-00:23 box-local at 6.6 MB/s) ran on
    the same box during 24 of those 59 minutes. The boot may be faster without
    it; the budget still has to cover the boot that was measured.
    """
    ready = _native().ready
    windows_stage_wall = 3702  # 23:41:37 -> 00:43:19, from the transcript's own stamps
    assert ready.timeout_s >= windows_stage_wall, (
        f"the ready budget is {ready.timeout_s}s; the Windows first boot's ready stage was "
        f"measured at {windows_stage_wall}s wall over 9p (7.7, 2026-09-05)"
    )


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
    rebuild the same bytes"); the entry simply had not used it.

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
    assert core.rev == "9980181ce9aa5940b6125a5991cc754148ed9b82", (
        f"the core is pinned to {core.rev!r}. It was 7c0fb278, then 3a8472e on the retired "
        "Shyalya fork, and since T30 it is 9980181c on `tortoise-wow/tortoise-wow` branch "
        "`bot-helpers` -- a different TREE, not a later commit of the same one, so every "
        "measurement in this file was taken again against it on `yulon-arch` 2026-09-11: the "
        "ready banner, the migrations path, the conf keys, the SQL layout, the cmake flags, "
        "the harmless bot-log line and the module's own startup lines "
        "(pyplan/gates/t30-measure-yulon-arch-2026-09-11/). Moving it again means taking them "
        "again"
    )
    module = next(s for s in sources if s.repo == "Sagiroth/TortoiseBots")
    assert module.rev == "fd7ec9ec7659035cfc3ea75d542c8683005525de", (
        f"the bots module is pinned to {module.rev!r}. Its own pin is as load-bearing as the "
        "core's: the module is what decides the folder names its SQL is installed under, "
        "which conf keys exist, and what the world prints when it loads"
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
      character, which nothing prints. A healthy server would wait out the whole
      ready budget and be reported as never ready, which is the original defect.
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
        "spends its whole ready budget before saying so"
    )


# --- the SQL the plan applies, and the SQL it deliberately leaves alone --------------
#
# The retired fork's `sql/character_updates/` lived here, with four tests and two
# constants: `CHARACTER_UPDATES`, `THE_THREE_FILES`, and the measurements from
# `yulon-arch` 2026-09-08 and `m910q` 2026-09-09 that put the phase in the plan.
# Nothing on that fork applied that directory, and a world died without it --
# immediately on a fresh install, and on its first honor-maintenance day on an
# established one.
#
# The Penqle core has no such directory (`06-sql-layout.txt`, T30 Half 1), and
# neither of the two `modules/mod-playerbots/sql/...` directories the plan also
# globbed. All three phases are gone. What replaces them is below: the plan must
# not name a directory this tree does not have, and the bots' own SQL is applied
# by the core rather than by us.


def _sql_plan():
    native = _native()
    assert native.cmangos is not None
    return native.cmangos.sql


def _phase_names() -> list[str]:
    return [phase.name for phase in _sql_plan().phases]


GONE = (
    "sql/character_updates",
    "modules/mod-playerbots/sql/characters",
    "modules/mod-playerbots/sql/world",
)
"""The three directories the plan globbed that the Penqle core does not have.

Listed on `yulon-arch` 2026-09-11 against a real clone at the pinned rev
(`pyplan/gates/t30-measure-yulon-arch-2026-09-11/06-sql-layout.txt`, "the old
fork paths the plan reads that do not exist on this tree"). The first was an
`on_error: fail` phase, so it is the one that would have stopped every fresh
install; the other two were `warn`, which is worse in the other direction --
they would have gone on reporting a successful import of nothing.
"""


def test_the_plan_names_no_directory_the_penqle_core_does_not_have() -> None:
    """Every glob in the plan, against the three directories that went with the fork.

    A `warn` phase over a missing directory is the defect this is really about:
    it imports nothing, says so in one line nobody reads, and leaves an install
    that passes every count-based check while missing the tables a later boot
    queries. That is exactly how the fork's own playerbots SQL went unimported
    for a week in 8.7 -- the same shape, one core earlier.

    Asserted over the whole plan rather than by phase name, because the way this
    comes back is a glob edited into a phase that kept its name.
    """
    globs = [pattern for phase in _sql_plan().phases for pattern in (phase.files or ())]
    assert globs, "the plan globs no files at all; this test is looking at nothing"
    for directory in GONE:
        offenders = [pattern for pattern in globs if directory in pattern]
        assert not offenders, (
            f"the plan still globs {directory!r}, which does not exist on the pinned core: "
            f"{offenders}"
        )
    assert any("/sql/base/" in pattern for pattern in globs), (
        "the 190 per-table world dumps are still the import; a plan that lost them would "
        "also satisfy every assertion above"
    )


def test_the_bots_own_sql_is_applied_by_the_core_and_the_plan_says_which_folders() -> None:
    """Nothing here imports the module's migrations, and that is a measured decision.

    The core applies a module's SQL at world start from
    `<TW_SOURCE_MODULES_DIR>/<module>/data/sql/<folder>`, where `<folder>` is
    the SAME `Database.AutoUpdate.CharUpdateName` / `WorldUpdateName` it uses
    for its own migrations (`AutoUpdater.cpp:245-283`, `:497-531`). So the conf
    block is what decides whether the module's SQL is found at all, and the two
    values are `character` and `world` -- the names `TortoiseBots.cmake:61-69`
    installs the module's `char` folder under, deliberately, with a comment
    saying "so a fresh install cannot silently skip SQL".

    Measured both ways on `yulon-arch` 2026-09-11: with the folder spelled
    `char` the world found nothing, said nothing about it, and died before its
    ready line on `[1146] Table 'tw_char.ai_playerbot_equip_cache' doesn't
    exist` (`08-world-run1.txt`); with it spelled `character` the same image
    applied 5 character and 3 world module migrations and came up (`08d`, `08e`).

    Catches the two folder names edited to the module's on-disk spelling, which
    would ALSO point the core's own `sql/database_updates/character` at a
    directory that is not there.
    """
    keys = _native().cmangos.conf.files["mangosd.conf"].keys  # type: ignore[union-attr]
    assert keys["Database.AutoUpdate.CharUpdateName"].strip('"') == "character"
    assert keys["Database.AutoUpdate.WorldUpdateName"].strip('"') == "world"
    assert keys["Database.AutoUpdate.AllowedModules"].strip('"') == "all", (
        "the updater reads this before it looks at any module; a list that does not name "
        "TortoiseBots skips the module's SQL exactly as a missing folder does"
    )
    globs = [pattern for phase in _sql_plan().phases for pattern in (phase.files or ())]
    assert not [pattern for pattern in globs if "TortoiseBots" in pattern], (
        "a phase imports the module's SQL. The core's updater applies those files itself and "
        "ledgers each by hash; importing them first means the same tables created twice, by "
        "two things that do not know about each other"
    )
    notes = " ".join(note for phase in _sql_plan().phases for note in phase.notes)
    assert "TW_SOURCE_MODULES_DIR" in notes, (
        "nothing in the plan records WHY it imports no bot SQL. A reader who sees the bots' "
        "tables missing at import time and adds a phase for them gets the double-apply above"
    )


def test_the_import_verifies_only_what_its_own_files_create() -> None:
    """The `ai_playerbot%` rule went with the phases that made those tables, and had to.

    It read `COUNT(*) ... table_name LIKE 'ai_playerbot%' >= 10` in `tw_world`,
    and it was true on the fork because the plan's own `playerbots world` phase
    imported those files. On this stack nothing creates them until the world
    starts for the first time -- the core's updater does it -- and the import's
    verify runs before that, so the rule would have failed every fresh install
    at the last step of a forty-minute import.

    Keeping it would have been worse than dropping it, and not only because of
    the failure: since T19 the completeness probe reads its evidence off the
    plan's OWN files (`created_tables()` over each streamed file), so a verify
    rule about tables no file in the plan creates is the one kind of rule that
    cannot be checked against anything.

    What proves the bots' tables arrived is the world itself: it refuses to
    finish starting without them, loudly, on the line in the fatal pattern
    (`test_the_fatal_pattern_catches_the_shape_this_core_dies_in`).
    """
    checks = _native().cmangos.sql.verify  # type: ignore[union-attr]
    assert checks, "the import verifies nothing at all"
    assert not [c for c in checks if "ai_playerbot" in c.query], (
        "the import claims to verify the bots' tables. Nothing in this plan creates them -- "
        "the core's updater does, at first world start, after this check has run"
    )
    world = [c for c in checks if c.db == "tw_world" and "information_schema" in c.query]
    assert world and world[0].min >= 150, (
        "the world import is still verified by the table count its own 190 base dumps "
        f"produce: {[(c.db, c.min) for c in checks]}"
    )
    assert [c for c in checks if c.db == "tw_logon"], "nothing checks the realm row landed"


def test_no_phase_in_this_catalog_runs_on_an_install_already_imported() -> None:
    """`rerun_on_marked` is an exception to the marker rule, and there is no longer one.

    The rule it excepts is the probe's (`pyplan/phase7-decisions.md`, "Probe"):
    a finished import is never re-run, because an app upgrade must not `DROP
    realmd` on a server with accounts on it. A phase carrying this flag is
    applied to that server on every install press instead, so the flag is only
    ever as safe as the files it names are idempotent -- which is a fact about
    somebody else's SQL, read by hand, and not something any test here can
    check.

    Exactly one phase ever carried it: `wow-tortoise`'s `character updates`,
    over the retired fork's `sql/character_updates/`, whose three files were
    read for idempotence by hand on 2026-09-09. That directory does not exist on
    the Penqle core and the phase is gone, so the catalog is back to the plain
    rule.

    Enumerated over the WHOLE catalog, because the failure this guards against
    is the flag appearing ANYWHERE: a phase whose files drop or truncate would
    run again on a live world. Adding one is deliberate work -- it means turning
    this assertion red on purpose and writing down, in the phase's own notes,
    why those files can be applied twice.
    """
    flagged = {
        (entry.id, phase.name)
        for entry in load_catalog().games
        if entry.install.native is not None and entry.install.native.cmangos is not None
        for phase in entry.install.native.cmangos.sql.phases
        if phase.rerun_on_marked
    }
    assert flagged == set(), (
        "`rerun_on_marked` applies a phase to an install the marker rule says is finished -- "
        f"somebody's server, mid-play. This catalog flags {sorted(flagged)}"
    )


# --- T30: the Penqle core's conf surface, and the line the world dies on -------------


def test_the_conf_block_writes_the_keys_this_core_needs_and_none_it_removed() -> None:
    """Six keys arrived with this core and three left with the fork, all in one file.

    Every line here is `05-conf-keys.txt` from T30 Half 1, which probed the
    shipped `mangosd.conf.dist.in` at the pinned rev key by key.

    **The six that arrived** are the auto-updater's folder names and switches
    plus the console. They are not decoration: `Database.AutoUpdate.AllowedModules`
    and the two `*UpdateName`s are what the core uses to find the BOTS' SQL as
    well as its own (`AutoUpdater.cpp:497-531`), `SortByName` decides the order
    migrations are applied in on a tree whose filenames are timestamps, and
    `Console.Enable` is the whole of this entry's command channel now that its
    `operations` block says `attach`.

    **The three that left** are `SOAP.Enabled`, `SOAP.IP` and `SOAP.Port`. The
    subsystem is gone from the core -- not the keys, the code: the only match
    for the string under `src/` is a comment at `src/game/World.h:799`, and no
    gsoap is vendored. They were never written from the conf table anyway; they
    came from `operations.enable_conf`, and they go back the same way.

    `GameType` is checked because its DEFAULT moved (the dist ships `6` on this
    core where the fork shipped `0`), which is the quiet kind of change: an
    entry that stopped writing the key would get a different world without
    anything in this repo being edited.
    """
    keys = _native().cmangos.conf.files["mangosd.conf"].keys  # type: ignore[union-attr]
    for arrived in (
        "Database.AutoUpdate.AllowedModules",
        "Database.AutoUpdate.AuthUpdateName",
        "Database.AutoUpdate.CharUpdateName",
        "Database.AutoUpdate.WorldUpdateName",
        "Database.AutoUpdate.SortByName",
        "Console.Enable",
    ):
        assert arrived in keys, f"{arrived} is new on this core and nothing writes it"
    assert keys["Console.Enable"] == "1", (
        "this entry's command channel is the console (`operations.channel: attach`); with "
        "the console off it has no channel at all"
    )
    assert keys["Database.AutoUpdate.SortByName"] == "1"
    for left in ("SOAP.Enabled", "SOAP.IP", "SOAP.Port"):
        assert left not in keys, (
            f"{left} is written into a conf this core never reads. It belongs to "
            "`operations.enable_conf`, and that block comes back with the subsystem"
        )
    assert keys["GameType"] == "0", (
        "the shipped default moved to 6 on this core; an entry that leaves the key alone "
        "gets a different world than the one that was measured"
    )


def test_both_restart_keys_the_owner_named_are_written_off() -> None:
    """ "When the server is made, AutoHonorRestart must be set from 1 to 0." — the owner.

    Two keys, and each is a way for a solo server to go down on its own while
    somebody is playing on it:

    * `AutoHonorRestart` ships at `1` in this core's `mangosd.conf.dist.in`
      (`:707`) and is read at `src/game/World.cpp:1139`.
    * `AutoRestart.MaxServerUptime` ships at `259200` (`:148`) -- three days.

    Both read on the pinned core, `yulon-arch` 2026-09-11 (`05-conf-keys.txt`).
    The values are strings because that is what a conf table holds, and `"0"`
    rather than `0` is the whole assertion: a JSON `0` would be written as the
    text `0` too, but a JSON `false` would be written as `False`, which this
    core reads as a default.
    """
    keys = _native().cmangos.conf.files["mangosd.conf"].keys  # type: ignore[union-attr]
    assert keys["AutoHonorRestart"] == "0", (
        "the key ships at 1 and the owner asked for 0 when the server is made; this is the "
        "only place the app says so"
    )
    assert keys["AutoRestart.MaxServerUptime"] == "0", "the dist ships three days"


def test_the_two_confs_this_image_does_not_ship_as_plain_dists_name_their_templates() -> None:
    """`materialise()`'s default is `<name>.dist` beside the file, and twice it is wrong here.

    Read off the built image on `yulon-arch` 2026-09-11
    (`12-image-contents.txt`), `/opt/tortoise/etc` holds:

        aiplayerbot.conf                 <- no `.dist` at all
        mangosd.conf.dist
        modules/tortoise_bots.conf       <- the live name, which is NOT a template
        modules/tortoise_bots.conf.dist
        realmd.conf.dist

    `TortoiseBots.cmake:40-46` `configure_file`s the first straight to its live
    name, and installs its own conf one directory down. Without a template per
    file both would be an `InstallerError` from the conf stage -- "the built
    image does not contain /opt/tortoise/etc/aiplayerbot.conf.dist" -- on an
    install that had just spent half an hour compiling.

    The module conf's table key is a PATH, `modules/tortoise_bots.conf`, so the
    install's `etc/` reproduces the image's layout: the module reads it from
    `etc/modules/`, and a file put beside `mangosd.conf` instead would be a file
    the world never opens.

    The other three entries in this table take the default and must go on doing
    so, which is the half that says this is a per-file override and not a new
    rule.
    """
    files = _native().cmangos.conf.files  # type: ignore[union-attr]
    assert files["aiplayerbot.conf"].template == "aiplayerbot.conf", (
        "the image ships this one already stripped; the default would look for "
        "aiplayerbot.conf.dist and refuse the image"
    )
    assert "modules/tortoise_bots.conf" in files, "the module's own conf is never materialised"
    module_conf = files["modules/tortoise_bots.conf"]
    assert module_conf.template == "modules/tortoise_bots.conf.dist"
    assert module_conf.keys, "a conf file in the table with no keys patches nothing"
    for untouched in ("mangosd.conf", "realmd.conf"):
        assert files[untouched].template is None, (
            f"{untouched} is an ordinary `.dist` in this image; naming a template for it "
            "turns a per-file exception into a habit"
        )
    for game in ("wow-tbc", "wow-vanilla"):
        other = _native(game).cmangos.conf.files  # type: ignore[union-attr]
        assert all(
            patch.template is None for patch in other.values()
        ), f"{game}'s images ship plain `.dist` files; the default must not have moved"


def test_the_fatal_pattern_catches_the_shape_this_core_dies_in() -> None:
    """Run 1 of T30 Half 1, which is what a fresh install gets wrong about its bots.

    The world applied every core migration, silently skipped all five of the
    module's character migrations because the folder it looked for was spelled
    differently, and then died -- not at once, and not on a line the old fatal
    pattern matched (`08-world-run1.txt`):

        SQL: select clazz, spec, lvl, slot, quality, item from ai_playerbot_equip_cache
        [1146] Table 'tw_char.ai_playerbot_equip_cache' doesn't exist
        Your database structure is not up to date. ...

    then an assertion inside `HandleMySQLError` and a `std::runtime_error`. What
    the install spine saw was a container that had gone away with no fatal line
    matched, so it restarted it, up to `restart_loop` times, each time spending
    minutes getting back to the same row. Both shapes are in the pattern now.

    The `Could not open` alternative keeps its negative lookahead and that is
    asserted here as well as above, because these two changes touch the same
    string: the module prints `Could not open bot log file .../bot_events.csv
    (No such file or directory). Logging to it is off for this run.` on nearly
    every tick of a perfectly healthy world.
    """
    ready = _native().ready
    assert ready.fatal is not None and ready.regex is True
    for dying in (
        "[1146] Table 'tw_char.ai_playerbot_equip_cache' doesn't exist",
        "Your database structure is not up to date. Please make sure you have executed all "
        "the queries in the sql/updates folders.",
    ):
        assert re.search(ready.fatal, dying), (
            f"the fatal pattern walks past {dying!r}; the install restarts the container "
            "instead of saying what happened"
        )
    for healthy in (
        "Could not open bot log file ../logs/bot_events.csv (No such file or directory). "
        "Logging to it is off for this run.",
        "World server is up and running! Loading time: 1 minutes 13 seconds",
        "[DB Auto-Updater] Found 5 possible migrations for character.",
        "TortoiseBots: native module loaded (AI enabled)",
    ):
        assert not re.search(
            ready.fatal, healthy
        ), f"the fatal pattern fires on a line a healthy world prints: {healthy!r}"
    assert re.search(_native().ready.world or "", "World server is up and running!"), (
        "the ready marker is unchanged on this core -- the first of its four alternatives is "
        "what it prints (`08d-world-run2.txt`), and it is asserted beside the fatal pattern "
        "because a change to one is usually a change to both"
    )
