"""T604: delete or clean up old backups from within Yu'lon.

`backup_shelf` lists the backups folder, says what protects each file, plans a
delete or a clean-up with the identity of every file it names, and only then acts:
it re-reads the folder under the maintenance lease and refuses everything if
anything changed. Each protection below has a test that names the file it keeps,
and a mutation in the commit log that proves the test fails without it.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import replace as dataclasses_replace
from datetime import datetime
from pathlib import Path

import pytest

from yulon import backup_shelf, docker
from yulon.backup_shelf import Plan, Rule, ShelfRefusal
from yulon.controller_wow_wotlk import maintenance

GAME = "wow-wotlk"
BANNER = b"-- MySQL dump 10.13  Distrib 8.0.36, for Linux (x86_64)\n--\n"
NOW = datetime(2026, 10, 9, 12, 0, 0)
NONE_INSTALLED: dict[str, frozenset[str]] = {}


def dump_of(database: str, *, game: str | None = GAME, whole: bool = True, pad: int = 0) -> bytes:
    preamble = f"-- yulon-backup: game={game}\n".encode() if game else b""
    body = preamble + BANNER + f"USE `{database}`;\nINSERT INTO `t` VALUES (1);\n".encode()
    body += (b"-- " + b"x" * pad + b"\n") if pad else b""
    return body + (b"-- Dump completed on 2026-10-09 12:00:00\n" if whole else b"")


@pytest.fixture
def server(tmp_path: Path) -> Path:
    (tmp_path / "sql_scripts" / "backups").mkdir(parents=True)
    return tmp_path


def folder_of(server: Path) -> Path:
    return server / "sql_scripts" / "backups"


def put(
    server: Path,
    stamp: str,
    database: str = "acore_world",
    *,
    label: str | None = None,
    game: str | None = GAME,
    whole: bool = True,
    pad: int = 0,
) -> Path:
    middle = f"{label}_" if label else ""
    path = folder_of(server) / f"{stamp}_{middle}{database}.sql"
    path.write_bytes(dump_of(database, game=game, whole=whole, pad=pad))
    return path


def shelf(
    server: Path, *, installed: dict[str, frozenset[str]] | None = None, game: str | None = GAME
) -> backup_shelf.Shelf:
    return backup_shelf.read_shelf(
        server, game_id=game, installed=NONE_INSTALLED if installed is None else installed
    )


def row(found: backup_shelf.Shelf, name: str) -> backup_shelf.ShelfRow:
    (match,) = [r for r in found.rows if r.name == name]
    return match


# ------------------------------------------------------------------ reading


def test_the_shelf_lists_only_top_level_backup_files(server: Path) -> None:
    keep = put(server, "20261001_100000")
    (folder_of(server) / "restore-in-progress.json").write_text("{}")
    (folder_of(server) / "notes.txt").write_text("hello")
    (folder_of(server) / "nested").mkdir()
    (folder_of(server) / "nested" / "20261001_100000_acore_world.sql").write_bytes(b"x")
    (folder_of(server) / "20261002_100000_acore_world.sql.partial").write_bytes(b"half")
    (folder_of(server) / "dump.sql.gz").write_bytes(b"\x1f\x8b")
    names = {r.name for r in shelf(server).rows}
    assert names == {keep.name, "20261002_100000_acore_world.sql.partial", "dump.sql.gz"}


def test_a_sql_file_the_player_put_there_is_listed_for_restore_but_never_deleted(
    server: Path,
) -> None:
    mine = folder_of(server) / "my-characters.sql"
    mine.write_bytes(dump_of("acore_characters"))
    put(server, "20261009_100000", "acore_characters")
    found = shelf(server)
    r = row(found, mine.name)
    assert r.named_by_yulon is False
    assert r.database is None
    assert r.cannot_delete
    assert "did not make this file" in r.cannot_delete
    with pytest.raises(ShelfRefusal, match="did not make this file"):
        backup_shelf.plan_delete(found, mine.name)
    plan = backup_shelf.plan_clean_up(found, Rule(older_than_days=0), now=NOW)
    assert mine.name not in plan.names
    # and it is not cover for a database: the Yu'lon-named copy is still the newest good one
    assert row(found, "20261009_100000_acore_characters.sql").kept_because


def test_a_linked_file_is_not_listed(server: Path) -> None:
    elsewhere = server / "elsewhere.sql"
    elsewhere.write_bytes(dump_of("acore_world"))
    link = folder_of(server) / "20261001_100000_acore_world.sql"
    link.symlink_to(elsewhere)
    assert shelf(server).rows == ()


def test_a_missing_folder_is_an_empty_shelf(tmp_path: Path) -> None:
    found = backup_shelf.read_shelf(tmp_path, game_id=GAME, installed=NONE_INSTALLED)
    assert found.rows == ()
    assert found.delete_refused is None


def test_a_row_says_when_what_and_how_big(server: Path) -> None:
    path = put(server, "20261001_103000", "acore_characters", pad=100)
    r = row(shelf(server), path.name)
    assert r.made_at == datetime(2026, 10, 1, 10, 30, 0)
    assert r.database == "acore_characters"
    assert r.label is None
    assert r.size == path.stat().st_size
    assert r.frees_bytes == r.size
    assert r.usable is True
    assert r.game == GAME
    assert r.read_only is False
    assert r.kind == "dump"


@pytest.mark.parametrize(
    ("label", "database"),
    [
        ("pre-restore", "acore_world"),
        ("before-new-build", "acore_characters"),
        ("after-new-build", "acore_auth"),
        ("before-bigger-stacks", "acore_world"),
        ("move", "acore_world"),
    ],
)
def test_the_label_is_read_before_the_database(server: Path, label: str, database: str) -> None:
    path = put(server, "20261001_100000", database, label=label)
    r = row(shelf(server), path.name)
    assert (r.label, r.database) == (label, database)


def test_before_new_build_is_not_read_as_a_before_id_item(server: Path) -> None:
    path = put(server, "20261001_100000", label="before-new-build")
    r = row(shelf(server, installed={"mod": frozenset({"new-build"})}), path.name)
    assert r.label == "before-new-build"
    assert r.item is None


def test_a_before_id_label_names_its_item(server: Path) -> None:
    path = put(server, "20261001_100000", label="before-bigger-stacks")
    assert row(shelf(server), path.name).item == "bigger-stacks"


def test_a_cut_short_dump_is_not_usable_but_is_listed(server: Path) -> None:
    path = put(server, "20261001_100000", whole=False)
    r = row(shelf(server), path.name)
    assert r.usable is False
    assert r.problem


def test_a_dump_whose_name_does_not_match_its_content_is_still_usable(server: Path) -> None:
    """Cut-short is judged by the dump's own banner and trailer, never by its file name."""
    path = folder_of(server) / "20261001_100000_manual.sql"
    path.write_bytes(dump_of("acore_world"))
    assert row(shelf(server), path.name).usable is True


def test_a_partial_and_a_gz_are_listed_unusable(server: Path) -> None:
    (folder_of(server) / "20261002_100000_acore_world.sql.partial").write_bytes(b"half")
    (folder_of(server) / "old.sql.gz").write_bytes(b"\x1f\x8b")
    found = shelf(server)
    assert row(found, "20261002_100000_acore_world.sql.partial").kind == "partial"
    assert row(found, "old.sql.gz").kind == "gz"
    assert not any(r.usable for r in found.rows)


def test_the_game_a_backup_records_is_shown_and_an_unreadable_record_is_not_usable(
    server: Path,
) -> None:
    old = put(server, "20261001_100000", game=None)
    foreign = put(server, "20261002_100000", game="wow-tbc")
    bad = folder_of(server) / "20261003_100000_acore_world.sql"
    bad.write_bytes(
        b"-- yulon-backup: game=\n" + BANNER + b"USE `acore_world`;\n-- Dump completed\n"
    )
    found = shelf(server)
    assert row(found, old.name).game is None
    assert row(found, foreign.name).game == "wow-tbc"
    assert row(found, bad.name).usable is False


def test_a_hard_linked_file_frees_nothing_and_cannot_be_deleted(server: Path) -> None:
    path = put(server, "20261001_100000", pad=50)
    os.link(path, server / "also-here.sql")
    r = row(shelf(server), path.name)
    assert r.frees_bytes == 0
    assert r.cannot_delete


def test_a_read_only_file_is_flagged(server: Path) -> None:
    path = put(server, "20261001_100000")
    path.chmod(0o444)
    assert row(shelf(server), path.name).read_only is True


def test_a_backups_folder_linked_out_of_the_install_is_listed_but_refuses_delete(
    server: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    other = tmp_path_factory.mktemp("otherdrive")
    (other / "20261001_100000_acore_world.sql").write_bytes(dump_of("acore_world"))
    folder = folder_of(server)
    folder.rmdir()
    folder.symlink_to(other, target_is_directory=True)
    found = shelf(server)
    assert [r.name for r in found.rows] == ["20261001_100000_acore_world.sql"]
    assert found.delete_refused
    assert all(r.cannot_delete for r in found.rows)


def test_a_backups_folder_linked_to_somewhere_inside_the_install_may_delete(
    server: Path,
) -> None:
    real = server / "tidy"
    real.mkdir()
    (real / "20261001_100000_acore_world.sql").write_bytes(dump_of("acore_world"))
    (real / "20261002_100000_acore_world.sql").write_bytes(dump_of("acore_world"))
    folder = folder_of(server)
    folder.rmdir()
    folder.symlink_to(real, target_is_directory=True)
    found = shelf(server)
    assert found.delete_refused is None
    assert row(found, "20261001_100000_acore_world.sql").cannot_delete is None


def test_the_look_at_each_file_is_a_whole_lstat_not_the_directory_entrys(
    server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows `DirEntry.stat()` leaves inode, device and link count at zero."""
    path = put(server, "20261001_100000")
    os.link(path, server / "second-name.sql")
    real_scandir = os.scandir

    class Entry:
        def __init__(self, inner: os.DirEntry[str]) -> None:
            self.name, self.path = inner.name, inner.path
            self._inner = inner

        def stat(self, *, follow_symlinks: bool = True) -> object:
            raw = self._inner.stat(follow_symlinks=follow_symlinks)
            return os.stat_result(
                (raw.st_mode, 0, 0, 0, raw.st_uid, raw.st_gid, raw.st_size, 0, raw.st_mtime, 0)
            )

    monkeypatch.setattr(os, "scandir", lambda p: [Entry(e) for e in real_scandir(p)])
    r = row(shelf(server), path.name)
    assert r.identity[:2] != (0, 0)
    assert r.links == 2
    assert r.frees_bytes == 0


# -------------------------------------------------------------- protections


def test_the_newest_good_copy_of_each_database_is_kept(server: Path) -> None:
    old_w = put(server, "20261001_100000", "acore_world")
    new_w = put(server, "20261003_100000", "acore_world")
    only_c = put(server, "20261002_100000", "acore_characters")
    found = shelf(server)
    assert row(found, old_w.name).kept_because is None
    assert row(found, new_w.name).kept_because
    assert row(found, only_c.name).kept_because


def test_a_cut_short_newest_copy_does_not_cover_for_the_older_good_one(server: Path) -> None:
    good = put(server, "20261001_100000", "acore_world")
    broken = put(server, "20261002_100000", "acore_world", whole=False)
    found = shelf(server)
    assert row(found, good.name).kept_because
    assert row(found, broken.name).kept_because is None


def test_another_games_newest_file_is_not_cover(server: Path) -> None:
    mine = put(server, "20261001_100000", "acore_world")
    foreign = put(server, "20261002_100000", "acore_world", game="wow-unbound")
    found = shelf(server)
    assert row(found, mine.name).kept_because
    assert row(found, foreign.name).kept_because is None


def test_the_newest_update_copy_set_is_kept_and_an_older_set_is_not(server: Path) -> None:
    older = [
        put(server, "20261001_100000", db, label="before-new-build")
        for db in ("acore_world", "acore_characters")
    ]
    newer = [
        put(server, "20261005_100000", db, label="before-new-build")
        for db in ("acore_world", "acore_characters")
    ]
    put(server, "20261009_100000", "acore_world")  # a later plain backup covers the databases
    put(server, "20261009_100000", "acore_characters")
    found = shelf(server)
    assert all(row(found, p.name).kept_because for p in newer)
    assert all(row(found, p.name).kept_because is None for p in older)


def test_files_the_restore_marker_names_are_kept(server: Path) -> None:
    named = put(server, "20261001_100000", "acore_world", label="pre-restore")
    source = put(server, "20261002_100000", "acore_world", label="move")
    other = put(server, "20261003_100000", "acore_world", label="move")
    put(server, "20261009_100000", "acore_world")
    marker = maintenance.InterruptedRestore(
        marker=maintenance.marker_path(server),
        backup=source,
        databases=("acore_world",),
        safety_backup=(named,),
        started_at="x",
    )
    found = backup_shelf.read_shelf(server, game_id=GAME, installed=NONE_INSTALLED, marker=marker)
    assert row(found, named.name).kept_because
    assert row(found, source.name).kept_because
    assert row(found, other.name).kept_because is None


def test_the_real_marker_file_is_read_when_none_is_given(server: Path) -> None:
    named = put(server, "20261001_100000", "acore_world", label="pre-restore")
    put(server, "20261009_100000", "acore_world")
    marker = {
        "backup": "",
        "databases": ["acore_world"],
        "safety_backup": [named.as_posix()],
        "started_at": "x",
    }
    maintenance.marker_path(server).write_text(json.dumps(marker))
    assert row(shelf(server), named.name).kept_because


def test_an_unreadable_marker_keeps_every_file(server: Path) -> None:
    a = put(server, "20261001_100000", "acore_world")
    put(server, "20261009_100000", "acore_world")
    maintenance.marker_path(server).write_text("{not json")
    found = shelf(server)
    assert row(found, a.name).kept_because
    assert all(r.kept_because for r in found.rows)


def test_the_earliest_copy_before_an_installed_item_is_kept(server: Path) -> None:
    first = put(server, "20261001_100000", label="before-bigger-stacks")
    again = put(server, "20261004_100000", label="before-bigger-stacks")
    put(server, "20261009_100000")  # newest plain copy: covers the database
    installed = {"mod": frozenset({"bigger-stacks"})}
    found = shelf(server, installed=installed)
    assert row(found, first.name).kept_because
    assert row(found, again.name).kept_because is None


def test_the_copy_before_an_item_that_is_not_installed_is_not_kept(server: Path) -> None:
    first = put(server, "20261001_100000", label="before-bigger-stacks")
    put(server, "20261009_100000")
    found = shelf(server, installed={"mod": frozenset({"something-else"})})
    assert row(found, first.name).kept_because is None


def test_when_the_installed_list_cannot_be_read_every_before_item_copy_is_kept(
    server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = put(server, "20261001_100000", label="before-bigger-stacks")
    plain = put(server, "20261002_100000", label="move")
    put(server, "20261009_100000")

    def broken(_server: Path) -> dict[str, frozenset[str]]:
        raise OSError("disk gone")

    monkeypatch.setattr(backup_shelf.apply, "installed_clones", broken)
    found = backup_shelf.read_shelf(server, game_id=GAME)
    assert row(found, first.name).kept_because
    assert row(found, plain.name).kept_because is None


# ------------------------------------------------------------------- plans


def test_delete_plans_one_file_and_says_how_much_it_frees(server: Path) -> None:
    old = put(server, "20261001_100000", pad=500)
    put(server, "20261003_100000")
    plan = backup_shelf.plan_delete(shelf(server), old.name)
    assert plan.names == (old.name,)
    assert plan.freed == old.stat().st_size


def test_delete_refuses_a_protected_file_with_the_reason(server: Path) -> None:
    only = put(server, "20261001_100000")
    with pytest.raises(ShelfRefusal, match="newest"):
        backup_shelf.plan_delete(shelf(server), only.name)


def test_delete_refuses_a_name_the_shelf_did_not_list(server: Path) -> None:
    put(server, "20261001_100000")
    with pytest.raises(ShelfRefusal):
        backup_shelf.plan_delete(shelf(server), "../../etc/passwd")


def test_delete_refuses_a_hard_linked_file(server: Path) -> None:
    old = put(server, "20261001_100000")
    put(server, "20261003_100000")
    os.link(old, server / "elsewhere.sql")
    with pytest.raises(ShelfRefusal, match="another name"):
        backup_shelf.plan_delete(shelf(server), old.name)


def test_delete_allows_a_read_only_file_and_says_so(server: Path) -> None:
    old = put(server, "20261001_100000")
    put(server, "20261003_100000")
    old.chmod(0o444)
    plan = backup_shelf.plan_delete(shelf(server), old.name)
    assert plan.read_only == (old.name,)


def test_clean_up_keeps_the_newest_n_per_database(server: Path) -> None:
    w = [put(server, f"2026100{d}_100000", "acore_world") for d in range(1, 6)]
    c = [put(server, f"2026100{d}_100000", "acore_characters") for d in range(1, 3)]
    plan = backup_shelf.plan_clean_up(shelf(server), Rule(keep_newest=2), now=NOW)
    assert set(plan.names) == {p.name for p in w[:3]}
    assert not set(plan.names) & {p.name for p in c}
    assert plan.freed == sum(p.stat().st_size for p in w[:3])


def test_clean_up_keeps_at_least_one(server: Path) -> None:
    with pytest.raises(ValueError, match="at least one"):
        Rule(keep_newest=0)
    with pytest.raises(ValueError, match="at least one"):
        Rule(keep_newest=-3)


def test_clean_up_by_age(server: Path) -> None:
    old = put(server, "20260101_100000", "acore_world")
    mid = put(server, "20261001_100000", "acore_world")
    put(server, "20261008_100000", "acore_world")
    plan = backup_shelf.plan_clean_up(shelf(server), Rule(older_than_days=30), now=NOW)
    assert plan.names == (old.name,)
    assert mid.name not in plan.names


def test_clean_up_never_names_a_protected_file(server: Path) -> None:
    only = put(server, "20260101_100000", "acore_world")  # old, but the only good copy
    plan = backup_shelf.plan_clean_up(shelf(server), Rule(older_than_days=1), now=NOW)
    assert only.name not in plan.names


def test_clean_up_skips_read_only_foreign_and_linked_files(server: Path) -> None:
    ro = put(server, "20261001_100000", "acore_world")
    ro.chmod(0o444)
    foreign = put(server, "20261002_100000", "acore_world", game="wow-unbound")
    linked = put(server, "20261003_100000", "acore_world")
    os.link(linked, server / "elsewhere.sql")
    put(server, "20261008_100000", "acore_world")
    plan = backup_shelf.plan_clean_up(shelf(server), Rule(older_than_days=1), now=NOW)
    assert not {ro.name, foreign.name, linked.name} & set(plan.names)


def test_clean_up_can_add_the_cut_short_files(server: Path) -> None:
    put(server, "20261008_100000", "acore_world")
    broken = put(server, "20261001_100000", "acore_world", whole=False)
    partial = folder_of(server) / "20261002_100000_acore_world.sql.partial"
    partial.write_bytes(b"half")
    found = shelf(server)
    assert backup_shelf.plan_clean_up(found, Rule(older_than_days=999), now=NOW).names == ()
    plan = backup_shelf.plan_clean_up(found, Rule(include_unusable=True), now=NOW)
    assert set(plan.names) == {broken.name, partial.name}


def test_a_rule_that_selects_nothing_is_an_empty_plan(server: Path) -> None:
    put(server, "20261008_100000", "acore_world")
    plan = backup_shelf.plan_clean_up(shelf(server), Rule(keep_newest=1), now=NOW)
    assert plan.names == ()
    assert plan.freed == 0


# -------------------------------------------------------------- carrying out


class Calls:
    """Records the order of the two checks `carry_out` makes before it deletes."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.order: list[str] = []
        self.holder: docker.ServerHolder | None = None
        self.lease_error: Exception | None = None
        monkeypatch.setattr(docker, "reservation_holder", self._holder)
        monkeypatch.setattr(docker, "maintenance_lease", self._lease)

    def _holder(self, *args: object, **kwargs: object) -> docker.ServerHolder | None:
        self.order.append("holder")
        return self.holder

    def _lease(self, *args: object, **kwargs: object):  # noqa: ANN202
        from contextlib import contextmanager

        @contextmanager
        def cm():  # noqa: ANN202
            self.order.append("lease")
            if self.lease_error is not None:
                raise self.lease_error
            try:
                yield
            finally:
                self.order.append("released")

        return cm()


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    return Calls(monkeypatch)


def carry(server: Path, plan: Plan, **kw: object) -> backup_shelf.Removed:
    return backup_shelf.carry_out(
        server, plan, game_id=GAME, installed=NONE_INSTALLED, spec=object(), **kw  # type: ignore[arg-type]
    )


def test_carry_out_removes_exactly_the_planned_files(server: Path, calls: Calls) -> None:
    old = put(server, "20261001_100000", pad=40)
    new = put(server, "20261003_100000")
    plan = backup_shelf.plan_delete(shelf(server), old.name)
    done = carry(server, plan)
    assert not old.exists()
    assert new.exists()
    assert done.names == (old.name,)
    assert done.freed == plan.freed


def test_the_reservation_is_asked_before_the_lease_is_taken(server: Path, calls: Calls) -> None:
    old = put(server, "20261001_100000")
    put(server, "20261003_100000")
    plan = backup_shelf.plan_delete(shelf(server), old.name)
    carry(server, plan)
    assert calls.order[:2] == ["holder", "lease"]


def test_another_yulon_holding_the_server_refuses_before_the_lease(
    server: Path, calls: Calls
) -> None:
    old = put(server, "20261001_100000")
    put(server, "20261003_100000")
    plan = backup_shelf.plan_delete(shelf(server), old.name)
    calls.holder = docker.ServerHolder(
        "yulon-busy-x", "abc", press="Backup", who="pk@other (Linux)", ours=False, here=False
    )
    with pytest.raises(ShelfRefusal, match="Another Yu'lon"):
        carry(server, plan)
    assert calls.order == ["holder"]
    assert old.exists()


def test_a_leftover_reservation_refuses_with_the_remove_command(server: Path, calls: Calls) -> None:
    old = put(server, "20261001_100000")
    put(server, "20261003_100000")
    plan = backup_shelf.plan_delete(shelf(server), old.name)
    calls.holder = docker.ServerHolder("yulon-busy-x", "abc", ours=True, here=False, host="")
    with pytest.raises(ShelfRefusal, match="docker rm -f yulon-busy-x"):
        carry(server, plan)
    assert old.exists()


def test_a_backup_running_in_this_yulon_refuses(server: Path, calls: Calls) -> None:
    old = put(server, "20261001_100000")
    put(server, "20261003_100000")
    plan = backup_shelf.plan_delete(shelf(server), old.name)
    calls.lease_error = docker.MaintenanceLeaseTaken("A backup of this server is running.")
    with pytest.raises(ShelfRefusal, match="A backup of this server is running"):
        carry(server, plan)
    assert old.exists()


def test_the_real_lease_refuses_while_a_backup_holds_it(server: Path) -> None:
    old = put(server, "20261001_100000")
    put(server, "20261003_100000")
    plan = backup_shelf.plan_delete(shelf(server), old.name)
    with docker.maintenance_lease(server, "A backup of this server is running."):
        with pytest.raises(ShelfRefusal, match="backup of this server is running"):
            backup_shelf.carry_out(server, plan, game_id=GAME, installed=NONE_INSTALLED, spec=None)
    assert old.exists()


def test_a_file_that_changed_since_the_plan_refuses_everything(server: Path, calls: Calls) -> None:
    a = put(server, "20261001_100000", "acore_world")
    b = put(server, "20261002_100000", "acore_world")
    put(server, "20261009_100000", "acore_world")
    plan = backup_shelf.plan_clean_up(shelf(server), Rule(keep_newest=1), now=NOW)
    assert set(plan.names) == {a.name, b.name}
    b.write_bytes(dump_of("acore_world") + b"rewritten longer")
    with pytest.raises(ShelfRefusal, match="changed"):
        carry(server, plan)
    assert a.exists()
    assert b.exists()


def test_a_replaced_file_with_the_same_size_and_time_still_refuses(
    server: Path, calls: Calls
) -> None:
    a = put(server, "20261001_100000", "acore_world")
    put(server, "20261009_100000", "acore_world")
    plan = backup_shelf.plan_delete(shelf(server), a.name)
    stat0 = a.stat()
    swap = server / "swap"
    swap.write_bytes(a.read_bytes())
    os.utime(swap, ns=(stat0.st_atime_ns, stat0.st_mtime_ns))
    os.replace(swap, a)  # a different inode, same name, size and mtime
    with pytest.raises(ShelfRefusal, match="changed"):
        carry(server, plan)
    assert a.exists()


def test_a_new_backup_that_changes_what_is_protected_refuses_the_cleanup(
    server: Path, calls: Calls
) -> None:
    a = put(server, "20261001_100000", "acore_world")
    newest = put(server, "20261002_100000", "acore_world")
    plan = backup_shelf.plan_clean_up(shelf(server), Rule(keep_newest=1), now=NOW)
    assert plan.names == (a.name,)
    newest.write_bytes(b"")  # the newest good copy is now empty: `a` is the cover
    with pytest.raises(ShelfRefusal):
        carry(server, plan)
    assert a.exists()


def test_a_file_that_became_protected_since_the_plan_is_refused(server: Path, calls: Calls) -> None:
    a = put(server, "20261001_100000", "acore_world")
    put(server, "20261009_100000", "acore_world")
    plan = backup_shelf.plan_delete(shelf(server), a.name)
    maintenance.marker_path(server).write_text("{not json")
    with pytest.raises(ShelfRefusal, match="restore"):
        carry(server, plan)
    assert a.exists()


def test_a_name_with_a_path_in_it_is_never_removed(server: Path, calls: Calls) -> None:
    victim = server / "precious.txt"
    victim.write_text("keep me")
    put(server, "20261001_100000")
    forged = Plan(
        names=("../../precious.txt",),
        identities={"../../precious.txt": (0, 0, 0, 0)},
        freed=0,
        rule=None,
    )
    with pytest.raises(ShelfRefusal):
        carry(server, forged)
    assert victim.exists()


def test_a_folder_swapped_for_a_link_after_the_plan_is_refused(
    server: Path, calls: Calls, tmp_path_factory: pytest.TempPathFactory
) -> None:
    old = put(server, "20261001_100000")
    put(server, "20261003_100000")
    plan = backup_shelf.plan_delete(shelf(server), old.name)
    other = tmp_path_factory.mktemp("otherdrive")
    (other / old.name).write_bytes(old.read_bytes())
    folder = folder_of(server)
    moved = server / "moved"
    folder.rename(moved)
    folder.symlink_to(other, target_is_directory=True)
    with pytest.raises(ShelfRefusal):
        carry(server, plan)
    assert (other / old.name).exists()


def test_a_read_only_file_is_made_writable_then_removed(server: Path, calls: Calls) -> None:
    old = put(server, "20261001_100000")
    put(server, "20261003_100000")
    old.chmod(0o444)
    done = carry(server, backup_shelf.plan_delete(shelf(server), old.name))
    assert not old.exists()
    assert done.names == (old.name,)


def test_a_read_only_file_that_will_not_go_gets_its_flag_back(
    server: Path, calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = put(server, "20261001_100000")
    put(server, "20261003_100000")
    old.chmod(0o444)
    plan = backup_shelf.plan_delete(shelf(server), old.name)

    def refuse(path: object, *a: object, **k: object) -> None:
        raise PermissionError("in use")

    monkeypatch.setattr(os, "unlink", refuse)
    with pytest.raises(ShelfRefusal, match="in use"):
        carry(server, plan)
    assert old.exists()
    assert stat.S_IMODE(old.stat().st_mode) == 0o444


def test_a_file_hard_linked_after_the_plan_is_refused(server: Path, calls: Calls) -> None:
    old = put(server, "20261001_100000")
    put(server, "20261003_100000")
    plan = backup_shelf.plan_delete(shelf(server), old.name)
    os.link(old, server / "late.sql")
    with pytest.raises(ShelfRefusal):
        carry(server, plan)
    assert old.exists()


def test_files_removed_before_a_failure_are_reported(
    server: Path, calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = put(server, "20261001_100000", "acore_world")
    b = put(server, "20261002_100000", "acore_world")
    put(server, "20261009_100000", "acore_world")
    plan = backup_shelf.plan_clean_up(shelf(server), Rule(keep_newest=1), now=NOW)
    real = os.unlink

    def second_fails(path: object, *args: object, **kw: object) -> None:
        if Path(str(path)).name == b.name:
            raise PermissionError("locked")
        real(path, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "unlink", second_fails)
    with pytest.raises(ShelfRefusal) as caught:
        carry(server, plan)
    assert a.name in str(caught.value)
    assert not a.exists()
    assert b.exists()


def test_a_directory_with_a_backups_name_is_not_a_backup(server: Path) -> None:
    (folder_of(server) / "20261001_100000_acore_world.sql").mkdir()
    assert shelf(server).rows == ()


def test_a_file_the_platform_calls_a_link_is_not_listed(
    server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows shows a junction as a plain entry; `links.stat_is_link` is the judge."""
    put(server, "20261001_100000")
    monkeypatch.setattr(backup_shelf.links, "stat_is_link", lambda _st: True)
    assert shelf(server).rows == ()


def test_a_new_backup_after_the_plan_refuses_the_whole_clean_up(server: Path, calls: Calls) -> None:
    """Every file named is still fine alone; the SET the rule selects is what moved."""
    a = put(server, "20261001_100000", "acore_world")
    b = put(server, "20261002_100000", "acore_world")
    plan = backup_shelf.plan_clean_up(shelf(server), Rule(keep_newest=1), now=NOW)
    assert plan.names == (a.name,)
    put(server, "20261003_100000", "acore_world")  # now `b` is surplus too
    with pytest.raises(ShelfRefusal, match="changed"):
        carry(server, plan)
    assert a.exists()
    assert b.exists()


def test_a_folder_replaced_by_an_identical_one_after_the_plan_refuses(
    server: Path, calls: Calls
) -> None:
    """Same names, inodes, sizes and times, a different directory: still not the plan's folder."""
    old = put(server, "20261001_100000")
    put(server, "20261003_100000")
    plan = backup_shelf.plan_delete(shelf(server), old.name)
    folder = folder_of(server)
    twin = server / "twin"
    twin.mkdir()
    for f in folder.iterdir():
        os.link(f, twin / f.name)
    moved = server / "moved"
    folder.rename(moved)
    for f in moved.iterdir():
        f.unlink()
    twin.rename(folder)
    with pytest.raises(ShelfRefusal, match="changed"):
        carry(server, plan)
    assert (folder / old.name).exists()


def test_remove_one_looks_at_the_file_again(server: Path) -> None:
    """The last look before the unlink, apart from the plan's checks, each refusing alone."""
    old = put(server, "20261001_100000")
    put(server, "20261003_100000")
    found = shelf(server)
    r = row(found, old.name)

    swapped = server / "swapped"
    swapped.write_bytes(old.read_bytes() + b"more")
    os.replace(swapped, old)
    with pytest.raises(ShelfRefusal, match="changed"):
        backup_shelf._remove_one(found, server, r)
    assert old.exists()

    fresh = shelf(server)
    os.link(old, server / "late.sql")
    with pytest.raises(ShelfRefusal, match="another name"):
        backup_shelf._remove_one(fresh, server, row(fresh, old.name))
    assert old.exists()

    with pytest.raises(ShelfRefusal, match="not a file name"):
        backup_shelf._remove_one(
            found, server, dataclasses_replace(r, name="../backups/" + old.name)
        )
    assert old.exists()


def test_remove_one_refuses_a_folder_that_is_not_the_one_listed(server: Path) -> None:
    old = put(server, "20261001_100000")
    put(server, "20261003_100000")
    found = shelf(server)
    folder = folder_of(server)
    twin = server / "twin"
    twin.mkdir()
    for f in folder.iterdir():
        os.link(f, twin / f.name)
    folder.rename(server / "moved")
    for f in (server / "moved").iterdir():
        f.unlink()
    twin.rename(folder)
    with pytest.raises(ShelfRefusal, match="changed"):
        backup_shelf._remove_one(found, server, row(found, old.name))
    assert (folder / old.name).exists()


def test_the_read_only_flag_is_cleared_before_the_unlink(
    server: Path, calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows refuses to unlink a read-only file; the order is what makes Delete work there."""
    old = put(server, "20261001_100000")
    put(server, "20261003_100000")
    old.chmod(0o444)
    plan = backup_shelf.plan_delete(shelf(server), old.name)
    seen: list[int] = []
    real = os.unlink

    def watching(path: object, *a: object, **k: object) -> None:
        seen.append(stat.S_IMODE(os.stat(path).st_mode))  # type: ignore[arg-type]
        real(path, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "unlink", watching)
    carry(server, plan)
    assert seen
    assert seen[0] & stat.S_IWUSR


# ---------------------------------------------------------- the keep setting


def test_the_automatic_keep_setting_is_off_by_default(
    server: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup_shelf.platform, "config_dir", lambda: tmp_path / "cfg")
    (server / "yulon-folder-id").write_text("abc123")
    monkeypatch.setattr(backup_shelf.docker, "reservation_name", lambda _s: "yulon-busy-abc123")
    assert backup_shelf.keep_setting(server) is None


def test_the_keep_setting_is_stored_per_install_in_the_config_dir(
    server: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "cfg"
    monkeypatch.setattr(backup_shelf.platform, "config_dir", lambda: cfg)
    monkeypatch.setattr(backup_shelf.docker, "reservation_name", lambda _s: "yulon-busy-abc123")
    backup_shelf.set_keep_setting(server, 3)
    assert (cfg / "backup-keep" / "abc123.json").is_file()
    assert backup_shelf.keep_setting(server) == 3
    backup_shelf.set_keep_setting(server, None)
    assert backup_shelf.keep_setting(server) is None


@pytest.mark.parametrize(
    "junk", ["{not json", '{"keep": 0}', '{"keep": "3"}', '{"keep": 1.5}', "[]"]
)
def test_a_bad_keep_file_reads_as_off(
    server: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, junk: str
) -> None:
    cfg = tmp_path / "cfg"
    monkeypatch.setattr(backup_shelf.platform, "config_dir", lambda: cfg)
    monkeypatch.setattr(backup_shelf.docker, "reservation_name", lambda _s: "yulon-busy-abc123")
    (cfg / "backup-keep").mkdir(parents=True)
    (cfg / "backup-keep" / "abc123.json").write_text(junk)
    assert backup_shelf.keep_setting(server) is None


def test_a_server_with_no_id_has_no_keep_setting(
    server: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup_shelf.platform, "config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(backup_shelf.docker, "reservation_name", lambda _s: None)
    assert backup_shelf.keep_setting(server) is None
    with pytest.raises(ShelfRefusal):
        backup_shelf.set_keep_setting(server, 2)


def test_keeping_fewer_than_one_is_refused(
    server: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup_shelf.platform, "config_dir", lambda: tmp_path / "cfg")
    monkeypatch.setattr(backup_shelf.docker, "reservation_name", lambda _s: "yulon-busy-abc123")
    with pytest.raises(ValueError, match="at least one"):
        backup_shelf.set_keep_setting(server, 0)


def test_retention_is_a_keep_rule_run_through_the_same_checks(server: Path, calls: Calls) -> None:
    a = put(server, "20261001_100000", "acore_world")
    b = put(server, "20261002_100000", "acore_world")
    c = put(server, "20261003_100000", "acore_world")
    done = backup_shelf.clean_up(
        server,
        Rule(keep_newest=1),
        game_id=GAME,
        installed=NONE_INSTALLED,
        spec=object(),  # type: ignore[arg-type]
        now=NOW,
    )
    assert set(done.names) == {a.name, b.name}
    assert c.exists()
    assert calls.order[:2] == ["holder", "lease"]


@pytest.mark.parametrize(
    ("size", "said"),
    [(0, "0 bytes"), (32, "32 bytes"), (2048, "2 KB"), (5 * 1024 * 1024, "5.0 MB")],
)
def test_sizes_are_said_in_the_unit_that_does_not_round_them_to_zero(size: int, said: str) -> None:
    assert backup_shelf.size_text(size) == said
