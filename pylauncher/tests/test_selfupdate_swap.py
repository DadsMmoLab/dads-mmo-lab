"""The helper that replaces the install after this process has quit (T90 plan 3).

Two halves. The first is about the PLAN — the argv list, and the fact that no
path appears in the script's text — and it is all offline. The second runs the
POSIX helper **for real**, on fake trees in `tmp_path`, with a `sleep` child
standing in for the app: it is the one place in this package where a real
subprocess is wanted, because "a shell script waits for a pid and renames two
directories" is not something reasoning can settle.

Nothing here is left running: every process started is waited for, and every
wait is against a deadline rather than a blind sleep.
"""

from __future__ import annotations

import os
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from yulon.selfupdate.cleanup import finish_previous_update
from yulon.selfupdate.detect import Install, InstallKind
from yulon.selfupdate.fetch import UpdateError
from yulon.selfupdate.stage import sibling
from yulon.selfupdate.swap import (
    POWERSHELL_UNDER_SYSTEMROOT,
    SwapPlan,
    plan_swap,
    powershell_path,
    start_helper,
)

HELPER_DEADLINE = 10.0
"""How long the real-helper tests wait for a condition before failing.

A deadlock breaker, not a claim about speed. Measured on this dev box: the
whole `sleep 0.3` + swap + relaunch sequence completes in about 0.6 s, so this
is over fifteen times the measured work.
"""


def _folder_install(root: Path, executable: str = "launch.sh") -> Install:
    target = root / "yulon"
    target.mkdir()
    return Install(InstallKind.TARBALL, target, executable, True)


def _until(condition: Callable[[], bool], what: str) -> None:
    """Poll `condition` to a deadline. Never a blind sleep, never unbounded."""
    deadline = time.monotonic() + HELPER_DEADLINE
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError(f"{what} did not happen within {HELPER_DEADLINE}s")


# -- the plan ----------------------------------------------------------------


def test_a_tarball_plan_is_sh_the_script_and_the_five_values(tmp_path: Path) -> None:
    install = _folder_install(tmp_path, "yulon")
    assert install.target is not None
    staged = sibling(install.target, ".new")
    scripts = tmp_path / "scripts"
    scripts.mkdir()

    plan = plan_swap(install, staged, pid=4242, script_dir=scripts, platform_id="linux")

    assert plan.argv == [
        "/bin/sh",
        str(plan.script),
        "4242",
        str(install.target),
        str(staged),
        str(install.target) + ".old",
        str(install.target / "yulon"),
    ]
    assert plan.script.parent == scripts
    assert plan.script.exists()
    assert stat.S_IMODE(plan.script.stat().st_mode) == 0o700


def test_an_appimage_plan_launches_the_file_itself(tmp_path: Path) -> None:
    target = tmp_path / "Yulon-v0.8.66-Public-x86_64.AppImage"
    target.write_bytes(b"old")
    install = Install(InstallKind.APPIMAGE, target, "", True)
    scripts = tmp_path / "scripts"
    scripts.mkdir()

    plan = plan_swap(install, sibling(target, ".new"), pid=7, script_dir=scripts)

    assert plan.argv[-1] == str(target), "an AppImage relaunches its own path, not a child of it"
    assert plan.argv[3] == str(target)


def test_a_windows_plan_runs_powershell_by_its_absolute_path_with_the_fixed_flags(
    tmp_path: Path,
) -> None:
    install = _folder_install(tmp_path, "yulon.exe")
    assert install.target is not None
    staged = sibling(install.target, ".new")
    scripts = tmp_path / "scripts"
    scripts.mkdir()

    plan = plan_swap(install, staged, pid=99, script_dir=scripts, platform_id="windows")

    assert plan.script.suffix == ".ps1"
    assert plan.argv[:8] == [
        powershell_path(),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-File",
    ]
    assert plan.argv[8:] == [
        str(plan.script),
        "99",
        str(install.target),
        str(staged),
        str(install.target) + ".old",
        str(install.target / "yulon.exe"),
    ]


def test_powershell_is_found_under_systemroot_and_never_on_the_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare `powershell.exe` is whatever the PATH resolves it to, which is not a decision.

    `SystemRoot` is read because that is where Windows itself says its own
    directory is; the tail under it is fixed, and a box with no `SystemRoot`
    falls back to `C:\\Windows` rather than to a name the PATH decides.
    """
    monkeypatch.delenv("SystemRoot", raising=False)
    assert powershell_path() == "C:\\Windows" + POWERSHELL_UNDER_SYSTEMROOT
    monkeypatch.setenv("SystemRoot", "D:\\Win")
    assert powershell_path() == "D:\\Win" + POWERSHELL_UNDER_SYSTEMROOT


@pytest.mark.parametrize("platform_id", ["linux", "windows"])
def test_a_hostile_folder_name_reaches_argv_unchanged_and_the_script_text_not_at_all(
    platform_id: str, tmp_path: Path
) -> None:
    """The rule the whole helper design exists for: paths are ARGUMENTS, never text.

    Interpolated into a shell script, `$(touch /tmp/x)` runs, `&` splits a
    command and `%VAR%` expands. As `argv` they are five strings the shell
    never parses. Asserted by ARGV — the list — and by the script text, which
    must not contain the target's path anywhere at all.
    """
    awkward = tmp_path / "a folder 'with' \"quotes\" $(x) & 100%"
    awkward.mkdir()
    target = awkward / "yulon"
    target.mkdir()
    install = Install(
        InstallKind.WINDOWS_ZIP if platform_id == "windows" else InstallKind.TARBALL,
        target,
        "yulon",
        True,
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()

    plan = plan_swap(
        install, sibling(target, ".new"), pid=5, script_dir=scripts, platform_id=platform_id
    )

    assert str(target) in plan.argv, "the target did not reach argv as one whole string"
    assert str(sibling(target, ".new")) in plan.argv
    text = plan.script.read_text(encoding="utf-8")
    for fragment in (str(target), str(awkward), "$(x)", "100%"):
        assert fragment not in text, f"{fragment!r} was pasted into the helper script"


@pytest.mark.parametrize(
    "install",
    [
        Install(InstallKind.SOURCE, None, "", False),
        Install(InstallKind.MACOS_APP, None, "", False),
        Install(InstallKind.TARBALL, Path("/opt/yulon"), "yulon", False),
    ],
)
def test_an_install_that_cannot_be_swapped_is_refused(install: Install, tmp_path: Path) -> None:
    with pytest.raises(UpdateError):
        plan_swap(install, tmp_path / "staged", pid=1, script_dir=tmp_path)


def test_start_helper_hands_the_argv_list_to_the_spawner_unchanged(tmp_path: Path) -> None:
    install = _folder_install(tmp_path, "yulon")
    assert install.target is not None
    plan = plan_swap(
        install, sibling(install.target, ".new"), pid=3, script_dir=tmp_path, platform_id="linux"
    )
    seen: list[list[str]] = []
    start_helper(plan, spawn=seen.append)
    assert seen == [plan.argv]


def test_the_detached_spawn_asks_for_a_session_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """The helper must outlive this process, which is the whole point of it.

    `Popen` is replaced rather than run: what is asserted is the keywords, and
    running it would start a real helper with nothing to wait for. The POSIX
    helper's real behaviour is measured further down, through a `Popen` the
    test can join.
    """
    from yulon.selfupdate import swap

    seen: dict[str, object] = {}

    def fake_popen(argv: list[str], **kwargs: object) -> object:
        seen["argv"] = argv
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(swap.subprocess, "Popen", fake_popen)
    swap._spawn_detached(["/bin/sh", "/tmp/x.sh"])

    assert seen["argv"] == ["/bin/sh", "/tmp/x.sh"]
    assert seen["stdin"] == subprocess.DEVNULL
    assert seen["stdout"] == subprocess.DEVNULL
    assert seen["stderr"] == subprocess.DEVNULL
    assert seen["close_fds"] is True
    if os.name == "posix":
        assert seen["start_new_session"] is True
        assert seen["creationflags"] == 0


# -- the POSIX helper, run for real ------------------------------------------

pytestmark_posix = pytest.mark.skipif(os.name != "posix", reason="the sh helper is POSIX only")


def _a_launcher(path: Path, label: str, witness: Path) -> None:
    """A tiny `sh` script that records that it was launched, and which tree it came from."""
    path.write_text(f'#!/bin/sh\nprintf %s {label} >> "{witness}"\n', encoding="utf-8")
    path.chmod(0o755)


def _run_helper(plan: SwapPlan, app: subprocess.Popen[bytes]) -> None:
    """Start the helper, reap the stand-in app, and join everything before returning.

    The app is reaped DELIBERATELY and early: a child that has exited but not
    been waited for is a zombie, and `kill -0 <zombie>` succeeds — so a test
    that left it would have the helper waiting out its whole 120 s bound.
    """
    helper = subprocess.Popen(plan.argv, stdin=subprocess.DEVNULL)
    try:
        app.wait(timeout=HELPER_DEADLINE)
        helper.wait(timeout=HELPER_DEADLINE)
    finally:
        for child in (app, helper):
            if child.poll() is None:  # pragma: no cover - only on a failing run
                child.kill()
                child.wait()
    assert app.poll() is not None and helper.poll() is not None


@pytestmark_posix
def test_the_helper_swaps_the_trees_relaunches_and_deletes_itself(tmp_path: Path) -> None:
    install = _folder_install(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "marker").write_text("old", encoding="utf-8")
    _a_launcher(target / "launch.sh", "old", witness)

    staged = sibling(target, ".new")
    staged.mkdir()
    (staged / "marker").write_text("new", encoding="utf-8")
    _a_launcher(staged / "launch.sh", "new", witness)

    app = subprocess.Popen(["sleep", "0.3"])
    plan = plan_swap(install, staged, pid=app.pid, script_dir=tmp_path, platform_id="linux")
    _run_helper(plan, app)

    _until(lambda: witness.exists(), "the new build was relaunched")
    assert (target / "marker").read_text(encoding="utf-8") == "new"
    assert sibling(target, ".old").joinpath("marker").read_text(encoding="utf-8") == "old"
    assert witness.read_text(encoding="utf-8") == "new", "the OLD build was relaunched"
    assert not staged.exists(), "the staged tree was left behind as well as moved"
    _until(lambda: not plan.script.exists(), "the helper script deleted itself")


@pytestmark_posix
def test_the_helper_puts_the_old_tree_back_when_the_new_one_is_not_there(tmp_path: Path) -> None:
    """The rollback arm: the second rename fails, so the first one is undone.

    Staged tree deleted between planning and running — which is what a disk
    that filled up, or a cleaner, or a second copy of Yu'lon looks like from
    the helper's side.
    """
    install = _folder_install(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "marker").write_text("old", encoding="utf-8")
    _a_launcher(target / "launch.sh", "old", witness)
    staged = sibling(target, ".new")
    assert not staged.exists(), "the precondition: there is no new tree to move"

    app = subprocess.Popen(["sleep", "0.3"])
    plan = plan_swap(install, staged, pid=app.pid, script_dir=tmp_path, platform_id="linux")
    _run_helper(plan, app)

    _until(lambda: witness.exists(), "the old build was relaunched")
    assert (target / "marker").read_text(encoding="utf-8") == "old", "the install was lost"
    assert witness.read_text(encoding="utf-8") == "old"
    assert not sibling(target, ".old").exists(), "the old tree was left under the .old name"


@pytestmark_posix
def test_the_helper_swaps_a_file_target_the_way_an_appimage_needs(tmp_path: Path) -> None:
    witness = tmp_path / "launched"
    target = tmp_path / "Yulon-v0.8.66-Public-x86_64.AppImage"
    _a_launcher(target, "old", witness)
    install = Install(InstallKind.APPIMAGE, target, "", True)
    staged = sibling(target, ".new")
    _a_launcher(staged, "new", witness)

    app = subprocess.Popen(["sleep", "0.3"])
    plan = plan_swap(install, staged, pid=app.pid, script_dir=tmp_path, platform_id="linux")
    _run_helper(plan, app)

    _until(lambda: witness.exists(), "the new AppImage was relaunched")
    assert witness.read_text(encoding="utf-8") == "new"
    assert "new" in target.read_text(encoding="utf-8")
    assert "old" in sibling(target, ".old").read_text(encoding="utf-8")
    assert target.name == "Yulon-v0.8.66-Public-x86_64.AppImage", (
        "the file keeps its old name on purpose, so a desktop entry or a shortcut "
        "pointing at it keeps working; the window title is what says the version"
    )


@pytestmark_posix
def test_the_helper_replaces_a_stale_old_tree_instead_of_refusing(tmp_path: Path) -> None:
    """A second update with an `.old` still beside the install must not stop there."""
    install = _folder_install(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "marker").write_text("old", encoding="utf-8")
    _a_launcher(target / "launch.sh", "old", witness)
    stale = sibling(target, ".old")
    stale.mkdir()
    (stale / "marker").write_text("older still", encoding="utf-8")
    staged = sibling(target, ".new")
    staged.mkdir()
    (staged / "marker").write_text("new", encoding="utf-8")
    _a_launcher(staged / "launch.sh", "new", witness)

    app = subprocess.Popen(["sleep", "0.3"])
    plan = plan_swap(install, staged, pid=app.pid, script_dir=tmp_path, platform_id="linux")
    _run_helper(plan, app)

    _until(lambda: witness.exists(), "the new build was relaunched")
    assert (target / "marker").read_text(encoding="utf-8") == "new"
    assert stale.joinpath("marker").read_text(encoding="utf-8") == "old"


# -- cleanup -----------------------------------------------------------------


def test_the_first_start_after_an_update_removes_the_old_tree(tmp_path: Path) -> None:
    install = _folder_install(tmp_path)
    assert install.target is not None
    old = sibling(install.target, ".old")
    old.mkdir()
    (old / "yulon").write_text("the previous build", encoding="utf-8")
    keep = tmp_path / "something-else"
    keep.write_text("x", encoding="utf-8")

    assert finish_previous_update(install) is True
    assert not old.exists()
    assert install.target.exists(), "the install itself was removed"
    assert keep.exists(), "something beside the install was removed"


def test_an_appimage_leaves_its_old_file_behind_and_that_is_removed_too(tmp_path: Path) -> None:
    target = tmp_path / "Yulon.AppImage"
    target.write_bytes(b"current")
    install = Install(InstallKind.APPIMAGE, target, "", True)
    sibling(target, ".old").write_bytes(b"previous")

    assert finish_previous_update(install) is True
    assert not sibling(target, ".old").exists()
    assert target.read_bytes() == b"current"


def test_a_start_with_nothing_to_clean_up_says_so(tmp_path: Path) -> None:
    install = _folder_install(tmp_path)
    assert finish_previous_update(install) is False


@pytest.mark.parametrize(
    "install",
    [
        Install(InstallKind.SOURCE, None, "", False),
        Install(InstallKind.UNSUPPORTED, None, "", False),
    ],
)
def test_an_install_with_no_target_has_nothing_to_clean_up(install: Install) -> None:
    assert finish_previous_update(install) is False


def test_an_old_tree_that_cannot_be_removed_answers_false_without_raising(tmp_path: Path) -> None:
    """It runs at startup. A traceback here would be a launcher that will not open."""
    install = _folder_install(tmp_path)
    assert install.target is not None
    old = sibling(install.target, ".old")
    old.mkdir()
    (old / "f").write_text("x", encoding="utf-8")
    tmp_path.chmod(0o500)
    try:
        assert finish_previous_update(install) is False
        assert old.exists(), "the precondition: this one really could not be removed"
    finally:
        tmp_path.chmod(0o700)
