"""T596 step 2 (PR-B): Tortoise's image build takes `<server>/modules/` in (E3).

A module from outside is cloned to `<server>/modules/<name>` (`apply.CLONE_DIRS`), not
into the core's own checkout (`src/tortoise-wow`, which "Update to latest" resets). The
core compiles whatever has `src/` under ITS `modules/` (`-DMODULES=static`), so the
recipe lays the server's `modules/` over the core's in the builder before CMake runs.
Three files say so, and each is pinned here: the Dockerfile's `COPY`, the
`.dockerignore` that lets the folder into the context without its git history or this
app's claim file, and the stage that makes the folder exist so the `COPY` never fails
on a server that has no module yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import resources
from yulon.catalog import composegen
from yulon.catalog.build_context import Rules, parse_dockerignore
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.git import CLONE_MARKER

from .test_families_cmangos import ENTRY, Recorder, context, installable, rooted

TEMPLATES = resources.installers_dir() / "wow-tortoise" / "native"
TORTOISE = installable(load_catalog().get("wow-tortoise"))
COPY_LINE = "COPY modules/ /src/modules/"


def test_the_builder_lays_the_servers_modules_over_the_cores_before_cmake_runs() -> None:
    lines = (TEMPLATES / "Dockerfile.tmpl").read_text(encoding="utf-8").splitlines()
    code = [line for line in lines if line and not line.startswith("#")]
    assert COPY_LINE in code
    core = code.index("COPY src/tortoise-wow /src")
    mine = code.index(COPY_LINE)
    cmake = next(i for i, line in enumerate(code) if line.startswith("RUN cmake"))
    assert core < mine < cmake, "after the core tree it merges into, before the configure reads it"


def _rules() -> Rules:
    text = (TEMPLATES / "dockerignore.tmpl").read_text(encoding="utf-8")
    rules = parse_dockerignore(text)
    assert rules is not None
    return rules


def _sent(rules: Rules, rel: str) -> bool:
    parts = rel.split("/")
    decision = None
    for i in range(1, len(parts) + 1):
        decision = rules.decide("/".join(parts[:i]), decision)
    assert decision is not None
    return not decision.excluded


@pytest.mark.parametrize(
    "rel",
    [
        "modules/mod-a/src/a.cpp",
        "modules/tw-mod-b/conf/tw-mod-b.conf.dist",
        "modules/mod-a/data/sql/world/x.sql",
        "src/tortoise-wow/CMakeLists.txt",
    ],
)
def test_the_context_carries_the_modules_and_the_core(rel: str) -> None:
    assert _sent(_rules(), rel)


@pytest.mark.parametrize(
    "rel",
    [
        "modules/mod-a/.git",
        "modules/mod-a/.git/HEAD",
        f"modules/mod-a/{CLONE_MARKER}",
        "src/tortoise-wow/.git/HEAD",
        "src/tortoise-wow/modules/TortoiseBots/.git",
        "etc/mangosd.conf",
        "etc/modules/mod-a.conf",
        "client/Data/common.mpq",
        "sql_scripts/clones/mobstats/MobStats.toc",
    ],
)
def test_the_context_leaves_out_history_claims_and_everything_else(rel: str) -> None:
    assert not _sent(_rules(), rel)


def _plant(root: Path, body: str) -> None:
    """Marked templates for `wow-tbc`'s block, with the banner this project's own carry."""
    assert ENTRY.install.native is not None
    folder = root / str(ENTRY.install.native.dockerfile_dir)
    folder.mkdir(parents=True)
    marker = (TEMPLATES / "dockerignore.tmpl").read_text(encoding="utf-8").splitlines()[0] + "\n"
    (folder / "Dockerfile.tmpl").write_text(marker + body, encoding="utf-8", newline="\n")
    (folder / "dockerignore.tmpl").write_text(marker + "*\n", encoding="utf-8", newline="\n")


def _installer() -> CmangosInstaller:
    return CmangosInstaller(
        TORTOISE,
        installers_root=resources.installers_dir(),
        seams=Recorder().seams(platform_id=lambda: "linux"),
    )


def test_the_stage_makes_the_modules_folder_so_the_copy_never_fails(tmp_path: Path) -> None:
    server = tmp_path / "srv"
    server.mkdir()
    said = list(_installer()._write_dockerfile(context(server)))
    assert (server / "modules").is_dir()
    assert any("modules/" in line for line in said), said
    assert "Wrote Dockerfile" in said


def test_the_stage_leaves_a_modules_folder_that_has_modules_alone(tmp_path: Path) -> None:
    server = tmp_path / "srv"
    (server / "modules" / "mod-a").mkdir(parents=True)
    (server / "modules" / "mod-a" / "x.txt").write_text("keep\n", encoding="utf-8")
    list(_installer()._write_dockerfile(context(server)))
    assert (server / "modules" / "mod-a" / "x.txt").read_text(encoding="utf-8") == "keep\n"


def test_a_second_run_says_nothing_about_a_folder_that_is_already_there(tmp_path: Path) -> None:
    server = tmp_path / "srv"
    server.mkdir()
    list(_installer()._write_dockerfile(context(server)))
    again = list(_installer()._write_dockerfile(context(server)))
    assert not any("modules/" in line and "Made" in line for line in again), again


def test_a_game_whose_recipe_does_not_copy_modules_gets_no_modules_folder(tmp_path: Path) -> None:
    root = tmp_path / "templates"
    _plant(root, "FROM ubuntu:24.04\nCOPY src/x /src\n")
    server = tmp_path / "srv"
    server.mkdir()
    list(rooted(root, Recorder())._write_dockerfile(context(server)))
    assert not (server / "modules").exists()


def test_a_recipe_that_copies_modules_gets_the_folder_whatever_the_game(tmp_path: Path) -> None:
    root = tmp_path / "templates"
    _plant(root, f"FROM ubuntu:24.04\n{COPY_LINE}\n")
    server = tmp_path / "srv"
    server.mkdir()
    list(rooted(root, Recorder())._write_dockerfile(context(server)))
    assert (server / "modules").is_dir()


def test_a_modules_path_that_is_a_file_is_refused_in_words(tmp_path: Path) -> None:
    from yulon.catalog.installer import InstallerError

    server = tmp_path / "srv"
    server.mkdir()
    (server / "modules").write_text("not a folder\n", encoding="utf-8")
    with pytest.raises(InstallerError, match="modules"):
        list(_installer()._write_dockerfile(context(server)))


def test_the_marker_still_leads_both_files() -> None:
    for name in ("Dockerfile.tmpl", "dockerignore.tmpl"):
        assert (
            (TEMPLATES / name).read_text(encoding="utf-8").startswith(composegen.GENERATED_MARKER)
        )
