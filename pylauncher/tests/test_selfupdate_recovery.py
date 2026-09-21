"""Putting a half-finished update right, by the app and by hand (T90 plan 3).

Two things are proved here, at every point the helper can be killed:

1. **the app repairs it** when the app can still start — `finish_previous_update`
   moves back every entry that is missing from the install and present in the
   backup;
2. **the written instruction works** when it cannot — the one case left to the
   player is an install with no executable, and the prose in
   `pylauncher/README.md` is EXECUTED here rather than read.

The kill points come from the helper's own move sequence, produced with a `mv`
that stops the script part-way (the technique the second cold review used). The
old README failed two of them: it told the player to move the program and
`_internal` aside, which on a swap killed after move 1 moved away the one good
`_internal`; and "move everything out of `.yulon-old`" moved the marker into
the install folder and left an empty, unmarked backup that every later update
refused.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from yulon.selfupdate import layout
from yulon.selfupdate.cleanup import finish_previous_update
from yulon.selfupdate.detect import Install, InstallKind
from yulon.selfupdate.stage import prepare
from yulon.selfupdate.swap import plan_swap

DEADLINE = 15.0
README = Path(__file__).resolve().parents[1] / "README.md"

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the real helper is POSIX only")

ENTRIES = ("yulon", "_internal", "extra.dat")
"""Three entries, so there are FIVE places between moves to be killed at."""


def _install(root: Path, version: str) -> Install:
    target = root / "app"
    (target / "_internal").mkdir(parents=True)
    (target / "_internal" / "lib").write_text(version, encoding="utf-8")
    (target / "_internal" / layout.SHIPPED_MANIFEST).write_text(
        "\n".join(ENTRIES) + "\n", encoding="utf-8"
    )
    (target / "yulon").write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
    (target / "yulon").chmod(0o755)
    (target / "extra.dat").write_text(version, encoding="utf-8")
    return Install(InstallKind.TARBALL, target, "yulon", True)


def _stage_a_build(install: Install, version: str) -> Path:
    staged = prepare(install, version, pid=os.getpid())
    (staged / "_internal").mkdir()
    (staged / "_internal" / "lib").write_text(version, encoding="utf-8")
    (staged / "_internal" / layout.SHIPPED_MANIFEST).write_text(
        "\n".join(ENTRIES) + "\n", encoding="utf-8"
    )
    (staged / "yulon").write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
    (staged / "yulon").chmod(0o755)
    (staged / "extra.dat").write_text(version, encoding="utf-8")
    layout.discard_ours(install, layout.DOWNLOAD_NAME)
    return staged


def _killing_mv(root: Path, at: int) -> Path:
    """A `PATH` whose `mv` kills the helper after its `at`-th successful move."""
    bindir = root / f"bin{at}"
    bindir.mkdir()
    (bindir / "mv").write_text(
        "#!/bin/sh\n"
        'n=$(cat "$MVCOUNT" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$MVCOUNT"\n'
        '/bin/mv "$@"; rc=$?\n'
        'if [ "$n" -eq "$KILLAT" ]; then kill -9 $PPID; fi\n'
        "exit $rc\n",
        encoding="utf-8",
    )
    (bindir / "mv").chmod(0o755)
    return bindir


def _swap_killed_after(root: Path, install: Install, at: int) -> None:
    """Run the real helper with a dead app pid, killing it after move `at`."""
    staged = layout.work_dir(install, layout.NEW_NAME)
    plan = plan_swap(
        install,
        staged,
        pid=os.getpid(),
        script_dir=root,
        entries=ENTRIES,
        platform_id="linux",
    )
    dead = subprocess.Popen(["sleep", "0"])
    dead.wait(timeout=DEADLINE)
    argv = list(plan.argv)
    argv[3] = str(dead.pid)
    counter = root / f"count{at}"
    env = {
        **os.environ,
        "PATH": f"{_killing_mv(root, at)}{os.pathsep}{os.environ['PATH']}",
        "MVCOUNT": str(counter),
        "KILLAT": str(at),
    }
    helper = subprocess.Popen(argv, stdin=subprocess.DEVNULL, env=env)
    try:
        helper.wait(timeout=DEADLINE)
    finally:
        if helper.poll() is None:  # pragma: no cover - only on a failing run
            helper.kill()
            helper.wait(timeout=DEADLINE)


KILL_POINTS = [1, 2, 3, 4, 5]
"""Three entries out and three in is six moves; a kill after each of the first five."""


# -- the app does it ---------------------------------------------------------


@pytest.mark.parametrize("at", KILL_POINTS)
def test_the_app_puts_a_half_finished_update_right_by_itself(at: int, tmp_path: Path) -> None:
    """Every entry missing from the install and present in the backup is moved back.

    Whatever the helper had managed, the install ends at a version that is
    whole: either the old one (put back here) or the new one (the swap really
    finished). Nothing is left half of each.
    """
    install = _install(tmp_path, "OLD")
    target = install.target
    assert target is not None
    _stage_a_build(install, "NEW")
    _swap_killed_after(tmp_path, install, at)

    missing = [name for name in ENTRIES if not (target / name).exists()]
    assert missing or at >= 6, f"kill point {at} did not interrupt anything"

    outcome = finish_previous_update(install, running="OLD")

    for name in ENTRIES:
        assert (target / name).exists(), f"{name} is still missing after the repair"
    assert "did not finish" in outcome.problem or "could not be installed" in outcome.problem
    for name in layout.WORK_NAMES:
        assert not layout.work_dir(install, name).exists(), f"{name} was left behind"
    # And the install works again as a starting point for the next attempt.
    assert prepare(install, "NEWER", pid=os.getpid()).exists()


@pytest.mark.parametrize("at", KILL_POINTS)
def test_a_repaired_install_holds_one_whole_version(at: int, tmp_path: Path) -> None:
    """Not a mixture: every entry comes from the same build."""
    install = _install(tmp_path, "OLD")
    target = install.target
    assert target is not None
    _stage_a_build(install, "NEW")
    _swap_killed_after(tmp_path, install, at)
    finish_previous_update(install, running="OLD")

    assert (target / "extra.dat").read_text(encoding="utf-8") == "OLD"
    assert (target / "_internal" / "lib").read_text(encoding="utf-8") == "OLD"
    assert "OLD" in (target / "yulon").read_text(encoding="utf-8")


# -- the written instruction, executed ---------------------------------------

RECOVERY_STEPS = """\
move the files from `.yulon-old` back into this folder, except `.yulon-marker`;
then delete `.yulon-old`
"""
"""The instruction this test EXECUTES, kept beside the script that does it.

**Prose and script can drift**, and this file cannot stop that: what it can do
is keep them one screen apart and assert that the README still contains these
words, so a change to one is visible next to the other.
"""


def _follow_the_readme(target: Path) -> None:
    """Exactly what the instruction says, and nothing a player would not do.

    1. move the files from `.yulon-old` back into this folder, EXCEPT the marker
    2. delete `.yulon-old`
    """
    backup = target / layout.OLD_NAME
    for item in sorted(backup.iterdir()):
        if item.name == layout.MARKER_NAME:
            continue
        shutil.move(str(item), str(target / item.name))
    shutil.rmtree(backup)


def test_the_readme_still_says_what_this_test_executes() -> None:
    """The one thing that can catch a drift between the prose and the script above."""
    text = README.read_text(encoding="utf-8")
    assert layout.OLD_NAME in text and layout.MARKER_NAME in text
    for phrase in ("back into this folder", "except", "delete"):
        assert phrase in text, f"the README no longer says {phrase!r}"
    assert re.search(r"hidden", text, re.I), "the README does not say the folders are hidden"


@pytest.mark.parametrize("at", KILL_POINTS)
def test_following_the_readme_by_hand_restores_the_old_build(at: int, tmp_path: Path) -> None:
    """The case the app cannot reach: no executable, so nothing of ours can run.

    The old instruction failed twice here — it moved the good `_internal` into
    a `broken/` folder, and it moved `.yulon-marker` into the install folder
    and left an empty unmarked `.yulon-old` behind, which every later update
    then refused.
    """
    install = _install(tmp_path, "OLD")
    target = install.target
    assert target is not None
    _stage_a_build(install, "NEW")
    _swap_killed_after(tmp_path, install, at)

    _follow_the_readme(target)

    for name in ENTRIES:
        assert (target / name).exists(), f"{name} is missing after following the README"
    assert (target / "extra.dat").read_text(encoding="utf-8") == "OLD"
    assert not (target / layout.MARKER_NAME).exists(), "the marker was moved into the install"
    assert not (target / layout.OLD_NAME).exists()
    # And a later update is not refused by what the player left behind.
    assert prepare(install, "NEWER", pid=os.getpid()).exists()
