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
import io
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


def test_the_password_parameter_is_spelled_the_same_in_every_package() -> None:
    """The leading parameter of every seam builder, which nothing checked.

    The sibling test below asserted "same shape everywhere" about
    `sql_for_install` -- the one function that was already uniform -- while
    `sql_for` had three shapes at once (review, 2026-09-03):

        tbc       (root_password, wsl_distro)
        vanilla   (db_root_password, wsl_distro)
        tortoise  (db_root_password, wsl_distro, container)

    A test that picks the uniform member of a family and reports the family
    uniform is worse than no test, because it retires the question. TBC was
    renamed to match the other three rather than the assertion widened.

    Trailing parameters are allowed to differ -- Tortoise's `container` is a
    capability the others do not offer, and a caller that ignores it is
    unaffected. What may not differ is the parameter a caller must pass.
    """
    from yulon.controller_wow_wotlk import accounts as wotlk_accounts
    from yulon.controller_wow_wotlk import maintenance as wotlk_maintenance

    leading: dict[str, str] = {}
    for game, mods in {
        "wow-tbc": (tbc_accounts, tbc_maintenance),
        "wow-vanilla": (vanilla_accounts, vanilla_maintenance),
        "wow-tortoise": (tortoise_accounts, tortoise_maintenance),
        "wow-wotlk": (wotlk_accounts, wotlk_maintenance),
    }.items():
        for mod in mods:
            for name in ("sql_for", "mysql_for"):
                builder = getattr(mod, name, None)
                if builder is None or not inspect.isfunction(builder):
                    continue
                first = next(iter(inspect.signature(builder).parameters))
                leading[game + "." + name] = first
    assert set(leading.values()) == {"db_root_password"}, (
        "the password parameter is spelled differently across packages, so a caller "
        "switching games has to switch keywords: " + repr(leading)
    )


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
    """The four packages' own factories, with `expected` READ rather than typed.

    This claimed to "discover" builders when it was written. It did not: three
    hand-written lists -- the packages, the function names, and the expected
    client per game -- behind a `checked >= 10` floor over an actual count of
    11. Slack of exactly one, so deleting a builder would still have passed. A
    review measured all of that (2026-09-03).

    Two things changed. `expected` now comes off the catalog entry instead of
    being a literal typed beside it, so this cannot pass with a seam bound to a
    value the catalog no longer declares. And the package list is checked
    against the catalog's own games, so a fifth game cannot be added without
    appearing here.

    What this no longer pretends to be is the general audit. That is
    `test_every_db_seam_binds_its_client.py`, which parses the source and finds
    every construction site anywhere -- including the ones in `ui/` and
    `install_wiring.py` that this test's "discovery" could never reach, because
    it only ever imported the controller packages. This file stays because it
    drives the factories FOR REAL and checks the seam they return, which an AST
    audit cannot do.
    """
    from yulon.catalog.catalog import load_catalog
    from yulon.controller_wow_wotlk import accounts as wotlk_accounts
    from yulon.controller_wow_wotlk import maintenance as wotlk_maintenance

    packages = {
        "wow-tbc": (tbc_accounts, tbc_maintenance),
        "wow-vanilla": (vanilla_accounts, vanilla_maintenance),
        "wow-tortoise": (tortoise_accounts, tortoise_maintenance),
        "wow-wotlk": (wotlk_accounts, wotlk_maintenance),
    }
    catalog = load_catalog()
    assert set(packages) == {
        game.id for game in catalog.games
    }, "a game was added to the catalog with no controller package listed here"

    checked = 0
    for game, mods in packages.items():
        native = catalog.get(game).install.native
        assert native is not None
        expected = native.db.client
        for mod in mods:
            for name in ("sql_for", "sql_for_install", "mysql_for"):
                builder = getattr(mod, name, None)
                if builder is None or not inspect.isfunction(builder):
                    continue
                first = next(iter(inspect.signature(builder).parameters))
                if first == "server_dir":
                    install = tmp_path / (game + "-" + name)
                    install.mkdir(exist_ok=True)
                    (install / ".db_password").write_text("hunter2", encoding="utf-8")
                    seam = builder(install)
                else:
                    seam = builder("pw")
                assert seam.client == expected, (
                    game
                    + " "
                    + name
                    + " built a seam with client="
                    + repr(seam.client)
                    + "; the catalog declares "
                    + repr(expected)
                )
                checked += 1
    assert checked == 11, (
        str(checked) + " builders were exercised, not 11. An exact count, not a floor: the "
        "floor this replaced had slack of one, so a deleted builder still passed it."
    )


def test_the_dump_the_restore_and_the_listing_all_use_the_declared_client() -> None:
    """All THREE argv-building paths of `DockerMysql`, not only the dump.

    The mutation review found that `client=self.client` could be dropped from
    `load_from()` (restore) or `databases()` (`SHOW DATABASES`) and survive the
    entire suite, because every test that checked argv went through
    `_dump_argv()`. Restore is where it hurts most: a backup that was taken
    fine and then cannot be put back.

    The probe is disabled through the real mechanism -- `docker_program()`
    returning None is what makes `_probe_client` give up -- so what gets
    measured is the fallback, which is the only case the binding decides.
    """
    from yulon.apply import _client_cache
    from yulon.catalog.catalog import load_catalog

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr("yulon.platform.docker_program", lambda: None)
        native = load_catalog().get("wow-tbc").install.native
        assert native is not None
        expected = native.db.client
        _client_cache.clear()
        seam = tbc_maintenance.mysql_for("pw")

        assert seam._dump_argv("realmd")[0] == expected + "-dump"

        seen = []

        def record(self, argv, **kwargs):
            seen.append(list(argv))
            raise RuntimeError("argv captured")

        monkey.setattr(type(seam), "_exec", record)
        for call in (lambda: seam.databases(), lambda: seam.load_from(io.BytesIO(b""))):
            try:
                call()
            except RuntimeError:
                pass
        assert len(seen) == 2, "one of the two methods never built an argv"
        assert all(argv[0] == expected for argv in seen), (
            "a DockerMysql method reached for "
            + repr([a[0] for a in seen])
            + " where the catalog declares "
            + repr(expected)
            + "; mariadb:11 ships no `mysql` binary"
        )
    finally:
        monkey.undo()
