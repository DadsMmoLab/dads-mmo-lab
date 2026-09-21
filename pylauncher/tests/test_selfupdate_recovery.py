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
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from yulon.selfupdate import layout
from yulon.selfupdate.cleanup import finish_previous_update, recovery_steps
from yulon.selfupdate.detect import Install, InstallKind
from yulon.selfupdate.stage import prepare
from yulon.selfupdate.swap import plan_swap

DEADLINE = 15.0
README = Path(__file__).resolve().parents[1] / "README.md"

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the real helper is POSIX only")

ENTRIES = ("yulon", "_internal", "extra.dat")
"""Three entries, so there are FIVE places between moves to be killed at."""

TWO = ("yulon", "_internal")
"""The shape every one-dir build really has, and the one the players run."""


def _put(where: Path, name: str, version: str, entries: tuple[str, ...]) -> None:
    """One entry, of the kind its name implies. The executable prints its version."""
    if name == "yulon":
        (where / name).write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
        (where / name).chmod(0o755)
    elif name == "_internal":
        (where / name).mkdir()
        (where / name / "lib").write_text(version, encoding="utf-8")
        (where / name / layout.SHIPPED_MANIFEST).write_text(
            "\n".join(entries) + "\n", encoding="utf-8"
        )
    else:
        (where / name).write_text(version, encoding="utf-8")


def _install(root: Path, version: str, entries: tuple[str, ...] = ENTRIES) -> Install:
    target = root / "app"
    target.mkdir(parents=True)
    for name in entries:
        _put(target, name, version, entries)
    return Install(InstallKind.TARBALL, target, "yulon", True)


def _stage_a_build(install: Install, version: str, entries: tuple[str, ...] = ENTRIES) -> Path:
    staged = prepare(install, version, pid=os.getpid())
    for name in entries:
        _put(staged, name, version, entries)
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


def _swap_killed_after(
    root: Path, install: Install, at: int, entries: tuple[str, ...] = ENTRIES
) -> None:
    """Run the real helper with a dead app pid, killing it after move `at`."""
    staged = layout.work_dir(install, layout.NEW_NAME)
    plan = plan_swap(
        install,
        staged,
        pid=os.getpid(),
        script_dir=root,
        entries=entries,
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


def _flat(text: str) -> str:
    """Wrapping, quoting, backticks and case removed, so prose and a message compare.

    The README sets the steps as a blockquote, so `>` at the start of a
    continuation line lands in the middle of a sentence; that is markdown, not
    a difference in the words.
    """
    lines = [line.strip().lstrip(">").strip() for line in text.splitlines()]
    return " ".join(" ".join(lines).replace("`", "").split()).lower()


def _step_one(target: Path, step: str) -> None:
    """Delete from the install everything that is also in the backup."""
    assert "also exists inside" in step and layout.OLD_NAME in step, step
    assert "delete" in step.lower(), step
    for item in sorted((target / layout.OLD_NAME).iterdir()):
        here = target / item.name
        if here.is_dir() and not here.is_symlink():
            shutil.rmtree(here)
        elif here.exists() or here.is_symlink():
            here.unlink()


def _step_two(target: Path, step: str) -> None:
    """Move the backup's contents in, **except the names the step itself lists**.

    The exception list is read OUT OF THE MESSAGE rather than out of `layout`:
    a step that forgot to name `helper.log` would then move it into the install
    folder here, where it stays for ever as a name the next update collides
    with. That is what "the app's bookkeeping" means to a player — whatever the
    sentence they were given happens to say.
    """
    assert layout.OLD_NAME in step and layout.NEW_NAME in step, step
    head, _, rest = step.partition("except ")
    assert head and rest, step
    kept = {name.strip() for name in rest.partition(" into the")[0].split(",")}
    assert kept == set(layout.BOOKKEEPING), f"the step names {sorted(kept)}"

    backup = target / layout.OLD_NAME
    for item in sorted(backup.iterdir()):
        if item.name not in kept:
            shutil.move(str(item), str(target / item.name))
    shutil.rmtree(backup)
    staged = target / layout.NEW_NAME
    if staged.exists():
        shutil.rmtree(staged)


def _step_three(target: Path, step: str) -> None:
    """Remove the names the NEW build brought and the old one has no use for."""
    assert step.startswith("Delete ") and " from the Yu'lon folder" in step, step
    named = step[len("Delete ") : step.index(" from the Yu'lon folder")]
    for name in named.split(", "):
        here = target / name.strip()
        assert layout.is_entry_name(name.strip()), name
        if here.is_dir() and not here.is_symlink():
            shutil.rmtree(here)
        elif here.exists() or here.is_symlink():
            here.unlink()


_EXECUTORS = {1: _step_one, 2: _step_two, 3: _step_three}
"""One implementation per step of `cleanup.recovery_steps()`, by position.

**The steps are data and this executes them** (round 4, M4). They used to be a
paragraph in the README, a second copy in this file that the test actually ran,
and a third, one-step version in the app's own message — and the app's was the
wrong one: following it at the last kill point before the executable arrives
put the old `_internal` INSIDE the new one and left a launcher that would not
start. A step this table has no entry for fails the test rather than being
skipped, which is the only way a new step cannot go unexecuted.
"""


def _follow_the_message(target: Path, steps: tuple[str, ...]) -> None:
    """Do what the player was told, in order, and nothing else."""
    assert steps, "the message gave no steps at all"
    for n, step in enumerate(steps, 1):
        assert n in _EXECUTORS, f"step {n} is one nothing here knows how to do: {step}"
        _EXECUTORS[n](target, step)


def _assert_the_old_build_is_back(
    target: Path, entries: tuple[str, ...] = ENTRIES, brought: tuple[str, ...] = ()
) -> None:
    """**Every entry, by content** — which is what the old one-file assertion missed.

    The one-step instruction left `_internal/_internal` nested inside the new
    `_internal` and the old `yulon` beside it, and a test that read only
    `extra.dat` called that a success (round 3, F3).
    """
    for name in entries:
        assert (target / name).exists(), f"{name} is missing"
    for name in brought:
        assert not (target / name).exists(), f"{name} is what the NEW version brought"
    assert "OLD" in (target / "yulon").read_text(encoding="utf-8")
    assert (target / "_internal" / "lib").read_text(encoding="utf-8") == "OLD"
    if "extra.dat" in entries:
        assert (target / "extra.dat").read_text(encoding="utf-8") == "OLD"
    assert not (
        target / "_internal" / "_internal"
    ).exists(), "a directory was moved INSIDE the one it was meant to replace"
    assert sorted(p.name for p in (target / "_internal").iterdir()) == sorted(
        ["lib", layout.SHIPPED_MANIFEST]
    ), "the old `_internal` is not the one that came back"


def _and_it_starts(target: Path) -> None:
    """**Run it.** Every assertion above is about files; this is about a launcher.

    A restore that leaves the right bytes in the wrong place still has to
    produce a program that runs — the nested-`_internal` failure was invisible
    to a test that only read files.
    """
    done = subprocess.run(
        [str(target / "yulon")], capture_output=True, text=True, timeout=DEADLINE, cwd=target
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "OLD", done.stdout


def test_the_readme_quotes_the_steps_the_app_itself_gives() -> None:
    """**One source, and the README has to match it word for word** (round 4, M4).

    The README and the app's message were written separately and drifted: the
    app was still giving a single step that the README had already replaced.
    Wrapping, backticks and case are removed before comparing, so the prose can
    be formatted as prose; the words cannot differ.
    """
    text = _flat(README.read_text(encoding="utf-8"))
    for step in recovery_steps():
        assert _flat(step) in text, f"the README no longer says: {step}"
    # And the third step, which only some builds get, by the rule it states.
    assert _flat("the new version brought them and the old one does not use them") in text


def _shapes() -> list[Any]:
    """Every kill point of a two-entry build, a three-entry one, and one that grows.

    The move count is not `2 × entries`: an entry the OLD build does not have
    is not moved out, only in. **Both sizes** because the executable leaves
    first and arrives last, so which entries are where at a given kill point
    depends on how many there are (round 4, M4).
    """
    cases = []
    for old, extra, name in (
        (TWO, (), "two"),
        (ENTRIES, (), "three"),
        (TWO, ("plugins.dat",), "two-and-a-new-entry"),
    ):
        moves = len(old) * 2 + len(extra)
        for at in range(1, moves):
            cases.append(pytest.param(old, extra, at, id=f"{name}-kill-{at}"))
    return cases


def _steps_in(message: str) -> tuple[str, ...]:
    """The numbered steps read back OUT of the message the player is shown.

    Not out of `cleanup`: what is under test is the sentence the app puts on
    its bar, so that is what gets executed.
    """
    _, _, tail = message.partition("To put the version you had back: ")
    assert tail, message
    tail = tail.partition(" (the installer's last message was:")[0]
    parts = re.split(r"(?:^|\s)(?=\d+\. )", tail.strip())
    steps = tuple(re.sub(r"^\d+\. ", "", part).strip() for part in parts if part.strip())
    assert steps, message
    return steps


@pytest.mark.parametrize(("old", "extra", "at"), _shapes())
def test_the_steps_the_app_gives_put_the_old_build_back(
    old: tuple[str, ...], extra: tuple[str, ...], at: int, tmp_path: Path
) -> None:
    """**The message is executed**, at every kill point, and the result is started.

    The old instruction failed twice here — it moved the good `_internal` into
    a `broken/` folder, and it moved `.yulon-marker` into the install folder
    and left an empty unmarked `.yulon-old` behind, which every later update
    then refused. It was also not the instruction the app was giving.
    """
    from yulon.selfupdate.cleanup import recovery_steps as from_source

    install = _install(tmp_path, "OLD", old)
    target = install.target
    assert target is not None
    entries = (*old, *extra)
    _stage_a_build(install, "NEW", entries)
    _swap_killed_after(tmp_path, install, at, entries)
    assert [n for n in old if not (target / n).exists()], "the precondition: something is gone"

    steps = _steps_in(finish_previous_update(install, running="OLD").problem)
    assert steps == from_source(extra), "the message is not the one source's steps"
    _follow_the_message(target, steps)

    _assert_the_old_build_is_back(target, old, extra)
    _and_it_starts(target)
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
def test_the_steps_still_work_for_a_player_who_started_and_stopped(
    at: int, restored: str | None, tmp_path: Path
) -> None:
    """Half-way through the instruction is a state too, and the steps must survive it.

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

    _follow_the_message(target, _steps_in(said))

    _assert_the_old_build_is_back(target)
    _and_it_starts(target)
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
