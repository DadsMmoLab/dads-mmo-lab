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


def test_every_seam_builder_in_every_package_binds_the_declared_client(tmp_path: Path) -> None:
    """EVERY function that builds a database seam, not the two someone remembered.

    This test was written naming `maintenance.mysql_for` alone, and it passed
    while `accounts.sql_for` -- one module over, in all four packages -- still
    built its seam with no client at all. That gap was found by driving the real
    path rather than reading it: creating an account on the live TBC server on
    m910q printed `client=None` on the seam it had just built (2026-09-03).

    So the builders are DISCOVERED rather than listed. Every `sql_for` /
    `sql_for_install` / `mysql_for` in every controller package is called and its
    seam checked, which is what makes a fifth builder added tomorrow fail here
    instead of shipping unbound. A test that names its subjects can only prove
    something about the names its author already thought of.

    Why it matters: `mysql_client()` asks the container `command -v` and believes
    the answer, so an unbound seam works whenever the probe can run. It falls
    back to its first candidate when it CANNOT -- no docker CLI, a timeout, an
    OSError -- and unbound that is `mysql`, which `mariadb:11` does not ship.
    """
    from yulon.controller_wow_wotlk import accounts as wotlk_accounts
    from yulon.controller_wow_wotlk import maintenance as wotlk_maintenance

    packages = {
        "wow-tbc": (tbc_accounts, tbc_maintenance, "mariadb"),
        "wow-vanilla": (vanilla_accounts, vanilla_maintenance, "mariadb"),
        "wow-tortoise": (tortoise_accounts, tortoise_maintenance, "mariadb"),
        "wow-wotlk": (wotlk_accounts, wotlk_maintenance, "mysql"),
    }
    checked = 0
    for game, (accounts_mod, maintenance_mod, expected) in packages.items():
        for mod in (accounts_mod, maintenance_mod):
            for name in ("sql_for", "sql_for_install", "mysql_for"):
                builder = getattr(mod, name, None)
                if builder is None or not inspect.isfunction(builder):
                    continue
                first = next(iter(inspect.signature(builder).parameters))
                if first == "server_dir":
                    # A real install directory: `sql_for_install` refuses one
                    # with no password file, and rightly so -- that refusal has
                    # its own test above. Here the question is the CLIENT, so
                    # the password has to be readable for the seam to exist.
                    install = tmp_path / game
                    install.mkdir(exist_ok=True)
                    (install / ".db_password").write_text("hunter2", encoding="utf-8")
                    seam = builder(install)
                else:
                    seam = builder("pw")
                assert seam.client == expected, (
                    f"{game}.{mod.__name__.rsplit('.', 1)[-1]}.{name}() built a seam with "
                    f"client={seam.client!r}; this entry declares {expected!r}, and an unbound "
                    "seam falls back to `mysql` when the container cannot be asked"
                )
                checked += 1
    assert checked >= 10, f"only {checked} seam builders were found; the discovery is not working"
