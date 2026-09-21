"""Putting a half-finished update right, by the app and by hand (T90 plan 3).

Two things are proved here, at every point the helper can be killed:

1. **the app changes nothing** — `finish_previous_update` reports a half-done
   swap and leaves the whole tree, working directories included, byte for byte
   as it found it. The version that repaired in place was removed on the lead's
   decision (2026-09-21): it renamed the `_internal` the live process was
   running out of, it had no undo of its own, and it was reachable only in the
   two hand-made states this file also builds;
2. **the written instruction works** — the prose in `pylauncher/README.md` is
   EXECUTED here rather than read, at every kill point, and it is the same
   sentence the app now puts on the bar.

The kill points come from the helper's own move sequence, produced with a `mv`
that stops the script part-way (the technique the second cold review used). The
old README failed two of them: it told the player to move the program and
`_internal` aside, which on a swap killed after move 1 moved away the one good
`_internal`; and "move everything out of `.yulon-old`" moved the marker into
the install folder and left an empty, unmarked backup that every later update
refused.
"""

from __future__ import annotations

import hashlib
import os
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


# -- the written instruction, executed ---------------------------------------

RECOVERY_STEPS = """\
1. In the Yu'lon folder, delete (or move away) every file and folder that ALSO
   exists inside `.yulon-old`.
2. Move everything in `.yulon-old` except `.yulon-marker` into the Yu'lon
   folder, then delete `.yulon-old` and `.yulon-new`.
"""
"""The instruction this test EXECUTES, kept beside the script that does it.

**Two steps, and the first one is not optional** (round 3, F3). The one-step
version — "move everything out of `.yulon-old` back in" — is wrong at the last
kill point before the executable arrives: the new `_internal` is already in
place, and moving a directory onto an existing directory of the same name puts
it INSIDE, so the player ended with the old `yulon` beside the NEW `_internal`
and a nested `_internal/_internal`. The test could not see it, because it read
one file.

**Prose and script can drift**, and this file cannot stop that: what it can do
is keep them one screen apart and assert that the README still contains these
words, so a change to one is visible next to the other.
"""


def _follow_the_readme(target: Path) -> None:
    """Exactly what `RECOVERY_STEPS` says, and nothing a player would not do.

    1. delete from the Yu'lon folder everything that ALSO exists in `.yulon-old`
    2. move everything in `.yulon-old` except the marker into the Yu'lon folder,
       then delete `.yulon-old` and `.yulon-new`
    """
    backup = target / layout.OLD_NAME
    theirs = [p for p in sorted(backup.iterdir()) if p.name != layout.MARKER_NAME]

    for item in theirs:  # step 1
        here = target / item.name
        if here.is_dir() and not here.is_symlink():
            shutil.rmtree(here)
        elif here.exists():
            here.unlink()

    for item in theirs:  # step 2
        shutil.move(str(item), str(target / item.name))
    shutil.rmtree(backup)
    staged = target / layout.NEW_NAME
    if staged.exists():
        shutil.rmtree(staged)


def _assert_the_old_build_is_back(target: Path) -> None:
    """**Every entry, by content** — which is what the old one-file assertion missed.

    The one-step instruction left `_internal/_internal` nested inside the new
    `_internal` and the old `yulon` beside it, and a test that read only
    `extra.dat` called that a success (round 3, F3).
    """
    for name in ENTRIES:
        assert (target / name).exists(), f"{name} is missing"
    assert (target / "extra.dat").read_text(encoding="utf-8") == "OLD"
    assert "OLD" in (target / "yulon").read_text(encoding="utf-8")
    assert (target / "_internal" / "lib").read_text(encoding="utf-8") == "OLD"
    assert not (
        target / "_internal" / "_internal"
    ).exists(), "a directory was moved INSIDE the one it was meant to replace"
    assert sorted(p.name for p in (target / "_internal").iterdir()) == sorted(
        ["lib", layout.SHIPPED_MANIFEST]
    ), "the old `_internal` is not the one that came back"


def test_the_readme_still_says_what_this_test_executes() -> None:
    """The one thing that can catch a drift between the prose and the script above."""
    text = README.read_text(encoding="utf-8")
    assert layout.OLD_NAME in text and layout.MARKER_NAME in text and layout.NEW_NAME in text
    for phrase in ("also exists inside", "except", "delete"):
        assert phrase.lower() in text.lower(), f"the README no longer says {phrase!r}"
    # Step 1 is the half that was missing, so it is named rather than implied.
    assert "delete (or move away)" in text


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

    _assert_the_old_build_is_back(target)
    assert not (target / layout.MARKER_NAME).exists(), "the marker was moved into the install"
    assert not (target / layout.OLD_NAME).exists()
    assert not (target / layout.NEW_NAME).exists()
    # And a later update is not refused by what the player left behind.
    assert prepare(install, "NEWER", pid=os.getpid()).exists()


# -- the startup pass touches NOTHING when a swap is half done ---------------


def _whole_tree(target: Path) -> dict[str, str]:
    """Every path and byte under the install, **including the working directories**.

    The work dirs are in here on purpose. This is the assertion that
    `finish_previous_update` moved nothing, and a hash that skipped
    `.yulon-old` would not see an entry taken OUT of it — which is exactly what
    the removed in-process repair did.
    """
    found: dict[str, str] = {}
    for path in sorted(target.rglob("*")):
        rel = str(path.relative_to(target))
        if path.is_symlink():
            found[rel] = "L:" + os.readlink(path)
        elif path.is_file():
            found[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            found[rel] = "D"
    return found


def _partly_restored_by_hand(install: Install, only: str) -> None:
    """What a player who started following the README and stopped leaves behind.

    The one state the removed in-process repair was ever reachable in: an
    executable is back, so the app can start, and the other entries are not.
    """
    target = install.target
    assert target is not None
    backup = layout.work_dir(install, layout.OLD_NAME)
    if (backup / only).exists():
        shutil.move(str(backup / only), str(target / only))


HALF_DONE_STATES = [
    *[pytest.param(at, None, id=f"kill-after-move-{at}") for at in KILL_POINTS],
    pytest.param(2, "yulon", id="player-moved-the-executable-back"),
    pytest.param(3, "_internal", id="player-moved-internal-back"),
]
"""Every state in which the app can find a half-finished update.

The five kill points, plus the two a player produces by starting the README
instruction and stopping. **The second pair is the only state in which the app
can even start with entries still missing**, because the helper takes the
executable out first and puts it back last — which is why the in-process repair
this file used to test was reachable almost never, and is why it is gone.

The kill points for those two are chosen so that something is still missing
after the player's one move: after two moves `yulon` and `_internal` are both
in the backup, so restoring `yulon` leaves `_internal` gone; after three,
restoring `_internal` leaves `yulon` and `extra.dat` gone.
"""


@pytest.mark.parametrize(("at", "restored"), HALF_DONE_STATES)
def test_the_startup_pass_changes_nothing_at_all_when_a_swap_is_half_done(
    at: int, restored: str | None, tmp_path: Path
) -> None:
    """**The app never moves a build entry, and this is how that is enforced.**

    Behavioural rather than a grep: the WHOLE target tree is hashed — the two
    working directories included — `finish_previous_update` runs, and the hash
    has to be identical. A move of any kind, in either direction, changes it.

    The version that did repair in place ran inside the live process and
    renamed the `_internal` that process was executing out of, had no undo of
    its own (an `OSError` on the second entry left the first already swapped),
    and was reachable only in the two hand-made states below. It was removed on
    the lead's decision; what is left says what is where.
    """
    install = _install(tmp_path, "OLD")
    target = install.target
    assert target is not None
    _stage_a_build(install, "NEW")
    _swap_killed_after(tmp_path, install, at)
    if restored is not None:
        _partly_restored_by_hand(install, restored)
    assert [n for n in ENTRIES if not (target / n).exists()], "the precondition: something is gone"

    before = _whole_tree(target)
    outcome = finish_previous_update(install, running="OLD")

    assert _whole_tree(target) == before, "the startup pass moved or removed something"
    assert outcome.removed is False
    assert "did not finish" in outcome.problem
    assert "Nothing has been changed." in outcome.problem
    assert layout.OLD_NAME in outcome.problem and layout.MARKER_NAME in outcome.problem


@pytest.mark.parametrize(("at", "restored"), HALF_DONE_STATES)
def test_the_instruction_the_half_done_message_gives_is_the_one_that_works(
    at: int, restored: str | None, tmp_path: Path
) -> None:
    """The message is the README's instruction, and the instruction is executed here.

    So the sentence a player reads at start is pinned to the procedure that
    really puts their install back, in every state they can be in.
    """
    install = _install(tmp_path, "OLD")
    target = install.target
    assert target is not None
    _stage_a_build(install, "NEW")
    _swap_killed_after(tmp_path, install, at)
    if restored is not None:
        _partly_restored_by_hand(install, restored)

    said = finish_previous_update(install, running="OLD").problem
    assert layout.OLD_NAME in said

    _follow_the_readme(target)

    _assert_the_old_build_is_back(target)
    assert not (target / layout.MARKER_NAME).exists()
    assert prepare(install, "NEWER", pid=os.getpid()).exists()


def test_a_rollback_removes_only_marked_folders_and_moves_nothing(tmp_path: Path) -> None:
    """The other report path, held to the same rule.

    Everything is in place and the running build is the old one, so there is
    nothing to move — and this asserts that nothing is moved: the install's own
    entries are byte-identical afterwards and only the marked directories go.
    """
    install = _install(tmp_path, "OLD")
    target = install.target
    assert target is not None
    _stage_a_build(install, "NEW")
    # The helper's rollback arm leaves the entries in place and the backup empty
    # but for its marker.
    layout.write_marker(
        install,
        layout.OLD_NAME,
        layout.Marker(
            role=layout.OLD_NAME,
            from_version="OLD",
            to_version="NEW",
            pid=os.getpid(),
            stamp=layout.now(),
            entries=ENTRIES,
            state=layout.SWAPPING,
        ),
    )
    entries_before = {name: (target / name).stat().st_mtime_ns for name in ENTRIES}
    bytes_before = (target / "extra.dat").read_bytes()

    outcome = finish_previous_update(install, running="OLD")

    assert "could not be installed" in outcome.problem
    assert {name: (target / name).stat().st_mtime_ns for name in ENTRIES} == entries_before
    assert (target / "extra.dat").read_bytes() == bytes_before
    for name in layout.WORK_NAMES:
        assert not layout.work_dir(install, name).exists()


def test_the_completed_swap_path_removes_only_marked_folders(tmp_path: Path) -> None:
    """And the happy path: the build stays exactly as the helper left it."""
    install = _install(tmp_path, "NEW")
    target = install.target
    assert target is not None
    backup = layout.work_dir(install, layout.OLD_NAME)
    backup.mkdir()
    for name in ENTRIES:
        (backup / name).write_text("the previous build", encoding="utf-8")
    layout.write_marker(
        install,
        layout.OLD_NAME,
        layout.Marker(
            role=layout.OLD_NAME,
            from_version="OLD",
            to_version="v9.9.9",
            pid=os.getpid(),
            stamp=layout.now(),
            entries=ENTRIES,
            state=layout.SWAPPING,
        ),
    )
    build_before = {name: (target / name).stat().st_mtime_ns for name in ENTRIES}

    outcome = finish_previous_update(install, running="9.9.9")

    assert outcome.removed is True
    assert {name: (target / name).stat().st_mtime_ns for name in ENTRIES} == build_before
    for name in layout.WORK_NAMES:
        assert not layout.work_dir(install, name).exists()
