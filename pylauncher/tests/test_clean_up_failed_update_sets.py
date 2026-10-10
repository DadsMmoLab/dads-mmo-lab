"""T646: Clean up and the update copies that pile up after failed updates (T633 + T604).

Since T633 a failed update keeps its copy set, so after one success and two failures the
folder holds three `before-new-build` sets. The newest-set rule kept only the last, which holds
the first update's migrations: "Return to the tested pin" (T630/T632) wants a dump from BEFORE
them and found none. The refusal names no recorded file -- it reads each dump's ledger table
(`snapshot.copy_from_before()`) -- so Clean up keeps the newest set per distinct ledger state.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests import test_backup_shelf as base
from yulon import backup_shelf
from yulon.backup_shelf import Rule

BANNER, GAME, NOW = base.BANNER, base.GAME, base.NOW
folder_of, row, shelf = base.folder_of, base.row, base.shelf
server = base.server  # the fixture, re-exported so this file's tests receive it
DB = "acore_world"


def _ledger_dump(updates: tuple[str, ...], table: str = "updates") -> bytes:
    rows = ",".join(
        f"('{name}','RELEASED','2026-10-0{n} 10:00:00',{n})" for n, name in enumerate(updates, 1)
    )
    return (
        f"-- yulon-backup: game={GAME}\n".encode()
        + BANNER
        + f"USE `{DB}`;\nCREATE TABLE `{table}` (`name` varchar(200));\n".encode()
        + f"INSERT INTO `{table}` VALUES {rows};\n".encode()
        + b"INSERT INTO `creature` VALUES (1,'Hogger');\n"
        + b"-- Dump completed on 2026-10-09 12:00:00\n"
    )


def _put(
    server: Path,
    stamp: str,
    updates: tuple[str, ...],
    label: str = "before-new-build",
    table: str = "updates",
) -> Path:
    middle = f"{label}_" if label else ""
    path = folder_of(server) / f"{stamp}_{middle}{DB}.sql"
    path.write_bytes(_ledger_dump(updates, table))
    return path


def _swept(server: Path) -> set[str]:
    found = shelf(server)
    return set(backup_shelf.plan_clean_up(found, Rule(keep_newest=1), now=NOW).names)


@pytest.mark.parametrize("table", ["updates", "migrations"])
def test_clean_up_keeps_the_copy_from_before_the_update_a_return_would_need(
    server: Path, table: str
) -> None:
    before_first = _put(server, "20261001_100000", ("u1",), table=table)
    after_first_a = _put(server, "20261005_100000", ("u1", "u2"), table=table)
    after_first_b = _put(server, "20261006_100000", ("u1", "u2"), table=table)
    swept = _swept(server)
    assert before_first.name not in swept, "a return past u2 needs the dump from before it"
    assert after_first_b.name not in swept, "the newest set"
    assert after_first_a.name in swept, "same ledger as the newest: an older copy of one state"
    reason = row(shelf(server), before_first.name).kept_because
    assert reason and "before" in reason


def test_clean_up_removes_older_sets_that_hold_the_same_migrations(server: Path) -> None:
    older = _put(server, "20261001_100000", ("u1",))
    newer = _put(server, "20261005_100000", ("u1",))
    swept = _swept(server)
    assert older.name in swept and newer.name not in swept


def test_a_changed_timestamp_in_the_ledger_is_not_a_new_state(server: Path) -> None:
    a = _put(server, "20261001_100000", ("u1", "u2"))
    b = folder_of(server) / "20261005_100000_before-new-build_acore_world.sql"
    b.write_bytes(_ledger_dump(("u1", "u2")).replace(b"2026-10-01", b"2026-11-09"))
    assert a.name in _swept(server)


def test_a_dump_whose_ledger_cannot_be_read_is_never_swept(
    server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _put(server, "20261001_100000", ("u1",))
    _put(server, "20261005_100000", ("u1", "u2"))
    _put(server, "20261006_100000", ("u1", "u2"))
    real = backup_shelf._ledger_state

    def refuse(path: Path) -> frozenset[bytes]:
        if path.name == old.name:
            raise PermissionError("denied")
        return real(path)

    monkeypatch.setattr(backup_shelf, "_ledger_state", refuse)
    assert old.name not in _swept(server)
    assert "could not read which updates" in str(row(shelf(server), old.name).kept_because)


def test_the_player_cannot_lose_the_copy_through_the_single_delete_either_unasked(
    server: Path,
) -> None:
    before_first = _put(server, "20261001_100000", ("u1",))
    _put(server, "20261005_100000", ("u1", "u2"))
    r = row(shelf(server), before_first.name)
    assert r.kept_because is not None
    assert os.path.exists(before_first)
