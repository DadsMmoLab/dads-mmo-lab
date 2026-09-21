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


@pytest.mark.parametrize("name", ["yulon", "yulon.exe", "_internal", "a b", "a&b", "100%"])
def test_an_ordinary_name_is_an_entry(name: str) -> None:
    assert layout.is_entry_name(name) is True


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
    (install.target / ".yulon-old").mkdir()
    assert layout.read_marker(install, layout.OLD_NAME) is None
    assert layout.is_ours(install, layout.OLD_NAME) is False
    assert layout.discard_ours(install, layout.OLD_NAME) is False
    assert (install.target / ".yulon-old").exists(), "an unmarked folder was deleted"


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
    assert layout.read_marker(install, layout.OLD_NAME) is None
    assert layout.discard_ours(install, layout.OLD_NAME) is False
    assert old.exists()


def test_a_marker_with_a_byte_order_mark_still_reads(tmp_path: Path) -> None:
    """Someone opens it in Notepad to see what it is. That must not break an update."""
    install = _folder(tmp_path)
    assert install.target is not None
    old = install.target / ".yulon-old"
    old.mkdir()
    (old / layout.MARKER_NAME).write_bytes(
        b"\xef\xbb\xbf" + _marker(role=layout.OLD_NAME).as_json().encode("utf-8")
    )
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


@pytest.mark.parametrize("name", ["_internal", "..", "thesis.docx", ".yulon-marker"])
def test_nothing_but_the_two_working_directories_can_even_be_asked_for(name: str) -> None:
    """`discard_ours` is the only delete in the package, and it takes two names."""
    install = Install(InstallKind.TARBALL, Path("/opt/app"), "yulon", True)
    with pytest.raises(ValueError, match="working director"):
        layout.discard_ours(install, name)


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
