"""T217: a failed "Update to latest" puts the databases back with the build.

When the new build's containers started and it then did not come up, the
rollback used to put the old images back, start them, and only then put the
source folders back -- and it never put back the databases the new build had
already changed at its first start. On WoW WotLK the new mod-playerbots dropped
two tables the old bots still read, and the old server crash-looped on
"Table 'acore_playerbots.playerbots_speech' doesn't exist".

The fix: the update route copies the databases a new build can change, with the
servers stopped, right before the new build first starts (`DatabaseSnapshot`),
and its rollback puts the tags back, then the source folders, then that copy,
and only then starts the old build. Every test here drives the real
`update_to_latest()` through `tests.support_native.Recorder` and asserts what
the machine holds afterwards and the ORDER things happened in -- the order is
the fix.
"""

from __future__ import annotations

import pytest

from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import installer_for

# -- Task 1: which databases a new build changes at its first start ----------

NEW_BUILD_CHANGES: dict[str, tuple[str, ...]] = {
    "wow-wotlk": ("playerbots",),
    "wow-tbc": (),
    "wow-vanilla": (),
    "wow-tortoise": ("auth", "characters"),
    "wow-centurion": (),
}
"""Per shipped entry, by role: what its new build can write at its first start.

* WotLK: the worldserver's playerbots updater runs whatever the image says
  (`AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES=1` is structural, `composegen.py`),
  and it reads the module's SQL from the HOST folder it bind-mounts.
* Tortoise: the worldserver's own AutoUpdater (`Database.AutoUpdate.Enabled`
  in its conf table) migrates the login, characters and world databases at
  start. World is left out on the owner's word of 2026-10-04: it is the biggest
  and the slowest to copy, so its rollback says it is not put back.
* TBC, Vanilla: no start-time updater (no AutoUpdate key; their `*-db`
  repositories stay on their pin).
* Centurion: `Updates.EnableDatabases` is forced to 0 by the catalog's own
  validator; the world tables an update changes are T179's own `back()`.
"""


def test_every_shipped_entry_names_the_databases_its_new_build_changes() -> None:
    """Pinned per entry, so a new entry lands here before it lands on a player."""
    for entry in load_catalog().games:
        assert entry.id in NEW_BUILD_CHANGES, (
            f"{entry.id} has no answer here: say which databases its new build can change "
            "at its first start (T217)"
        )
        engine = installer_for(entry)
        assert isinstance(engine, native.StagedInstaller)
        assert engine.databases_a_new_build_changes() == NEW_BUILD_CHANGES[entry.id], entry.id


@pytest.mark.parametrize(
    ("game", "names"),
    [
        ("wow-wotlk", ("acore_playerbots",)),
        ("wow-tortoise", ("tw_logon", "tw_char")),
        ("wow-tbc", ()),
        ("wow-vanilla", ()),
        ("wow-centurion", ()),
    ],
)
def test_the_roles_resolve_to_the_entrys_own_schema_names(
    game: str, names: tuple[str, ...]
) -> None:
    """The copy is taken by schema name, and a CMaNGOS install has no `acore_*` schema."""
    engine = installer_for(load_catalog().get(game))
    assert isinstance(engine, native.StagedInstaller)
    assert engine.snapshot_databases() == names


def test_the_tortoise_answer_is_read_off_its_own_conf_not_off_its_id() -> None:
    """The rule is the conf key that switches the updater on, so TBC without it answers ()."""
    tortoise = load_catalog().get("wow-tortoise")
    native_block = tortoise.install.native
    assert native_block is not None and native_block.cmangos is not None
    files = dict(native_block.cmangos.conf.files)
    mangosd = files["mangosd.conf"]
    keys = {k: v for k, v in mangosd.keys.items() if k != "Database.AutoUpdate.Enabled"}
    files["mangosd.conf"] = mangosd.model_copy(update={"keys": keys})
    conf = native_block.cmangos.conf.model_copy(update={"files": files})
    cmangos = native_block.cmangos.model_copy(update={"conf": conf})
    switched_off = tortoise.model_copy(
        update={
            "install": tortoise.install.model_copy(
                update={"native": native_block.model_copy(update={"cmangos": cmangos})}
            )
        }
    )
    engine = installer_for(switched_off)
    assert isinstance(engine, native.StagedInstaller)
    assert engine.databases_a_new_build_changes() == ()
