"""T673: one rule for account names and passwords, for Create and for every later press.

The Accounts tab's Create took any name of 17 characters or fewer without a space
(`anna-lise`, `jo`, `Cafe.x`) and any password of 1-16 characters, while Delete,
Set password and Set GM level allow only `[A-Za-z0-9_]{3,..}` names and
`[A-Za-z0-9_@#%+=!-]{4,16}` passwords. So an account could be made that the same tab
then refused to manage, and a name under the command channel's `YULON_` prefix was
made and then hidden. The length ceilings are each core's own, read from the pinned
trees (see `yulon.account_rules`).

Create is driven through each family's real writer, with an SQL seam that stops the
call at its first statement: a name the writer accepted is one that reached the SQL.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest

from yulon import account_rules, commands, useraccounts
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_centurion import accounts as centurion_accounts
from yulon.controller_wow_tbc import accounts as tbc_accounts
from yulon.controller_wow_tortoise import accounts as tortoise_accounts
from yulon.controller_wow_vanilla import accounts as vanilla_accounts
from yulon.controller_wow_wotlk import accounts as wotlk_accounts
from yulon.manifest import Db

CATALOG = load_catalog()


class _ReachedSql(Exception):
    """The writer got past every check and was about to read or write the database."""


class _StopAtSql:
    """A `SqlSeam` that ends the call at its first statement, so nothing is written."""

    def run_statement(self, db: Db, statement: str) -> None:
        raise _ReachedSql(statement)

    def query(self, db: Db, statement: str) -> str:
        raise _ReachedSql(statement)


def _wotlk(name: str, password: str) -> None:
    wotlk_accounts.create_account(
        _StopAtSql(), name, password, scheme="azerothcore", names_are_names=True
    )


def _centurion(name: str, password: str) -> None:
    centurion_accounts.create_account(CATALOG.get("wow-centurion"), _StopAtSql(), name, password)


WRITERS = {
    "wow-wotlk": _wotlk,
    "wow-tbc": lambda name, pw: tbc_accounts.create_account(_StopAtSql(), name, pw),
    "wow-vanilla": lambda name, pw: vanilla_accounts.create_account(_StopAtSql(), name, pw),
    "wow-tortoise": lambda name, pw: tortoise_accounts.create_account(_StopAtSql(), name, pw),
    "wow-centurion": _centurion,
}


def _create_accepts(game: str, name: str, password: str) -> bool:
    try:
        WRITERS[game](name, password)
    except _ReachedSql:
        return True
    except wotlk_accounts.AccountError:
        return False
    raise AssertionError("the stop-at-SQL seam let the writer finish")


@pytest.mark.parametrize("game", sorted(WRITERS))
@pytest.mark.parametrize("name", ["anna-lise", "jo", "Cafe.x", "Café", "o'neil", "x;y"])
def test_create_refuses_a_name_manage_would_refuse(game: str, name: str) -> None:
    """The audit's measured names: created, then "not a name this server would accept"."""
    assert not _create_accepts(game, name, "s3cret")


@pytest.mark.parametrize("game", sorted(WRITERS))
@pytest.mark.parametrize("password", ["pw1", "a b c", "pass.word", "é1234", "x" * 17])
def test_create_refuses_a_password_set_password_would_refuse(game: str, password: str) -> None:
    assert not _create_accepts(game, "anna", password)


@pytest.mark.parametrize(
    ("game", "longest"),
    [
        # MAX_ACCOUNT_STR, read at each catalog pin: AzerothCore 8a4e76ea AccountMgr.h:34 (17);
        # mangos-tbc 15b6ddb4 and mangos-classic 8ec338a1 src/game/Accounts/AccountMgr.h:34,
        # tortoise-wow 10393426 src/game/AccountMgr.h:50, TrinityCore112 25d3e6ef
        # src/server/game/Accounts/AccountMgr.h:42 (16).
        ("wow-wotlk", 17),
        ("wow-tbc", 16),
        ("wow-vanilla", 16),
        ("wow-tortoise", 16),
        ("wow-centurion", 16),
    ],
)
def test_the_longest_name_is_the_cores_own(game: str, longest: int) -> None:
    assert _create_accepts(game, "a" * longest, "s3cret")
    assert not _create_accepts(game, "a" * (longest + 1), "s3cret")


def test_every_name_and_password_create_accepts_is_one_manage_accepts() -> None:
    """The done-when of T673: nothing Create makes is refused by Delete, Set password or GM."""
    pieces = ["a", "Z", "7", "_", "-", ".", " ", "é", "'", "@"]
    names = {"".join(p) for n in (1, 2, 3) for p in itertools.product(pieces, repeat=n)}
    names |= {"abc" + "d" * n for n in range(0, 20)} | {"123", "12345", "AHBOT"}
    passwords = ["s3cret", "Passw0rd!Mix", "abc", "abcd", "a" * 16, "pa ss", "pa.ss", "p@ss#%+=!-"]
    accepted = 0
    for game in WRITERS:
        entry = CATALOG.get(game)
        ids = useraccounts.digits_are_ids(entry)
        pw_ids = useraccounts.password_digits_are_ids(entry)
        for name in names:
            if not _create_accepts(game, name, "s3cret"):
                continue
            accepted += 1
            folded = wotlk_accounts.fold(name.strip())
            commands.account_delete(folded, digits_are_ids=ids)
            commands.account_set_password(folded, "s3cret", digits_are_ids=pw_ids)
            commands.account_set_gm_level(folded, 1, realms=True, highest=3, digits_are_ids=ids)
        for password in passwords:
            if _create_accepts(game, "anna", password):
                commands.account_set_password("ANNA", password, digits_are_ids=pw_ids)
    assert accepted > 50, "the sample proves nothing if Create accepts almost none of it"


# -- the reserved prefix, and the one function the launcher's Create calls (T696) ----------


@pytest.mark.parametrize("name", ["yulon_dad", "YULON_ABCD1234", "Yulon_x"])
def test_the_command_channels_prefix_is_refused_for_a_players_account(name: str) -> None:
    for game in WRITERS:
        problem = account_rules.create_problem(CATALOG.get(game), name, "s3cret")
        assert "YULON_" in problem, game


def test_the_auction_house_account_stays_creatable() -> None:
    """mod-ah-bot's own prompt tells the player to create the AHBOT account first."""
    assert account_rules.create_problem(CATALOG.get("wow-wotlk"), "AHBOT", "s3cret") == ""


def test_create_problem_says_what_the_writer_would_refuse() -> None:
    """One rule: the launcher's pre-check and the writer never disagree on a name."""
    for game in WRITERS:
        entry = CATALOG.get(game)
        for name in ["anna", "jo", "anna-lise", "12345", "a" * 16, "a" * 17, "Café"]:
            said = account_rules.create_problem(entry, name, "s3cret")
            assert (said == "") == _create_accepts(game, name, "s3cret"), (game, name, said)


def test_an_all_digit_name_is_refused_where_the_server_reads_digits_as_an_id() -> None:
    assert account_rules.create_problem(CATALOG.get("wow-tbc"), "12345", "s3cret")
    assert account_rules.create_problem(CATALOG.get("wow-wotlk"), "12345", "s3cret") == ""


# -- the Accounts tab ------------------------------------------------------------------


def test_the_accounts_tab_refuses_a_reserved_name_before_anything_is_written(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_controller_view import WOTLK, _FakeMaintenance, _Ps, _services
    from yulon import runner
    from yulon.ui.controller_view import ControllerView
    from yulon.ui.widgets.job import run_inline

    ps = _Ps()
    monkeypatch.setattr(runner, "run", ps)
    made = _FakeMaintenance()
    view = ControllerView(
        WOTLK, _services(ps, tmp_path, [], made), status_poll_ms=0, job_runner=run_inline
    )
    try:
        _refused_on_the_tab(view, made)
    finally:
        view.shutdown()


def _refused_on_the_tab(view: Any, made: Any) -> None:
    view.account_name.setText("yulon_dad")
    view.account_password.setText("s3cret")
    view.create_account()
    assert made.created == [], "a reserved name reached the writer"
    assert "YULON_" in view.account_report.text()
    assert view.create_account_button.isEnabled()

    view.account_name.setText("anna-lise")
    view.account_password.setText("s3cret")
    view.create_account()
    assert made.created == []
    assert "letters" in view.account_report.text()


def test_each_family_says_its_own_cores_longest_name() -> None:
    """A CMaNGOS package re-exporting AzerothCore's 17 told its readers the wrong ceiling."""
    assert tbc_accounts.MAX_USERNAME == account_rules.name_max("mangos_srp6") == 16
    assert vanilla_accounts.MAX_USERNAME == account_rules.name_max("mangos_srp6") == 16
    assert wotlk_accounts.MAX_USERNAME == account_rules.name_max("azerothcore") == 17
