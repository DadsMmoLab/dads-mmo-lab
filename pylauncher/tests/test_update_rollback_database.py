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

import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from tests.support_native import SNAPSHOT_STAMP, FakeSnapshot, Recorder
from tests.test_update_to_latest import (  # noqa: F401 - `_gated` is an autouse fixture
    ABORTED_AFTER_READY,
    EARLY_RETURNS,
    NEW,
    OLD,
    _gated,
    _moving_heads,
    _recreate_given_up,
    _spine,
)
from yulon import docker, git, server_build_presses
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.installer import (
    InstallerError,
    InstallOptions,
    RollbackNotDone,
    WorldStoppedAfterReadyError,
    installer_for,
)
from yulon.docker import AttachedRun

WOTLK = load_catalog().get("wow-wotlk")
TORTOISE = load_catalog().get("wow-tortoise")
TBC = load_catalog().get("wow-tbc")

WOTLK_COPY = ("acore_auth", "acore_characters", "acore_world", "acore_playerbots")
"""What a WotLK update copies: the playerbots database (T217) and the core's three (T220)."""
WOTLK_LISTED = "acore_auth, acore_characters, acore_world and acore_playerbots"
VANILLA = load_catalog().get("wow-vanilla")

# -- Task 1: which databases a new build changes at its first start ----------

NEW_BUILD_CHANGES: dict[str, tuple[str, ...]] = {
    "wow-wotlk": ("auth", "characters", "world", "playerbots"),
    "wow-unbound": ("auth", "characters", "world", "playerbots"),  # WotLK's family and pin
    "wow-tbc": (),
    "wow-vanilla": (),
    "wow-tortoise": ("auth", "characters"),
    "wow-centurion": (),
}
"""Per shipped entry, by role: what its new build can write at its first start.

* WotLK: the worldserver's playerbots updater runs whatever the image says
  (`AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES=1` is structural, `composegen.py`),
  and it reads the module's SQL from the HOST folder it bind-mounts. And since
  T220 the update itself applies the new core's own auth, characters and world
  updates before the first start, so those three are copied first too.
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
        ("wow-wotlk", WOTLK_COPY),
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
    copied = _at(calls, f"snapshot:{','.join(WOTLK_COPY)}", stop_new)
    start_new = _at(calls, "recreate", copied)
    # The rollback: stopped, tags back, module then core back, the copy back, old build.
    stop_failed = _at(calls, "stop_servers", start_new)
    tags_back = _at(calls, "tag:", stop_failed)
    module_back = _at(calls, "restore:mod-playerbots->", tags_back)
    core_back = _at(calls, "restore:server->", module_back)
    copy_back = _at(calls, f"put-back:{','.join(WOTLK_COPY)}", core_back)
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
    assert (
        f"{WOTLK_LISTED} were replaced with the copy taken just before the new build started"
        in text
    )
    # The copy's own file is named when it is taken, so the player can find it.
    assert any(fake.taken[0].files[0].name in line for line in said), said
    assert "after-new-build_acore_playerbots.sql" in text, "the database as the new build left it"
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


def test_a_copy_that_fails_its_check_drops_nothing_and_never_sends_to_a_restore_of_it(
    tmp_path: Path,
) -> None:
    """Cold review: an incomplete copy drops nothing; the servers stay stopped; it is named.

    Maintenance's Restore refuses an incomplete copy, so the sentence must not send
    the player there with it.
    """
    from yulon.catalog.snapshot import CopyNotUsable

    rec, server_dir, made, fake, _said, raised = _press(
        tmp_path,
        WOTLK,
        copy=lambda rec: FakeSnapshot(
            rec,
            put_back_error=CopyNotUsable(
                "the copy x.sql is not complete (cut short), so nothing was dropped or put back"
            ),
        ),
        wait_ready=_old_build_comes_back(),
    )
    assert isinstance(raised, native.ServersLeftStopped), raised
    text = str(raised)
    assert rec.calls.count("recreate") == 1, "the old build was not started"
    assert "nothing was dropped or put back" in text
    assert fake.taken[0].files[0].name in text
    assert "as the new build left them" in text
    assert "choose" not in text, "no Restore of a copy Maintenance would refuse"
    assert "prune" not in rec.calls


def test_the_rollback_says_the_tables_the_new_build_added_were_dropped(tmp_path: Path) -> None:
    _rec, _dir, _made, _fake, said, raised = _press(
        tmp_path, WOTLK, wait_ready=_old_build_comes_back()
    )
    assert raised is not None
    putting = [line for line in said if line.startswith("Putting ")]
    assert len(putting) == 1 and "tables the new build added are dropped" in putting[0], said
    assert "the tables the new build added were dropped" in str(raised)


def test_an_update_with_the_images_gone_compiles_nothing_and_takes_no_copy(
    tmp_path: Path,
) -> None:
    """Cold review item 5: T170's no-rollback path is the Rebuild press's alone.

    `update_to_latest()` never passes `missing_images_ok`, so with the images gone
    it is refused before the compile and before any copy, and a new build that
    ran with no build to roll back to and a copy left unput is not reachable here.
    """
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    rec.images = False
    made = make(wait_ready=lambda spec, ready: False)
    fake = FakeSnapshot(rec)
    made._snapshot = fake
    with pytest.raises(InstallerError) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert "this press does not compile without one" in str(raised.value)
    assert "build" not in rec.calls and "recreate" not in rec.calls
    assert fake.taken == [] and fake.put_back_calls == []


def test_a_stop_during_the_core_updates_names_the_press_not_the_install(tmp_path: Path) -> None:
    """Cold review: the Stop arrived during an update, and "the install was stopped" is untrue."""
    from yulon.catalog.families import azerothcore

    rec, server_dir, make = _spine(tmp_path, WOTLK)
    made = make(wait_ready=_old_build_comes_back())
    made._snapshot = FakeSnapshot(rec)
    stop = threading.Event()
    inner = made._seams.one_shot

    def one_shot(
        service: str, where: Path, *, sink: object = None, cancel: object = None
    ) -> AttachedRun:
        stop.set()
        return inner(service, where, sink=sink, cancel=cancel)

    made._seams = replace(made._seams, one_shot=one_shot)
    with pytest.raises(InstallerError) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir), cancel=stop))
    text = str(raised.value)
    assert f"{server_build_presses.UPDATE_TO_LATEST} was stopped" in text, text
    assert "the install was stopped" not in text
    assert azerothcore.CORE_UPDATES_NOTE in text


def test_a_copy_that_cannot_be_taken_never_starts_the_new_build(tmp_path: Path) -> None:
    rec, server_dir, made, fake, _said, raised = _press(
        tmp_path,
        WOTLK,
        copy=lambda rec: FakeSnapshot(rec, take_error=InstallerError("disk full")),
        wait_ready=_old_build_comes_back(),
    )
    assert raised is not None and not isinstance(raised, native.ServersLeftStopped)
    text = str(raised)
    assert f"could not copy {WOTLK_LISTED} before starting the new build" in text
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
    assert [copy.databases for copy in fake.taken] == [WOTLK_COPY]
    assert fake.put_back_calls == []
    assert "prune" in rec.calls and _at(rec.calls, "prune") > _at(rec.calls, "recreate")
    assert _moving_heads(rec, server_dir, made) == {NEW}
    assert any("before starting the new build" in line for line in said)


def _asked_ready(rec: Recorder, answers: list[bool]) -> Callable[[object, object], bool]:
    """`wait_ready` that records each ask in `rec.calls` and answers from `answers` in turn."""

    def wait_ready(spec: object, ready: object) -> bool:
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        rec.calls.append(f"ready?{'yes' if answer else 'no'}")
        return answer

    return wait_ready


def test_a_rollback_forgets_older_copies_only_after_the_old_build_reported_ready(
    tmp_path: Path,
) -> None:
    """Live proof 2026-10-05, item 5: the copies went in the second the old build was recreated.

    It then crash-looped, and the last copies holding the tables it needed were gone.
    """
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    made = make(wait_ready=_asked_ready(rec, [False, True]))
    made._snapshot = FakeSnapshot(rec)
    with pytest.raises(InstallerError) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert "is running again" in str(raised.value)
    old_up = _at(rec.calls, "ready?yes")
    assert _at(rec.calls, "put-back:") < old_up
    assert rec.calls.count("prune") == 1 and _at(rec.calls, "prune") > old_up, rec.calls


def test_an_old_build_that_does_not_come_up_after_the_put_back_keeps_every_copy_and_stops(
    tmp_path: Path,
) -> None:
    """Live proof 2026-10-05, item 5: no copy is forgotten, the servers do not crash-loop.

    The sentence says the old build did not come up and names the copies that can
    restore it -- never "starts on the databases it knows" or "agree again".
    """
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    made = make(wait_ready=_asked_ready(rec, [False, False]))
    fake = FakeSnapshot(rec)
    made._snapshot = fake
    with pytest.raises(native.ServersLeftStopped) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    text = str(raised.value)
    assert "prune" not in rec.calls, rec.calls
    old_down = _at(rec.calls, "ready?no", _at(rec.calls, "put-back:"))
    _at(rec.calls, "stop_servers", old_down)
    assert "recreate" not in rec.calls[old_down:], "nothing started again after the stop"
    assert "did not come up either" in text
    for path in (*fake.taken[0].files, *_safety_of(fake)):
        assert path.name in text, path
    assert "Maintenance" in text
    assert "starts on the databases it knows" not in text
    assert "agree again" not in text and "is running" not in text
    assert text.endswith(native.SOURCES_PUT_BACK_DATABASE_NOT_NOTE)
    assert _moving_heads(rec, server_dir, made) == {OLD}


def _safety_of(fake: FakeSnapshot) -> tuple[Path, ...]:
    from yulon.catalog.snapshot import ROLLBACK_SAFETY_LABEL

    copy = fake.taken[0]
    return tuple(
        copy.directory / f"{SNAPSHOT_STAMP}_{ROLLBACK_SAFETY_LABEL}_{name}.sql"
        for name in copy.databases
    )


def _copies_named(fake: FakeSnapshot) -> tuple[str, str]:
    """The copy's files and the rollback's safety files, as the sentence names them."""
    copy = fake.taken[0]
    files = ", ".join(f"backups/{path.name}" for path in copy.files)
    safety = ", ".join(f"backups/{path.name}" for path in _safety_of(fake))
    return files, safety


def test_an_old_build_that_did_not_come_up_is_said_stopped_in_exactly_these_words(
    tmp_path: Path,
) -> None:
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    made = make(wait_ready=_asked_ready(rec, [False, False]))
    fake = FakeSnapshot(rec)
    made._snapshot = fake
    with pytest.raises(native.ServersLeftStopped) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    files, safety = _copies_named(fake)
    assert (
        f"Its source folders and {WOTLK_LISTED} were put back, and the build from before this "
        "update still did not come up, so its servers were stopped. Every copy is kept: "
        f"{WOTLK_LISTED} as they were just before the new build started are in {files}, as the "
        f"new build left them in {safety}. Restore the one you want on Maintenance (it works "
        "with the server stopped), then press Start."
    ) in str(raised.value)
    assert "next older" not in str(raised.value), "no older copy to send the player to"


def test_the_old_builds_failure_is_said_once_when_it_reads_as_the_new_ones(
    tmp_path: Path,
) -> None:
    """Re-live 2026-10-05: the crash-loop sentence was printed twice in a row, word for word."""
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    made = make(wait_ready=_asked_ready(rec, [False, False]))
    made._snapshot = FakeSnapshot(rec)
    with pytest.raises(native.ServersLeftStopped) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    text = str(raised.value)
    assert text.count("never reported ready") == 1, text
    assert "but it did not come up either, for the same reason." in text


def test_an_old_build_that_cannot_be_stopped_after_failing_is_never_said_to_be_stopped(
    tmp_path: Path,
) -> None:
    """Scoped re-review of c5bf1b67: a failed stop read "were stopped" and "stays stopped" too."""
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    made = make(wait_ready=_asked_ready(rec, [False, False]))
    fake = FakeSnapshot(rec)
    made._snapshot = fake

    def refuse(control: object) -> None:
        if rec.calls.count("recreate") == 2:
            raise docker.DockerCommandError("the daemon did not answer the stop")

    rec.on_stop_servers = refuse
    with pytest.raises(native.OldBuildNotStopped) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert not isinstance(raised.value, native.ServersLeftStopped)
    text = str(raised.value)
    files, safety = _copies_named(fake)
    assert (
        f"Its source folders and {WOTLK_LISTED} were put back, and the build from before this "
        "update still did not come up. Yu'lon could not stop its servers (the daemon did not "
        "answer the stop), so they may still be restarting: press Stop on the Server tab. Every "
        f"copy is kept: {WOTLK_LISTED} as they were just before the new build started are in "
        f"{files}, as the new build left them in {safety}. Once it is stopped, restore the one "
        "you want on Maintenance, then press Start."
    ) in text
    assert text.endswith(
        " The source folders were put back on the commits they were on, so what is on disk is "
        "the build that was put back."
    )
    assert "were stopped" not in text and "stays stopped" not in text and "STOPPED" not in text
    assert "agree again" not in text
    assert "prune" not in rec.calls


def test_an_old_build_that_fails_another_way_says_how(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Said once only when it is the same sentence; a different failure is said in full."""
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    made = make()
    made._snapshot = FakeSnapshot(rec)
    failures = iter(["the new world aborted on a missing table.", "the old world crashed."])

    def wait_for_ready(ctx: object, ready: object, **_kw: object) -> Iterator[str]:
        raise InstallerError(next(failures))
        yield ""  # pragma: no cover - a generator, like the real one

    monkeypatch.setattr(made, "wait_for_ready", wait_for_ready)
    with pytest.raises(native.ServersLeftStopped) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    text = str(raised.value)
    assert "for the same reason" not in text
    assert "did not come up either: the old world crashed." in text


def test_older_copies_are_named_only_when_there_are_any(tmp_path: Path) -> None:
    """Scoped re-review: "the copies earlier updates took" was said on a first update too."""
    from yulon.catalog.snapshot import SNAPSHOT_LABEL

    rec, server_dir, make = _spine(tmp_path, WOTLK)
    backups = server_dir / "sql_scripts" / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    oldest = backups / f"20261002_090000_{SNAPSHOT_LABEL}_acore_playerbots.sql"
    older = backups / f"20261003_090000_{SNAPSHOT_LABEL}_acore_playerbots.sql"
    for path in (oldest, older):
        path.write_text("-- dump\n", encoding="utf-8")
    (backups / "20261002_090000_acore_characters.sql").write_text("-- mine\n", encoding="utf-8")
    made = make(wait_ready=_asked_ready(rec, [False, False]))
    made._snapshot = FakeSnapshot(rec)
    with pytest.raises(native.ServersLeftStopped) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    text = str(raised.value)
    # Re-live 2026-10-05: named by file, newest first, and what to do if the newest
    # copy is itself one a restarted old world had already changed.
    assert (
        "Copies earlier updates took before their new build started are kept too, newest "
        f"first: backups/{older.name}, backups/{oldest.name}. If Restore of the newest copy "
        "does not bring the old build up, restore the next older copy listed."
    ) in text
    assert "20261002_090000_acore_characters.sql" not in text, "the player's own backup is not one"


def test_a_tortoise_old_build_that_crash_loops_is_stopped_at_its_restart_with_its_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scoped re-review: Tortoise's stop waits for a world to load, and a crash loop never does.

    The stop ends the wait when the world restarts (T159's control), and while it
    waits the press says the hint that goes with "Stop now anyway".
    """
    import subprocess

    spec = TORTOISE.container_spec()
    assert spec.stop_waits_for_load
    runs = iter(["2026-10-05T01:00:00Z", "2026-10-05T01:00:09Z"])
    monkeypatch.setattr(
        docker,
        "container_state",
        lambda name, **_kw: docker.ContainerState(status="running", started_at=next(runs)),
    )
    monkeypatch.setattr(
        docker,
        "exec_output",
        lambda *_a, **_kw: subprocess.CompletedProcess([], 0, "SigCgt:\t0000000000000000\n", ""),
    )
    monkeypatch.setattr(docker, "_pause", lambda control, seconds: None)
    # T600: a deaf world's log tail is read to see whether it ends on a failed update.
    monkeypatch.setattr(docker, "_logs", lambda *_a, **_kw: "Loading maps...\n")
    rec, server_dir, make = _spine(tmp_path, TORTOISE)
    made = make(wait_ready=_asked_ready(rec, [False, False]))
    made._snapshot = FakeSnapshot(rec)
    heard: list[str] = []

    def stop(control: docker.StopControl | None) -> None:
        if control is not None and rec.calls.count("ready?no") == 2:
            for line in docker.world_load_steps(spec, control):
                heard.append(line)
                if control.say is not None:
                    control.say(line)

    rec.on_stop_servers = stop
    said: list[str] = []
    with pytest.raises(native.ServersLeftStopped):
        for line in made.update_to_latest(InstallOptions(server_dir=server_dir)):
            said.append(line)
    assert heard == [docker.WORLD_STILL_LOADING, docker.WORLD_RESTARTED_STOPPING]
    assert native.OLD_BUILD_WAIT_HINT in said
    assert said.index(native.OLD_BUILD_WAIT_HINT) == said.index(docker.WORLD_STILL_LOADING) + 1


def test_the_stop_wait_of_a_world_stuck_at_a_failed_update_kills_it_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T600: the failed build's world sits in a read after a failed update and ignores the stop.

    Every stop path (the rollback's included) used to say "still loading" until "Stop now
    anyway"; now it says which update failed and what MariaDB said, kills the stuck world and
    goes on.

    Mutation: drop the `_stuck_at_a_failed_update()` check in `world_load_steps()`, and the
    steps are the loading hint, again and again.
    """
    import subprocess

    spec = TORTOISE.container_spec()
    monkeypatch.setattr(
        docker,
        "container_state",
        lambda name, **_kw: docker.ContainerState(
            status="running", started_at="2026-10-05T01:00:00Z"
        ),
    )
    monkeypatch.setattr(
        docker,
        "exec_output",
        lambda *_a, **_kw: subprocess.CompletedProcess([], 0, "SigCgt:\t0000000000000000\n", ""),
    )
    monkeypatch.setattr(docker, "_pause", lambda control, seconds: None)
    monkeypatch.setattr(
        docker,
        "_logs",
        lambda *_a, **_kw: (
            "[1062] Duplicate entry '44070' for key 'PRIMARY'\n"
            "[DB Auto-Updater] Migration 20260903063722_world with hash AB12 failed to apply.\n"
        ),
    )
    killed: list[str] = []
    monkeypatch.setattr(docker, "kill_container", lambda name, **_kw: killed.append(name))

    heard = list(docker.world_load_steps(spec, docker.StopControl()))

    assert killed == [spec.world]
    assert len(heard) == 1 and "20260903063722_world.sql" in heard[0]
    assert docker.WORLD_STILL_LOADING not in heard


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


# -- Task 4: the database follows the sources on T197's exits too ------------


def test_a_kept_build_keeps_the_database_it_changed_and_names_the_copy_as_not_needed(
    tmp_path: Path,
) -> None:
    """T71's keep: the new build came up and stopped on its data, so it is what runs."""
    rec, server_dir, made, fake, _said, raised = _press(
        tmp_path, WOTLK, world_output=lambda spec: ABORTED_AFTER_READY
    )
    assert isinstance(raised, WorldStoppedAfterReadyError), raised
    assert len(fake.taken) == 1 and fake.put_back_calls == []
    text = str(raised)
    assert f"The copy of {WOTLK_LISTED} taken before it started is kept in" in text
    assert fake.taken[0].files[0].name in text and "it was not needed" in text
    assert _moving_heads(rec, server_dir, made) == {NEW}
    assert "prune" in rec.calls, "the kept build's copy is the last one; older ones go"


@pytest.mark.parametrize("how", ["stop-refused", "name-refused", "retag-refused"])
def test_a_rollback_that_stops_early_keeps_the_database_with_the_new_build(
    tmp_path: Path, how: str
) -> None:
    """`RollbackNotDone`: the tags still name the new build, so its sources and database stay."""
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    rec.ready = False
    made = make(**EARLY_RETURNS[how](rec))
    fake = FakeSnapshot(rec)
    made._snapshot = fake
    with pytest.raises(RollbackNotDone) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert len(fake.taken) == 1 and fake.put_back_calls == []
    assert _moving_heads(rec, server_dir, made) == {NEW}
    text = str(raised.value)
    assert "it was not needed" in text and fake.taken[0].files[0].name in text
    assert text.endswith(native.SOURCES_LEFT_NOTE)
    # No build reported ready, so no older copy is forgotten (live proof, item 5).
    assert "prune" not in rec.calls


def test_mixed_tags_put_the_sources_back_then_the_copy_and_still_refuse_every_start(
    tmp_path: Path,
) -> None:
    """Mixed: no one build on the tags, so the sources go back -- and the database with them."""
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    rec.ready = False
    made = make(**EARLY_RETURNS["mixed"](rec))
    fake = FakeSnapshot(rec)
    made._snapshot = fake
    with pytest.raises(RollbackNotDone) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert raised.value.mixed is True
    core_back = _at(rec.calls, "restore:server->")
    _at(rec.calls, f"put-back:{','.join(WOTLK_COPY)}", core_back)
    assert rec.calls.count("recreate") == 1, "nothing started after the rollback"
    assert _moving_heads(rec, server_dir, made) == {OLD}
    assert native.owed_start_refusal(server_dir) == native.REBUILD_OWED_REFUSAL
    assert "acore_playerbots" in str(raised.value)


def test_mixed_tags_whose_copy_will_not_go_back_name_the_file_to_restore(tmp_path: Path) -> None:
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    rec.ready = False
    made = make(**EARLY_RETURNS["mixed"](rec))
    fake = FakeSnapshot(rec, put_back_error=InstallerError("mysql exited 1"))
    made._snapshot = fake
    with pytest.raises(RollbackNotDone) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    text = str(raised.value)
    assert fake.taken[0].files[0].name in text and "Maintenance" in text
    assert "prune" not in rec.calls, "the copy named as the one to restore is not removed"


def test_a_press_given_up_before_any_server_stopped_takes_no_copy(tmp_path: Path) -> None:
    """`touched=False`: the stop was given up in the load wait, so `forward()` never ran."""
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    _recreate_given_up(rec)
    made = make()
    fake = FakeSnapshot(rec)
    made._snapshot = fake
    with pytest.raises(InstallerError):
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert fake.taken == [] and fake.put_back_calls == []


def test_a_reader_that_goes_away_after_the_copy_puts_nothing_back_and_yields_nothing(
    tmp_path: Path,
) -> None:
    """`GeneratorExit` after `take`: no restore (it would need a yield), and no traceback."""
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    made = make()
    fake = FakeSnapshot(rec)
    made._snapshot = fake
    press = made.update_to_latest(InstallOptions(server_dir=server_dir))
    for line in press:
        if line.startswith(f"Copied {WOTLK_LISTED}"):
            break
    press.close()
    assert len(fake.taken) == 1 and fake.put_back_calls == []
    assert "prune" not in rec.calls


# -- Task 5: a source that will not go back ----------------------------------

MODULE = "modules/mod-playerbots"


def _module_will_not_go_back(rec: Recorder, server_dir: Path) -> None:
    rec.restore_errors[server_dir / MODULE] = git.GitError("index.lock exists")


def test_an_update_left_stopped_keeps_the_command_that_shows_the_world_servers_log(
    tmp_path: Path,
) -> None:
    """T248 review: the update route's ServersLeftStopped re-wrap carries the Details too."""
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    _module_will_not_go_back(rec, server_dir)
    made = make(wait_ready=_old_build_comes_back())
    made._snapshot = FakeSnapshot(rec)
    with pytest.raises(native.ServersLeftStopped) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert "docker compose logs" in raised.value.detail, str(raised.value)
    assert "docker compose logs" not in str(raised.value)


def test_a_module_that_will_not_go_back_leaves_the_servers_stopped_and_names_the_command(
    tmp_path: Path,
) -> None:
    """The player's state, prevented: the old core is not started under the new module.

    Until T217 the route said "agree again" after a source that did not go back,
    and started the old build on the folder that still held the new module's SQL.
    """
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    _module_will_not_go_back(rec, server_dir)
    made = make(wait_ready=_old_build_comes_back())
    fake = FakeSnapshot(rec)
    made._snapshot = fake
    with pytest.raises(native.ServersLeftStopped) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    text = str(raised.value)
    assert rec.heads[server_dir] == OLD and rec.heads[server_dir / MODULE] == NEW
    assert rec.calls.count("recreate") == 1, "the old build was not started"
    assert f"git -C {server_dir / MODULE} checkout --detach --force {OLD}" in text
    assert "agree again" not in text and "is running again" not in text
    # The copy still goes back: the database is ready for the folder once it is fixed.
    assert fake.put_back_calls == fake.taken
    assert native.sources_off_refusal(server_dir) is not None, "every start is refused"


@pytest.mark.parametrize("when", ["rollback", "compile-failed"])
def test_a_folder_that_will_not_go_back_is_never_said_to_be_ahead_of_a_running_server(
    tmp_path: Path, when: str
) -> None:
    """Live proof 2026-10-05, item 3: the line said "the server that is running" while nothing ran.

    It names what is true in both places it is said: the folder is on another
    commit than the build the server was made from.
    """
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    _module_will_not_go_back(rec, server_dir)
    if when == "rollback":
        made = make(wait_ready=_old_build_comes_back())
    else:
        rec.build_result = AttachedRun(2, ("error: no",))
        made = make()
    made._snapshot = FakeSnapshot(rec)
    said: list[str] = []
    with pytest.raises(InstallerError):
        for line in made.update_to_latest(InstallOptions(server_dir=server_dir)):
            said.append(line)
    line = next(line for line in said if "could NOT be put back" in line)
    assert "is running" not in line, line
    assert "not the commit the build this server has was made from" in line, line


def test_a_module_that_will_not_go_back_after_a_failed_compile_never_says_agree_again(
    tmp_path: Path,
) -> None:
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    _module_will_not_go_back(rec, server_dir)
    rec.build_result = AttachedRun(2, ("error: no",))
    with pytest.raises(InstallerError) as raised:
        list(make().update_to_latest(InstallOptions(server_dir=server_dir)))
    text = str(raised.value)
    assert "agree again" not in text
    assert text.endswith(native.SOURCES_NOT_ALL_BACK_NOTE)


def _built_from(server_dir: Path, rec: Recorder, heads: dict[str, str]) -> None:
    """The install record says the running build was made from `heads` (by repo)."""
    state = native.read_state(server_dir, valid=())
    assert state is not None
    revs = tuple(
        native.SourceRev(repo=repo, built=f"{sha[:7]} · 2026-10-03") for repo, sha in heads.items()
    )
    native.write_state(server_dir, replace(state, source_revs=revs))


def test_rebuild_refuses_a_module_that_is_not_on_the_commit_the_server_was_built_from(
    tmp_path: Path,
) -> None:
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    _built_from(
        server_dir,
        rec,
        {"mod-playerbots/azerothcore-wotlk": OLD, "mod-playerbots/mod-playerbots": OLD},
    )
    rec.heads[server_dir / MODULE] = NEW
    with pytest.raises(InstallerError) as raised:
        list(make().rebuild(InstallOptions(server_dir=server_dir)))
    text = str(raised.value)
    assert f"is on {NEW[:7]}" in text and f"built from {OLD[:7]}" in text
    assert f"git -C {server_dir / MODULE} checkout --detach --force {OLD[:7]}" in text
    assert "Nothing was changed" in text
    assert "build" not in rec.calls and not [c for c in rec.calls if c.startswith("tag:")]


def _detached_on(dest: Path, sha: str) -> None:
    (dest / ".git").mkdir(parents=True, exist_ok=True)
    (dest / ".git" / "HEAD").write_text(f"{sha}\n", encoding="utf-8")


def test_the_rebuild_refusal_before_the_question_reads_the_record_and_the_head_file(
    tmp_path: Path,
) -> None:
    """The view asks this before its Rebuild question (live proof, item 3). No git is run.

    Through `install_wiring.rebuild_refusal_for_app()` and the real engine: the
    same sentence the press itself refuses with, and None wherever the press
    would go on (no record, a HEAD it cannot read, every folder where it was built).
    """
    from yulon import install_wiring

    rec, server_dir, make = _spine(tmp_path, WOTLK)
    ask = install_wiring.rebuild_refusal_for_app(WOTLK, server_dir, engine=make)
    module = server_dir / MODULE
    _detached_on(server_dir, OLD)
    _detached_on(module, NEW)
    assert ask() is None, "no record: an older pin is rebuilt as it is"
    _built_from(
        server_dir,
        rec,
        {"mod-playerbots/azerothcore-wotlk": OLD, "mod-playerbots/mod-playerbots": OLD},
    )
    rec.heads[module] = NEW
    refused = ask()
    assert refused is not None
    assert f"{module} is on {NEW[:7]}, but this server was built from {OLD[:7]}" in refused
    assert f"git -C {module} checkout --detach --force {OLD[:7]}" in refused
    with pytest.raises(InstallerError) as pressed:
        list(make().rebuild(InstallOptions(server_dir=server_dir)))
    assert str(pressed.value) == refused, "one sentence, asked twice"
    _detached_on(module, OLD)
    assert ask() is None, "the HEAD file says it is back: git is not asked"
    _detached_on(module, NEW)
    (module / ".git" / "HEAD").unlink()
    assert ask() is None, "a HEAD it cannot read is the press's to report"
    assert install_wiring.rebuild_refusal_for_app(WOTLK, server_dir, wsl_distro="Ubuntu")() is None


def test_the_early_rebuild_refusal_refuses_only_where_the_press_would(tmp_path: Path) -> None:
    """Scoped re-review: the press builds as-is when git cannot answer, so the early check
    must not refuse there, nor on a HEAD file it cannot read as a commit."""
    from yulon import install_wiring

    rec, server_dir, make = _spine(tmp_path, WOTLK)
    module = server_dir / MODULE
    _built_from(
        server_dir,
        rec,
        {"mod-playerbots/azerothcore-wotlk": OLD, "mod-playerbots/mod-playerbots": OLD},
    )
    ask = install_wiring.rebuild_refusal_for_app(WOTLK, server_dir, engine=make)
    _detached_on(server_dir, OLD)
    _detached_on(module, NEW)
    rec.heads[module] = NEW
    rec.git_reads = False
    assert ask() is None, "git cannot answer: the press goes on, so the early check does too"
    said = list(make().rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" in rec.calls and any("could not check" in line for line in said)
    rec.git_reads = True
    assert ask() is not None, "git answers off its build: refused before the question"


def test_a_head_file_that_is_not_a_commit_is_read_as_unknown(tmp_path: Path) -> None:
    gitdir = tmp_path / ".git"
    (gitdir / "refs" / "heads").mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (gitdir / "refs" / "heads" / "main").write_text("ref: refs/heads/other\n", encoding="utf-8")
    assert native.read_head_file(tmp_path) is None, "a loose ref holding a ref is not a commit"
    (gitdir / "HEAD").write_text("not a commit\n", encoding="utf-8")
    assert native.read_head_file(tmp_path) is None
    (gitdir / "HEAD").write_text(f"{NEW}\n", encoding="utf-8")
    assert native.read_head_file(tmp_path) == NEW
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (gitdir / "refs" / "heads" / "main").write_text(f"{OLD}\n", encoding="utf-8")
    assert native.read_head_file(tmp_path) == OLD


def test_rebuild_goes_on_and_says_so_when_git_cannot_say_where_a_source_is(
    tmp_path: Path,
) -> None:
    """Rebuild is the repair press; an unreadable checkout must not lock it out (Q4)."""
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    rec.git_reads = False
    said = list(make().rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" in rec.calls
    assert any("could not check" in line for line in said), said


def test_rebuild_with_no_record_goes_on_and_says_the_sources_are_on_an_older_pin(
    tmp_path: Path,
) -> None:
    """An install writes no `source_revs`; only an update does. Pins move between releases.

    So with no record, a HEAD that is not this Yu'lon's pin is a server installed
    on an older pin, not a folder half put back. It is never refused, and it is
    never told to check out the pin: that would compile the new core over the old
    database (the T220 crash). It is told "Update to latest" moves it safely.
    """
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    for source in WOTLK.emulator.sources:
        assert source.rev is not None
        rec.heads[server_dir / source.dest] = source.rev
    said = list(make().rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" in rec.calls
    assert not any("older pin" in line for line in said), said
    rec.calls.clear()
    rec.heads[server_dir / MODULE] = NEW
    said = list(make().rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" in rec.calls, "never refused without a record"
    told = [line for line in said if "older pin" in line]
    assert len(told) == 1, said
    assert str(server_dir / MODULE) in told[0] and NEW[:7] in told[0]
    assert server_build_presses.UPDATE_TO_LATEST in told[0]
    assert "database" in told[0]
    assert not any("checkout" in line for line in said), said


def _off(server_dir: Path) -> Path:
    """Case B as a failed update leaves it: the module on NEW, recorded as built from OLD."""
    module = server_dir / MODULE
    (module / ".git").mkdir(parents=True, exist_ok=True)
    (module / ".git" / "HEAD").write_text(f"{NEW}\n", encoding="utf-8")
    native.remember_sources_off(server_dir, [("mod-playerbots/mod-playerbots", module, OLD)])
    return module


def _refusal_for(module: Path) -> str:
    """The owner's sentence (T217 (a), 2026-10-05): what is off, and what to press."""
    return (
        f"mod-playerbots/mod-playerbots in {module} is on {NEW[:7]}, not on {OLD[:7]}, the "
        "commit this server was built from, and its world server would apply the database "
        "updates in that folder, so the server is not started: press \u201cReturn to the tested "
        "pin\u2026\u201d under \u201cServer build \u25be\u201d on the Modules tab, or put the "
        "folder back with this command and press \u201cRebuild the server\u2026\u201d under "
        "\u201cServer build \u25be\u201d on the Modules tab:\n"
        f"git -C {module} checkout --detach --force {OLD}"
    )


def test_a_start_is_refused_while_a_source_is_off_its_commit_and_allowed_once_it_is_back(
    tmp_path: Path,
) -> None:
    """Owner, T217 (a): Start REFUSES (it warned until 2026-10-05). Read off `.git/HEAD`."""
    module = _off(tmp_path)
    assert native.sources_off_refusal(tmp_path) == _refusal_for(module)
    (module / ".git" / "HEAD").write_text(f"{OLD}\n", encoding="utf-8")
    assert native.sources_off_refusal(tmp_path) is None
    assert not (tmp_path / native.SOURCES_OFF_FILE).exists(), "forgotten once it is back"


def test_the_controllers_start_is_refused_while_a_source_is_off_its_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner, T217 (a): the live proof (2026-10-05, item 3) showed the warned Start let the
    old world apply the off-commit module's SQL and crash-loop. Start now refuses, through
    `Controller.refuse_start()`, and starts nothing; once the folder is back it starts."""
    from yulon import docker
    from yulon.controller import Controller, StartRefused

    module = _off(tmp_path)
    started: list[Path] = []
    monkeypatch.setattr(
        docker, "start_staged", lambda spec, where, **_kw: started.append(where) or True
    )
    controller = Controller(WOTLK.container_spec(), tmp_path)
    monkeypatch.setattr(controller, "port_conflicts", lambda: [])
    with pytest.raises(StartRefused) as refused:
        controller.refuse_start()
    assert str(refused.value) == _refusal_for(module)
    with pytest.raises(StartRefused):
        controller.start()
    assert started == []
    (module / ".git" / "HEAD").write_text(f"{OLD}\n", encoding="utf-8")
    controller.start()
    assert started == [tmp_path]


def test_return_to_the_tested_pin_is_not_refused_by_a_source_off_its_commit(
    tmp_path: Path,
) -> None:
    """It is one of the two ways out the refusal names, so it must still run."""
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    _off(server_dir)
    made = make()
    made._snapshot = FakeSnapshot(rec)
    list(made.update_to_latest(InstallOptions(server_dir=server_dir), to_pin=True))
    assert "build" in rec.calls and rec.calls.count("recreate") >= 1


# -- Task 6: what the player is told before and as the press runs --------------


def test_the_wotlk_question_says_its_playerbots_database_goes_back_and_what_the_backup_is_for() -> (
    None
):
    text = native.update_to_latest_confirmation(
        WOTLK, Path("/srv"), "x/y", copied=("acore_playerbots",)
    )
    assert "acore_playerbots" in text
    assert "as it was just before the new server started" in text
    assert "Anything the new server writes into your database on first start is not" not in text
    # The backup still has a job: a later return, which no rollback covers.
    assert server_build_presses.RETURN_TO_PIN in text
    assert "backup" in text


def test_the_wotlk_question_names_all_four_databases_and_what_copying_them_costs() -> None:
    """Cold review: since T220 WotLK copies its core's three too, which takes time and disk."""
    text = native.update_to_latest_confirmation(WOTLK, Path("/srv"), "x/y", copied=WOTLK_COPY)
    for name in WOTLK_COPY:
        assert name in text
    assert "a few minutes" in text and "some hundreds of MB" in text
    assert "only the newest" in text


def test_the_tortoise_question_says_its_world_database_is_not_copied() -> None:
    text = native.update_to_latest_confirmation(
        TORTOISE, Path("/srv"), "x/y", copied=("tw_logon", "tw_char"), not_copied=("tw_world",)
    )
    assert "tw_logon and tw_char" in text
    assert "tw_world is not copied" in text


def test_the_tbc_question_keeps_its_words() -> None:
    """No copy, so the old sentence is the true one."""
    text = native.update_to_latest_confirmation(TBC, Path("/srv"), "x/y")
    assert text.endswith(
        "If the build fails, the build you have now is put back. Anything the new server writes "
        "into your database on first start is not put back — that is what the backup is for."
        + native.world_updates_note(TBC)
    )
    assert native.world_updates_note(TBC), "T531: TBC's question names the world content fixes"


def test_the_wiring_asks_the_family_which_databases_the_question_names(tmp_path: Path) -> None:
    from yulon import install_wiring

    route = install_wiring.update_to_latest_for_app(WOTLK, tmp_path)
    assert route is not None
    assert "acore_playerbots" in route.confirmation()
    route = install_wiring.update_to_latest_for_app(TBC, tmp_path)
    assert route is not None
    assert "is not put back — that is what the backup is for" in route.confirmation()


def test_the_press_says_what_it_copies_before_it_says_anything_else_happens(
    tmp_path: Path,
) -> None:
    """The opening line's clauses are in the order the rollback runs them (Task 3)."""
    rec, _dir, _made, _fake, said, _raised = _press(tmp_path, WOTLK)
    opening = native.copy_opening_line(WOTLK_COPY)
    assert opening in said
    assert said.index(opening) == said.index(native.UPDATE_TO_LATEST_OPENING_NOTE) + 1
    # Sources, then the copy, then the build you have: the order `back()` runs.
    assert (
        opening.index("the source folders go back first")
        < opening.index("then those copies")
        < opening.index("then does the build you have start again")
    )


def test_a_tbc_press_says_nothing_about_a_copy(tmp_path: Path) -> None:
    _rec, _dir, _made, _fake, said, _raised = _press(tmp_path, TBC)
    assert not [line for line in said if "copies" in line or "Copying" in line]


# -- T220: a WotLK update applies the new core's own database updates ---------
#
# Upstream as the player met it (T217's evidence): 7f12e89 -> f19a187 renames the
# DBC override table to `emotestextsound_dbc` and creates it only in
# `data/sql/updates/db_world/2026_09_21_05.sql`; mod-playerbots 7bae1b5 -> 037c014
# adds `2026_09_13_00_ai_playerbot_target_requester_text.sql` and
# `2026_09_21_00_playerbots_speech.sql`. The world server runs with its own updater
# off and a start never runs the import, so the new world aborted on the missing
# table. The Recorder holds no database; what is asserted is that the import
# one-shot runs on the new build, with the servers down, after the copy and
# before the first start, and what happens when it fails.


def test_a_wotlk_update_applies_the_new_cores_updates_after_the_copy_before_the_first_start(
    tmp_path: Path,
) -> None:
    rec, _dir, _made, _fake, said, raised = _press(tmp_path, WOTLK)
    assert raised is None, raised
    stop = _at(rec.calls, "stop_servers")
    copied = _at(rec.calls, "snapshot:", stop)
    imported = _at(rec.calls, "one-shot:ac-db-import", copied)
    _at(rec.calls, "recreate", imported)
    assert _at(rec.calls, "build") < imported, "on the new build's image"
    assert any("database updates are in" in line for line in said)


def test_core_updates_that_fail_never_start_the_new_build_and_the_copy_goes_back(
    tmp_path: Path,
) -> None:
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    rec.one_shot_result = AttachedRun(1, ("ERROR 1050 (42S01): Table already exists",))
    made = make(wait_ready=_old_build_comes_back())
    fake = FakeSnapshot(rec)
    made._snapshot = fake
    with pytest.raises(InstallerError) as failed:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    text = str(failed.value)
    assert "Applying the new build's database updates failed (exit 1)" in text
    assert rec.calls.count("recreate") == 1, "only the old build's: the new one never started"
    assert fake.put_back_calls == fake.taken, "what the updates applied is undone"
    assert _moving_heads(rec, server_dir, made) == {OLD}


def test_a_plain_rebuild_and_a_tbc_update_run_no_import(tmp_path: Path) -> None:
    rec, server_dir, make = _spine(tmp_path / "wotlk", WOTLK)
    for source in WOTLK.emulator.sources:
        assert source.rev is not None
        rec.heads[server_dir / source.dest] = source.rev
    list(make().rebuild(InstallOptions(server_dir=server_dir)))
    assert not [call for call in rec.calls if call.startswith("one-shot:")]
    rec, _dir, _made, _fake, _said, _raised = _press(tmp_path / "tbc", TBC)
    assert not [call for call in rec.calls if call.startswith("one-shot:")]


def test_the_way_back_to_the_pin_runs_the_import_too(tmp_path: Path) -> None:
    """Harmless on the way back (nothing newer to apply), and the same route."""
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    made = make()
    made._snapshot = FakeSnapshot(rec)
    list(made.update_to_latest(InstallOptions(server_dir=server_dir), to_pin=True))
    assert "one-shot:ac-db-import" in rec.calls
