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

from yulon import party
from yulon.catalog import native
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
    # Eight builders: `sql_for` and `mysql_for` in each of four packages. The
    # count is asserted because the loop SKIPS what it cannot find, so a set of
    # spellings is equally happy with eight builders and with one -- delete
    # `sql_for` from every package and `{"db_root_password"}` still holds
    # (review, 2026-09-03).
    assert len(leading) == 8, f"expected eight seam builders, found {sorted(leading)}"
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


_NO_UNINSTALL_YET = frozenset({"wow-tbc", "wow-tortoise"})
"""The games whose tab has no Uninstall yet, and the box that removes each name.

`wow-vanilla` LEFT this set in **8.9b**, the second and last of the two family
boxes, so both gated families are now wired. `wow-tbc` and `wow-tortoise` are
CMaNGOS forks too and inherit the same mechanism, but neither has a box yet --
so they are named here rather than left to be noticed, and this test fails the
day one of them is wired without this line being edited.
"""


def test_every_game_offers_the_whole_controller_surface_wotlk_does(tmp_path: Path) -> None:
    """7.9's "mirroring `controller_wow_wotlk`", asked of the OBJECT the view uses.

    The tempting comparison is the call sites -- diff the keyword arguments each
    `_for_<game>()` passes -- and it answers the wrong question. `prompt`,
    `prompt_precedes_answer` and `scheme` are bound INSIDE each package's own
    `console`/`accounts` module, so the UI does not pass them; `import_probe`
    and `reset_unfinished` are AzerothCore-only, because a CMaNGOS install has
    no one-shot import service to re-run. A spelling diff reports all five as
    missing features. They are not.

    What the view actually depends on is `ControllerServices`: one field per
    operation the Server tab can perform. So every field is required to arrive
    for every game, with exactly one documented exception -- `store` and
    `applier` are the module surface, and a game has them only if the catalog
    says `has_manifests` and a `manifests/<game>/` tree exists for it. That was
    `wow-wotlk` alone until 8.7b added `wow-tbc`; for the two that still have
    none the pair is legitimately None, and `_no_manifest_store()` warns if the
    catalog ever says otherwise. The exception is keyed on the ENTRY rather
    than on a list of game ids here, so the next game to get manifests needs no
    edit in this file -- and cannot silently keep the exemption either.

    Enumerated from the dataclass rather than listed here, so a sixteenth
    capability added to the view cannot be wired for WotLK and forgotten for
    the rest.

    **This proves COMPLETENESS, not correctness, and the distinction is not
    pedantry.** `is not None` says a callable was bound; it never says the
    callable is THIS GAME'S. Swap Tortoise's console, backup and restore for
    Vanilla's and every assertion below still holds -- what catches that is the
    per-game work in `test_controller_view.py`, which drives each seam and reads
    the schema and container it addressed. So the sixteenth capability is
    required by this test to be present, and required by those tests to be the
    right one; neither half is the other, and a reader who takes this file for
    the whole answer will wire a game to its neighbour's database.
    """
    from dataclasses import fields

    from yulon.catalog.catalog import load_catalog
    from yulon.ui.controller_view import ControllerServices

    module_surface = {"store", "applier", "module_sql"}
    every_field = {f.name for f in fields(ControllerServices)}
    assert module_surface < every_field, "the module fields are no longer called store/applier"
    # `module_sql` joined the surface when the module importer got a button
    # (8.7a). It is None for the same three games and for a second reason as
    # well as the manifests one: they name no one-shot import service, so there
    # is no container to run their modules' SQL in. Both halves are asserted
    # per game in `test_controller_view.py`; what is required here is only that
    # the exception stays deliberate.

    catalog = load_catalog()
    for game in sorted(g.id for g in catalog.games):
        entry = catalog.get(game)
        server_dir = tmp_path / game
        server_dir.mkdir()
        password_file = entry.install.password.file
        if password_file:
            (server_dir / password_file).write_text("hunter2", encoding="utf-8")

        services = ControllerServices.for_entry(entry, server_dir)
        absent = sorted(name for name in every_field if getattr(services, name, None) is None)
        # The FIRST documented exception, and since 8.7b it closes itself too.
        # `store`/`applier` used to be absent on "everything except wow-wotlk";
        # the entry now says which games ship manifests, so the exception is
        # read off the catalog and a game that gains a `manifests/<game>/` tree
        # fails this test until its store is wired. `module_sql` is absent on
        # all three CMaNGOS games either way, and for a different reason: they
        # name no one-shot import service, so there is no container to run
        # their modules' SQL in. WHOSE manifests arrived is not asked here --
        # that is `test_controller_view.test_a_tab_gets_a_manifest_store_
        # exactly_when_the_catalog_says_it_has_one`, which reads the store's
        # own game id.
        unstocked = module_surface if not entry.has_manifests else {"module_sql"}
        # The second documented exception, and it closes itself. 8.1a wired the
        # dashboard and the pre-stop snapshot for WotLK only, because the counts
        # need per-tree facts measured per tree; 8.1b, 8.1c and 8.1d each add
        # their own. Rather than hard-code which three games are behind, the
        # exception is tied to the fact that decides it — an entry with no
        # measured `observability` block has not had its 8.1 box. When that
        # block lands for a tree, this test fails until its wiring does too.
        unmeasured = {"dashboard", "log_snapshot"} if entry.observability is None else set()
        # The same self-closing shape for 8.2a's channel: an entry with no
        # measured `operations` block has not had its 8.2 box, and the day it
        # gets one this test fails until its wiring lands.
        # ...and, since 8.2e, for a channel there is nothing to set UP: an
        # attach channel has no listener, no port and no account, so a
        # `channel_setup` on that tree would be a state machine with no states.
        # Both facts are read from the entry, so neither can rot into a
        # hard-coded list of which games are behind.
        unwired = (
            {"channel_setup"}
            if entry.operations is None or entry.operations.channel == "attach"
            else set()
        )
        # And again for 8.3a's accounts. The fact that decides it is where this
        # core keeps a GM level: reading the wrong store does not fail, it
        # reports every account as level 0, so an entry without that measurement
        # has not had its 8.3 box. 8.3b, 8.3c and 8.3d each add their own, and
        # this test fails the day one of them lands without its wiring.
        unlisted = {"accounts"} if entry.accounts.level is None else set()
        # 8.5a's bot browser rides on the same measurement 8.1a's dashboard
        # does -- the bot marker -- so it is absent exactly where `observability`
        # is, and arrives with that tree's own 8.1 box.
        unbrowsed = {"bots"} if entry.observability is None else set()
        # 8.2e runs the other way: `console_probe` belongs to the ONE tree whose
        # channel is the console, so it is absent everywhere else -- including on
        # the reference. The fact that decides it is the channel the entry
        # declares, so a tree that ever gains an attach channel fails this until
        # its probe is wired, and a tree that loses one fails it until the seam
        # goes.
        console = entry.operations.channel if entry.operations is not None else None
        unprobed = {"console_probe"} if console != "attach" else set()
        # 8.4a's Characters tab, decided by the same kind of fact: where a tree
        # keeps a character's equipped items, and how many attachments one of
        # its mails carries. Both go out unchecked -- a column name into a
        # statement and a number onto a button -- so a tree without that
        # measurement has not had its 8.4 box, and this fails the day 8.4b,
        # 8.4c or 8.4d lands its catalog block without its wiring. It did:
        # 8.4c's block landed first and this was the RED that asked for the
        # `play=` line in `_for_vanilla`.
        unplayed = {"play"} if entry.play is None else set()
        # 8.9a's uninstall, and the one seam here whose absence is decided by a
        # BOX rather than by a fact in the entry -- because that is what the box
        # says. Uninstall is gated on two FAMILIES, not four games: 8.9a is
        # AzerothCore (wow-wotlk) and 8.9b is CMaNGOS (wow-vanilla), and
        # `phase8-decisions.md` calls that the one place owner answer 3's "one
        # box per family" is deliberately not followed, because the mechanism is
        # the compose project and the folder -- the engine's, not the
        # emulator's. So the entry carries nothing that could decide it, and the
        # list is spelled out with its expiry: wow-tbc and wow-tortoise share
        # wow-vanilla's engine and inherit the seam when a box wires them.
        unremovable = {"uninstall"} if game in _NO_UNINSTALL_YET else set()
        # 8.7a's update check counts git CLONES under `modules/`, so it belongs
        # to a game whose manifest set actually puts one there. wow-tbc ships a
        # `modules.json` with an empty `items` -- 8.7b's finding, a core that
        # compiles no modules -- so it has manifests and still has nothing that
        # could be behind, which is why this cannot ride on `has_manifests`.
        # Read off the store the services were handed, so the day a CMaNGOS game
        # ships its first module manifest this fails until its wiring lands.
        counted = services.store is not None and any(services.store.load_all("module"))
        # The four seams behind "Install from link…" and "Install from
        # folder…" (module-from-link, 2026-09-08) ride on the same fact: a
        # custom module is a C++ checkout or a copy under `modules/`, so it
        # belongs to a game whose manifest set already puts one there. On the
        # three CMaNGOS games a module is a conf key or a SQL mod (8.7b, 8.7c)
        # and there is no `modules/` folder for a clone or a copy to land in.
        custom = {
            "module_from_link",
            "module_from_folder",
            "module_install_custom",
            "module_forget",
        }
        uncounted = set() if counted else {"module_updates"} | custom
        # 8.6's My Party, and the one seam whose absence is decided by the
        # ENGINE rather than by a measurement. The route is `mod-ale`, an
        # AzerothCore Lua module hooking AzerothCore's command table, and the
        # bot it adds is `mod-playerbots`' `addclass`. A CMaNGOS tree has
        # neither, so there is nothing to wire and nothing a later box could
        # measure that would change it -- wiring it there would be a control
        # that sends AzerothCore's commands at a server that has never heard of
        # them. `InstallParty.for_entry_is_possible` is the same rule spelled
        # once in the module, and this reads it rather than repeating it, so a
        # tree that ever gains the route fails this until its wiring lands.
        unpartied = set() if party.InstallParty.for_entry_is_possible(entry) else {"my_party"}
        # T14's updates press, and the one seam here whose absence is decided by
        # the install PLAN. A phase carrying `rerun_on_marked` is a promise that
        # its own files are safe to re-apply to a server somebody is playing on,
        # made per phase beside the notes that argue it (T11) -- so a tree with
        # no such phase has nothing this control could apply, and a button there
        # would report success having done nothing. Read off the catalog through
        # the same function the wiring reads it with, so the day another entry's
        # plan gains one this fails until that game's tab offers the press. It is
        # absent on the REFERENCE too: AzerothCore imports through a compose
        # one-shot and carries no phase list for a flag to sit on.
        unupdatable = set() if native.update_phases(entry) else {"updates"}
        # T19's adopt press, gated on the SAME fact and read through the same
        # function, which is the point: the row it writes is a claim about the
        # databases that nothing takes back, and it buys something only where a
        # later press would then do what it cannot do now. So a game with no
        # flagged phase is missing both seams together or neither, and a day
        # when one is offered without the other is a real gap.
        unadoptable = set() if native.update_phases(entry) else {"adopt"}
        # T36's client-folder controls, absent here for EVERY game including
        # the reference -- and for a different kind of reason than the ones
        # above. `client_dir` and `set_client_dir` are not a per-game
        # capability a manifest or a catalog block measures; `set_client_dir`
        # is bound by `main.py` after the fact, over the one live `AppState` a
        # running window holds, and `client_dir` only ever carries something
        # when a caller passes one in. `ControllerServices.for_entry()` built
        # directly, the way this test and the CLI harness do, has neither a
        # window to bind the write seam into nor a folder to report, so both
        # read `None` here regardless of which game's entry this loop is on.
        unwindowed = {"client_dir", "set_client_dir"}
        allowed = (
            unstocked
            | unmeasured
            | unwired
            | unlisted
            | unbrowsed
            | unprobed
            | unplayed
            | unremovable
            | unpartied
            | uncounted
            | unupdatable
            | unadoptable
            | unwindowed
        )
        if game == "wow-wotlk":
            reference = unprobed | unupdatable | unadoptable | unwindowed
            assert (
                set(absent) == reference
            ), f"wow-wotlk is the reference and is missing {sorted(set(absent) - reference)}"
        else:
            assert set(absent) <= allowed, (
                f"{game} is missing {sorted(set(absent) - allowed)}, which is not the "
                "module surface and so is a real gap against wow-wotlk"
            )
            # And the exception has to be REAL. Without this the test would
            # pass just as happily on an implementation where nothing is ever
            # None -- including one that handed a game somebody else's
            # manifest store, which is the mistake `_no_manifest_store()`
            # exists to prevent.
            assert set(absent) == allowed, (
                f"{game} reports {absent} rather than the module surface; its entry does not "
                "say `has_manifests`, so a non-None store here means this game was handed "
                "somebody else's manifests"
            )
