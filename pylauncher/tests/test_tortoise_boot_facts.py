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
