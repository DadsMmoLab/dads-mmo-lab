"""T646: Clean up and the update copies that pile up after failed updates (T633 + T604).

Since T633 a failed update keeps its copy set, so after one success and two failures the
folder holds three `before-new-build` sets. The newest-set rule kept only the last, which holds
the first update's migrations: "Return to the tested pin" (T630/T632) wants a dump from BEFORE
them and found none. The refusal names no recorded file -- it reads each dump's ledger table
(`snapshot.copy_from_before()`) -- so Clean up keeps the newest set per distinct ledger state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import test_backup_shelf as base
from yulon import backup_shelf
from yulon.backup_shelf import Rule

BANNER, GAME, NOW = base.BANNER, base.GAME, base.NOW
folder_of, row, shelf = base.folder_of, base.row, base.shelf
server = base.server  # the fixture, re-exported so this file's tests receive it
DB = "acore_world"


def _ledger_dump(
    updates: tuple[str, ...],
    table: str = "updates",
    *,
    game: str = GAME,
    applied: str = "2026-10-01",
) -> bytes:
    rows = ",".join(
        f"('{name}','RELEASED','{applied} 10:0{n}:00',{n})" for n, name in enumerate(updates, 1)
    )
    return (
        f"-- yulon-backup: game={game}\n".encode()
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
    *,
    game: str = GAME,
    applied: str = "2026-10-01",
) -> Path:
    middle = f"{label}_" if label else ""
    path = folder_of(server) / f"{stamp}_{middle}{DB}.sql"
    path.write_bytes(_ledger_dump(updates, table, game=game, applied=applied))
    return path


@pytest.fixture(autouse=True)
def _fresh_ledger_cache() -> None:
    backup_shelf._LEDGER_CACHE.clear()


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


@pytest.mark.parametrize("table", ["updates", "migrations"])
def test_a_different_applied_time_in_the_ledger_is_not_a_new_state(
    server: Path, table: str
) -> None:
    """Rolling back loads a copy back, but a retry's rows may carry other times: same updates."""
    a = _put(server, "20261001_100000", ("u1", "u2"), table=table, applied="2026-10-01")
    b = _put(server, "20261003_100000", ("u1", "u2"), table=table, applied="2026-10-02")
    c = _put(server, "20261005_100000", ("u1", "u2"), table=table, applied="2026-11-09")
    assert _swept(server) == {a.name, b.name}
    assert row(shelf(server), a.name).kept_because is None
    assert row(shelf(server), c.name).kept_because


def test_another_games_update_copy_does_not_shadow_this_games(server: Path) -> None:
    """WotLK and Unbound share schema names: the other game's newest copy is not this game's."""
    before = _put(server, "20261001_100000", ("u1",))
    _put(server, "20261005_100000", ("u1", "u2"))
    _put(server, "20261006_100000", ("u1",), game="wow-unbound")
    assert before.name not in _swept(server)
    assert row(shelf(server), before.name).kept_because


def test_a_dump_is_read_once_while_the_file_is_unchanged(
    server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _put(server, "20261001_100000", ("u1",))
    _put(server, "20261005_100000", ("u1", "u2"))
    reads: list[bytes] = []
    real = backup_shelf._QUOTED

    class Counting:
        def findall(self, line: bytes) -> list[bytes]:
            reads.append(line)
            return real.findall(line)  # type: ignore[no-any-return]

    monkeypatch.setattr(backup_shelf, "_QUOTED", Counting())
    shelf(server)
    first = len(reads)
    assert first == 2
    shelf(server)
    shelf(server)
    assert len(reads) == first, "a refresh of an unchanged folder must not read the dumps again"


def test_a_dump_that_changed_is_read_again(server: Path) -> None:
    old = _put(server, "20261001_100000", ("u1",))
    newest = _put(server, "20261005_100000", ("u1", "u2"))
    assert row(shelf(server), old.name).kept_because
    old.write_bytes(_ledger_dump(("u1", "u2")) + b"-- more\n")  # now the same state
    assert row(shelf(server), old.name).kept_because is None
    assert row(shelf(server), newest.name).kept_because


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
    found = row(shelf(server), old.name)
    assert "could not read which updates" in str(found.kept_because)
    assert found.cannot_delete is None, "a deliberate single Delete is still allowed"


def test_a_single_delete_of_an_older_distinct_copy_is_allowed_but_clean_up_never_takes_it(
    server: Path,
) -> None:
    before_first = _put(server, "20261001_100000", ("u1",))
    _put(server, "20261005_100000", ("u1", "u2"))
    found = shelf(server)
    r = row(found, before_first.name)
    assert r.kept_because is not None
    assert r.cannot_delete is None
    plan = backup_shelf.plan_delete(found, before_first.name)
    assert plan.names == (before_first.name,)
    assert before_first.name not in _swept(server)
