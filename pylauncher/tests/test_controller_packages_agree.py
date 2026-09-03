"""One question asked of all three CMaNGOS controller packages at once.

Every test file here is per-game, which is right for what each game does
differently and is exactly why a gap in ONE package survives: nobody reading
`test_controller_wow_tbc.py` is looking at Vanilla, so "Vanilla has this and TBC
does not" is invisible from inside either file. The defect that produced this
module was of that shape -- TBC alone had no `sql_for_install()`, while its
password plan (`generated`, `.db_password`) is identical to the two packages
that did, so a caller wanting a TBC account had no way to reach the password the
installer had minted.

So the rule here is comparative, and it is the only kind of test that can be:
these packages are peers, and a peer's absence is only visible against the
others. Where a game genuinely differs, the difference is named in the test
rather than being allowed to widen quietly.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from yulon.controller_wow_tbc import accounts as tbc_accounts
from yulon.controller_wow_tbc import maintenance as tbc_maintenance
from yulon.controller_wow_tortoise import accounts as tortoise_accounts
from yulon.controller_wow_tortoise import maintenance as tortoise_maintenance
from yulon.controller_wow_vanilla import accounts as vanilla_accounts
from yulon.controller_wow_vanilla import maintenance as vanilla_maintenance

CMANGOS_ACCOUNTS = {
    "wow-tbc": tbc_accounts,
    "wow-vanilla": vanilla_accounts,
    "wow-tortoise": tortoise_accounts,
}
CMANGOS_MAINTENANCE = {
    "wow-tbc": tbc_maintenance,
    "wow-vanilla": vanilla_maintenance,
    "wow-tortoise": tortoise_maintenance,
}


@pytest.mark.parametrize("name", ["sql_for", "sql_for_install", "create_account"])
def test_every_cmangos_package_offers_the_same_account_functions(name: str) -> None:
    """A function that exists for two of the three games and not the third is a bug.

    These three entries share a family, a database client and a `generated`
    password plan; nothing about TBC makes it need a smaller account surface
    than Vanilla. `sql_for_install` was missing from TBC until 2026-09-03 and
    this is the assertion that says so out loud.
    """
    missing = [game for game, mod in CMANGOS_ACCOUNTS.items() if not hasattr(mod, name)]
    assert (
        not missing
    ), f"{name}() is missing from {missing} but present in the other CMaNGOS packages"


def test_sql_for_install_takes_the_server_dir_the_same_way_everywhere() -> None:
    """Same name is not enough: a caller switching games must not have to switch shapes.

    Asserts the signature, not just the presence, because the failure this
    guards is a UI that reads the password for one game and cannot for another.
    """
    shapes = {
        game: [p.name for p in inspect.signature(mod.sql_for_install).parameters.values()]
        for game, mod in CMANGOS_ACCOUNTS.items()
    }
    assert len(set(map(tuple, shapes.values()))) == 1, f"sql_for_install differs by game: {shapes}"
    assert shapes["wow-tbc"][0] == "server_dir"


def test_a_generated_password_that_cannot_be_read_is_refused_and_not_guessed(
    tmp_path: Path,
) -> None:
    """All three refuse, and none falls back to a literal.

    The fallback this forbids is not hypothetical: a shared default is how every
    SQL-backed control came to authenticate as root with the password
    "password" (2026-08-26). `tmp_path` holds no `.db_password`, so each package
    is asked for a password that provably is not there.

    The wording is asserted, not only the exception type, and that is what
    caught the second divergence: Tortoise said "Nothing was written" where the
    other two said "Nothing was asked of the database". Both were true and they
    are not the same promise -- a user who switches games should not be told a
    different story about the identical refusal. Tortoise was aligned rather
    than the assertion loosened to accept both (2026-09-03).
    """
    for game, mod in CMANGOS_ACCOUNTS.items():
        with pytest.raises(mod.AccountError) as caught:  # type: ignore[attr-defined]
            mod.sql_for_install(tmp_path)  # type: ignore[attr-defined]
        message = str(caught.value)
        assert "not knowable" in message, (game, message)
        assert "Nothing was asked of the database" in message, (game, message)


def test_every_cmangos_package_binds_a_database_client_for_backups() -> None:
    """`mysql_for()` must hand `DockerMysql` the declared client, in every package.

    The unbound fallback is `mysql`, and `mariadb:11` ships neither `mysql` nor
    `mysqldump`. This was true of all four packages until 2026-09-03; asserting
    it per game in one place is what stops it being true of the fifth.
    """
    for game, mod in CMANGOS_MAINTENANCE.items():
        seam = mod.mysql_for("pw")  # type: ignore[attr-defined]
        assert seam.client, f"{game} builds a DockerMysql with no declared client"
        assert (
            seam.client == "mariadb"
        ), f"{game} is a CMaNGOS entry on MariaDB but declares {seam.client!r}"
