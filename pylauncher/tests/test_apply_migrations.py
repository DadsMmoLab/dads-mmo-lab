"""T596 (E1, E2): an outside mod's SQL, recorded in the server's own migrations ledger.

The Tortoise core keeps a `migrations(Name, Module, Hash, AppliedAt)` table in
each database and skips a file whose `Module + ":" + Hash` is there
(`AutoUpdater.cpp:83-86`, `:135-193`); the hash is the SHA1 of the file's bytes
in UPPER-case hex (`Util.cpp:668-690`, `%02X`), compared case-sensitively. So a
file Yu'lon runs and records the same way is one the updater will never run
again, and one the updater ran is one Yu'lon does not send again.

E2 is the backup taken before the first statement of such a press: only the
databases the press writes, and a failed backup sends nothing.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pytest

from yulon.apply import Applier, ApplyError, ApplyRefusal, write_clone_claim
from yulon.manifest import Db, Manifest, parse_manifest

ITEM = "bot-gear-sql"
CHAR_FILE = "data/sql/character/20260915090000_char.sql"
WORLD_FILE = "data/sql/world/20260915090100_world.sql"
ROWS_ONLY = "DELETE FROM bot_gear WHERE id = 1;\nINSERT INTO bot_gear VALUES (1, 'it''s');\n"
WITH_DDL = "CREATE TABLE IF NOT EXISTS bot_gear (id INT);\nINSERT INTO bot_gear VALUES (2);\n"


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest().upper()


class _Ledger:
    """A database that can be read like `DockerSql.query()`, holding a migrations table.

    `applied` is per database the `(Module, Hash)` rows the table holds; a
    database missing from `tables` has no migrations table at all, which is a
    never-started world. `fail` makes every read raise, the way a stopped or
    unreachable database answers.
    """

    def __init__(
        self,
        applied: dict[str, set[tuple[str, str]]] | None = None,
        *,
        tables: tuple[str, ...] = ("auth", "characters", "world"),
        fail: str = "",
    ) -> None:
        self.applied = applied or {}
        self.tables = tables
        self.fail = fail
        self.queries: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str, str]] = []

    def query(self, db: Db, statement: str) -> str:
        self.queries.append((db, statement))
        if self.fail:
            raise RuntimeError(self.fail)
        if "information_schema" in statement:
            return "1\n" if db in self.tables else "0\n"
        found = re.search(r"Module = '([^']*)'", statement)
        assert found, statement
        # The real column is `utf8_general_ci`: case-blind unless the query says BINARY.
        exact = "BINARY Module" in statement
        return "".join(
            f"{digest}\n"
            for module, digest in self.applied.get(db, set())
            if (module == found[1] if exact else module.lower() == found[1].lower())
        )

    def run_file(self, db: Db, path: Path) -> None:
        self.sent.append(("file", db, path.read_text(encoding="utf-8")))

    def run_statement(self, db: Db, statement: str) -> None:
        self.sent.append(("statement", db, statement))


class _Backups:
    """The E2 seam: records what it was asked to back up, or refuses."""

    def __init__(self, fail: str = "") -> None:
        self.fail = fail
        self.asked: list[tuple[str, tuple[str, ...]]] = []
        self.named_for: list[str] = []

    def before(self, manifest: Manifest, dbs: tuple[Db, ...]) -> str | None:
        self.asked.append((manifest.id, tuple(dbs)))
        if self.fail:
            raise RuntimeError(self.fail)
        return f"backed up {', '.join(dbs)} before {manifest.id}"

    def named(self, manifest: Manifest) -> str | None:
        self.named_for.append(manifest.id)
        return f"the backup taken before {manifest.id}'s first database change"


def _manifest(*files: tuple[str, str], extra: list[dict[str, Any]] | None = None) -> Manifest:
    return parse_manifest(
        {
            "schema_version": 1,
            "id": ITEM,
            "name": ITEM,
            "type": "mod",
            "game": "wow-tortoise",
            "origin": {"kind": "link", "added": "2026-10-09"},
            "build": {"rebuild": False, "restart": False},
            "sql": [
                *(extra or []),
                *({"db": db, "path": path, "migration_module": ITEM} for db, path in files),
            ],
        }
    )


def _server(tmp_path: Path, files: dict[str, str]) -> Path:
    clone = tmp_path / "sql_scripts" / "clones" / ITEM
    for rel, text in files.items():
        (clone / rel).parent.mkdir(parents=True, exist_ok=True)
        (clone / rel).write_bytes(text.encode("utf-8"))
    # The folder an install from a link or a folder leaves: this app's own claim in it.
    write_clone_claim(clone, item_id=ITEM, url="", completed=True)
    return tmp_path


def _applier(server: Path, sql: Any, backups: _Backups | None = None) -> Applier:
    return Applier(server, sql=sql, world_running=lambda: False, sql_backup=backups)


def _insert(module: str, name: str, digest: str) -> str:
    return (
        "INSERT INTO `migrations` (`Name`, `Module`, `Hash`, `AppliedAt`) "
        f"VALUES ('{name}', '{module}', '{digest}', NOW());"
    )


def test_a_file_and_its_ledger_row_go_as_one_transaction_with_the_updaters_own_key(
    tmp_path: Path,
) -> None:
    """One text: the table made if missing, then START TRANSACTION, the file, the row, COMMIT.

    The row is the one `ExecuteUpdate()` writes: Name = the file's stem, Module =
    the package, Hash = upper-case hex SHA1 of the bytes. Inside the transaction,
    so a file that fails leaves no row and a row never outlives its file.
    """
    server = _server(tmp_path, {CHAR_FILE: ROWS_ONLY})
    ledger = _Ledger(tables=())
    report = _applier(server, ledger).install(_manifest(("characters", CHAR_FILE)))

    assert len(ledger.sent) == 1, ledger.sent
    kind, db, text = ledger.sent[0]
    assert (kind, db) == ("statement", "characters")
    assert text.startswith("CREATE TABLE IF NOT EXISTS `migrations`")
    body = text[text.index("START TRANSACTION;") :]
    row = _insert(ITEM, "20260915090000_char", _sha1(ROWS_ONLY))
    assert body == "START TRANSACTION;\n" + ROWS_ONLY + row + "\nCOMMIT;\n"
    assert any(CHAR_FILE in line and "migrations" in line for line in report.done), report.done


def test_a_file_the_ledger_already_holds_is_not_sent_again(tmp_path: Path) -> None:
    """Install again sends nothing: the hash is there under this Module, so the file is done."""
    server = _server(tmp_path, {CHAR_FILE: ROWS_ONLY})
    ledger = _Ledger({"characters": {(ITEM, _sha1(ROWS_ONLY))}})
    backups = _Backups()
    report = _applier(server, ledger, backups).install(_manifest(("characters", CHAR_FILE)))

    assert ledger.sent == []
    assert backups.asked == [], "nothing is sent, so nothing needs a backup"
    assert any(CHAR_FILE in line and "already" in line for line in report.skipped), report.skipped


@pytest.mark.parametrize(
    "held",
    [
        pytest.param({("", "HASH")}, id="same-hash-under-no-module"),
        pytest.param({(ITEM, "lower")}, id="lower-case-hash"),
        pytest.param({("other-package", "HASH")}, id="another-module"),
        pytest.param({(ITEM.upper(), "HASH")}, id="the-module-in-another-case"),
    ],
)
def test_only_this_modules_exact_upper_case_hash_counts_as_applied(
    tmp_path: Path, held: set[tuple[str, str]]
) -> None:
    """The updater's key is `Module:Hash`, compared exactly; nothing looser skips a file."""
    digest = _sha1(ROWS_ONLY)
    rows = {(m, digest if h == "HASH" else digest.lower()) for m, h in held}
    server = _server(tmp_path, {CHAR_FILE: ROWS_ONLY})
    ledger = _Ledger({"characters": rows})
    _applier(server, ledger).install(_manifest(("characters", CHAR_FILE)))
    assert len(ledger.sent) == 1


def test_an_unreadable_ledger_refuses_before_any_statement_of_the_press(tmp_path: Path) -> None:
    """Could not ask is not "nothing applied": the press is refused with nothing sent.

    Including an earlier step of the same press that has no ledger of its own,
    because the ledger is read before the first statement goes anywhere.
    """
    server = _server(tmp_path, {CHAR_FILE: ROWS_ONLY})
    ledger = _Ledger(fail="ERROR 2002: Can't connect")
    manifest = _manifest(
        ("characters", CHAR_FILE),
        extra=[{"db": "world", "statement": "UPDATE item_template SET stackable = 20"}],
    )
    with pytest.raises(ApplyRefusal) as refused:
        _applier(server, ledger).install(manifest)
    assert ledger.sent == []
    assert "No SQL was run" in str(refused.value)
    assert "migrations" in str(refused.value)


def test_a_reader_less_runner_refuses_a_ledgered_step_rather_than_guessing(
    tmp_path: Path,
) -> None:
    class _WriteOnly:
        def __init__(self) -> None:
            self.sent: list[str] = []

        def run_file(self, db: Db, path: Path) -> None:
            self.sent.append(path.name)

        def run_statement(self, db: Db, statement: str) -> None:
            self.sent.append(statement)

    server = _server(tmp_path, {CHAR_FILE: ROWS_ONLY})
    runner = _WriteOnly()
    with pytest.raises(ApplyRefusal):
        _applier(server, runner).install(_manifest(("characters", CHAR_FILE)))
    assert runner.sent == []


def test_a_file_that_cannot_be_one_transaction_still_goes_with_its_row_as_one_script(
    tmp_path: Path,
) -> None:
    """DDL commits by itself, so no transaction; the row is still the script's last statement.

    `mysql` stops at the first error, so the row is written only when every
    statement of the file ran (Codex review: a separate INSERT could fail after
    the file and leave it unrecorded, to be sent again).
    """
    server = _server(tmp_path, {WORLD_FILE: WITH_DDL})
    ledger = _Ledger()
    report = _applier(server, ledger).install(_manifest(("world", WORLD_FILE)))

    assert len(ledger.sent) == 1, ledger.sent
    kind, db, text = ledger.sent[0]
    assert (kind, db) == ("statement", "world")
    assert text.startswith("CREATE TABLE IF NOT EXISTS `migrations`")
    row = _insert(ITEM, "20260915090100_world", _sha1(WITH_DDL))
    assert text.endswith(WITH_DDL + row + "\n")
    assert "START TRANSACTION" not in text
    assert any("one transaction" in line for line in report.done), report.done


def test_a_lone_file_that_fails_says_part_may_be_applied(tmp_path: Path) -> None:
    class _Fails(_Ledger):
        def run_statement(self, db: Db, statement: str) -> None:
            raise ApplyError("ERROR 1062 (23000) at line 2: Duplicate entry")

    server = _server(tmp_path, {WORLD_FILE: WITH_DDL})
    with pytest.raises(ApplyError) as failed:
        _applier(server, _Fails()).install(_manifest(("world", WORLD_FILE)))
    said = str(failed.value)
    assert "Duplicate entry" in said and "may have" in said and "was not written" in said


ACCENTED = (
    "UPDATE npc SET name = 'Caf\u00e9', text = 'it\u2019s',\n"
    "  greeting = '\u041f\u0440\u0438\u0432\u0435\u0442\n  second line' WHERE id = 1;\n"
)


def test_the_file_reaches_mysql_with_its_own_bytes_so_the_ledger_hash_is_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of 2026-10-09: outside SQL went through a text-mode pipe on Windows.

    cp1252 turned `\u00e9` into one byte and `\\n` into `\\r\\n`, so the row said "applied
    this SHA1" over different content. Driven through the REAL `DockerSql` with only
    `subprocess.run` replaced: the file's bytes must appear VERBATIM inside the
    script handed to mysql, and the row must carry the SHA1 of those same bytes.
    """
    import subprocess

    from yulon import apply as apply_module

    monkeypatch.setattr(
        apply_module.platform, "docker_prefix", lambda wsl_distro=None, **kw: ("docker",)
    )
    monkeypatch.setattr(apply_module, "_probe_client", lambda container, candidates: None)
    apply_module._client_cache.clear()
    scripts: list[bytes] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        statement = kwargs["input"]
        assert isinstance(statement, bytes), "a text-mode pipe re-encodes with the locale codec"
        assert not kwargs.get("text") and not kwargs.get("encoding")
        if b"information_schema" in statement:
            return subprocess.CompletedProcess(argv, 0, b"1\n", b"")
        if b"Module = " in statement:
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        scripts.append(statement)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    server = _server(tmp_path, {CHAR_FILE: ACCENTED})
    sql = apply_module.DockerSql("tortoise-db", "hunter2", schemas={"characters": "tw_char"})
    _applier(server, sql).install(_manifest(("characters", CHAR_FILE)))

    assert len(scripts) == 1, scripts
    assert ACCENTED.encode("utf-8") in scripts[0]
    assert b"\r" not in scripts[0]
    assert _sha1(ACCENTED).encode() in scripts[0]


@pytest.mark.parametrize(
    "boom",
    [
        UnicodeEncodeError("charmap", "\u041f", 0, 1, "character maps to <undefined>"),
        BrokenPipeError(32, "Broken pipe"),
    ],
)
def test_an_encode_or_pipe_error_on_a_lone_file_is_an_apply_error_that_says_part_may_be_applied(
    tmp_path: Path, boom: Exception
) -> None:
    """Not a raw `UnicodeEncodeError` past every handler: the same words as any failed file."""

    class _Breaks(_Ledger):
        def run_statement(self, db: Db, statement: str) -> None:
            raise boom

    server = _server(tmp_path, {WORLD_FILE: WITH_DDL})
    with pytest.raises(ApplyError) as failed:
        _applier(server, _Breaks()).install(_manifest(("world", WORLD_FILE)))
    said = str(failed.value)
    assert "may have" in said and "was not written" in said, said
    assert failed.value.__cause__ is boom


def test_a_byte_order_mark_is_not_sent_but_the_ledger_key_is_the_files_own_bytes(
    tmp_path: Path,
) -> None:
    """The row's hash must equal the server updater's SHA1 of the file as it lies on disk.

    mysql chokes on a leading BOM, so the script drops it; the ledger key (`Hash`) is
    still the SHA1 of the raw bytes WITH it, because that is what the core's updater
    computes for the same file and compares (Codex review, 2026-10-09: pinned, not
    changed). Hashing what Yu'lon sent instead would make the updater run the file again.
    """
    clone = tmp_path / "sql_scripts" / "clones" / ITEM
    (clone / CHAR_FILE).parent.mkdir(parents=True)
    raw = b"\xef\xbb\xbf" + ROWS_ONLY.encode("utf-8")
    (clone / CHAR_FILE).write_bytes(raw)
    write_clone_claim(clone, item_id=ITEM, url="", completed=True)
    ledger = _Ledger(tables=())
    _applier(tmp_path, ledger).install(_manifest(("characters", CHAR_FILE)))

    text = ledger.sent[0][2]
    assert "\ufeff" not in text
    assert f"'{hashlib.sha1(raw).hexdigest().upper()}'" in text


def test_a_file_that_is_not_utf8_is_refused_before_anything_is_sent(tmp_path: Path) -> None:
    server = _server(tmp_path, {CHAR_FILE: ROWS_ONLY})
    clone = server / "sql_scripts" / "clones" / ITEM
    (clone / WORLD_FILE).parent.mkdir(parents=True, exist_ok=True)
    (clone / WORLD_FILE).write_bytes(b"UPDATE x SET name = '\xe9t\xe9';\n")
    ledger = _Ledger()
    with pytest.raises(ApplyRefusal) as refused:
        _applier(server, ledger).install(
            _manifest(("characters", CHAR_FILE), ("world", WORLD_FILE))
        )
    assert ledger.sent == [] and ledger.queries == []
    assert "not UTF-8" in str(refused.value)


def test_two_files_with_the_same_bytes_are_one_migration(tmp_path: Path) -> None:
    """The updater keys a file by `Module:Hash`, so the second copy is the same migration."""
    copy = "data/sql/character/20260916000000_char.sql"
    server = _server(tmp_path, {CHAR_FILE: ROWS_ONLY, copy: ROWS_ONLY})
    ledger = _Ledger()
    report = _applier(server, ledger).install(
        _manifest(("characters", CHAR_FILE), ("characters", copy))
    )
    assert len(ledger.sent) == 1
    assert any(copy in line and "already applied" in line for line in report.skipped)


def test_files_run_in_the_order_the_manifest_lists_them(tmp_path: Path) -> None:
    server = _server(tmp_path, {CHAR_FILE: ROWS_ONLY, WORLD_FILE: ROWS_ONLY.replace("1", "3")})
    ledger = _Ledger()
    _applier(server, ledger).install(_manifest(("characters", CHAR_FILE), ("world", WORLD_FILE)))
    assert [db for _, db, _ in ledger.sent] == ["characters", "world"]


# ---------------------------------------------------------------- E2: the backup


def test_the_backup_is_taken_once_of_exactly_the_databases_that_will_be_written(
    tmp_path: Path,
) -> None:
    """Before the first statement, and only `characters`: `world`'s file is already applied."""
    world = ROWS_ONLY.replace("1", "3")
    server = _server(tmp_path, {CHAR_FILE: ROWS_ONLY, WORLD_FILE: world})
    order: list[str] = []

    class _Watched(_Ledger):
        def run_statement(self, db: Db, statement: str) -> None:
            order.append("sql")
            super().run_statement(db, statement)

    class _WatchedBackups(_Backups):
        def before(self, manifest: Manifest, dbs: tuple[Db, ...]) -> str | None:
            order.append("backup")
            return super().before(manifest, dbs)

    ledger = _Watched({"world": {(ITEM, _sha1(world))}})
    backups = _WatchedBackups()
    report = _applier(server, ledger, backups).install(
        _manifest(("characters", CHAR_FILE), ("world", WORLD_FILE))
    )
    assert backups.asked == [(ITEM, ("characters",))]
    assert order == ["backup", "sql"]
    assert f"backed up characters before {ITEM}" in report.done


def test_the_ledger_is_read_again_after_the_backup(tmp_path: Path) -> None:
    """Codex review: a row written while the backup ran is not sent over."""
    server = _server(tmp_path, {CHAR_FILE: ROWS_ONLY})
    ledger = _Ledger()

    class _RowArrives(_Backups):
        def before(self, manifest: Manifest, dbs: tuple[Db, ...]) -> str | None:
            ledger.applied["characters"] = {(ITEM, _sha1(ROWS_ONLY))}
            return super().before(manifest, dbs)

    report = _applier(server, ledger, _RowArrives()).install(_manifest(("characters", CHAR_FILE)))
    assert ledger.sent == []
    assert any("already applied" in line for line in report.skipped), report.skipped


def test_a_failed_backup_sends_nothing(tmp_path: Path) -> None:
    server = _server(tmp_path, {CHAR_FILE: ROWS_ONLY})
    ledger = _Ledger()
    with pytest.raises(ApplyRefusal) as refused:
        _applier(server, ledger, _Backups(fail="tortoise-db is not running")).install(
            _manifest(("characters", CHAR_FILE))
        )
    assert ledger.sent == []
    assert "tortoise-db is not running" in str(refused.value)
    assert "none were sent" in str(refused.value)


def test_a_world_started_during_the_backup_is_refused_before_the_first_statement(
    tmp_path: Path,
) -> None:
    """The backup can take minutes; the running-world guard is asked again after it."""
    server = _server(tmp_path, {CHAR_FILE: ROWS_ONLY})
    ledger = _Ledger()
    up = [False]

    class _StartsTheWorld(_Backups):
        def before(self, manifest: Manifest, dbs: tuple[Db, ...]) -> str | None:
            up[0] = True
            return super().before(manifest, dbs)

    applier = Applier(server, sql=ledger, world_running=lambda: up[0], sql_backup=_StartsTheWorld())
    with pytest.raises(ApplyRefusal):
        applier.install(_manifest(("characters", CHAR_FILE)))
    assert ledger.sent == []


def test_remove_keeps_the_database_changes_and_names_the_backup(tmp_path: Path) -> None:
    server = _server(tmp_path, {CHAR_FILE: ROWS_ONLY})
    ledger = _Ledger()
    backups = _Backups()
    applier = _applier(server, ledger, backups)
    manifest = _manifest(("characters", CHAR_FILE))
    applier.install(manifest)
    sent = list(ledger.sent)

    report = applier.remove(manifest)

    assert ledger.sent == sent, "Remove sends nothing: the changes are kept"
    assert backups.named_for == [ITEM]
    assert report.left_behind[-1] == f"the backup taken before {ITEM}'s first database change"
    assert any(CHAR_FILE in line for line in report.left_behind), report.left_behind


def test_remove_of_an_item_with_no_install_sql_names_no_backup(tmp_path: Path) -> None:
    server = _server(tmp_path, {"Addon.toc": "## Interface: 11200\n"})
    backups = _Backups()
    manifest = parse_manifest(
        {
            "schema_version": 1,
            "id": ITEM,
            "name": ITEM,
            "type": "mod",
            "game": "wow-tortoise",
            "build": {"rebuild": False, "restart": False},
        }
    )
    report = _applier(server, _Ledger(), backups).remove(manifest)
    assert backups.named_for == []
    assert report.left_behind == ()
