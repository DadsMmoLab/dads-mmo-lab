"""The helper that replaces the install's entries after this process has quit (T90 plan 3).

Two halves. The first is about the PLAN — the argv list, and the fact that no
path appears in the script's text — and it is all offline. The second runs the
POSIX helpers **for real**, on fake trees in `tmp_path`, with a `sleep` child
standing in for the app: it is the one place in this package where a real
subprocess is wanted, because "a shell script waits for a pid and moves files"
is not something reasoning can settle.

**The first test below is the cold review's own scenario**, and it is the
reason this file exists in this shape: a Windows zip unpacked into a Downloads
folder, with the player's documents beside the executable.

Nothing here is left running: every process started is waited for, and every
wait is against a deadline rather than a blind sleep.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from yulon.selfupdate import layout
from yulon.selfupdate.cleanup import finish_previous_update
from yulon.selfupdate.detect import Install, InstallKind
from yulon.selfupdate.fetch import UpdateError
from yulon.selfupdate.swap import (
    DEFAULT_TICKS,
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

posix_only = pytest.mark.skipif(os.name != "posix", reason="the sh helpers are POSIX only")


def _until(condition: Callable[[], bool], what: str) -> None:
    """Poll `condition` to a deadline. Never a blind sleep, never unbounded."""
    deadline = time.monotonic() + HELPER_DEADLINE
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError(f"{what} did not happen within {HELPER_DEADLINE}s")


def _hash_tree(root: Path, *, ignoring: set[str]) -> str:
    """One digest over every path and byte under `root`, minus the names given."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        top = path.relative_to(root).parts[0]
        if top in ignoring:
            continue
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _a_launcher(path: Path, label: str, witness: Path) -> None:
    """A tiny `sh` script that records that it was launched, and from which tree."""
    path.write_text(f'#!/bin/sh\nprintf %s {label} >> "{witness}"\n', encoding="utf-8")
    path.chmod(0o755)


def _mark(install: Install, name: str, *, entries: tuple[str, ...] = (), to: str = "v2") -> None:
    layout.write_marker(
        install,
        name,
        layout.Marker(
            role=name,
            from_version="v1",
            to_version=to,
            pid=os.getpid(),
            stamp=layout.now(),
            entries=entries,
        ),
    )


def _staged_folder(install: Install, *, label: str, witness: Path) -> Path:
    """A marked `.yulon-new` holding a build made of `_internal/` and the executable."""
    staged = layout.work_dir(install, layout.NEW_NAME)
    (staged / "_internal").mkdir(parents=True, exist_ok=True)
    (staged / "_internal" / "lib").write_text(f"{label} lib", encoding="utf-8")
    _a_launcher(staged / install.executable, label, witness)
    _mark(install, layout.NEW_NAME)
    return staged


def _run_helper(plan: SwapPlan, app: subprocess.Popen[bytes]) -> int:
    """Start the helper, reap the stand-in app, and join everything before returning.

    The app is reaped DELIBERATELY and early: a child that has exited but not
    been waited for is a zombie, and `kill -0 <zombie>` succeeds — so a test
    that left it would have the helper waiting out its whole bound.
    """
    helper = subprocess.Popen(plan.argv, stdin=subprocess.DEVNULL)
    try:
        app.wait(timeout=HELPER_DEADLINE)
        code = helper.wait(timeout=HELPER_DEADLINE + DEFAULT_TICKS * 0.0)
    finally:
        for child in (app, helper):
            if child.poll() is None:  # pragma: no cover - only on a failing run
                child.kill()
                child.wait()
    assert app.poll() is not None and helper.poll() is not None
    return code


# -- the cold review's own scenario ------------------------------------------


@posix_only
def test_an_install_unpacked_into_a_downloads_folder_never_touches_the_players_files(
    tmp_path: Path,
) -> None:
    """**The measured data loss, and the test that would have caught it.**

    The Windows zip has no top-level directory, so "Extract here" in Downloads
    makes the install folder `Downloads`. The first design renamed that folder
    and the next start deleted the backup: a `thesis.docx` and a `photos/`
    folder beside the executable were gone (measured with this same helper).

    Run end to end with the REAL helper and the REAL `finish_previous_update`.
    What is asserted is the whole tree minus the two names the build ships:
    byte for byte identical afterwards.
    """
    downloads = tmp_path / "Downloads"
    (downloads / "_internal").mkdir(parents=True)
    (downloads / "_internal" / "lib").write_text("old lib", encoding="utf-8")
    (downloads / "photos").mkdir()
    (downloads / "photos" / "holiday.jpg").write_bytes(b"jpeg bytes")
    (downloads / "thesis.docx").write_bytes(b"years of work")
    witness = tmp_path / "launched"
    install = Install(InstallKind.WINDOWS_ZIP, downloads, "yulon", True)
    _a_launcher(downloads / "yulon", "old", witness)

    shipped = {"yulon", "_internal", layout.NEW_NAME, layout.OLD_NAME}
    before = _hash_tree(downloads, ignoring=shipped)

    _staged_folder(install, label="new", witness=witness)
    app = subprocess.Popen(["sleep", "0.3"])
    plan = plan_swap(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=app.pid,
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    _run_helper(plan, app)
    _until(lambda: witness.exists(), "the new build was relaunched")

    assert (downloads / "thesis.docx").read_bytes() == b"years of work"
    assert (downloads / "photos" / "holiday.jpg").read_bytes() == b"jpeg bytes"
    assert (downloads / "_internal" / "lib").read_text(encoding="utf-8") == "new lib"
    assert witness.read_text(encoding="utf-8") == "new", "the OLD build was relaunched"
    assert _hash_tree(downloads, ignoring=shipped) == before, "a file of the player's changed"

    # And the first start after it, which is where the deletion happened.
    outcome = finish_previous_update(install, running="v2")
    assert outcome.removed is True
    assert (downloads / "thesis.docx").read_bytes() == b"years of work"
    assert (downloads / "photos" / "holiday.jpg").exists()
    assert not (downloads / layout.OLD_NAME).exists()
    assert _hash_tree(downloads, ignoring=shipped) == before


# -- the plan ----------------------------------------------------------------


def _plain(root: Path, kind: InstallKind = InstallKind.TARBALL, exe: str = "yulon") -> Install:
    target = root / "app"
    target.mkdir(exist_ok=True)
    return Install(kind, target, exe, True)


def test_a_folder_plan_is_sh_the_script_the_bound_the_pid_and_the_entries(tmp_path: Path) -> None:
    install = _plain(tmp_path)
    assert install.target is not None
    _mark(install, layout.NEW_NAME)
    plan = plan_swap(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=4242,
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    assert plan.argv == [
        "/bin/sh",
        str(plan.script),
        str(DEFAULT_TICKS),
        "4242",
        str(install.target),
        str(install.target / "yulon"),
        "2",
        # the entries, twice: executable first, then executable last
        "yulon",
        "_internal",
        "_internal",
        "yulon",
    ]
    assert plan.entries == ("yulon", "_internal")
    assert stat.S_IMODE(plan.script.stat().st_mode) == 0o700


def test_planning_makes_the_marked_backup_folder_the_helper_insists_on(tmp_path: Path) -> None:
    """The helper refuses unless BOTH work dirs are marked; one it made itself proves nothing."""
    install = _plain(tmp_path)
    _mark(install, layout.NEW_NAME)
    plan_swap(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=1,
        script_dir=tmp_path,
        entries=("yulon",),
        platform_id="linux",
    )
    marker = layout.read_marker(install, layout.OLD_NAME)
    assert marker is not None
    assert marker.state == layout.SWAPPING
    assert marker.entries == ("yulon",)
    assert marker.to_version == "v2", "the backup has to say which version it is the way back from"


def test_an_unmarked_backup_folder_stops_the_update_rather_than_being_deleted(
    tmp_path: Path,
) -> None:
    """A `.yulon-old` the player made is somebody's folder. Decided: refuse, never move it."""
    install = _plain(tmp_path)
    assert install.target is not None
    theirs = install.target / layout.OLD_NAME
    theirs.mkdir()
    (theirs / "notes.txt").write_text("mine", encoding="utf-8")
    _mark(install, layout.NEW_NAME)

    with pytest.raises(UpdateError, match="did not put it there"):
        plan_swap(
            install,
            layout.work_dir(install, layout.NEW_NAME),
            pid=1,
            script_dir=tmp_path,
            entries=("yulon",),
            platform_id="linux",
        )
    assert (theirs / "notes.txt").read_text(encoding="utf-8") == "mine"


def test_a_staging_folder_with_no_marker_is_never_swapped_from(tmp_path: Path) -> None:
    install = _plain(tmp_path)
    assert install.target is not None
    (install.target / layout.NEW_NAME).mkdir()
    with pytest.raises(UpdateError, match="not marked"):
        plan_swap(
            install,
            layout.work_dir(install, layout.NEW_NAME),
            pid=1,
            script_dir=tmp_path,
            entries=("yulon",),
            platform_id="linux",
        )


@pytest.mark.parametrize("entries", [(), ("",), ("../x",), ("a/b",), ("..",)])
def test_a_plan_refuses_an_entry_that_is_not_a_single_name(
    entries: tuple[str, ...], tmp_path: Path
) -> None:
    install = _plain(tmp_path)
    _mark(install, layout.NEW_NAME)
    with pytest.raises(UpdateError, match="which files to replace"):
        plan_swap(
            install,
            layout.work_dir(install, layout.NEW_NAME),
            pid=1,
            script_dir=tmp_path,
            entries=entries,
            platform_id="linux",
        )


def test_an_appimage_plan_names_the_file_and_nothing_else(tmp_path: Path) -> None:
    target = tmp_path / "Yulon-v0.8.66-Public-x86_64.AppImage"
    target.write_bytes(b"old")
    install = Install(InstallKind.APPIMAGE, target, "", True)
    _mark(install, layout.NEW_NAME)
    plan = plan_swap(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=7,
        script_dir=tmp_path,
        platform_id="linux",
    )
    assert plan.argv == ["/bin/sh", str(plan.script), str(DEFAULT_TICKS), "7", str(target)]
    assert plan.entries == (layout.APPIMAGE_ENTRY,)


def test_a_windows_plan_runs_powershell_by_its_absolute_path_with_the_fixed_flags(
    tmp_path: Path,
) -> None:
    install = _plain(tmp_path, InstallKind.WINDOWS_ZIP, "yulon.exe")
    assert install.target is not None
    _mark(install, layout.NEW_NAME)
    plan = plan_swap(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=99,
        script_dir=tmp_path,
        entries=("yulon.exe", "_internal"),
        platform_id="windows",
    )
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
        str(DEFAULT_TICKS),
        "99",
        str(install.target),
        str(install.target / "yulon.exe"),
        "2",
        "yulon.exe",
        "_internal",
        "_internal",
        "yulon.exe",
    ]


def test_powershell_is_found_under_systemroot_and_never_on_the_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare `powershell.exe` is whatever the PATH resolves it to, which is not a decision."""
    monkeypatch.delenv("SystemRoot", raising=False)
    assert powershell_path() == "C:\\Windows" + POWERSHELL_UNDER_SYSTEMROOT
    monkeypatch.setenv("SystemRoot", "D:\\Win")
    assert powershell_path() == "D:\\Win" + POWERSHELL_UNDER_SYSTEMROOT


@pytest.mark.parametrize("platform_id", ["linux", "windows"])
def test_a_hostile_folder_name_reaches_argv_unchanged_and_the_script_text_not_at_all(
    platform_id: str, tmp_path: Path
) -> None:
    """The rule the whole helper design exists for: paths are ARGUMENTS, never text."""
    awkward = tmp_path / "a folder 'with' \"quotes\" $(x) & 100%"
    awkward.mkdir()
    kind = InstallKind.WINDOWS_ZIP if platform_id == "windows" else InstallKind.TARBALL
    install = _plain(awkward, kind)
    assert install.target is not None
    _mark(install, layout.NEW_NAME)

    plan = plan_swap(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=5,
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id=platform_id,
    )

    assert str(install.target) in plan.argv, "the target did not reach argv as one whole string"
    text = plan.script.read_text(encoding="utf-8")
    for fragment in (str(install.target), str(awkward), "$(x)", "100%"):
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
        plan_swap(install, tmp_path / "staged", pid=1, script_dir=tmp_path, entries=("yulon",))


def test_start_helper_hands_the_argv_list_to_the_spawner_unchanged(tmp_path: Path) -> None:
    install = _plain(tmp_path)
    _mark(install, layout.NEW_NAME)
    plan = plan_swap(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=3,
        script_dir=tmp_path,
        entries=("yulon",),
        platform_id="linux",
    )
    seen: list[list[str]] = []
    start_helper(plan, spawn=seen.append)
    assert seen == [plan.argv]


def test_the_detached_spawn_asks_for_a_session_of_its_own_and_its_own_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """It must outlive this process — and must NOT hold the install folder open.

    A process whose working directory is a folder holds that folder on Windows,
    and this helper's whole job is to move things inside one. So its `cwd` is
    the script's own directory, never the target.
    """
    from yulon.selfupdate import swap

    seen: dict[str, object] = {}

    def fake_popen(argv: list[str], **kwargs: object) -> object:
        seen["argv"] = argv
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(swap.subprocess, "Popen", fake_popen)
    script = tmp_path / "scripts" / "x.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    swap._spawn_detached(["/bin/sh", str(script), "600", "1", "/opt/app"])

    assert seen["stdin"] == subprocess.DEVNULL
    assert seen["close_fds"] is True
    assert seen["cwd"] == str(script.parent), "the helper was started inside the install folder"
    if os.name == "posix":
        assert seen["start_new_session"] is True
        assert seen["creationflags"] == 0


# -- the POSIX folder helper, run for real ------------------------------------


@posix_only
def test_the_helper_swaps_the_entries_relaunches_and_deletes_itself(tmp_path: Path) -> None:
    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "_internal").mkdir()
    (target / "_internal" / "lib").write_text("old lib", encoding="utf-8")
    _a_launcher(target / "yulon", "old", witness)
    _staged_folder(install, label="new", witness=witness)

    app = subprocess.Popen(["sleep", "0.3"])
    plan = plan_swap(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=app.pid,
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    _run_helper(plan, app)

    _until(lambda: witness.exists(), "the new build was relaunched")
    assert (target / "_internal" / "lib").read_text(encoding="utf-8") == "new lib"
    assert witness.read_text(encoding="utf-8") == "new"
    old = layout.work_dir(install, layout.OLD_NAME)
    assert (old / "_internal" / "lib").read_text(encoding="utf-8") == "old lib"
    assert (old / "yulon").exists()
    _until(lambda: not plan.script.exists(), "the helper script deleted itself")


@posix_only
def test_a_failure_part_way_puts_every_entry_back_exactly_as_it_was(tmp_path: Path) -> None:
    """The rollback arm: `_internal` cannot be brought in, so `yulon` goes back too.

    The staged `_internal` is removed between planning and running — which is
    what a disk that filled up, or a second copy of Yu'lon, looks like from the
    helper's side. Any precondition failure must move NOTHING in the end.
    """
    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "_internal").mkdir()
    (target / "_internal" / "lib").write_text("old lib", encoding="utf-8")
    _a_launcher(target / "yulon", "old", witness)
    staged = _staged_folder(install, label="new", witness=witness)
    before = _hash_tree(target, ignoring={layout.NEW_NAME, layout.OLD_NAME})

    import shutil

    shutil.rmtree(staged / "_internal")
    assert not (staged / "_internal").exists(), "the precondition: one staged entry is missing"

    app = subprocess.Popen(["sleep", "0.3"])
    plan = plan_swap(
        install,
        staged,
        pid=app.pid,
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    _run_helper(plan, app)

    _until(lambda: witness.exists(), "the old build was relaunched")
    assert _hash_tree(target, ignoring={layout.NEW_NAME, layout.OLD_NAME}) == before
    assert witness.read_text(encoding="utf-8") == "old"
    assert (target / "_internal" / "lib").read_text(encoding="utf-8") == "old lib"


@posix_only
def test_the_helper_gives_up_on_a_live_app_and_moves_and_starts_nothing(tmp_path: Path) -> None:
    """**The bound is an ARGUMENT so this test can exist** (cold review 1).

    It was a constant in the script text, and mutating the give-up branch to
    swap anyway left every swap test green. Here the app stays alive past a
    two-tick bound, and what is asserted is that nothing moved, nothing was
    launched — a second copy beside a running one is the wrong answer — and the
    staged build is still there, marked, for the next start.
    """
    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "_internal").mkdir()
    (target / "_internal" / "lib").write_text("old lib", encoding="utf-8")
    _a_launcher(target / "yulon", "old", witness)
    _staged_folder(install, label="new", witness=witness)
    before = _hash_tree(target, ignoring={layout.OLD_NAME})

    alive = subprocess.Popen(["sleep", "30"])
    try:
        plan = plan_swap(
            install,
            layout.work_dir(install, layout.NEW_NAME),
            pid=alive.pid,
            script_dir=tmp_path,
            entries=("yulon", "_internal"),
            platform_id="linux",
        )
        # Two ticks, not the default six hundred: the bound is argv[2].
        argv = list(plan.argv)
        argv[2] = "2"
        helper = subprocess.Popen(argv, stdin=subprocess.DEVNULL)
        try:
            code = helper.wait(timeout=HELPER_DEADLINE)
        finally:
            # A helper that did NOT give up would otherwise be left counting
            # down for six minutes after this test has finished.
            if helper.poll() is None:  # pragma: no cover - only on a failing run
                helper.kill()
                helper.wait(timeout=HELPER_DEADLINE)
    finally:
        alive.kill()
        alive.wait(timeout=HELPER_DEADLINE)
    assert alive.poll() is not None

    assert code == 75, "the give-up path has its own exit code"
    assert not witness.exists(), "a second copy was launched beside the running one"
    assert _hash_tree(target, ignoring={layout.OLD_NAME}) == before, "something moved"
    assert (target / "_internal" / "lib").read_text(encoding="utf-8") == "old lib"
    assert (
        layout.read_marker(install, layout.NEW_NAME) is not None
    ), "the staged build lost its mark"
    assert not plan.script.exists(), "the helper left itself behind"


@posix_only
def test_the_helper_refuses_when_the_backup_folder_is_not_marked(tmp_path: Path) -> None:
    """The marker check is the helper's own, not just the planner's."""
    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "_internal").mkdir()
    _a_launcher(target / "yulon", "old", witness)
    _staged_folder(install, label="new", witness=witness)

    dead = subprocess.Popen(["sleep", "0"])
    dead.wait(timeout=HELPER_DEADLINE)
    plan = plan_swap(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=dead.pid,
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    layout.marker_path(install, layout.OLD_NAME).unlink()
    before = _hash_tree(target, ignoring={layout.OLD_NAME})

    helper = subprocess.Popen(plan.argv, stdin=subprocess.DEVNULL)
    assert helper.wait(timeout=HELPER_DEADLINE) == 64
    assert _hash_tree(target, ignoring={layout.OLD_NAME}) == before
    assert not witness.exists()


@posix_only
def test_the_appimage_helper_swaps_one_file_and_keeps_its_name(tmp_path: Path) -> None:
    witness = tmp_path / "launched"
    target = tmp_path / "Yulon-v0.8.66-Public-x86_64.AppImage"
    _a_launcher(target, "old", witness)
    install = Install(InstallKind.APPIMAGE, target, "", True)
    staged = layout.work_dir(install, layout.NEW_NAME)
    staged.mkdir()
    _a_launcher(staged / layout.APPIMAGE_ENTRY, "new", witness)
    _mark(install, layout.NEW_NAME)

    app = subprocess.Popen(["sleep", "0.3"])
    plan = plan_swap(install, staged, pid=app.pid, script_dir=tmp_path, platform_id="linux")
    _run_helper(plan, app)

    _until(lambda: witness.exists(), "the new AppImage was relaunched")
    assert witness.read_text(encoding="utf-8") == "new"
    assert "new" in target.read_text(encoding="utf-8")
    old = layout.work_dir(install, layout.OLD_NAME)
    assert "old" in (old / layout.APPIMAGE_ENTRY).read_text(encoding="utf-8")
    assert target.name == "Yulon-v0.8.66-Public-x86_64.AppImage", (
        "the file keeps its old name on purpose, so a desktop entry or a shortcut "
        "pointing at it keeps working; the window title is what says the version"
    )


@posix_only
def test_the_appimage_helper_refuses_a_staging_folder_with_no_marker(tmp_path: Path) -> None:
    witness = tmp_path / "launched"
    target = tmp_path / "Yulon.AppImage"
    _a_launcher(target, "old", witness)
    install = Install(InstallKind.APPIMAGE, target, "", True)
    staged = layout.work_dir(install, layout.NEW_NAME)
    staged.mkdir()
    _a_launcher(staged / layout.APPIMAGE_ENTRY, "new", witness)
    _mark(install, layout.NEW_NAME)
    dead = subprocess.Popen(["sleep", "0"])
    dead.wait(timeout=HELPER_DEADLINE)
    plan = plan_swap(install, staged, pid=dead.pid, script_dir=tmp_path, platform_id="linux")
    layout.marker_path(install, layout.NEW_NAME).unlink()

    helper = subprocess.Popen(plan.argv, stdin=subprocess.DEVNULL)
    assert helper.wait(timeout=HELPER_DEADLINE) == 64
    assert "old" in target.read_text(encoding="utf-8")
    assert not witness.exists()


# -- what the helper refuses before it moves anything (cold review 2, S3) ----


def _ready_to_swap(tmp_path: Path) -> tuple[Install, Path, Path]:
    """An install, a marked staged build, and a witness — all ready for the helper."""
    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "_internal").mkdir()
    (target / "_internal" / "lib").write_text("old lib", encoding="utf-8")
    _a_launcher(target / "yulon", "old", witness)
    _staged_folder(install, label="new", witness=witness)
    return install, target, witness


def _a_dead_pid() -> int:
    dead = subprocess.Popen(["sleep", "0"])
    dead.wait(timeout=HELPER_DEADLINE)
    return dead.pid


@posix_only
@pytest.mark.parametrize(
    ("label", "mangle"),
    [
        ("ticks-empty", lambda a: a[:2] + [""] + a[3:]),
        ("ticks-word", lambda a: a[:2] + ["soon"] + a[3:]),
        ("ticks-zero", lambda a: a[:2] + ["0"] + a[3:]),
        ("ticks-negative", lambda a: a[:2] + ["-1"] + a[3:]),
        ("pid-empty", lambda a: a[:3] + [""] + a[4:]),
        ("pid-word", lambda a: a[:3] + ["abc"] + a[4:]),
        ("pid-one", lambda a: a[:3] + ["1"] + a[4:]),
        ("pid-zero", lambda a: a[:3] + ["0"] + a[4:]),
        ("half-word", lambda a: a[:6] + ["two"] + a[7:]),
        ("half-zero", lambda a: a[:6] + ["0"] + a[7:]),
        ("half-disagrees", lambda a: a[:6] + ["3"] + a[7:]),
        ("relative-target", lambda a: a[:4] + ["app"] + a[5:]),
        ("entry-empty", lambda a: a[:7] + [""] + a[8:]),
        ("entry-dotdot", lambda a: a[:7] + [".."] + a[8:]),
        ("entry-nested", lambda a: a[:7] + ["a/b"] + a[8:]),
        ("entry-backslash", lambda a: a[:7] + ["a\\b"] + a[8:]),
        ("entry-space", lambda a: a[:7] + ["my file"] + a[8:]),
        ("entry-glob", lambda a: a[:7] + ["*"] + a[8:]),
        ("entry-dash", lambda a: a[:7] + ["-rf"] + a[8:]),
        ("entry-marker", lambda a: a[:7] + [".yulon-marker"] + a[8:]),
        ("entry-backup", lambda a: a[:7] + [".yulon-old"] + a[8:]),
        ("no-entries", lambda a: a[:7]),
    ],
)
def test_the_real_helper_refuses_a_bad_argument_and_moves_nothing(
    label: str, mangle: Callable[[list[str]], list[str]], tmp_path: Path
) -> None:
    """**A pid of `abc` used to swap IMMEDIATELY**, without waiting for anything.

    `kill -0 abc` fails, so the wait loop was never entered at all — the helper
    went straight to moving files under a still-running app. Non-numeric ticks
    made `[ -gt ]` an error and the give-up bound never fired. And the reserved
    names were accepted as entries, so a swap could be asked to move
    `.yulon-old` (cold review 2, S3).

    Every case runs the REAL script and asserts exit 64 and an untouched tree.
    """
    install, target, witness = _ready_to_swap(tmp_path)
    before = _hash_tree(target, ignoring={layout.OLD_NAME})
    plan = plan_swap(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=_a_dead_pid(),
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    argv = mangle(list(plan.argv))

    helper = subprocess.Popen(argv, cwd=str(tmp_path), stdin=subprocess.DEVNULL)
    try:
        code = helper.wait(timeout=HELPER_DEADLINE)
    finally:
        if helper.poll() is None:  # pragma: no cover - only on a failing run
            helper.kill()
            helper.wait(timeout=HELPER_DEADLINE)

    assert code == 64, f"{label}: the helper did not refuse its arguments"
    assert not witness.exists(), f"{label}: something was launched"
    assert _hash_tree(target, ignoring={layout.OLD_NAME}) == before, f"{label}: something moved"
    assert not plan.script.exists(), f"{label}: the refused helper left itself in the temp dir"


@posix_only
def test_a_live_pid_that_is_not_the_app_is_still_waited_for(tmp_path: Path) -> None:
    """The precondition the pid validation protects: the wait loop really runs."""
    install, target, witness = _ready_to_swap(tmp_path)
    alive = subprocess.Popen(["sleep", "30"])
    try:
        plan = plan_swap(
            install,
            layout.work_dir(install, layout.NEW_NAME),
            pid=alive.pid,
            script_dir=tmp_path,
            entries=("yulon", "_internal"),
            platform_id="linux",
        )
        argv = list(plan.argv)
        argv[2] = "2"
        helper = subprocess.Popen(argv, stdin=subprocess.DEVNULL)
        try:
            assert helper.wait(timeout=HELPER_DEADLINE) == 75
        finally:
            if helper.poll() is None:  # pragma: no cover - only on a failing run
                helper.kill()
                helper.wait(timeout=HELPER_DEADLINE)
    finally:
        alive.kill()
        alive.wait(timeout=HELPER_DEADLINE)
    assert alive.poll() is not None
    assert not witness.exists()


# -- the entries go in both orders (S2) --------------------------------------


def test_the_entries_are_handed_over_twice_in_the_two_orders_the_swap_needs(
    tmp_path: Path,
) -> None:
    """Reversing a list inside POSIX `sh` means rebuilding it into a string.

    A string is word-split, which is how an entry called `my file` would have
    failed the swap and left itself in `.yulon-old`. The reversing is done
    here, in Python, where a list is a list.
    """
    install = _plain(tmp_path)
    assert install.target is not None
    _mark(install, layout.NEW_NAME)
    plan = plan_swap(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=4242,
        script_dir=tmp_path,
        entries=("yulon", "_internal", "extra.dat"),
        platform_id="linux",
    )
    half = int(plan.argv[6])
    assert half == 3
    forward = plan.argv[7 : 7 + half]
    backward = plan.argv[7 + half :]
    assert forward == ["yulon", "_internal", "extra.dat"]
    assert backward == list(reversed(forward))
    assert forward[0] == "yulon", "the executable leaves first"
    assert backward[-1] == "yulon", "the executable arrives last"


def test_the_windows_argv_puts_the_script_where_the_spawner_looks_for_it(
    tmp_path: Path,
) -> None:
    """`argv[-1]` is an ENTRY now that the entries are trailing arguments.

    `_spawn_detached` used it as the script path to derive its working
    directory from, so on Windows it was setting the helper's cwd from a file
    name (cold review 2).
    """
    from yulon.selfupdate.swap import script_in

    install = _plain(tmp_path, InstallKind.WINDOWS_ZIP, "yulon.exe")
    _mark(install, layout.NEW_NAME)
    plan = plan_swap(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=99,
        script_dir=tmp_path,
        entries=("yulon.exe", "_internal"),
        platform_id="windows",
    )
    assert plan.argv[-1] != str(plan.script), "the precondition: the script is not last"
    assert script_in(plan.argv) == plan.script

    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    other = _plain(elsewhere)
    _mark(other, layout.NEW_NAME)
    posix = plan_swap(
        other,
        layout.work_dir(other, layout.NEW_NAME),
        pid=2,
        script_dir=tmp_path,
        entries=("yulon",),
        platform_id="linux",
    )
    assert script_in(posix.argv) == posix.script


def test_the_posix_helper_never_iterates_an_unquoted_variable() -> None:
    """The loops read `"$@"`; nothing is rebuilt into a string and word-split.

    **This is a TEXT pin and it says so**, because the behaviour it guards is
    unreachable while the name rules hold: `layout.is_entry_name` refuses
    whitespace and globs on the Python side, and the helper's own `case`
    refuses them again, so no entry that WOULD word-split can reach the loops.
    Restoring the old `moved="$e $moved"` / `for e in $moved` shape therefore
    changes no behaviour any test can observe today — measured: that mutation
    left all 56 swap and live tests green.

    What it does change is the shape, and the shape is the reason the rules
    above are belt and braces rather than the only defence. So the shape is
    what is asserted: every `for` loop in both helpers iterates `"$@"`.
    """
    from yulon.selfupdate.swap import POSIX_FILE_HELPER, POSIX_FOLDER_HELPER

    for script in (POSIX_FOLDER_HELPER, POSIX_FILE_HELPER):
        loops = [line.strip() for line in script.splitlines() if line.strip().startswith("for ")]
        for loop in loops:
            assert loop == 'for e in "$@"; do', f"a loop iterates something else: {loop}"
        assert (
            "$moved" not in script and "$placed" not in script
        ), "the helper is rebuilding a list into a string again"


def test_the_helper_refuses_a_name_that_would_word_split_rather_than_quoting_it(
    tmp_path: Path,
) -> None:
    """The other half: the rule that makes the loops' shape belt and braces.

    Driven through the REAL script with `my file` as an entry, so what is
    pinned is the shell's own `case` and not only the Python rule.
    """
    install, target, witness = _ready_to_swap(tmp_path)
    plan = plan_swap(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=_a_dead_pid(),
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    argv = list(plan.argv)
    argv[7] = "my file"
    helper = subprocess.Popen(argv, stdin=subprocess.DEVNULL)
    try:
        assert helper.wait(timeout=HELPER_DEADLINE) == 64
    finally:
        if helper.poll() is None:  # pragma: no cover - only on a failing run
            helper.kill()
            helper.wait(timeout=HELPER_DEADLINE)
    assert not witness.exists()
