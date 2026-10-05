"""T219: the Windows world server's map data is copied into a volume before it starts.

On Windows the server folder's `data/` reaches a container over Docker Desktop's 9p
share, about 12 ms per file opened (yulon-win11, 2026-10-04), and the world server
opens thousands of terrain files while it makes its first bots: the first rebalance
took 58.9 s warm over the share and 1.81 s from a volume. So on Windows the world
server reads `data/` from the `world-data` volume, and its entrypoint copies into the
volume whatever folder changed since the last copy, then `exec`s the server.

These tests run the RENDERED script (compose's `$$` unescaped, exactly what the
container's `sh` gets) against temporary folders, with only its two paths pointed
at them -- the precedent is `test_db_outlives_world.py`. The image's tools are GNU
coreutils (`cp` 9.4, measured in the image), so the tests need a Linux `sh` and `cp`.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from tests import support_rendered
from yulon import resources
from yulon.catalog import composegen
from yulon.catalog.catalog import load_catalog

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("sh") is None,
    reason="the script runs in a Linux container with GNU coreutils",
)

CORE_DIR = "/opt/trinitycore"
FINGERPRINT = ".yulon-world-data"
WORLD_DIRS = ("dbc", "maps", "vmaps", "mmaps", "Cameras")


def rendered_script() -> str:
    """The shell text the Windows world container runs, `$$` unescaped as compose does."""
    plan = composegen.render(
        load_catalog().get("wow-centurion"),
        support_rendered.linux_server_dir("wow-centurion"),
        templates_root=resources.installers_dir(),
        db_password="t219-password-for-tests",
        platform_id=lambda: "windows",
    )
    world = yaml.safe_load(plan.base)["services"]["centurion-worldserver"]
    assert world["entrypoint"][:2] == ["sh", "-c"]
    return world["entrypoint"][2].replace("$$", "$")


def pointed_at(src: Path, vol: Path) -> str:
    """The script with its two folders moved to `src` and `vol`; nothing else changed."""
    script = rendered_script()
    for name, path in (("src", f"{CORE_DIR}/data-src"), ("vol", f"{CORE_DIR}/data")):
        line = f"{name}={path}\n"
        assert script.count(line) == 1, f"the script must set {name} exactly once: {line!r}"
        script = script.replace(line, f"{name}={src if name == 'src' else vol}\n")
    return script


class Box:
    """A server folder's `data/` (src) and an empty volume (vol), with tools on PATH."""

    def __init__(self, tmp_path: Path) -> None:
        self.src = tmp_path / "data"
        self.vol = tmp_path / "volume"
        self.bin = tmp_path / "bin"
        self.calls = tmp_path / "cp-calls.txt"
        self.src.mkdir()
        self.vol.mkdir()
        self.bin.mkdir()
        self.script = pointed_at(self.src, self.vol)
        files = {
            "dbc/Spell.dbc": b"spell",
            "maps/0000000.map": b"map zero",
            "maps/0013238.map": b"map one",
            "vmaps/000.vmtree": b"tree",
            "vmaps/nested/deeper/000_32_32.vmtile": b"tile",
            "mmaps/000.mmap": b"navmesh",
            "Cameras/FlyBy.m2": b"camera",
            "Buildings/dir_bin": b"an extractor intermediate",
            ".yulon-previous/maps/0000000.map": b"the map data a re-extract moved aside",
        }
        for name, body in files.items():
            path = self.src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        self.spy_cp()

    def spy_cp(self, slow_in: str | None = None) -> None:
        """`cp` on PATH records every call (its folder and arguments), then runs the real one.

        `slow_in`: in that source folder the shim marks that it started and then sleeps
        instead of copying, so a test can signal the script mid-copy.
        """
        real = shutil.which("cp")
        assert real is not None
        slow = (
            f'case "$PWD" in */{slow_in}) : > "{self.calls}.slow"; exec sleep 30;; esac\n'
            if slow_in
            else ""
        )
        shim = self.bin / "cp"
        shim.write_text(
            f'#!/bin/sh\necho "$PWD $*" >> "{self.calls}"\n{slow}exec {real} "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)

    def fingerprint(self, **lines: str) -> None:
        """The server folder's fingerprint: one `<folder> <hash>` line each, in WORLD_DIRS order."""
        values = {name: f"{name}-v1" for name in WORLD_DIRS} | lines
        (self.src / FINGERPRINT).write_text(
            "".join(f"{name} {values[name]}\n" for name in WORLD_DIRS), encoding="utf-8"
        )

    def env(self) -> dict[str, str]:
        return {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"}

    def run(self, *argv: str) -> subprocess.CompletedProcess[str]:
        self.calls.unlink(missing_ok=True)
        return subprocess.run(
            ["sh", "-c", self.script, "yulon-world", *(argv or ("true",))],
            capture_output=True,
            text=True,
            env=self.env(),
            timeout=60,
        )

    def copied_from(self) -> set[str]:
        """The source folders `cp` was run in, since the last `run()`."""
        if not self.calls.exists():
            return set()
        return {
            Path(line.split(" ", 1)[0]).name
            for line in self.calls.read_text(encoding="utf-8").splitlines()
        }

    def volume_lines(self) -> list[str]:
        path = self.vol / FINGERPRINT
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def test_the_first_start_copies_every_world_folder_and_then_its_fingerprint(box: Box) -> None:
    box.fingerprint()
    done = box.run()
    assert done.returncode == 0, done.stderr
    for name in (
        "dbc/Spell.dbc",
        "maps/0000000.map",
        "maps/0013238.map",
        "vmaps/000.vmtree",
        "vmaps/nested/deeper/000_32_32.vmtile",
        "mmaps/000.mmap",
        "Cameras/FlyBy.m2",
    ):
        assert (box.vol / name).read_bytes() == (box.src / name).read_bytes(), name
    assert (box.vol / FINGERPRINT).read_bytes() == (box.src / FINGERPRINT).read_bytes()
    assert "yulon: copied" in done.stdout


def test_what_the_world_server_never_opens_is_not_copied(box: Box) -> None:
    """`Buildings` is the extractor's intermediate (no DataDir path names it, Task 0), and
    `.yulon-previous` is where a re-extract moves the old map data aside (T241)."""
    box.fingerprint()
    assert box.run().returncode == 0
    assert not (box.vol / "Buildings").exists()
    assert not (box.vol / ".yulon-previous").exists()
    assert sorted(path.name for path in box.vol.iterdir()) == sorted([*WORLD_DIRS, FINGERPRINT])


def test_a_start_with_nothing_changed_copies_nothing(box: Box) -> None:
    box.fingerprint()
    assert box.run().returncode == 0
    before = {path: path.stat().st_mtime_ns for path in box.vol.rglob("*")}
    again = box.run()
    assert again.returncode == 0, again.stderr
    assert box.copied_from() == set(), "cp ran on a start whose fingerprint had not moved"
    assert {path: path.stat().st_mtime_ns for path in box.vol.rglob("*")} == before
    assert "yulon: copying" not in again.stdout


def test_a_changed_folder_is_the_only_one_copied_again(box: Box) -> None:
    box.fingerprint()
    assert box.run().returncode == 0
    (box.src / "maps/0000000.map").write_bytes(b"map zero, extracted again")
    box.fingerprint(maps="maps-v2")
    again = box.run()
    assert again.returncode == 0, again.stderr
    assert box.copied_from() == {"maps"}
    assert (box.vol / "maps/0000000.map").read_bytes() == b"map zero, extracted again"
    assert sorted(box.volume_lines()) == sorted(
        (box.src / FINGERPRINT).read_text(encoding="utf-8").splitlines()
    ), "the volume's lines are the server folder's, the re-copied one written last"


def test_a_folder_removed_from_the_server_folder_is_removed_from_the_volume(box: Box) -> None:
    """A re-copy replaces the folder whole: a file gone from `data/` is gone from the volume."""
    box.fingerprint()
    assert box.run().returncode == 0
    (box.src / "maps/0013238.map").unlink()
    box.fingerprint(maps="maps-v2")
    assert box.run().returncode == 0
    assert sorted(path.name for path in (box.vol / "maps").iterdir()) == ["0000000.map"]


def test_unfinished_pathfinding_leaves_the_volumes_mmaps_empty(box: Box) -> None:
    """`-` is Yu'lon's word for "this folder is not for the server yet": an empty folder."""
    box.fingerprint()
    assert box.run().returncode == 0
    assert (box.vol / "mmaps/000.mmap").exists()
    box.fingerprint(mmaps="-")
    again = box.run()
    assert again.returncode == 0, again.stderr
    assert (box.vol / "mmaps").is_dir()
    assert list((box.vol / "mmaps").iterdir()) == []
    assert box.copied_from() == set()
    assert "mmaps -" in box.volume_lines()


def test_a_stop_during_a_copy_ends_at_once_and_the_next_start_copies_that_folder_again(
    box: Box, tmp_path: Path
) -> None:
    """A shell as PID 1 ignores SIGTERM unless it traps it; without the trap a Stop during a
    copy would wait out the 5-minute grace. The folder being copied keeps no fingerprint
    line, so the next start copies it again, and the half-made copy is cleared first."""
    box.fingerprint()
    box.spy_cp(slow_in="maps")
    started = subprocess.Popen(
        ["sh", "-c", box.script, "yulon-world", "true"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=box.env(),
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 20
        while not Path(f"{box.calls}.slow").exists():
            assert time.monotonic() < deadline, "the copy of maps never started"
            time.sleep(0.05)
        signalled = time.monotonic()
        started.send_signal(signal.SIGTERM)
        started.wait(timeout=5)
        assert time.monotonic() - signalled < 2.0
        # 143 is the trap's own exit: a shell without the trap dies OF the signal (-15 here),
        # and as a container's PID 1 it would not die at all -- it would ignore it.
        assert started.returncode == 143
    finally:
        # The shim's `sleep` outlives the script, as it would outlive PID 1 only until the
        # container ends; its own session makes it this test's to end.
        try:
            os.killpg(started.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    assert started.stdout is not None
    said = started.stdout.read()
    assert "yulon: stopped" in said
    lines = box.volume_lines()
    assert "dbc dbc-v1" in lines, "the folder finished before the stop keeps its line"
    assert not any(line.startswith("maps ") for line in lines)
    assert (box.vol / "maps.yulon-new").exists(), "the stop left its half-made copy"
    box.spy_cp()
    again = box.run()
    assert again.returncode == 0, again.stderr
    assert not (box.vol / "maps.yulon-new").exists()
    assert (box.vol / "maps/0013238.map").read_bytes() == b"map one"
    assert "dbc" not in box.copied_from(), "dbc's line survived, so it is current"
    assert (box.vol / FINGERPRINT).read_bytes() == (box.src / FINGERPRINT).read_bytes()


def test_a_server_folder_without_a_fingerprint_is_copied_whole_and_said_once(box: Box) -> None:
    """A start Yu'lon did not make, before Yu'lon ever wrote one: copy everything, loudly,
    and record nothing -- there is nothing to compare the next start with."""
    done = box.run()
    assert done.returncode == 0, done.stderr
    assert done.stdout.count("yulon: no fingerprint") == 1
    assert box.copied_from() == {"dbc", "maps", "vmaps", "mmaps", "Cameras"}
    assert (box.vol / "maps/0000000.map").exists()
    assert box.volume_lines() == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only folder")
def test_a_copy_that_fails_stops_the_container_with_a_reason(box: Box) -> None:
    box.fingerprint()
    box.vol.chmod(0o555)
    try:
        done = box.run("echo", "the server started")
    finally:
        box.vol.chmod(0o755)
    assert done.returncode != 0
    assert "yulon: could not copy" in done.stdout + done.stderr
    assert "the server started" not in done.stdout


def test_the_server_replaces_the_script_as_the_containers_first_process(
    box: Box, tmp_path: Path
) -> None:
    """`exec`: the server must be PID 1 so a Stop's SIGTERM reaches it, as before T219."""
    box.fingerprint()
    pid = tmp_path / "print-pid"
    pid.write_text('#!/bin/sh\necho "pid=$$ args=$*"\n', encoding="utf-8")
    pid.chmod(0o755)
    started = subprocess.Popen(
        ["sh", "-c", box.script, "yulon-world", str(pid), "-c", "worldserver.conf"],
        stdout=subprocess.PIPE,
        text=True,
        env=box.env(),
    )
    out, _ = started.communicate(timeout=60)
    assert started.returncode == 0
    assert f"pid={started.pid} args=-c worldserver.conf" in out


def test_the_fingerprint_yulon_writes_is_the_one_the_copy_reads(tmp_path: Path) -> None:
    """End to end over the two halves: `world_data.refresh()` writes the server folder's file
    on a Windows install, the rendered script copies by it, and a second start with nothing
    changed copies nothing -- then a changed map file moves `maps` alone."""
    from yulon.catalog import world_data

    entry = load_catalog().get("wow-centurion")
    server_dir = tmp_path / "server"
    plan = composegen.render(
        entry,
        server_dir,
        templates_root=resources.installers_dir(),
        db_password="t219-password-for-tests",
        platform_id=lambda: "windows",
    )
    server_dir.mkdir()
    (server_dir / composegen.BASE_FILE).write_text(plan.base, encoding="utf-8")
    box = Box(tmp_path)
    assert box.src == server_dir.parent / "data"
    box.src.rename(server_dir / "data")
    box.src = server_dir / "data"
    box.script = pointed_at(box.src, box.vol)

    assert world_data.refresh(entry, server_dir) is None
    first = box.run()
    assert first.returncode == 0, first.stderr
    assert "yulon: no fingerprint" not in first.stdout
    assert box.copied_from() == {"dbc", "maps", "vmaps", "Cameras"}, "mmaps is not done"
    assert list((box.vol / "mmaps").iterdir()) == []

    assert world_data.refresh(entry, server_dir) is None
    assert box.run().returncode == 0
    assert box.copied_from() == set()

    (box.src / "maps" / "0013238.map").write_bytes(b"map one, extracted again")
    assert world_data.refresh(entry, server_dir) is None
    assert box.run().returncode == 0
    assert box.copied_from() == {"maps"}
    assert (box.vol / "maps" / "0013238.map").read_bytes() == b"map one, extracted again"
