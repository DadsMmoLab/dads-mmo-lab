"""The Tortoise core's failed-update pause is carried out of the build (T600).

`src/shared/Database/AutoUpdater.cpp` of tortoise-wow at the pin ends a failed
migration with `std::getline(std::cin, line)` before it returns false. The world
container has `stdin_open` and `tty` (the Console tab needs them), so the read
never returns: the world sits there before its signal handler, SOAP and console
exist, and the failure line that would say why is never printed. Shyalya's fork
dropped the read in `b405b10adb`; tortoise-wow never did. The fix is carried as
a catalog patch (`wow-tortoise/patches/updater-no-wait-for-enter.patch`).

The fixture under `tests/fixtures/tortoise-187af788/` is that one file, byte for
byte, from tortoise-wow at `187af788` (the pin), read on 2026-10-09.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from yulon import resources
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import patch

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tortoise-187af788"
REL = Path("src") / "shared" / "Database" / "AutoUpdater.cpp"
PATCH_FILE = "wow-tortoise/patches/updater-no-wait-for-enter.patch"
CORE_DEST = "src/tortoise-wow"
AUDITED_PIN = "187af788177aa2f9f0e61eb8c5b9653d8f4f7199"
"""The core commit whose `AutoUpdater.cpp` was read for this patch (2026-10-09).

A pin bump (T597 is the next) must fail `test_the_pin_is_the_one_whose_updater_was_read`
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
    assert "AutoUpdater.cpp" in reason and "187af788" in reason and "b405b10" in reason


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
        "replace the fixture under tests/fixtures/tortoise-187af788 and AUDITED_PIN; if it "
        "does not, drop the patch row, the patch file and the fixture together (T600)."
    )
