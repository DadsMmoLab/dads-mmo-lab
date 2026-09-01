"""Tests for the ini conf patcher (`yulon.catalog.families.conf`).

Pure text in, pure text out, asserted byte for byte: a conf file is what the
emulator reads at boot, so "close enough" is a server that will not start.
The fixtures are shaped like the real `mangosd.conf.dist` / `realmd.conf.dist`
that ship in the CMaNGOS images, including the commented-out `SyncLevel` keys
the Vanilla installer's seds relied on uncommenting.

Two things here are about bytes rather than behaviour, and both are defects
this project has shipped in neighbouring modules already:

* **Line endings are the subject.** The real files disagree with each other:
  `mangosd.conf.dist.in` and `realmd.conf.dist.in` in `cmangos/mangos-classic`
  are LF, and `playerbot/aiplayerbot.conf.dist.in` in `cmangos/playerbots` is
  CRLF throughout (4862 of 4862 newlines, checked against the upstream file) —
  so ONE install patches both, and a patcher with one hard-coded newline mixes
  endings in whichever file it guessed wrong about. Every ending assertion here
  therefore encodes to UTF-8 and compares BYTES: `str` comparison would pass on
  the very substitution a bug introduces once the text has been through a
  `read_text()` anywhere, because that call translates `\\r\\n` to `\\n`.
* **A key has three states, not two** — absent, present-but-commented, and
  present-and-set — and each has its own test here, in both the
  `match_commented` and the default mood. A conf carrying both `# Foo = 1` and
  `Foo = 2` is a file nobody can reason about, so "found the comment, appended
  anyway" is asserted against explicitly.
"""

from __future__ import annotations

import pytest

from yulon.catalog import composegen
from yulon.catalog.catalog import ConfPatch, load_catalog
from yulon.catalog.composegen import ComposeGenError
from yulon.catalog.families import conf
from yulon.catalog.installer import InstallerError

TOKENS = {
    "DB_HOST": "tbc-db",
    "DB_USER": "mangos",
    "DB_PASSWORD": "tbc-0a1b2c3d4e5f6a7b",
    "AUTH_DB": "realmd",
    "WORLD_DB": "mangos",
}

MANGOSD = (
    "[MangosdConf]\n"
    "ConfVersion=2010062001\n"
    "\n"
    "###################################\n"
    "# Connections and access\n"
    "###################################\n"
    'LoginDatabaseInfo     = "127.0.0.1;3306;mangos;mangos;realmd"\n'
    'WorldDatabaseInfo     = "127.0.0.1;3306;mangos;mangos;mangos"\n'
    'DataDir = "."\n'
    "DataDir2 = 5\n"
)
"""The aligned `Key     = value` spelling and the `DataDir` / `DataDir2` pair are both
copied from the real `mangos-classic` `mangosd.conf.dist.in`: the alignment is what a
rewrite collapses, and the pair is the prefix collision the match must not make."""

# The Vanilla `aiplayerbot.conf.dist` shape: the key present only as a comment.
VANILLA_SYNC = (
    "#    SyncLevel\n"
    "#        Level of synchronisation between world and logon\n"
    "#        Default: 0\n"
    "#\n"
    "#SyncLevel = 0\n"
    "# SyncLevel.Server = 0\n"
    "\n"
    'DataDir = "."\n'
)
"""Both comment spellings the real file uses — `#Key` and `# Key` — and three lines of
prose above them that name the key WITHOUT an `=`, which is what stops a looser regex
from uncommenting a sentence."""

LOGIN = '"{{DB_HOST}};3306;{{DB_USER}};{{DB_PASSWORD}};{{AUTH_DB}}"'


# --- the value is rewritten in place -----------------------------------------------


def test_patch_rewrites_active_keys_byte_for_byte() -> None:
    table = ConfPatch(keys={"LoginDatabaseInfo": LOGIN, "DataDir": '"/opt/mangos/data"'})
    assert conf.patch(MANGOSD, table, TOKENS) == (
        "[MangosdConf]\n"
        "ConfVersion=2010062001\n"
        "\n"
        "###################################\n"
        "# Connections and access\n"
        "###################################\n"
        'LoginDatabaseInfo = "tbc-db;3306;mangos;tbc-0a1b2c3d4e5f6a7b;realmd"\n'
        'WorldDatabaseInfo     = "127.0.0.1;3306;mangos;mangos;mangos"\n'
        'DataDir = "/opt/mangos/data"\n'
        "DataDir2 = 5\n"
    )


def test_patch_rewrites_every_active_spelling_of_one_key() -> None:
    """A conf that sets a key twice is patched twice.

    The emulator reads such a file top to bottom and the LAST assignment wins, so
    rewriting only the first would leave the original value in force further down —
    a patch that reports success and changes nothing the server sees.
    """
    text = "Foo = 1\nDataDir = 1\nBar = 2\nDataDir=2\n"
    table = ConfPatch(keys={"DataDir": "9"})
    assert conf.patch(text, table, {}) == "Foo = 1\nDataDir = 9\nBar = 2\nDataDir = 9\n"


def test_patch_fills_tokens_in_values_only_and_never_in_the_file() -> None:
    """A literal `{{...}}` already in the conf survives untouched.

    `fill()` refuses an unfilled placeholder, so a patcher that ran it over the WHOLE
    file would refuse any conf whose comments mention the token grammar — and would
    silently rewrite one whose comments happened to name a real token.
    """
    text = "# see {{DB_HOST}} in the docs\nOther = {{DB_USER}}\nDataDir = 1\n"
    table = ConfPatch(keys={"DataDir": "{{DB_HOST}}"})
    assert conf.patch(text, table, TOKENS) == (
        "# see {{DB_HOST}} in the docs\nOther = {{DB_USER}}\nDataDir = tbc-db\n"
    )


def test_patch_keeps_a_rewritten_last_line_unterminated() -> None:
    """A file with no final newline keeps having none; the rewrite adds nothing."""
    assert conf.patch("Foo = 1\nDataDir = 1", ConfPatch(keys={"DataDir": "2"}), {}) == (
        "Foo = 1\nDataDir = 2"
    )


# --- the key is absent: it is appended ---------------------------------------------


def test_patch_appends_a_key_the_file_does_not_have() -> None:
    table = ConfPatch(keys={"AiPlayerbot.MinRandomBots": "1600"})
    assert conf.patch("Foo = 1\n", table, {}) == "Foo = 1\nAiPlayerbot.MinRandomBots = 1600\n"


def test_patch_appends_after_a_missing_final_newline() -> None:
    table = ConfPatch(keys={"Bar": "2"})
    assert conf.patch("Foo = 1", table, {}) == "Foo = 1\nBar = 2\n"


def test_patch_appends_to_an_empty_file_rather_than_refusing_it() -> None:
    """Empty text is a conf with no keys in it, not an error.

    The third answer — a file that could not be READ — is not this function's to give:
    `patch()` takes text and never touches the filesystem, so it cannot tell an empty
    file from an unreadable one and does not pretend to. `materialise()` (J.2) owns
    that distinction, on the side of the seam that does the opening.
    """
    assert conf.patch("", ConfPatch(keys={"Foo": "1"}), {}) == "Foo = 1\n"


def test_patch_does_not_treat_a_longer_key_as_the_key() -> None:
    """`DataDir2 = 5` is not `DataDir`, so `DataDir` is still absent and gets appended."""
    assert conf.patch("DataDir2 = 5\n", ConfPatch(keys={"DataDir": "1"}), {}) == (
        "DataDir2 = 5\nDataDir = 1\n"
    )


def test_patch_treats_the_dot_in_a_key_as_a_dot() -> None:
    """Nearly every key these files patch has one — `AiPlayerbot.*`, Tortoise's `*.Info`.

    Unescaped in a regex a `.` matches any character, so the key would also claim a
    line belonging to a different setting, and the real one would be left at its
    default with the patch reporting success.
    """
    text = "AiPlayerbotXMinRandomBots = 1\n"
    table = ConfPatch(keys={"AiPlayerbot.MinRandomBots": "500"}, match_commented=True)
    assert conf.patch(text, table, {}) == (
        "AiPlayerbotXMinRandomBots = 1\nAiPlayerbot.MinRandomBots = 500\n"
    )


def test_patch_does_not_recognise_an_indented_key() -> None:
    """Keys are matched at column 0, which is where every shipped conf writes them.

    An indented spelling is left exactly as it stands and the key is appended below
    it — deliberate, because the alternative is a regex that would also fire on the
    `#    SyncLevel` prose lines the real files are full of.
    """
    assert conf.patch("  DataDir = 1\n", ConfPatch(keys={"DataDir": "2"}), {}) == (
        "  DataDir = 1\nDataDir = 2\n"
    )


# --- the key is commented: left alone, or uncommented in place ----------------------


def test_patch_leaves_a_commented_key_alone_unless_told_to_match_it() -> None:
    table = ConfPatch(keys={"SyncLevel": "1"})
    assert conf.patch(VANILLA_SYNC, table, {}) == VANILLA_SYNC + "SyncLevel = 1\n"


def test_patch_uncomments_the_vanilla_synclevel_keys_in_place() -> None:
    table = ConfPatch(keys={"SyncLevel": "1", "SyncLevel.Server": "2"}, match_commented=True)
    assert conf.patch(VANILLA_SYNC, table, {}) == (
        "#    SyncLevel\n"
        "#        Level of synchronisation between world and logon\n"
        "#        Default: 0\n"
        "#\n"
        "SyncLevel = 1\n"
        "SyncLevel.Server = 2\n"
        "\n"
        'DataDir = "."\n'
    )


def test_patch_uncomments_a_commented_key_exactly_once() -> None:
    """The first commented spelling wins; a second stays a comment.

    Uncommenting every one would turn a documented alternative into a second live
    assignment — and in these files the later one usually carries the upstream
    default, so it would win and quietly undo the patch.
    """
    text = "#SyncLevel = 0\n# SyncLevel = 7\nDataDir = 1\n"
    table = ConfPatch(keys={"SyncLevel": "1"}, match_commented=True)
    assert conf.patch(text, table, {}) == "SyncLevel = 1\n# SyncLevel = 7\nDataDir = 1\n"


def test_patch_does_not_uncomment_a_key_mentioned_inside_a_sentence() -> None:
    """A commented key is one at column 0; a `#Key =` further along a line is prose.

    Uncommenting is a whole-line replacement, so a match found mid-line does not
    reveal the key — it DELETES the sentence that mentioned it, and these files are
    mostly sentences.
    """
    text = "# use SyncLevel; the shipped default is #SyncLevel = 0\nDataDir = 1\n"
    table = ConfPatch(keys={"SyncLevel": "1"}, match_commented=True)
    assert conf.patch(text, table, {}) == (
        "# use SyncLevel; the shipped default is #SyncLevel = 0\nDataDir = 1\nSyncLevel = 1\n"
    )


def test_patch_prefers_the_active_line_over_a_commented_twin() -> None:
    text = "#SyncLevel = 0\nSyncLevel = 5\n"
    table = ConfPatch(keys={"SyncLevel": "1"}, match_commented=True)
    assert conf.patch(text, table, {}) == "#SyncLevel = 0\nSyncLevel = 1\n"


def test_patch_never_appends_beside_a_comment_it_just_uncommented() -> None:
    """The three states stay apart: uncommenting IS the write, not a step before one."""
    table = ConfPatch(keys={"SyncLevel": "1"}, match_commented=True)
    out = conf.patch("#SyncLevel = 0\n", table, {})
    assert out == "SyncLevel = 1\n"
    assert out.count("SyncLevel") == 1


# --- line endings, proved in bytes -------------------------------------------------


def test_patch_preserves_crlf_line_endings() -> None:
    text = 'DataDir = "."\r\nFoo = 1\r\n'
    table = ConfPatch(keys={"DataDir": '"/opt/mangos/data"', "Bar": "2"})
    assert conf.patch(text, table, {}).encode("utf-8") == (
        b'DataDir = "/opt/mangos/data"\r\nFoo = 1\r\nBar = 2\r\n'
    )


def test_patch_never_mixes_endings_in_a_crlf_file() -> None:
    """Every `\\n` in the result is part of a `\\r\\n` — the assertion a `str` cannot make.

    This is the shape of the real `aiplayerbot.conf.dist`, which is CRLF while the
    `mangosd.conf.dist` beside it is LF: one install patches both, and a bare `"\\n"`
    anywhere in the appended or rewritten lines is a mixed-ending conf.
    """
    text = "AiPlayerbot.MinRandomBots = 1000\r\n# AiPlayerbot.SyncLevelWithPlayers = 0\r\n"
    table = ConfPatch(
        keys={
            "AiPlayerbot.MinRandomBots": "500",
            "AiPlayerbot.SyncLevelWithPlayers": "1",
            "AiPlayerbot.RandomBotAccountCount": "100",
        },
        match_commented=True,
    )
    out = conf.patch(text, table, {}).encode("utf-8")
    assert out == (
        b"AiPlayerbot.MinRandomBots = 500\r\n"
        b"AiPlayerbot.SyncLevelWithPlayers = 1\r\n"
        b"AiPlayerbot.RandomBotAccountCount = 100\r\n"
    )
    assert out.count(b"\n") == out.count(b"\r\n") == 3


def test_patch_terminates_an_unterminated_crlf_file_with_crlf() -> None:
    """The newline the append INVENTS is the file's own, not the platform's.

    Reading the file's ending off its first line rather than its last is what makes
    this case work at all: the last line has no ending to copy.
    """
    out = conf.patch("Foo = 1\r\nBar = 2", ConfPatch(keys={"Baz": "3"}), {}).encode("utf-8")
    assert out == b"Foo = 1\r\nBar = 2\r\nBaz = 3\r\n"
    assert out.count(b"\n") == out.count(b"\r\n") == 3


def test_patch_appends_lf_to_an_lf_file_on_any_platform() -> None:
    """The mirror image: no stray `\\r` where the file never had one.

    On Windows the tempting spellings (`os.linesep`, a text-mode write) would put one
    here, and every `str` assertion in this file would still pass.
    """
    out = conf.patch("Foo = 1\n", ConfPatch(keys={"Bar": "2"}), {}).encode("utf-8")
    assert out == b"Foo = 1\nBar = 2\n"
    assert b"\r" not in out


def test_patch_appends_lf_to_an_empty_file() -> None:
    """An empty file has no ending to copy; `\\n` is the documented answer."""
    assert conf.patch("", ConfPatch(keys={"Foo": "1"}), {}).encode("utf-8") == b"Foo = 1\n"


# --- running it twice ---------------------------------------------------------------


def test_patch_is_idempotent() -> None:
    table = ConfPatch(keys={"LoginDatabaseInfo": LOGIN, "New": "x"}, match_commented=True)
    once = conf.patch(MANGOSD, table, TOKENS)
    assert conf.patch(once, table, TOKENS) == once


def test_patch_is_idempotent_on_a_crlf_file_in_bytes() -> None:
    """A reinstall must not grow a second copy of an appended key, or a lone `\\n`."""
    text = "Foo = 1\r\n#SyncLevel = 0\r\n"
    table = ConfPatch(keys={"SyncLevel": "1", "New": "x"}, match_commented=True)
    once = conf.patch(text, table, {})
    twice = conf.patch(once, table, {})
    assert twice.encode("utf-8") == once.encode("utf-8")
    assert once.encode("utf-8") == b"Foo = 1\r\nSyncLevel = 1\r\nNew = x\r\n"


# --- values that cannot be written ---------------------------------------------------


def test_patch_refuses_an_unknown_token() -> None:
    table = ConfPatch(keys={"LoginDatabaseInfo": '"{{NOPE}}"'})
    with pytest.raises(InstallerError, match="LoginDatabaseInfo"):
        conf.patch(MANGOSD, table, TOKENS)


def test_the_unknown_token_error_keeps_the_composegen_cause() -> None:
    """One `{{TOKEN}}` grammar (A6), so the refusal is `fill()`'s, re-raised with the key.

    Chaining matters: `fill()`'s message is the one that says WHICH placeholder, and
    the key is what says which conf line to look at. Losing either leaves a user
    reading "unfilled placeholder" about a file with forty of them.
    """
    table = ConfPatch(keys={"DataDir": "{{NOPE}}"})
    with pytest.raises(InstallerError) as caught:
        conf.patch(MANGOSD, table, TOKENS)
    assert isinstance(caught.value.__cause__, ComposeGenError)
    assert "NOPE" in str(caught.value.__cause__)


def test_patch_refuses_a_value_that_would_splice_a_conf_line() -> None:
    """A newline in a value writes keys nobody declared; it is refused, not written.

    Silently, this is the worst thing this module could do: `Foo = 1\\nGmLevel = 3`
    is one patched key and one key the catalog never mentioned, and the file that
    results is valid ini that the emulator obeys.
    """
    table = ConfPatch(keys={"DataDir": "1\nGmLevel = 3"})
    with pytest.raises(InstallerError, match="DataDir"):
        conf.patch(MANGOSD, table, {})


def test_patch_refuses_a_line_break_that_arrives_through_a_token() -> None:
    """The check is on the FILLED value, so a token cannot smuggle one past it."""
    table = ConfPatch(keys={"DataDir": "{{EVIL}}"})
    with pytest.raises(InstallerError, match="DataDir"):
        conf.patch(MANGOSD, table, {"EVIL": "1\r\nGmLevel = 3"})


# --- the constants J.2 consumes, and the shipped catalog ------------------------------


def test_the_conf_files_this_module_writes_are_owner_only() -> None:
    """The database password is in them, so J.2 writes `0o600` and nothing wider."""
    assert conf.CONF_MODE == 0o600
    assert conf.DIST_SUFFIX == ".dist"


def test_every_shipped_cmangos_conf_value_fills_from_the_entry_tokens() -> None:
    """The real `catalog.json` tables, through the real `patch()`.

    A conf value naming a token `entry_tokens()` does not produce is a catalog typo
    that no unit fixture would catch, and it surfaces at install time as a refused
    stage on a machine that has already compiled the core for an hour.
    """
    catalog = load_catalog()
    seen = 0
    for game in ("wow-tbc", "wow-vanilla", "wow-tortoise"):
        entry = catalog.get(game)
        native = entry.install.native
        assert native is not None and native.cmangos is not None
        tokens = {
            **composegen.entry_tokens(entry),
            "DB_PASSWORD": "pw-0a1b2c3d4e5f6a7b",
            "REALM_HOST": "127.0.0.1",
            "AUTH_PORT": "3724",
            "WORLD_PORT": "8085",
            "DB_PORT": "3306",
        }
        for name, table in native.cmangos.conf.files.items():
            out = conf.patch("", table, tokens)
            assert "{{" not in out, f"{game}/{name} left a placeholder"
            for key in table.keys:
                assert f"{key} = " in out
            seen += 1
    assert seen >= 9
