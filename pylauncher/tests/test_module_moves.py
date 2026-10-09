"""Tests for the module-update record and the build-error scanner (T557 step 1).

`module_moves` keeps, per server, the commit each compiled module's clone was on
before an Update moved it, so a build that fails on that module can put it back.
The record is plain JSON in the server folder; these tests drive it through its
own functions and read the file back where the file is the fact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yulon import module_moves
from yulon.module_moves import BuildErrorScanner, Move
from yulon.ui import lines

A = "a" * 40
B = "b" * 40
C = "c" * 40
KEY = "module/mod-x"


def _file(server: Path) -> Path:
    return server / module_moves.MOVES_FILE


def _read(server: Path) -> module_moves.Ledger:
    ledger = module_moves.read(server)
    assert ledger is not None, "a ledger this build wrote must read back"
    return ledger


# -- the record ------------------------------------------------------------


def test_an_absent_record_reads_as_an_empty_one(tmp_path: Path) -> None:
    ledger = _read(tmp_path)
    assert ledger.moves == {} and ledger.skipped == {} and ledger.built_unix is None


def test_update_records_the_commit_it_moved_from(tmp_path: Path) -> None:
    """Test 1, the ledger half: the write before the move and the write after it."""
    assert module_moves.record_start(tmp_path, KEY, head=A, release="v1") == ""
    assert module_moves.record_end(tmp_path, KEY, head=B, release="v2") == ""

    move = _read(tmp_path).moves[KEY]
    assert (move.from_sha, move.to_sha) == (A, B)
    assert (move.from_release, move.to_release) == ("v1", "v2")
    assert move.at, "the move carries the clock's time"
    said = json.loads(_file(tmp_path).read_text(encoding="utf-8"))
    assert said["version"] == module_moves.VERSION
    assert said["moves"][KEY]["from"] == A and said["moves"][KEY]["to"] == B


def test_second_unbuilt_update_keeps_the_first_from(tmp_path: Path) -> None:
    """Test 2, the ledger half: A→B unbuilt, then B→C; the put-back must go to A."""
    module_moves.record_start(tmp_path, KEY, head=A, release="")
    module_moves.record_end(tmp_path, KEY, head=B, release="")

    module_moves.record_start(tmp_path, KEY, head=B, release="")
    module_moves.record_end(tmp_path, KEY, head=C, release="")

    move = _read(tmp_path).moves[KEY]
    assert (move.from_sha, move.to_sha) == (A, C)


def test_a_stale_entry_is_replaced_not_kept(tmp_path: Path) -> None:
    """HEAD is not the entry's `to` (a hand reset since): the new move starts from HEAD."""
    module_moves.record_start(tmp_path, KEY, head=A, release="")
    module_moves.record_end(tmp_path, KEY, head=B, release="")

    module_moves.record_start(tmp_path, KEY, head=C, release="")

    assert _read(tmp_path).moves[KEY].from_sha == C


def test_update_that_moved_nothing_records_nothing(tmp_path: Path) -> None:
    """Test 3, the ledger half: already on the tip, so `to == from` and the entry goes."""
    module_moves.record_start(tmp_path, KEY, head=A, release="")
    module_moves.record_end(tmp_path, KEY, head=A, release="")

    assert KEY not in _read(tmp_path).moves


def test_a_move_whose_end_was_never_written_is_still_a_move(tmp_path: Path) -> None:
    """Test 4, the ledger half: the write-ahead stays when nothing after it ran."""
    module_moves.record_start(tmp_path, KEY, head=A, release="")

    move = _read(tmp_path).moves[KEY]
    assert move.to_sha is None
    assert move.unbuilt_at(B), "HEAD moved off `from`: the reset happened"
    assert not move.unbuilt_at(A), "HEAD still on `from`: nothing moved"
    assert not move.unbuilt_at(None), "a HEAD git would not give is never a match"


def test_an_entry_is_only_acted_on_while_head_is_its_to(tmp_path: Path) -> None:
    move = Move(from_sha=A, to_sha=B)
    assert move.unbuilt_at(B)
    assert not move.unbuilt_at(C)
    assert not move.unbuilt_at(A)


def test_settle_drops_every_move_and_keeps_the_skips(tmp_path: Path) -> None:
    module_moves.record_start(tmp_path, KEY, head=A, release="")
    module_moves.record_end(tmp_path, KEY, head=B, release="")
    module_moves.record_start(tmp_path, "module/mod-y", head=A, release="")
    module_moves.record_end(tmp_path, "module/mod-y", head=C, release="")
    module_moves.skip(tmp_path, "module/mod-y", tip=C)

    assert module_moves.settle(tmp_path, now_unix=1_700_000_000) == ""

    ledger = _read(tmp_path)
    assert ledger.moves == {}
    assert ledger.skipped["module/mod-y"].tip == C
    assert ledger.built_unix == 1_700_000_000


def test_skip_moves_the_entry_to_skipped_with_its_tip(tmp_path: Path) -> None:
    module_moves.record_start(tmp_path, KEY, head=A, release="")
    module_moves.record_end(tmp_path, KEY, head=B, release="")

    module_moves.skip(tmp_path, KEY, tip=B)

    ledger = _read(tmp_path)
    assert KEY not in ledger.moves
    assert ledger.skipped[KEY].tip == B


def test_clear_skip_drops_only_the_skip(tmp_path: Path) -> None:
    module_moves.skip(tmp_path, KEY, tip=B)
    module_moves.record_start(tmp_path, KEY, head=A, release="")

    module_moves.clear_skip(tmp_path, KEY)

    ledger = _read(tmp_path)
    assert KEY not in ledger.skipped and KEY in ledger.moves


def test_drop_removes_the_move_and_the_skip(tmp_path: Path) -> None:
    module_moves.record_start(tmp_path, KEY, head=A, release="")
    module_moves.skip(tmp_path, KEY, tip=B)
    module_moves.record_start(tmp_path, "module/mod-y", head=A, release="")

    module_moves.drop(tmp_path, KEY)

    ledger = _read(tmp_path)
    assert KEY not in ledger.moves and KEY not in ledger.skipped
    assert "module/mod-y" in ledger.moves, "only the named module's entries go"


def test_mark_sql_says_the_update_ran_database_changes(tmp_path: Path) -> None:
    module_moves.record_start(tmp_path, KEY, head=A, release="")
    assert not _read(tmp_path).moves[KEY].sql

    module_moves.mark_sql(tmp_path, KEY)

    assert _read(tmp_path).moves[KEY].sql


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        json.dumps({"version": 99, "moves": {}}),
        json.dumps({"version": 1, "moves": []}),
        json.dumps(["version", 1]),
        json.dumps({"version": 1, "moves": {KEY: {"from": 7}}}),
    ],
    ids=["torn", "newer-version", "moves-not-a-map", "not-an-object", "bad-entry"],
)
def test_unreadable_ledger_reads_as_none_and_is_never_overwritten(
    tmp_path: Path, text: str
) -> None:
    """Test 18, the ledger half: fail closed, and leave the evidence where it is."""
    _file(tmp_path).write_text(text, encoding="utf-8")

    assert module_moves.read(tmp_path) is None
    problem = module_moves.record_start(tmp_path, KEY, head=A, release="")
    assert problem, "a write over a record it cannot read says why it did not write"
    for write in (
        lambda: module_moves.record_end(tmp_path, KEY, head=B, release=""),
        lambda: module_moves.settle(tmp_path),
        lambda: module_moves.skip(tmp_path, KEY, tip=B),
        lambda: module_moves.drop(tmp_path, KEY),
    ):
        assert write()
    assert _file(tmp_path).read_text(encoding="utf-8") == text


def test_a_write_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    module_moves.record_start(tmp_path, KEY, head=A, release="")
    module_moves.settle(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [module_moves.MOVES_FILE]


def test_a_write_into_a_missing_folder_says_so_and_raises_nothing(tmp_path: Path) -> None:
    assert module_moves.record_start(tmp_path / "gone", KEY, head=A, release="")


# -- the scanner -----------------------------------------------------------


def _scan(*said: str) -> tuple[str, ...]:
    scanner = BuildErrorScanner()
    for line in said:
        scanner.feed(line)
    return scanner.named


def test_a_compiler_error_names_the_module() -> None:
    """The reported line, as the engine yields it: marked as tool output, BuildKit's prefix."""
    reported = (
        "#12 512.3 /azerothcore/modules/mod-dungeon-clear/src/Lab/DcLabInject.cpp:376:68: "
        "fatal error: too many arguments to function call, expected 4, have 5"
    )
    assert _scan(lines.TOOL + reported) == ("mod-dungeon-clear",)
    assert _scan("/azerothcore/modules/mod-x/src/a.cpp:12:3: error: 'x' was not declared") == (
        "mod-x",
    )


def test_cmake_and_linker_errors_name_the_module() -> None:
    """Test 10: the two other patterns."""
    assert _scan("CMake Error at /azerothcore/modules/mod-cm/CMakeLists.txt:4 (add_library):") == (
        "mod-cm",
    )
    assert _scan(
        "/usr/bin/ld: modules/libmodules.a(Foo.cpp.o): in function `Foo::Bar()':",
        "/azerothcore/modules/mod-ld/src/Foo.cpp:31: undefined reference to `Baz::Qux()'",
    ) == ("mod-ld",)


def test_cmake_names_the_module_by_the_path_relative_to_the_source_dir_too() -> None:
    """CMake writes a file inside its own source tree relative to it: `modules/<id>/...`.

    The shape the repository's own build-output tests already carry
    (`test_docker.py`, `#25 3.2 CMake Error at modules/mod-city-bots/CMakeLists.txt:7`).
    Mutation: keep the absolute-only prefix and a CMake failure puts nothing back.
    """
    assert _scan(
        "#25 3.2 CMake Error at modules/mod-city-bots/CMakeLists.txt:7 (add_library):"
    ) == ("mod-city-bots",)
    assert _scan("CMake Error at modules/mod-x/cmake/find.cmake:3 (message):") == ("mod-x",)
    assert _scan("CMake Error at src/server/CMakeLists.txt:7 (message):") == ()


def test_a_tortoise_build_names_the_module_by_its_source_path() -> None:
    """Tortoise compiles in `/src/modules/<id>/`, where WotLK's is `/azerothcore/modules/<id>/`."""
    assert _scan(
        lines.TOOL + "#14 301.2 /src/modules/tw-mod-x/src/Hearth.cpp:12:5: error: 'y' undeclared"
    ) == ("tw-mod-x",)
    assert _scan("/src/modules/mod-y/src/a.cpp:3:1: fatal error: nope.h: No such file") == (
        "mod-y",
    )
    assert _scan("/src/modules/mod-z/src/a.cpp:31: undefined reference to `Baz::Qux()'") == (
        "mod-z",
    )
    assert _scan("CMake Error at /src/modules/mod-cm/CMakeLists.txt:4 (add_library):") == (
        "mod-cm",
    )
    assert _scan("CMake Error at modules/mod-rel/CMakeLists.txt:4 (add_library):") == ("mod-rel",)


def test_a_tortoise_path_is_read_only_at_the_start_of_a_path() -> None:
    """A `/src/modules/` inside a longer path (a host checkout) is not the build's."""
    assert _scan("/home/me/src/modules/mod-a/src/a.cpp:3:1: error: x") == ()
    assert _scan("/opt/x/src/modules/mod-a/src/a.cpp:3:1: error: x") == ()
    assert _scan("/src/src/game/Foo.cpp:3:1: error: x") == ()


def test_a_tortoise_world_start_line_does_not_name_a_module() -> None:
    assert (
        _scan(
            ">> Attempting to execute update 20260915090000_char.sql from "
            "/src/modules/mod-x/data/sql/char",
            "[ERROR] DB AutoUpdater FAILED, cancelling server.",
        )
        == ()
    )


def test_sql_lines_at_world_start_do_not_name_a_module() -> None:
    """Test 9: worldserver's own SQL apply lines name a module folder and are not compile errors."""
    assert (
        _scan(
            "Applying of file '/azerothcore/modules/mod-x/data/sql/db-world/base.sql' to "
            "database 'acore_world' failed!",
            "ERROR 1064 (42000) at line 3: You have an error in your SQL syntax",
            ">> Applying update /azerothcore/modules/mod-x/data/sql/db-world/2026_01_01.sql",
        )
        == ()
    )


def test_warnings_and_notes_do_not_name_a_module() -> None:
    assert (
        _scan(
            "/azerothcore/modules/mod-x/src/a.cpp:9:1: warning: unused variable 'y'",
            "/azerothcore/modules/mod-x/src/a.cpp:9:1: note: declared here",
            "/azerothcore/src/server/game/Movement/MotionMaster.h:255:10: error: elsewhere",
        )
        == ()
    )


def test_several_modules_are_named_once_each_in_the_order_met() -> None:
    assert _scan(
        "/azerothcore/modules/mod-b/src/x.cpp:1:1: error: one",
        "/azerothcore/modules/mod-a/src/y.cpp:2:2: error: two",
        "/azerothcore/modules/mod-b/src/z.cpp:3:3: error: three",
    ) == ("mod-b", "mod-a")


def test_a_write_that_changes_nothing_makes_no_file(tmp_path: Path) -> None:
    """A Remove of a module that was never updated, on a server that never had a record."""
    assert module_moves.drop(tmp_path, KEY) == ""
    assert module_moves.record_end(tmp_path, KEY, head=A, release="") == ""
    assert module_moves.settle(tmp_path) == ""
    assert not _file(tmp_path).exists()


def test_two_writers_in_one_process_do_not_lose_each_others_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A module Update (worker) and a failing Rebuild (worker) both read-modify-rename the record.

    Each read the file before the other renamed, so the second rename dropped the
    first's entry. Here the read is made slow so the two would interleave every time.

    Mutation: drop the lock around the read-modify-write.
    """
    import threading
    import time

    real = module_moves._load

    def slow(path: Path) -> object:
        found = real(path)
        time.sleep(0.15)
        return found

    monkeypatch.setattr(module_moves, "_load", slow)
    start = threading.Barrier(2)

    def write(item: str) -> None:
        start.wait()
        module_moves.record_start(tmp_path, item, head=A, release="")

    threads = [threading.Thread(target=write, args=(k,)) for k in ("module/mod-x", "module/mod-y")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert set(_read(tmp_path).moves) == {"module/mod-x", "module/mod-y"}
