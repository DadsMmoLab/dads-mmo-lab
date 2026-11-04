"""T867: mod-ale compiles on Conquest of Azeroth with Yu'lon's own patch, for one compile.

Zyth45/azerothcore-wotlk-coa changed hooks and menus no mod-ale release follows: the
core's `UnitScript::DealDamage` takes a fifth parameter (c9553af67), its `PlayerMenu`
holds a `std::mutex` and is not copyable, there is no `WorldSession::IsHeadless()`, and
its mod-playerbots fork still has the pre-#2854 member names. The fix ships as
`catalog/installers/wow-coa/patches/mod-ale-coa.patch`, is laid by
`ale_playerbots.bridge()` for the compile alone, shares that module's record, and is
put back when the compile returns.

The fixture is built from the SHIPPED patch itself: for every file, its hunks'
pre-images (or post-images) joined by a unique separator line. Nothing here is a real
mod-ale checkout; this is evidence about what each press writes, what it refuses, and
in what order.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tests.support_native import ENTRY, Recorder
from tests.test_apply import _installed_clone
from yulon import ale_playerbots, docker, resources
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry, ModulePatch
from yulon.catalog.families import patch
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.git import git_available

PATCH_FILE = "wow-coa/patches/mod-ale-coa.patch"
PATCH_SHA256 = "d6098aec49abddc2cf97427cf98bcfdbce7910afcedb259f220819539d1f21f3"
ALE = ale_playerbots.ALE
SC_PATH = "src/ALE_SC.cpp"
METHODS_PATH = "src/LuaEngine/methods/PlayerMethods.h"
LAST_PATH = "src/LuaEngine/methods/Playerbots/RandomPlayerBotMgrMethods.h"
SESSION_HEADER = "src/server/game/Server/WorldSession.h"
SESSION_TEXT = "class WorldSession\n{\npublic:\n    bool IsBot();\n};\n"

PATCH_TEXT = (resources.installers_dir() / PATCH_FILE).read_text(encoding="utf-8")


def _trees(text: str) -> tuple[dict[str, str], dict[str, str]]:
    """Upstream and patched text per file, built from the patch's own hunks.

    Each file is its hunks' lines in order, a unique `// --- n ---` separator
    between hunks so a repeated context block stays unambiguous; LF.
    """
    by_file: dict[str, list[patch.Hunk]] = {}
    for hunk in patch.parse(text):
        by_file.setdefault(hunk.path, []).append(hunk)
    upstream: dict[str, str] = {}
    patched: dict[str, str] = {}
    for path, hunks in by_file.items():
        before: list[str] = []
        after: list[str] = []
        for number, hunk in enumerate(hunks):
            if number:
                before.append(f"// --- {number} ---")
                after.append(f"// --- {number} ---")
            before.extend(hunk.before)
            after.extend(hunk.after)
        upstream[path] = "\n".join(before) + "\n"
        patched[path] = "\n".join(after) + "\n"
    return upstream, patched


UPSTREAM, PATCHED = _trees(PATCH_TEXT)
PATCHES = (ale_playerbots.CompilePatch(PATCH_FILE, PATCH_TEXT, "Fits it."),)


def scratch_entry(**block: Any) -> CatalogEntry:
    """WotLK's entry with an AzerothCore block carrying `block`, VALIDATED as catalog data is."""
    data = ENTRY.model_dump(mode="json", by_alias=True, exclude_none=True)
    data["id"] = "wow-scratch-ac"
    data["install"]["native"]["azerothcore"].update(block)
    return CatalogEntry.model_validate(data)


MODULE_PATCHES = [
    {"file": PATCH_FILE, "module": "mod-ale", "reason": "Fits it."},
]


def lay_mod_ale(server_dir: Path, texts: Mapping[str, str]) -> None:
    """A server folder with mod-ale's `src/` holding `texts` (module-relative paths)."""
    for relative, text in texts.items():
        path = server_dir / ALE / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def tree_bytes(server_dir: Path) -> dict[str, bytes]:
    """Every file under mod-ale's `src/`, by module-relative path."""
    root = server_dir / ALE / "src"
    return {
        path.relative_to(server_dir / ALE).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def make(
    entry: CatalogEntry, rec: Recorder, installers: Path | None = None
) -> AzerothCoreInstaller:
    return AzerothCoreInstaller(
        entry,
        installers_root=installers or resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(),
    )


def installed(
    tmp_path: Path, installers: Path | None = None
) -> tuple[Recorder, Path, AzerothCoreInstaller]:
    """The scratch entry installed on the doubles (its build included)."""
    rec = Recorder(images=False)
    server_dir = tmp_path / "server"
    made = make(scratch_entry(module_patches=MODULE_PATCHES), rec, installers)
    list(made.run(InstallOptions(server_dir=server_dir)))
    return rec, server_dir, made


def recording_build(
    rec: Recorder,
    server_dir: Path,
    seen: dict[str, bytes],
    records: list[dict[str, Any]],
    result: int = 0,
) -> Any:
    """A build seam that records mod-ale's bytes and the bridge record as it stands."""

    def build(*args: object, **kwargs: object) -> docker.AttachedRun:
        seen.update(tree_bytes(server_dir))
        record = server_dir / ale_playerbots.RECORD
        if record.is_file():
            records.append(json.loads(record.read_text(encoding="utf-8")))
        rec.calls.append("build")
        return docker.AttachedRun(result, ("built",) if result == 0 else ("error: boom",))

    return build


def build_ctx(made: AzerothCoreInstaller, server_dir: Path) -> native.StageContext:
    """The install's own `build` stage context, with the skip rule out of play."""
    return native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(made.entry.id, made._install_id(server_dir)),
        cancel=None,
        secrets=native.Secrets(db_password="pw"),
        force_build=True,
    )


def patched_lines(said: list[str]) -> list[str]:
    return [line for line in said if line.startswith("Yu'lon builds mod-ale with its fix")]


# -- the catalog names it, and the patch file is the proven one ---------------


def test_the_coa_entry_carries_the_mod_ale_patch_for_the_compile() -> None:
    from yulon.catalog import catalog

    coa = catalog.load_catalog().get("wow-coa")
    assert coa.install.native is not None and coa.install.native.azerothcore is not None
    specs = coa.install.native.azerothcore.module_patches
    assert len(specs) == 1, specs
    spec = specs[0]
    assert spec.file == PATCH_FILE and spec.module == "mod-ale"
    assert spec.reason.endswith(".")
    file = resources.installers_dir() / spec.file
    assert file.is_file()
    assert hashlib.sha256(file.read_bytes()).hexdigest() == PATCH_SHA256
    hunks = patch.parse(file.read_text(encoding="utf-8"))
    assert len(hunks) == 17
    assert len({hunk.path for hunk in hunks}) == 11
    assert not any(hunk.creates for hunk in hunks)


def test_a_module_patch_names_a_module_and_stays_inside_installers() -> None:
    with pytest.raises(ValidationError):
        ModulePatch(file="wow-coa/patches/x.patch", module="mod-x", reason="Fits it.")
    with pytest.raises(ValidationError):
        ModulePatch(file="../x.patch", module="mod-ale", reason="Fits it.")


# -- the compile sees the patched text, the disk never keeps it ---------------


def test_a_rebuild_compiles_patched_mod_ale_and_leaves_upstreams_files(
    tmp_path: Path,
) -> None:
    rec, server_dir, made = installed(tmp_path)
    lay_mod_ale(server_dir, UPSTREAM)
    rec.images = True
    seen: dict[str, bytes] = {}
    records: list[dict[str, Any]] = []
    made._seams = rec.seams(build=recording_build(rec, server_dir, seen, records))

    said = list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert seen == {name: text.encode("utf-8") for name, text in PATCHED.items()}
    assert tree_bytes(server_dir) == {name: text.encode("utf-8") for name, text in UPSTREAM.items()}
    assert not (server_dir / ale_playerbots.RECORD).exists()
    assert len(patched_lines(said)) == 1, said
    folded = [line for line in said if line.startswith("mod-ale's changes for the compile")]
    assert folded == [
        "mod-ale's changes for the compile were taken back out (11 files); its folder is as "
        "mod-ale ships it."
    ], said


def test_the_install_build_stage_lays_the_patch_too(tmp_path: Path) -> None:
    rec, server_dir, made = installed(tmp_path)
    lay_mod_ale(server_dir, UPSTREAM)
    seen: dict[str, bytes] = {}
    records: list[dict[str, Any]] = []
    made._seams = rec.seams(build=recording_build(rec, server_dir, seen, records))

    list(made.stage_named("build").run(build_ctx(made, server_dir)))

    assert seen == {name: text.encode("utf-8") for name, text in PATCHED.items()}
    assert tree_bytes(server_dir) == {name: text.encode("utf-8") for name, text in UPSTREAM.items()}


def test_a_failed_compile_still_puts_upstreams_files_back(tmp_path: Path) -> None:
    rec, server_dir, made = installed(tmp_path)
    lay_mod_ale(server_dir, UPSTREAM)
    rec.images = True
    seen: dict[str, bytes] = {}
    records: list[dict[str, Any]] = []
    made._seams = rec.seams(build=recording_build(rec, server_dir, seen, records, result=1))

    with pytest.raises(InstallerError):
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert seen == {name: text.encode("utf-8") for name, text in PATCHED.items()}
    assert tree_bytes(server_dir) == {name: text.encode("utf-8") for name, text in UPSTREAM.items()}
    assert not (server_dir / ale_playerbots.RECORD).exists()


def test_a_press_closed_at_the_patch_line_still_puts_upstreams_files_back(
    tmp_path: Path,
) -> None:
    rec, server_dir, made = installed(tmp_path)
    lay_mod_ale(server_dir, UPSTREAM)
    rec.images = True
    seen: dict[str, bytes] = {}
    records: list[dict[str, Any]] = []
    made._seams = rec.seams(build=recording_build(rec, server_dir, seen, records))

    press = made.rebuild(InstallOptions(server_dir=server_dir))
    for line in press:
        if line.startswith("Yu'lon builds mod-ale with its fix"):
            assert tree_bytes(server_dir) == {
                name: text.encode("utf-8") for name, text in PATCHED.items()
            }
            break
    else:
        pytest.fail("the patch was never laid")

    press.close()

    assert tree_bytes(server_dir) == {name: text.encode("utf-8") for name, text in UPSTREAM.items()}
    assert not (server_dir / ale_playerbots.RECORD).exists()


# -- a mod-ale the patch no longer fits stops before anything is written ------


def _broken(texts: Mapping[str, str], path: str) -> dict[str, str]:
    """`texts` with one pre-image line of `path` swapped for something else."""
    hunk = next(one for one in patch.parse(PATCH_TEXT) if one.path == path)
    line = hunk.before[0]
    changed = dict(texts)
    changed[path] = texts[path].replace(line, "// upstream moved", 1)
    assert changed[path] != texts[path]
    return changed


def test_a_mod_ale_the_patch_no_longer_fits_stops_before_the_compile_and_changes_nothing(
    tmp_path: Path,
) -> None:
    rec, server_dir, made = installed(tmp_path)
    lay_mod_ale(server_dir, _broken(UPSTREAM, SC_PATH))
    rec.images = True
    before = tree_bytes(server_dir)
    seen: dict[str, bytes] = {}
    records: list[dict[str, Any]] = []
    made._seams = rec.seams(build=recording_build(rec, server_dir, seen, records))
    rec.calls.clear()

    with pytest.raises(InstallerError) as raised:
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    said = str(raised.value)
    assert "mod-ale" in said and "Update Yu'lon" in said, said
    assert "Remove" in said and "Nothing was built" in said, said
    assert SC_PATH in said, said
    assert seen == {} and "build" not in rec.calls
    assert tree_bytes(server_dir) == before
    assert not (server_dir / ale_playerbots.RECORD).exists()


def test_a_second_file_that_no_longer_fits_writes_not_even_the_first(
    tmp_path: Path,
) -> None:
    rec, server_dir, made = installed(tmp_path)
    lay_mod_ale(server_dir, _broken(UPSTREAM, LAST_PATH))
    rec.images = True
    before = tree_bytes(server_dir)
    seen: dict[str, bytes] = {}
    records: list[dict[str, Any]] = []
    made._seams = rec.seams(build=recording_build(rec, server_dir, seen, records))
    rec.calls.clear()

    with pytest.raises(InstallerError, match="Nothing was built"):
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert seen == {} and "build" not in rec.calls
    assert tree_bytes(server_dir) == before
    assert not (server_dir / ale_playerbots.RECORD).exists()


# -- where the patch is not wanted -------------------------------------------


def test_a_server_without_mod_ale_builds_without_reading_the_patch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "installers"
    shutil.copytree(resources.installers_dir(), root)
    (root / PATCH_FILE).unlink()
    rec, server_dir, made = installed(tmp_path, root)
    rec.images = True
    seen: dict[str, bytes] = {}
    records: list[dict[str, Any]] = []
    made._seams = rec.seams(build=recording_build(rec, server_dir, seen, records))
    rec.calls.clear()

    said = list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert "build" in rec.calls
    assert seen == {}
    assert not any("mod-ale-coa.patch" in line for line in said), said
    assert patched_lines(said) == []


def test_wotlk_compiles_mod_ale_exactly_as_before(tmp_path: Path) -> None:
    """D6: the same server, mod-ale present, and no `module_patches` on the entry."""
    rec = Recorder(images=False)
    server_dir = tmp_path / "server"
    made = AzerothCoreInstaller(
        ENTRY,
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(),
    )
    list(made.run(InstallOptions(server_dir=server_dir)))
    lay_mod_ale(server_dir, UPSTREAM)
    rec.images = True
    seen: dict[str, bytes] = {}
    records: list[dict[str, Any]] = []
    made._seams = rec.seams(build=recording_build(rec, server_dir, seen, records))

    said = list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert seen == {name: text.encode("utf-8") for name, text in UPSTREAM.items()}
    assert patched_lines(said) == []
    assert not (server_dir / ale_playerbots.RECORD).exists()


# -- a hunk already present is left; one record holds upstream's text ---------


def test_a_hunk_mod_ale_already_carries_is_left_and_the_rest_laid(tmp_path: Path) -> None:
    rec, server_dir, made = installed(tmp_path)
    already = dict(UPSTREAM, **{SC_PATH: PATCHED[SC_PATH]})
    lay_mod_ale(server_dir, already)
    rec.images = True
    seen: dict[str, bytes] = {}
    records: list[dict[str, Any]] = []
    made._seams = rec.seams(build=recording_build(rec, server_dir, seen, records))

    list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert seen[SC_PATH] == PATCHED[SC_PATH].encode("utf-8")
    assert records and f"{ALE}/{SC_PATH}" not in records[0]["files"], records
    assert tree_bytes(server_dir)[SC_PATH] == PATCHED[SC_PATH].encode("utf-8")


def test_the_patch_and_the_name_bridge_share_one_record_of_upstreams_text(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "server"
    lay_mod_ale(server_dir, UPSTREAM)
    header = server_dir / SESSION_HEADER
    header.parent.mkdir(parents=True)
    header.write_text(SESSION_TEXT, encoding="utf-8")

    ale_playerbots.bridge(server_dir, PATCHES)

    methods = server_dir / ALE / METHODS_PATH
    assert methods.read_text(encoding="utf-8") == PATCHED[METHODS_PATH]
    record = json.loads((server_dir / ale_playerbots.RECORD).read_text(encoding="utf-8"))
    assert record["files"][f"{ALE}/{METHODS_PATH}"]["original"] == UPSTREAM[METHODS_PATH]

    ale_playerbots.put_back(server_dir)

    assert methods.read_text(encoding="utf-8") == UPSTREAM[METHODS_PATH]


# -- the Modules tab's Update and the record ---------------------------------


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
def test_a_leftover_patch_is_put_back_before_the_modules_tab_update_asks_git(
    tmp_path: Path,
) -> None:
    applier, manifest, clone = _installed_clone(tmp_path)
    tracked = clone / "src" / "LuaEngine" / "ALEConfig.cpp"
    original = tracked.read_text(encoding="utf-8")
    first = original.split("\n", 1)[0]
    tiny = (
        "--- a/src/LuaEngine/ALEConfig.cpp\n"
        "+++ b/src/LuaEngine/ALEConfig.cpp\n"
        "@@ -1,1 +1,2 @@\n"
        f" {first}\n"
        "+// patched by Yu'lon\n"
    )

    list(
        ale_playerbots.bridge(
            applier.server_dir, (ale_playerbots.CompilePatch("tiny", tiny, "Fits it."),)
        )
    )
    assert tracked.read_text(encoding="utf-8") != original

    applier.update(manifest)

    assert tracked.read_text(encoding="utf-8") == original
    assert not (applier.server_dir / ale_playerbots.RECORD).exists()


@pytest.mark.skipif(
    platform.system() == "Windows", reason="a Windows link needs privileges to make"
)
def test_a_linked_file_the_patch_names_refuses_with_nothing_written(tmp_path: Path) -> None:
    server_dir = tmp_path / "server"
    laid = dict(UPSTREAM)
    del laid[SC_PATH]
    lay_mod_ale(server_dir, laid)
    outside = tmp_path / "elsewhere.cpp"
    outside.write_text(UPSTREAM[SC_PATH], encoding="utf-8")
    link = server_dir / ALE / SC_PATH
    link.symlink_to(outside)

    with pytest.raises(InstallerError) as raised:
        ale_playerbots.bridge(server_dir, PATCHES)

    assert SC_PATH in str(raised.value) and "Nothing was built" in str(raised.value)
    assert outside.read_text(encoding="utf-8") == UPSTREAM[SC_PATH]
    assert link.is_symlink()
    assert not (server_dir / ale_playerbots.RECORD).exists()
    assert tree_bytes(server_dir) == {name: text.encode("utf-8") for name, text in laid.items()}
