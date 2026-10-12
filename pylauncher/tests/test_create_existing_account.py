"""The player's Create on a name that already exists (T749, audit 2026-10-12 lane B finding 4).

Create `bob` / `newpass` / GM 3 where `bob` existed answered "bob: already existed (id 5), GM
level 3.": the typed password was dropped without a word and someone else's account (or a bot
account the list hides) became a GM 3 with no question asked. The shared writer's "make it, or
bring an existing one up to what was asked" is what the command channel needs for its own
`YULON_` account (a repeated settle must not fail on the row it made), and it was the player's
Create as well.

Now an existing account is kept and raised only when it is Yu'lon's own (`YULON_`, the prefix
`account_rules` refuses every player's Create); any other existing name is refused before
anything is written, with the presses that do change it named. The shared pre-check also
refuses the random bots' account prefix, so a player's account cannot be made into a bot.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from tests.test_accounts import PASSWORD, _FakeSql
from yulon import account_rules
from yulon.apply import ApplyError
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_tbc import accounts as tbc_accounts
from yulon.controller_wow_tortoise import accounts as tortoise_accounts
from yulon.controller_wow_vanilla import accounts as vanilla_accounts
from yulon.controller_wow_wotlk import accounts
from yulon.manifest import Db

CATALOG = load_catalog()


def _writes(sql: _FakeSql) -> list[str]:
    return [s for _, s in sql.statements]


@pytest.mark.parametrize("scheme", get_args(accounts.Scheme))
def test_create_on_a_name_that_exists_is_refused_and_writes_nothing(scheme: str) -> None:
    """Mutation: let `_account_row()` keep any existing row again -> red."""
    sql = _FakeSql({"BOB": 5})
    with pytest.raises(accounts.AccountError, match="already exists") as refused:
        accounts.create_account(sql, "bob", PASSWORD, gm_level=3, scheme=scheme)  # type: ignore[arg-type]
    assert _writes(sql) == [], "a statement ran for an account the player did not make"
    assert sql.access == {} and sql.ranks == {}, "its GM level was raised"
    said = str(refused.value)
    assert said.startswith("An account named BOB already exists")
    assert "Set password" in said and "Set GM level" in said
    assert PASSWORD not in said
    assert type(refused.value).__name__ == "AccountExists"  # an AccountError: what callers catch


@pytest.mark.parametrize(
    "create",
    [
        lambda sql: tbc_accounts.create_account(sql, "bob", PASSWORD, gm_level=3),
        lambda sql: vanilla_accounts.create_account(sql, "bob", PASSWORD, gm_level=3),
        lambda sql: tortoise_accounts.create_account(sql, "bob", PASSWORD, gm_level=3),
    ],
    ids=["tbc", "vanilla", "tortoise"],
)
def test_every_familys_create_refuses_an_existing_name(create: object) -> None:
    sql = _FakeSql({"BOB": 5})
    with pytest.raises(accounts.AccountError, match="already exists"):
        create(sql)  # type: ignore[operator]
    assert _writes(sql) == []


def test_a_bot_account_the_list_hides_is_not_made_a_gm() -> None:
    sql = _FakeSql({"RNDBOT12": 40})
    with pytest.raises(accounts.AccountError, match="RNDBOT12 already exists"):
        accounts.create_account(sql, "rndbot12", PASSWORD, gm_level=3)
    assert sql.access == {}


class _InsertAnswersAnError(_FakeSql):
    """An `account` INSERT that answers `ApplyError`, and the row the database holds after.

    `committed=True`: the insert itself went in and only the answer was lost (a timeout, an
    exec that died on the way back), so the row holds the salt THIS call wrote. False:
    somebody else made the name in between, with a salt of their own. `readback_fails`: the
    question "whose row is it" cannot be asked.
    """

    def __init__(self, *, committed: bool, readback_fails: bool = False) -> None:
        super().__init__()
        self.committed = committed
        self.readback_fails = readback_fails
        self.stored: dict[int, str] = {}

    def run_statement(self, db: Db, statement: str) -> None:
        if not statement.startswith(("INSERT INTO account (", "INSERT INTO account(")):
            super().run_statement(db, statement)
            return
        self.statements.append((db, statement))
        name = _literals(statement)[0].decode()
        self.table[name] = 99
        if statement.startswith("INSERT INTO account (username, v, s"):
            written = _literals(statement)[2].decode()  # `s`, stored as hex text
        elif "salt" in statement:
            written = _literals(statement)[1].hex().upper()
        else:
            written = ""
        self.stored[99] = written if self.committed else "AB" * 32
        raise ApplyError("SQL failed (inline → acore_auth): Lost connection")

    def query(self, db: Db, statement: str) -> str:
        if statement.startswith(("SELECT HEX(salt) FROM account", "SELECT s FROM account")):
            self.queries.append((db, statement))
            if self.readback_fails:
                raise ApplyError("SQL failed: Lost connection")
            return self.stored[int(statement.rsplit("= ", 1)[1])] + "\n"
        return super().query(db, statement)


def _literals(statement: str) -> list[bytes]:
    return [bytes.fromhex(part.split("'")[0]) for part in statement.split("X'")[1:]]


def test_losing_the_race_for_a_players_name_is_refused_too() -> None:
    """Someone made the name between the look and the insert: it is not the player's account.

    Mutation: return the raced row as "already existed" again -> red.
    """
    sql = _InsertAnswersAnError(committed=False)
    with pytest.raises(accounts.AccountError, match="RACED already exists"):
        accounts.create_account(sql, "Raced", PASSWORD, gm_level=3)
    assert sql.access == {}


@pytest.mark.parametrize("scheme", ["azerothcore", "mangos_srp6"])
def test_an_insert_that_went_in_before_its_error_is_finished_as_the_players_own(
    scheme: str,
) -> None:
    """Round 2 (review): the row holds the salt this call wrote, so it is the player's account,
    with the password typed. It is finished (counters seeded, level granted) and reported
    as created, not "already exists ... nothing was changed".

    Mutation: treat a row whose salt matches as somebody else's -> red.
    """
    sql = _InsertAnswersAnError(committed=True)
    made = accounts.create_account(sql, "mine", PASSWORD, scheme=scheme)  # type: ignore[arg-type]
    assert made.created is True and made.account_id == 99
    assert any(s.startswith("INSERT INTO realmcharacters") for s in _writes(sql))


def test_a_committed_insert_also_gets_the_gm_level_asked_for() -> None:
    sql = _InsertAnswersAnError(committed=True)
    made = accounts.create_account(sql, "mine", PASSWORD, gm_level=2)
    assert made.created is True and made.gm_level == 2
    assert sql.access == {99: 2}


@pytest.mark.parametrize(
    ("committed", "readback_fails", "scheme"),
    [(True, False, "mangos_sha"), (True, True, "azerothcore"), (False, True, "azerothcore")],
    ids=["hash-proves-nothing", "readback-fails", "readback-fails-not-ours"],
)
def test_an_insert_whose_row_cannot_be_told_says_so_plainly(
    committed: bool, readback_fails: bool, scheme: str
) -> None:
    """Neither "already exists" (it may be theirs) nor a GM grant (it may not be)."""
    sql = _InsertAnswersAnError(committed=committed, readback_fails=readback_fails)
    with pytest.raises(accounts.AccountError) as said:
        accounts.create_account(
            sql, "mine", "hunter2", gm_level=3, scheme=scheme  # type: ignore[arg-type]
        )
    assert type(said.value).__name__ != "AccountExists"
    text = str(said.value)
    assert text.startswith("Yu'lon cannot tell whether it created MINE")
    assert "account list" in text and "Set GM level" in text
    assert "hunter2" not in text
    assert sql.access == {} and sql.ranks == {}


def test_a_new_name_is_still_created() -> None:
    sql = _FakeSql({"BOB": 5})
    made = accounts.create_account(sql, "anna", PASSWORD, gm_level=1)
    assert made.created is True and made.username == "ANNA" and made.gm_level == 1


def test_yulons_own_channel_account_is_still_kept_and_brought_up() -> None:
    """The command channel's settle (`channel_setup.ensure()`) reads `created is False` as
    "the row is there with a password this machine lacks" (T386) and needs its level.

    Mutation: refuse every existing name, the app's own too -> red.
    """
    sql = _FakeSql({"YULON_AB12CD34": 7})
    made = accounts.create_account(sql, "YULON_AB12CD34", PASSWORD, gm_level=3)
    assert made.created is False and made.account_id == 7 and made.gm_level == 3
    assert not [s for s in _writes(sql) if s.startswith("INSERT INTO account (")]


def test_the_channel_settle_still_reads_an_existing_own_account_as_lost(tmp_path: object) -> None:
    """Through `channel_setup.ensure()` with the real writer as its `create` seam."""
    from yulon import channel_setup

    sql = _FakeSql({"YULON_AB12CD34": 7})

    class _NeverAsked:
        def send(self, _command: str) -> object:
            raise AssertionError("a lost account is not tried")

    state = channel_setup.ensure(
        account="YULON_AB12CD34",
        password="Fresh-pw1",
        create=lambda name, pw, level: accounts.create_account(sql, name, pw, gm_level=level),
        channel=_NeverAsked(),
        game="wow-wotlk",
        install_id="ab12cd34",
        host="127.0.0.1",
        port=7878,
        namespace="t749",
        config_dir=tmp_path,  # type: ignore[arg-type]
    )
    assert isinstance(state, channel_setup.Refused)
    assert state.reason == channel_setup.LOST_PASSWORD
    assert sql.access == {7: 3}


# ------------------------------------------------------------- the shared pre-check


@pytest.mark.parametrize(
    ("game", "name"),
    [
        ("wow-wotlk", "RNDBOT12"),
        ("wow-wotlk", "rndbot7"),
        ("wow-wotlk", "RndBotFan"),
        ("wow-centurion", "PLAYERBOTONE"),
    ],
)
def test_create_refuses_the_random_bots_account_prefix(game: str, name: str) -> None:
    """A player's account named like the bots' is counted, listed and treated as a bot.

    Mutation: drop the bot check from `create_problem()` -> red.
    """
    entry = CATALOG.get(game)
    said = account_rules.create_problem(entry, name, PASSWORD)
    assert "bots" in said
    assert said.startswith("Names that start with")


def test_create_still_takes_an_ordinary_name_and_ahbot() -> None:
    entry = CATALOG.get("wow-wotlk")
    assert account_rules.create_problem(entry, "bob", PASSWORD) == ""
    assert account_rules.create_problem(entry, "AHBOT", PASSWORD) == ""
    assert account_rules.create_problem(entry, "botfan", PASSWORD) == ""


def test_the_bot_prefix_this_install_runs_with_is_refused(tmp_path: Path) -> None:
    """Round 2: `AiPlayerbot.RandomBotAccountPrefix = bot` in the install's conf.

    Mutation: ignore `server_dir` (catalog default only) -> red.
    """
    entry = CATALOG.get("wow-wotlk")
    assert entry.observability is not None
    conf = tmp_path / str(entry.observability.bots.prefix_conf_file)
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("AiPlayerbot.RandomBotAccountPrefix = bot\n", encoding="utf-8")

    said = account_rules.create_problem(entry, "botty", PASSWORD, server_dir=tmp_path)
    assert said.startswith("Names that start with BOT are the random bots'")
    # The catalog's default is not this server's prefix, so it is a free name here.
    assert account_rules.create_problem(entry, "rndbot1", PASSWORD, server_dir=tmp_path) == ""
    # Without the folder the default still stands.
    assert account_rules.create_problem(entry, "rndbot1", PASSWORD) != ""


def test_a_conf_that_cannot_say_falls_back_to_the_default(tmp_path: Path) -> None:
    entry = CATALOG.get("wow-wotlk")
    assert entry.observability is not None
    conf = tmp_path / str(entry.observability.bots.prefix_conf_file)
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("AiPlayerbot.RandomBotAccountPrefix =\n", encoding="utf-8")  # blank
    assert account_rules.create_problem(entry, "rndbot1", PASSWORD, server_dir=tmp_path) != ""
