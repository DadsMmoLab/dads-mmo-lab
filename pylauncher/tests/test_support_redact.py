"""The redactor (T93): passwords and the home folder out of text, nothing else changed."""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from yulon.support.redact import MASK, Redactor, database_info_passwords


def test_the_longest_known_value_wins_where_one_contains_another() -> None:
    short = "Abcdefgh" + secrets.token_hex(2)
    long = short + "XYZ9"
    redactor = Redactor.build([short, long])
    # Shorter-first would leave `***XYZ9` behind: the tail of the longer secret.
    assert redactor.redact(f"a {long} b {short} c") == f"a {MASK} b {MASK} c"


def test_line_endings_survive_every_rule() -> None:
    text = (
        'one\r\nLoginDatabaseInfo = "db;3306;mangos;zz9plural;realmd"\r\nAdminPassword = hunter\r\n'
    )
    assert Redactor.build([]).redact(text) == (
        f'one\r\nLoginDatabaseInfo = "db;3306;mangos;{MASK};realmd"\r\nAdminPassword = {MASK}\r\n'
    )


def test_a_database_info_password_is_masked_even_when_nobody_knew_it() -> None:
    line = "Cannot connect to world database tbc-db;3306;mangos;Unknown-Value-1;mangos"
    assert Redactor.build([]).redact(line) == (
        f"Cannot connect to world database tbc-db;3306;mangos;{MASK};mangos"
    )


def test_an_empty_database_info_password_stays_empty() -> None:
    assert Redactor.build([]).redact("h;3306;u;;s") == "h;3306;u;;s"


def test_the_generated_shape_is_masked_without_being_known() -> None:
    generated = "vanilla-" + secrets.token_hex(8)
    assert Redactor.build([]).redact(f"pw={generated}.") == f"pw={MASK}."


def test_a_forty_digit_commit_is_not_mistaken_for_a_generated_password() -> None:
    commit = "rev-" + "0123456789abcdef" * 2 + "01234567"
    assert Redactor.build([]).redact(commit) == commit


def test_password_settings_are_masked_in_every_spelling() -> None:
    redactor = Redactor.build([])
    assert redactor.redact('Ra.Password = "abc def"') == f"Ra.Password = {MASK}"
    assert redactor.redact("DB_ROOT_PASSWORD=abc") == f"DB_ROOT_PASSWORD={MASK}"
    assert redactor.redact('{"password": "abc"}') == f'{{"password": {MASK}}}'
    assert redactor.redact('SOAP.Password = ""') == 'SOAP.Password = ""'


def test_a_short_known_value_is_masked_only_as_a_whole_token() -> None:
    redactor = Redactor.build(["acore"])
    assert redactor.redact("ac-db;3306;acore;acore;acore_auth") == (
        f"ac-db;3306;{MASK};{MASK};acore_auth"
    )


def test_a_value_shorter_than_the_token_floor_is_ignored() -> None:
    assert Redactor.build(["abc"]).redact("abc abcd") == "abc abcd"


def test_the_home_folder_becomes_a_tilde_in_both_slash_spellings() -> None:
    redactor = Redactor.build([], home=Path("/home/user"))
    assert redactor.redact("/home/user/wow and /home/user\\wow") == "~/wow and ~\\wow"


def test_a_longer_user_name_sharing_the_home_prefix_is_left_alone() -> None:
    redactor = Redactor.build([], home=Path("/home/user"))
    assert redactor.redact("/home/username/x") == "/home/username/x"


LINEAR_BOUND_S = 2.0
"""Seconds. A linear redaction of 400 KB takes ~0.1 s; the quadratic one took minutes."""


def test_a_long_line_without_spaces_is_redacted_in_linear_time() -> None:
    """A base64 blob or a minified dump in a log must not hang the Save button."""
    blob = secrets.token_urlsafe(300_000)
    started = time.monotonic()
    Redactor.build(["Known" + secrets.token_hex(4)]).redact(blob)
    assert time.monotonic() - started < LINEAR_BOUND_S


def test_database_info_passwords_reads_the_fourth_field_of_every_line() -> None:
    conf = (
        'LoginDatabaseInfo = "h;3306;mangos;first-secret;realmd"\n'
        '# WorldDatabaseInfo = "h;3306;mangos;commented;world"\n'
        "WorldDatabaseInfo = h;3306;mangos;second-secret;world\r\n"
        'LogsDatabaseInfo = "h;3306;mangos;;logs"\n'
    )
    assert database_info_passwords(conf) == {"first-secret", "second-secret"}
