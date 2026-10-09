"""T633: older pre-update database copies are forgotten only after a press succeeds.

The owner's word of 2026-10-09, "Delete only after success". T630 found the A-press
on m910q: a "Return to the tested pin…" whose new build crash-looped was rolled
back, and once the old build came back the rollback forgot the four
`before-new-build` files the earlier "Update the server to latest…" had kept -- the
only copy of the databases from before that update, which is what a player needs to
go back.

So the older copies go only once the new build is up and its world reported ready
and the press ends well. A failed build, a world that never gets ready (rolled back
or kept), a Stop and a refusal each keep every copy; a full success still keeps only
the newest (owner, 2026-10-04). Every test drives the real `update_to_latest()` and
asserts the files on disk, through `FakeSnapshot.prune()`, which forgets for real.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tests.support_native import FakeSnapshot, Recorder
from tests.support_stop import compile_until_stopped, stop_when
from tests.test_update_rollback_database import WOTLK, WOTLK_COPY, _asked_ready, _press
from tests.test_update_to_latest import (  # noqa: F401 - `_gated` is an autouse fixture
    ABORTED_AFTER_READY,
    OLD,
    _gated,
    _spine,
)
from yulon.catalog import native, snapshot
from yulon.catalog.installer import InstallerError, InstallOptions, WorldStoppedAfterReadyError
from yulon.docker import AttachedRun

EARLIER = "20261008_120000"
"""The stamp of the copy an earlier, successful update kept: the one a way back needs."""


def _lay_earlier_copy(server_dir: Path) -> tuple[Path, ...]:
    folder = server_dir / snapshot.BACKUPS_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    laid = []
    for name in WOTLK_COPY:
        path = folder / f"{EARLIER}_{snapshot.SNAPSHOT_LABEL}_{name}.sql"
        path.write_text("-- Dump completed\n", encoding="utf-8")
        laid.append(path)
    return tuple(laid)


def _kept(laid: tuple[Path, ...]) -> bool:
    return all(path.is_file() for path in laid)


def _engine(tmp_path: Path, **overrides: object) -> tuple[Recorder, Path, native.StagedInstaller]:
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    made = make(**overrides)
    made._snapshot = FakeSnapshot(rec)
    return rec, server_dir, made


# -- success: only the newest stays ------------------------------------------------


def test_an_update_that_comes_up_forgets_the_earlier_copy(tmp_path: Path) -> None:
    rec, server_dir, made = _engine(tmp_path)
    laid = _lay_earlier_copy(server_dir)
    list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert not any(path.exists() for path in laid), "a full success keeps only the newest"
    assert rec.calls.count("prune") == 1


def test_a_return_that_comes_up_forgets_the_earlier_copy_too(tmp_path: Path) -> None:
    rec, server_dir, made = _engine(tmp_path)
    laid = _lay_earlier_copy(server_dir)
    list(made.update_to_latest(InstallOptions(server_dir=server_dir), to_pin=True))
    assert not any(path.exists() for path in laid)


# -- every other ending keeps every copy -------------------------------------------


def test_a_world_that_never_gets_ready_keeps_every_copy_when_the_old_build_comes_back(
    tmp_path: Path,
) -> None:
    """The A-press: rolled back, the old build reported ready -- and nothing is forgotten."""
    rec, server_dir, make = _spine(tmp_path, WOTLK)
    made = make(wait_ready=_asked_ready(rec, [False, True]))
    made._snapshot = FakeSnapshot(rec)
    laid = _lay_earlier_copy(server_dir)
    with pytest.raises(InstallerError) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir), to_pin=True))
    assert "is running again" in str(raised.value)
    assert "prune" not in rec.calls, rec.calls
    assert _kept(laid)


def test_a_new_build_that_stops_after_ready_keeps_every_copy(tmp_path: Path) -> None:
    """T71's kept build: it came up, then its world stopped on its data. Not a success."""
    rec, server_dir, _made, fake, _said, raised = _press(
        tmp_path, WOTLK, world_output=lambda spec: ABORTED_AFTER_READY
    )
    assert isinstance(raised, WorldStoppedAfterReadyError), raised
    assert "prune" not in rec.calls, rec.calls


def test_a_failed_build_keeps_every_copy(tmp_path: Path) -> None:
    rec, server_dir, made = _engine(tmp_path)
    rec.build_result = AttachedRun(1, ("error: no member named 'IsBot'",))
    laid = _lay_earlier_copy(server_dir)
    with pytest.raises(InstallerError):
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert "prune" not in rec.calls and _kept(laid)


def test_a_stop_during_the_build_keeps_every_copy(qapp: object, tmp_path: Path) -> None:
    compiling = threading.Event()
    rec, server_dir, made = _engine(tmp_path, build=compile_until_stopped(compiling))
    laid = _lay_earlier_copy(server_dir)
    options = InstallOptions(server_dir=server_dir)
    panel, _finished = stop_when(
        lambda cancel: made.update_to_latest(options, cancel=cancel), compiling, "compiling"
    )
    assert panel.cancelled is True
    assert "prune" not in rec.calls and _kept(laid)


def test_a_refused_press_keeps_every_copy(tmp_path: Path) -> None:
    """A refusal before the build (here: a source carrying the player's own edits)."""
    rec, server_dir, made = _engine(tmp_path)
    rec.edits[server_dir / "modules/mod-playerbots"] = ("src/mine.cpp",)
    laid = _lay_earlier_copy(server_dir)
    with pytest.raises(InstallerError):
        list(made.update_to_latest(InstallOptions(server_dir=server_dir), to_pin=True))
    assert "prune" not in rec.calls and _kept(laid)
    assert set(rec.heads[server_dir / s.dest] for s in WOTLK.emulator.sources) == {OLD}


def test_a_return_refused_after_the_move_keeps_every_copy(tmp_path: Path) -> None:
    """T630's refusal: the sources moved and went back; the earlier copy is what it names."""
    from tests.test_return_newer_updates import _newer_bots_update

    rec, server_dir, made = _engine(tmp_path)
    _newer_bots_update(rec, server_dir)
    laid = _lay_earlier_copy(server_dir)
    with pytest.raises(InstallerError) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir), to_pin=True))
    assert "Database updates only go forward" in str(raised.value)
    assert "prune" not in rec.calls and _kept(laid)
