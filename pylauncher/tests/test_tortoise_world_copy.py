"""T643: Tortoise's update copies tw_world too, before the new build first starts.

T217 copied only tw_logon and tw_char on Tortoise (the owner's word of 2026-10-04:
"world is the biggest and the slowest to copy"), so a world migration the new
build's AutoUpdater applied could never be undone: a failed update's rollback
started the old build on a migrated tw_world, and T632's "Return to the tested
pin…" refusal ended in "found no copy of tw_world".

Measured on yulon-ubuntu's Tortoise install (2026-10-10, ticket T643): tw_world
dumps to 144 MB in 4.1 s (tw_char: 94 MB in 5.6 s), and restoring it is about the
install's one-minute base import. So the copy now holds all three. Every test
drives the real `update_to_latest()` (or the app's confirmation through
`install_wiring`) on an installed Tortoise folder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import FakeSnapshot, Recorder
from tests.test_update_rollback_database import (
    TORTOISE,
    _at,
    _old_build_comes_back,
    _press,
)
from tests.test_update_to_latest import (  # noqa: F401 - `_gated` is an autouse fixture
    _gated,
    _spine,
)
from yulon import install_wiring
from yulon.catalog import native, snapshot
from yulon.catalog.installer import InstallerError, InstallOptions

TORTOISE_COPY = ("tw_logon", "tw_char", "tw_world")
"""What a Tortoise update copies: the three databases its AutoUpdater migrates at start."""

EARLIER = "20261008_120000"


def test_a_tortoise_update_copies_its_world_database_with_the_servers_stopped(
    tmp_path: Path,
) -> None:
    rec, _dir, _made, fake, said, raised = _press(tmp_path, TORTOISE)
    assert raised is None, raised
    assert [copy.databases for copy in fake.taken] == [TORTOISE_COPY]
    # Taken with the servers down, before the new build's first start.
    stopped = _at(rec.calls, "stop_servers")
    copied = _at(rec.calls, f"snapshot:{','.join(TORTOISE_COPY)}", stopped)
    _at(rec.calls, "recreate", copied)
    assert native.copy_opening_line(TORTOISE_COPY) in said
    assert any("tw_world" in line and "before starting the new build" in line for line in said)


def test_a_rolled_back_tortoise_update_puts_its_world_database_back_too(tmp_path: Path) -> None:
    rec, _dir, _made, fake, _said, raised = _press(
        tmp_path, TORTOISE, wait_ready=_old_build_comes_back()
    )
    assert raised is not None
    calls = rec.calls
    start_new = _at(calls, "recreate", _at(calls, f"snapshot:{','.join(TORTOISE_COPY)}"))
    copy_back = _at(calls, f"put-back:{','.join(TORTOISE_COPY)}", start_new)
    _at(calls, "recreate", copy_back)
    assert fake.put_back_calls == fake.taken
    text = str(raised)
    assert "tw_logon, tw_char and tw_world were replaced with the copy" in text, text
    assert "not copied" not in text and "is NOT put back" not in text, text


def test_the_tortoise_question_names_its_world_database_among_the_copies(
    tmp_path: Path,
) -> None:
    """The app's own confirmation, asked of the family through `install_wiring`."""
    route = install_wiring.update_to_latest_for_app(TORTOISE, tmp_path)
    assert route is not None
    text = route.confirmation()
    assert "puts tw_logon, tw_char and tw_world back" in text, text
    assert "not copied" not in text, text
    assert "some hundreds of MB" in text and "only the newest" in text


def _engine(tmp_path: Path, **overrides: object) -> tuple[Recorder, Path, native.StagedInstaller]:
    rec, server_dir, make = _spine(tmp_path, TORTOISE)
    made = make(**overrides)
    made._snapshot = FakeSnapshot(rec)
    return rec, server_dir, made


def _lay_earlier_world_copy(server_dir: Path) -> Path:
    folder = server_dir / snapshot.BACKUPS_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    earlier = folder / f"{EARLIER}_{snapshot.SNAPSHOT_LABEL}_tw_world.sql"
    earlier.write_text("-- Dump completed\n", encoding="utf-8")
    return earlier


def test_a_rolled_back_tortoise_update_keeps_the_earlier_world_copy(tmp_path: Path) -> None:
    """T633 holds for the world copy: a rollback forgets nothing."""
    _rec, server_dir, made = _engine(tmp_path, wait_ready=_old_build_comes_back())
    earlier = _lay_earlier_world_copy(server_dir)
    with pytest.raises(InstallerError):
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert earlier.is_file()


def test_a_tortoise_update_that_comes_up_forgets_the_earlier_world_copy(tmp_path: Path) -> None:
    rec, server_dir, made = _engine(tmp_path)
    earlier = _lay_earlier_world_copy(server_dir)
    list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert not earlier.exists(), "a full success keeps only the newest copy, world included"
    assert rec.calls.count("prune") == 1
