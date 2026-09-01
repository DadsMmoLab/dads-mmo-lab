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

import os
import re
import stat
from pathlib import Path

import pytest

from yulon import resources
from yulon.catalog import composegen
from yulon.catalog.catalog import ConfPatch, ConfPatchTable, load_catalog
from yulon.catalog.composegen import ComposeGenError
from yulon.catalog.families import conf
from yulon.catalog.installer import InstallerError
from yulon.docker import DockerCliMissingError

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


# ===================================================================================
# materialise() and apply_table(): the side of the seam that opens files
# ===================================================================================
#
# `patch()` above is pure and deliberately cannot tell an empty conf from an
# unreadable one. Everything below owns that distinction, so it is asserted
# explicitly rather than left to whichever exception happens to escape:
#
# * **empty** -> a conf with no keys in it; every key is appended. Not an error.
# * **could not be read** -> `InstallerError` naming the path, for a file that is
#   missing, is a directory, or is not UTF-8 (a `UnicodeDecodeError` is a
#   `ValueError`, not an `OSError`, so it needs saying out loud or it escapes as
#   a raw traceback out of a resume).
#
# The byte assertions are here for the same reason they are above: a `read_text()`
# comparison is vacuous about line endings because it performs the very
# translation a bug introduces. `composegen.write_plan` has that exact live defect
# filed against it, so these tests compare `read_bytes()`.


class _Image:
    """`docker.copy_from_image` over a dict of `<file>.dist` -> bytes, recording every call.

    A directory copy, as `docker cp <c>:/opt/mangos/etc <dest>` does it: `dest`
    does not exist beforehand and becomes the directory, holding its files.

    Bytes rather than text on purpose. A fake that took `str` and wrote it with
    `write_text()` would newline-translate on Windows, so the CRLF fixture below
    would arrive as LF and the test that the endings survive the round trip would
    be asserting the fake's behaviour instead of the module's.
    """

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.calls: list[tuple[str, str, Path]] = []

    def __call__(self, image: str, src: str, dest: Path) -> None:
        self.calls.append((image, src, dest))
        dest.mkdir(parents=True)
        for name, data in self.files.items():
            (dest / name).write_bytes(data)


class _Exploding:
    """A `copy_from_image` that fails the way the real one does, after being asked."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def __call__(self, image: str, src: str, dest: Path) -> None:
        self.calls += 1
        raise self.error


TABLE = ConfPatchTable(
    source_dir="/opt/mangos/etc",
    files={
        "mangosd.conf": ConfPatch(
            keys={"LoginDatabaseInfo": LOGIN, "DataDir": '"/opt/mangos/data"'}
        ),
        "realmd.conf": ConfPatch(keys={"LoginDatabaseInfo": LOGIN}),
    },
)
REALMD = 'LoginDatabaseInfo = "127.0.0.1;3306;mangos;mangos;realmd"\n'
REALMD_PATCHED = 'LoginDatabaseInfo = "tbc-db;3306;mangos;tbc-0a1b2c3d4e5f6a7b;realmd"\n'
IMAGE_FILES = {
    "mangosd.conf.dist": MANGOSD.encode("utf-8"),
    "realmd.conf.dist": REALMD.encode("utf-8"),
    # In the image and NOT in the table, to prove the other direction: only what
    # the table names is materialised, however much the image ships beside it.
    "ahbot.conf.dist": b"AuctionHouseBot.Chance.Sell = 0\n",
}


def _materialised(tmp_path: Path, files: dict[str, bytes] | None = None) -> Path:
    """An `etc/` with the table's confs in it, straight out of the fake image."""
    etc = tmp_path / "etc"
    conf.materialise(
        TABLE, image_ref="img", etc_dir=etc, copy_from_image=_Image(files or IMAGE_FILES)
    )
    return etc


# --- materialise: one copy, only the table's files, `.dist` stripped -----------------


def test_materialise_copies_the_source_dir_once_and_strips_dist(tmp_path: Path) -> None:
    image = _Image(IMAGE_FILES)
    etc = tmp_path / "etc"
    created = conf.materialise(
        TABLE, image_ref="yulon.local/wow-tbc-server:t", etc_dir=etc, copy_from_image=image
    )
    assert created == (etc / "mangosd.conf", etc / "realmd.conf")
    assert len(image.calls) == 1, "one docker round trip for however many files are missing"
    assert image.calls[0][0] == "yulon.local/wow-tbc-server:t"
    assert image.calls[0][1] == "/opt/mangos/etc"
    assert (etc / "mangosd.conf").read_bytes() == MANGOSD.encode("utf-8")
    assert not (etc / "ahbot.conf").exists(), "only files in the table are materialised"
    assert not (etc / "mangosd.conf.dist").exists()
    assert sorted(p.name for p in etc.iterdir()) == [
        "mangosd.conf",
        "realmd.conf",
    ], "no staging dir and no leftovers"


def test_materialise_makes_the_etc_dir_it_is_pointed_at(tmp_path: Path) -> None:
    """A first install has no `etc/` yet; the stage does not require one to exist."""
    etc = tmp_path / "server" / "deep" / "etc"
    conf.materialise(TABLE, image_ref="img", etc_dir=etc, copy_from_image=_Image(IMAGE_FILES))
    assert (etc / "mangosd.conf").is_file()


def test_materialise_never_recopies_an_existing_file(tmp_path: Path) -> None:
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "mangosd.conf").write_text("mine\n", encoding="utf-8")
    image = _Image(IMAGE_FILES)
    created = conf.materialise(TABLE, image_ref="img", etc_dir=etc, copy_from_image=image)
    assert created == (etc / "realmd.conf",)
    assert (etc / "mangosd.conf").read_text(encoding="utf-8") == "mine\n"
    again = conf.materialise(TABLE, image_ref="img", etc_dir=etc, copy_from_image=image)
    assert again == ()
    assert len(image.calls) == 1, "nothing missing means no docker round trip"


def test_materialise_asks_docker_for_nothing_when_every_conf_is_already_there(
    tmp_path: Path,
) -> None:
    """The resume case, and the reason it is checked before the copy and not after.

    A `copy_from_image` that is called and then discarded would still be a
    `docker create` + `cp` + `rm` per resume, against an image that may not even
    be on the machine any more.
    """
    etc = _materialised(tmp_path)
    exploding = _Exploding(AssertionError("docker must not be touched"))
    assert conf.materialise(TABLE, image_ref="img", etc_dir=etc, copy_from_image=exploding) == ()
    assert exploding.calls == 0


# --- materialise: what the image does not ship ---------------------------------------


def test_materialise_names_a_dist_the_image_does_not_ship(tmp_path: Path) -> None:
    image = _Image({"mangosd.conf.dist": MANGOSD.encode("utf-8")})
    with pytest.raises(InstallerError, match=r"/opt/mangos/etc/realmd\.conf\.dist"):
        conf.materialise(TABLE, image_ref="img", etc_dir=tmp_path / "etc", copy_from_image=image)


def test_materialise_creates_nothing_at_all_when_one_dist_is_missing(tmp_path: Path) -> None:
    """All or nothing, and the "nothing" half is the one worth a test.

    `realmd.conf.dist` is missing while `mangosd.conf.dist` is present, and the
    table lists mangosd FIRST. A loop that moved as it went would leave
    `mangosd.conf` behind - and because `materialise()` never recopies a file
    that exists, the next resume would sail past it and hand `apply_table` a
    directory that is half a server. Every missing file is checked before
    anything is moved.
    """
    etc = tmp_path / "etc"
    image = _Image({"mangosd.conf.dist": MANGOSD.encode("utf-8")})
    with pytest.raises(InstallerError):
        conf.materialise(TABLE, image_ref="img", etc_dir=etc, copy_from_image=image)
    assert list(etc.iterdir()) == [], "no half-materialised etc/ and no staging dir"


def test_materialise_says_which_image_and_which_path(tmp_path: Path) -> None:
    """A catalog bug, so the message has to name both halves of the disagreement."""
    image = _Image({"mangosd.conf.dist": MANGOSD.encode("utf-8")})
    with pytest.raises(InstallerError) as caught:
        conf.materialise(
            TABLE,
            image_ref="yulon.local/wow-tbc-server:t",
            etc_dir=tmp_path / "etc",
            copy_from_image=image,
        )
    message = str(caught.value)
    assert "yulon.local/wow-tbc-server:t" in message
    assert "/opt/mangos/etc/realmd.conf.dist" in message


def test_materialise_does_not_accept_a_directory_as_a_dist_file(tmp_path: Path) -> None:
    """`etc/realmd.conf.dist/` is not a conf; moving it would make `etc/realmd.conf` one."""
    etc = tmp_path / "etc"

    def copy(image: str, src: str, dest: Path) -> None:
        dest.mkdir(parents=True)
        (dest / "mangosd.conf.dist").write_bytes(MANGOSD.encode("utf-8"))
        (dest / "realmd.conf.dist").mkdir()

    with pytest.raises(InstallerError, match=r"realmd\.conf\.dist"):
        conf.materialise(TABLE, image_ref="img", etc_dir=etc, copy_from_image=copy)
    assert list(etc.iterdir()) == []


# --- materialise: the staging directory ----------------------------------------------


def test_materialise_leaves_no_staging_dir_when_the_copy_fails(tmp_path: Path) -> None:
    """A failed copy on every resume must not pile up half-extracted image trees."""
    etc = tmp_path / "etc"
    with pytest.raises(RuntimeError):
        conf.materialise(
            TABLE,
            image_ref="img",
            etc_dir=etc,
            copy_from_image=_Exploding(RuntimeError("daemon went away")),
        )
    assert list(etc.iterdir()) == []


def test_materialise_lets_the_copy_error_through_untouched(tmp_path: Path) -> None:
    """`copy_from_image` already keeps three outcomes apart; wrapping them loses that.

    Its `DockerCliMissingError` carries the install-Docker help and nothing else
    on purpose, and it SUBCLASSES `DockerCommandError` - so an
    `except DockerCommandError: raise InstallerError(...)` here would swallow the
    one message a user can act on into a sentence about a conf file.
    """
    original = DockerCliMissingError("no docker")
    with pytest.raises(DockerCliMissingError) as caught:
        conf.materialise(
            TABLE,
            image_ref="img",
            etc_dir=tmp_path / "etc",
            copy_from_image=_Exploding(original),
        )
    assert caught.value is original


def test_materialise_clears_a_staging_dir_an_interrupted_run_left(tmp_path: Path) -> None:
    """`docker cp` onto a `dest` that EXISTS puts the tree inside it, not at it.

    So a leftover staging directory does not just waste space - it silently moves
    the goalposts, and the copy would land at `.yulon-conf-dist/etc/`, where no
    `.dist` is ever found. Left there, every resume would fail with "the image
    does not ship this" about an image that does.
    """
    etc = tmp_path / "etc"
    stale = etc / conf._STAGING_DIR
    stale.mkdir(parents=True)
    (stale / "junk.conf.dist").write_bytes(b"stale\n")
    conf.materialise(TABLE, image_ref="img", etc_dir=etc, copy_from_image=_Image(IMAGE_FILES))
    assert sorted(p.name for p in etc.iterdir()) == ["mangosd.conf", "realmd.conf"]


def test_materialise_clears_a_leftover_staging_file_too(tmp_path: Path) -> None:
    """`rmtree` on a plain file raises; the leftover is cleared either way."""
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / conf._STAGING_DIR).write_bytes(b"not a directory\n")
    conf.materialise(TABLE, image_ref="img", etc_dir=etc, copy_from_image=_Image(IMAGE_FILES))
    assert sorted(p.name for p in etc.iterdir()) == ["mangosd.conf", "realmd.conf"]


def test_the_staging_dir_is_inside_the_etc_dir(tmp_path: Path) -> None:
    """So the move off it is a rename on one filesystem rather than a copy.

    `etc_dir` is under the user's server directory, which on Windows is routinely
    a different volume from `%TEMP%`; staging in the temp dir would turn every
    conf into a read-and-rewrite and would lose the atomicity of the move.
    """
    etc = tmp_path / "etc"
    seen: list[Path] = []

    def copy(image: str, src: str, dest: Path) -> None:
        seen.append(dest)
        dest.mkdir(parents=True)
        for name, data in IMAGE_FILES.items():
            (dest / name).write_bytes(data)

    conf.materialise(TABLE, image_ref="img", etc_dir=etc, copy_from_image=copy)
    assert seen == [etc / conf._STAGING_DIR]
    assert seen[0].parent == etc


def test_materialise_asks_for_the_source_dir_without_a_trailing_slash(tmp_path: Path) -> None:
    """`docker cp <c>:/opt/mangos/etc/ <dest>` copies the CONTENTS, not the directory.

    Same command, different result, decided by one character the catalog is free
    to write either way - and the difference only shows up against a real daemon.
    """
    table = ConfPatchTable(source_dir="/opt/mangos/etc/", files=TABLE.files)
    image = _Image(IMAGE_FILES)
    conf.materialise(table, image_ref="img", etc_dir=tmp_path / "etc", copy_from_image=image)
    assert image.calls[0][1] == "/opt/mangos/etc"


# --- materialise: file modes ---------------------------------------------------------
#
# The two `stat` tests below are the real proof and they SKIP on Windows, where
# `os.chmod` only toggles a read-only bit. That is most of this project's
# development machines, so a dropped `chmod` would sit green on a laptop until
# CI ran - and "a fix with no test that distinguishes it from the bug" is one of
# the ways this suite has been fooled before. The two beside them record the call
# instead, which says less but says it on every platform.


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_materialise_writes_owner_only_files(tmp_path: Path) -> None:
    etc = _materialised(tmp_path)
    assert stat.S_IMODE((etc / "mangosd.conf").stat().st_mode) == 0o600
    assert stat.S_IMODE((etc / "realmd.conf").stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_apply_table_writes_owner_only_files(tmp_path: Path) -> None:
    """Including over a conf that arrived world-readable from somewhere else."""
    etc = _materialised(tmp_path)
    os.chmod(etc / "realmd.conf", 0o644)
    conf.apply_table(TABLE, etc, TOKENS)
    assert stat.S_IMODE((etc / "realmd.conf").stat().st_mode) == 0o600


def _chmods(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, int]]:
    """Every `os.chmod` this module makes, recorded and still performed."""
    calls: list[tuple[Path, int]] = []
    real = os.chmod

    def record(path: object, mode: int, *args: object, **kwargs: object) -> None:
        calls.append((Path(str(path)), mode))
        real(str(path), mode)

    monkeypatch.setattr(conf.os, "chmod", record)
    return calls


def test_materialise_chmods_every_conf_it_creates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the skipped test above asserts, on a platform that cannot observe a mode."""
    calls = _chmods(monkeypatch)
    etc = _materialised(tmp_path)
    assert calls == [(etc / "mangosd.conf", conf.CONF_MODE), (etc / "realmd.conf", conf.CONF_MODE)]


def test_apply_table_chmods_the_conf_before_it_becomes_the_conf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The temporary file is what gets the mode, so the conf is never briefly readable.

    Chmod-after-rename would leave a window in which anyone on the machine could
    read the database password - short, and wide enough on a multi-user box.
    """
    etc = _materialised(tmp_path)
    calls = _chmods(monkeypatch)
    conf.apply_table(TABLE, etc, TOKENS)
    assert [mode for _, mode in calls] == [conf.CONF_MODE, conf.CONF_MODE]
    assert [path.name for path, _ in calls] == [
        f"mangosd.conf{conf._TEMP_SUFFIX}",
        f"realmd.conf{conf._TEMP_SUFFIX}",
    ]


def test_the_conf_mode_stays_owner_only_only_while_the_images_run_as_root() -> None:
    """The trap `CONF_MODE = 0o600` sets, made loud instead of silent.

    The conf files are written by the HOST user and read by the server INSIDE the
    container. `0o600` is safe today only because root bypasses POSIX permission
    checks and these images declare no `USER`. Adding one is an ordinary
    hardening change; it would make every conf unreadable to the server, and the
    symptom is "it will not boot" with nothing pointing at a permissions constant
    three modules away.

    So the two facts are held together here rather than in two files that never
    mention each other: change the images to run as a non-root user and this test
    goes red, in the same commit, naming the constant to revisit.
    """
    installers = resources.installers_dir()
    dockerfiles = sorted(installers.glob("*/native/Dockerfile.tmpl"))
    compose = sorted((installers / "shared" / "cmangos").glob("*.yml.tmpl"))
    assert dockerfiles and compose, "the templates moved; this guard is now vacuous"
    offenders = []
    for path in dockerfiles:
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.match(r"^\s*USER\s+\S", line):
                offenders.append(f"{path.name}: {line.strip()}")
    for path in compose:
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.match(r"^\s*user:\s*\S", line):
                offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, (
        f"these images no longer run as root ({offenders}), so conf.CONF_MODE = "
        f"{conf.CONF_MODE:#o} makes every conf file unreadable to the server. Widen the "
        "mode, or give the container the host user's uid."
    )


# --- apply_table: patch in place, write only on change --------------------------------


def test_apply_table_patches_in_place_and_returns_only_changed_files(tmp_path: Path) -> None:
    etc = _materialised(tmp_path)
    changed = conf.apply_table(TABLE, etc, TOKENS)
    assert changed == (etc / "mangosd.conf", etc / "realmd.conf")
    assert (etc / "realmd.conf").read_text(encoding="utf-8") == REALMD_PATCHED
    assert conf.apply_table(TABLE, etc, TOKENS) == (), "a second run changes nothing"


def test_apply_table_returns_the_files_in_table_order(tmp_path: Path) -> None:
    """The caller reports them to the user; alphabetical-by-accident is not an order."""
    table = ConfPatchTable(
        source_dir="/opt/mangos/etc",
        files={
            "realmd.conf": ConfPatch(keys={"LoginDatabaseInfo": LOGIN}),
            "mangosd.conf": ConfPatch(keys={"DataDir": '"/opt/mangos/data"'}),
        },
    )
    etc = _materialised(tmp_path)
    assert conf.apply_table(table, etc, TOKENS) == (etc / "realmd.conf", etc / "mangosd.conf")


def test_apply_table_keeps_a_users_other_edits(tmp_path: Path) -> None:
    etc = _materialised(tmp_path)
    conf.apply_table(TABLE, etc, TOKENS)
    path = etc / "mangosd.conf"
    path.write_text(path.read_text(encoding="utf-8") + "MaxPlayers = 3\n", encoding="utf-8")
    assert conf.apply_table(TABLE, etc, TOKENS) == ()
    assert path.read_text(encoding="utf-8").endswith("MaxPlayers = 3\n")


def test_apply_table_does_not_touch_a_file_it_reports_as_unchanged(tmp_path: Path) -> None:
    """ "Returned no path" and "did not write" have to be the same fact.

    Asserted through the filesystem rather than the return value: a resume over a
    finished install moves no mtime, which is what lets a user tell at a glance
    whether Yu'lon rewrote their conf.
    """
    etc = _materialised(tmp_path)
    conf.apply_table(TABLE, etc, TOKENS)
    names = sorted(p.name for p in etc.iterdir())
    os.utime(etc / "mangosd.conf", ns=(0, 0))
    assert conf.apply_table(TABLE, etc, TOKENS) == ()
    assert (etc / "mangosd.conf").stat().st_mtime_ns == 0
    assert sorted(p.name for p in etc.iterdir()) == names


def test_apply_table_writes_only_the_file_that_changed(tmp_path: Path) -> None:
    """One key drifting in one conf does not rewrite the other."""
    etc = _materialised(tmp_path)
    conf.apply_table(TABLE, etc, TOKENS)
    (etc / "realmd.conf").write_text(REALMD, encoding="utf-8")
    os.utime(etc / "mangosd.conf", ns=(0, 0))
    assert conf.apply_table(TABLE, etc, TOKENS) == (etc / "realmd.conf",)
    assert (etc / "mangosd.conf").stat().st_mtime_ns == 0


def test_apply_table_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    etc = _materialised(tmp_path)
    conf.apply_table(TABLE, etc, TOKENS)
    assert sorted(p.name for p in etc.iterdir()) == ["mangosd.conf", "realmd.conf"]


def test_apply_table_keeps_the_old_conf_when_the_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truncated conf would be the worst outcome available here.

    `materialise()` never recopies a file that exists, so a conf half-written by
    an interrupted run is one no resume ever repairs: the server reads it, takes
    defaults for everything past the cut, and boots looking healthy. The new text
    goes to a temporary file beside it and is renamed over it, so the conf on
    disk is either entirely the old one or entirely the new one.
    """
    etc = _materialised(tmp_path)
    before = (etc / "mangosd.conf").read_bytes()

    def refuse(src: object, dst: object) -> None:
        raise OSError(13, "permission denied")

    monkeypatch.setattr(conf.os, "replace", refuse)
    with pytest.raises(InstallerError, match=r"mangosd\.conf"):
        conf.apply_table(TABLE, etc, TOKENS)
    assert (etc / "mangosd.conf").read_bytes() == before
    assert sorted(p.name for p in etc.iterdir()) == ["mangosd.conf", "realmd.conf"]


# --- apply_table: empty, and could-not-be-read, are different answers -------------------


def test_apply_table_treats_an_empty_conf_as_a_conf_with_no_keys(tmp_path: Path) -> None:
    """The distinction `patch()` handed over: empty is not an error, it is a file.

    An emulator reading a conf with the key missing silently takes a default, so
    the answer to an empty file is to append every key - not to refuse, and not
    to confuse it with the files below that could not be read at all.
    """
    etc = _materialised(tmp_path)
    conf.apply_table(TABLE, etc, TOKENS)
    (etc / "realmd.conf").write_bytes(b"")
    assert conf.apply_table(TABLE, etc, TOKENS) == (etc / "realmd.conf",)
    assert (etc / "realmd.conf").read_bytes() == REALMD_PATCHED.encode("utf-8")


def test_apply_table_names_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(InstallerError, match=r"mangosd\.conf"):
        conf.apply_table(TABLE, tmp_path / "etc", TOKENS)


def test_apply_table_names_a_conf_that_is_a_directory(tmp_path: Path) -> None:
    """Present, and non-empty as far as `exists()` goes, and unreadable all the same."""
    etc = _materialised(tmp_path)
    (etc / "mangosd.conf").unlink()
    (etc / "mangosd.conf").mkdir()
    with pytest.raises(InstallerError, match=r"mangosd\.conf"):
        conf.apply_table(TABLE, etc, TOKENS)


def test_apply_table_names_a_conf_that_is_not_utf8(tmp_path: Path) -> None:
    """A `UnicodeDecodeError` is a `ValueError`, so an `except OSError` misses it.

    Not hypothetical: a user opens `mangosd.conf` in an editor that saves cp1252
    and puts one accented character in a comment. Left uncaught it escapes the
    install as a raw traceback about byte 0xe9 in position 6, which says nothing
    about which of a dozen files it came from.
    """
    etc = _materialised(tmp_path)
    (etc / "realmd.conf").write_bytes(b'# caf\xe9\nLoginDatabaseInfo = "x"\n')
    with pytest.raises(InstallerError, match=r"realmd\.conf"):
        conf.apply_table(TABLE, etc, TOKENS)


def test_the_unreadable_conf_error_keeps_the_cause(tmp_path: Path) -> None:
    """The chained exception is what says WHY - missing, a directory, a permission."""
    with pytest.raises(InstallerError) as caught:
        conf.apply_table(TABLE, tmp_path / "etc", TOKENS)
    assert isinstance(caught.value.__cause__, OSError)


# --- line endings survive the round trip, proved in bytes -------------------------------


def test_a_crlf_conf_stays_crlf_through_materialise_and_apply(tmp_path: Path) -> None:
    r"""The `aiplayerbot.conf.dist` case, end to end and in bytes.

    `read_text()`/`write_text()` would translate on the way in AND on the way
    out, so a `str` assertion passes over a file this has quietly rewritten. On
    Windows the default text-mode WRITE alone converts every `\n` to `\r\n`,
    which would corrupt the LF file in the next test while every `str`
    comparison in this module kept passing.
    """
    crlf = dict(IMAGE_FILES, **{"realmd.conf.dist": REALMD.replace("\n", "\r\n").encode("utf-8")})
    etc = _materialised(tmp_path, crlf)
    assert (etc / "realmd.conf").read_bytes() == REALMD.replace("\n", "\r\n").encode(
        "utf-8"
    ), "materialise moves the bytes, it does not rewrite them"
    conf.apply_table(TABLE, etc, TOKENS)
    out = (etc / "realmd.conf").read_bytes()
    assert out == REALMD_PATCHED.replace("\n", "\r\n").encode("utf-8")
    assert out.count(b"\n") == out.count(b"\r\n") == 1, "no mixed endings"


def test_an_lf_conf_gains_no_carriage_return_on_any_platform(tmp_path: Path) -> None:
    """The mirror image, and the one a Windows developer breaks by accident."""
    etc = _materialised(tmp_path)
    conf.apply_table(TABLE, etc, TOKENS)
    out = (etc / "mangosd.conf").read_bytes()
    assert b"\r" not in out
    assert out.endswith(b'DataDir = "/opt/mangos/data"\nDataDir2 = 5\n')


def test_an_appended_key_takes_the_crlf_files_own_ending(tmp_path: Path) -> None:
    r"""An absent key is INVENTED text, so it is where a hard-coded `\n` would land."""
    table = ConfPatchTable(
        source_dir="/opt/mangos/etc",
        files={"realmd.conf": ConfPatch(keys={"AiPlayerbot.MinRandomBots": "500"})},
    )
    crlf = {"realmd.conf.dist": REALMD.replace("\n", "\r\n").encode("utf-8")}
    etc = tmp_path / "etc"
    conf.materialise(table, image_ref="img", etc_dir=etc, copy_from_image=_Image(crlf))
    assert conf.apply_table(table, etc, TOKENS) == (etc / "realmd.conf",)
    out = (etc / "realmd.conf").read_bytes()
    assert out.endswith(b"AiPlayerbot.MinRandomBots = 500\r\n")
    assert out.count(b"\n") == out.count(b"\r\n") == 2


def test_a_crlf_conf_is_not_rewritten_on_a_resume(tmp_path: Path) -> None:
    """The `composegen.write_plan` defect, asserted against here.

    Its unchanged-file check compares `read_text()`, so a CRLF copy of an LF file
    reads back equal, the write is skipped, and the CRLF is preserved for ever.
    The same shape here would be worse: a translating read would find the patched
    LF text equal to the file's translated CRLF text and skip, leaving a conf
    reported as patched that is not - or, with a translating write, rewrite an
    already-correct file on every single resume.
    """
    crlf = dict(IMAGE_FILES, **{"realmd.conf.dist": REALMD.replace("\n", "\r\n").encode("utf-8")})
    etc = _materialised(tmp_path, crlf)
    conf.apply_table(TABLE, etc, TOKENS)
    settled = (etc / "realmd.conf").read_bytes()
    assert conf.apply_table(TABLE, etc, TOKENS) == ()
    assert (etc / "realmd.conf").read_bytes() == settled


# --- the two together, on the shipped catalog -------------------------------------------


def test_the_shipped_tbc_table_materialises_and_patches_from_its_own_dist_names(
    tmp_path: Path,
) -> None:
    """The real `catalog.json` table, driving the real seam.

    A `source_dir` or a file name that no image ships is a catalog bug that
    surfaces an hour into an install; the shape of it is checkable here.
    """
    entry = load_catalog().get("wow-tbc")
    native = entry.install.native
    assert native is not None and native.cmangos is not None
    table = native.cmangos.conf
    image = _Image({f"{name}{conf.DIST_SUFFIX}": b"" for name in table.files})
    etc = tmp_path / "etc"
    created = conf.materialise(table, image_ref="img", etc_dir=etc, copy_from_image=image)
    assert created == tuple(etc / name for name in table.files)
    assert image.calls[0][1] == table.source_dir
    tokens = {
        **composegen.entry_tokens(entry),
        "DB_PASSWORD": "pw-0a1b2c3d4e5f6a7b",
        "REALM_HOST": "127.0.0.1",
        "AUTH_PORT": "3724",
        "WORLD_PORT": "8085",
        "DB_PORT": "3306",
    }
    assert conf.apply_table(table, etc, tokens) == created
    for name, file_table in table.files.items():
        text = (etc / name).read_text(encoding="utf-8")
        assert "{{" not in text
        for key in file_table.keys:
            assert f"{key} = " in text
