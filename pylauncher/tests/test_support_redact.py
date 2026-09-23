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


def test_the_dotted_key_spelling_of_a_native_tortoise_conf_is_read_and_masked() -> None:
    """Tortoise's native `mangosd.conf` spells the keys `LoginDatabase.Info` (catalog.json)."""
    conf = (
        'LoginDatabase.Info = "h;3306;mangos;dotted-secret;realmd"\n'
        "CharacterDatabase.Info = h;3306;mangos;bare-secret;characters\n"
        '# WorldDatabase.Info = "h;3306;mangos;commented;world"\n'
        'LogsDatabase.Info = "h;3306;mangos;;logs"\n'
    )
    assert database_info_passwords(conf) == {"dotted-secret", "bare-secret"}
    assert Redactor.build([]).redact(conf) == (
        f'LoginDatabase.Info = "h;3306;mangos;{MASK};realmd"\n'
        f"CharacterDatabase.Info = h;3306;mangos;{MASK};characters\n"
        f'# WorldDatabase.Info = "h;3306;mangos;{MASK};world"\n'
        'LogsDatabase.Info = "h;3306;mangos;;logs"\n'
    )


def test_mysql_access_denied_keeps_whether_a_password_was_sent() -> None:
    """MySQL 1045 is the commonest database failure; `YES`/`NO` is its one useful word.

    The value is not a password, and the `)` after it is not part of one.
    """
    redactor = Redactor.build([])
    for sent in ("YES", "NO", "yes"):
        line = (
            "ERROR 1045 (28000): Access denied for user 'mangos'@'172.18.0.3' "
            f"(using password: {sent})"
        )
        assert redactor.redact(line) == line


def test_a_password_setting_still_masks_a_real_value_and_leaves_a_closing_paren() -> None:
    redactor = Redactor.build([])
    assert redactor.redact("Password = hunter2x") == f"Password = {MASK}"
    assert redactor.redact("password: s3cret") == f"password: {MASK}"
    assert redactor.redact("--password=abcdef12") == f"--password={MASK}"
    assert redactor.redact("(password: s3cret)") == f"(password: {MASK})"
    assert redactor.redact("Password = NOPE") == f"Password = {MASK}"


def test_a_semicolon_list_that_is_not_database_info_is_left_alone() -> None:
    """A build log's module list has five `;` fields too, but no port in the second."""
    line = "modules: mod-a;mod-b;mod-c;mod-d;mod-e"
    assert Redactor.build([]).redact(line) == line
    assert Redactor.build([]).redact("MODULES=mod-a;mod-b;mod-c;mod-d;mod-e") == (
        "MODULES=mod-a;mod-b;mod-c;mod-d;mod-e"
    )


def test_database_info_is_masked_by_address_hostname_and_socket() -> None:
    redactor = Redactor.build([])
    assert redactor.redact("127.0.0.1;3306;mangos;Pw9unknown;realmd") == (
        f"127.0.0.1;3306;mangos;{MASK};realmd"
    )
    assert redactor.redact("x=ac-database;3306;acore;Pw9unknown;acore_auth") == (
        f"x=ac-database;3306;acore;{MASK};acore_auth"
    )
    # AzerothCore's own conf documents `.;/path/to/unix_socket;user;password;database`.
    assert redactor.redact('".;/var/run/mysqld/mysqld.sock;acore;Pw9unknown;acore_auth"') == (
        f'".;/var/run/mysqld/mysqld.sock;acore;{MASK};acore_auth"'
    )
