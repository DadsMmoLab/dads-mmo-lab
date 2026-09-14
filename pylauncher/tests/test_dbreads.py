"""Tests for `yulon.dbreads` — how many players and how many bots (8.1a).

Two incidents recorded in `.notes/phase8-reads/hypeer.md` decide almost every
rule here, and both are about a detector failing in a direction that looks fine:

* Asking only the playerbots registry fails **open**. On a freshly built install
  that registry can be empty while a thousand bot characters play, so
  `account NOT IN (<empty set>)` is true for every row and every bot is counted
  as a person (`botid.rs:4-17`, live proof 2026-08-01).
* An empty account prefix compiles to `LIKE '%'` and fails the other way: every
  account is a bot (`botid.rs:33-38`).

So the clause has two arms, and a prefix that cannot be read is refused rather
than defaulted, guessed or blanked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import dbreads
from yulon.catalog import catalog as catalog_module

WOTLK = catalog_module.load_catalog().get("wow-wotlk")


class _FakeSql:
    """Records each statement and answers with canned tab-separated rows."""

    def __init__(self, answer: str = "0\t0\t0\t0\n") -> None:
        self.statements: list[tuple[str, str]] = []
        self.answer = answer

    def query(self, db: str, statement: str) -> str:
        self.statements.append((db, statement))
        return self.answer


def _install(
    tmp_path: Path, prefix_line: str | None = "AiPlayerbot.RandomBotAccountPrefix = rndbot"
) -> Path:
    """A server dir holding just the one conf file the marker is read from."""
    conf = tmp_path / WOTLK.observability.bots.prefix_conf_file
    conf.parent.mkdir(parents=True, exist_ok=True)
    body = "# a comment\nAiPlayerbot.MaxRandomBots = 500\n"
    if prefix_line is not None:
        body += prefix_line + "\n"
    conf.write_text(body, encoding="utf-8")
    return tmp_path


# -- resolving the marker --------------------------------------------------


def test_the_prefix_is_read_from_this_installs_conf_not_from_the_shipped_default(
    tmp_path: Path,
) -> None:
    """The live value, because an install may have been given a prefix of its own."""
    server_dir = _install(tmp_path, "AiPlayerbot.RandomBotAccountPrefix = HOUSEBOT")

    answer = dbreads.resolve_marker(WOTLK, server_dir)

    assert answer.problem == ""
    assert answer.marker is not None
    assert answer.marker.prefix == "HOUSEBOT"
    assert answer.marker.source == "conf"


def test_a_key_the_conf_does_not_set_falls_back_to_the_modules_default_and_says_which(
    tmp_path: Path,
) -> None:
    """That is how the server resolves it, and the source is reported either way."""
    server_dir = _install(tmp_path, prefix_line=None)

    answer = dbreads.resolve_marker(WOTLK, server_dir)

    assert answer.marker is not None
    assert answer.marker.prefix == WOTLK.observability.bots.account_prefix
    assert answer.marker.source == "default"


def test_a_blank_prefix_is_refused_instead_of_becoming_like_percent(tmp_path: Path) -> None:
    """An empty prefix matches every account: the failure that calls a family bots."""
    server_dir = _install(tmp_path, "AiPlayerbot.RandomBotAccountPrefix =")

    answer = dbreads.resolve_marker(WOTLK, server_dir)

    assert answer.marker is None
    assert "blank" in answer.problem or "empty" in answer.problem


def test_a_module_conf_that_is_simply_not_there_means_the_compiled_default_is_in_force(
    tmp_path: Path,
) -> None:
    """Measured on yulon-ubuntu, 2026-09-06, and it refuted the rule that was here.

    This code used to refuse when the conf was absent, reasoning that a missing
    file is not evidence about what the server read. The live 7.2 install says
    otherwise, in the worldserver's own words:

        > Config::LoadFile: Failed open file
          '/azerothcore/env/dist/etc/modules/playerbots.conf'
        > Not found modules config files

    That is the path the catalog names, absent on a normal install — only
    `playerbots.conf.dist` is shipped, and it is NOT loaded. So the server runs
    on the module's compiled defaults, and "absent" is exactly what the default
    being in force looks like. Refusing there would have left the tab with no
    counts on the very install this box gates.
    """
    answer = dbreads.resolve_marker(WOTLK, tmp_path)

    assert answer.marker is not None
    assert answer.marker.prefix == WOTLK.observability.bots.account_prefix
    assert answer.marker.source == "default"


def test_a_conf_that_exists_and_cannot_be_read_is_still_refused(
    tmp_path: Path, monkeypatch
) -> None:
    """The half of the old rule that survives the measurement.

    A file that is THERE and will not open says nothing about what is in it, so
    the answer is a refusal rather than the default — the distinction is between
    "the server found no file either" and "there is a file here whose contents
    are unknown to me".
    """
    server_dir = _install(tmp_path)

    def refuse(*_args, **_kwargs):
        raise PermissionError("[Errno 13] Permission denied")

    monkeypatch.setattr(Path, "read_text", refuse)

    answer = dbreads.resolve_marker(WOTLK, server_dir)

    assert answer.marker is None
    assert "Permission denied" in answer.problem


def test_the_same_key_set_twice_to_different_values_is_refused(tmp_path: Path) -> None:
    """Which one the server takes is not something this reader is entitled to guess."""
    server_dir = _install(tmp_path, "AiPlayerbot.RandomBotAccountPrefix = one")
    conf = server_dir / WOTLK.observability.bots.prefix_conf_file
    conf.write_text(
        conf.read_text(encoding="utf-8") + "AiPlayerbot.RandomBotAccountPrefix = two\n",
        encoding="utf-8",
    )

    answer = dbreads.resolve_marker(WOTLK, server_dir)

    assert answer.marker is None
    assert "twice" in answer.problem or "more than once" in answer.problem


def test_the_same_key_set_twice_to_the_same_value_is_not_a_conflict(tmp_path: Path) -> None:
    """Duplicated is untidy; contradictory is what cannot be resolved."""
    server_dir = _install(tmp_path, "AiPlayerbot.RandomBotAccountPrefix = same")
    conf = server_dir / WOTLK.observability.bots.prefix_conf_file
    conf.write_text(
        conf.read_text(encoding="utf-8") + "AiPlayerbot.RandomBotAccountPrefix = same\n",
        encoding="utf-8",
    )

    answer = dbreads.resolve_marker(WOTLK, server_dir)

    assert answer.marker is not None
    assert answer.marker.prefix == "same"


# -- the clause ------------------------------------------------------------


def test_the_bot_clause_asks_the_registry_AND_the_prefix_because_either_alone_fails(
    tmp_path: Path,
) -> None:
    """Registry-only fails open on an empty registry; prefix-only misses renamed accounts."""
    answer = dbreads.resolve_marker(WOTLK, _install(tmp_path))
    assert answer.marker is not None

    clause = dbreads.bot_clause(WOTLK, answer.marker)

    assert "playerbots_account_type" in clause
    assert "acore_auth" in clause and "LIKE" in clause
    assert " OR " in clause


def test_the_prefix_arm_is_anchored_at_the_start_so_a_name_containing_it_is_not_a_bot(
    tmp_path: Path,
) -> None:
    """`LIKE '%rndbot%'` would classify an account called `notrndbotreally` as a bot."""
    answer = dbreads.resolve_marker(WOTLK, _install(tmp_path))
    assert answer.marker is not None

    clause = dbreads.bot_clause(WOTLK, answer.marker)

    assert "'RNDBOT%'" in clause.upper()
    assert "'%RNDBOT" not in clause.upper()


def test_a_prefix_carrying_a_quote_is_refused_rather_than_pasted_into_the_clause(
    tmp_path: Path,
) -> None:
    """The conf file is on disk and this value ends up inside a SQL string."""
    server_dir = _install(tmp_path, "AiPlayerbot.RandomBotAccountPrefix = rnd'bot")

    answer = dbreads.resolve_marker(WOTLK, server_dir)

    assert answer.marker is None
    assert answer.problem != ""


# -- the counts ------------------------------------------------------------


def test_the_counts_come_from_one_query_so_a_five_second_tick_costs_one_exec(
    tmp_path: Path,
) -> None:
    """Each `docker exec mysql` costs a third of a second; this runs on a timer."""
    answer = dbreads.resolve_marker(WOTLK, _install(tmp_path))
    assert answer.marker is not None
    sql = _FakeSql("3\t497\t500\t512\n")

    counts = dbreads.population(sql, WOTLK, answer.marker)

    assert len(sql.statements) == 1
    assert sql.statements[0][0] == "characters"
    assert counts.players == 3
    assert counts.bots == 497
    assert counts.problem == ""


def test_a_marker_that_matched_nothing_while_characters_exist_warns_rather_than_reporting_zero(
    tmp_path: Path,
) -> None:
    """Zero bots on a server full of characters is a marker question, not a fact."""
    answer = dbreads.resolve_marker(WOTLK, _install(tmp_path))
    assert answer.marker is not None
    sql = _FakeSql("2\t0\t0\t812\n")

    counts = dbreads.population(sql, WOTLK, answer.marker)

    assert counts.bots == 0
    assert counts.warning != ""
    assert "rndbot" in counts.warning.lower()


def test_an_empty_server_does_not_warn_because_nothing_is_unexplained(tmp_path: Path) -> None:
    """No characters at all is a server nobody has played, not a broken marker."""
    answer = dbreads.resolve_marker(WOTLK, _install(tmp_path))
    assert answer.marker is not None
    sql = _FakeSql("0\t0\t0\t0\n")

    counts = dbreads.population(sql, WOTLK, answer.marker)

    assert counts.warning == ""


def test_a_query_that_fails_is_reported_and_never_counted_as_zero(tmp_path: Path) -> None:
    """A database that cannot be asked must not look like a server nobody is on."""
    answer = dbreads.resolve_marker(WOTLK, _install(tmp_path))
    assert answer.marker is not None

    class _Broken:
        def query(self, db: str, statement: str) -> str:
            raise RuntimeError("ERROR 2002 (HY000): Can't connect to local MySQL server")

    counts = dbreads.population(_Broken(), WOTLK, answer.marker)

    assert counts.problem != ""
    assert counts.players is None and counts.bots is None


def test_an_answer_that_is_not_four_numbers_is_reported_rather_than_parsed_optimistically(
    tmp_path: Path,
) -> None:
    """A short row means the query did not run the way this code believes it did."""
    answer = dbreads.resolve_marker(WOTLK, _install(tmp_path))
    assert answer.marker is not None

    counts = dbreads.population(_FakeSql("3\t497\n"), WOTLK, answer.marker)

    assert counts.problem != ""
    assert counts.players is None


# -- the three prefix-only trees (8.1b, 8.1c, 8.1d), each with its own facts --
#
# Parametrised rather than copied, and every assertion reads the entry under
# test rather than a literal: these three happen to agree on this shape, and the
# day one of them stops agreeing the test must fail rather than pass because its
# neighbours still hold.
#
# Two of them are CMaNGOS and the third is not. `cmangos.md` says so in as many
# words — "This is **not** a CMaNGOS tree… Nothing in this section may be
# inherited from the two sections above" — so Tortoise's values come from its
# own section and its own source tree, and they carry their own citations even
# where the value is the same: the compiled default is at
# `PlayerbotAIConfig.cpp:545` here and `:500` on TBC, the shipped line is `:63`
# here and `:57` there. Measured on m910q on 2026-09-06: TBC at `~/tbc-7.4c`,
# Vanilla at `~/vanilla-75b`, Tortoise from the source at `~/tortoise-server`.

CMANGOS = ["wow-tbc", "wow-vanilla", "wow-tortoise"]
TBC = catalog_module.load_catalog().get("wow-tbc")


def _cmangos_install(
    entry: object, tmp_path: Path, line: str = "AiPlayerbot.RandomBotAccountPrefix = RNDBOT"
) -> Path:
    """A CMaNGOS server dir holding the one conf file, as the real installs have it."""
    conf = tmp_path / entry.observability.bots.prefix_conf_file
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(
        "AiPlayerbot.MinRandomBots = 500\nAiPlayerbot.MaxRandomBots = 500\n" + line + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _tbc_install(tmp_path: Path, line: str = "AiPlayerbot.RandomBotAccountPrefix = RNDBOT") -> Path:
    return _cmangos_install(TBC, tmp_path, line)


@pytest.mark.parametrize("game", CMANGOS)
def test_a_cmangos_tree_reads_its_prefix_from_the_conf_that_install_really_has(
    game: str, tmp_path: Path
) -> None:
    """Where TBC differs from WotLK, measured on `m910q` 2026-09-06.

    The AzerothCore install has no `playerbots.conf` at all, so its marker comes
    from the module's compiled default. A CMaNGOS install DOES have
    `etc/aiplayerbot.conf` — Yu'lon materialises it out of the image and patches
    it — with the key active at column 0:

        57:AiPlayerbot.RandomBotAccountPrefix = RNDBOT

    So on this tree the answer comes from the file and says so, and the two trees
    reach the same question by different routes.
    """
    entry = catalog_module.load_catalog().get(game)

    answer = dbreads.resolve_marker(entry, _cmangos_install(entry, tmp_path))

    assert answer.marker is not None
    assert answer.marker.prefix == "RNDBOT"
    assert answer.marker.source == "conf"


@pytest.mark.parametrize("game", CMANGOS)
def test_a_cmangos_clause_has_no_registry_arm_because_that_tree_has_no_such_table(
    game: str, tmp_path: Path
) -> None:
    """`cmangos.md:570`: "bot-account marker | none — only the `account.username` RNDBOT% prefix".

    An arm invented for this tree would query a table that does not exist and
    turn every count into a SQL error.
    """
    entry = catalog_module.load_catalog().get(game)
    answer = dbreads.resolve_marker(entry, _cmangos_install(entry, tmp_path))
    assert answer.marker is not None

    clause = dbreads.bot_clause(entry, answer.marker)

    assert clause.count(" OR ") == 0
    assert "playerbots_account_type" not in clause
    assert f"{entry.databases.auth}.account" in clause
    assert "acore_" not in clause, "nothing from the AzerothCore tree belongs in this clause"
    others = {"realmd", "tw_logon"} - {entry.databases.auth}
    for foreign in others:
        assert foreign not in clause, f"{game} must not reach into {foreign}"


@pytest.mark.parametrize("game", CMANGOS)
def test_a_cmangos_trees_counts_address_its_own_schemas(game: str, tmp_path: Path) -> None:
    """Read from the entry, so a tree whose schemas change fails rather than passes."""
    entry = catalog_module.load_catalog().get(game)
    answer = dbreads.resolve_marker(entry, _cmangos_install(entry, tmp_path))
    assert answer.marker is not None
    sql = _FakeSql("1\t2\t3\t4\n")

    dbreads.population(sql, entry, answer.marker)

    statement = sql.statements[0][1]
    assert f"FROM {entry.databases.characters}.{entry.observability.characters.table}" in statement
    assert "acore_" not in statement
