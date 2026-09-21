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
import re
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
    arm,
    plan_swap,
    powershell_path,
    start_helper,
)


def _a_ready_plan(*args: object, **kw: object) -> SwapPlan:
    """`plan_swap` + `arm`: a plan with a script on disk, ready to start.

    Since round 5 `plan_swap` writes nothing — the script is written once, at
    the moment of use, by `arm()`, because arming in both places left an
    orphaned helper script in the temp directory after every single update.
    These tests want the armed thing, so they say so here in one place.
    """
    return arm(plan_swap(*args, **kw))  # type: ignore[arg-type]


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
    plan = _a_ready_plan(
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
    plan = _a_ready_plan(
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
        plan.nonce,
        str(install.target),
        str(install.target / "yulon"),
        "2",
        # the entries, twice: executable first, then executable last
        "yulon",
        "_internal",
        "_internal",
        "yulon",
    ]
    assert re.fullmatch(r"[0-9a-f]{16}", plan.nonce), "the nonce is not hex"
    assert plan.nonce in plan.script.name, "two attempts would share one script file"
    assert plan.entries == ("yulon", "_internal")
    assert stat.S_IMODE(plan.script.stat().st_mode) == 0o700


def test_planning_makes_the_marked_backup_folder_the_helper_insists_on(tmp_path: Path) -> None:
    """The helper refuses unless BOTH work dirs are marked; one it made itself proves nothing."""
    install = _plain(tmp_path)
    _mark(install, layout.NEW_NAME)
    _a_ready_plan(
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

    with pytest.raises(UpdateError, match="cannot vouch for"):
        _a_ready_plan(
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
        _a_ready_plan(
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
        _a_ready_plan(
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
    plan = _a_ready_plan(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=7,
        script_dir=tmp_path,
        platform_id="linux",
    )
    assert plan.argv == [
        "/bin/sh",
        str(plan.script),
        str(DEFAULT_TICKS),
        "7",
        plan.nonce,
        str(target),
    ]
    assert plan.entries == (layout.APPIMAGE_ENTRY,)


def test_a_windows_plan_runs_powershell_by_its_absolute_path_with_the_fixed_flags(
    tmp_path: Path,
) -> None:
    install = _plain(tmp_path, InstallKind.WINDOWS_ZIP, "yulon.exe")
    assert install.target is not None
    _mark(install, layout.NEW_NAME)
    plan = _a_ready_plan(
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
        plan.nonce,
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

    plan = _a_ready_plan(
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
        _a_ready_plan(install, tmp_path / "staged", pid=1, script_dir=tmp_path, entries=("yulon",))


def test_start_helper_hands_the_argv_list_to_the_spawner_unchanged(tmp_path: Path) -> None:
    install = _plain(tmp_path)
    _mark(install, layout.NEW_NAME)
    plan = _a_ready_plan(
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
    plan = _a_ready_plan(
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
def test_a_staged_entry_that_vanished_stops_the_helper_before_it_moves_anything(
    tmp_path: Path,
) -> None:
    """The re-validation immediately before the first move (round 4).

    The staged `_internal` is removed between planning and running — what a
    disk that filled up, a second copy of Yu'lon, or the player looks like from
    the helper's side. The helper now refuses rather than swapping and rolling
    back: nothing moves at all.
    """
    import shutil

    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "_internal").mkdir()
    (target / "_internal" / "lib").write_text("old lib", encoding="utf-8")
    _a_launcher(target / "yulon", "old", witness)
    staged = _staged_folder(install, label="new", witness=witness)
    before = _hash_tree(target, ignoring={layout.NEW_NAME, layout.OLD_NAME})

    shutil.rmtree(staged / "_internal")
    assert not (staged / "_internal").exists(), "the precondition: one staged entry is missing"

    app = subprocess.Popen(["sleep", "0.3"])
    plan = _a_ready_plan(
        install,
        staged,
        pid=app.pid,
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    assert _run_helper(plan, app) == 74, "the helper did not refuse"

    assert _hash_tree(target, ignoring={layout.NEW_NAME, layout.OLD_NAME}) == before
    assert not witness.exists(), "something was launched"


@posix_only
def test_a_move_that_fails_part_way_is_undone_exactly(tmp_path: Path) -> None:
    """The rollback arm, reached the only way it now can be: a `mv` that fails.

    The third move is the first bring-in, so both entries are out and one is
    coming back when it fails. What must come out of that is the OLD build,
    whole, and a relaunch of it.
    """
    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "_internal").mkdir()
    (target / "_internal" / "lib").write_text("old lib", encoding="utf-8")
    _a_launcher(target / "yulon", "old", witness)
    _staged_folder(install, label="new", witness=witness)
    before = _hash_tree(target, ignoring={layout.NEW_NAME, layout.OLD_NAME})

    failing = tmp_path / "failbin"
    failing.mkdir()
    (failing / "mv").write_text(
        "#!/bin/sh\n"
        'n=$(cat "$MVCOUNT" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$MVCOUNT"\n'
        'if [ "$n" -eq "$FAILAT" ]; then exit 1; fi\n'
        '/bin/mv "$@"\n',
        encoding="utf-8",
    )
    (failing / "mv").chmod(0o755)

    app = subprocess.Popen(["sleep", "0.3"])
    plan = _a_ready_plan(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=app.pid,
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    env = {
        **os.environ,
        "PATH": f"{failing}{os.pathsep}{os.environ['PATH']}",
        "MVCOUNT": str(tmp_path / "count"),
        "FAILAT": "3",
    }
    helper = subprocess.Popen(plan.argv, stdin=subprocess.DEVNULL, env=env)
    try:
        app.wait(timeout=HELPER_DEADLINE)
        helper.wait(timeout=HELPER_DEADLINE)
    finally:
        for child in (app, helper):
            if child.poll() is None:  # pragma: no cover - only on a failing run
                child.kill()
                child.wait()

    _until(lambda: witness.exists(), "the old build was relaunched")
    assert _hash_tree(target, ignoring={layout.NEW_NAME, layout.OLD_NAME}) == before
    assert witness.read_text(encoding="utf-8") == "old"
    log = (layout.work_dir(install, layout.OLD_NAME) / layout.HELPER_LOG).read_text("utf-8")
    assert "rolling back" in log


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
        plan = _a_ready_plan(
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
    plan = _a_ready_plan(
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
    plan = _a_ready_plan(install, staged, pid=app.pid, script_dir=tmp_path, platform_id="linux")
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
    plan = _a_ready_plan(install, staged, pid=dead.pid, script_dir=tmp_path, platform_id="linux")
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
        ("nonce-empty", lambda a: a[:4] + [""] + a[5:]),
        ("nonce-not-hex", lambda a: a[:4] + ["zz"] + a[5:]),
        ("pid-word", lambda a: a[:3] + ["abc"] + a[4:]),
        ("pid-one", lambda a: a[:3] + ["1"] + a[4:]),
        ("pid-zero", lambda a: a[:3] + ["0"] + a[4:]),
        ("half-word", lambda a: a[:7] + ["two"] + a[8:]),
        ("half-zero", lambda a: a[:7] + ["0"] + a[8:]),
        ("half-disagrees", lambda a: a[:7] + ["3"] + a[8:]),
        ("relative-target", lambda a: a[:5] + ["app"] + a[6:]),
        ("entry-empty", lambda a: a[:8] + [""] + a[9:]),
        ("entry-dotdot", lambda a: a[:8] + [".."] + a[9:]),
        ("entry-nested", lambda a: a[:8] + ["a/b"] + a[9:]),
        ("entry-backslash", lambda a: a[:8] + ["a\\b"] + a[9:]),
        ("entry-space", lambda a: a[:8] + ["my file"] + a[9:]),
        ("entry-glob", lambda a: a[:8] + ["*"] + a[9:]),
        ("entry-dash", lambda a: a[:8] + ["-rf"] + a[9:]),
        ("entry-marker", lambda a: a[:8] + [".yulon-marker"] + a[9:]),
        ("entry-backup", lambda a: a[:8] + [".yulon-old"] + a[9:]),
        ("no-entries", lambda a: a[:8]),
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
    plan = _a_ready_plan(
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
        plan = _a_ready_plan(
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
    plan = _a_ready_plan(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=4242,
        script_dir=tmp_path,
        entries=("yulon", "_internal", "extra.dat"),
        platform_id="linux",
    )
    half = int(plan.argv[7])
    assert half == 3
    forward = plan.argv[8 : 8 + half]
    backward = plan.argv[8 + half :]
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
    plan = _a_ready_plan(
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
    posix = _a_ready_plan(
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

    folder_loops = [
        line.strip() for line in POSIX_FOLDER_HELPER.splitlines() if line.strip().startswith("for ")
    ]
    assert folder_loops, "the folder helper has no loops; this pin is reading the wrong text"
    for script in (POSIX_FOLDER_HELPER, POSIX_FILE_HELPER):
        loops = [line.strip() for line in script.splitlines() if line.strip().startswith("for ")]
        for loop in loops:
            assert loop in (
                'for e in "$@"; do',
                'for f in "$@"; do',
            ), f"a loop iterates something else: {loop}"
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
    plan = _a_ready_plan(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=_a_dead_pid(),
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    argv = list(plan.argv)
    argv[8] = "my file"
    helper = subprocess.Popen(argv, stdin=subprocess.DEVNULL)
    try:
        assert helper.wait(timeout=HELPER_DEADLINE) == 64
    finally:
        if helper.poll() is None:  # pragma: no cover - only on a failing run
            helper.kill()
            helper.wait(timeout=HELPER_DEADLINE)
    assert not witness.exists()


def test_the_real_helper_says_it_is_running_before_it_waits(tmp_path: Path) -> None:
    """**The Windows gate's fix, proved on the shell that can be run here.**

    The app does not close until this file exists. On Windows 11 the helper was
    spawned and never ran, and the app closed anyway; a file the helper writes
    itself is the only thing that can prove otherwise.

    Written BEFORE the wait loop, so it is there while the app is still open —
    which is the whole point.
    """
    install, target, witness = _ready_to_swap(tmp_path)
    alive = subprocess.Popen(["sleep", "30"])
    try:
        plan = _a_ready_plan(
            install,
            layout.work_dir(install, layout.NEW_NAME),
            pid=alive.pid,
            script_dir=tmp_path,
            entries=("yulon", "_internal"),
            platform_id="linux",
        )
        argv = list(plan.argv)
        argv[2] = "20"
        helper = subprocess.Popen(argv, stdin=subprocess.DEVNULL)
        try:
            stamp = layout.helper_stamp(install)
            _until(stamp.exists, "the helper stamped before waiting for the app")
            assert not witness.exists(), "it swapped before the app had gone"
        finally:
            helper.kill()
            helper.wait(timeout=HELPER_DEADLINE)
    finally:
        alive.kill()
        alive.wait(timeout=HELPER_DEADLINE)
    assert alive.poll() is not None


def test_a_refused_helper_never_says_it_is_running(tmp_path: Path) -> None:
    """The stamp means "I validated my arguments and I am waiting", not "I was started"."""
    install, target, _witness = _ready_to_swap(tmp_path)
    plan = _a_ready_plan(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=_a_dead_pid(),
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    argv = list(plan.argv)
    argv[3] = "abc"  # a pid the helper refuses

    helper = subprocess.Popen(argv, stdin=subprocess.DEVNULL)
    assert helper.wait(timeout=HELPER_DEADLINE) == 64
    assert not layout.helper_stamp(install).exists(), "a refused helper claimed to be running"


def test_the_helper_leaves_a_log_of_what_it_did(tmp_path: Path) -> None:
    """The Windows gate could only guess, because the helper left no trace at all."""
    install, target, witness = _ready_to_swap(tmp_path)
    app = subprocess.Popen(["sleep", "0.3"])
    plan = _a_ready_plan(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=app.pid,
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    _run_helper(plan, app)
    _until(lambda: witness.exists(), "the new build was relaunched")

    log = (layout.work_dir(install, layout.OLD_NAME) / layout.HELPER_LOG).read_text("utf-8")
    assert "started pid=" in log
    assert "moved out yulon" in log and "brought in yulon" in log
    assert "relaunching" in log
    assert layout.helper_log_tail(install).startswith("relaunching")


def test_the_two_halves_must_name_the_same_set(tmp_path: Path) -> None:
    """Otherwise phase 1 moves one set out and phase 2 brings another in (round 3, F8)."""
    install, target, witness = _ready_to_swap(tmp_path)
    before = _hash_tree(target, ignoring={layout.OLD_NAME})
    plan = _a_ready_plan(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=_a_dead_pid(),
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    argv = list(plan.argv)
    assert argv[8:] == ["yulon", "_internal", "_internal", "yulon"], "the precondition"
    argv[10] = "yulon"  # the halves now name {yulon,_internal} and {yulon,yulon}

    helper = subprocess.Popen(argv, stdin=subprocess.DEVNULL)
    assert helper.wait(timeout=HELPER_DEADLINE) == 64
    assert _hash_tree(target, ignoring={layout.OLD_NAME}) == before
    assert not witness.exists()
    assert not plan.script.exists(), "a refused helper left itself in the temp dir"


def test_a_plan_is_armed_with_a_fresh_nonce_and_its_own_script(tmp_path: Path) -> None:
    """Every attempt gets its own nonce and its own file (round 4).

    A stale stamp is then harmless by construction, and two attempts cannot
    overwrite each other's script — which they did, because the name carried
    only the pid.
    """
    from yulon.selfupdate.swap import arm, staging_is_intact

    install, target, _witness = _ready_to_swap(tmp_path)
    plan = _a_ready_plan(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=_a_dead_pid(),
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    again = arm(plan)

    assert again.nonce != plan.nonce, "two attempts share a nonce"
    assert again.script != plan.script, "two attempts share a script file"
    assert again.script.exists() and again.script.read_text(encoding="utf-8") == again.body
    assert again.argv[4] == again.nonce
    assert staging_is_intact(install, again) is None

    (layout.work_dir(install, layout.NEW_NAME) / "_internal").rename(tmp_path / "taken")
    assert staging_is_intact(install, again) == "part of it (_internal) is gone"

    layout.discard_ours(install, layout.NEW_NAME)
    assert staging_is_intact(install, again) == "the files it had prepared are gone"


def test_a_stamp_from_another_attempt_is_not_this_ones(tmp_path: Path) -> None:
    """What made the app close for a helper that had never started (round 4, M2)."""
    install, _target, _witness = _ready_to_swap(tmp_path)
    layout.work_dir(install, layout.OLD_NAME).mkdir(parents=True, exist_ok=True)
    layout.helper_stamp(install).write_text("deadbeefdeadbeef", encoding="utf-8")

    assert layout.stamp_holds(install, "deadbeefdeadbeef") is True
    assert layout.stamp_holds(install, "0123456789abcdef") is False
    assert layout.stamp_holds(install, "") is False


def test_clearing_removes_a_stale_stamp_and_stand_down(tmp_path: Path) -> None:
    from yulon.selfupdate.swap import clear_stamp

    install, _target, _witness = _ready_to_swap(tmp_path)
    layout.work_dir(install, layout.OLD_NAME).mkdir(parents=True, exist_ok=True)
    layout.helper_stamp(install).write_text("old", encoding="utf-8")
    layout.stand_down_path(install).write_text("older", encoding="utf-8")

    clear_stamp(install)

    assert not layout.helper_stamp(install).exists()
    assert not layout.stand_down_path(install).exists()


@posix_only
def test_only_one_of_two_helpers_on_one_pid_swaps(tmp_path: Path) -> None:
    """**The mixed-version install with no backup** (round 4, measured 6 of 6).

    Both helpers used to pass their checks, both swapped, and the second moved
    the first's NEW executable into a backup that then held only bookkeeping —
    so the old executable was gone entirely. `mkdir` of the lock is atomic:
    one takes it, the other exits 73 having moved nothing.
    """
    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "_internal").mkdir()
    (target / "_internal" / "lib").write_text("old lib", encoding="utf-8")
    _a_launcher(target / "yulon", "old", witness)
    _staged_folder(install, label="new", witness=witness)

    app = subprocess.Popen(["sleep", "0.6"])
    plans = [
        _a_ready_plan(
            install,
            layout.work_dir(install, layout.NEW_NAME),
            pid=app.pid,
            script_dir=tmp_path,
            entries=("yulon", "_internal"),
            platform_id="linux",
        )
        for _ in range(2)
    ]
    helpers = [subprocess.Popen(p.argv, stdin=subprocess.DEVNULL) for p in plans]
    try:
        app.wait(timeout=HELPER_DEADLINE)
        codes = [h.wait(timeout=HELPER_DEADLINE) for h in helpers]
    finally:
        for child in [app, *helpers]:
            if child.poll() is None:  # pragma: no cover - only on a failing run
                child.kill()
                child.wait()

    assert sorted(codes) == [0, 73], f"both helpers acted: {codes}"
    _until(lambda: witness.exists(), "the winner relaunched")
    assert witness.read_text(encoding="utf-8") == "new", "the loser swapped too"
    assert (target / "_internal" / "lib").read_text(encoding="utf-8") == "new lib"
    backup = layout.work_dir(install, layout.OLD_NAME)
    assert "old" in (backup / "yulon").read_text(encoding="utf-8"), "the old build was lost"
    assert (backup / "_internal" / "lib").read_text(encoding="utf-8") == "old lib"


@posix_only
def test_a_helper_whose_app_had_already_gone_still_reads_the_stand_down(tmp_path: Path) -> None:
    """**The check immediately before the first move**, reached deterministically.

    The app's pid is already dead when the helper starts, so the wait loop's
    body never runs even once and the only thing that can stop this helper is
    the re-read it does at the last moment before moving anything. That is the
    real shape of the orphan: the app gave up and quit while the helper was
    still getting going, and a moment later the swap happened into a window
    nobody was watching.
    """
    from yulon.selfupdate.swap import stand_down

    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "_internal").mkdir()
    (target / "_internal" / "lib").write_text("old lib", encoding="utf-8")
    _a_launcher(target / "yulon", "old", witness)
    _staged_folder(install, label="new", witness=witness)
    before = _hash_tree(target, ignoring={layout.NEW_NAME, layout.OLD_NAME})

    plan = _a_ready_plan(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=_a_dead_pid(),
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    stand_down(install, plan)
    assert layout.stand_down_path(install).exists(), "the precondition: it was told to stop"

    helper = subprocess.Popen(plan.argv, stdin=subprocess.DEVNULL)
    try:
        code = helper.wait(timeout=HELPER_DEADLINE)
    finally:
        if helper.poll() is None:  # pragma: no cover - only on a failing run
            helper.kill()
            helper.wait(timeout=HELPER_DEADLINE)

    assert code == 74, "the helper moved on a stand-down it should have read"
    assert _hash_tree(target, ignoring={layout.NEW_NAME, layout.OLD_NAME}) == before
    assert not witness.exists(), "the orphan swapped and relaunched anyway"
    log = (layout.work_dir(install, layout.OLD_NAME) / layout.HELPER_LOG).read_text("utf-8")
    assert "before moving anything" in log


@posix_only
def test_a_helper_told_to_stand_down_while_waiting_moves_nothing(tmp_path: Path) -> None:
    """**The orphan** (round 4, M1): it swapped twelve seconds after the app gave up."""
    from yulon.selfupdate.swap import stand_down

    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "_internal").mkdir()
    (target / "_internal" / "lib").write_text("old lib", encoding="utf-8")
    _a_launcher(target / "yulon", "old", witness)
    _staged_folder(install, label="new", witness=witness)
    before = _hash_tree(target, ignoring={layout.NEW_NAME, layout.OLD_NAME})

    app = subprocess.Popen(["sleep", "30"])
    try:
        plan = _a_ready_plan(
            install,
            layout.work_dir(install, layout.NEW_NAME),
            pid=app.pid,
            script_dir=tmp_path,
            entries=("yulon", "_internal"),
            platform_id="linux",
        )
        helper = subprocess.Popen(plan.argv, stdin=subprocess.DEVNULL)
        try:
            _until(layout.helper_stamp(install).exists, "the helper reported in")
            stand_down(install, plan)
            code = helper.wait(timeout=HELPER_DEADLINE)
        finally:
            if helper.poll() is None:  # pragma: no cover - only on a failing run
                helper.kill()
                helper.wait(timeout=HELPER_DEADLINE)
    finally:
        app.kill()
        app.wait(timeout=HELPER_DEADLINE)
    assert app.poll() is not None

    assert code == 74, "the helper did not stand down"
    # And now the app really has gone: nothing may happen afterwards either.
    time.sleep(0.5)
    assert _hash_tree(target, ignoring={layout.NEW_NAME, layout.OLD_NAME}) == before
    assert not witness.exists(), "the orphan swapped and relaunched anyway"
    assert not plan.script.exists(), "the helper left itself behind"
    assert not (layout.work_dir(install, layout.OLD_NAME) / layout.HELPER_LOCK).exists()


# -- round 5: the lock outlives the process, and a backup somebody is using ---


def _an_appimage(root: Path, witness: Path) -> tuple[Install, Path]:
    """An AppImage install with a marked staged build beside it."""
    target = root / "Yulon-v0.8.66-Public-x86_64.AppImage"
    _a_launcher(target, "old", witness)
    install = Install(InstallKind.APPIMAGE, target, "", True)
    staged = layout.work_dir(install, layout.NEW_NAME)
    staged.mkdir()
    _a_launcher(staged / layout.APPIMAGE_ENTRY, "new", witness)
    _mark(install, layout.NEW_NAME)
    return install, target


@posix_only
def test_ending_a_helper_leaves_no_lock_and_no_script(tmp_path: Path) -> None:
    """**dash runs no EXIT trap on SIGTERM** (round 5, M1), and this is the whole cost.

    Measured through the real spawn: the helper came back with rc -15 and left
    `helper.lock` and its own script behind, so the very next press — the one
    the app's own message asks for — got exit 73 and no stamp, and did so for
    the rest of the session. Here the app stands it down, ends it, and clears
    up after it; then a SECOND attempt is armed and started, and has to work.
    """
    from yulon.selfupdate.swap import _spawn_detached, arm, end_helper, release_after, stand_down

    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "_internal").mkdir()
    (target / "_internal" / "lib").write_text("old lib", encoding="utf-8")
    _a_launcher(target / "yulon", "old", witness)
    _staged_folder(install, label="new", witness=witness)

    app = subprocess.Popen(["sleep", "30"])
    try:
        plan = _a_ready_plan(
            install,
            layout.work_dir(install, layout.NEW_NAME),
            pid=app.pid,
            script_dir=tmp_path,
            entries=("yulon", "_internal"),
            platform_id="linux",
        )
        handle = _spawn_detached(plan.argv)
        _until(layout.helper_stamp(install).exists, "the helper reported in")
        assert layout.lock_holder(install) is not None, "the precondition: it took the lock"

        stand_down(install, plan)
        assert end_helper(handle, seconds=HELPER_DEADLINE) is True, "the helper would not stop"

        # **Before the app tidies anything**: the script's own TERM trap has to
        # have run. `trap cleanup EXIT` alone does not run on a signal in dash,
        # which is what `/bin/sh` is on most Linuxes — measured rc -15 with the
        # lock and the script both still there.
        assert layout.lock_holder(install) is None, "the helper's own trap did not run"
        assert not plan.script.exists(), "the script was left in the temp directory"

        release_after(install, plan)
        assert not layout.helper_stamp(install).exists()
        assert not layout.stand_down_path(install).exists()

        # And the press the app's own message asks for now works.
        again = arm(plan)
        second = _spawn_detached(again.argv)
        try:
            _until(
                lambda: layout.stamp_holds(install, again.nonce),
                "the second helper reported in",
            )
        finally:
            stand_down(install, again)
            assert end_helper(second, seconds=HELPER_DEADLINE) is True
            release_after(install, again)
    finally:
        app.kill()
        app.wait(timeout=HELPER_DEADLINE)
    assert not witness.exists(), "something was swapped and relaunched"


def test_ending_a_helper_that_ignores_everything_answers_false() -> None:
    """`end_helper` is asked for a FACT, and a wait that timed out was swallowed.

    Terminate, wait, kill, wait — and if it is still there, say so, because the
    caller's next move is to start another one.
    """
    from yulon.selfupdate.swap import end_helper

    class Deathless:
        """A handle for a process nothing can stop: the shape, not a process."""

        def __init__(self) -> None:
            self.asked: list[str] = []

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            self.asked.append("terminate")

        def kill(self) -> None:
            self.asked.append("kill")

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired("helper", timeout or 0)

    handle = Deathless()
    assert end_helper(handle, seconds=0.01) is False
    assert handle.asked == ["terminate", "kill"], "it was not escalated"
    assert end_helper(None) is True, "nothing to stop is stopped"


@posix_only
def test_a_stopped_helper_does_not_take_a_second_helpers_lock_with_it(tmp_path: Path) -> None:
    """**The exit trap removed a lock that was no longer its own** (round 5, M2).

    The state: a first helper is alive because the close was refused after it
    had stamped, and the player presses Update now again. Planning the second
    attempt used to rmtree `.yulon-old` — the live helper's lock with it — and
    when that first helper finally exited, its trap removed the SECOND one's
    lock; the second's re-validation then failed and the app closed with
    nothing swapped and nothing relaunched.
    """
    from yulon.selfupdate.swap import _spawn_detached, end_helper

    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "_internal").mkdir()
    (target / "_internal" / "lib").write_text("old lib", encoding="utf-8")
    _a_launcher(target / "yulon", "old", witness)
    _staged_folder(install, label="new", witness=witness)

    app = subprocess.Popen(["sleep", "30"])
    first = None
    try:
        one = _a_ready_plan(
            install,
            layout.work_dir(install, layout.NEW_NAME),
            pid=app.pid,
            script_dir=tmp_path,
            entries=("yulon", "_internal"),
            platform_id="linux",
        )
        first = _spawn_detached(one.argv)
        _until(layout.helper_stamp(install).exists, "the first helper reported in")
        held = layout.lock_holder(install)
        assert held is not None and held.nonce == one.nonce and held.alive()

        # The second press. Planning it must not pull the rug out from under
        # the first helper: it stands it down and waits for the lock to go.
        _staged_folder(install, label="new", witness=witness)
        two = _a_ready_plan(
            install,
            layout.work_dir(install, layout.NEW_NAME),
            pid=app.pid,
            script_dir=tmp_path,
            entries=("yulon", "_internal"),
            platform_id="linux",
        )
        assert first.wait(timeout=HELPER_DEADLINE) == 74, "the first helper was not stood down"
        second = _spawn_detached(two.argv)
        try:
            _until(
                lambda: layout.stamp_holds(install, two.nonce),
                "the second helper reported in",
            )
            held = layout.lock_holder(install)
            assert held is not None and held.nonce == two.nonce, "the second helper has no lock"
            # The first helper has already exited; its trap must not have taken
            # the second one's lock with it.
            time.sleep(0.3)
            held = layout.lock_holder(install)
            assert held is not None and held.nonce == two.nonce, "the first helper freed it"
        finally:
            from yulon.selfupdate.swap import release_after, stand_down

            stand_down(install, two)
            assert end_helper(second, seconds=HELPER_DEADLINE) is True
            release_after(install, two)
    finally:
        for child in (app, first):
            if child is not None and child.poll() is None:  # pragma: no cover - failing run only
                child.kill()
                child.wait(timeout=HELPER_DEADLINE)


@posix_only
def test_a_lock_left_by_a_helper_that_is_gone_does_not_refuse_an_update(tmp_path: Path) -> None:
    """The other side of M2: a lock with a dead pid in it is rubbish, not somebody's work."""
    from yulon.selfupdate.swap import make_way_for_the_backup

    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    backup = layout.work_dir(install, layout.OLD_NAME)
    (backup / layout.HELPER_LOCK).mkdir(parents=True)
    (backup / layout.HELPER_LOCK / "owner").write_text("beefbeefbeefbeef", encoding="utf-8")
    (backup / layout.HELPER_LOCK / "pid").write_text(str(_a_dead_pid()), encoding="utf-8")

    assert make_way_for_the_backup(install, seconds=0.2) is None


@posix_only
def test_a_backup_a_live_helper_holds_is_refused_rather_than_deleted(tmp_path: Path) -> None:
    """And when it will not let go, the player is told — nothing is deleted under it."""
    from yulon.selfupdate.swap import make_way_for_the_backup

    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    backup = layout.work_dir(install, layout.OLD_NAME)
    lock = backup / layout.HELPER_LOCK
    lock.mkdir(parents=True)
    (lock / "owner").write_text("beefbeefbeefbeef", encoding="utf-8")
    holder = subprocess.Popen(["sleep", "30"])
    try:
        (lock / "pid").write_text(str(holder.pid), encoding="utf-8")

        said = make_way_for_the_backup(install, seconds=0.3)

        assert said is not None and "still working in this folder" in said
        assert lock.is_dir(), "the lock was removed under a live helper"
        assert layout.stand_down_path(install).read_text(encoding="utf-8").strip() == (
            "beefbeefbeefbeef"
        ), "it was not even asked to stop"
    finally:
        holder.kill()
        holder.wait(timeout=HELPER_DEADLINE)


@posix_only
def test_a_swap_leaves_no_helper_script_behind(tmp_path: Path) -> None:
    """One script per update stayed in `%TEMP%` for ever (round 5, Windows gate).

    The helper removes its own on every path it can exit by; this is the whole
    happy path, with the temp directory of its own so the count means something.
    """
    install, target, witness = _ready_to_swap(tmp_path)
    scripts = tmp_path / "scripts"
    scripts.mkdir()

    app = subprocess.Popen(["sleep", "0.3"])
    plan = _a_ready_plan(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=app.pid,
        script_dir=scripts,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    assert sorted(p.name for p in scripts.iterdir()) == [plan.script.name], "armed twice"
    assert _run_helper(plan, app) == 0

    _until(lambda: witness.exists(), "the new build was launched")
    assert list(scripts.iterdir()) == [], "the helper left its script behind"


# -- round 5, N3: the AppImage helper has the same three properties -----------


@posix_only
def test_only_one_of_two_appimage_helpers_swaps(tmp_path: Path) -> None:
    """The AppImage script is a second implementation and was never held to this."""
    from yulon.selfupdate.swap import _spawn_detached

    witness = tmp_path / "launched"
    install, target = _an_appimage(tmp_path, witness)
    staged = layout.work_dir(install, layout.NEW_NAME)

    app = subprocess.Popen(["sleep", "0.6"])
    plans = [
        _a_ready_plan(install, staged, pid=app.pid, script_dir=tmp_path, platform_id="linux")
        for _ in range(2)
    ]
    helpers = [_spawn_detached(p.argv) for p in plans]
    try:
        app.wait(timeout=HELPER_DEADLINE)
        codes = [h.wait(timeout=HELPER_DEADLINE) for h in helpers]
    finally:
        for child in [app, *helpers]:
            if child.poll() is None:  # pragma: no cover - only on a failing run
                child.kill()
                child.wait()

    assert sorted(codes) == [0, 73], f"both helpers acted: {codes}"
    _until(lambda: witness.exists(), "the winner relaunched")
    assert witness.read_text(encoding="utf-8") == "new", "the loser swapped too"
    old = layout.work_dir(install, layout.OLD_NAME)
    assert "old" in (old / layout.APPIMAGE_ENTRY).read_text(encoding="utf-8")


@posix_only
def test_an_appimage_helper_reads_the_stand_down_before_its_first_move(tmp_path: Path) -> None:
    """Deterministically: the app's pid is already gone, so only the last check can stop it."""
    from yulon.selfupdate.swap import stand_down

    witness = tmp_path / "launched"
    install, target = _an_appimage(tmp_path, witness)
    staged = layout.work_dir(install, layout.NEW_NAME)
    before = target.read_text(encoding="utf-8")

    plan = _a_ready_plan(
        install, staged, pid=_a_dead_pid(), script_dir=tmp_path, platform_id="linux"
    )
    stand_down(install, plan)

    helper = subprocess.Popen(plan.argv, stdin=subprocess.DEVNULL)
    try:
        code = helper.wait(timeout=HELPER_DEADLINE)
    finally:
        if helper.poll() is None:  # pragma: no cover - only on a failing run
            helper.kill()
            helper.wait(timeout=HELPER_DEADLINE)

    assert code == 74
    assert target.read_text(encoding="utf-8") == before, "the AppImage was replaced anyway"
    assert not witness.exists()
    assert not plan.script.exists(), "the helper left itself behind"


@posix_only
def test_an_appimage_helper_that_loses_the_lock_mid_wait_moves_nothing(tmp_path: Path) -> None:
    """`still_ours` reads the lock's OWNER, not merely that a lock is there.

    Taken from the folder helper, where the same two lines were unpinned: a
    lock whose owner has become another attempt's nonce is not ours, and acting
    on it is the two-helper swap by another route.
    """
    witness = tmp_path / "launched"
    install, target = _an_appimage(tmp_path, witness)
    staged = layout.work_dir(install, layout.NEW_NAME)
    before = target.read_text(encoding="utf-8")

    app = subprocess.Popen(["sleep", "0.5"])
    plan = _a_ready_plan(install, staged, pid=app.pid, script_dir=tmp_path, platform_id="linux")
    helper = subprocess.Popen(plan.argv, stdin=subprocess.DEVNULL)
    try:
        _until(layout.helper_stamp(install).exists, "the helper reported in")
        (layout.helper_lock(install) / "owner").write_text("0000111122223333", "utf-8")
        app.wait(timeout=HELPER_DEADLINE)
        code = helper.wait(timeout=HELPER_DEADLINE)
    finally:
        for child in (app, helper):
            if child.poll() is None:  # pragma: no cover - only on a failing run
                child.kill()
                child.wait(timeout=HELPER_DEADLINE)

    assert code == 74, "it swapped an install another attempt had taken"
    assert target.read_text(encoding="utf-8") == before
    assert not witness.exists()


@posix_only
def test_a_folder_helper_whose_stamp_was_overwritten_moves_nothing(tmp_path: Path) -> None:
    """The other unpinned line of `still_ours`: the stamp must still be ours too."""
    install, target, witness = _ready_to_swap(tmp_path)
    before = _hash_tree(target, ignoring={layout.NEW_NAME, layout.OLD_NAME})

    app = subprocess.Popen(["sleep", "0.5"])
    plan = _a_ready_plan(
        install,
        layout.work_dir(install, layout.NEW_NAME),
        pid=app.pid,
        script_dir=tmp_path,
        entries=("yulon", "_internal"),
        platform_id="linux",
    )
    helper = subprocess.Popen(plan.argv, stdin=subprocess.DEVNULL)
    try:
        _until(layout.helper_stamp(install).exists, "the helper reported in")
        layout.helper_stamp(install).write_text("4444555566667777", encoding="utf-8")
        app.wait(timeout=HELPER_DEADLINE)
        code = helper.wait(timeout=HELPER_DEADLINE)
    finally:
        for child in (app, helper):
            if child.poll() is None:  # pragma: no cover - only on a failing run
                child.kill()
                child.wait(timeout=HELPER_DEADLINE)

    assert code == 74, "it acted although another attempt had stamped over it"
    assert _hash_tree(target, ignoring={layout.NEW_NAME, layout.OLD_NAME}) == before
    assert not witness.exists()


@posix_only
def test_a_helper_that_could_not_run_its_trap_still_leaves_no_lock(tmp_path: Path) -> None:
    """**SIGKILL runs nothing, and neither does `TerminateProcess`** (round 5, M1).

    The trap is the helper's half; this is the app's. A helper killed outright
    — which is what `end_helper` escalates to, and what Windows does always —
    leaves its lock behind, and the press the app's own message asks for then
    gets exit 73 and no stamp. So the app removes it, and only its own.
    """
    from yulon.selfupdate.swap import _spawn_detached, arm, release_after, stand_down

    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "_internal").mkdir()
    (target / "_internal" / "lib").write_text("old lib", encoding="utf-8")
    _a_launcher(target / "yulon", "old", witness)
    _staged_folder(install, label="new", witness=witness)

    app = subprocess.Popen(["sleep", "30"])
    try:
        plan = _a_ready_plan(
            install,
            layout.work_dir(install, layout.NEW_NAME),
            pid=app.pid,
            script_dir=tmp_path,
            entries=("yulon", "_internal"),
            platform_id="linux",
        )
        handle = _spawn_detached(plan.argv)
        _until(layout.helper_stamp(install).exists, "the helper reported in")
        stand_down(install, plan)
        handle.kill()  # type: ignore[attr-defined]
        handle.wait(timeout=HELPER_DEADLINE)  # type: ignore[attr-defined]
        held = layout.lock_holder(install)
        assert (
            held is not None and held.nonce == plan.nonce
        ), "the precondition: a killed helper leaves its lock"

        release_after(install, plan)
        assert layout.lock_holder(install) is None, "the lock outlived the helper"

        # And the press the message asks for is not refused by the leavings.
        again = arm(plan)
        second = _spawn_detached(again.argv)
        try:
            _until(
                lambda: layout.stamp_holds(install, again.nonce),
                "the second helper reported in",
            )
        finally:
            stand_down(install, again)
            from yulon.selfupdate.swap import end_helper

            assert end_helper(second, seconds=HELPER_DEADLINE) is True
            release_after(install, again)
    finally:
        app.kill()
        app.wait(timeout=HELPER_DEADLINE)
    assert not witness.exists(), "something was swapped and relaunched"


@posix_only
def test_an_exiting_helper_never_frees_a_lock_that_has_become_another_ones(
    tmp_path: Path,
) -> None:
    """**The exit trap took the second helper's lock** (round 5, M2, 5 runs of 12).

    The app no longer deletes a backup a live helper holds, so this state is
    hard to reach from the app — which is exactly why it is built by hand
    here: the backup directory is replaced under a running helper, the way the
    old `_make_the_backup_dir` did it, and a second helper takes a lock of the
    same name. What is asserted is that the first helper's exit leaves that
    lock alone, because its `owner` file is no longer its own nonce.
    """
    from yulon.selfupdate.swap import _spawn_detached, end_helper, release_after, stand_down

    install = _plain(tmp_path)
    target = install.target
    assert target is not None
    witness = tmp_path / "launched"
    (target / "_internal").mkdir()
    (target / "_internal" / "lib").write_text("old lib", encoding="utf-8")
    _a_launcher(target / "yulon", "old", witness)
    _staged_folder(install, label="new", witness=witness)

    app = subprocess.Popen(["sleep", "30"])
    first = None
    second = None
    try:
        one = _a_ready_plan(
            install,
            layout.work_dir(install, layout.NEW_NAME),
            pid=app.pid,
            script_dir=tmp_path,
            entries=("yulon", "_internal"),
            platform_id="linux",
        )
        first = _spawn_detached(one.argv)
        _until(layout.helper_stamp(install).exists, "the first helper reported in")

        # What the app used to do: the backup goes, live helper's lock and all.
        layout.discard_ours(install, layout.OLD_NAME)
        _staged_folder(install, label="new", witness=witness)
        two = _a_ready_plan(
            install,
            layout.work_dir(install, layout.NEW_NAME),
            pid=app.pid,
            script_dir=tmp_path,
            entries=("yulon", "_internal"),
            platform_id="linux",
        )
        second = _spawn_detached(two.argv)
        _until(lambda: layout.stamp_holds(install, two.nonce), "the second helper reported in")
        held = layout.lock_holder(install)
        assert held is not None and held.nonce == two.nonce, "the second helper has no lock"

        # Now the first one exits. Its `have_lock` is still 1 and the lock is
        # still there — but it is not ITS lock any more.
        stand_down(install, one)
        assert end_helper(first, seconds=HELPER_DEADLINE) is True
        time.sleep(0.3)

        held = layout.lock_holder(install)
        assert held is not None, "the first helper freed the second one's lock"
        assert held.nonce == two.nonce
    finally:
        for child in (app, first, second):
            if child is not None and child.poll() is None:  # pragma: no cover - failing run
                child.kill()
                child.wait(timeout=HELPER_DEADLINE)
        release_after(install, two)
