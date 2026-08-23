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

from yulon.apply import ApplyError
from yulon.controller_wow_wotlk import accounts
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
    """A `SqlSeam` that records statements and answers lookups from a tiny table."""

    def __init__(self, accounts_table: dict[str, int] | None = None) -> None:
        self.statements: list[tuple[str, str]] = []
        self.queries: list[tuple[str, str]] = []
        self.table: dict[str, int] = dict(accounts_table or {})
        self.access: dict[int, int] = {}
        self.next_id = 12401
        self.insert_error: ApplyError | None = None
        self.on_insert: object = None

    def run_statement(self, db: Db, statement: str) -> None:
        self.statements.append((db, statement))
        if statement.startswith("INSERT INTO account ("):
            if self.insert_error is not None:
                raise self.insert_error
            name = _folded_name_in(statement)
            self.table[name] = self.next_id
            self.next_id += 1

    def query(self, db: Db, statement: str) -> str:
        self.queries.append((db, statement))
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
    """Pull the username back out of a `CONVERT(X'…' USING utf8mb4)` literal."""
    hex_body = statement.split("X'", 1)[1].split("'", 1)[0]
    return bytes.fromhex(hex_body).decode()


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
    assert f" {accounts.EXPANSION}, '', '', NOW())" in insert
    # The stored name is the folded one, carried as a hex literal.
    assert f"X'{b'NEWBIE'.hex().upper()}'" in insert


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
    sql = _FakeSql()
    result = accounts.create_account(sql, "Admin", PASSWORD, gm_level=3)
    assert result.gm_level == 3
    access = _one_statement(sql, "INSERT INTO account_access")
    assert f"VALUES ({result.account_id}, 3, {accounts.ALL_REALMS})" in access


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
    assert sql.statements == [], "an existing account must not be overwritten"


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
    sql.insert_error = ApplyError("SQL failed (inline → acore_auth): Duplicate entry")

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
    sql.insert_error = ApplyError("SQL failed (inline → acore_auth): table is read only")
    with pytest.raises(accounts.AccountError, match="could not create account NOPE"):
        accounts.create_account(sql, "Nope", PASSWORD)


def test_a_row_that_cannot_be_read_back_after_a_successful_insert_is_an_error() -> None:
    """Never invent an id — later writes would attach to the wrong account."""

    class _Amnesiac(_FakeSql):
        def run_statement(self, db: Db, statement: str) -> None:
            self.statements.append((db, statement))  # accepts the insert, stores nothing

    with pytest.raises(accounts.AccountError, match="cannot be read back"):
        accounts.create_account(_Amnesiac(), "Ghost", PASSWORD)


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


def _one_statement(sql: _FakeSql, prefix: str) -> str:
    matches = [statement for _, statement in sql.statements if statement.startswith(prefix)]
    assert len(matches) == 1, f"expected exactly one {prefix!r}, got {len(matches)}"
    return matches[0]


def _verifier_from_credentials(credentials: str, salt: bytes) -> str:
    import hashlib

    inner = hashlib.sha1(credentials.encode()).digest()
    x = int.from_bytes(hashlib.sha1(salt + inner).digest(), "little")
    return pow(accounts.GENERATOR, x, accounts.MODULUS).to_bytes(32, "little").hex().upper()
