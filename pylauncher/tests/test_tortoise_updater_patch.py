"""The Tortoise core's failed-update pause is carried out of the build (T600).

`src/shared/Database/AutoUpdater.cpp` of tortoise-wow at the pin ends a failed
migration with `std::getline(std::cin, line)` before it returns false. The world
container has `stdin_open` and `tty` (the Console tab needs them), so the read
never returns: the world sits there before its signal handler, SOAP and console
exist, and the failure line that would say why is never printed. Shyalya's fork
dropped the read in `b405b10adb`; tortoise-wow never did. The fix is carried as
a catalog patch (`wow-tortoise/patches/updater-no-wait-for-enter.patch`).

The fixture under `tests/fixtures/tortoise-6131a26f/` is that one file, byte for
byte, from tortoise-wow at `187af788`, read on 2026-10-09; the file is byte-identical at
`6131a26f`, the pin since T656 (compared 2026-10-10), so the folder carries the new pin's name.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.support_native import Recorder
from yulon import resources
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import patch
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tortoise-6131a26f"
REL = Path("src") / "shared" / "Database" / "AutoUpdater.cpp"
PATCH_FILE = "wow-tortoise/patches/updater-no-wait-for-enter.patch"
CORE_DEST = "src/tortoise-wow"
AUDITED_PIN = "6131a26f91d60e64f6d58c7f0a9b6333f2d9f25a"
"""The core commit whose `AutoUpdater.cpp` was read for this patch (187af788 on 2026-10-09,
6131a26f on 2026-10-10 for T656: the same bytes, the read still there).

A pin bump must fail `test_the_pin_is_the_one_whose_updater_was_read`
until somebody re-reads the file at the new pin: still has the read -> keep the
patch and replace the fixture; the read is gone -> drop the patch row, the patch
file and the fixture together.
"""


def _native():
    entry = load_catalog().get("wow-tortoise")
    assert entry.install.native is not None and entry.install.native.cmangos is not None
    return entry, entry.install.native.cmangos


def _checkout(tmp_path: Path) -> Path:
    root = tmp_path / "core"
    (root / REL).parent.mkdir(parents=True)
    shutil.copyfile(FIXTURE / REL, root / REL)
    return root


def _shipped_text() -> str:
    return (resources.installers_dir() / PATCH_FILE).read_text(encoding="utf-8")


def test_the_fixture_has_the_blocking_read_the_patch_exists_for() -> None:
    """The control: a fixture without the read would make every test below vacuous."""
    text = (FIXTURE / REL).read_text(encoding="utf-8")
    assert text.count("std::getline(std::cin, line);") == 1
    assert "failed to apply." in text


def test_wow_tortoise_carries_the_updater_patch_against_the_core() -> None:
    _entry, block = _native()
    assert [(p.file, p.source) for p in block.patches] == [(PATCH_FILE, CORE_DEST)]
    reason = block.patches[0].reason
    assert "AutoUpdater.cpp" in reason and "6131a26f" in reason and "b405b10" in reason


def test_the_patch_leaves_no_read_of_stdin_after_a_failed_migration(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    results = patch.apply(_shipped_text(), root, name="the updater patch")
    assert [r.path for r in results] == [str(REL).replace("\\", "/")]
    after = (root / REL).read_text(encoding="utf-8")
    assert "std::cin" not in after and "getline" not in after
    # the failure is still logged and still reported to the caller
    assert 'sLog.outError("[DB Auto-Updater] Migration %s with hash %s failed to apply."' in after
    failed = after.split("failed to apply.")[1].split("}")[0]
    assert "return false;" in failed


def test_the_patch_changes_nothing_else_in_the_file(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    patch.apply(_shipped_text(), root, name="the updater patch")
    before = (FIXTURE / REL).read_text(encoding="utf-8").splitlines()
    after = (root / REL).read_text(encoding="utf-8").splitlines()
    removed = [line for line in before if line not in after]
    gone = ["std::string line;", "std::getline(std::cin, line);"]
    assert [line.strip() for line in removed] == gone
    assert len(after) == len(before)  # two lines out, two comment lines in


def test_applying_it_twice_writes_nothing_the_second_time(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    patch.apply(_shipped_text(), root, name="the updater patch")
    once = (root / REL).read_bytes()
    patch.apply(_shipped_text(), root, name="the updater patch")
    assert (root / REL).read_bytes() == once


def test_a_core_that_moved_under_the_patch_is_refused_by_file(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    path = root / REL
    path.write_text(
        path.read_text(encoding="utf-8").replace("failed to apply.", "could not be applied."),
        encoding="utf-8",
    )
    with pytest.raises(patch.PatchError, match="AutoUpdater.cpp"):
        patch.apply(_shipped_text(), root, name="the updater patch")


def test_the_pin_is_the_one_whose_updater_was_read() -> None:
    """Relationship: the patch is measured against a commit; a new pin must be re-read (T597)."""
    entry, _block = _native()
    core = next(s for s in entry.emulator.sources if s.dest == CORE_DEST)
    assert core.rev == AUDITED_PIN, (
        f"wow-tortoise's core is now pinned at {core.rev}, and the updater patch was measured "
        f"at {AUDITED_PIN}. Re-read src/shared/Database/AutoUpdater.cpp at the new pin: if "
        "ProcessTargetUpdates still calls std::getline(std::cin, ...) after 'failed to apply.', "
        "replace the fixture folder (tests/fixtures/tortoise-<pin>) and AUDITED_PIN; if it "
        "does not, drop the patch row, the patch file and the fixture together (T600)."
    )


# -- the patch is skipped, not refused, when upstream has already removed the read ----------


def _engine() -> CmangosInstaller:
    entry, _block = _native()
    return CmangosInstaller(entry, seams=Recorder().seams(platform_id=lambda: "linux"))


def _server(tmp_path: Path, edit: Callable[[str], str] | None = None) -> Path:
    server = tmp_path / "srv"
    target = server / CORE_DEST / REL
    target.parent.mkdir(parents=True)
    text = (FIXTURE / REL).read_text(encoding="utf-8")
    target.write_text(edit(text) if edit else text, encoding="utf-8")
    return server


def _upstream_fixed(text: str) -> str:
    """Upstream's own fix: the read gone, the lines around it rewritten by them."""
    return text.replace(
        "                std::string line;\n                std::getline(std::cin, line);\n"
        "                return false;\n",
        "                return false; // upstream: no wait\n",
    )


def _moved_but_still_blocking(text: str) -> str:
    """Upstream edited next to the read and left it in: the patch no longer applies."""
    return text.replace(
        "                std::string line;\n",
        '                std::string line;\n                sLog.outError("more");\n',
    ).replace("failed to apply.", "failed to apply!")


def test_the_catalog_names_the_read_whose_absence_makes_the_patch_obsolete() -> None:
    _entry, block = _native()
    assert block.patches[0].obsolete_when_absent == "std::getline(std::cin"


def test_an_update_whose_new_rev_already_dropped_the_read_skips_the_patch_with_a_note(
    tmp_path: Path,
) -> None:
    """Upstream's own fix must not refuse the whole update.

    Mutation: drop the obsolete check in `_resolve()` and the dry run raises, naming the file.
    """
    server = _server(tmp_path, _upstream_fixed)
    before = (server / CORE_DEST / REL).read_bytes()
    engine = _engine()
    checked = list(engine.check_carried_patches(server))
    applied = list(engine.apply_carried_patches(server))
    assert any("no longer has" in line for line in checked + applied), (checked, applied)
    assert (server / CORE_DEST / REL).read_bytes() == before


def test_a_new_rev_that_still_blocks_but_moved_under_the_patch_is_still_refused(
    tmp_path: Path,
) -> None:
    """The hang would come back, so the update stays refused and names the file.

    Mutation: treat every non-applying patch as obsolete and this builds a hanging core.
    """
    server = _server(tmp_path, _moved_but_still_blocking)
    with pytest.raises(InstallerError, match="AutoUpdater.cpp"):
        list(_engine().check_carried_patches(server))


def test_the_stage_and_a_second_press_agree_when_the_read_is_already_gone(
    tmp_path: Path,
) -> None:
    """The install stage on a checkout this patch (or upstream) already fixed: a note, no error."""
    server = _server(tmp_path)
    engine = _engine()
    from tests.test_families_cmangos import context

    list(engine.apply_carried_patches(server))  # patched now
    said = list(engine._patch_sources(context(server)))
    assert any("no longer has" in line or "already carries" in line for line in said), said
