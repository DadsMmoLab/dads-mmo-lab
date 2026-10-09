"""T611 (2): a player module may not take the name of a module the Tortoise core already holds.

The image recipe lays `<server>/modules/` over the core's own `modules/` with
`COPY modules/ /src/modules/`, file by file, so a player module with a core module's name
would be MIXED into it with no warning. The core's folder is read from the server's own
checkout (`src/tortoise-wow/modules`): at Install and Update of the module, and again at
Rebuild, because "Update to latest" can move the core onto a module of that name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import module_moves
from yulon.apply import ApplyRefusal
from yulon.catalog.installer import InstallerError
from yulon.controller_wow_tortoise import autoupdate
from yulon.controller_wow_tortoise import modules as tortoise_modules
from yulon.git import git_available

from .test_apply_put_back import _git, _LocalOrigin
from .test_families_cmangos import context
from .test_tortoise_custom import _Db
from .test_tortoise_module_build import _installer

URL = "https://github.com/you/mod-pb"


def _core_has(server: Path, *names: str, core: str = "src/tortoise-wow") -> None:
    folder = server / core / "modules"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "README.md").write_text("the core's own modules\n", encoding="utf-8")
    for name in names:
        (folder / name).mkdir()
        (folder / name / "CMakeLists.txt").write_text("# core\n", encoding="utf-8")


def _publish(origin: Path, label: str) -> str:
    (origin / "src").mkdir(parents=True, exist_ok=True)
    (origin / "src" / "x.cpp").write_text(f"// {label}\n", encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", label)
    return _git(origin, "rev-parse", "HEAD")


def _rig(tmp_path: Path) -> tuple[autoupdate.GuardedApplier, Path, Path, _LocalOrigin]:
    origin, server = tmp_path / "origin", tmp_path / "server"
    origin.mkdir()
    server.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    git = _LocalOrigin(origin)
    applier = tortoise_modules.applier(
        server,
        sql=_Db(),
        arming=lambda: autoupdate.Arming(enabled=False),
        world_running=lambda: False,
        git=git,  # type: ignore[arg-type]
        client_dir=None,
    )
    applier.remote_url = lambda _dest: URL  # type: ignore[method-assign]
    return applier, origin, server, git


needs_git = pytest.mark.skipif(not git_available(), reason="needs a host git")


@needs_git
def test_a_link_named_like_a_core_module_is_refused_before_anything_is_cloned(
    tmp_path: Path,
) -> None:
    applier, origin, server, git = _rig(tmp_path)
    _publish(origin, "v1")
    _core_has(server, "mod-pb")

    with pytest.raises(ApplyRefusal) as refused:
        tortoise_modules.install_custom(applier)(tortoise_modules.derive_link(URL), None)

    said = str(refused.value)
    assert said.count("mod-pb") >= 2, "names the player's module and the core's"
    assert "src/tortoise-wow/modules/mod-pb" in said
    assert "Nothing was changed" in said
    assert git.specs == [], "refused before git was asked for anything"
    assert not (server / "modules").exists()
    assert list(tortoise_modules.store().load_all("module")) == []


@needs_git
def test_the_core_may_spell_the_name_in_another_case_and_both_are_named(tmp_path: Path) -> None:
    applier, origin, server, _git_seam = _rig(tmp_path)
    _publish(origin, "v1")
    _core_has(server, "Mod-PB")

    with pytest.raises(ApplyRefusal) as refused:
        tortoise_modules.install_custom(applier)(tortoise_modules.derive_link(URL), None)

    said = str(refused.value)
    assert "mod-pb" in said and "Mod-PB" in said


@needs_git
def test_a_folder_named_like_a_core_module_is_refused_before_it_is_copied(tmp_path: Path) -> None:
    applier, _origin, server, _git_seam = _rig(tmp_path)
    _core_has(server, "mod-pb")
    folder = tmp_path / "mod-pb"
    (folder / "src").mkdir(parents=True)
    (folder / "src" / "x.cpp").write_text("// mine\n", encoding="utf-8")

    with pytest.raises(ApplyRefusal) as refused:
        tortoise_modules.install_custom(applier)(tortoise_modules.derive_folder(folder), folder)

    assert "src/tortoise-wow/modules/mod-pb" in str(refused.value)
    assert not (server / "modules" / "mod-pb").exists()


@needs_git
def test_another_name_and_a_core_without_modules_install_as_before(tmp_path: Path) -> None:
    applier, origin, server, _git_seam = _rig(tmp_path)
    _publish(origin, "v1")
    _core_has(server, "mod-other")  # a README.md file and one other module
    tortoise_modules.install_custom(applier)(tortoise_modules.derive_link(URL), None)
    assert (server / "modules" / "mod-pb" / "src" / "x.cpp").is_file()


@needs_git
def test_a_core_that_gains_the_name_later_refuses_the_update_and_changes_nothing(
    tmp_path: Path,
) -> None:
    applier, origin, server, git = _rig(tmp_path)
    first = _publish(origin, "v1")
    tortoise_modules.install_custom(applier)(tortoise_modules.derive_link(URL), None)
    _publish(origin, "v2")
    _core_has(server, "mod-pb")  # "Update to latest" moved the core onto a module of that name
    calls = len(git.specs)

    listed = {m.id: m for m in tortoise_modules.store().load_all("module")}
    with pytest.raises(ApplyRefusal) as refused:
        applier.update(listed["mod-pb"])

    said = str(refused.value)
    assert said.count("mod-pb") >= 2 and "src/tortoise-wow/modules/mod-pb" in said
    assert "Remove" in said
    assert len(git.specs) == calls, "nothing was fetched"
    assert _git(server / "modules" / "mod-pb", "rev-parse", "HEAD") == first
    ledger = module_moves.read(server)
    assert ledger is None or (not ledger.moves and not ledger.skipped)


@needs_git
def test_a_module_named_like_a_core_module_can_still_be_removed(tmp_path: Path) -> None:
    applier, origin, server, _git_seam = _rig(tmp_path)
    _publish(origin, "v1")
    tortoise_modules.install_custom(applier)(tortoise_modules.derive_link(URL), None)
    _core_has(server, "mod-pb")

    listed = {m.id: m for m in tortoise_modules.store().load_all("module")}
    applier.remove(listed["mod-pb"])

    assert not (server / "modules" / "mod-pb").exists()


# ---------------------------------------------------------------- the rebuild


def test_the_rebuild_refuses_a_module_the_core_has_since_gained_naming_both(
    tmp_path: Path,
) -> None:
    server = tmp_path / "srv"
    (server / "modules" / "mod-pb").mkdir(parents=True)
    _core_has(server, "Mod-PB")

    with pytest.raises(InstallerError) as refused:
        list(_installer()._write_dockerfile(context(server)))

    said = str(refused.value)
    assert "mod-pb" in said and "Mod-PB" in said
    assert "src/tortoise-wow/modules/Mod-PB" in said
    assert not (server / "Dockerfile").exists(), "refused before the recipe was written"


def test_the_rebuild_goes_on_when_no_module_shares_a_name(tmp_path: Path) -> None:
    server = tmp_path / "srv"
    (server / "modules" / "mod-pb").mkdir(parents=True)
    _core_has(server, "TortoiseBots")
    said = list(_installer()._write_dockerfile(context(server)))
    assert "Wrote Dockerfile" in said


def test_the_rebuild_question_is_not_asked_when_the_press_would_refuse(tmp_path: Path) -> None:
    server = tmp_path / "srv"
    (server / "modules" / "mod-pb").mkdir(parents=True)
    _core_has(server, "mod-pb")

    refusal = _installer().rebuild_refusal_before_asking(server)

    assert refusal is not None and "src/tortoise-wow/modules/mod-pb" in refusal


def test_the_rebuild_question_is_asked_as_before_when_all_is_well(tmp_path: Path) -> None:
    server = tmp_path / "srv"
    (server / "modules" / "mod-pb").mkdir(parents=True)
    assert _installer().rebuild_refusal_before_asking(server) is None


@needs_git
def test_an_addon_with_a_core_modules_name_is_not_a_server_module_and_is_not_refused(
    tmp_path: Path,
) -> None:
    """Only `<server>/modules/` is laid over the core's; an add-on is cloned elsewhere."""
    applier, origin, server, _git_seam = _rig(tmp_path)
    (origin / "MobStats.toc").write_text("## Interface: 11200\n", encoding="utf-8")
    (origin / "MobStats.lua").write_text("-- v1\n", encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "v1")
    _core_has(server, "MobStats")

    tortoise_modules.install_custom(applier)(
        tortoise_modules.derive_link("https://github.com/you/MobStats"), None
    )

    assert (server / "sql_scripts" / "clones" / "mobstats" / "MobStats.toc").is_file()


def test_the_folder_read_is_the_core_checkouts_modules_in_the_catalog() -> None:
    from yulon.catalog.catalog import load_catalog

    core = load_catalog().get("wow-tortoise").emulator.sources[0].dest
    assert f"{core}/modules" == autoupdate.CORE_MODULES_DIR


def test_a_recipe_that_does_not_lay_modules_over_the_core_refuses_nothing(tmp_path: Path) -> None:
    from .test_families_cmangos import ENTRY, Recorder, rooted
    from .test_tortoise_module_build import _plant

    root = tmp_path / "templates"
    _plant(root, "FROM ubuntu:24.04\nCOPY src/x /src\n")
    server = tmp_path / "srv"
    (server / "modules" / "mod-pb").mkdir(parents=True)
    # same name, but nothing mixes the two
    _core_has(server, "mod-pb", core=ENTRY.emulator.sources[0].dest)

    engine = rooted(root, Recorder())
    assert engine.rebuild_refusal_before_asking(server) is None
    assert "Wrote Dockerfile" in list(engine._write_dockerfile(context(server)))
