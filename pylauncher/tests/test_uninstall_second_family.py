"""What an uninstall has to know about the SECOND family (8.9b, WoW Vanilla).

8.9a's box gates uninstall on two families rather than four "because the
mechanism is the compose project and the folder, which are the engine's and not
the emulator's" (`pyplan/checklist.md`, line 2506). That claim was checked
against the real Vanilla install at `/home/pk/vanilla-75b` on m910q,
2026-09-08, and it holds for the OBJECTS:

* the folder carries the same `.yulon-install.json` claim (`family: cmangos`,
  `install_id: 06ced116`) that ownership is proved from;
* `docker-compose.yml` carries `name: yulon-wow-vanilla-06ced116`, the same
  `yulon-<game>-<install id>` `composegen.project_name()` computes;
* its volume `yulon-wow-vanilla-06ced116_db-data` carries
  `com.docker.compose.project=yulon-wow-vanilla-06ced116`, so the label filter
  every scoped command uses answers on this tree exactly as it does on WotLK
  (`docker volume ls --filter label=…` returned that one name and nothing else).

It fails for three RELATIONSHIPS, and each has a test here. None of them is
about an emulator: they are about what the folder holds, what the project
declares, and what the launcher's record points at — which is to say they are
about the very mechanism the box says is shared.

Nothing here starts a container, reads a network or writes outside `tmp_path`.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from yulon import resources, state
from yulon.catalog import composegen, native
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError

WOTLK = "wow-wotlk"
"""The family 8.9a's code was written and gated against."""

VANILLA = "wow-vanilla"
"""The family 8.9b has to work on, and the one whose gate box says "as 8.9a"."""

DB_VOLUME_KEY = "db-data"
"""The compose volume key both families declare for the database (`<project>_db-data`)."""

TEMPLATES = resources.installers_dir()


def entry_for(game: str) -> CatalogEntry:
    return load_catalog().get(game)


def db_volume(game: str, server_dir: Path) -> str:
    """What `docker volume ls` prints for this install's database volume."""
    project = composegen.project_name(game, server_dir, platform_id=lambda: "linux")
    return f"{project}_{DB_VOLUME_KEY}"


def test_the_cmangos_folder_holds_the_only_key_to_the_volume_a_ticked_purge_keeps(
    tmp_path: Path,
) -> None:
    """ "Keep my characters" keeps the volume — and on this tree the key is in the folder.

    `purge.Uninstaller.run(keep_characters=True)` keeps `<project>_db-data` and
    then removes the server folder. On the AzerothCore tree that is the whole
    story: `wow-wotlk`'s password plan is `fixed`, so the value is in
    `catalog.json` and a reinstall regenerates a compose file carrying the same
    one.

    On the CMaNGOS tree the password is GENERATED per install and written to
    `.db_password` inside the folder the purge deletes (measured on
    `~/vanilla-75b`: `.db_password` 0600, 25 bytes, and the same secret again in
    plaintext in `.env` — both inside the folder). The volume name is a digest
    of the ABSOLUTE PATH, so it does not change when the folder is deleted and
    recreated: what is kept is a database nothing can open any more.

    The two halves are asserted together on purpose. Either alone reads as a
    fact about a password file; together they are the reason 8.9a's ticked path
    cannot satisfy 8.9b's definition of done.

    The value written below is sixteen zeros. It was `~/vanilla-75b`'s REAL
    generated password when this test was written on 2026-09-08, and
    `tests/test_no_secrets_in_evidence.py` caught it at the merge -- which is
    the second time that guard has earned its keep. Nothing here depends on
    which value it is.
    """
    server_dir = tmp_path / "vanilla"
    server_dir.mkdir()
    entry = entry_for(VANILLA)
    plan = entry.install.password
    assert plan.mode == "generated" and plan.file, plan
    (server_dir / plan.file).write_text("vanilla-0000000000000000\n", encoding="utf-8")

    volume_before = db_volume(VANILLA, server_dir)
    assert entry.install.db_password(server_dir) == "vanilla-0000000000000000"

    shutil.rmtree(server_dir)  # what a purge does to the folder, ticked or not

    assert (
        db_volume(VANILLA, server_dir) == volume_before
    ), "the kept volume still belongs to this path, so the reinstall comes back to it"
    assert (
        entry.install.db_password(server_dir) is None
    ), "the folder held the only copy of the password that volume was created with"

    wotlk = entry_for(WOTLK)
    assert wotlk.install.password.mode == "fixed"
    assert (
        wotlk.install.db_password(tmp_path / "never-existed") == "password"
    ), "on the gated family the key is in the catalog, so deleting the folder costs nothing"


def test_the_reinstall_a_ticked_purge_promises_is_refused_on_the_cmangos_tree(
    tmp_path: Path,
) -> None:
    """And the refusal is the shipped installer's, asked of the real `wow-vanilla` entry.

    The state a ticked 8.9a purge leaves on this tree — an empty folder at the
    same path, and `<project>_db-data` still on the daemon — is exactly the
    state `CmangosInstaller._db_password` refuses to write into. So "a reinstall
    to the same folder finds the characters on the login screen" does not merely
    lose the characters here: the install stops at stage 3 of 13 and the only
    way past it that the message can name deletes the volume the checkbox was
    ticked to save.

    `tests/test_families_cmangos.py::test_db_password_refuses_when_the_file_is_gone_but_the
    _volume_exists` pins the same refusal against a synthetic entry. This asks it
    of the entry a user actually presses Install on, with the volume name
    `composegen` gives that entry — the pair a purge has to reason about.
    """
    server_dir = tmp_path / "vanilla"
    server_dir.mkdir()  # the purge deleted it; the reinstall made it again
    entry = entry_for(VANILLA)
    volume = db_volume(VANILLA, server_dir)

    engine = CmangosInstaller(
        entry,
        seams=native.Seams(platform_id=lambda: "linux", volume_exists=lambda name: name == volume),
    )
    ctx = native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(
            game_id=entry.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "linux"),
            family="cmangos",
        ),
        cancel=None,
        secrets=native.Secrets(db_password="vanilla-0000000000000000"),
    )

    with pytest.raises(InstallerError) as refusal:
        list(engine._db_password(ctx))

    message = str(refusal.value)
    assert volume in message
    assert f"docker volume rm {volume}" in message
    assert not (server_dir / ".db_password").exists(), "the refusal wrote nothing"


def top_level_volumes(compose_text: str) -> set[str]:
    """The keys under a column-0 `volumes:` — what compose prefixes with the project.

    Textual, like every other reader of these generated files in this suite:
    there is no YAML parser in this project's dependencies, and the shape of a
    file this app wrote itself is known.
    """
    names: set[str] = set()
    inside = False
    for line in compose_text.splitlines():
        if re.match(r"^volumes:\s*$", line):
            inside = True
            continue
        if inside and re.match(r"^\S", line):
            break
        if inside:
            match = re.match(r"^  ([A-Za-z0-9._-]+):\s*$", line)
            if match:
                names.add(match.group(1))
    return names


def render(entry: CatalogEntry, server_dir: Path) -> composegen.ComposePlan:
    password = None if entry.install.password.mode == "fixed" else "test-0000000000000000"
    return composegen.render(
        entry,
        server_dir,
        templates_root=TEMPLATES,
        db_password=password,
        platform_id=lambda: "linux",
    )


def test_the_two_gated_families_declare_different_volume_sets(tmp_path: Path) -> None:
    """One project, two volumes on WotLK and one on Vanilla — so they are enumerated.

    WotLK's client data is a SECOND named volume (`client-data`, 3.2 GB on
    yulon-ubuntu); the CMaNGOS tree extracts the user's own client into `./data`
    INSIDE the server folder (2.4 GB on `~/vanilla-75b`). Two consequences a
    purge cannot be written around:

    * a removal that names its volumes leaks one here or fails on a volume that
      does not exist there — `docker volume rm` exits non-zero on "no such
      volume" — which is why the objects have to be listed by the project label
      and removed by the names Docker gave back;
    * the ticked path costs a different thing on each tree. Keeping `db-data` on
      WotLK keeps the characters and the client data survives beside it; keeping
      it here keeps the characters and throws away an extraction and an mmap
      build that the reinstall has to do again.
    """
    wotlk = top_level_volumes(render(entry_for(WOTLK), tmp_path / "wotlk").base)
    vanilla = top_level_volumes(render(entry_for(VANILLA), tmp_path / "vanilla").base)

    assert DB_VOLUME_KEY in wotlk and DB_VOLUME_KEY in vanilla, (wotlk, vanilla)
    assert wotlk - vanilla, "WotLK declares a volume Vanilla does not; enumerate, never name"
    assert vanilla == {DB_VOLUME_KEY}


def test_the_database_image_is_no_familys_to_remove_and_this_ones_is_shared(
    tmp_path: Path,
) -> None:
    """`--rmi all` would take the database image; on m910q two installs hold it.

    `composegen.built_image_refs()` is what this install BUILT (one ref for a
    CMaNGOS entry, four for WotLK), and the pulled database image is never among
    them — which is what makes "remove the refs, never a compose flag" safe.

    The second half is why the distinction is not academic on this tree.
    `wow-vanilla` and `wow-tbc` both pull `mariadb:11`, and on m910q both are
    installed (`yulon-wow-vanilla-06ced116_db-data`,
    `yulon-wow-tbc-37f13213_db-data`). Measured there 2026-09-08:
    `docker ps -a --filter ancestor=mariadb:11` is EMPTY — the TBC install has no
    containers at all — so the daemon would not refuse the removal, and the
    neighbour's next start would re-pull 458 MB over that box's known-flaky
    link. WotLK's `mysql:8.4` is nobody else's, so the gate 8.9a runs cannot see
    this.
    """
    vanilla = entry_for(VANILLA)
    wotlk = entry_for(WOTLK)
    vanilla_db = vanilla.install.native.db.image
    wotlk_db = wotlk.install.native.db.image

    for entry in (vanilla, wotlk):
        refs = composegen.built_image_refs(entry, tmp_path / entry.id, platform_id=lambda: "linux")
        assert refs, entry.id
        assert entry.install.native.db.image not in refs, refs

    siblings = {
        game.id
        for game in load_catalog().games
        if game.install.native is not None and game.install.native.db.image == vanilla_db
    }
    assert siblings > {VANILLA}, f"{vanilla_db} is shared with {siblings - {VANILLA}}"
    assert wotlk_db not in {
        game.install.native.db.image
        for game in load_catalog().games
        if game.install.native is not None and game.id != WOTLK
    }, "the gated family's database image is its own, which is why 8.9a cannot see this"


def test_only_the_cmangos_tree_makes_the_record_name_a_folder_outside_the_install(
    tmp_path: Path,
) -> None:
    """`KnownInstall.client_dir` is populated on this family and on no other.

    `install.requires_client_dir` is true for the three CMaNGOS entries and
    absent for WotLK, whose client data is downloaded into a volume — so on
    yulon-ubuntu the record 8.9a forgets names exactly one folder, the one it
    deletes, and any code that deleted "what the record names" would pass its
    gate. Here the same record names the user's own WoW 1.12.1 client, which is
    the one folder in this feature nobody could restore.

    The second half asserts the property rather than the wording: forgetting the
    record leaves that folder alone.
    """
    catalog = load_catalog()
    assert catalog.get(WOTLK).install.requires_client_dir is False
    cmangos = [
        game.id
        for game in catalog.games
        if game.install.native is not None and game.install.native.family == "cmangos"
    ]
    assert cmangos, "the catalog ships no CMaNGOS entry"
    for game in cmangos:
        assert catalog.get(game).install.requires_client_dir is True, game

    server_dir = tmp_path / "vanilla"
    client_dir = tmp_path / "clients" / "WoW-Vanilla-1.12.1"
    client_dir.mkdir(parents=True)
    (client_dir / "Data").mkdir()
    path = tmp_path / "state.json"
    app_state = state.AppState()
    app_state.remember(
        state.KnownInstall(game=VANILLA, server_dir=server_dir, client_dir=client_dir)
    )
    state.save_state(app_state, path)

    app_state.forget(VANILLA, server_dir)
    state.save_state(app_state, path)

    assert state.load_state(path).installs == []
    assert client_dir.is_dir() and (client_dir / "Data").is_dir()


def test_the_catalog_says_which_containers_this_family_stops(tmp_path: Path) -> None:
    """The census is per entry, so the running refusal reads Vanilla's own three names.

    Recorded because it is the clause 8.9b's gate proves by pressing Uninstall on
    a RUNNING server, and the names it must show are `vanilla-mangosd`,
    `vanilla-realmd` and `vanilla-db` — not the `ac-*` triple every message in
    8.9a's gate evidence carries. They come from `catalog.json`, so a spec built
    per entry is right on both trees and one built from a constant is right on
    one.
    """
    vanilla = entry_for(VANILLA).containers
    wotlk = entry_for(WOTLK).containers
    assert (vanilla.db, vanilla.auth, vanilla.world) == (
        "vanilla-db",
        "vanilla-realmd",
        "vanilla-mangosd",
    )
    assert not {vanilla.db, vanilla.auth, vanilla.world} & {wotlk.db, wotlk.auth, wotlk.world}
    base = render(entry_for(VANILLA), tmp_path / "vanilla").base
    for name in (vanilla.db, vanilla.auth, vanilla.world):
        assert f"container_name: {name}" in base, name


def test_the_generated_compose_carries_the_project_a_purge_scopes_to(tmp_path: Path) -> None:
    """`name:` in the file is the identity, on this tree as on the other.

    The half of the box's claim that DOES hold, pinned so a change to it is
    noticed: `docker.install_project()` reads `name:` out of the generated file,
    and on `~/vanilla-75b` that line reads `name: yulon-wow-vanilla-06ced116`
    for an install whose absolute path hashes to `06ced116`. Every container,
    volume and network of this install carries that string as its
    `com.docker.compose.project` label (read on m910q, 2026-09-08).
    """
    for game in (VANILLA, WOTLK):
        server_dir = tmp_path / game
        base = render(entry_for(game), server_dir).base
        project = composegen.project_name(game, server_dir, platform_id=lambda: "linux")
        assert f"name: {project}" in base
        assert project.startswith(f"yulon-{game}-")


def test_a_failed_cmangos_install_is_a_purge_subject_with_no_volume_at_all(
    tmp_path: Path,
) -> None:
    """The state `~/vanilla-75` is in, and the one a user reaches for Uninstall from.

    Read on m910q 2026-09-08: `.yulon-install.json` OWNED, four stages completed,
    `last_error` naming the vmap extractor, an image
    (`yulon.local/cmangos-vanilla-server:native-0baff6f3`), a folder with
    `.db_password` and `src/` — and NO containers and NO `db-data` volume,
    because the install never reached `start-db`.

    A purge of that install with "Keep my characters" ticked has no character
    volume to keep, and 8.9a's `run()` raises rather than promising one. That is
    the right behaviour and the wrong message to meet on the commonest thing a
    user uninstalls, so 8.9b's gate presses it. What this test pins is the shape
    the message is built from: the volume it will say does not exist is the one
    `composegen` names for this folder, and a failed install's claim is still
    OWNED, so the refusal is about the volume and never about ownership.
    """
    from yulon.apply import server_dir_claim
    from yulon.ownership import Ownership

    server_dir = tmp_path / "vanilla-75"
    server_dir.mkdir()
    claim = {
        "version": 1,
        "game_id": VANILLA,
        "family": "cmangos",
        "install_id": composegen.install_id(server_dir),
        "completed": ["clone-sources", "write-dockerfile", "generate-compose", "build"],
        "last_error": "vmap extract failed (exit 1)",
        "updated_unix": 1788481989,
    }
    (server_dir / ".yulon-install.json").write_text(json.dumps(claim), encoding="utf-8")
    assert server_dir_claim(server_dir) is Ownership.OWNED
    assert db_volume(VANILLA, server_dir).endswith("_db-data")


def test_the_seams_this_file_uses_are_the_shipped_ones(tmp_path: Path) -> None:
    """A guard on the guards: the fake above replaces a real default, not a name.

    `native.Seams.volume_exists` defaults to `docker.volume_exists`, so the
    engine built in this file differs from the shipped one in exactly the seam
    named — and the refusal it produced is the shipped code path.
    """
    from yulon import docker

    assert native.Seams().volume_exists is docker.volume_exists
    replaced = replace(native.Seams(), volume_exists=lambda _name: True)
    assert replaced.volume_exists is not docker.volume_exists


# ------------------------------------------------- 8.9b: the wiring itself
#
# Everything above was written while 8.9b had no code of its own: it read the
# real installs and pinned what the second family's shape does to 8.9a's
# engine. What follows is the box's own half — the Vanilla tab actually gets an
# Uninstall — and it is asked of `ControllerServices.for_entry()`, the object
# the Server tab holds, rather than of `_for_vanilla`'s source, so a factory
# that built an Uninstaller and forgot to pass it fails here.


def test_the_vanilla_tab_gets_an_uninstall_scoped_to_this_install(tmp_path: Path) -> None:
    """8.9b's first requirement: there is a button, and it is aimed at this folder.

    `ControllerServices.uninstall = None` is what "no controls on this tab"
    means (`ui/controller_view.py`, and `_NO_UNINSTALL_YET` in
    `test_controller_packages_agree.py` names the games it is still true of).
    Presence is not enough to assert on its own — an Uninstaller built with the
    WotLK spec would be present and would stop `ac-worldserver`, a container
    this box has never had — so the three facts that scope it are read back:
    the game, the folder, and the container spec that came from THIS entry.
    """
    from yulon.ui.controller_view import ControllerServices

    entry = entry_for(VANILLA)
    server_dir = tmp_path / "vanilla"
    server_dir.mkdir()
    (server_dir / ".db_password").write_text("hunter2\n", encoding="utf-8")

    services = ControllerServices.for_entry(entry, server_dir)
    assert services.uninstall is not None, "the Vanilla tab still has no Uninstall (8.9b)"
    assert services.uninstall.game == VANILLA
    assert services.uninstall.server_dir == server_dir
    assert services.uninstall.spec.world == "vanilla-mangosd"
    assert services.uninstall.spec.world != entry_for(WOTLK).container_spec().world


def test_the_vanilla_uninstall_removes_this_installs_images_and_not_the_shared_one(
    tmp_path: Path,
) -> None:
    """Defect 2 of `.notes/8.9b-gate-plan.md`, pinned at the wiring rather than in the engine.

    `wow-vanilla` and `wow-tbc` both pull `mariadb:11`; `wow-wotlk` pulls
    `mysql:8.4` and nothing else does, so 8.9a's gate could not see this. What
    the Uninstaller is handed must therefore be the BUILT refs — the ones this
    install's own build produced, tagged with this folder's digest — and never
    the pulled database image a live neighbour is using.
    """
    from yulon.ui.controller_view import ControllerServices

    entry = entry_for(VANILLA)
    server_dir = tmp_path / "vanilla"
    server_dir.mkdir()
    (server_dir / ".db_password").write_text("hunter2\n", encoding="utf-8")

    services = ControllerServices.for_entry(entry, server_dir)
    assert services.uninstall is not None
    refs = services.uninstall.image_refs
    assert refs == composegen.built_image_refs(entry, server_dir)
    assert refs, "an install with no built image would leave its image behind for ever"
    for ref in refs:
        assert ref.startswith("yulon.local/"), ref
        assert "mariadb" not in ref and "mysql" not in ref, f"{ref} is a pulled image"


def test_a_ticked_vanilla_purge_keeps_the_password_the_reinstall_will_need(
    tmp_path: Path,
) -> None:
    """The whole reason this box exists, driven through the shipped `run()`.

    WotLK's password plan is `fixed`, so 8.9a's ticked press kept nothing and
    could not exercise this at all. Vanilla's is `generated` into
    `.db_password` inside the folder the purge deletes, so the copy-and-recall
    path `yulon/dbsecret.py` was written for gets its first press here. Only
    the four seams that touch the world are replaced; the decision about what
    to keep is the shipped one.
    """
    from yulon import dbsecret, docker, logsnap, purge
    from yulon.ownership import Ownership

    entry = entry_for(VANILLA)
    server_dir = tmp_path / "vanilla"
    server_dir.mkdir()
    (server_dir / ".db_password").write_text("s3cret-generated\n", encoding="utf-8")
    volume = db_volume(VANILLA, server_dir)
    config_dir = tmp_path / "config"

    removed_volumes: list[str] = []
    uninstaller = purge.Uninstaller(
        game=VANILLA,
        server_dir=server_dir,
        spec=entry.container_spec(),
        image_refs=(),
        forget=lambda: None,
        claim=lambda _p: Ownership.OWNED,
        project_of=lambda: composegen.project_name(
            VANILLA, server_dir, platform_id=lambda: "linux"
        ),
        census=lambda _p: docker.Running(),
        containers_of=lambda _p: [],
        volumes_of=lambda _p: [volume],
        folder_size=lambda _p: 0,
        keep_secret=lambda password, vol: dbsecret.remember(
            VANILLA,
            composegen.install_id(server_dir),
            password=password,
            volume=vol,
            config_dir=config_dir,
        ),
        snapshot=lambda: logsnap.Snapshot(path=tmp_path / "snap.log"),
        remove_containers=lambda: True,
        remove_volume=removed_volumes.append,
        remove_image=lambda ref: ref,
        remove_folder=lambda path: shutil.rmtree(path),
    )
    result = uninstaller.run(keep_characters=True)

    assert result.warnings == (), result.warnings
    assert removed_volumes == [], "a ticked purge must keep the database volume"
    assert result.kept_volumes == (volume,)
    assert result.secret_kept is not None, "nothing recorded where the password went"
    assert result.folder_removed and not server_dir.exists()
    kept = dbsecret.recall(VANILLA, composegen.install_id(server_dir), config_dir=config_dir)
    assert kept is not None, "the key to the kept volume died with the folder"
    assert kept.password == "s3cret-generated"
    assert kept.volume == volume


def test_planning_a_purge_does_not_warn_that_this_installs_stages_are_unknown(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Found by 8.9b's live gate: every purge plan logged a warning that is not true.

    `purge._default_reason()` and `apply.server_dir_claim()` both call
    `native.read_claim(server_dir, valid=())`, and both say in as many words that
    the stage names are not their business — they want the identity and the
    version. `_parse_state` measures `completed` against `valid` anyway, so with
    an empty tuple EVERY recorded stage is "unknown" and the log says:

        .yulon-install.json records stages this build does not know:
        clone-sources, write-dockerfile, build, extract, mmaps, conf, import.
        … this is usually an older Yu'lon opening an install a newer one created.

    Read on m910q 2026-09-08 against a perfectly ordinary CMaNGOS install this
    build had just written. The sentence is advice about a version mismatch that
    is not happening, printed on the one action that cannot be undone, and it is
    the last thing a user should be reading while deciding whether to press
    Uninstall. It is a family-neutral defect: `valid=()` makes every name unknown
    whoever installed it, so 8.9a's WotLK gate saw it too and nobody read the log.
    """
    import logging

    from yulon.catalog import native

    server_dir = tmp_path / "vanilla"
    server_dir.mkdir()
    stages = ("clone-sources", "write-dockerfile", "build", "extract", "conf", "import")
    native.write_state(
        server_dir,
        native.InstallState(
            game_id=VANILLA,
            install_id=composegen.install_id(server_dir),
            family="cmangos",
            completed=stages,
        ),
    )

    with caplog.at_level(logging.WARNING, logger="yulon.catalog.native"):
        claim = native.read_claim(server_dir, valid=())
    assert claim.state is not None and claim.state.install_id
    assert [r.message for r in caplog.records] == [], (
        "asking for the identity alone must not accuse the install of coming from a "
        "newer build; only a caller that supplied a stage list is asking about stages"
    )

    # And the warning must still fire for the caller that IS asking about stages.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="yulon.catalog.native"):
        native.read_claim(server_dir, valid=("clone-sources",))
    assert any(
        "records stages this build does not know" in r.message for r in caplog.records
    ), "a caller that named the stages it knows must still be told about the ones it does not"
