"""T533 + T534: what an update does with the core's database chain and the bots' world SQL.

T533 (lead's call (d), 2026-10-07): a move that ADDS a file to the core's update chain
(`sql/updates/{mangos,characters,realmd,logs}`) is refused before anything is built,
every moved source back -- those are migrations for the characters and accounts as well
as the world, and a rollback after applying them would strand the old core.

T534 (lead, 2026-10-07): the bots' world SQL is whole-table files, so each one whose
bytes the world's file ledger does not hold is loaded again whole, in T531's
servers-down window; a file of another shape is named and not run, and the update's
question says the reload replaces what the bots generated into those tables.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_world_content_updates import (  # noqa: F401 - `_gated` is an autouse fixture
    NEW,
    OLD,
    TBC,
    _gated,
    _installed,
    _lay,
    _press,
)
from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import sqlplan
from yulon.catalog.installer import InstallerError, UpdateRefused

CORE = next(s for s in TBC.emulator.sources if s.repo == "cmangos/mangos-tbc")
BOTS = next(s for s in TBC.emulator.sources if s.repo == "cmangos/playerbots")
BOT_FILES = (
    "src/mangos-tbc/src/modules/Bots/sql/world/0001.sql",
    "src/mangos-tbc/src/modules/Bots/sql/world/0002.sql",
    "src/mangos-tbc/src/modules/Bots/sql/world/tbc/0001.sql",
    "src/mangos-tbc/src/modules/Bots/sql/world/tbc/0002.sql",
)


def _sent(rec: object, rel: str) -> int:
    return rec.sql_calls.count(f"-- {rel}")  # type: ignore[attr-defined]


# -- T533 -------------------------------------------------------------------------


@pytest.mark.parametrize("folder", ["mangos", "characters", "realmd", "logs"])
def test_a_core_move_that_adds_a_database_update_is_refused_before_anything_is_built(
    tmp_path: Path, folder: str
) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    core = server_dir / CORE.dest
    rec.diffs[(core, OLD, NEW)] = (("A", f"sql/updates/{folder}/s9999_01_{folder}_x.sql"),)
    with pytest.raises(InstallerError, match="database update") as raised:
        _press(rec, server_dir, db, world)
    assert "s9999_01" in str(raised.value) and "fresh install" in str(raised.value)
    assert "build" not in rec.calls
    assert {rec.heads[server_dir / s.dest] for s in TBC.emulator.sources} == {OLD}
    assert db.rows == {}, "nothing was written to the world either"


def test_a_core_move_that_changes_an_existing_update_file_is_refused_too(
    tmp_path: Path,
) -> None:
    """Codex on T533: an edited migration the server already ran is never applied either,
    so a new core could meet an older schema. Fail closed on any change to the chain."""
    rec, server_dir, db, world = _installed(tmp_path)
    core = server_dir / CORE.dest
    rec.diffs[(core, OLD, NEW)] = (("M", "sql/updates/mangos/0002.sql"),)
    with pytest.raises(InstallerError, match="database update"):
        _press(rec, server_dir, db, world)
    assert "build" not in rec.calls


def test_a_core_move_that_changes_only_code_is_not_refused(tmp_path: Path) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    core = server_dir / CORE.dest
    rec.diffs[(core, OLD, NEW)] = (("M", "src/game/x.cpp"), ("A", "sql/base/other.sql"))
    _press(rec, server_dir, db, world)
    assert "build" in rec.calls


def test_a_core_move_git_cannot_describe_is_refused(tmp_path: Path) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    rec.diffs[(server_dir / CORE.dest, OLD, NEW)] = None
    with pytest.raises(InstallerError, match="could not read"):
        _press(rec, server_dir, db, world)
    assert "build" not in rec.calls


# -- T534 -------------------------------------------------------------------------


def test_the_first_update_loads_every_bot_table_file_once_and_the_next_loads_none(
    tmp_path: Path,
) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    lines = _press(rec, server_dir, db, world)
    for rel in BOT_FILES:
        assert _sent(rec, rel) == 1, rel
        assert db.rows[("playerbots world", rel)][1] == "applied"
    assert any("every bot table" in line and "replaced" in line for line in lines), lines
    rec.sql_calls.clear()
    _press(rec, server_dir, db, world)
    assert not any(_sent(rec, rel) for rel in BOT_FILES)


def test_a_bot_table_file_upstream_changed_is_loaded_again_whole(tmp_path: Path) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    _press(rec, server_dir, db, world)
    changed = BOT_FILES[2]
    bots = server_dir / BOTS.dest

    def upstream_changes_it(dest: Path) -> None:
        if dest == bots:
            _lay(
                server_dir,
                changed,
                f"-- {changed}\nDROP TABLE IF EXISTS t;\nINSERT INTO t VALUES (2);\n",
            )

    rec.on_clone = upstream_changes_it
    rec.upstream[bots] = "c" * 40
    rec.sql_calls.clear()
    _press(rec, server_dir, db, world)
    assert _sent(rec, changed) == 1
    assert sum(_sent(rec, rel) for rel in BOT_FILES) == 1, "only the changed one"


def test_a_bot_file_that_is_not_whole_table_is_named_and_the_others_still_load(
    tmp_path: Path,
) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    bad = BOT_FILES[1]
    _lay(server_dir, bad, f"-- {bad}\nINSERT INTO gossip_menu_option VALUES (99);\n")
    lines = _press(rec, server_dir, db, world)
    assert _sent(rec, bad) == 0 and ("playerbots world", bad) not in db.rows
    assert any(bad in line and "without emptying it first" in line for line in lines), lines
    assert all(_sent(rec, rel) == 1 for rel in BOT_FILES if rel != bad)


def test_a_bot_file_the_database_refuses_is_named_and_the_others_still_load(
    tmp_path: Path,
) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    rec.failing_sql = "SELECT 2;"  # the two `…/0002.sql` files lay_sql wrote
    lines = _press(rec, server_dir, db, world)
    assert db.rows[("playerbots world", BOT_FILES[1])][1] == "failed"
    assert db.rows[("playerbots world", BOT_FILES[0])][1] == "applied"
    assert any("refused" in line and BOT_FILES[1] in line for line in lines), lines
    assert "recreate" in rec.calls
    # Codex on T534: a whole-table file that failed (or stopped) part-way may have left
    # its table empty, and re-running it is safe, so the next update loads it again.
    rec.failing_sql = ""
    rec.sql_calls.clear()
    _press(rec, server_dir, db, world)
    assert _sent(rec, BOT_FILES[1]) == 1 and _sent(rec, BOT_FILES[0]) == 0
    assert db.rows[("playerbots world", BOT_FILES[1])][1] == "applied"


def test_an_index_file_runs_one_index_at_a_time_and_a_present_one_hides_none_after_it(
    tmp_path: Path,
) -> None:
    """ai_playerbot_indexes.sql is only CREATE INDEX. Run whole, the client stops at the
    first index that is already there and never makes a new one after it (Codex, round
    3); run one at a time, each is made, or -- already there (ERROR 1061) -- dropped and
    made again from the file, so its definition is the file's (Codex, round 4)."""
    rec, server_dir, db, world = _installed(tmp_path)
    index = BOT_FILES[0]
    _lay(
        server_dir, index, f"-- {index}\ncreate index idx_a on t(a);\ncreate index idx_b on t(b);\n"
    )
    sent = _index_client(db, {"idx_a": "ERROR 1061 (42000) at line 1: Duplicate key name 'idx_a'"})
    lines = _press(rec, server_dir, db, world)
    # idx_a is there: refused, then dropped and made again from the file (Codex round 4,
    # its definition may differ); idx_b is made; neither hides the other.
    assert sent == ["idx_a", "idx_a", "dropped", "idx_b"], sent
    assert db.rows[("playerbots world", index)][1] == "applied"
    assert any(
        index in line and "1 of its indexes were there and were made again" in line
        for line in lines
    )
    sent.clear()
    _press(rec, server_dir, db, world)
    assert sent == [], "applied: not run again"


def test_an_index_file_refused_for_any_other_reason_is_failed_and_tried_again(
    tmp_path: Path,
) -> None:
    """Codex round 2: a missing table or a permission error is not "the indexes are there"."""
    rec, server_dir, db, world = _installed(tmp_path)
    index = BOT_FILES[0]
    _lay(server_dir, index, f"-- {index}\ncreate index idx_a on t(a);\n")
    sent = _index_client(
        db, {"idx_a": "ERROR 1146 (42S02) at line 1: Table 'mangos.t' doesn't exist"}
    )
    _press(rec, server_dir, db, world)
    assert db.rows[("playerbots world", index)][1] == "failed"
    sent.clear()
    _press(rec, server_dir, db, world)
    assert "idx_a" in sent, "failed: loaded again on the next update"


def _index_client(db: object, refusals: dict[str, str]) -> list[str]:
    """The client for CREATE INDEX scripts: refuses an index named in `refusals` with that
    stderr, accepts the rest; returns the list of index names it was sent, in order."""
    import io
    import re
    import subprocess

    real = db.exec_stdin  # type: ignore[attr-defined]
    sent: list[str] = []

    def exec_stdin(container, argv, source, *, env, wsl_distro=None):  # type: ignore[no-untyped-def]
        data = source.read()
        found = re.findall(rb"(?i)create index (\w+)", data)
        if found:
            names = [n.decode() for n in found]
            sent.extend(names)
            if re.search(rb"(?i)drop index", data):
                sent.append("dropped")
                return subprocess.CompletedProcess(list(argv), 0, "", "")
            for name in names:
                if name in refusals:
                    return subprocess.CompletedProcess(list(argv), 1, "", refusals[name])
            return subprocess.CompletedProcess(list(argv), 0, "", "")
        return real(container, argv, io.BytesIO(data), env=env, wsl_distro=wsl_distro)

    db.exec_stdin = exec_stdin  # type: ignore[attr-defined]
    return sent


@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla", "wow-tortoise", "wow-wotlk"])
def test_the_update_question_says_changed_bot_tables_replace_what_the_bots_generated(
    game: str,
) -> None:
    """Restored in the T534 rework: an edit in round 3 deleted it by accident."""
    entry = load_catalog().get(game)
    for text in (
        native.update_to_latest_confirmation(entry, Path("/srv"), "x/y"),
        native.return_to_pin_confirmation(entry, Path("/srv"), "x/y"),
    ):
        said = "loads every bot table fresh" in text and "the bots generated" in text
        assert said is (game in ("wow-tbc", "wow-vanilla")), text


# -- the whole-table guard, pure --------------------------------------------------


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        (
            "/*!40101 SET NAMES utf8 */;\nDROP TABLE IF EXISTS `t`;\nCREATE TABLE `t` (a int);\n"
            "/*!40000 ALTER TABLE `t` DISABLE KEYS */;\nINSERT INTO `t` VALUES (1,'a;b');\n"
            "/*!40000 ALTER TABLE `t` ENABLE KEYS */;\n",
            None,
        ),
        ("DELETE FROM t;\nINSERT INTO t VALUES (1);\nUPDATE t SET a = a + 1;\n", None),
        ("delete FROM gossip_menu_option where option_id = 99;\n", None),
        ("create index i on creature_loot_template(item);\n", None),
        (
            "DROP TABLE IF EXISTS t;\nCREATE TABLE IF NOT EXISTS t (a int);\n"
            "CREATE INDEX i ON t(a);\n",
            None,
        ),
        ("DELETE FROM t;\nCREATE INDEX i ON t(a);\n", "adds an index to t, which it did not drop"),
        (
            "DELETE FROM t;\nREPLACE INTO t VALUES (1);\n"
            "TRUNCATE u;\nINSERT IGNORE INTO u VALUES (1);\n",
            None,
        ),
        ("REPLACE INTO t VALUES (1);\n", "it writes t without emptying"),
        ("INSERT IGNORE INTO u VALUES (1);\n", "it writes u without emptying"),
        ("INSERT INTO t VALUES (1);\n", "it writes t without emptying it first"),
        ("DELETE FROM t WHERE a = 1;\nINSERT INTO t VALUES (1);\n", "it writes t without emptying"),
        ("UPDATE t SET a = a + 1;\n", "it writes t without emptying"),
        ("DELETE FROM t;\nUPDATE t JOIN u ON t.a = u.a SET u.b = 1;\n", "more than one table"),
        ("DELETE FROM t;\nUPDATE t, u SET u.b = 1;\n", "more than one table"),
        ("DELETE FROM t;\nUPDATE t AS x SET x.b = 1;\n", "more than one table"),
        ("DELETE FROM t LIMIT 1;\nINSERT INTO t VALUES (1);\n", "not safe to repeat"),
        ("DELETE FROM t ORDER BY a LIMIT 1;\nINSERT INTO t VALUES (1);\n", "not safe to repeat"),
        ("DELETE t FROM t JOIN u ON t.a = u.a;\nINSERT INTO t VALUES (1);\n", "not safe to repeat"),
        ("ALTER TABLE t ADD COLUMN b int;\n", "not safe to repeat"),
        ("CREATE TABLE t (a int);\n", "creates t without dropping it first"),
        (
            "CREATE TABLE IF NOT EXISTS t (a int);\nDELETE FROM t;\n",
            "creates t without dropping it first",
        ),
        ("/*!50000 ALTER TABLE t ADD COLUMN b int */;\n", "not safe to repeat"),
        ("INSERT INTO t VALUES ('x;DROP TABLE IF EXISTS t');\n", "it writes t without emptying"),
    ],
    ids=[
        "dump",
        "emptied",
        "gossip",
        "index",
        "drop-create-index",
        "index-in-mixed-file",
        "replace",
        "replace-unemptied",
        "insert-ignore-unemptied",
        "insert",
        "where",
        "update",
        "update-join",
        "update-list",
        "update-alias",
        "delete-limit",
        "delete-order-limit",
        "delete-join",
        "alter",
        "create-undropped",
        "create-if-not-exists",
        "exec-alter",
        "quoted-drop",
    ],
)
def test_the_whole_table_guard(tmp_path: Path, text: str, problem: str | None) -> None:
    path = _lay(tmp_path, "x.sql", text)
    found = sqlplan.whole_table_problem(path)
    if problem is None:
        assert found is None, found
    else:
        assert found is not None and problem in found, found


# -- the real bot files (T534 cold review) -------------------------------------------

PINNED_BOT_SQL = Path(__file__).parent / "data" / "playerbots-world-45bed519" / "world"
"""playerbots 45bed519's sql/world files, statements verbatim, INSERTs cut to one row."""

REAL_BOT_FILES = sorted(PINNED_BOT_SQL.rglob("*.sql"))


def test_every_real_bot_file_is_one_the_reload_will_run() -> None:
    """The cold review found 8 of these refused: mysqldump headers carry `/*!40101 SET
    @OLD_SQL_MODE=… SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */`, and the strict world-update scan
    refuses any executable comment and any sql_mode. The bot reload reads the bodies of
    executable comments for schema names and does not refuse the comment itself."""
    assert len(REAL_BOT_FILES) == 13, REAL_BOT_FILES
    others = {"characters", "realmd", "logs"}
    for path in REAL_BOT_FILES:
        assert sqlplan.whole_table_problem(path) is None, path
        assert sqlplan.foreign_schemas(path, others, executable_comments_ok=True) == (), path


def test_the_bot_scan_still_reads_schema_names_inside_an_executable_comment(
    tmp_path: Path,
) -> None:
    path = _lay(tmp_path, "x.sql", "/*!40101 UPDATE characters.c SET a=1 */;\n")
    assert sqlplan.foreign_schemas(path, {"characters"}, executable_comments_ok=True) == (
        "characters",
    )


def test_the_first_update_reloads_the_real_tbc_bot_files_headers_and_all(
    tmp_path: Path,
) -> None:
    """A route test on the verbatim pinned files: every TBC bot world file (the root ones
    and tbc/) is sent and recorded applied -- none named "not loaded"."""
    rec, server_dir, db, world = _installed(tmp_path)
    bots_sql = server_dir / BOTS.dest / "sql" / "world"
    for laid in [*bots_sql.glob("*.sql"), *(bots_sql / "tbc").glob("*.sql")]:
        laid.unlink()
    wanted = [p for p in REAL_BOT_FILES if p.parent.name in ("world", "tbc")]
    for path in wanted:
        target = bots_sql / path.relative_to(PINNED_BOT_SQL)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    lines = _press(rec, server_dir, db, world)
    assert not any("was not loaded" in line for line in lines), lines
    rels = {f"{BOTS.dest}/sql/world/{p.relative_to(PINNED_BOT_SQL).as_posix()}" for p in wanted}
    assert len(rels) == 8
    assert {
        f
        for (phase, f), (_sha, state) in db.rows.items()
        if phase == "playerbots world" and state == "applied"
    } == rels


# -- T533 refusals are remembered, and an unreadable move refuses ----------------------


def test_a_refused_core_commit_is_remembered_so_it_is_not_offered_again(tmp_path: Path) -> None:
    """UpdateRefused carries the repo and the commit; the route records them and the
    Server tab stops offering that same commit (T179's mechanism)."""
    rec, server_dir, db, world = _installed(tmp_path)
    core = server_dir / CORE.dest
    rec.diffs[(core, OLD, NEW)] = (("A", "sql/updates/mangos/s9999_01_mangos_x.sql"),)
    with pytest.raises(InstallerError) as raised:
        _press(rec, server_dir, db, world)
    refused = raised.value.__cause__  # the route adds that the sources went back
    assert isinstance(refused, UpdateRefused), type(refused)
    assert refused.repo == CORE.repo and refused.commit == NEW
    state = native.read_state(server_dir, valid=())
    assert state is not None and (CORE.repo, NEW) in state.refused_updates


def test_a_moved_core_git_cannot_name_is_refused_before_the_compile(tmp_path: Path) -> None:
    """Cold review of T533: `head_sha()` answering None after the move let the press go on."""
    from tests.test_families_cmangos import engine as tbc_engine
    from tests.test_world_content_updates import _overrides
    from yulon.catalog.installer import InstallOptions

    rec, server_dir, db, world = _installed(tmp_path)
    core = server_dir / CORE.dest

    def head_sha(dest: Path) -> str | None:
        if dest == core and rec.heads.get(dest) == NEW:
            return None
        return rec.head_sha(dest)

    made = tbc_engine(rec, **_overrides(db, world), head_sha=head_sha)
    with pytest.raises(InstallerError, match="could not read"):
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert "build" not in rec.calls


# -- what the player is told ---------------------------------------------------------------


def test_a_rollback_says_the_bot_tables_stay_as_the_new_files_left_them(tmp_path: Path) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    rec.ready = False
    said: list[str] = []
    with pytest.raises(InstallerError):
        _press(rec, server_dir, db, world, said=said)
    assert any("bot table" in line and "build from before" in line for line in said), said


def test_the_first_update_says_it_loads_every_bot_table_and_later_ones_only_changed(
    tmp_path: Path,
) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    lines = _press(rec, server_dir, db, world)
    assert any("first update" in line and "every bot table" in line for line in lines), lines
    text = native.update_to_latest_confirmation(TBC, Path("/srv"), "x/y")
    assert "The first update loads every bot table fresh" in text
    assert "later ones only those whose files changed" in text


def test_a_rollback_part_way_through_the_bot_tables_counts_the_ones_already_loaded(
    tmp_path: Path,
) -> None:
    """Codex on the T534 rework: the count is kept per file, so a press that dies on the
    second bot file still tells the rollback that the first one was loaded."""
    import io

    from yulon import docker

    rec, server_dir, db, world = _installed(tmp_path)
    real = db.exec_stdin

    def exec_stdin(container, argv, source, *, env, wsl_distro=None):  # type: ignore[no-untyped-def]
        data = source.read()
        if data.startswith(f"-- {BOT_FILES[1]}".encode()):
            raise docker.DockerCommandError("the database went away")
        return real(container, argv, io.BytesIO(data), env=env, wsl_distro=wsl_distro)

    db.exec_stdin = exec_stdin  # type: ignore[method-assign]
    said: list[str] = []
    with pytest.raises(InstallerError):
        _press(rec, server_dir, db, world, said=said)
    assert any(
        "The 1 bot table file(s) loaded before the new build started" in line for line in said
    ), said


def test_a_bot_table_that_landed_counts_even_when_its_ledger_row_cannot_be_written(
    tmp_path: Path,
) -> None:
    """Codex: the table is replaced once its SQL ran; a ledger write that then fails must not
    hide it from the rollback's sentence."""
    import io

    from yulon import docker

    rec, server_dir, db, world = _installed(tmp_path)
    real = db.exec_stdin

    def exec_stdin(container, argv, source, *, env, wsl_distro=None):  # type: ignore[no-untyped-def]
        data = source.read()
        if b"'applied'" in data and BOT_FILES[0].encode() in data:
            raise docker.DockerCommandError("the database went away")
        return real(container, argv, io.BytesIO(data), env=env, wsl_distro=wsl_distro)

    db.exec_stdin = exec_stdin  # type: ignore[method-assign]
    said: list[str] = []
    with pytest.raises(InstallerError):
        _press(rec, server_dir, db, world, said=said)
    assert any(
        "The 1 bot table file(s) loaded before the new build started" in line for line in said
    ), said


# -- re-review of d2abf7a3 ---------------------------------------------------------------


def test_the_bot_scan_refuses_a_sql_mode_switch_part_way_through(tmp_path: Path) -> None:
    """Re-review of d2abf7a3: only mysqldump's own two SQL_MODE lines are accepted in a bot
    file. A switch to NO_BACKSLASH_ESCAPES part-way changes what the scan must read as a
    string from there on, and here it hides a write to characters."""
    text = (
        "SELECT 'a\\''; SET SESSION sql_mode='NO_BACKSLASH_ESCAPES'; SELECT '\\'; "
        "UPDATE characters.x SET y=1; -- ';\n"
    )
    path = _lay(tmp_path, "x.sql", text)
    found = sqlplan.foreign_schemas(path, {"characters"}, executable_comments_ok=True)
    assert found != (), found


def test_the_mysqldump_sql_mode_lines_are_the_only_ones_a_bot_file_may_carry(
    tmp_path: Path,
) -> None:
    dump = (
        "/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;\n"
        "DROP TABLE IF EXISTS `t`;\n"
        "/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;\n"
    )
    path = _lay(tmp_path, "dump.sql", dump)
    assert sqlplan.foreign_schemas(path, set(), executable_comments_ok=True) == ()
    path.write_text(dump + "SET SESSION sql_mode='ANSI_QUOTES';\n", encoding="utf-8")
    assert sqlplan.foreign_schemas(path, set(), executable_comments_ok=True) == (
        "a change of sql_mode",
    )


@pytest.mark.parametrize(
    "line",
    [
        "\\u realmd",
        "\\. /tmp/other.sql",
        "\\! rm -rf /",
        "connect realmd",
        "source /tmp/x.sql",
        "system id",
    ],
    ids=["use", "source-short", "shell", "connect", "source", "system"],
)
@pytest.mark.parametrize("bots", [False, True], ids=["world-update", "bot-file"])
def test_a_mysql_client_command_on_its_own_line_is_refused(
    tmp_path: Path, line: str, bots: bool
) -> None:
    """T548: the client runs `\\u realmd`, `connect`, `source` and `system` itself, at the
    start of a line -- they switch schema, read other files or run a shell."""
    path = _lay(tmp_path, "x.sql", f"UPDATE t SET a=1;\n{line}\nUPDATE x SET b=1;\n")
    found = sqlplan.foreign_schemas(path, {"realmd"}, executable_comments_ok=bots)
    assert "a mysql client command" in found, found


def test_every_vendored_insert_keeps_a_real_first_row() -> None:
    """Re-review: the cut must end each INSERT on its first row, never inside a comment."""
    for path in REAL_BOT_FILES:
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(?is)INSERT INTO[^;]*?VALUES\s*(.*?);", text):
            body = match.group(1).lstrip()
            while body.startswith("--"):
                body = body.split("\n", 1)[1].lstrip()
            assert body.startswith("("), (path.name, match.group(0)[:120])


@pytest.mark.parametrize(
    "text",
    [
        "SET @OLD_SQL_MODE=0x4e4f5f4241434b534c4153485f45534341504553;\n"
        "/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;\n",
        "/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;\n"
        "/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;\n",
        "/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;\n"
        "/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;\n"
        "/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;\n",
    ],
    ids=["footer-after-own-assignment", "footer-before-header", "two-footers"],
)
def test_the_dump_sql_mode_pair_counts_only_as_one_header_then_one_footer(
    tmp_path: Path, text: str
) -> None:
    """Codex on de3192bc: a footer alone restores whatever the file put in @OLD_SQL_MODE."""
    path = _lay(tmp_path, "x.sql", text)
    found = sqlplan.foreign_schemas(path, set(), executable_comments_ok=True)
    assert "a change of sql_mode" in found, found
