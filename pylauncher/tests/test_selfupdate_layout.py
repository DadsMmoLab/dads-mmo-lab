"""Marked working directories, shipped entries, and the instance lock (T90 plan 3).

Everything a delete in this package is gated on. The rules here exist because
the first design had none of them and destroyed a `thesis.docx`; each test says
which half of that it holds.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from yulon.selfupdate import layout
from yulon.selfupdate.detect import Install, InstallKind


def _folder(root: Path, executable: str = "yulon") -> Install:
    target = root / "app"
    (target / "_internal").mkdir(parents=True, exist_ok=True)
    (target / executable).write_text("the running build", encoding="utf-8")
    return Install(InstallKind.TARBALL, target, executable, True)


def _appimage(root: Path) -> Install:
    target = root / "Yulon-v0.8.66-Public-x86_64.AppImage"
    target.write_bytes(b"the running build")
    return Install(InstallKind.APPIMAGE, target, "", True)


def _a_stand_in(exe: Path) -> subprocess.Popen[bytes]:
    """A live process whose `/proc/<pid>/exe` really is `exe`, or a skip.

    A copy of `sleep` and not of the interpreter: a Python copied out of its
    prefix cannot find its standard library and exits in milliseconds, which
    made these a race against a process that was already gone.

    **`sleep` is not always a program** (round 5, N6): on a busybox box it is an
    applet, and a copy of busybox invoked under another name looks for an
    applet of THAT name and exits at once. A shell script cannot stand in
    either — `/proc/<pid>/exe` would be the interpreter, not the copy. So the
    copy is started and watched: if it is not alive a moment later, this box
    cannot answer the question and the test says so instead of failing.
    """
    import shutil

    sleep = shutil.which("sleep")
    if sleep is None:
        pytest.skip("this box has no `sleep` to stand in for the app")
    shutil.copy2(sleep, exe)
    child = subprocess.Popen([str(exe), "30"])
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if child.poll() is not None:
            pytest.skip(f"a copy of {sleep} does not run under another name (busybox?)")
        if layout.pid_is_this_app(child.pid, exe) is True:
            return child
        time.sleep(0.02)
    child.kill()
    child.wait(timeout=30)
    pytest.skip("a copied `sleep` could not be identified through /proc on this box")


def _marker(**kw: object) -> layout.Marker:
    base: dict[str, object] = {
        "role": layout.NEW_NAME,
        "from_version": "0.8.66-Public",
        "to_version": "v0.8.70-Public",
        "pid": os.getpid(),
        "stamp": layout.now(),
    }
    return layout.Marker(**{**base, **kw})  # type: ignore[arg-type]


# -- where things live -------------------------------------------------------


def test_a_folder_install_keeps_its_working_directories_inside_itself(tmp_path: Path) -> None:
    """Inside, so the swap is a rename — and so nothing beside the folder is ever touched."""
    install = _folder(tmp_path)
    assert install.target is not None
    assert layout.work_dir(install, layout.NEW_NAME) == install.target / ".yulon-new"
    assert layout.work_dir(install, layout.OLD_NAME) == install.target / ".yulon-old"
    assert layout.marker_path(install, layout.NEW_NAME).parent == install.target / ".yulon-new"


def test_an_appimage_keeps_its_working_directories_beside_the_file(tmp_path: Path) -> None:
    install = _appimage(tmp_path)
    assert install.target is not None
    assert layout.work_dir(install, layout.NEW_NAME) == Path(str(install.target) + ".yulon-new")
    assert layout.marker_path(install, layout.OLD_NAME).name == layout.MARKER_NAME


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b", "/abs", 7, None])
def test_only_a_single_path_segment_is_an_entry(name: object) -> None:
    """The one rule the Python side and BOTH helper scripts apply to every entry."""
    assert layout.is_entry_name(name) is False


# -- the marker --------------------------------------------------------------


def test_a_marker_round_trips(tmp_path: Path) -> None:
    install = _folder(tmp_path)
    written = _marker(entries=("yulon", "_internal"), state=layout.SWAPPING)
    layout.write_marker(install, layout.NEW_NAME, written)
    assert layout.read_marker(install, layout.NEW_NAME) == written


def test_a_directory_with_no_marker_is_not_ours(tmp_path: Path) -> None:
    """The whole point: a `.yulon-old` the player made is never this app's to delete."""
    install = _folder(tmp_path)
    assert install.target is not None
    theirs = install.target / ".yulon-old"
    theirs.mkdir()
    # With something of theirs in it. An EMPTY one is this app's own leftover
    # and is deliberately removable (S1); what must never be touched is a
    # directory that holds anything.
    (theirs / "save.dat").write_bytes(b"a saved game")
    assert layout.read_marker(install, layout.OLD_NAME) is None
    assert layout.is_ours(install, layout.OLD_NAME) is False
    assert layout.discard_ours(install, layout.OLD_NAME) is False
    assert (theirs / "save.dat").read_bytes() == b"a saved game"


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        "[]",
        '{"role": ".yulon-old"}',
        '{"signature": "something-else", "role": ".yulon-old"}',
        '{"signature": "yulon-self-update", "role": ".yulon-old", "entries": ["../x"]}',
        '{"signature": "yulon-self-update", "role": ".yulon-old", "entries": "yulon"}',
    ],
)
def test_a_marker_that_does_not_parse_means_not_ours(payload: str, tmp_path: Path) -> None:
    install = _folder(tmp_path)
    assert install.target is not None
    old = install.target / ".yulon-old"
    old.mkdir()
    (old / layout.MARKER_NAME).write_text(payload, encoding="utf-8")
    # A file of somebody's beside it: a directory holding ONLY a marker is
    # deliberately treated as ours (S1), so the thing under test here has to be
    # a directory with something in it to lose.
    (old / "save.dat").write_bytes(b"a saved game")
    assert layout.read_marker(install, layout.OLD_NAME) is None
    assert layout.discard_ours(install, layout.OLD_NAME) is False
    assert (old / "save.dat").read_bytes() == b"a saved game"


def test_a_marker_with_a_byte_order_mark_still_reads(tmp_path: Path) -> None:
    """Someone opens it in Notepad to see what it is. That must not break an update."""
    install = _folder(tmp_path)
    assert install.target is not None
    # Written by the app — so it carries this install's path and token — and
    # then given the byte-order mark a Windows editor would add.
    layout.write_marker(install, layout.OLD_NAME, _marker(role=layout.OLD_NAME))
    path = layout.marker_path(install, layout.OLD_NAME)
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert layout.read_marker(install, layout.OLD_NAME) is not None


def test_a_marked_directory_is_removed_and_nothing_else_is(tmp_path: Path) -> None:
    install = _folder(tmp_path)
    assert install.target is not None
    layout.write_marker(install, layout.OLD_NAME, _marker(role=layout.OLD_NAME))
    (install.target / ".yulon-old" / "yulon").write_text("previous", encoding="utf-8")
    keep = install.target / "thesis.docx"
    keep.write_bytes(b"years of work")

    assert layout.discard_ours(install, layout.OLD_NAME) is True

    assert not (install.target / ".yulon-old").exists()
    assert keep.read_bytes() == b"years of work"
    assert (install.target / "yulon").exists()


# -- what the build shipped --------------------------------------------------


def test_the_shipped_manifest_says_what_may_be_replaced(tmp_path: Path) -> None:
    install = _folder(tmp_path)
    assert install.target is not None
    (install.target / "_internal" / layout.SHIPPED_MANIFEST).write_text(
        "_internal\nyulon\nextras\n", encoding="utf-8"
    )
    assert layout.shipped_entries(install) == frozenset({"_internal", "yulon", "extras"})


def test_a_build_with_no_manifest_falls_back_to_what_every_one_dir_build_has(
    tmp_path: Path,
) -> None:
    """Measured against the real v0.8.712-fixtest artifacts: exactly these two names.

    The Linux tar.gz's `yulon/` holds `_internal` and `yulon`; the Windows
    zip's top level holds `_internal` and `yulon.exe`. So the fallback is the
    truth today, and the manifest exists for a build that ships a third thing.
    """
    install = _folder(tmp_path)
    assert layout.shipped_entries(install) == frozenset({"_internal", "yulon"})
    windows = Install(InstallKind.WINDOWS_ZIP, install.target, "yulon.exe", True)
    assert layout.shipped_entries(windows) == frozenset({"_internal", "yulon.exe"})


def test_a_manifest_that_names_nothing_usable_falls_back_too(tmp_path: Path) -> None:
    install = _folder(tmp_path)
    assert install.target is not None
    (install.target / "_internal" / layout.SHIPPED_MANIFEST).write_text(
        "../etc\n\n/abs\n", encoding="utf-8"
    )
    assert layout.shipped_entries(install) == frozenset({"_internal", "yulon"})


def test_the_running_executable_is_always_ours_whatever_the_manifest_says(
    tmp_path: Path,
) -> None:
    """It is running out of that file; nothing else can be that certain."""
    install = _folder(tmp_path)
    assert install.target is not None
    (install.target / "_internal" / layout.SHIPPED_MANIFEST).write_text(
        "_internal\n", encoding="utf-8"
    )
    assert "yulon" in layout.shipped_entries(install)


def test_the_real_published_bundle_ships_exactly_what_the_fallback_names() -> None:
    """The measurement, written down where it can go stale loudly.

    `v0.8.712-fixtest`, downloaded 2026-09-21: the tar.gz top level is
    `['yulon']` and inside it `['_internal', 'yulon']`; the zip top level is
    `['_internal', 'yulon.exe']`. If a future build ships a third entry, the
    manifest step in `release.yml` is what carries it and this constant stays
    as the answer for builds cut before that step existed.
    """
    assert layout.FALLBACK_ENTRIES == ("_internal",)


# -- the instance lock -------------------------------------------------------


def test_a_staging_directory_left_by_a_dead_process_is_ours_to_replace(tmp_path: Path) -> None:
    install = _folder(tmp_path)
    dead = subprocess.Popen([sys.executable, "-c", "raise SystemExit(0)"])
    dead.wait(timeout=30)
    layout.write_marker(install, layout.NEW_NAME, _marker(pid=dead.pid))
    assert layout.another_copy_is_updating(install, os.getpid()) is None


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="identity is read off /proc here")
def test_a_staging_directory_another_copy_of_the_app_made_is_not(tmp_path: Path) -> None:
    """One instance's stage() deleting another's staged build is how an install was lost.

    The other process really is running the install's executable — a copied
    interpreter under the install's own name — because since round 4 a live pid
    on its own is not enough to refuse.
    """
    install = _folder(tmp_path)
    assert install.target is not None
    exe = install.target / install.executable
    exe.unlink()
    alive = _a_stand_in(exe)
    try:
        layout.write_marker(install, layout.NEW_NAME, _marker(pid=alive.pid))
        deadline = time.monotonic() + 10
        said = None
        while time.monotonic() < deadline and said is None:
            said = layout.another_copy_is_updating(install, os.getpid())
            time.sleep(0.02)
        assert said is not None and "already installing" in said
    finally:
        alive.kill()
        alive.wait(timeout=30)
    assert alive.poll() is not None


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="identity is read off /proc here")
def test_a_marker_naming_a_live_pid_that_is_not_this_app_refuses_nothing(tmp_path: Path) -> None:
    """**Pid reuse** (round 4, S2), and the dead end it used to be.

    The pid in the marker is alive — it belongs to something else entirely,
    which is what a recycled pid looks like. Before the identity check this
    answered "another copy of Yu'lon is already installing" for as long as that
    process lived, and the only way out was deleting a hidden folder by hand.
    """
    install = _folder(tmp_path)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert layout.pid_is_this_app(other.pid, install.target / install.executable) is False
        layout.write_marker(install, layout.NEW_NAME, _marker(pid=other.pid))
        assert layout.another_copy_is_updating(install, os.getpid()) is None
    finally:
        other.kill()
        other.wait(timeout=30)
    assert other.poll() is not None


def test_a_marker_too_old_to_believe_is_ignored_when_identity_cannot_be_asked(
    tmp_path: Path,
) -> None:
    """An AppImage has no executable to compare, so age is all there is.

    Fifteen minutes is the whole difference between refusing and carrying on,
    and the pid is this very process, so the liveness check genuinely passes.
    """
    install = _appimage(tmp_path)
    assert layout.pid_is_this_app(os.getpid(), None) is None

    layout.write_marker(install, layout.NEW_NAME, _marker(pid=os.getpid()))
    said = layout.another_copy_is_updating(install, our_pid=os.getpid() + 1)
    assert said is not None and "already installing" in said, "a fresh marker must be believed"

    path = layout.marker_path(install, layout.NEW_NAME)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["stamp"] = layout.now() - layout.STALE_MARKER_SECONDS - 1
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert layout.another_copy_is_updating(install, our_pid=os.getpid() + 1) is None


def test_liveness_is_asked_of_a_real_process_and_a_reaped_one() -> None:
    """`pid_is_alive` against the two answers that are not guesses (round 4, S1)."""
    assert layout.pid_is_alive(os.getpid()) is True
    gone = subprocess.Popen([sys.executable, "-c", "raise SystemExit(0)"])
    gone.wait(timeout=30)
    assert gone.poll() is not None, "the precondition: the child has been reaped"
    assert layout.pid_is_alive(gone.pid) is False
    assert layout.pid_is_alive(0) is False
    assert layout.pid_is_alive(-1) is False


def test_on_windows_liveness_never_goes_near_os_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """**`os.kill(pid, 0)` sends Ctrl-C on Windows** (round 4, S1).

    CPython maps signal 0 there to `GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)`
    — so the liveness probe the POSIX branch uses would, against a console
    process group, interrupt the very process it was asking about. The Windows
    branch is injected here because it cannot be run from Linux; what is pinned
    is that it is taken, and that `os.kill` is not.
    """
    asked: list[int] = []

    def never(*args: object, **kw: object) -> None:  # pragma: no cover - must not run
        raise AssertionError("os.kill was used as a liveness probe on Windows")

    def ask_the_kernel(pid: int) -> bool:
        asked.append(pid)
        return True

    monkeypatch.setattr(layout.os, "kill", never)
    monkeypatch.setattr(layout, "_windows_pid_is_alive", ask_the_kernel)

    assert layout.pid_is_alive(4321, windows=True) is True
    assert asked == [4321], "the kernel was not asked"
    assert layout.pid_is_alive(0, windows=True) is False
    assert asked == [4321], "a pid that cannot exist was still asked about"


def test_our_own_staging_directory_is_not_another_copy(tmp_path: Path) -> None:
    install = _folder(tmp_path)
    layout.write_marker(install, layout.NEW_NAME, _marker(pid=os.getpid()))
    assert layout.another_copy_is_updating(install, os.getpid()) is None


def test_no_staging_directory_at_all_is_not_another_copy(tmp_path: Path) -> None:
    assert layout.another_copy_is_updating(_folder(tmp_path), os.getpid()) is None


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="/proc is how this is answered")
def test_another_process_running_the_same_executable_is_found(tmp_path: Path) -> None:
    """The guard that stops a swap happening under a second open copy.

    Driven with a real second process running a real copied interpreter, so
    what is asserted is the `/proc/<pid>/exe` read and not a fake.
    """
    exe = tmp_path / "yulon"
    other = _a_stand_in(exe)
    try:
        deadline = time.monotonic() + 10
        found: list[int] = []
        while time.monotonic() < deadline and not found:
            found = layout.other_instances(exe, os.getpid())
            time.sleep(0.02)
        assert found == [other.pid]
    finally:
        other.kill()
        other.wait(timeout=30)
    assert other.poll() is not None
    assert layout.other_instances(exe, os.getpid()) == []


def test_our_own_process_is_never_another_instance() -> None:
    assert os.getpid() not in layout.other_instances(Path(sys.executable), os.getpid())


def test_a_marker_holds_the_clock_rather_than_a_typed_time(tmp_path: Path) -> None:
    """Stamps come from the clock: a record nobody hand-typed."""
    before = time.time()
    install = _folder(tmp_path)
    layout.write_marker(install, layout.NEW_NAME, _marker())
    marker = layout.read_marker(install, layout.NEW_NAME)
    assert marker is not None
    assert before <= marker.stamp <= time.time() + 1
    raw = json.loads(layout.marker_path(install, layout.NEW_NAME).read_text(encoding="utf-8"))
    assert raw["signature"] == "yulon-self-update"


# -- what a marker is bound to (cold review 2) -------------------------------


def test_a_forged_marker_without_this_installs_token_is_not_ours(tmp_path: Path) -> None:
    """**A marker is a file an archive could contain and a player could type.**

    The second cold review wrote a `.yulon-old/` holding a hand-made marker and
    a `save.dat`, and it was deleted. The marker now carries a secret that
    lives in the config directory, which nothing being unpacked into the
    install folder can read.
    """
    install = _folder(tmp_path)
    assert install.target is not None
    theirs = install.target / layout.OLD_NAME
    theirs.mkdir()
    (theirs / "save.dat").write_bytes(b"a saved game")
    (theirs / layout.MARKER_NAME).write_text(
        json.dumps(
            {
                "signature": "yulon-self-update",
                "role": layout.OLD_NAME,
                "entries": [],
                "target": str(install.target),
                "token": "guessed",
            }
        ),
        encoding="utf-8",
    )

    assert layout.read_marker(install, layout.OLD_NAME) is None
    assert layout.discard_ours(install, layout.OLD_NAME) is False
    assert (theirs / "save.dat").read_bytes() == b"a saved game"


def test_a_marker_naming_a_different_install_is_not_ours(tmp_path: Path) -> None:
    """One copied from another Yu'lon folder, or from a backup of one."""
    install = _folder(tmp_path)
    layout.write_marker(install, layout.OLD_NAME, _marker(role=layout.OLD_NAME))
    path = layout.marker_path(install, layout.OLD_NAME)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["target"] = "/somewhere/else"
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert layout.read_marker(install, layout.OLD_NAME) is None


def test_a_marker_for_the_other_role_is_not_ours(tmp_path: Path) -> None:
    """A `.yulon-new` marker copied into `.yulon-old` is not this app's word about it."""
    install = _folder(tmp_path)
    layout.write_marker(install, layout.NEW_NAME, _marker())
    staged = layout.marker_path(install, layout.NEW_NAME)
    backup = layout.work_dir(install, layout.OLD_NAME)
    backup.mkdir()
    (backup / layout.MARKER_NAME).write_text(staged.read_text(encoding="utf-8"), encoding="utf-8")

    assert layout.read_marker(install, layout.OLD_NAME) is None


def test_the_token_is_kept_in_the_config_dir_and_is_stable(tmp_path: Path) -> None:
    from yulon import update_state

    first = layout.install_token()
    assert len(first) >= 16
    assert layout.install_token() == first, "a second call minted a new token"
    assert update_state.load_update_state().update_token == first


# -- an empty work dir is ours (S1) ------------------------------------------


def test_an_empty_work_dir_counts_as_ours(tmp_path: Path) -> None:
    """`mkdir` succeeded and the marker did not — a disk that filled up in between.

    It used to be a permanent refusal: every later attempt found an unmarked
    `.yulon-new` and stopped. An empty directory under one of this app's own
    names holds nothing of anybody's.
    """
    install = _folder(tmp_path)
    assert install.target is not None
    (install.target / layout.NEW_NAME).mkdir()

    assert layout.is_empty_work_dir(install, layout.NEW_NAME) is True
    assert layout.is_ours(install, layout.NEW_NAME) is True
    assert layout.discard_ours(install, layout.NEW_NAME) is True
    assert not (install.target / layout.NEW_NAME).exists()


def test_a_work_dir_holding_only_an_unreadable_marker_counts_as_ours(tmp_path: Path) -> None:
    """The same failure one step later: the marker was created and not finished."""
    install = _folder(tmp_path)
    assert install.target is not None
    path = install.target / layout.NEW_NAME
    path.mkdir()
    (path / layout.MARKER_NAME).write_text("", encoding="utf-8")

    assert layout.is_empty_work_dir(install, layout.NEW_NAME) is True
    assert layout.discard_ours(install, layout.NEW_NAME) is True


def test_a_work_dir_with_anything_else_in_it_is_not_empty(tmp_path: Path) -> None:
    install = _folder(tmp_path)
    assert install.target is not None
    path = install.target / layout.OLD_NAME
    path.mkdir()
    (path / "save.dat").write_bytes(b"x")
    assert layout.is_empty_work_dir(install, layout.OLD_NAME) is False
    assert layout.discard_ours(install, layout.OLD_NAME) is False
    assert (path / "save.dat").exists()


# -- names a swap may never move (S2) ----------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "my file",
        "tab\tname",
        "*",
        "a*b",
        "a?b",
        "a[b]c",
        "-rf",
        "a:b",
        'a"b',
        "a\x01b",
        ".yulon-new",
        ".yulon-old",
        ".yulon-download",
        ".yulon-marker",
    ],
)
def test_a_name_a_shell_or_windows_would_read_as_something_else_is_not_an_entry(
    name: str,
) -> None:
    """Whitespace and globs are refused rather than quoted (cold review 2, S2).

    The helper's loops no longer word-split — but a `*` reaching a shell at all
    is a class of defect this does not want to depend on one file to avoid, and
    the reserved names are ones the swap must never be able to move.
    """
    assert layout.is_entry_name(name) is False


@pytest.mark.parametrize("name", ["yulon", "yulon.exe", "_internal", "LICENSE.txt", "a-b_c.1"])
def test_an_ordinary_build_name_is_still_an_entry(name: str) -> None:
    assert layout.is_entry_name(name) is True


def test_the_three_work_dirs_are_the_only_names_discard_will_take() -> None:
    install = Install(InstallKind.TARBALL, Path("/opt/app"), "yulon", True)
    for name in layout.WORK_NAMES:
        assert layout.discard_ours(install, name) is True  # nothing there; nothing to do
    for name in ("_internal", "..", "thesis.docx", layout.MARKER_NAME):
        with pytest.raises(ValueError, match="working director"):
            layout.discard_ours(install, name)


# -- the helper lock (round 5) ------------------------------------------------


def _held(install: layout.Install, nonce: str, pid: int) -> Path:
    lock = layout.helper_lock(install)
    lock.mkdir(parents=True)
    (lock / "owner").write_text(nonce, encoding="utf-8")
    (lock / "pid").write_text(str(pid), encoding="utf-8")
    return lock


def test_a_lock_says_who_holds_it_and_whether_they_are_alive(tmp_path: Path) -> None:
    """**The pid is in the lock so the app can tell a live helper from leavings.**

    A helper killed with SIGKILL, or with `TerminateProcess` which cannot be
    trapped at all, leaves the directory behind; without a pid the app could
    only guess whether waiting for it would ever end.
    """
    install = _folder(tmp_path)
    assert layout.lock_holder(install) is None

    _held(install, "abcd0123abcd0123", os.getpid())
    holder = layout.lock_holder(install)
    assert holder is not None
    assert (holder.nonce, holder.pid) == ("abcd0123abcd0123", os.getpid())
    assert holder.alive() is True

    dead = subprocess.Popen([sys.executable, "-c", "raise SystemExit(0)"])
    dead.wait(timeout=30)
    (layout.helper_lock(install) / "pid").write_text(str(dead.pid), encoding="utf-8")
    holder = layout.lock_holder(install)
    assert holder is not None and holder.alive() is False


def test_a_lock_with_no_owner_file_still_reads_as_held(tmp_path: Path) -> None:
    """Between `mkdir` and the write there is a moment, and a crash in it is a lock."""
    install = _folder(tmp_path)
    layout.helper_lock(install).mkdir(parents=True)
    holder = layout.lock_holder(install)
    assert holder is not None and holder.nonce == "" and holder.alive() is False


def test_only_the_attempt_that_took_the_lock_may_release_it(tmp_path: Path) -> None:
    """**The nonce is the whole safety of it** (round 5, M1).

    The app removes a lock after it has ended the helper it started, because
    dash runs no EXIT trap on SIGTERM and `TerminateProcess` runs nothing at
    all. A lock somebody else's helper holds is never touched.
    """
    install = _folder(tmp_path)
    _held(install, "abcd0123abcd0123", os.getpid())

    assert layout.release_lock_of(install, "ffff0000ffff0000") is False
    assert layout.helper_lock(install).is_dir(), "another attempt's lock was removed"
    assert layout.release_lock_of(install, "") is False
    assert layout.helper_lock(install).is_dir()

    assert layout.release_lock_of(install, "abcd0123abcd0123") is True
    assert layout.lock_holder(install) is None
    # And releasing what is already gone is not an error.
    assert layout.release_lock_of(install, "abcd0123abcd0123") is True


def test_two_spellings_of_one_file_are_one_file(tmp_path: Path) -> None:
    """**The kernel answers with the spelling the process was started with** (round 5, W2).

    On Windows that is an 8.3 short path, a `subst` drive or a junction; here
    it is a symlinked directory, which is the same question a `==` between two
    strings gets wrong — and getting it wrong reads as "that pid is not
    Yu'lon" and clears the other copy's staging directory.
    """
    real = tmp_path / "install"
    real.mkdir()
    exe = real / "yulon"
    exe.write_text("the build", encoding="utf-8")
    link = tmp_path / "by-another-name"
    link.symlink_to(real)

    assert link / "yulon" != exe, "the precondition: two spellings"
    assert layout.same_file(link / "yulon", exe) is True
    assert layout.same_file(exe, exe) is True
    assert layout.same_file(real / "nothing-here", exe) is False


def test_a_path_that_no_longer_exists_falls_back_to_its_spelling(tmp_path: Path) -> None:
    """`samefile` needs both files; when one is gone the spellings are all there is."""
    gone = tmp_path / "gone"
    assert layout.same_file(gone, gone) is True
    assert layout.same_file(gone, tmp_path / "other") is False


def test_a_lock_naming_nobody_is_given_a_moment_before_it_is_rubbish(tmp_path: Path) -> None:
    """**The `mkdir` → owner-write window is real** (round 7, S2).

    A helper takes the lock by creating the directory and then writes its nonce
    and pid into it. In between, the lock names nobody — and a lock naming
    nobody is exactly what a helper killed at that moment leaves behind. So
    "nobody is behind it" cannot be decided on emptiness alone: young ones are
    left alone (a live helper is a heartbeat away from writing its owner), old
    ones are rubbish. Both halves of that are asserted here, with the age set
    by `os.utime` rather than by waiting.
    """
    install = _folder(tmp_path)
    lock = layout.helper_lock(install)
    lock.mkdir(parents=True)
    assert layout.lock_holder(install) is not None, "the precondition: a lock naming nobody"

    young = time.time() - 5
    os.utime(lock, (young, young))
    assert layout.clear_stale_lock(install) is False, "a helper mid-mkdir lost its lock"
    assert lock.is_dir()

    old = time.time() - (layout.STALE_LOCK_SECONDS + 1)
    os.utime(lock, (old, old))
    assert layout.clear_stale_lock(install) is True
    assert not lock.exists(), "a lock nobody is behind was kept for ever"


def test_a_lock_stamped_in_the_future_is_not_treated_as_ancient(tmp_path: Path) -> None:
    """A clock that jumped, or a file copied off a box with a different one.

    A future mtime makes the age negative, which is younger than any bound —
    so it is kept, which is the safe side: the cost is one refused press, and
    the cost of the other side is deleting a live helper's lock.
    """
    install = _folder(tmp_path)
    lock = layout.helper_lock(install)
    lock.mkdir(parents=True)
    ahead = time.time() + 3600
    os.utime(lock, (ahead, ahead))

    assert layout.clear_stale_lock(install) is False
    assert lock.is_dir()


def test_a_lock_naming_a_pid_is_judged_by_that_pid_and_not_by_its_age(tmp_path: Path) -> None:
    """Age is the fallback for a lock with no owner, never the rule for one with a pid."""
    install = _folder(tmp_path)
    lock = layout.helper_lock(install)
    lock.mkdir(parents=True)
    (lock / "owner").write_text("abcd0123abcd0123", encoding="utf-8")
    (lock / "pid").write_text(str(os.getpid()), encoding="utf-8")
    ancient = time.time() - (layout.STALE_LOCK_SECONDS * 100)
    os.utime(lock, (ancient, ancient))

    assert layout.clear_stale_lock(install) is False, "a LIVE helper's lock was removed"
    assert lock.is_dir()


def test_waiting_for_a_lock_lets_the_caller_keep_the_window_alive(tmp_path: Path) -> None:
    """**A ten-second freeze is the crash this feature is trying not to be** (round 7, S1).

    `make_way` runs on the GUI thread when a press finds a helper of an earlier
    attempt still holding the lock. It polled on a bare `time.sleep`, so the
    window stopped repainting for the whole bound — measured 10.02 s. The tick
    is what the caller paints with; the bound is unchanged.
    """
    install = _folder(tmp_path)
    lock = layout.helper_lock(install)
    lock.mkdir(parents=True)
    (lock / "owner").write_text("abcd0123abcd0123", encoding="utf-8")
    (lock / "pid").write_text(str(os.getpid()), encoding="utf-8")  # alive, and staying
    ticks: list[int] = []

    started = time.monotonic()
    said = layout.make_way(install, seconds=0.4, tick=lambda: ticks.append(1))
    waited = time.monotonic() - started

    assert said is not None and "still working in this folder" in said
    assert ticks, "the caller was never given a chance to repaint"
    assert 0.3 <= waited < 4.0, f"the wait took {waited:.2f}s"
    assert layout.stand_down_path(install).read_text(encoding="utf-8").strip() == (
        "abcd0123abcd0123"
    )


def test_waiting_without_a_tick_is_allowed(tmp_path: Path) -> None:
    """The worker-thread caller (`stage.prepare`) passes none, and nothing breaks."""
    install = _folder(tmp_path)
    lock = layout.helper_lock(install)
    lock.mkdir(parents=True)
    (lock / "owner").write_text("abcd0123abcd0123", encoding="utf-8")
    (lock / "pid").write_text(str(os.getpid()), encoding="utf-8")

    assert layout.make_way(install, seconds=0.2) is not None
