"""SRP6 account creation against rows AzerothCore itself wrote.

`SERVER_WRITTEN` is not a vector this project computed. Both accounts were
created by the worldserver over its console on the Ubuntu test box on
2026-08-23 (`account create …` at the `AC>` prompt on AzerothCore rev
`9fb906bb7296+`), and their `salt`/`verifier` read straight back out of
`acore_auth.account` with `SELECT HEX(salt), HEX(verifier)`. They were deleted
afterwards. So these tests compare our arithmetic against the server's, which is
the only comparison that means anything here — a vector we generated ourselves
would only prove our code agrees with our code.

The `Café1234` row is the one that discriminates. Every plausible wrong
implementation still matches on a pure-ASCII password.
"""

from __future__ import annotations

import logging

import pytest

from yulon.apply import ApplyError, DockerSql
from yulon.controller_wow_wotlk import accounts, docker_ctl
from yulon.manifest import Db

# (username as typed, password as typed, server-written salt, server-written verifier)
SERVER_WRITTEN: list[tuple[str, str, str, str]] = [
    (
        "YULONSRP6",
        "Passw0rd!Mix",
        "D45E643F99E8702459718992BC99F982231CDAC45E3E98DD7C59BD8A8DECDB7E",
        "A8CCB6136F23637034C3646EF4C9B3DB4D236B31E05A8A90F72692256821AB23",
    ),
    (
        # Typed lowercase at the console; the server stored it as YULONSRP6U.
        "yulonsrp6u",
        "Café1234",
        "5A70358B13B43BEA137CD6E00A1209A3A80B0245582007DBA1AEEC4E9CEBF727",
        "9079D024812206F3801D5E4B26CA5917C75EDB32413BA84DD07FAD1B5AD2FC18",
    ),
]

PASSWORD = "Passw0rd!Mix"


class _FakeSql:
    """A `SqlSeam` that records statements and answers lookups from a tiny table.

    It keeps the two schema facts the module actually leans on: `account` is
    keyed by the folded username, and `account_access` rejects a second plain
    INSERT for an account that already has a row, exactly as MySQL's
    `(id, RealmID)` primary key does.

    It keys `access` by account id ALONE, so what it really models is a primary
    key of `(id)`. That is indistinguishable here — every write this module
    makes uses `RealmID = ALL_REALMS` — and it still kills the
    ON-DUPLICATE-KEY mutation, so the test is sound; but a future test that
    writes a second realm would find the fake stricter than the database
    (review, 2026-08-23).

    `fail_on` is a statement prefix that raises `failure` instead of running,
    which is how a database that dies part way through a multi-statement create
    is reproduced; `query_error` is the same for the read half (Docker down).
    """

    def __init__(self, accounts_table: dict[str, int] | None = None) -> None:
        self.statements: list[tuple[str, str]] = []
        self.queries: list[tuple[str, str]] = []
        self.table: dict[str, int] = dict(accounts_table or {})
        self.access: dict[int, int] = {}
        self.ranks: dict[int, int] = {}
        self.next_id = 12401
        self.fail_on: str | None = None
        self.failure = ApplyError("SQL failed (inline → acore_auth): Lost connection")
        self.query_error: ApplyError | None = None

    def run_statement(self, db: Db, statement: str) -> None:
        self.statements.append((db, statement))
        if self.fail_on is not None and statement.startswith(self.fail_on):
            raise self.failure
        if statement.startswith("INSERT INTO account(username,sha_pass_hash"):
            self.table[_folded_name_in(statement)] = self.next_id
            self.next_id += 1
        elif statement.startswith("UPDATE account SET `rank`"):
            self.ranks[int(statement.split("WHERE id = ")[1].rstrip(";"))] = int(
                statement.split("`rank` = ")[1].split(" ")[0]
            )
        elif statement.startswith("INSERT INTO account ("):
            self.table[_folded_name_in(statement)] = self.next_id
            self.next_id += 1
        elif statement.startswith("INSERT INTO account_access"):
            account_id, level = _access_values_in(statement)
            if account_id in self.access and "ON DUPLICATE KEY UPDATE" not in statement:
                raise ApplyError("SQL failed (inline → acore_auth): Duplicate entry")
            self.access[account_id] = level

    def query(self, db: Db, statement: str) -> str:
        self.queries.append((db, statement))
        if self.query_error is not None:
            raise self.query_error
        if statement.startswith("SELECT `rank` FROM account"):
            who = int(statement.split("WHERE id = ")[1].rstrip(";"))
            return str(self.ranks.get(who, 0)) + chr(10)
        if statement.startswith("SELECT id FROM account"):
            name = _folded_name_in(statement)
            found = self.table.get(name)
            return "" if found is None else f"{found}\n"
        if statement.startswith("SELECT gmlevel"):
            account_id = int(statement.split("id = ")[1].split()[0])
            level = self.access.get(account_id)
            return "" if level is None else f"{level}\n"
        raise AssertionError(f"unexpected query: {statement}")


def _folded_name_in(statement: str) -> str:
    """Pull the username back out of an `_utf8mb4 X'…'` literal.

    (Said `CONVERT(X'…' USING utf8mb4)` until 2026-08-23 — the spelling the
    collation fix removed, and never what the code emitted.)
    """
    hex_body = statement.split("X'", 1)[1].split("'", 1)[0]
    return bytes.fromhex(hex_body).decode()


def _access_values_in(statement: str) -> tuple[int, int]:
    """`(id, gmlevel)` out of an `INSERT INTO account_access … VALUES (…)`."""
    account_id, level, _realm = statement.split("VALUES (", 1)[1].split(")", 1)[0].split(",")
    return int(account_id), int(level)


# ------------------------------------------------------- the byte-exact gate


@pytest.mark.parametrize(("username", "password", "salt_hex", "verifier_hex"), SERVER_WRITTEN)
def test_the_verifier_matches_the_one_the_server_wrote_for_that_salt(
    username: str, password: str, salt_hex: str, verifier_hex: str
) -> None:
    computed = accounts.verifier_for(username, password, bytes.fromhex(salt_hex))
    assert computed.hex().upper() == verifier_hex


def test_uppercasing_the_password_the_python_way_produces_a_verifier_that_would_never_log_in() -> (
    None
):
    """`str.upper()` is the plausible shortcut, and it is wrong on non-ASCII.

    Guards `fold()`: swap it for `str.upper()` and this row's verifier changes
    while still looking perfectly well-formed. The account would exist and never
    authenticate, which is worse than no account at all.
    """
    username, password, salt_hex, verifier_hex = SERVER_WRITTEN[1]
    salt = bytes.fromhex(salt_hex)

    assert accounts.fold(password) == "CAFé1234"  # AzerothCore leaves é alone
    assert password.upper() == "CAFÉ1234"  # Python does not
    assert accounts.verifier_for(username, password, salt).hex().upper() == verifier_hex

    naive = f"{username.upper()}:{password.upper()}"
    assert _verifier_from_credentials(naive, salt) != verifier_hex


def test_reading_the_inner_digest_big_endian_produces_a_verifier_that_would_never_log_in() -> None:
    """Guards the `int.from_bytes(..., "little")` in `verifier_for()`.

    AzerothCore builds its `BigNumber` from the SHA1 digest with
    `littleEndian = true` (`BN_lebin2bn`). Big-endian is the natural Python
    default and is silently wrong.
    """
    import hashlib

    username, password, salt_hex, verifier_hex = SERVER_WRITTEN[0]
    salt = bytes.fromhex(salt_hex)
    inner = hashlib.sha1(f"{username}:{accounts.fold(password)}".encode()).digest()
    big_endian_x = int.from_bytes(hashlib.sha1(salt + inner).digest(), "big")
    wrong = pow(accounts.GENERATOR, big_endian_x, accounts.MODULUS).to_bytes(32, "little")
    assert wrong.hex().upper() != verifier_hex


# A salt whose verifier is a number small enough to need the padding: its
# top little-endian byte is zero, so the unpadded encoding is 31 bytes. Found by
# searching salts for one, because roughly 255 in 256 of them do NOT exercise
# this and a handful of random salts is a lottery, not a test.
SHORT_VERIFIER_SALT = bytes.fromhex(
    "0000000000000000000000000000000000000000000000000000000000000012"
)
SHORT_VERIFIER = "18F0E9EB10ECB1C4FB5C6A22D385DA49C2ADB448D12C4D6A4DC1401EFAEA8B00"


def test_the_verifier_is_always_thirty_two_bytes_even_when_the_number_is_smaller() -> None:
    """`binary(32)` is fixed width; `BN_bn2lebinpad` zero-pads and so must we.

    Uses a salt picked *because* its verifier has a zero top byte. An earlier
    version of this test drew random salts and let the 1-in-256 case turn up on
    its own; dropping the padding left it green.
    """
    computed = accounts.verifier_for("PADDING", PASSWORD, SHORT_VERIFIER_SALT)
    assert computed.endswith(b"\x00"), "this vector is supposed to need the padding"
    assert len(computed) == accounts.VERIFIER_LENGTH == 32
    assert computed.hex().upper() == SHORT_VERIFIER


def test_a_salt_that_is_not_thirty_two_bytes_is_refused_rather_than_hashed() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        accounts.verifier_for("SHORTSALT", PASSWORD, b"\x01" * 16)


def test_every_registration_gets_a_fresh_random_salt() -> None:
    salts = {accounts.registration_data("SAMENAME", PASSWORD)[0] for _ in range(32)}
    assert len(salts) == 32


def test_the_salt_is_the_length_the_server_uses() -> None:
    salt, verifier = accounts.registration_data("LENGTHS", PASSWORD)
    assert len(salt) == accounts.SALT_LENGTH == 32
    assert accounts.verifier_for("LENGTHS", PASSWORD, salt) == verifier


def test_folding_touches_ascii_letters_and_nothing_else() -> None:
    assert accounts.fold("abcXYZ-09_!") == "ABCXYZ-09_!"
    assert accounts.fold("café ß аб") == "CAFé ß аб"


# ------------------------------------------------------------- the row write


def test_creating_an_account_writes_the_row_the_server_would_have_written() -> None:
    sql = _FakeSql()
    result = accounts.create_account(sql, "Newbie", PASSWORD)

    assert result.created is True
    assert result.username == "NEWBIE"  # what the user types at the login box
    assert result.account_id == 12401
    assert result.gm_level == accounts.NO_GM

    insert = _one_statement(sql, "INSERT INTO account (")
    assert "expansion, reg_mail, email, joindate" in insert
    # The stored name is the folded one, carried as a hex literal.
    assert f"X'{b'NEWBIE'.hex().upper()}'" in insert


def test_the_row_is_written_for_wotlk_and_not_for_whatever_the_schema_defaults_to() -> None:
    """`expansion = 2` is WotLK, and nothing else in the suite defends it.

    0 is vanilla: such an account exists, authenticates, and then cannot use a
    single WotLK zone, class or race — the "looks fine, is wrong" failure the
    whole module is built to avoid. 2 is what `AccountMgr::CreateAccount` passes
    from `CONFIG_EXPANSION`, what the live server's `Expansion = 2` config says,
    and what both server-written rows carried.

    Pinned to the literal, because this assertion used to interpolate
    `accounts.EXPANSION` and so could not fail (review, 2026-08-23).
    """
    assert accounts.EXPANSION == 2
    sql = _FakeSql()
    accounts.create_account(sql, "Wotlk", PASSWORD)
    assert " 2, '', '', NOW())" in _one_statement(sql, "INSERT INTO account (")


def test_creating_an_account_also_seeds_the_realm_character_counter() -> None:
    """`AccountMgr::CreateAccount` runs `LOGIN_INS_REALM_CHARACTERS_INIT` right after."""
    sql = _FakeSql()
    accounts.create_account(sql, "Newbie", PASSWORD)
    realmchars = _one_statement(sql, "INSERT INTO realmcharacters")
    assert "WHERE acctid IS NULL" in realmchars  # idempotent, as AzerothCore wrote it


def test_a_new_account_gets_no_gm_powers_unless_they_were_asked_for() -> None:
    """The server's own `account create` leaves `account_access` empty; so do we."""
    sql = _FakeSql()
    result = accounts.create_account(sql, "Family", PASSWORD)
    assert result.gm_level == 0
    assert not [s for _, s in sql.statements if "account_access" in s]


def test_an_explicit_gm_level_writes_the_access_row_for_every_realm() -> None:
    """`RealmID = -1` is "every realm", as `account set gmlevel <name> 3 -1` asks for.

    The literal, not `accounts.ALL_REALMS`: the assertion used to rebuild its
    expectation from the constant it was testing (review, 2026-08-23).
    """
    assert accounts.ALL_REALMS == -1
    sql = _FakeSql()
    result = accounts.create_account(sql, "Admin", PASSWORD, gm_level=3)
    assert result.gm_level == 3
    access = _one_statement(sql, "INSERT INTO account_access")
    assert f"VALUES ({result.account_id}, 3, -1)" in access
    assert sql.access == {result.account_id: 3}


def test_a_gm_level_the_server_has_no_meaning_for_is_refused() -> None:
    sql = _FakeSql()
    with pytest.raises(accounts.AccountError, match="GM level"):
        accounts.create_account(sql, "Console", PASSWORD, gm_level=4)
    assert sql.statements == []


# ------------------------------------------------------------ already exists


def test_an_existing_username_is_reported_not_raised() -> None:
    sql = _FakeSql({"TAKEN": 7})
    sql.access[7] = 2
    result = accounts.create_account(sql, "taken", PASSWORD)

    assert result.created is False  # how the caller tells this from a failure
    assert result.account_id == 7
    assert result.gm_level == 2
    assert not [
        s for _, s in sql.statements if s.startswith("INSERT INTO account (")
    ], "an existing account keeps its salt and verifier, or its password stops working"


def test_an_existing_username_is_matched_regardless_of_the_case_it_was_typed_in() -> None:
    sql = _FakeSql({"TAKEN": 7})
    assert accounts.create_account(sql, "TaKeN", PASSWORD).created is False


def test_losing_a_race_for_the_username_is_reported_as_already_existing() -> None:
    """The pre-check cannot be atomic across two `docker exec`s.

    Decided by asking the database again, never by matching MySQL's
    duplicate-key text — that string is not part of any contract and is
    localised.
    """
    sql = _FakeSql()
    sql.fail_on = "INSERT INTO account ("
    sql.failure = ApplyError("SQL failed (inline → acore_auth): Duplicate entry")

    def _appear() -> None:
        sql.table["RACED"] = 99

    original = sql.run_statement

    def racing(db: Db, statement: str) -> None:
        if statement.startswith("INSERT INTO account ("):
            _appear()
        original(db, statement)

    sql.run_statement = racing  # type: ignore[method-assign]
    result = accounts.create_account(sql, "Raced", PASSWORD)
    assert result.created is False
    assert result.account_id == 99


def test_an_insert_that_fails_for_any_other_reason_is_an_error_not_a_silent_success() -> None:
    sql = _FakeSql()
    sql.fail_on = "INSERT INTO account ("
    sql.failure = ApplyError("SQL failed (inline → acore_auth): table is read only")
    with pytest.raises(accounts.AccountError, match="could not create account NOPE"):
        accounts.create_account(sql, "Nope", PASSWORD)


def test_a_row_that_cannot_be_read_back_after_a_successful_insert_is_an_error() -> None:
    """Never invent an id — later writes would attach to the wrong account."""

    class _Amnesiac(_FakeSql):
        def run_statement(self, db: Db, statement: str) -> None:
            self.statements.append((db, statement))  # accepts the insert, stores nothing

    with pytest.raises(accounts.AccountError, match="cannot be read back"):
        accounts.create_account(_Amnesiac(), "Ghost", PASSWORD)


# ------------------------------------------------- finishing a half-done create


def test_a_call_that_dies_after_the_account_row_is_finished_by_the_next_one() -> None:
    """There is no transaction: each statement is its own `docker exec`.

    So the `account` row is committed before the GM grant is even attempted, and
    a dropped connection in between is a state only a retry can repair. Until
    2026-08-23 the retry took the already-exists early return and wrote nothing,
    so the level the caller asked for was never granted and, with no other code
    path in the module that raises one, never could be (review, 2026-08-23).
    """
    sql = _FakeSql()
    sql.fail_on = "INSERT INTO account_access"
    with pytest.raises(accounts.AccountError, match="could not grant GM level 3"):
        accounts.create_account(sql, "Admin", PASSWORD, gm_level=3)
    assert sql.table == {"ADMIN": 12401}, "the account row is committed and stays"
    assert sql.access == {}, "and the grant it was supposed to carry is missing"

    sql.fail_on = None  # the database comes back; the user tries the same thing again
    again = accounts.create_account(sql, "Admin", PASSWORD, gm_level=3)
    assert again.created is False  # the row was already there
    assert again.account_id == 12401
    assert again.gm_level == 3  # ... and the missing grant is made
    assert sql.access == {12401: 3}


def test_an_account_the_console_already_made_still_gets_the_gm_level_asked_for() -> None:
    """The bootstrap admin's account may exist before this module ever runs.

    `controller_view` still creates accounts over the worldserver console where
    there is a pty, and the server's own `account create` grants nothing — so
    "the bootstrap admin passes `gm_level=3` explicitly" only works if an
    existing account can still be raised. It could not before 2026-08-23.
    """
    sql = _FakeSql({"ADMIN": 42})
    result = accounts.create_account(sql, "Admin", PASSWORD, gm_level=3)
    assert result.created is False
    assert result.gm_level == 3
    assert sql.access == {42: 3}


def test_raising_the_level_of_an_account_that_already_has_a_row_updates_it() -> None:
    """`account_access`'s primary key is `(id, RealmID)`, so a plain INSERT would fail.

    That is what `ON DUPLICATE KEY UPDATE` is for, and it now defends a branch
    that a call can actually reach: promoting a moderator to administrator hits
    the key. (Dropping the clause left the whole suite green before 2026-08-23,
    because nothing reached it — review, 2026-08-23.)
    """
    sql = _FakeSql({"MOD": 7})
    sql.access[7] = 1
    result = accounts.create_account(sql, "Mod", PASSWORD, gm_level=3)
    assert result.gm_level == 3
    assert sql.access == {7: 3}


def test_asking_for_less_than_the_account_already_holds_takes_nothing_away() -> None:
    """`gm_level` is a floor, not an assignment.

    Otherwise the default `NO_GM` would demote the administrator the moment
    anyone re-ran a create for their own account.
    """
    sql = _FakeSql({"BOSS": 7})
    sql.access[7] = 3
    result = accounts.create_account(sql, "Boss", PASSWORD, gm_level=1)
    assert result.gm_level == 3
    assert not [s for _, s in sql.statements if "account_access" in s]


def test_the_realm_character_counters_are_seeded_on_the_already_exists_path_too() -> None:
    """The other half of a create that can be interrupted after the account row.

    `WHERE acctid IS NULL` is what makes running it again safe, and running it
    again is the only way the missing counter row ever appears.
    """
    sql = _FakeSql({"HALFDONE": 7})
    accounts.create_account(sql, "Halfdone", PASSWORD)
    assert "WHERE acctid IS NULL" in _one_statement(sql, "INSERT INTO realmcharacters")


def test_a_database_that_cannot_be_reached_raises_the_type_the_docstring_names() -> None:
    """`AccountError` is the only type `create_account()` documents, so it is the only one.

    Docker not running is the likeliest failure there is, and it used to come
    out as a bare `ApplyError` from the pre-check query — a type no caller was
    told to catch (review, 2026-08-23). The insert must not happen either: an
    unreachable database is not evidence that the name is free.
    """
    sql = _FakeSql()
    sql.query_error = ApplyError("Docker Desktop does not seem to be installed")
    with pytest.raises(accounts.AccountError, match="Docker Desktop"):
        accounts.create_account(sql, "Nodocker", PASSWORD)
    assert sql.statements == []


def test_a_realmcharacters_failure_is_reported_in_this_modules_vocabulary_too() -> None:
    """Every statement, not just the first — that was the shape of the leak."""
    sql = _FakeSql()
    sql.fail_on = "INSERT INTO realmcharacters"
    with pytest.raises(accounts.AccountError, match="could not seed the realm character counters"):
        accounts.create_account(sql, "Counters", PASSWORD)


# ----------------------------------------------------------- no password echo


def test_the_password_never_reaches_the_sql_the_logs_or_anything_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """argv is world-readable and logs get pasted into issues; neither may carry this."""
    secret = "Hunter2!Secret"
    sql = _FakeSql()
    with caplog.at_level(logging.DEBUG):
        result = accounts.create_account(sql, "Quiet", secret, gm_level=3)

    everything = [statement for _, statement in sql.statements + sql.queries]
    everything += [record.getMessage() for record in caplog.records]
    everything += [repr(result), str(result)]
    for text in everything:
        assert secret not in text
        assert secret.upper() not in text
        assert accounts.fold(secret) not in text
    assert secret.encode().hex().upper() not in " ".join(everything)


def test_a_rejected_password_is_never_quoted_back_in_the_error() -> None:
    sql = _FakeSql()
    too_long = "Z" * (accounts.MAX_PASSWORD + 1)
    with pytest.raises(accounts.AccountError) as caught:
        accounts.create_account(sql, "Longpass", too_long)
    assert too_long not in str(caught.value)
    assert str(accounts.MAX_PASSWORD) in str(caught.value)


# ---------------------------------------------------------------- bad inputs


def test_a_password_longer_than_the_server_accepts_is_refused_before_anything_is_written() -> None:
    """`MAX_PASS_STR` is a ceiling: the server rejects a longer one, it does not truncate."""
    sql = _FakeSql()
    with pytest.raises(accounts.AccountError):
        accounts.create_account(sql, "Longpass", "P" * (accounts.MAX_PASSWORD + 1))
    assert sql.statements == []


def test_an_empty_username_or_password_is_refused() -> None:
    sql = _FakeSql()
    with pytest.raises(accounts.AccountError, match="name cannot be empty"):
        accounts.create_account(sql, "   ", PASSWORD)
    with pytest.raises(accounts.AccountError, match="password cannot be empty"):
        accounts.create_account(sql, "Someone", "")
    assert sql.statements == []


def test_a_username_longer_than_the_server_accepts_is_refused() -> None:
    sql = _FakeSql()
    with pytest.raises(accounts.AccountError, match="at most 17"):
        accounts.create_account(sql, "N" * (accounts.MAX_USERNAME + 1), PASSWORD)


def test_a_username_with_whitespace_or_control_characters_is_refused() -> None:
    sql = _FakeSql()
    for bad in ["two words", "tab\there", "null\x00byte"]:
        with pytest.raises(accounts.AccountError, match="spaces or control characters"):
            accounts.create_account(sql, bad, PASSWORD)
    assert sql.statements == []


def test_a_username_full_of_sql_cannot_break_out_of_the_statement() -> None:
    """`DockerSql` has no parameter binding, so every value goes in as a hex literal.

    A quote-escaping scheme would be the thing that breaks here — and would also
    break under `NO_BACKSLASH_ESCAPES`, where a backslash stops being an escape.
    """
    sql = _FakeSql()
    injection = "x','',''),(1"
    accounts.create_account(sql, injection, PASSWORD)
    insert = _one_statement(sql, "INSERT INTO account (")
    assert injection.upper() not in insert
    assert f"X'{accounts.fold(injection).encode().hex().upper()}'" in insert


def test_values_go_in_as_hex_literals_so_no_sql_mode_can_change_their_meaning() -> None:
    assert accounts._hex_literal(b"\x00\xff") == "X'00FF'"
    assert accounts._text_literal("AB") == "_utf8mb4 X'4142'"


def test_a_text_literal_claims_no_collation_of_its_own() -> None:
    """`CONVERT(… USING utf8mb4)` ties with the column and MySQL 8.4 errors 1267.

    The introducer form leaves the collation to the column, which is what makes
    the username lookup case-insensitive. Verified against a real MySQL 8.4;
    a fake seam cannot fail this.
    """
    assert "CONVERT" not in accounts._text_literal("AB")
    assert "COLLATE" not in accounts._text_literal("AB")


def test_the_lookup_uses_the_username_not_last_insert_id() -> None:
    """Each statement is its own `docker exec`; the inserting connection is long gone."""
    sql = _FakeSql()
    accounts.create_account(sql, "Lookup", PASSWORD)
    assert any("SELECT id FROM account WHERE username =" in q for _, q in sql.queries)
    assert not any("LAST_INSERT_ID" in s for _, s in sql.statements + sql.queries)


def test_accounts_are_written_to_the_auth_database() -> None:
    sql = _FakeSql()
    accounts.create_account(sql, "Whichdb", PASSWORD, gm_level=1)
    assert {db for db, _ in sql.statements + sql.queries} == {"auth"}


def test_the_account_seam_reaches_the_same_container_the_module_applier_does() -> None:
    """One connection story per controller, not a second one growing beside it.

    `sql_for()` has no caller yet — the UI still creates accounts over the
    console — so this is what stops it drifting away from `modules.applier()`
    before the wiring pass arrives (review, 2026-08-23).
    """
    seam = accounts.sql_for("hunter2")
    assert isinstance(seam, DockerSql)
    assert seam.db_container == docker_ctl.SPEC.db
    assert seam.root_password == "hunter2"


def _one_statement(sql: _FakeSql, prefix: str) -> str:
    matches = [statement for _, statement in sql.statements if statement.startswith(prefix)]
    assert len(matches) == 1, f"expected exactly one {prefix!r}, got {len(matches)}"
    return matches[0]


def _verifier_from_credentials(credentials: str, salt: bytes) -> str:
    import hashlib

    inner = hashlib.sha1(credentials.encode()).digest()
    x = int.from_bytes(hashlib.sha1(salt + inner).digest(), "little")
    return pow(accounts.GENERATOR, x, accounts.MODULUS).to_bytes(32, "little").hex().upper()


def test_a_lookup_that_answers_with_something_that_is_not_a_number_is_an_account_error() -> None:
    """`int(rows[0])` was the last way another exception type could leave here.

    `create_account`'s `Raises:` block promises `AccountError` is the only type
    a caller has to handle. The seam reports success by EXIT CODE, so a query
    that exits 0 having printed something that is not a number — a warning MySQL
    chose to put on stdout, a schema that is not the one assumed — reached
    `int()` and raised `ValueError` straight past that promise. The contract was
    made true rather than softened (review, 2026-08-23).
    """

    class _Garbled(_FakeSql):
        def query(self, db: Db, statement: str) -> str:
            return "Warning: mysql had an opinion\n"

    with pytest.raises(accounts.AccountError, match="expected a number"):
        accounts.create_account(_Garbled(), "caitlin", PASSWORD)


# ------------------------------------------------- the mangos_sha scheme

# Rows the TORTOISE worldserver itself wrote, over its console on the m910q box
# (2026-08-26). Not computed here: `account create` was typed at the `mangos>`
# prompt and the core logged its own INSERT, which is where these hashes come
# from. Same discipline as SERVER_WRITTEN above — a vector we generated would
# only prove our code agrees with our code.
#
#   INSERT INTO account(username,sha_pass_hash,joindate)
#   VALUES('PLAYER','3CE8A96D17C5AE88A30681024E86279F1A38C041',NOW())
#
# The MixedCase row is the one that discriminates: it is the only one that can
# tell "uppercase both" from "uppercase the username only" or "as typed".
MANGOS_WRITTEN: list[tuple[str, str, str]] = [
    ("player", "player", "3CE8A96D17C5AE88A30681024E86279F1A38C041"),
    ("MixedCase", "SoMePaSs", "75FA45B4D076CD2C9FDE701C821EE057C1CB151E"),
]


def test_mangos_password_hash_matches_the_rows_the_server_wrote() -> None:
    """`sha_pass_hash = SHA1(UPPER(user):UPPER(pass))`, uppercase hex."""
    for username, password, expected in MANGOS_WRITTEN:
        assert accounts.mangos_password_hash(username, password) == expected, username


def test_the_mixedcase_vector_rules_out_the_plausible_wrong_foldings() -> None:
    """Every wrong folding still matches on an all-lowercase pair; this one does not."""
    import hashlib

    _, _, server = MANGOS_WRITTEN[1]
    for wrong in ("MixedCase:SoMePaSs", "MIXEDCASE:SoMePaSs", "mixedcase:somepass"):
        assert hashlib.sha1(wrong.encode()).hexdigest().upper() != server, wrong


def test_a_mangos_account_is_written_the_way_that_core_writes_it() -> None:
    """The core's own statement, reproduced: no salt, no verifier, no account_access."""
    sql = _FakeSql()
    result = accounts.create_account(sql, "bob", "hunter2", scheme="mangos_sha")
    assert result.created is True and result.username == "BOB"

    written = [s for _, s in sql.statements]
    insert = _one_statement(sql, "INSERT INTO account(username,sha_pass_hash")
    # Literals go through `_text_literal`, so the hash appears hex-encoded —
    # the same treatment every other value in this module gets.
    expected = accounts._text_literal(accounts.mangos_password_hash("bob", "hunter2"))
    assert expected in insert, insert
    assert "hunter2" not in insert, "the password itself must never reach a statement"
    assert not any("salt" in s or "verifier" in s for s in written), written
    assert not any("account_access" in s for s in written), written


def test_a_mangos_account_takes_its_gm_level_from_the_rank_column() -> None:
    """CMaNGOS-family cores have no `account_access`; the level is a column."""
    sql = _FakeSql()
    result = accounts.create_account(sql, "gm", "hunter2", gm_level=3, scheme="mangos_sha")
    assert result.gm_level == 3
    grant = _one_statement(sql, "UPDATE account SET `rank`")
    assert "`rank` = 3" in grant and f"WHERE id = {result.account_id}" in grant

    # `gm_level` is a floor on this scheme too: asking for less writes nothing.
    again = accounts.create_account(sql, "gm", "hunter2", gm_level=1, scheme="mangos_sha")
    assert again.created is False and again.gm_level == 3
    assert len([s for _, s in sql.statements if s.startswith("UPDATE account SET `rank`")]) == 1


def test_the_azerothcore_scheme_is_still_the_default() -> None:
    """Every existing caller passes no scheme and must keep writing salt/verifier."""
    sql = _FakeSql()
    accounts.create_account(sql, "bob", "hunter2")
    insert = _one_statement(sql, "INSERT INTO account (")
    assert "salt, verifier" in insert and "sha_pass_hash" not in insert, insert
