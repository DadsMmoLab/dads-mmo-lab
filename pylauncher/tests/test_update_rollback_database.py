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

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.support_native import FakeSnapshot, Recorder
from tests.test_update_to_latest import (  # noqa: F401 - `_gated` is an autouse fixture
    NEW,
    OLD,
    _gated,
    _moving_heads,
    _spine,
)
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.installer import (
    InstallerError,
    InstallOptions,
    installer_for,
)

WOTLK = load_catalog().get("wow-wotlk")
TORTOISE = load_catalog().get("wow-tortoise")
TBC = load_catalog().get("wow-tbc")
VANILLA = load_catalog().get("wow-vanilla")

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


# -- Task 3: sources, then the copy, then the old build ----------------------


def _old_build_comes_back() -> Callable[[object, object], bool]:
    """`wait_ready`: the new build never reports ready, the old one does.

    The rollback's own wait answers differently from the first, so the restore
    really ran to the end and the old build is what is running afterwards.
    """
    answers = [False, True]

    def wait_ready(spec: object, ready: object) -> bool:
        return answers.pop(0) if len(answers) > 1 else answers[0]

    return wait_ready


def _press(
    tmp_path: Path,
    entry: CatalogEntry,
    *,
    copy: Callable[[Recorder], FakeSnapshot] = FakeSnapshot,
    **overrides: object,
) -> tuple[Recorder, Path, native.StagedInstaller, FakeSnapshot, list[str], Exception | None]:
    """An installed `entry`, one Update to latest pressed on it, and everything it left."""
    rec, server_dir, make = _spine(tmp_path, entry)
    made = make(**overrides)
    fake = copy(rec)
    made._snapshot = fake
    said: list[str] = []
    raised: Exception | None = None
    try:
        for line in made.update_to_latest(InstallOptions(server_dir=server_dir)):
            said.append(line)
    except InstallerError as exc:
        raised = exc
    return rec, server_dir, made, fake, said, raised


def _at(calls: list[str], wanted: str, start: int = 0) -> int:
    """Where `wanted` (a call or a call prefix) first happened at or after `start`."""
    for index in range(start, len(calls)):
        if calls[index] == wanted or calls[index].startswith(wanted):
            return index
    raise AssertionError(f"{wanted!r} never happened after {start}: {calls}")


def test_a_rolled_back_update_puts_tags_then_sources_then_the_copy_back_before_the_old_build(
    tmp_path: Path,
) -> None:
    """The order is the fix: the old build must not start on the new module's SQL or tables.

    Until T217 the sources went back only after `rebuild()` had raised, so after
    the old build had already started -- on the new mod-playerbots folder, whose
    updater it runs -- and the database was never put back at all.
    """
    rec, server_dir, _made, fake, _said, raised = _press(
        tmp_path, WOTLK, wait_ready=_old_build_comes_back()
    )
    assert raised is not None and not isinstance(raised, native.ServersLeftStopped)
    calls = rec.calls
    # The new build: stopped, copied with its servers down, then started.
    stop_new = _at(calls, "stop_servers")
    copied = _at(calls, "snapshot:acore_playerbots", stop_new)
    start_new = _at(calls, "recreate", copied)
    # The rollback: stopped, tags back, module then core back, the copy back, old build.
    stop_failed = _at(calls, "stop_servers", start_new)
    tags_back = _at(calls, "tag:", stop_failed)
    module_back = _at(calls, "restore:mod-playerbots->", tags_back)
    core_back = _at(calls, "restore:server->", module_back)
    copy_back = _at(calls, "put-back:acore_playerbots", core_back)
    _at(calls, "recreate", copy_back)
    assert calls.count("recreate") == 2, calls
    assert fake.put_back_calls == fake.taken, "the copy taken is the copy put back"
    assert _moving_heads(rec, server_dir, _made) == {OLD}


def test_the_rollback_says_the_database_went_back_and_where_the_new_builds_copy_is(
    tmp_path: Path,
) -> None:
    _rec, _dir, _made, fake, said, raised = _press(
        tmp_path, WOTLK, wait_ready=_old_build_comes_back()
    )
    text = str(raised)
    assert "put back and is running again" in text
    assert "acore_playerbots as it was just before the new build started" in text
    # The copy's own file is named when it is taken, so the player can find it.
    assert any(fake.taken[0].files[0].name in line for line in said), said
    assert "pre-restore_acore_playerbots.sql" in text, "the database as the new build left it"
    assert "is NOT put back" not in text


def test_a_copy_that_cannot_go_back_leaves_the_servers_stopped_and_names_the_file(
    tmp_path: Path,
) -> None:
    """The old build must not start on the database the new one changed (owner rule)."""
    rec, server_dir, made, fake, _said, raised = _press(
        tmp_path,
        WOTLK,
        copy=lambda rec: FakeSnapshot(rec, put_back_error=InstallerError("mysql exited 1")),
        wait_ready=_old_build_comes_back(),
    )
    assert isinstance(raised, native.ServersLeftStopped), raised
    text = str(raised)
    assert rec.calls.count("recreate") == 1, "the old build was not started"
    assert fake.taken[0].files[0].name in text
    assert "Maintenance" in text and "Restore" in text
    assert "mysql exited 1" in text
    assert "agree again" not in text and "is running" not in text
    assert _moving_heads(rec, server_dir, made) == {OLD}, "the sources went back first"


def test_a_copy_that_cannot_be_taken_never_starts_the_new_build(tmp_path: Path) -> None:
    rec, server_dir, made, fake, _said, raised = _press(
        tmp_path,
        WOTLK,
        copy=lambda rec: FakeSnapshot(rec, take_error=InstallerError("disk full")),
        wait_ready=_old_build_comes_back(),
    )
    assert raised is not None and not isinstance(raised, native.ServersLeftStopped)
    text = str(raised)
    assert "could not copy acore_playerbots before starting the new build" in text
    assert "disk full" in text and "did not start it" in text
    # One recreate, and it is the old build's: the new one never started.
    assert rec.calls.count("recreate") == 1, rec.calls
    assert _at(rec.calls, "restore:server->") < _at(rec.calls, "recreate")
    assert fake.put_back_calls == []
    assert _moving_heads(rec, server_dir, made) == {OLD}
    assert "is NOT put back" not in text


def test_tortoise_copies_login_and_characters_and_says_world_is_not_put_back(
    tmp_path: Path,
) -> None:
    rec, _dir, _made, fake, _said, raised = _press(
        tmp_path, TORTOISE, wait_ready=_old_build_comes_back()
    )
    assert [copy.databases for copy in fake.taken] == [("tw_logon", "tw_char")]
    assert "put-back:tw_logon,tw_char" in rec.calls
    text = str(raised)
    assert "tw_world" in text and "not copied" in text


@pytest.mark.parametrize("entry", [TBC, VANILLA], ids=lambda entry: entry.id)
def test_a_family_whose_new_build_changes_nothing_copies_nothing_and_says_what_it_said(
    tmp_path: Path, entry: CatalogEntry
) -> None:
    """No copy, no put-back, and the rollback's database sentence stays as it was."""
    rec, server_dir, made, fake, _said, raised = _press(
        tmp_path, entry, wait_ready=_old_build_comes_back()
    )
    assert fake.taken == [] and fake.put_back_calls == []
    assert not [call for call in rec.calls if call.startswith(("snapshot:", "put-back:"))]
    text = str(raised)
    assert "What the new build wrote into the database on its first start, if anything, is NOT" in (
        text
    )
    assert text.endswith(native.SOURCES_PUT_BACK_NOTE)
    # The sources still go back before the old build starts, on every family.
    restores = [index for index, call in enumerate(rec.calls) if call.startswith("restore:")]
    assert restores and max(restores) < len(rec.calls) - 1 - rec.calls[::-1].index("recreate")
    assert _moving_heads(rec, server_dir, made) == {OLD}


def test_an_update_that_comes_up_keeps_its_copy_and_forgets_the_older_ones(
    tmp_path: Path,
) -> None:
    rec, server_dir, made, fake, said, raised = _press(tmp_path, WOTLK)
    assert raised is None, raised
    assert [copy.databases for copy in fake.taken] == [("acore_playerbots",)]
    assert fake.put_back_calls == []
    assert "prune" in rec.calls and _at(rec.calls, "prune") > _at(rec.calls, "recreate")
    assert _moving_heads(rec, server_dir, made) == {NEW}
    assert any("before starting the new build" in line for line in said)


def test_a_mixed_tags_record_refuses_the_update_before_anything_is_fetched(
    tmp_path: Path,
) -> None:
    """Decision 9: the route asks the start refusal itself, now that it always hands work."""
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    assert native.owe_start(server_dir) == ""
    made = make()
    made._snapshot = FakeSnapshot(rec)
    with pytest.raises(InstallerError) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert native.REBUILD_OWED_REFUSAL in str(raised.value)
    assert rec.clones == [] and "snapshot:" not in " ".join(rec.calls)
