"""The three reads the client launcher window shows (T187).

Who to log in as, how many bots and players are online, and which addons the
ready-to-play client carries. All three run on a worker thread while the window
is open, with the server possibly stopped and Docker possibly down, so each one
answers -- an empty list, or `None` for "do not show a count" -- rather than
raising into the window.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from yulon import dbreads, launcher_reads
from yulon.catalog.catalog import CatalogEntry, load_catalog

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
TBC = CATALOG.get("wow-tbc")
VANILLA = CATALOG.get("wow-vanilla")
TORTOISE = CATALOG.get("wow-tortoise")
MARKER = dbreads.Marker(prefix="rndbot", source="default")
APP = "YULON_AB12CD34"

# (entry, its auth schema, its characters schema) -- the per-tree facts a read
# that used the wrong entry would get wrong without failing.
FAMILIES = [
    pytest.param(WOTLK, "acore_auth", "acore_characters", id="azerothcore-wotlk"),
    pytest.param(TBC, "realmd", "characters", id="cmangos-tbc"),
    pytest.param(VANILLA, "realmd", "characters", id="cmangos-vanilla"),
    pytest.param(TORTOISE, "tw_logon", "tw_char", id="tortoise"),
]


class _Reader:
    """A SQL seam that answers each statement in turn and records what it was asked."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.asked: list[tuple[str, str]] = []

    def query(self, db: str, statement: str) -> str:
        self.asked.append((db, statement))
        return self.answers.pop(0) if self.answers else ""

    def run_statement(self, db: str, statement: str) -> None:  # pragma: no cover
        raise AssertionError("the launcher's reads must not write")


class _Broken(_Reader):
    def query(self, db: str, statement: str) -> str:
        raise RuntimeError("the database container is not running")


# -- Log in as ---------------------------------------------------------------


@pytest.mark.parametrize(("entry", "auth", "characters"), FAMILIES)
def test_log_in_as_offers_the_servers_accounts_by_name_for_every_game(
    entry: CatalogEntry, auth: str, characters: str
) -> None:
    sql = _Reader("1\tALICE\t0\n7\tBOB\t3\n")

    names = launcher_reads.login_accounts(sql, entry, MARKER, app_account=APP)

    assert names == ("ALICE", "BOB")
    db, statement = sql.asked[0]
    assert db == "auth"
    assert f"FROM {auth}.account a" in statement
    # The bot accounts are left out by this install's own marker, in the WHERE
    # (useraccounts' rule) -- not 500 rndbot names in a dropdown.
    assert "NOT (" in statement
    assert "LIKE 'RNDBOT%'" in statement.upper()


def test_on_wotlk_the_bots_are_also_left_out_by_the_playerbots_registry() -> None:
    """The registry arm, because on WotLK the prefix alone misses renamed bots."""
    sql = _Reader("")

    launcher_reads.login_accounts(sql, WOTLK, MARKER, app_account=APP)

    assert "acore_playerbots.playerbots_account_type" in sql.asked[0][1]


@pytest.mark.parametrize("entry", [TBC, VANILLA, TORTOISE], ids=["tbc", "vanilla", "tortoise"])
def test_a_tree_without_a_registry_leaves_bots_out_by_the_prefix_alone(entry: CatalogEntry) -> None:
    sql = _Reader("")

    launcher_reads.login_accounts(sql, entry, MARKER, app_account=APP)

    assert "playerbots_account_type" not in sql.asked[0][1]


def test_the_apps_own_account_is_never_offered_whatever_its_name() -> None:
    """Yu'lon's command-channel account is not one a person logs in as.

    The statement already leaves out every `YULON_` name; this is the second
    half, for an app account the database hands back anyway (another spelling,
    another case) -- the dropdown must not offer it either way.
    """
    sql = _Reader("1\tALICE\t0\n2\tchannel\t3\n3\tBOB\t0\n")

    names = launcher_reads.login_accounts(sql, WOTLK, MARKER, app_account="CHANNEL")

    assert names == ("ALICE", "BOB")
    assert "LEFT(a.username, 6) <> 'YULON_'" in sql.asked[0][1]


def test_the_accounts_are_sorted_whatever_order_and_case_they_come_back_in() -> None:
    sql = _Reader("3\tcarl\t0\n1\tBob\t0\n2\tALICE\t0\n")

    names = launcher_reads.login_accounts(sql, TBC, MARKER, app_account=APP)

    assert names == ("ALICE", "Bob", "carl")


def test_a_database_that_cannot_be_read_offers_no_accounts_rather_than_raising() -> None:
    """The window still has "Ask in the game"; an exception would take the window down."""
    assert launcher_reads.login_accounts(_Broken(), WOTLK, MARKER, app_account=APP) == ()


def test_a_row_that_does_not_parse_offers_no_accounts_rather_than_some() -> None:
    sql = _Reader("1\tALICE\t0\nnonsense\n")

    assert launcher_reads.login_accounts(sql, WOTLK, MARKER, app_account=APP) == ()


def test_a_tree_whose_gm_level_store_is_unmeasured_offers_no_accounts() -> None:
    unmeasured = WOTLK.model_copy(
        update={"accounts": WOTLK.accounts.model_copy(update={"level": None})}
    )
    sql = _Reader("1\tALICE\t0\n")

    assert launcher_reads.login_accounts(sql, unmeasured, MARKER, app_account=APP) == ()
    assert sql.asked == []


def test_a_command_channel_name_the_database_hands_back_is_never_offered() -> None:
    """`useraccounts`' rule in Python as well: no `YULON_` name, whatever its case or spacing.

    The statement leaves them out with `LEFT(...) <> 'YULON_'`, which a name
    stored as ` yulon_ab12` (a space, lower case) gets past on a case-sensitive
    collation; the writes refuse such a name by `.strip().upper()`, and so must
    the dropdown, or it offers a person another install's channel credential.
    """
    sql = _Reader("1\tALICE\t0\n2\t yulon_ab12\t3\n3\tYULON_OTHER\t3\n4\tBOB\t0\n")

    names = launcher_reads.login_accounts(sql, WOTLK, MARKER, app_account=APP)

    assert names == ("ALICE", "BOB")


def test_a_blank_marker_offers_no_accounts_says_why_and_asks_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`LIKE '%'` would call every account a bot: no list, a reason in the log, no SQL."""
    import logging

    sql = _Reader("1\tALICE\t0\n")
    blank = dbreads.Marker(prefix="  ", source="default")

    with caplog.at_level(logging.INFO, logger="yulon"):
        assert launcher_reads.login_accounts(sql, WOTLK, blank, app_account=APP) == ()
        assert launcher_reads.account_names(sql, WOTLK, blank, app_account=APP) is None

    assert sql.asked == []
    assert "marker is blank" in caplog.text


def test_account_names_tells_could_not_read_from_no_accounts() -> None:
    """The launcher keeps a saved account it could not look for, and drops one that is gone."""
    assert launcher_reads.account_names(_Broken(), WOTLK, MARKER, app_account=APP) is None
    assert launcher_reads.account_names(_Reader("x\n"), WOTLK, MARKER, app_account=APP) is None
    assert launcher_reads.account_names(_Reader(""), WOTLK, MARKER, app_account=APP) == ()
    names = launcher_reads.account_names(_Reader("1\tBOB\t0\n"), TBC, MARKER, app_account=APP)
    assert names == ("BOB",)


# -- Realm address -------------------------------------------------------------


@pytest.mark.parametrize(("entry", "auth", "characters"), FAMILIES)
def test_the_announced_address_is_the_realm_rows_own_read(
    entry: CatalogEntry, auth: str, characters: str
) -> None:
    """The SELECT the installer's realm step compares with, read for its `address` column."""
    from yulon import networking

    sql = _Reader("192.168.1.20\t127.0.0.1\n")

    assert launcher_reads.announced_address(sql, entry) == "192.168.1.20"
    assert sql.asked == [("auth", networking.realmlist_address_query(entry))]
    assert f"FROM {auth}." in sql.asked[0][1]


@pytest.mark.parametrize("answer", ["", "\n", "a\nb\n", "\t127.0.0.1\n"])
def test_an_announced_address_that_is_not_one_row_is_not_known(answer: str) -> None:
    assert launcher_reads.announced_address(_Reader(answer), WOTLK) is None


def test_an_announced_address_that_cannot_be_read_is_not_known() -> None:
    assert launcher_reads.announced_address(_Broken(), WOTLK) is None


# -- N bots and M players online --------------------------------------------


@pytest.mark.parametrize(("entry", "auth", "characters"), FAMILIES)
def test_the_online_count_splits_bots_from_players_for_every_game(
    entry: CatalogEntry, auth: str, characters: str
) -> None:
    # population()'s one row: players online, bots online, bots in all, characters.
    sql = _Reader("3\t480\t500\t503\n")

    counts = launcher_reads.online_counts(sql, entry, MARKER)

    assert counts == (480, 3)
    db, statement = sql.asked[0]
    assert db == "characters"
    assert f"FROM {characters}.characters" in statement
    assert "online = 1" in statement
    # Bots are identified exactly as the bot list identifies them: the one
    # identity clause, not a second reading of the catalog that could drift.
    assert dbreads.bot_clause(entry, MARKER) in statement
    assert f"{auth}.account" in statement


def test_on_wotlk_a_bot_is_known_by_the_registry_as_well_as_the_prefix() -> None:
    sql = _Reader("0\t1\t1\t1\n")

    launcher_reads.online_counts(sql, WOTLK, MARKER)

    assert "acore_playerbots.playerbots_account_type" in sql.asked[0][1]


@pytest.mark.parametrize("entry", [TBC, VANILLA, TORTOISE], ids=["tbc", "vanilla", "tortoise"])
def test_a_tree_without_a_registry_knows_a_bot_by_the_prefix_alone(entry: CatalogEntry) -> None:
    sql = _Reader("0\t1\t1\t1\n")

    launcher_reads.online_counts(sql, entry, MARKER)

    assert "playerbots_account_type" not in sql.asked[0][1]


def test_a_server_with_no_characters_yet_is_nobody_online_not_a_failure() -> None:
    """`SUM()` over no rows is NULL, which is zero here: a fresh server is a real state."""
    sql = _Reader("NULL\tNULL\tNULL\t0\n")

    assert launcher_reads.online_counts(sql, TBC, MARKER) == (0, 0)


def test_a_database_that_cannot_be_read_hides_the_count() -> None:
    assert launcher_reads.online_counts(_Broken(), WOTLK, MARKER) is None


def test_an_answer_that_is_not_four_numbers_hides_the_count() -> None:
    assert launcher_reads.online_counts(_Reader("garbled\n"), WOTLK, MARKER) is None


def test_a_marker_that_matched_no_character_hides_the_count() -> None:
    """Zero bots beside 900 players is a lie shaped like an answer.

    On a server this app made the bots are always there, so a marker that
    matched nothing while characters exist means every bot would be counted as
    a player. Hidden, not shown wrong.
    """
    sql = _Reader("900\t0\t0\t900\n")

    assert launcher_reads.online_counts(sql, TBC, MARKER) is None


def test_an_empty_marker_is_refused_before_it_can_call_every_account_a_bot() -> None:
    sql = _Reader("0\t3\t3\t3\n")

    assert launcher_reads.online_counts(sql, WOTLK, dbreads.Marker("", "conf")) is None
    assert sql.asked == []


def test_a_tree_with_no_measured_bot_marker_hides_the_count() -> None:
    unmeasured = TBC.model_copy(update={"observability": None})
    sql = _Reader("0\t3\t3\t3\n")

    assert launcher_reads.online_counts(sql, unmeasured, MARKER) is None
    assert sql.asked == []


# -- Addons in this client ---------------------------------------------------


def _addons(
    play_dir: Path, *names: str, interface: str = "Interface", addons: str = "AddOns"
) -> Path:
    folder = play_dir / interface / addons
    folder.mkdir(parents=True)
    for name in names:
        (folder / name).mkdir()
    return folder


def test_the_addons_are_the_folders_in_interface_addons_sorted(tmp_path: Path) -> None:
    folder = _addons(tmp_path, "Questie", "pfQuest", "AtlasLoot")
    (folder / "readme.txt").write_text("not an addon", encoding="utf-8")

    assert launcher_reads.addon_folders(tmp_path) == ("AtlasLoot", "pfQuest", "Questie")


def test_blizzards_own_addons_are_left_out_in_any_case(tmp_path: Path) -> None:
    _addons(tmp_path, "Blizzard_AuctionUI", "blizzard_TalentUI", "Questie", "BlizzMove")

    assert launcher_reads.addon_folders(tmp_path) == ("BlizzMove", "Questie")


def test_interface_and_addons_are_found_whatever_their_case(tmp_path: Path) -> None:
    """A client copied from Windows may say `interface/addons` or `Interface/Addons`."""
    _addons(tmp_path, "Questie", interface="interface", addons="Addons")

    assert launcher_reads.addon_folders(tmp_path) == ("Questie",)


def test_a_client_without_addons_lists_none(tmp_path: Path) -> None:
    (tmp_path / "Interface").mkdir()

    assert launcher_reads.addon_folders(tmp_path) == ()
    assert launcher_reads.addon_folders(tmp_path / "not-there") == ()


def test_a_folder_that_cannot_be_listed_lists_no_addons_rather_than_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _addons(tmp_path, "Questie")

    def refuse(path: object) -> object:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(launcher_reads, "_scandir", refuse)

    assert launcher_reads.addon_folders(tmp_path) == ()


def _listed(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Every folder `addon_folders` lists, in order."""
    seen: list[Path] = []
    real = launcher_reads._scandir

    def spy(path: Path) -> object:
        seen.append(Path(path))
        return real(path)

    monkeypatch.setattr(launcher_reads, "_scandir", spy)
    return seen


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_a_linked_addon_folder_is_listed_by_name_and_never_entered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The player's own addon, shared by a link: it is an addon the game loads.

    Its name is listed; what is inside it -- the player's other client, wherever
    that is -- is never read.
    """
    shared = tmp_path / "elsewhere" / "Shared"
    shared.mkdir(parents=True)
    (shared / "Inner").mkdir()
    play = tmp_path / "play"
    folder = _addons(play, "Questie")
    (folder / "Shared").symlink_to(shared, target_is_directory=True)
    (folder / "Gone").symlink_to(tmp_path / "nothing-here", target_is_directory=True)
    seen = _listed(monkeypatch)

    names = launcher_reads.addon_folders(play)

    # A link to nothing is not an addon the game can load, so it is not listed.
    assert names == ("Questie", "Shared")
    assert seen == [play, play / "Interface", folder], "only the way down to AddOns is listed"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_a_linked_addons_folder_is_listed_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Interface/AddOns` shared between two clients by a link is where the game looks.

    So its folders ARE this client's addons, and they are listed -- one listing
    of the link's target, never a walk into any addon.
    """
    shared = tmp_path / "shared-addons"
    shared.mkdir()
    (shared / "Questie").mkdir()
    (shared / "Questie" / "Modules").mkdir()
    (shared / "Blizzard_Old").mkdir()
    play = tmp_path / "play"
    (play / "Interface").mkdir(parents=True)
    (play / "Interface" / "AddOns").symlink_to(shared, target_is_directory=True)
    seen = _listed(monkeypatch)

    assert launcher_reads.addon_folders(play) == ("Questie",)
    assert play / "Interface" / "AddOns" / "Questie" not in seen
