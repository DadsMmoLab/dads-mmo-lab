"""T119: every Accountwide Systems install was refused after T92 (#202).

T92 made Install run a module's configure-time steps as its first configure.
accountwide's reputation flag is a configure-time patch of `AccountReputation.lua`,
and upstream (Aldori15/azerothcore-eluna-accountwide) ships no file by that
name: its two reputation scripts are `AccountReputation (default AC-Wotlk).lua`
and one for the Ashen Order server. So every install stopped with
`patch target missing: .../AccountReputation.lua`, after the clone and the
deploy. Measured with the real `Applier` over a real clone of upstream @fddbd8c.

The fixture here carries upstream's OWN file names. A patch whose target only
exists after a `deploy.rename` is traced back to the file upstream ships, so a
patch aimed at a name nobody ships fails here the way it fails for real.
"""

from __future__ import annotations

import re
from pathlib import Path

from yulon import apply as apply_module
from yulon.apply import Applier
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.git import CloneSpec
from yulon.manifest import Manifest

ACCOUNTWIDE_UPSTREAM = (
    "00_AccountWideUtils.lua",
    "AccountAchievements.lua",
    "AccountCurrency.lua",
    "AccountMoney.lua",
    "AccountMounts.lua",
    "AccountPets.lua",
    "AccountPlaytime.lua",
    "AccountProfessions.lua",
    "AccountPvPRank.lua",
    "AccountReputation (default AC-Wotlk).lua",
    "AccountReputation (modified for Ashen Order).lua",
    "AccountTaxiPaths.lua",
    "AccountTitles.lua",
)
"""`lua_scripts/AccountWide/` of Aldori15/azerothcore-eluna-accountwide @fddbd8c, listed 2026-09-24.

There is NO `AccountReputation.lua`: each reputation variant carries
`local ENABLE_ACCOUNTWIDE_REPUTATION = false` at line 7 under its own long name.
"""

_FLAG = re.compile(r"local\\s\+(\w+)")
"""The flag name out of a patch's `find` regex (`^(\\s*local\\s+ENABLE_...)(true|false)`)."""


class _Clone:
    """Writes the given files into the clone dir instead of cloning."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def is_unmodified(self, dest: Path, relative_path: str) -> bool | None:
        return True

    def no_local_commits(self, dest: Path, branch: str | None) -> bool | None:
        return True

    def clone(self, spec: CloneSpec) -> None:
        spec.dest.mkdir(parents=True, exist_ok=True)
        for rel, text in self.files.items():
            path = spec.dest / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (spec.dest / ".git").mkdir(exist_ok=True)


class _Sql:
    def run_file(self, db: str, path: Path) -> None:
        pass

    def run_statement(self, db: str, statement: str) -> None:
        pass


def _accountwide() -> Manifest:
    return wotlk_modules.store().load("ale", "accountwide")


def _flag(find: str) -> str:
    match = _FLAG.search(find)
    assert match is not None, find
    return match.group(1)


def _upstream_clone(manifest: Manifest) -> dict[str, str]:
    """Upstream's file names, each holding the `local ENABLE_... = false` lines its patches edit."""
    shipped_as = {new: old for step in manifest.deploy for old, new in step.rename}
    body = {name: "" for name in ACCOUNTWIDE_UPSTREAM}
    for patch in manifest.patches:
        name = Path(patch.file).name
        name = shipped_as.get(name, name)
        if name in body:
            body[name] += f"local {_flag(patch.find)} = false\n"
    body[
        "AccountReputation (modified for Ashen Order).lua"
    ] += "local ENABLE_ACCOUNTWIDE_REPUTATION = false\n"
    files = {f"lua_scripts/AccountWide/{n}": text or "-- lua\n" for n, text in body.items()}
    files["sql/create_accountwide_tables.sql"] = "-- tables\n"
    return files


def test_accountwide_installs_over_upstreams_own_file_names_with_every_flag_written(
    tmp_path: Path,
) -> None:
    """All 13 flag patches find their line in a file upstream ships.

    Mutation: drop the `AccountReputation.lua` rename from the manifest and the
    install raises `patch target missing: .../AccountReputation.lua` again.
    """
    manifest = _accountwide()
    (tmp_path / "modules" / "mod-ale").mkdir(parents=True)
    flags = [p.key for p in manifest.prompts if p.kind == "bool"]
    assert len(flags) == 13
    Applier(tmp_path, git=_Clone(_upstream_clone(manifest)), sql=_Sql()).install(
        manifest, {key: "true" for key in flags}
    )

    assert len(manifest.patches) == 13
    for patch in manifest.patches:
        text = (tmp_path / patch.file).read_text(encoding="utf-8")
        assert f"local {_flag(patch.find)} = true" in text, (patch.file, text)


def test_the_reputation_flag_lands_in_the_stock_script_under_the_name_its_patch_edits(
    tmp_path: Path,
) -> None:
    """The stock variant is deployed AS `AccountReputation.lua`; the long name is gone."""
    manifest = _accountwide()
    (tmp_path / "modules" / "mod-ale").mkdir(parents=True)
    Applier(tmp_path, git=_Clone(_upstream_clone(manifest)), sql=_Sql()).install(
        manifest, {"reputation": "true"}
    )
    deployed = tmp_path / "env/dist/etc/modules/lua_scripts/accountwide"
    assert "local ENABLE_ACCOUNTWIDE_REPUTATION = true" in (
        deployed / "AccountReputation.lua"
    ).read_text(encoding="utf-8")
    assert not (deployed / "AccountReputation (default AC-Wotlk).lua").exists()


def test_the_patch_targets_are_files_upstream_ships_or_a_rename_produces() -> None:
    """Every configure patch names a file that will exist after the deploy."""
    manifest = _accountwide()
    renamed = {new for step in manifest.deploy for _old, new in step.rename}
    for patch in manifest.patches:
        name = Path(patch.file).name
        assert name in ACCOUNTWIDE_UPSTREAM or name in renamed, name
    assert apply_module.required_prompts(manifest, "install")
