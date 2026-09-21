"""The whole update, composed from the REAL functions, on a fake install (T90 plan 3).

**This file exists because every other test in this package fakes the
neighbour it depends on** (cold review 2). Each unit test is honest about one
function and blind to the joins between them, and the defect that got through
lived in exactly such a join: the downloaded archive was written into the
staging directory, so `staged_entries()` returned it as one of the build's own
entries and the helper moved a 90 MB tarball into the player's folder as part
of the program. No unit test could see it, because each one built the staging
directory itself.

So here `apply_update`, `prepare`, `stage`, `entries_to_swap`, `plan_swap`,
the **real POSIX helper** and `finish_previous_update` all run for real,
against a tar.gz this file builds and a fake install in `tmp_path`.

The ONE thing faked is the network: the opener hands back a local file. Every
byte after that is the real path.

Nothing is left running: every process is waited for, and every wait is against
a deadline.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tarfile
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from yulon.selfupdate import layout
from yulon.selfupdate.apply import ApplyIO, ReadyToRestart, apply_update
from yulon.selfupdate.cleanup import finish_previous_update
from yulon.selfupdate.detect import Install, InstallKind
from yulon.selfupdate.fetch import UpdateError
from yulon.update import CHECKSUMS_NAME, ReleaseAsset, UpdateCheck

DEADLINE = 15.0
"""A deadlock breaker. Measured: the whole compose-and-swap run takes ~0.8 s here."""

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the real helper is POSIX only")


def _build_tree(root: Path, version: str, *, extra: tuple[str, ...] = ()) -> None:
    """What a real one-dir build looks like: the executable and `_internal`, and no more.

    Measured against the published `v0.8.712-fixtest`: the tar.gz's `yulon/`
    holds exactly `_internal` and `yulon`.
    """
    root.mkdir(parents=True)
    (root / "yulon").write_text(f'#!/bin/sh\nprintf %s "{version}" >> "$YULON_WITNESS"\n', "utf-8")
    (root / "yulon").chmod(0o755)
    internal = root / "_internal"
    internal.mkdir()
    (internal / "base_library.zip").write_text(version, encoding="utf-8")
    (internal / layout.SHIPPED_MANIFEST).write_text(
        "\n".join(["_internal", "yulon", *extra]) + "\n", encoding="utf-8"
    )
    for name in extra:
        (root / name).write_text(f"shipped {version}", encoding="utf-8")


def _archive(path: Path, version: str, *, extra: tuple[str, ...] = ()) -> Path:
    """A real `.tar.gz` with the real shape: one top-level `yulon/`."""
    source = path.parent / f"src-{path.name}"
    _build_tree(source / "yulon", version, extra=extra)
    with tarfile.open(path, "w:gz") as archive:
        archive.add(source / "yulon", arcname="yulon")
    return path


def _install(root: Path, version: str, *, extra: tuple[str, ...] = ()) -> Install:
    target = root / "app"
    _build_tree(target, version, extra=extra)
    return Install(InstallKind.TARBALL, target, "yulon", True)


def _a_release(tag: str, size: int) -> UpdateCheck:
    name = f"Yulon-{tag}-x86_64.tar.gz"
    return UpdateCheck(
        current="0.8.66-Public",
        latest=tag,
        available=True,
        url=f"https://github.com/DadsMmoLab/dads-mmo-lab/releases/tag/{tag}",
        assets=(
            ReleaseAsset(name, f"https://example.invalid/{name}", size),
            ReleaseAsset(CHECKSUMS_NAME, "https://example.invalid/sums", 100),
        ),
        has_checksums=True,
    )


def _network(archive: Path, name: str) -> tuple[Callable[[str], str], Callable[..., Path]]:
    """The ONE seam: a `SHA256SUMS` and a download, both served from a local file."""
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    def fetch_text(url: str) -> str:
        assert url.endswith(f"/{CHECKSUMS_NAME}"), url
        return f"{digest}  {name}\n"

    def download(url: str, dest: Path, **kwargs: object) -> Path:
        assert url.endswith(f"/{name}"), url
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(archive.read_bytes())
        return dest

    return fetch_text, download


def _io(archive: Path, name: str, scripts: Path) -> ApplyIO:
    fetch_text, download = _network(archive, name)
    return ApplyIO(fetch_text=fetch_text, download=download, script_dir=lambda: scripts)


def _run(
    install: Install,
    archive: Path,
    tag: str,
    scripts: Path,
    *,
    smoke: Callable[[Path], None] | None = None,
) -> ReadyToRestart:
    """`apply_update` with only the network faked, and the real smoke test replaced.

    The smoke test is the one other thing that cannot run for real here: it
    starts the staged BINARY, and a fake tree's `yulon` is a shell script
    rather than a frozen app. Replaced by something that asserts it was handed
    the staged executable and that the executable is really there.
    """
    name = f"Yulon-{tag}-x86_64.tar.gz"
    seen: list[Path] = []

    def smoke_test(exe: Path) -> None:
        seen.append(exe)
        assert exe.is_file(), f"the smoke test was handed {exe}, which does not exist"
        if smoke is not None:
            smoke(exe)

    io = _io(archive, name, scripts)
    outcome = apply_update(
        _a_release(tag, archive.stat().st_size),
        install,
        downloads_dir=scripts,
        pid=os.getpid(),
        progress=lambda _d, _t: None,
        stage_changed=lambda _s: None,
        cancelled=lambda: False,
        io=ApplyIO(
            fetch_text=io.fetch_text,
            download=io.download,
            smoke_test=smoke_test,
            script_dir=io.script_dir,
        ),
    )
    assert isinstance(outcome, ReadyToRestart)
    assert seen, "the staged build was never smoke-tested"
    return outcome


def _swap(plan: object, witness: Path) -> None:
    """Arm the plan as the app does, run the REAL helper, and wait for the relaunch.

    **`arm()` is called here and nowhere else in this file**, because since
    round 5 that is the only place a helper script is written: `plan_swap`
    returns a description, and arming it at the moment of use is what stopped
    every update leaving an orphaned script in the temp directory.
    """
    from yulon.selfupdate.swap import arm

    armed = arm(plan)  # type: ignore[arg-type]
    dead = subprocess.Popen(["sleep", "0"])
    dead.wait(timeout=DEADLINE)
    argv = list(armed.argv)
    argv[3] = str(dead.pid)
    env = {**os.environ, "YULON_WITNESS": str(witness)}
    helper = subprocess.Popen(argv, stdin=subprocess.DEVNULL, env=env)
    try:
        assert helper.wait(timeout=DEADLINE) == 0, "the helper refused"
    finally:
        if helper.poll() is None:  # pragma: no cover - only on a failing run
            helper.kill()
            helper.wait(timeout=DEADLINE)
    assert not armed.script.exists(), "the helper left its script behind"


def _until(condition: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + DEADLINE
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError(f"{what} did not happen within {DEADLINE}s")


def _listing(target: Path) -> set[str]:
    return {p.name for p in target.iterdir()}


# -- the whole thing ---------------------------------------------------------


def test_one_update_end_to_end_leaves_the_build_the_players_files_and_nothing_else(
    tmp_path: Path,
) -> None:
    """**The test that was missing.** Real functions all the way down.

    What is asserted afterwards is the WHOLE listing of the install folder: the
    new build's entries, the player's own files, and nothing else. The bug this
    would have caught left a 90 MB `Yulon-9.9.9-x86_64.tar.gz` in there for
    ever.
    """
    install = _install(tmp_path, "OLD")
    target = install.target
    assert target is not None
    (target / "notes.txt").write_text("the player's notes", encoding="utf-8")
    (target / "saves").mkdir()
    (target / "saves" / "slot1").write_bytes(b"savegame")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    witness = tmp_path / "witness"
    archive = _archive(tmp_path / "Yulon-9.9.9-x86_64.tar.gz", "NEW")

    ready = _run(install, archive, "9.9.9", scripts)

    assert ready.plan.entries == ("yulon", "_internal")
    assert not any(
        e.endswith(".tar.gz") for e in ready.plan.entries
    ), "the downloaded archive was handed to the helper as part of the build"
    assert not layout.work_dir(
        install, layout.DOWNLOAD_NAME
    ).exists(), "the archive was left beside the install"

    _swap(ready.plan, witness)
    _until(lambda: witness.exists(), "the new build was relaunched")

    assert witness.read_text(encoding="utf-8") == "NEW", "the OLD build was relaunched"
    assert (target / "_internal" / "base_library.zip").read_text(encoding="utf-8") == "NEW"
    assert _listing(target) == {
        "yulon",
        "_internal",
        "notes.txt",
        "saves",
        layout.NEW_NAME,
        layout.OLD_NAME,
    }

    outcome = finish_previous_update(install, running="9.9.9")

    assert outcome.removed is True and outcome.problem == ""
    assert _listing(target) == {
        "yulon",
        "_internal",
        "notes.txt",
        "saves",
    }, "the install folder holds something that is neither the build nor the player's"
    assert (target / "notes.txt").read_text(encoding="utf-8") == "the player's notes"
    assert (target / "saves" / "slot1").read_bytes() == b"savegame"


def test_a_second_update_from_there_works(tmp_path: Path) -> None:
    """The state one update leaves has to be a state the next one can start from."""
    install = _install(tmp_path, "OLD")
    target = install.target
    assert target is not None
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    witness = tmp_path / "witness"

    first = _run(install, _archive(tmp_path / "a-9.9.9.tar.gz", "MIDDLE"), "9.9.9", scripts)
    _swap(first.plan, witness)
    _until(lambda: witness.exists(), "the middle build was relaunched")
    assert finish_previous_update(install, running="9.9.9").removed is True

    witness.unlink()
    second = _run(install, _archive(tmp_path / "b-9.9.10.tar.gz", "NEWEST"), "9.9.10", scripts)
    _swap(second.plan, witness)
    _until(lambda: witness.exists(), "the newest build was relaunched")
    assert finish_previous_update(install, running="9.9.10").removed is True

    assert witness.read_text(encoding="utf-8") == "NEWEST"
    assert _listing(target) == {"yulon", "_internal"}


def test_the_players_own_copy_of_that_release_is_not_mistaken_for_the_update(
    tmp_path: Path,
) -> None:
    """A player who downloaded the same release by hand into the Yu'lon folder.

    With the archive downloaded into the staging directory this was a refusal
    about the player's own file ("Yu'lon did not put that there"), because the
    staged "entry" and their file had the same name.
    """
    install = _install(tmp_path, "OLD")
    target = install.target
    assert target is not None
    theirs = target / "Yulon-9.9.9-x86_64.tar.gz"
    theirs.write_bytes(b"the player downloaded this themselves")
    scripts = tmp_path / "scripts"
    scripts.mkdir()

    ready = _run(install, _archive(tmp_path / "src.tar.gz", "NEW"), "9.9.9", scripts)

    assert ready.plan.entries == ("yulon", "_internal")
    assert theirs.read_bytes() == b"the player downloaded this themselves"


def test_a_build_that_ships_a_third_entry_installs_it(tmp_path: Path) -> None:
    """The manifest's whole purpose, through the real path."""
    install = _install(tmp_path, "OLD", extra=("LICENSE.txt",))
    target = install.target
    assert target is not None
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    witness = tmp_path / "witness"
    archive = _archive(tmp_path / "x.tar.gz", "NEW", extra=("LICENSE.txt",))

    ready = _run(install, archive, "9.9.9", scripts)
    assert set(ready.plan.entries) == {"yulon", "_internal", "LICENSE.txt"}
    assert ready.plan.entries[0] == "yulon", "the executable leaves first and arrives last"

    _swap(ready.plan, witness)
    _until(lambda: witness.exists(), "the new build was relaunched")
    assert (target / "LICENSE.txt").read_text(encoding="utf-8") == "shipped NEW"
    finish_previous_update(install, running="9.9.9")
    assert _listing(target) == {"yulon", "_internal", "LICENSE.txt"}


def test_a_smoke_test_that_refuses_leaves_the_install_and_the_folder_exactly_as_they_were(
    tmp_path: Path,
) -> None:
    """The refusal contract, through the real staging rather than a fake one."""
    install = _install(tmp_path, "OLD")
    target = install.target
    assert target is not None
    (target / "notes.txt").write_text("the player's notes", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    before = {p.name: p.stat().st_mtime_ns for p in target.iterdir()}

    def refuse(_exe: Path) -> None:
        raise UpdateError("The new version did not start properly.")

    with pytest.raises(UpdateError, match="did not start properly"):
        _run(install, _archive(tmp_path / "x.tar.gz", "NEW"), "9.9.9", scripts, smoke=refuse)

    assert {p.name: p.stat().st_mtime_ns for p in target.iterdir()} == before
    for name in layout.WORK_NAMES:
        assert not layout.work_dir(install, name).exists(), f"{name} was left behind"
