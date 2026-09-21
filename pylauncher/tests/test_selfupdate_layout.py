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


def test_a_staging_directory_a_live_process_made_is_not(tmp_path: Path) -> None:
    """One instance's stage() deleting another's staged build is how an install was lost."""
    install = _folder(tmp_path)
    alive = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        layout.write_marker(install, layout.NEW_NAME, _marker(pid=alive.pid))
        said = layout.another_copy_is_updating(install, os.getpid())
        assert said is not None and "already installing" in said
    finally:
        alive.kill()
        alive.wait(timeout=30)
    assert alive.poll() is not None


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
    import shutil

    exe = tmp_path / "yulon"
    shutil.copy2(sys.executable, exe)
    other = subprocess.Popen([str(exe), "-c", "import time; time.sleep(30)"])
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
