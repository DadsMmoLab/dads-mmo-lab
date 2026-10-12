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


def test_losing_the_race_for_a_players_name_is_refused_too() -> None:
    """Someone made the name between the look and the insert: it is not the player's account.

    Mutation: return the raced row as "already existed" again -> red.
    """
    sql = _FakeSql()
    original = sql.run_statement

    def racing(db: Db, statement: str) -> None:
        if statement.startswith("INSERT INTO account ("):
            sql.table["RACED"] = 99
            sql.statements.append((db, statement))
            raise ApplyError("SQL failed (inline → acore_auth): Duplicate entry")
        original(db, statement)

    sql.run_statement = racing  # type: ignore[method-assign]
    with pytest.raises(accounts.AccountError, match="RACED already exists"):
        accounts.create_account(sql, "Raced", PASSWORD, gm_level=3)
    assert sql.access == {}


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
