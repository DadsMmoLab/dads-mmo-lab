"""Create AzerothCore game accounts by writing the SRP6 registration row directly.

The GM console (`console.py`) cannot do this everywhere: `docker attach` refuses
a stdin that is not a TTY, so `send_command()` raises before it builds its argv
on any host where `runner.pty_supported()` is false — Windows. SOAP cannot do it
either, because SOAP authenticates against an account that must already exist,
so it can never create the first one. Writing the row is therefore the only
account path that works on all three platforms, and the only one at all for the
very first account (roadmap 6.5 item 4, `pyplan/phase6-decisions.md`).

Where the algorithm comes from
------------------------------
Read out of AzerothCore's own sources on the checkout at
`/home/pk/wow-server-playerbots` (rev `9fb906bb7296+`, Playerbot branch), not
from a blog post:

* `src/common/Cryptography/Authentication/SRP6.{h,cpp}` — `g = {7}`,
  `N = HexStrToByteArray<32>("894B…9BB7", reverse=true)`, `SALT_LENGTH = 32`,
  and `CalculateVerifier()`: `v = g ^ H(s || H(u || ':' || p)) mod N`.
* `src/common/Cryptography/BigNumber.{h,cpp}` — the endianness. Constructing a
  `BigNumber` from a byte array defaults to `littleEndian = true`
  (`BN_lebin2bn`), so the 20-byte SHA1 digest is read as a LITTLE-endian
  integer; `ToByteArray<32>()` likewise defaults to little-endian and goes
  through `BN_bn2lebinpad`, so the verifier is little-endian and zero-padded to
  exactly 32 bytes.
* `src/server/game/Accounts/AccountMgr.cpp` — `CreateAccount()` folds BOTH the
  username and the password before hashing, and treats an existing name as a
  distinct result (`AOR_NAME_ALREADY_EXIST`), not as a failure.
* `src/common/Utilities/Util.h` — the fold is `Utf8ToUpperOnlyLatin`, which is
  `isBasicLatinCharacter(c) ? wcharToUpper(c) : c`, and `isBasicLatinCharacter`
  is ASCII `a-z`/`A-Z` **only**. This is why `fold()` below is hand-written
  instead of `str.upper()`: Python would also uppercase `é`, `ß` and Cyrillic,
  which AzerothCore leaves alone, and the resulting row would look perfectly
  well-formed while never authenticating.
* `src/server/database/Database/Implementation/LoginDatabase.cpp` — the exact
  statements this module reproduces (`LOGIN_INS_ACCOUNT`,
  `LOGIN_INS_REALM_CHARACTERS_INIT`, `LOGIN_INS_ACCOUNT_ACCESS`).

What it was checked against
---------------------------
Two accounts were created **by the worldserver itself** over its console on the
Ubuntu test box on 2026-08-23, and their server-written `salt`/`verifier` read
back out of `acore_auth.account`. `verifier_for()` reproduces both from those
salts, byte for byte:

    YULONSRP6  / "Passw0rd!Mix"
      salt     D45E643F99E8702459718992BC99F982231CDAC45E3E98DD7C59BD8A8DECDB7E
      verifier A8CCB6136F23637034C3646EF4C9B3DB4D236B31E05A8A90F72692256821AB23
    YULONSRP6U / "Café1234"
      salt     5A70358B13B43BEA137CD6E00A1209A3A80B0245582007DBA1AEEC4E9CEBF727
      verifier 9079D024812206F3801D5E4B26CA5917C75EDB32413BA84DD07FAD1B5AD2FC18

The second vector is the one that earns its keep: with `str.upper()` in place of
`fold()` it comes out `78719A8A…`, and with the digest read big-endian
`F370412D…` — both wrong, both indistinguishable from correct by inspection.
Both accounts were deleted afterwards. `tests/test_accounts.py` pins the pair.

Passwords
---------
Nothing here ever writes a password anywhere it could be read back: not into
argv (the statement goes to `DockerSql` over stdin, and the DB password reaches
`mysql` through `MYSQL_PWD`), not into a log record, not into an exception
message, and not into any dataclass this module returns. Only the derived salt
and verifier reach the SQL text, and neither can be turned back into the
password.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import Protocol

from yulon.apply import ApplyError, DockerSql
from yulon.controller_wow_wotlk import docker_ctl
from yulon.log import get_logger
from yulon.manifest import Db

logger = get_logger(__name__)

# SRP6.cpp: g = {7}, N = HexStrToByteArray<32>("894B…9BB7", reverse=true), read
# back as a little-endian BigNumber — which is this integer.
GENERATOR = 7
MODULUS = 0x894B645E89E1535BBDAD5B8B290650530801B18EBFBF5E8FAB3C82872A3E9BB7

SALT_LENGTH = 32  # SRP6.h SALT_LENGTH
VERIFIER_LENGTH = 32  # SRP6.h VERIFIER_LENGTH

# AccountMgr.h. Both are ceilings, not targets: AzerothCore refuses anything
# LONGER, so a "stronger" 32-character password is rejected outright.
MAX_USERNAME = 17  # MAX_ACCOUNT_STR
MAX_PASSWORD = 16  # MAX_PASS_STR

# AccountMgr.cpp passes sWorld's CONFIG_EXPANSION; 2 is WotLK, and is what the
# server wrote in both observed rows. Written explicitly rather than left to the
# column default so the row does not depend on which schema version is loaded.
EXPANSION = 2

# `account_access.RealmID = -1` is "every realm" — what the guide's
# `account set gmlevel <name> 3 -1` asks for.
ALL_REALMS = -1

# SEC_PLAYER .. SEC_ADMINISTRATOR from Common.h. SEC_CONSOLE (4) is deliberately
# not offered: it is the level the console process runs at, not one a login can
# usefully hold.
NO_GM = 0
MAX_GM_LEVEL = 3

_ACCOUNTS_DB: Db = "auth"

# AzerothCore itself only length-checks the name, but the client sends the
# username in the logon packet and the realmlist/auth path treats it as a plain
# token. Control characters and whitespace cannot be typed at a login box and
# would produce an account nobody can sign in to, so they are refused here with
# a sentence rather than written and discovered later.
_USERNAME_OK = re.compile(r"\A[^\s\x00-\x1f\x7f]+\Z")


class AccountError(RuntimeError):
    """An account could not be created (bad name/password, or the write failed).

    Never carries the password: `create_account()` builds every message in this
    module from the username and the reason alone.
    """


@dataclass(frozen=True)
class AccountResult:
    """The outcome of one `create_account()` call.

    `created` is the field that separates "already exists" from "failed" —
    "failed" raises `AccountError`, so a returned result is always a usable
    account. `username` is the folded form actually stored, which is what the
    user must type at the login box.

    Deliberately holds no password, so no caller can log or repr one by echoing
    this back.
    """

    username: str
    account_id: int
    created: bool
    gm_level: int


class SqlSeam(Protocol):
    """The read+write slice of `apply.DockerSql` this module needs.

    Declared structurally rather than importing `DockerSql` as a type so tests
    can pass a fake, and so this module states its requirement instead of
    depending on the whole engine (style-guide §3).
    """

    def run_statement(self, db: Db, statement: str) -> None: ...

    def query(self, db: Db, statement: str) -> str: ...


# ------------------------------------------------------------------ the crypto


def fold(text: str) -> str:
    """Uppercase ASCII letters and nothing else — AzerothCore's `Utf8ToUpperOnlyLatin`.

    `str.upper()` is NOT a substitute. It also maps `é→É`, `ß→SS` and Cyrillic,
    where `isBasicLatinCharacter()` returns false and AzerothCore leaves the
    character exactly as it was. Proven by the `Café1234` vector in this
    module's docstring: the two spellings give different verifiers, and only
    this one matches the row the server wrote.
    """
    return "".join(chr(ord(ch) - 0x20) if "a" <= ch <= "z" else ch for ch in text)


def make_salt() -> bytes:
    """32 CSPRNG bytes, matching `Crypto::GetRandomBytes(res.first)` in `MakeRegistrationData`."""
    return secrets.token_bytes(SALT_LENGTH)


def verifier_for(username: str, password: str, salt: bytes) -> bytes:
    """`v = g ^ H(s || H(U || ':' || P)) mod N`, little-endian, padded to 32 bytes.

    `username`/`password` are folded here, so callers pass what the user typed.

    Raises:
        ValueError: `salt` is not exactly `SALT_LENGTH` bytes. A short salt
            hashes happily and yields a verifier that simply never
            authenticates, so it is refused rather than used.
    """
    if len(salt) != SALT_LENGTH:
        raise ValueError(f"salt must be {SALT_LENGTH} bytes, got {len(salt)}")
    credentials = f"{fold(username)}:{fold(password)}".encode()
    inner = hashlib.sha1(credentials).digest()
    # BigNumber(std::array, littleEndian=true) — the digest is an integer read
    # from its LAST byte. Reading it big-endian is the classic silent failure.
    x = int.from_bytes(hashlib.sha1(salt + inner).digest(), "little")
    # ToByteArray<32>() → BN_bn2lebinpad: little-endian, zero-padded to exactly
    # 32 bytes, so a verifier that happens to fit in fewer still fills binary(32).
    return pow(GENERATOR, x, MODULUS).to_bytes(VERIFIER_LENGTH, "little")


def registration_data(username: str, password: str) -> tuple[bytes, bytes]:
    """A fresh `(salt, verifier)` pair — AzerothCore's `SRP6::MakeRegistrationData`."""
    salt = make_salt()
    return salt, verifier_for(username, password, salt)


# --------------------------------------------------------------- SQL literals


def _hex_literal(raw: bytes) -> str:
    """`X'…'`, MySQL's binary-string literal.

    Every value this module puts in a statement goes through here or
    `_text_literal()`. `DockerSql` has no parameter binding, and hand-escaping a
    quoted string is exactly the trap `NO_BACKSLASH_ESCAPES` sets (a `\\` stops
    being an escape and the statement changes meaning). A hex literal has no
    escape character at all, so it means the same thing under every `sql_mode`.
    """
    return f"X'{raw.hex().upper()}'"


def _text_literal(text: str) -> str:
    """A string literal for a `utf8mb4` column, carried as its UTF-8 bytes.

    The introducer says how to read the bytes without claiming a collation, so
    the comparison takes the *column's* — `account.username` is
    `utf8mb4_unicode_ci`, which makes `WHERE username = …` case-insensitive
    exactly as `AccountMgr::GetId()` is.

    `CONVERT(… USING utf8mb4)` was the obvious spelling and is the wrong one:
    its result carries the connection's default collation, so on MySQL 8.4
    (`utf8mb4_0900_ai_ci`) every lookup died with `ERROR 1267 Illegal mix of
    collations`. Found against the real database on 2026-08-23, not by a unit
    test — a fake SQL seam cannot have an opinion about collations.
    """
    return f"_utf8mb4 {_hex_literal(text.encode())}"


# ------------------------------------------------------------------ the write


def create_account(
    sql: SqlSeam,
    username: str,
    password: str,
    *,
    gm_level: int = NO_GM,
) -> AccountResult:
    """Create one game account, or report that the name is already taken.

    Reproduces what `AccountMgr::CreateAccount()` does, in the same order: the
    `account` row, then `realmcharacters` for every realm that has no counter
    for it yet. An `account_access` row is written only when `gm_level` is
    above `NO_GM`.

    **`gm_level` defaults to 0 — no `account_access` row at all.** The guide
    pairs `account create` with `account set gmlevel <name> 3 -1`, but that is
    the pairing for the *administrator's own* account, and `account_access` was
    empty after both server-written accounts in this module's docstring: the
    server's own `account create` grants nothing. Defaulting to 3 would make
    every account a full administrator, so the second family member to make a
    character could delete the realm. The bootstrap admin passes `gm_level=3`
    explicitly.

    Returns:
        `AccountResult` with `created=True` for a new account, or
        `created=False` when the username was already present — in which case
        nothing is written and the existing account's id and GM level are
        reported unchanged. Callers tell the two apart on `created`; a genuine
        failure never returns.

    Raises:
        AccountError: the username or password is unusable, `gm_level` is out
            of range, or the write failed. Never contains the password.
    """
    name = _checked_username(username)
    _check_password(password)
    if not NO_GM <= gm_level <= MAX_GM_LEVEL:
        raise AccountError(f"GM level must be between {NO_GM} and {MAX_GM_LEVEL}, got {gm_level}")

    existing = _account_id(sql, name)
    if existing is not None:
        logger.info(f"account {name} already exists (id {existing}), leaving it alone")
        return AccountResult(
            username=name,
            account_id=existing,
            created=False,
            gm_level=_gm_level(sql, existing),
        )

    salt, verifier = registration_data(name, password)
    logger.info(f"creating account {name} (gm level {gm_level})")  # never the password
    try:
        sql.run_statement(
            _ACCOUNTS_DB,
            "INSERT INTO account (username, salt, verifier, expansion, reg_mail, email, joindate)"
            f" VALUES ({_text_literal(name)}, {_hex_literal(salt)}, {_hex_literal(verifier)},"
            f" {EXPANSION}, '', '', NOW())",
        )
    except ApplyError as exc:
        # Something raced us to the name between the check above and here. Ask
        # the database again rather than reading the driver's error text: the
        # duplicate-key message is not part of MySQL's contract, it is
        # localised, and matching on it would break on a server whose locale we
        # never tested. If the row is there now, the caller wanted an account
        # with that name and there is one.
        raced = _account_id(sql, name)
        if raced is None:
            raise AccountError(f"could not create account {name}: the insert failed") from exc
        logger.info(f"account {name} appeared while we were inserting it (id {raced})")
        return AccountResult(
            username=name,
            account_id=raced,
            created=False,
            gm_level=_gm_level(sql, raced),
        )

    account_id = _account_id(sql, name)
    if account_id is None:
        # The insert reported success and the row is not there. Say so plainly
        # instead of returning a made-up id that later writes would attach to
        # the wrong account.
        raise AccountError(f"account {name} was inserted but cannot be read back")

    # AccountMgr::CreateAccount runs LOGIN_INS_REALM_CHARACTERS_INIT right
    # after the insert. Reproduced verbatim; its `WHERE acctid IS NULL` makes it
    # idempotent, so it is safe on a server that already has the rows.
    sql.run_statement(
        _ACCOUNTS_DB,
        "INSERT INTO realmcharacters (realmid, acctid, numchars)"
        " SELECT realmlist.id, account.id, 0 FROM realmlist, account"
        " LEFT JOIN realmcharacters ON acctid=account.id WHERE acctid IS NULL",
    )

    if gm_level > NO_GM:
        _grant_gm(sql, account_id, gm_level)

    return AccountResult(username=name, account_id=account_id, created=True, gm_level=gm_level)


def _grant_gm(sql: SqlSeam, account_id: int, gm_level: int) -> None:
    """Write the `account_access` row (`LOGIN_INS_ACCOUNT_ACCESS`), for every realm.

    `ON DUPLICATE KEY UPDATE` rather than a plain insert because the primary key
    is `(id, RealmID)`: on the retry path an account can already carry a row,
    and the caller asked for a level, not for an error.
    """
    sql.run_statement(
        _ACCOUNTS_DB,
        f"INSERT INTO account_access (id, gmlevel, RealmID)"
        f" VALUES ({account_id}, {gm_level}, {ALL_REALMS})"
        f" ON DUPLICATE KEY UPDATE gmlevel = {gm_level}",
    )


def _account_id(sql: SqlSeam, username: str) -> int | None:
    """The id of `username`, or `None` — AzerothCore's `AccountMgr::GetId()`.

    Looked up by name, never by `LAST_INSERT_ID()`: each statement is its own
    `docker exec`, so the connection that did the insert is gone by the time
    anything could ask.
    """
    literal = _text_literal(username)
    rows = _rows(sql.query(_ACCOUNTS_DB, f"SELECT id FROM account WHERE username = {literal}"))
    return int(rows[0]) if rows else None


def _gm_level(sql: SqlSeam, account_id: int) -> int:
    """The account's GM level for all realms, or `NO_GM` when it has no `account_access` row."""
    rows = _rows(
        sql.query(
            _ACCOUNTS_DB,
            f"SELECT gmlevel FROM account_access WHERE id = {account_id}"
            f" AND RealmID = {ALL_REALMS}",
        )
    )
    return int(rows[0]) if rows else NO_GM


def _rows(output: str) -> list[str]:
    """The non-empty lines of a `--skip-column-names` result."""
    return [line.strip() for line in output.splitlines() if line.strip()]


def _checked_username(username: str) -> str:
    """The folded username, or `AccountError` explaining why it cannot be used."""
    name = fold(username.strip())
    if not name:
        raise AccountError("the account name cannot be empty")
    if len(name) > MAX_USERNAME:
        raise AccountError(
            f"the account name can be at most {MAX_USERNAME} characters, got {len(name)}"
        )
    if not _USERNAME_OK.match(name):
        raise AccountError("the account name cannot contain spaces or control characters")
    return name


def _check_password(password: str) -> None:
    """Refuse a password AzerothCore would refuse — without naming it in the error.

    The length is the only thing checked, because it is the only thing
    `AccountMgr::CreateAccount()` checks. Note `MAX_PASS_STR` is a ceiling: a
    longer password is rejected by the server, not truncated, so silently
    letting one through here would write a verifier for a password the user can
    never enter.
    """
    if not password:
        raise AccountError("the password cannot be empty")
    if len(password) > MAX_PASSWORD:
        raise AccountError(f"the password can be at most {MAX_PASSWORD} characters")


def sql_for(db_root_password: str, container: str = docker_ctl.SPEC.db) -> SqlSeam:
    """A `SqlSeam` over the WotLK database container.

    Here rather than at the call site so the account path and the module applier
    reach the database the same way (`modules.applier()` builds the same
    `DockerSql`), instead of a second connection story growing beside it.
    """
    return DockerSql(container, db_root_password)
