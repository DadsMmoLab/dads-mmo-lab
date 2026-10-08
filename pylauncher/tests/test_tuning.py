"""Tests for `yulon.tuning` — the Tuning tab's reading, writing and guard (T43).

No Qt anywhere in this file: everything the tab decides about a setting is
decided here, against a directory on disk, so the rules are assertable without
a `QApplication`. The widgets are `tests/test_tuning_panel.py`'s.
"""

from __future__ import annotations

import os
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

import pytest

from yulon import tuning
from yulon.manifest import Manifest, parse_manifest

BASE: dict[str, Any] = {
    "schema_version": 1,
    "id": "mod-beast",
    "name": "NPC Beastmaster",
    "type": "module",
    "game": "wow-wotlk",
    "description": "Pets for every class.",
    "source": {"repo": "azerothcore/mod-npc-beastmaster"},
}


def _manifest(**over: Any) -> Manifest:
    return parse_manifest({**BASE, **over})


def _conf(file: str, keys: list[dict[str, Any]]) -> dict[str, Any]:
    return {"file": file, "keys": keys}


CONF = "env/dist/etc/modules/mod_npc_beastmaster.conf"


def _never(path: Path) -> NoReturn:
    """A `_read` that must not be called: `(DB table)` and a glob are not paths."""
    raise AssertionError(f"the reader opened {path}, which is not a file it may open")


def _write(server_dir: Path, rel: str, text: str) -> Path:
    path = server_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# -- point 2: the reader ----------------------------------------------------


def test_only_an_installed_module_contributes_rows(tmp_path: Path) -> None:
    """An uninstalled module has no file to tune, so it has no row."""
    here = _manifest(conf=[_conf(CONF, [{"key": "BeastMaster.Enable"}])])
    gone = _manifest(
        id="mod-other",
        name="Other",
        conf=[_conf("env/dist/etc/modules/other.conf", [{"key": "A"}])],
    )
    rows = tuning.rows_for([here, gone], {"module": frozenset({"mod-beast"})}, tmp_path)
    assert [row.module_id for row in rows] == ["mod-beast"]
    assert all(row.installed for row in rows)


def test_a_row_carries_every_field_the_tab_draws(tmp_path: Path) -> None:
    _write(server_dir := tmp_path, CONF, "[worldserver]\nBeastMaster.MinLevel = 30\n")
    manifest = _manifest(
        conf=[
            _conf(
                CONF,
                [
                    {
                        "key": "BeastMaster.MinLevel",
                        "default": "10",
                        "label": "Minimum level",
                        "explain": "Level a character must reach before adopting a pet.",
                        "type": "int",
                        "min": 0,
                        "max": 80,
                    }
                ],
            )
        ]
    )
    (row,) = tuning.rows_for([manifest], {"module": frozenset({"mod-beast"})}, server_dir)
    assert row.module_id == "mod-beast"
    assert row.module_name == "NPC Beastmaster"
    assert row.family == "module"
    assert row.file == CONF
    assert row.key == "BeastMaster.MinLevel"
    assert row.label == "Minimum level"
    assert row.explain == "Level a character must reach before adopting a pet."
    assert (row.type, row.min, row.max) == ("int", 0, 80)
    assert row.default == "10"
    assert row.current == "30"
    assert row.backend == "conf"
    assert row.read_only_reason is None


def test_a_key_with_no_label_is_drawn_under_its_own_key(tmp_path: Path) -> None:
    manifest = _manifest(conf=[_conf(CONF, [{"key": "BeastMaster.Enable"}])])
    (row,) = tuning.rows_for([manifest], {"module": frozenset({"mod-beast"})}, tmp_path)
    assert row.label == "BeastMaster.Enable"
    assert row.explain is None and row.type is None


def test_a_conf_that_is_not_there_gives_no_current_value_and_still_lists(tmp_path: Path) -> None:
    """Never a fabricated `current`: the row shows its default and says nothing else."""
    manifest = _manifest(conf=[_conf(CONF, [{"key": "BeastMaster.Enable", "default": "1"}])])
    (row,) = tuning.rows_for([manifest], {"module": frozenset({"mod-beast"})}, tmp_path)
    assert row.current is None and row.default == "1"


def test_a_conf_that_cannot_be_read_gives_no_current_value(tmp_path: Path) -> None:
    (tmp_path / CONF).mkdir(parents=True)  # a directory where the file should be
    manifest = _manifest(conf=[_conf(CONF, [{"key": "BeastMaster.Enable"}])])
    (row,) = tuning.rows_for([manifest], {"module": frozenset({"mod-beast"})}, tmp_path)
    assert row.current is None


def test_the_first_assignment_wins_in_a_conf_and_a_commented_one_is_not_read(
    tmp_path: Path,
) -> None:
    """AzerothCore's own rule, read off `Config.cpp:305-331` rather than inherited.

    `ParseFile` trims the whole line, skips it only when it is empty or starts
    with `#` or `[`, splits on the FIRST `=`, and then `IsDuplicateOption`
    SKIPS every later copy of a key it has already seen — first occurrence
    wins, and the later ones are logged as an error.

    Two of those three contradict what this module first did, which was
    `party.read_conf()`'s rule for a Lua script applied to a `.conf`:
    last-wins (wrong: the server takes the first) and column-0 only (wrong: the
    line is trimmed before anything is decided, so an INDENTED assignment is
    live).
    """
    _write(
        tmp_path,
        CONF,
        # A commented copy, the live one, a later duplicate the server ignores,
        # and an INDENTED assignment of a second key. The last two are what this
        # test turns on; the comment is excluded by the exact key match rather
        # than by the comment guard (see `_key_line`).
        "# BeastMaster.Enable = 9\n"
        "BeastMaster.Enable = 1\n"
        "BeastMaster.Enable = 0\n"
        '  BeastMaster.Name = "White Fang"\n',
    )
    manifest = _manifest(
        conf=[_conf(CONF, [{"key": "BeastMaster.Enable"}, {"key": "BeastMaster.Name"}])]
    )
    rows = tuning.rows_for([manifest], {"module": frozenset({"mod-beast"})}, tmp_path)
    # "1" and not "0": the duplicate below it is what the server logs as an
    # error and skips. "White Fang" although the line is INDENTED: the core
    # trims before it decides anything.
    assert [row.current for row in rows] == ["1", "White Fang"]


def test_a_comment_and_a_section_header_are_both_lines_that_say_nothing() -> None:
    """`Config.cpp:310-314`, asserted where it can actually be falsified.

    The `#` arm decides real answers — a commented copy of a key above the live
    one is the shape `test_the_first_assignment_wins...` and
    `test_the_first_assignment_is_rewritten...` both turn on. The `[` arm
    decides none of them and is here for fidelity: a `[` binds to the first
    token, so `[worldserver]` strips to `[worldserver` and could never equal a
    bare key anyway. Pinned directly rather than through a file, because a test
    that routed it through `rows_for` would pass with the arm deleted and claim
    to be guarding it.
    """
    assert tuning._is_conf_comment("[worldserver]")
    assert tuning._is_conf_comment("  [authserver]  ")
    assert tuning._is_conf_comment("# K = 9")
    assert tuning._is_conf_comment("   ")
    assert not tuning._is_conf_comment("K = 1")


def test_a_lua_script_keeps_luas_own_rule_and_not_the_cores(tmp_path: Path) -> None:
    """Two languages, two rules, and the row's backend is what picks.

    Lua is last-assignment-wins and comments with `--`, so the AzerothCore rule
    would read a Lua script's last word as its first. DML's own Lua reader
    (`crates/dml-wow/src/tuning.rs`, `lua_cfg_read`) takes the last for exactly
    this reason.
    """
    lua = "env/dist/etc/modules/lua_scripts/SitMeansRest.lua"
    _write(tmp_path, lua, "DURATION = 20\nDURATION = 30\n-- DURATION = 99\n")
    manifest = _manifest(
        id="sitmeanrest", name="Sit", type="ale", conf=[_conf(lua, [{"key": "DURATION"}])]
    )
    (row,) = tuning.rows_for([manifest], {"ale": frozenset({"sitmeanrest"})}, tmp_path)
    assert row.current == "30"


def test_a_lua_backed_key_is_listed_read_only_and_says_why(tmp_path: Path) -> None:
    """T43 decision 5: never silently dropped, and never written by v1."""
    lua = "env/dist/etc/modules/lua_scripts/SitMeansRest.lua"
    _write(tmp_path, lua, "DURATION = 20\n")
    manifest = _manifest(
        id="sitmeanrest",
        name="Sit Means Rest",
        type="ale",
        conf=[_conf(lua, [{"key": "DURATION"}])],
    )
    (row,) = tuning.rows_for([manifest], {"ale": frozenset({"sitmeanrest"})}, tmp_path)
    assert row.backend == "lua"
    assert row.current == "20"
    assert row.read_only_reason == tuning.LUA_IS_NOT_IN_V1


def test_a_key_whose_file_is_not_a_file_is_read_only_and_never_touches_the_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tuning, "_read", _never)
    table = "acore_ale.paragon_config (DB table)"
    manifest = _manifest(
        id="paragon", name="Paragon", type="ale", conf=[_conf(table, [{"key": "xp"}])]
    )
    (row,) = tuning.rows_for([manifest], {"ale": frozenset({"paragon"})}, tmp_path)
    assert row.backend == "other"
    assert row.current is None
    assert row.read_only_reason == tuning.NOT_A_CONF_FILE.format(file=table)


def test_a_key_whose_file_is_a_glob_is_read_only_because_there_is_no_one_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tuning, "_read", _never)
    glob = "env/dist/etc/modules/lua_scripts/accountwide/*.lua"
    manifest = _manifest(
        id="accountwide", name="Account Wide", type="ale", conf=[_conf(glob, [{"key": "X"}])]
    )
    (row,) = tuning.rows_for([manifest], {"ale": frozenset({"accountwide"})}, tmp_path)
    assert row.current is None
    assert row.read_only_reason == tuning.MORE_THAN_ONE_FILE.format(file=glob)


def test_rows_are_ordered_by_family_then_catalog_then_file_then_key(tmp_path: Path) -> None:
    ale = _manifest(
        id="sitmeanrest",
        name="Sit",
        type="ale",
        conf=[_conf("env/dist/etc/modules/lua_scripts/S.lua", [{"key": "B"}, {"key": "A"}])],
    )
    module = _manifest(
        conf=[
            _conf("env/dist/etc/modules/z.conf", [{"key": "Z1"}]),
            _conf("env/dist/etc/modules/a.conf", [{"key": "A1"}]),
        ]
    )
    installed = {"module": frozenset({"mod-beast"}), "ale": frozenset({"sitmeanrest"})}
    rows = tuning.rows_for([ale, module], installed, tmp_path)
    assert [(row.family, row.file.rsplit("/", 1)[-1], row.key) for row in rows] == [
        ("module", "z.conf", "Z1"),
        ("module", "a.conf", "A1"),
        ("ale", "S.lua", "B"),
        ("ale", "S.lua", "A"),
    ]


def test_a_family_is_read_from_its_own_clone_folder(tmp_path: Path) -> None:
    """T41's defect from the other side: an ale is not installed because a module is."""
    ale = _manifest(
        id="mod-beast",
        type="ale",
        conf=[_conf("env/dist/etc/modules/lua_scripts/S.lua", [{"key": "A"}])],
    )
    rows = tuning.rows_for([ale], {"module": frozenset({"mod-beast"})}, tmp_path)
    assert rows == ()


# -- point 3: the writer ----------------------------------------------------


def _key(**over: Any) -> Any:
    return _manifest(conf=[_conf(CONF, [{"key": "K", **over}])]).conf[0].keys[0]


CLEAN = (
    "[worldserver]\n"
    "#\n"
    "# BeastMaster.Enable = 9\n"
    "BeastMaster.Enable = 1\n"
    "\n"
    "BeastMaster.MinLevel = 10\n"
    "BeastMaster.HunterOnly = 1\n"
)


def test_only_the_named_keys_move_and_every_other_line_is_untouched(tmp_path: Path) -> None:
    path = _write(tmp_path, CONF, CLEAN)
    tuning.write(path, {"BeastMaster.MinLevel": "40"})
    assert path.read_text(encoding="utf-8") == CLEAN.replace(
        "BeastMaster.MinLevel = 10", "BeastMaster.MinLevel = 40"
    )


def test_a_key_the_file_does_not_carry_is_appended_under_a_dated_comment(
    tmp_path: Path,
) -> None:
    """The 73 defaultless keys are exactly the ones a shipped .conf.dist may omit."""
    path = _write(tmp_path, CONF, "[worldserver]\nA = 1\n")
    tuning.write(path, {"B": "2"}, now=datetime(2026, 9, 12, 14, 30, 0))
    assert path.read_text(encoding="utf-8") == (
        "[worldserver]\nA = 1\n"
        "# Added by Yu'lon on 2026-09-12 — this key was not in the file.\n"
        "B = 2\n"
    )


def test_a_crlf_file_comes_back_crlf_and_an_lf_file_stays_lf(tmp_path: Path) -> None:
    """A conf a Windows editor wrote must come back out the way it went in."""
    crlf = tmp_path / "crlf.conf"
    crlf.write_bytes(b"[worldserver]\r\nK = 1\r\nOther = 2\r\n")
    tuning.write(crlf, {"K": "5"})
    assert crlf.read_bytes() == b"[worldserver]\r\nK = 5\r\nOther = 2\r\n"
    lf = tmp_path / "lf.conf"
    lf.write_bytes(b"K = 1\n")
    tuning.write(lf, {"K": "5"})
    assert lf.read_bytes() == b"K = 5\n"


def test_a_key_appended_to_a_crlf_file_is_appended_with_crlf(tmp_path: Path) -> None:
    crlf = tmp_path / "crlf.conf"
    crlf.write_bytes(b"A = 1\r\n")
    tuning.write(crlf, {"B": "2"}, now=datetime(2026, 9, 12))
    assert crlf.read_bytes().endswith(b"\r\nB = 2\r\n")
    assert b"\n\r" not in crlf.read_bytes()


def test_a_backup_is_taken_before_the_write_and_its_path_is_returned(tmp_path: Path) -> None:
    path = _write(tmp_path, CONF, CLEAN)
    made = tuning.write(path, {"BeastMaster.MinLevel": "40"})
    assert made.parent == path.parent
    assert made.read_text(encoding="utf-8") == CLEAN
    assert tuning.backups_of(path) == (made,)


def test_a_value_that_fails_its_own_type_is_refused_before_a_byte_is_written(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path, CONF, CLEAN)
    spec = {"BeastMaster.MinLevel": _key(type="int", min=0, max=80)}
    for bad in ("many", "", "81", "-1"):
        with pytest.raises(tuning.TuningError, match="K"):
            tuning.write(path, {"BeastMaster.MinLevel": bad}, spec=spec)
    assert path.read_text(encoding="utf-8") == CLEAN
    assert tuning.backups_of(path) == ()


def test_one_bad_value_in_a_batch_writes_none_of_the_batch(tmp_path: Path) -> None:
    """Save is per module card, so a card of six settings is one refusal or one write."""
    path = _write(tmp_path, CONF, CLEAN)
    spec = {"BeastMaster.MinLevel": _key(type="int")}
    with pytest.raises(tuning.TuningError):
        tuning.write(
            path,
            {"BeastMaster.Enable": "0", "BeastMaster.MinLevel": "forty"},
            spec=spec,
        )
    assert path.read_text(encoding="utf-8") == CLEAN


def test_no_bound_is_invented_where_the_catalog_states_none() -> None:
    tuning.check(_key(type="int"), "999999999")
    tuning.check(_key(type="int", min=0), "999999999")
    tuning.check(_key(type="int", max=80), "-999")
    with pytest.raises(tuning.TuningError):
        tuning.check(_key(type="int", min=0), "-1")


NOT_PLAIN_DIGITS = ("１２", "١٢", "1_0", " 5", "5 ", "+5", "0x10")
"""Whole numbers a core reads differently from Python's `int()`, or not at all.

Each one but `0x10` is inside 0..100 once Python parses it (`int("١٢") == 12`,
`int("1_0") == 10`, `int(" 5") == 5`, `int("+5") == 5`), so only the spelling
rule refuses it. AzerothCore reads an int key with `std::from_chars` over the
whole value (`StringConvert.h:70` at its pin), which takes ASCII digits and a
leading minus and nothing else; mangos-tbc reads it with `std::stoi`
(`Config.cpp:127-131`), which stops at `_` or `x` and throws on a value with no
digit at the front.
"""


@pytest.mark.parametrize("value", NOT_PLAIN_DIGITS)
def test_an_int_key_refuses_any_spelling_but_plain_digits(tmp_path: Path, value: str) -> None:
    from tests.support_player_text import command_faults, text_faults

    path = _write(tmp_path, CONF, CLEAN)
    spec = {"BeastMaster.MinLevel": _key(type="int", min=0, max=100)}
    # `_key()` names its key `K`; the refusal names the key it is about.
    with pytest.raises(tuning.TuningError, match="^K: ") as refusal:
        tuning.write(path, {"BeastMaster.MinLevel": value}, spec=spec)
    assert "whole number" in str(refusal.value)
    assert ("space" in str(refusal.value)) == (value != value.strip()), str(refusal.value)
    assert command_faults(str(refusal.value)) == [], str(refusal.value)
    assert text_faults(str(refusal.value)) == [], str(refusal.value)
    assert path.read_text(encoding="utf-8") == CLEAN
    assert tuning.backups_of(path) == ()


def test_an_int_key_still_takes_plain_digits_and_a_leading_minus() -> None:
    for value in ("0", "5", "100", "007", "-5", "2147483647", "-2147483648"):
        tuning.check(_key(type="int"), value)


@pytest.mark.parametrize("value", ["2147483648", "-2147483649", "99999999999999999999"])
def test_an_int_key_refuses_a_number_past_what_the_servers_int_holds(value: str) -> None:
    """mangos-tbc's `std::stoi` throws past int32 at world start; AzerothCore falls back."""
    from tests.support_player_text import command_faults

    with pytest.raises(tuning.TuningError, match="^K: ") as refusal:
        tuning.check(_key(type="int"), value)
    assert command_faults(str(refusal.value)) == [], str(refusal.value)
    assert "2147483647" in str(refusal.value)


def test_an_unsigned_int_key_takes_zero_to_uint32_and_refuses_a_negative() -> None:
    """T370: `mod-ah-bot` reads its GUID as `uint32`, so 3000000000 is real and -1 is not."""
    for value in ("0", "5", "2147483648", "3000000000", "4294967295"):
        tuning.check(_key(type="int", unsigned=True), value)
    for value in ("-1", "4294967296", "99999999999999999999"):
        with pytest.raises(tuning.TuningError, match="^K: "):
            tuning.check(_key(type="int", unsigned=True), value)
    with pytest.raises(tuning.TuningError, match="^K: "):
        tuning.check(_key(type="int"), "3000000000")
    with pytest.raises(tuning.TuningError, match="0 to 4294967295") as refusal:
        tuning.check(_key(type="int", unsigned=True), "-1")
    assert "more than" not in str(refusal.value)
    with pytest.raises(tuning.TuningError) as refusal:
        tuning.check(_key(type="int", unsigned=True), "+5")
    assert "-5" not in str(refusal.value)


def test_unsigned_belongs_to_an_int_key_only() -> None:
    with pytest.raises(ValueError, match="unsigned"):
        _key(type="bool", unsigned=True)


def test_the_shipped_ah_bot_keys_are_unsigned() -> None:
    """`GetOption<uint32>` at the pin: GUID, Account (mod-ah-bot) and ItemsPerCycle (plus)."""
    import json

    base = Path(__file__).resolve().parent.parent / "manifests" / "wow-wotlk" / "modules"
    for name, wanted, prompts in (
        (
            "mod-ah-bot",
            {"AuctionHouseBot.GUID", "AuctionHouseBot.Account"},
            {"bot_guid", "bot_account"},
        ),
        ("mod-ah-bot-plus", {"AuctionHouseBot.ItemsPerCycle"}, {"bot_guid"}),
    ):
        manifest = parse_manifest(json.loads((base / f"{name}.json").read_text(encoding="utf-8")))
        keys = {k.key: k for conf in manifest.conf for k in conf.keys}
        assert {k for k in wanted if keys[k].unsigned} == wanted, name
        assert {p.key for p in manifest.prompts if p.unsigned} == prompts, name


def test_a_raw_conf_text_names_each_declared_int_key_whose_value_fails_check() -> None:
    """T371: the raw editor's text, read against the declared keys; free text stays free."""
    spec = _key(type="int", min=0, max=100)
    text = "[worldserver]\n# K = oops\nK = +5\nOther = nothing\n"
    problems = tuning.int_problems(text, {"K": spec})
    assert len(problems) == 1 and problems[0].startswith("K: ")
    assert tuning.int_problems("K = 5\n", {"K": spec}) == ()
    assert tuning.int_problems("Other = +5\n", {"K": spec}) == ()
    assert tuning.int_problems("K = +5\n", {"K": _key(type="bool")}) == ()
    assert "K" in tuning.value_sentence(problems)
    assert tuning.value_sentence(()) is None


def test_a_key_with_no_type_accepts_anything_because_it_is_a_text_box() -> None:
    """T43's safety rule: a refusal the catalog never declared is a refusal we invented."""
    for value in ("anything at all", "", "3.5", "0,1,2"):
        tuning.check(_key(), value)
        tuning.check(None, value)


def test_a_bool_key_takes_both_spellings_its_files_use_and_nothing_wider() -> None:
    for value in ("0", "1", "true", "FALSE", " 1 "):
        tuning.check(_key(type="bool"), value)
    with pytest.raises(tuning.TuningError, match="on/off"):
        tuning.check(_key(type="bool"), "yes")


def test_a_quoted_value_keeps_its_quotes(tmp_path: Path) -> None:
    """`LoginDatabaseInfo = "..."` is quoted in every real conf; unquoting it breaks it."""
    path = _write(tmp_path, CONF, 'Motd = "hello"\n')
    tuning.write(path, {"Motd": "goodbye"})
    assert path.read_text(encoding="utf-8") == 'Motd = "goodbye"\n'


def test_a_backup_can_be_put_back(tmp_path: Path) -> None:
    path = _write(tmp_path, CONF, CLEAN)
    made = tuning.write(path, {"BeastMaster.MinLevel": "40"})
    tuning.restore(made, path)
    assert path.read_text(encoding="utf-8") == CLEAN
    assert made.is_file(), "a Revert must not consume the only record of the old file"


# -- point 4: the raw editor's guard ----------------------------------------
#
# DML's `launcher/src/lib/conf-lint.test.ts`, case for case, so the two
# launchers refuse the same text. Its own cases are marked; the last two are
# this tab's.


def test_a_clean_conf_has_nothing_to_say() -> None:
    assert (
        tuning.lint(
            "# playerbots.conf\n"
            "\n"
            "AiPlayerbot.RandomBotAutologin = 1\n"
            "AiPlayerbot.MinRandomBots = 50\n"
            "   # indented comment\n"
        )
        == ()
    )


def test_a_line_with_no_assignment_is_reported_with_its_number_and_text() -> None:
    assert tuning.lint("Key = 1\nthis is not a setting\nOther = 2") == (
        tuning.LintIssue(2, "this is not a setting"),
    )


def test_a_line_whose_key_is_empty_is_reported() -> None:
    assert tuning.lint("= orphan value") == (tuning.LintIssue(1, "= orphan value"),)


def test_an_empty_value_and_a_value_holding_an_equals_sign_are_both_fine() -> None:
    assert tuning.lint("Motd =") == ()
    assert tuning.lint("Greeting = a = b") == ()


def test_line_numbers_are_one_indexed_across_crlf_and_lf() -> None:
    assert tuning.lint("Good = 1\r\nbad line\r\nAlso = 2\r\nanother bad") == (
        tuning.LintIssue(2, "bad line"),
        tuning.LintIssue(4, "another bad"),
    )


def test_trailing_whitespace_does_not_make_a_line_a_problem() -> None:
    assert tuning.lint("Key = 1   \n   ") == ()


def test_an_ini_section_header_is_valid_conf_and_a_broken_one_is_not() -> None:
    """Every real AzerothCore conf opens with one; flagging it would flag every file."""
    assert tuning.lint('[worldserver]\n\nLoginDatabaseInfo = "x"\n  [authserver]  ') == ()
    assert tuning.lint("[worldserver") == (tuning.LintIssue(1, "[worldserver"),)


def test_the_confirm_names_the_first_offending_line_and_nothing_else() -> None:
    """One bad line is usually the edit that went wrong; forty is a dialog nobody reads."""
    issues = tuning.lint("bad one\nAlso = 2\nbad two")
    said = tuning.lint_sentence(issues)
    assert said is not None
    assert "Line 1" in said and "bad one" in said and "bad two" not in said


def test_a_clean_text_asks_nothing(tmp_path: Path) -> None:
    assert tuning.lint_sentence(tuning.lint("Key = 1\n")) is None


# -- point 5: what a change costs -------------------------------------------


def _row(file: str, **over: Any) -> tuning.TuningRow:
    backend = tuning.backend_of(file)
    fields: dict[str, Any] = {
        "module_id": "mod-beast",
        "module_name": "NPC Beastmaster",
        "family": "module",
        "file": file,
        "key": "K",
        "label": "K",
        "explain": None,
        "type": None,
        "min": None,
        "max": None,
        "default": None,
        "current": None,
        "installed": True,
        "backend": backend,
        "read_only_reason": tuning._read_only_reason(file, backend),
    }
    return tuning.TuningRow(**{**fields, **over})


def test_a_setting_this_app_does_not_write_owes_nothing() -> None:
    for file in (
        "env/dist/etc/modules/lua_scripts/SitMeansRest.lua",
        "acore_ale.paragon_config (DB table)",
        "env/dist/etc/modules/lua_scripts/accountwide/*.lua",
    ):
        row = _row(file)
        assert not row.editable
        assert tuning.apply_rule(row) == "read-only"


def test_a_conf_the_containers_read_off_the_users_disk_needs_a_restart() -> None:
    row = _row("env/dist/etc/modules/mod_npc_beastmaster.conf")
    assert row.editable
    assert tuning.apply_rule(row) == "restart"


def test_a_conf_outside_every_bind_needs_the_containers_recreated() -> None:
    """The running container is using the image's copy; saving changes only the disk."""
    assert tuning.apply_rule(_row("conf/somewhere-else.conf")) == "recreate"


def test_a_cmangos_etc_conf_is_read_off_the_users_disk_and_needs_a_restart() -> None:
    """T99: `./etc` is bound into mangosd and realmd, so a restart applies it (T94 follow-up).

    Mutation: drop `etc/` from `BOUND_INTO_THE_CONTAINERS` and the Bots tab's
    box, a Tuning save and a reset of a CMaNGOS conf all ask for the dearer
    recreate again.
    """
    assert tuning.apply_rule(_row("etc/aiplayerbot.conf")) == "restart"
    assert tuning.file_rule("etc/mangosd.conf") == "restart"
    assert tuning.file_rule("etc/modules/tortoise_bots.conf") == "restart"


def test_a_setting_in_the_modules_own_source_tree_needs_a_rebuild() -> None:
    row = _row("env/dist/etc/modules/mod_npc_beastmaster.conf")
    assert tuning.apply_rule(row, in_clone=True) == "rebuild"


def test_the_bound_directory_is_the_one_this_apps_compose_actually_binds() -> None:
    """A declaration nothing proves is a declaration that can rot (T43's own template).

    Read off the installer template rather than restated, so moving the mount
    breaks this test instead of quietly turning every "restart" on the tab into
    a promise the app cannot keep.
    """
    installers = Path(__file__).resolve().parents[1] / "catalog" / "installers"
    templates = {
        "env/dist/etc/": installers / "wow-wotlk" / "native" / "base.yml.tmpl",
        # T99: every CMaNGOS game's compose, into mangosd AND realmd.
        "etc/": installers / "shared" / "cmangos" / "base.yml.tmpl",
    }
    assert set(tuning.BOUND_INTO_THE_CONTAINERS) == set(templates)
    for prefix, path in templates.items():
        template = path.read_text(encoding="utf-8")
        binds = template.count(f"- ./{prefix.rstrip('/')}:")
        assert binds >= 1, prefix
        if prefix == "etc/":
            assert binds == 2, "both the world and the login server read ./etc"


def test_every_rule_has_a_sentence_and_no_sentence_has_no_rule() -> None:
    """The chip and the banner read these; a rule with no words would draw blank."""
    for file, clone in (
        ("env/dist/etc/modules/a.conf", False),
        ("conf/a.conf", False),
        ("env/dist/etc/modules/a.conf", True),
        ("a.lua", False),
    ):
        rule = tuning.apply_rule(_row(file), in_clone=clone)
        assert tuning.apply_sentence(rule).strip() != ""
    assert set(tuning.APPLY_SENTENCES) == {"rebuild", "recreate", "restart", "read-only"}


def test_a_card_names_every_job_its_rows_owe() -> None:
    """Saying "restart" over a save that needs a rebuild is DML's refused promise."""
    assert tuning.owed(["restart", "rebuild", "read-only"]) == ("rebuild", "restart")
    assert tuning.owed(["restart", "recreate"]) == ("recreate", "restart")
    assert tuning.owed(["read-only", "restart"]) == ("restart",)
    assert tuning.owed([]) == ()


# -- a catalog shorthand is not a key ---------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "AutoBalance.Enable.*",
        "MountScaling.Ground.Journeyman.*",
        "MountScaling.Flying.Expert.*",
        "MountScaling.Flying.Artisan.*",
        "AuctionHouseBot.ListProportion.*",
        "common/rare/ultraRare_*_price",
        "FillRateCommon / FillRateRare / FillRateUltra",
        "PotentialDurations ",
    ],
)
def test_a_key_that_names_a_family_of_keys_is_listed_read_only(key: str, tmp_path: Path) -> None:
    """SEVEN of the 107 shipped "keys" are shorthand for a GROUP of settings.

    `AutoBalance.Enable.*` is eleven real keys (`.Global`, `.5M`, `.10M`, …),
    `MountScaling` carries three of these on its own (`Ground.Journeyman.*`,
    `Flying.Expert.*`, `Flying.Artisan.*` — the last two went untested when
    this said six), and `FillRateCommon / FillRateRare / FillRateUltra` is
    three keys in one string. Writing any of them would append a line the
    module never reads, under a comment saying Yu'lon put it there.

    The last case carries a trailing space rather than a `*`: the rule is an
    ALLOW-list of what a real key looks like, so it refuses anything it cannot
    recognise rather than hunting for the two characters seen so far.
    """
    _write(tmp_path, CONF, "[worldserver]\n")
    manifest = _manifest(conf=[_conf(CONF, [{"key": key}])])
    (row,) = tuning.rows_for([manifest], {"module": frozenset({"mod-beast"})}, tmp_path)
    assert not row.editable
    assert row.read_only_reason == tuning.NOT_ONE_KEY.format(key=key)
    assert tuning.apply_rule(row) == "read-only"


def test_an_ordinary_key_is_still_writable(tmp_path: Path) -> None:
    """The guard above must not swallow the keys this whole tab exists for."""
    _write(tmp_path, CONF, "[worldserver]\n")
    for key in ("BeastMaster.Enable", "SoloCraft.Debuff.Enable", "mod-quest-loot.Enable"):
        manifest = _manifest(conf=[_conf(CONF, [{"key": key}])])
        (row,) = tuning.rows_for([manifest], {"module": frozenset({"mod-beast"})}, tmp_path)
        assert row.editable, key


# -- round 2: the writer preserves the line, and writes the line the core reads


def test_the_line_keeps_its_indentation_and_its_spacing_around_the_equals(
    tmp_path: Path,
) -> None:
    """Everything outside the VALUE is the user's, including how they aligned it."""
    path = _write(tmp_path, CONF, "\tK    =    old\nOther = 2\n")
    tuning.write(path, {"K": "new"})
    assert path.read_text(encoding="utf-8") == "\tK    =    new\nOther = 2\n"


def test_the_whole_value_is_replaced_including_a_second_equals(tmp_path: Path) -> None:
    """`Config.cpp:325` splits on the FIRST `=`; the rest of the line is the value.

    So `K = old = fallback` has the value `old = fallback`, and replacing the
    value replaces all of it. Keeping the `= fallback` would leave the server
    reading `new = fallback`.
    """
    path = _write(tmp_path, CONF, "K = old = fallback\n")
    tuning.write(path, {"K": "new"})
    assert path.read_text(encoding="utf-8") == "K = new\n"


def test_a_hash_after_the_value_is_part_of_the_value_and_goes_with_it(
    tmp_path: Path,
) -> None:
    """AzerothCore has no trailing-comment syntax, and this is the evidence.

    `Config.cpp:307-314` trims the line and skips it only when it is then empty
    or begins with `#` or `[`; line 325 then splits on the first `=` and takes
    EVERYTHING after it as the value. So on `K = old # keep this` the running
    server's value is literally `old # keep this`.

    Keeping the `# keep this` while changing `old` would therefore write the
    value `new # keep this` into a live conf — a value the user never asked
    for. Dropping it is the only answer that leaves the file meaning what the
    person pressing Save meant.
    """
    path = _write(tmp_path, CONF, "K = old # keep this\n")
    assert tuning.conf_value(path.read_text(encoding="utf-8"), "K") == "old # keep this"
    tuning.write(path, {"K": "new"})
    assert path.read_text(encoding="utf-8") == "K = new\n"


def test_the_first_assignment_is_rewritten_and_the_duplicate_below_it_is_left(
    tmp_path: Path,
) -> None:
    """The line the server READS. `IsDuplicateOption` skips every later copy."""
    path = _write(tmp_path, CONF, "# K = 9\n  K = 1\nK = 2\n")
    tuning.write(path, {"K": "7"})
    assert path.read_text(encoding="utf-8") == "# K = 9\n  K = 7\nK = 2\n"


def test_a_file_with_no_trailing_newline_keeps_none(tmp_path: Path) -> None:
    path = tmp_path / "x.conf"
    path.write_bytes(b"K = 1")
    tuning.write(path, {"K": "2"})
    assert path.read_bytes() == b"K = 2"


def test_a_crlf_line_keeps_its_spacing_and_its_carriage_return(tmp_path: Path) -> None:
    path = tmp_path / "x.conf"
    path.write_bytes(b"[worldserver]\r\nK   =   1\r\nOther = 2\r\n")
    tuning.write(path, {"K": "5"})
    assert path.read_bytes() == b"[worldserver]\r\nK   =   5\r\nOther = 2\r\n"


# -- round 2: a file this app cannot read is refused, never rewritten --------


def test_a_conf_that_is_not_utf8_is_refused_rather_than_rewritten(tmp_path: Path) -> None:
    """`errors="replace"` on the way in and UTF-8 on the way out is corruption.

    One byte in an unrelated comment line comes back as U+FFFD, and the user's
    file is silently changed in a place this app was never asked to touch.
    """
    path = tmp_path / "x.conf"
    path.write_bytes(b"# \xff\nK = 1\n")
    with pytest.raises(tuning.TuningError, match="UTF-8"):
        tuning.write(path, {"K": "2"})
    assert path.read_bytes() == b"# \xff\nK = 1\n"
    assert tuning.backups_of(path) == ()


def test_a_conf_that_is_not_utf8_has_no_current_value_rather_than_a_replaced_one(
    tmp_path: Path,
) -> None:
    path = tmp_path / CONF
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"BeastMaster.Enable = \xff\n")
    manifest = _manifest(conf=[_conf(CONF, [{"key": "BeastMaster.Enable"}])])
    (row,) = tuning.rows_for([manifest], {"module": frozenset({"mod-beast"})}, tmp_path)
    assert row.current is None, "a replacement character is not a reading of the file"


# -- round 2: the write is atomic, and every backup is its own file ----------


def test_a_write_that_fails_leaves_the_file_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truncate-then-write interrupted leaves a conf with half a file in it."""
    path = _write(tmp_path, CONF, CLEAN)

    real_replace = tuning.os.replace

    def boom(src: object, dst: object) -> None:
        # Only the CONF's rename: the backup's own rename (T94 fix round 2)
        # is the step before, and must land for the backup to exist.
        if Path(str(dst)) == path:
            raise OSError("no space left on device")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(tuning.os, "replace", boom)
    with pytest.raises(OSError):
        tuning.write(path, {"BeastMaster.MinLevel": "40"})
    assert path.read_text(encoding="utf-8") == CLEAN
    (backup,) = tuning.backups_of(path)
    assert backup.read_text(encoding="utf-8") == CLEAN
    leftovers = [p.name for p in path.parent.iterdir() if p.suffix == tuning.TEMP_SUFFIX]
    assert leftovers == [], f"a temp file was left behind: {leftovers}"


def test_two_saves_inside_one_second_are_two_backups(tmp_path: Path) -> None:
    """A stamp to the second collides, and the earlier backup is overwritten."""
    path = _write(tmp_path, CONF, "K = 1\n")
    same = datetime(2026, 9, 13, 12, 0, 0)
    first = tuning.backup(path, now=same)
    path.write_text("K = 2\n", encoding="utf-8")
    second = tuning.backup(path, now=same)
    assert first != second
    assert first.read_text(encoding="utf-8") == "K = 1\n"
    assert second.read_text(encoding="utf-8") == "K = 2\n"
    assert tuning.backups_of(path) == (first, second), "newest last"


# -- round 2: a card owes every job, not the most expensive one --------------


def test_a_card_that_needs_a_rebuild_and_a_recreate_names_both(tmp_path: Path) -> None:
    """The dangerous direction: naming only the rebuild leaves the container stale."""
    assert tuning.owed(["rebuild", "recreate"]) == ("rebuild", "recreate")
    said = tuning.owed_sentence(("rebuild", "recreate"))
    assert tuning.apply_sentence("rebuild") in said
    assert tuning.apply_sentence("recreate") in said


def test_owed_lists_each_job_once_most_expensive_first_and_drops_read_only() -> None:
    assert tuning.owed(["restart", "rebuild", "read-only", "restart"]) == (
        "rebuild",
        "restart",
    )
    assert tuning.owed(["read-only"]) == ()
    assert tuning.owed_sentence(()) == tuning.apply_sentence("read-only")


def test_a_conf_file_can_never_be_a_clone_file_so_the_rebuild_branch_has_no_caller() -> None:
    """`apply_rule(in_clone=True)` is unreachable from the catalog, and this pins why.

    `ConfFile.file` is relative to the SERVER dir and the model has no
    `in_clone` field — only `Patch` has one. So no manifest can point a tuning
    row at a file inside a module's own source tree, and `build_tuning_cards()`
    never passes `in_clone=True`. The parameter stays because the schema
    already carries the concept one model along; this test fails the day
    `ConfFile` gains the field, which is the day the branch acquires a caller.
    """
    from yulon.manifest import ConfFile, Patch

    assert "in_clone" not in ConfFile.model_fields
    assert "in_clone" in Patch.model_fields


# -- which of a conf's keys are shadowed by the compose environment (T44 r2) --


def test_conf_keys_lists_the_assignments_and_nothing_else() -> None:
    """Every key an active line assigns, in the file's own order.

    The same rule `conf_value()` reads one key by (`Config.cpp:310-314`): a
    trimmed line that is empty, `#` or `[` says nothing, and after the first
    `=` everything is value -- AzerothCore has no trailing-comment syntax.

    Mutation: `conf-keys-reads-comments` -- drop the comment arm and
    `#Transmog.Enable = 1`, which is the SHIPPED shape of a commented-out
    default, is reported as a key the file carries.
    """
    text = (
        "[worldserver]\n"
        "\n"
        "# a comment\n"
        "#AiPlayerbot.MinRandomBots = 40\n"
        "AiPlayerbot.MinRandomBots = 500\n"
        "AiPlayerbot.RandomBotAutologin=1\n"
        "not a setting\n"
        '  Spaced.Key = "x = y"\n'
    )

    assert tuning.conf_keys(text) == (
        "AiPlayerbot.MinRandomBots",
        "AiPlayerbot.RandomBotAutologin",
        "Spaced.Key",
    )


def test_a_key_named_twice_is_listed_once() -> None:
    """A conf may assign a key more than once; the tab asks about it once.

    Mutation: return a list with duplicates and the shadow warning names the
    same key twice.
    """
    assert tuning.conf_keys("A = 1\nA = 2\n") == ("A",)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
@pytest.mark.parametrize("mode", [0o600, 0o644])
def test_a_save_keeps_the_files_own_mode(tmp_path: Path, mode: int) -> None:
    """T116: the temp was opened at the umask's 0644, so a 0600 CMaNGOS conf -- mangosd.conf
    carries the database password -- came back readable by every local account."""
    path = tmp_path / "mangosd.conf"
    path.write_text("Key = 1\n", encoding="utf-8")
    os.chmod(path, mode)
    old = os.umask(0o022)
    try:
        tuning.write(path, {"Key": "2"})
    finally:
        os.umask(old)
    assert path.read_text(encoding="utf-8") == "Key = 2\n"
    assert stat.S_IMODE(path.stat().st_mode) == mode


# -- T573: a conf that is a link out of the server folder is neither read nor written ----------


def _linked_conf(tmp_path: Path) -> tuple[Path, Path, Path]:
    """`(server_dir, the conf inside it, the file outside it that the conf points to)`."""
    server = tmp_path / "server"
    server.mkdir()
    outside = tmp_path / "elsewhere.conf"
    outside.write_text("Key = 1\n", encoding="utf-8")
    link = server / "mod_x.conf"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need a privilege here")
    return server, link, outside


def test_a_write_through_a_link_out_of_the_server_folder_is_refused(tmp_path: Path) -> None:
    """Mutation: drop `check_inside` from `write` and the outside file is rewritten."""
    server, link, outside = _linked_conf(tmp_path)

    with pytest.raises(tuning.TuningError, match="outside the server folder"):
        tuning.write(link, {"Key": "2"}, root=server)

    assert outside.read_text(encoding="utf-8") == "Key = 1\n"
    assert not list(tmp_path.glob("**/*.bak")), "a backup of the outside file was taken"


def test_a_backup_through_a_link_out_of_the_server_folder_is_refused(tmp_path: Path) -> None:
    """Mutation: drop `check_inside` from `backup` and a copy of the outside file lands here."""
    server, link, _ = _linked_conf(tmp_path)

    with pytest.raises(tuning.TuningError, match="outside the server folder"):
        tuning.backup(link, root=server)

    assert not list(server.glob("*.bak"))


def test_a_link_that_stays_inside_the_server_folder_is_allowed(tmp_path: Path) -> None:
    server = tmp_path / "server"
    server.mkdir()
    real = server / "real.conf"
    real.write_text("Key = 1\n", encoding="utf-8")
    link = server / "mod_x.conf"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need a privilege here")

    tuning.check_inside(link, server)


def test_a_linked_folder_on_the_way_to_the_conf_is_refused_too(tmp_path: Path) -> None:
    server = tmp_path / "server"
    server.mkdir()
    away = tmp_path / "away"
    away.mkdir()
    (away / "mod_x.conf").write_text("Key = 1\n", encoding="utf-8")
    try:
        (server / "modules").symlink_to(away, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need a privilege here")

    with pytest.raises(tuning.TuningError, match="outside the server folder"):
        tuning.check_inside(server / "modules" / "mod_x.conf", server)


def test_a_windows_junction_out_of_the_server_folder_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A junction is a folder to Python on 3.11; `links.is_link` is what sees it (T375).

    Stood in on any platform: the folder is flagged as a name-surrogate reparse point and the
    final path is reported as outside, which is the answer `realpath` gives on Windows.
    """
    from yulon import links

    server = tmp_path / "server"
    (server / "modules").mkdir(parents=True)
    conf = server / "modules" / "mod_x.conf"
    conf.write_text("Key = 1\n", encoding="utf-8")
    junction = server / "modules"
    real_lstat = os.lstat

    class _Junction:
        st_mode = stat.S_IFDIR
        st_file_attributes = links.FILE_ATTRIBUTE_REPARSE_POINT
        st_reparse_tag = links.IO_REPARSE_TAG_MOUNT_POINT

    def fake_lstat(p: Any) -> Any:
        return _Junction() if Path(p) == junction else real_lstat(p)

    monkeypatch.setattr(links, "_lstat", fake_lstat)
    monkeypatch.setattr(
        tuning, "_real", lambda p: Path(p) if Path(p) == server else tmp_path / "away" / "x"
    )

    with pytest.raises(tuning.TuningError, match="outside the server folder"):
        tuning.write(conf, {"Key": "2"}, root=server)
    assert conf.read_text(encoding="utf-8") == "Key = 1\n"
    assert not list(server.glob("**/*.bak"))


def test_a_plain_file_is_not_resolved_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a link is chased: a plain tree costs no `realpath` call (9p reads are slow)."""
    path = _write(tmp_path, CONF, CLEAN)

    def boom(_: Any) -> NoReturn:
        raise AssertionError("resolved a plain file")

    monkeypatch.setattr(tuning, "_real", boom)
    tuning.check_inside(path, tmp_path)


def test_a_link_out_of_the_server_folder_is_refused_before_the_file_is_even_read(
    tmp_path: Path,
) -> None:
    """Mutation: drop `write`'s own check and the (invalid) outside text is read first."""
    server, link, outside = _linked_conf(tmp_path)
    outside.write_bytes(b"Key = \xff\n")

    with pytest.raises(tuning.TuningError, match="outside the server folder"):
        tuning.write(link, {"Key": "2"}, root=server)


# -- T573 item 2: a raw save keeps every line the player did not touch, byte for byte ---------


def test_the_editor_is_given_no_bom_and_only_lf() -> None:
    assert tuning.editor_view("﻿A = 1\r\nB = 2\nC = 3\rD = 4") == "A = 1\nB = 2\nC = 3\nD = 4"


def test_an_unedited_text_comes_back_as_the_same_bytes_whatever_the_endings() -> None:
    """Mutation: write every line with one ending and this fails on the mixed file."""
    raw = "﻿A = 1\r\nB = 2\nC = 3\rD = 4\r\n"
    assert tuning.save_text(raw, tuning.editor_view(raw)) == raw


def test_an_edited_line_changes_and_its_neighbours_keep_their_own_endings() -> None:
    raw = "A = 1\r\nB = 2\nC = 3\r\n"
    edited = tuning.editor_view(raw).replace("B = 2", "B = 9")
    assert tuning.save_text(raw, edited) == "A = 1\r\nB = 9\nC = 3\r\n"


def test_an_added_line_takes_the_ending_of_the_line_above_it() -> None:
    raw = "A = 1\nB = 2\r\n"
    assert tuning.save_text(raw, "A = 1\nNEW = 1\nB = 2\n") == "A = 1\nNEW = 1\nB = 2\r\n"


def test_a_line_added_at_the_end_of_a_file_with_no_final_newline() -> None:
    assert tuning.save_text("A = 1\r\nB = 2", "A = 1\nB = 2\nC = 3") == "A = 1\r\nB = 2\r\nC = 3"


def test_a_deleted_line_takes_its_ending_with_it() -> None:
    raw = "A = 1\r\nB = 2\nC = 3\r\n"
    assert tuning.save_text(raw, "A = 1\nC = 3\n") == "A = 1\r\nC = 3\r\n"


def test_a_file_with_no_bom_gets_none_and_a_pasted_cr_is_an_lf_edit() -> None:
    assert tuning.save_text("A = 1\n", "A = 1\r\nB = 2\n") == "A = 1\nB = 2\n"
    assert not tuning.save_text("A = 1\n", "A = 1\n").startswith("﻿")


def test_a_blank_line_added_after_a_lone_cr_line_stays_a_blank_line() -> None:
    """T573 review: 'A = 1\\rB = 2\\r' typed to a blank line plus C must not glue \\r\\n.

    Mutation: drop the glue pass and the blank line merges into a CRLF.
    """
    raw = "A = 1\rB = 2\r"
    edited = "A = 1\nB = 2\n\nC = 3"

    out = tuning.save_text(raw, edited)

    assert out == "A = 1\rB = 2\r\rC = 3"
    assert tuning.editor_view(out) == edited


@pytest.mark.parametrize("cap", [1500, 0, 2])
def test_an_edit_in_place_keeps_every_untouched_lines_raw_ending(
    monkeypatch: pytest.MonkeyPatch, cap: int
) -> None:
    """T573 re-review, fuzz: changing lines without adding or removing any keeps the rest.

    Run with the real cap and with caps of 0 and 2, which force the unique-line anchors and
    the in-order match onto tiny files. The oracle is on the RAW endings: line i that was not
    changed ends as it did.
    """
    import random

    def split(text: str) -> tuple[list[str], list[str]]:
        lines, found, at = [], [], 0
        for m in tuning._TERMINATOR.finditer(text):
            lines.append(text[at : m.start()])
            found.append(m.group())
            at = m.end()
        return [*lines, text[at:]], [*found, ""]

    monkeypatch.setattr(tuning, "MAX_DIFF_LINES", cap)
    rng = random.Random(cap + 1)
    words = ["A", "B", "", "# note", "[s]"]
    for _ in range(3000):
        raw = "".join(
            f"{rng.choice(words)}{i}" + rng.choice(["\n", "\r\n", "\r"])
            for i in range(rng.randint(1, 9))
        )
        raw += rng.choice(["", "tail"])
        old, old_ends = split(raw)
        new = list(old)
        for _ in range(rng.randint(0, 3)):
            at = rng.randrange(len(new))
            new[at] = f"{rng.choice(words)}chg{at}"
        edited = "\n".join(new)

        out = tuning.save_text(raw, edited)

        assert tuning.editor_view(out) == edited, (raw, edited, out)
        got, got_ends = split(out)
        assert len(got) == len(old)
        for i in range(len(old) - 1):
            if new[i] == old[i]:
                assert got_ends[i] == old_ends[i], (raw, edited, out, i)


def test_whatever_is_typed_comes_back_as_the_same_lines_after_a_save() -> None:
    """T573 review, fuzz: a save never merges or splits lines, and keeps untouched bytes.

    Seeded, so a failure is reproducible. `editor_view(save_text(raw, edited))` is what the
    editor shows after the save re-reads the file: it must be exactly what was typed.
    """
    import random

    rng = random.Random(573)
    words = ["A = 1", "B = 2", "", "# note", "[s]", "Key = x y"]
    ends = ["\n", "\r\n", "\r"]
    for _ in range(3000):
        n = rng.randint(0, 7)
        raw = "".join(rng.choice(words) + rng.choice(ends) for _ in range(n))
        raw += rng.choice(["", "", "tail"])
        if rng.random() < 0.2:
            raw = tuning.BOM + raw
        lines = tuning.editor_view(raw).split("\n")
        for _ in range(rng.randint(0, 3)):
            op = rng.choice(["del", "add", "change"])
            if op == "add" or not lines:
                lines.insert(rng.randint(0, len(lines)), rng.choice(words))
            elif op == "del":
                del lines[rng.randrange(len(lines))]
            else:
                lines[rng.randrange(len(lines))] = rng.choice(words)
        edited = "\n".join(lines)
        out = tuning.save_text(raw, edited)
        assert tuning.editor_view(out) == edited, (raw, edited, out)
        assert out.startswith(tuning.BOM) == raw.startswith(tuning.BOM)


def test_a_big_conf_saves_fast_and_keeps_the_lines_that_were_not_touched() -> None:
    """T573 review (d): 7.7 s on a big conf with many alike lines, on the window's thread.

    One edit in the middle of a big mixed-ending file must keep every other line's bytes, and a
    rewrite of every line (the worst case for a line diff of alike lines: 4.6 s at 12000 lines
    without the size fallback) must still finish at once.
    The exact line-up is capped per gap (`MAX_DIFF_LINES`) and per save (`_EXACT_CELLS`) for this.
    """
    import time

    n = 12000
    lines = [f"Key{i % 40} = {i % 7}" for i in range(n)]
    raw = "".join(line + ("\r\n" if i % 3 else "\n") for i, line in enumerate(lines))

    started = time.perf_counter()
    edited = list(lines)
    edited[6000] = "Key6000 = changed"
    out = tuning.save_text(raw, "\n".join(edited) + "\n")
    expected = raw.split("\n")
    expected[6000] = expected[6000].replace(lines[6000], "Key6000 = changed")
    assert out == "\n".join(expected)

    everything = "\n".join(f"Key{(i * 3) % 40} = {(i * 2) % 7}" for i in range(n)) + "\n"
    again = tuning.save_text(raw, everything)
    assert tuning.editor_view(again) == everything
    assert time.perf_counter() - started < 2.0


def test_two_far_apart_edits_in_a_big_mixed_ending_file_keep_every_other_lines_bytes() -> None:
    """T573 re-review: in a big file of alike lines the lines between two edits lost their endings.

    3200 lines of 280 texts repeated (no line occurs once), every 5th LF, every 50th a lone CR,
    the rest CRLF; edit the first and the last.
    Mutation: let the in-order match jump to the next equal text instead of lining up the next
    `_WINDOW` lines, and the changed first line is paired with a copy of itself 280 lines on.
    """
    n = 3200
    lines = [f"Key{i % 40} = {i % 7}" for i in range(n)]
    ends = ["\r" if i % 50 == 0 else "\n" if i % 5 == 0 else "\r\n" for i in range(n)]
    raw = "".join(line + end for line, end in zip(lines, ends, strict=True))
    edited = list(lines)
    edited[0], edited[n - 1] = "First = changed", "Last = changed"

    out = tuning.save_text(raw, "\n".join(edited) + "\n")

    expected = "".join(line + end for line, end in zip(edited, ends, strict=True))
    assert out == expected


def test_a_line_added_in_a_big_file_leaves_the_lines_after_it_their_endings() -> None:
    """A line added at the top of a big file of alike lines: every line after it keeps its bytes.

    Mutation: match the lines left over by their place in the gap and this is red.
    """
    n = 3200
    lines = [f"Key{i % 40} = {i % 7}" for i in range(n)]
    ends = ["\r" if i % 50 == 0 else "\n" if i % 5 == 0 else "\r\n" for i in range(n)]
    raw = "".join(line + end for line, end in zip(lines, ends, strict=True))
    edited = ["Inserted = 1", *lines]
    edited[n - 5] = "Changed = last"  # near the end, so the unchanged tail is short

    out = tuning.save_text(raw, "\n".join(edited) + "\n")

    # Every line from the old first to the one before the change sits one place lower
    # than it did, and keeps its bytes.
    middle = "".join(line + end for line, end in zip(lines[: n - 6], ends[: n - 6], strict=True))
    assert out.startswith("Inserted = 1" + tuning._newline_of(raw) + middle)


def _lines_and_ends(text: str) -> tuple[list[str], list[str]]:
    """`text` cut into its lines and each line's own raw ending (the last one's is "")."""
    lines, found, at = [], [], 0
    for m in tuning._TERMINATOR.finditer(text):
        lines.append(text[at : m.start()])
        found.append(m.group())
        at = m.end()
    return [*lines, text[at:]], [*found, ""]


def _mixed_file(n: int) -> tuple[list[str], list[str], str]:
    """`n` unique key lines: every 50th ends in a lone CR, every 5th in LF, the rest CRLF."""
    lines = [f"Key{i} = {i}" for i in range(n)]
    ends = ["\r" if i % 50 == 0 else "\n" if i % 5 == 0 else "\r\n" for i in range(n)]
    return lines, ends, "".join(line + end for line, end in zip(lines, ends, strict=True))


def _edit(lines: list[str], steps: list[tuple[str, int]]) -> list[tuple[int | None, str]]:
    """Apply `steps` (("ins"|"del"|"chg", at)) to `lines`, each line tracked by its old index."""
    items: list[tuple[int | None, str]] = list(enumerate(lines))
    for op, at in steps:
        if op == "ins":
            items.insert(at, (None, f"New{at} = 1"))
        elif op == "del":
            del items[at]
        else:
            items[at] = (None, f"Changed{at} = 1")
    return items


def _unedited_lines_that_lost_their_bytes(
    raw: str, items: list[tuple[int | None, str]], out: str
) -> list[int]:
    """New indexes of lines the player did not touch whose raw ending is not the one they had."""
    old, old_ends = _lines_and_ends(raw.removeprefix(tuning.BOM))
    got, got_ends = _lines_and_ends(out.removeprefix(tuning.BOM))
    assert got[: len(items)] == [text for _, text in items]
    return [
        j
        for j, (i, _) in enumerate(items)
        if i is not None and i < len(old) - 1 and j < len(got) - 1 and got_ends[j] != old_ends[i]
    ]


@pytest.mark.parametrize(
    "steps",
    [
        pytest.param([("ins", 10), ("ins", 1991)], id="two-inserts-far-apart"),
        pytest.param([("del", 10), ("del", 1989)], id="two-deletes-far-apart"),
        pytest.param([("ins", 10), ("del", 1991)], id="insert-and-delete-net-zero"),
        pytest.param([("del", 10), ("ins", 1990)], id="delete-and-insert-net-zero"),
        pytest.param(
            [("chg", 5), ("del", 1000), ("ins", 1500), ("chg", 1990)],
            id="delete-and-insert-between-two-changes",
        ),
        pytest.param([("ins", 10), ("chg", 1991)], id="insert-and-change"),
    ],
)
def test_far_apart_edits_that_move_lines_keep_every_untouched_lines_raw_bytes(
    steps: list[tuple[str, int]],
) -> None:
    """T573 third review: past the diff size, lines that moved were matched by place alone.

    2000 unique lines with CRLF, LF and lone-CR endings mixed; two edits far apart that change
    the line count (or cancel out) shifted every line between them, and 395-791 untouched lines
    took a neighbour's ending (lone CRs moved onto the next line). The oracle is the raw bytes
    of every line the edit did not touch, tracked by identity.
    Mutation: match the over-size middle by its place from the top/bottom again and this is red.
    """
    lines, _, raw = _mixed_file(2000)
    items = _edit(lines, steps)
    edited = "\n".join(text for _, text in items) + "\n"

    out = tuning.save_text(raw, edited)

    assert tuning.editor_view(out) == edited
    assert _unedited_lines_that_lost_their_bytes(raw, items, out) == []


def test_runs_of_alike_comment_lines_are_matched_to_their_own_run() -> None:
    """T573 third review: runs of `#` lines were matched from the wrong side (596 lines).

    3000 lines, every 10th-ish run of three `#` lines, CRLF and LF alternating; insert a line
    near the top and change one near the bottom.
    """
    n = 3000
    lines = ["#" if i % 10 in (3, 4, 5) else f"K{i} = {i}" for i in range(n)]
    ends = ["\n" if i % 2 else "\r\n" for i in range(n)]
    raw = "".join(line + end for line, end in zip(lines, ends, strict=True))
    items = _edit(lines, [("chg", 2990), ("ins", 10)])
    edited = "\n".join(text for _, text in items) + "\n"

    out = tuning.save_text(raw, edited)

    assert _unedited_lines_that_lost_their_bytes(raw, items, out) == []


@pytest.mark.parametrize("cap", [0, 2, 1500])
def test_a_pasted_copy_of_the_lines_below_does_not_take_their_endings(
    monkeypatch: pytest.MonkeyPatch, cap: int
) -> None:
    """The lines that occur once on each side anchor the match, not the first equal text.

    A copy of 40 lines pasted just above them (a section duplicated to start a new one), and a
    change far below: matching equal text in order alone takes the pasted copy for the old
    lines, so the old line above them and the real ones lose their endings.
    Mutation: drop the unique-line anchors and this is red at caps 0 and 2.
    """
    monkeypatch.setattr(tuning, "MAX_DIFF_LINES", cap)
    lines, _, raw = _mixed_file(300)
    items: list[tuple[int | None, str]] = list(enumerate(lines))
    items[10:10] = [(None, text) for text in lines[11:51]]
    items[290] = (None, "Changed = 1")
    edited = "\n".join(text for _, text in items) + "\n"

    out = tuning.save_text(raw, edited)

    assert _unedited_lines_that_lost_their_bytes(raw, items, out) == []


@pytest.mark.parametrize("cap", [1500, 6, 2, 0])
def test_any_edit_keeps_the_raw_bytes_of_every_line_it_did_not_touch(
    monkeypatch: pytest.MonkeyPatch, cap: int
) -> None:
    """T573 third review, fuzz with each line tracked by identity, through every path.

    Files mixing CRLF, LF and lone CR, with or without a BOM, with alike lines (`#`, blank,
    repeated keys) among unique ones; 1-6 edits of every kind. The caps force the diff, the
    unique-line anchors and the in-order match onto small files. A line whose text occurs once
    in the old and once in the new text must end exactly as it did. A line with alike twins can
    honestly be matched to a twin (deleting one of two `#` lines reads the same either way), so
    its ending must be one an alike old line had. An edit that typed a text the file already
    had is only checked for its lines (deleting a blank line and typing one lower down reads
    the same as moving the lines between, so neither reading is wrong).
    """
    import random

    monkeypatch.setattr(tuning, "MAX_DIFF_LINES", cap)
    rng = random.Random(5730 + cap)
    alike = ["#", "", "# ---", "Enabled = 1"]
    for case in range(2500):
        n = rng.randint(1, 40)
        old_lines = [
            rng.choice(alike) if rng.random() < 0.4 else f"K{i} = {rng.randint(0, 9)}"
            for i in range(n)
        ]
        raw = "".join(line + rng.choice(["\n", "\r\n", "\r"]) for line in old_lines)
        raw += rng.choice(["", "tail"])
        if rng.random() < 0.3:
            raw = tuning.BOM + raw
        old, old_ends = _lines_and_ends(raw.removeprefix(tuning.BOM))
        items: list[tuple[int | None, str]] = list(enumerate(old))
        for step in range(rng.randint(1, 6)):
            op = rng.choice(["ins", "del", "chg"])
            text = rng.choice(alike) if rng.random() < 0.3 else f"T{case}.{step} = x"
            if op == "ins" or len(items) < 2:
                items.insert(rng.randint(0, len(items)), (None, text))
            elif op == "del":
                del items[rng.randrange(len(items))]
            else:
                items[rng.randrange(len(items))] = (None, text)
        edited = "\n".join(text for _, text in items)

        out = tuning.save_text(raw, edited)

        assert tuning.editor_view(out) == edited, (raw, edited, out)
        assert out.startswith(tuning.BOM) == raw.startswith(tuning.BOM)
        got, got_ends = _lines_and_ends(out.removeprefix(tuning.BOM))
        new = [text for _, text in items]
        if not {text for i, text in items if i is None}.isdisjoint(old):
            continue
        default = tuning._newline_of(raw)
        for j, (i, text) in enumerate(items):
            if i is None or i == len(old) - 1 or j == len(items) - 1:
                continue
            if text == "" and j > 0 and got_ends[j - 1] == "\r" and got_ends[j] == "\r":
                continue  # the glue rule: a blank line after a lone CR takes a lone CR
            if old.count(text) == 1 and new.count(text) == 1:
                assert got_ends[j] == old_ends[i], (raw, edited, out, j)
            else:
                twins = {old_ends[k] or default for k in range(len(old)) if old[k] == text}
                assert got_ends[j] in twins, (raw, edited, out, j)


def test_a_1_mb_conf_of_repeated_sections_saves_fast_with_edits_far_apart() -> None:
    """T573 third review: speed on a 1 MB conf whose sections repeat (no line is unique).

    A whole-file line diff of such a file took 7.7 s at 2 MB on the window's thread. Three
    edits far apart (an insert near the top, a change in the middle, a delete near the end)
    must save in well under half a second and keep every untouched line's raw bytes.
    """
    import time

    section = [
        "[Bots]",
        "#",
        "#    Bot.Enabled",
        "#        Description: Whether this bot is on.",
        "#        Default:     1",
        "",
        "Bot.Enabled = 1",
        "Bot.Count = 50",
        "#",
        "",
    ]
    lines = section * (1024 * 1024 // sum(len(line) + 2 for line in section) + 1)
    n = len(lines)
    # A lone CR before an empty LF line would read back as one CRLF: none of those.
    ends = [
        "\r" if i % 97 == 0 and lines[i + 1] else "\n" if i % 3 == 0 else "\r\n" for i in range(n)
    ]
    raw = "".join(line + end for line, end in zip(lines, ends, strict=True))
    items = _edit(lines, [("del", n - 20), ("chg", n // 2), ("ins", 10)])
    edited = "\n".join(text for _, text in items) + "\n"

    started = time.perf_counter()
    out = tuning.save_text(raw, edited)
    took = time.perf_counter() - started

    assert _unedited_lines_that_lost_their_bytes(raw, items, out) == []
    assert took < 0.5, took


def test_a_line_moved_past_a_run_of_alike_lines_leaves_the_run_its_endings() -> None:
    """T573 fourth review (p1): a line moved past 20 `#` lines took 10 of them a neighbour's ending.

    The `#` lines end in LF and CRLF by turns. The moved line is the only line in its gap
    whose text occurs once on each side, so lining the gap up at it first left every `#`
    line unmatched; a gap this small is lined up exactly instead.
    Mutation: look for unique-line anchors before the exact line-up and this is red (10).
    """
    lines = ["Top = 1", "U = 1", *["#"] * 20, "V = 2", "W = 3"]
    ends = ["\r\n", "\r\n", *["\n", "\r\n"] * 10, "\r\n", "\r\n"]
    raw = "".join(line + end for line, end in zip(lines, ends, strict=True))
    items: list[tuple[int | None, str]] = list(enumerate(lines))
    moved = items.pop(1)
    items.insert(21, (None, moved[1]))
    edited = "\n".join(text for _, text in items) + "\n"

    out = tuning.save_text(raw, edited)

    assert tuning.editor_view(out) == edited
    assert _unedited_lines_that_lost_their_bytes(raw, items, out) == []


def _repeated(section: list[str], n: int) -> tuple[list[str], str]:
    """`n` lines of `section` over and over, ending in LF and CRLF by turns."""
    lines = (section * (n // len(section) + 1))[:n]
    ends = ["\r\n" if i % 2 else "\n" for i in range(n)]
    return lines, "".join(line + end for line, end in zip(lines, ends, strict=True))


_SECTION = [
    "[Bots]",
    "#",
    "#    Bot.Enabled",
    "#        Description: Whether this bot is on.",
    "#        Default:     1",
    "",
    "Bot.Enabled = 1",
    "Bot.Count = 50",
    "#",
    "",
]


@pytest.mark.parametrize(
    "section, n, at, size, text",
    [
        pytest.param(["#", "", "E = 1"], 600, (100, 400), 10, "", id="10-fresh-lines-twice"),
        pytest.param(["#", "", "E = 1"], 600, (100, 400), 17, "", id="17-fresh-lines-twice"),
        pytest.param(["#", "", "E = 1"], 600, (100, 400), 20, "", id="20-fresh-lines-twice"),
        pytest.param(_SECTION, 1000, (100, 700), 12, "", id="12-fresh-lines-in-sections"),
        pytest.param(["#", "", "E = 1"], 600, (100, 400), 3, "E = 1", id="3-twin-lines-twice"),
        pytest.param(["#", "", "E = 1"], 600, (100, 400), 10, "E = 1", id="10-twin-lines-twice"),
        pytest.param(["#", "", "E = 1"], 600, (100, 400), 20, "E = 1", id="20-twin-lines-twice"),
    ],
)
def test_blocks_added_among_repeated_sections_leave_every_other_line_its_ending(
    section: list[str], n: int, at: tuple[int, int], size: int, text: str
) -> None:
    """T573 fourth review (p7): two blocks pasted into a file of repeated sections.

    No line occurs once, so the file goes to the in-order match. 295 of 600 untouched lines
    took a neighbour's ending there: a window counting only matches lined the lines after
    the block up with a twin a few lines on, as the true line-up's last lines fall past
    its edge. Fresh lines (texts the file does not have) and lines the file has (`E = 1`)
    are both tried.
    Mutation: line a window up by matches alone (closed at both ends) and the twin-line
    cases are red; let a changed line cost nothing and the fresh-line cases are red.
    """
    lines, raw = _repeated(section, n)
    items: list[tuple[int | None, str]] = list(enumerate(lines))
    for where in sorted(at, reverse=True):
        items[where:where] = [(None, text or f"New{where}.{k} = 1") for k in range(size)]
    edited = "\n".join(text for _, text in items) + "\n"

    out = tuning.save_text(raw, edited)

    assert tuning.editor_view(out) == edited
    assert _unedited_lines_that_lost_their_bytes(raw, items, out) == []


@pytest.mark.parametrize(
    "section, changed",
    [
        pytest.param(["#"], (125, 157, 242, 247, 351, 468, 477, 531), id="all-hash-lines"),
        pytest.param(["#", "", "E = 1"], (82, 99, 101, 135, 301), id="two-close-in-sections"),
    ],
)
def test_lines_changed_among_twins_keep_the_twins_their_endings(
    section: list[str], changed: tuple[int, ...]
) -> None:
    """A changed line among twins is the line it replaced, not an added one (T573 round 5).

    In a window, `#` changed into `C` reads the same as `C` added and one `#` dropped further
    on; the line counts of the whole tell them apart. Counted as added, every `#` line after
    it took its neighbour's ending (263 of 624).
    Mutation: drop the charge for leaving the sides uneven (`_BEHIND = 0`) and this is red.
    """
    lines, raw = _repeated(section, 624)
    items: list[tuple[int | None, str]] = list(enumerate(lines))
    for k, where in enumerate(changed):
        items[where] = (None, f"C{k} = changed")
    edited = "\n".join(text for _, text in items) + "\n"

    out = tuning.save_text(raw, edited)

    assert tuning.editor_view(out) == edited
    assert _unedited_lines_that_lost_their_bytes(raw, items, out) == []


def test_a_block_pasted_above_changed_lines_stays_added_lines() -> None:
    """Changed lines further down do not make a pasted block read as changed lines (T573 r5).

    Ten fresh lines pasted into a file of repeated sections, three lines changed far below.
    In a window at the block, three of its lines paired with the old lines there read as
    well as the changes below; taken so, every line between moved a section on (397 of 600).
    Mutation: let a run of any length of fresh lines be changed lines and this is red.
    """
    lines, raw = _repeated(["#", "", "E = 1"], 600)
    items: list[tuple[int | None, str]] = list(enumerate(lines))
    for k, where in enumerate((300, 400, 500)):
        items[where] = (None, f"C{k} = changed")
    items[100:100] = [(None, f"Pasted{k} = 1") for k in range(10)]
    edited = "\n".join(text for _, text in items) + "\n"

    out = tuning.save_text(raw, edited)

    assert tuning.editor_view(out) == edited
    assert _unedited_lines_that_lost_their_bytes(raw, items, out) == []


@pytest.mark.parametrize("edits", [("add", "paste"), ("change", "change-run")])
def test_fresh_edits_in_big_files_of_repeated_sections_keep_every_other_lines_bytes(
    edits: tuple[str, str],
) -> None:
    """T573 fourth review (p6), fuzz: files of 300-700 lines of one section repeated.

    Endings CRLF, LF and lone CR at random; 1-8 edits that each type text the file does not
    have: lines added one at a time or pasted 5-80 at once, or lines changed one at a time
    or 2-3 together. The file is far bigger than `_walk`'s window and has no line that
    occurs once, so this goes through the window, keeps its first half and moves on. With
    the twins' texts untouched, which old line each untouched new line is can be read off,
    so every one of them must end as it did. 126 of 250 such cases lost endings before.
    """
    import random

    rng = random.Random(573)
    alike = ["#", "", "# ---", "E = 1", "C = 5", "[S]"]
    for case in range(120):
        section = [rng.choice(alike) for _ in range(rng.randint(3, 12))]
        n = rng.randint(300, 700)
        lines = (section * (n // len(section) + 1))[:n]
        ends = [rng.choice(["\n", "\r\n", "\r"]) for _ in lines]
        for k in range(n - 1):
            if ends[k] == "\r" and lines[k + 1] == "":
                ends[k] = "\n"  # a lone CR before a blank line would read back as one CRLF
        raw = "".join(line + end for line, end in zip(lines, ends, strict=True))
        items: list[tuple[int | None, str]] = list(enumerate(lines))
        for step in range(rng.randint(1, 8)):
            edit = rng.choice(edits)
            at = rng.randrange(len(items))
            if edit == "add":
                items.insert(at, (None, f"N{case}.{step}"))
            elif edit == "paste":
                items[at:at] = [(None, f"P{case}.{step}.{k}") for k in range(rng.randint(5, 80))]
            elif edit == "change":
                items[at] = (None, f"C{case}.{step}")
            else:
                width = len(items[at : at + rng.randint(2, 3)])
                items[at : at + width] = [(None, f"R{case}.{step}.{k}") for k in range(width)]
        edited = "\n".join(text for _, text in items) + "\n"

        out = tuning.save_text(raw, edited)

        assert tuning.editor_view(out) == edited, case
        got_ends = _lines_and_ends(out)[1]
        lost = [
            j
            for j in _unedited_lines_that_lost_their_bytes(raw, items, out)
            # The glue rule: a blank line after a lone-CR line takes a lone CR itself.
            if not (items[j][1] == "" and got_ends[j - 1] == got_ends[j] == "\r")
        ]
        assert lost == [], case


def test_a_1_mb_file_of_one_letter_lines_with_swaps_everywhere_saves_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T573 Codex adversarial review: the in-order match's windows were not budgeted.

    About 420,000 lines of `a` or `b` at random, LF and CRLF by turns, with every third
    pair of lines swapped: no line is unique and the two texts differ every few lines, so
    every difference opened an exactly lined-up window (1.8 s for 500,000 such lines, on the
    window's thread). The windows are charged to the save's `_EXACT_CELLS`, and once those
    are spent the rest is matched in linear time. The work is counted, which no machine's
    speed changes: the line pairs lined up exactly stay within `_EXACT_CELLS`. The time
    is a loose second check: 0.3 s here, 0.85 s on a CI runner sharing its cores with
    three other test workers.
    Mutation: never charge a window and 23 times the cells are lined up (1.5 s here).
    """
    import random
    import time

    rng = random.Random(573)
    n = 1024 * 1024 * 2 // 5
    lines = [rng.choice("ab") for _ in range(n)]
    raw = "".join(line + ("\r\n" if i % 2 else "\n") for i, line in enumerate(lines))
    new = list(lines)
    for k in range(0, n - 1, 3):
        new[k], new[k + 1] = new[k + 1], new[k]
    edited = "\n".join(new) + "\n"
    cells = [0]
    line_up = tuning._line_up

    def counted(old: list[str], new: list[str], *rest: object) -> list[tuple[int, int]]:
        cells[0] += len(old) * len(new)
        return line_up(old, new, *rest)  # type: ignore[arg-type]

    monkeypatch.setattr(tuning, "_line_up", counted)

    started = time.perf_counter()
    out = tuning.save_text(raw, edited)
    took = time.perf_counter() - started

    assert tuning.editor_view(out) == edited
    assert len(raw) > 1000 * 1000
    assert 0 < cells[0] <= tuning._EXACT_CELLS, cells[0]
    assert took < 2.0, took


def test_a_line_added_at_the_top_of_a_lone_cr_file_ends_in_a_lone_cr() -> None:
    """A file whose lines all end in a lone CR keeps that ending on a line added above them.

    Such a line has no line above it and replaces none, so it takes the file's usual ending,
    which was LF for any file without a CRLF in it.
    """
    out = tuning.save_text("A = 1\rB = 2\r", "New = 1\nA = 1\nB = 2\n")

    assert out == "New = 1\rA = 1\rB = 2\r"


def test_an_emptied_editor_writes_an_empty_file() -> None:
    assert tuning.save_text("A = 1\r\n", "") == ""


# -- T573 item 3: one file under two spellings is one file on Windows and on macOS -------------


@pytest.mark.parametrize("platform, same", [("win32", True), ("darwin", True), ("linux", False)])
def test_two_spellings_of_a_file_name_are_one_file_where_the_disk_ignores_case(
    monkeypatch: pytest.MonkeyPatch, platform: str, same: bool
) -> None:
    """Mutation: key on `normcase` alone and macOS sees `Playerbots.conf` as a second file."""
    monkeypatch.setattr(tuning, "_disk_ignores_case", lambda: platform in ("win32", "darwin"))
    a = tuning.file_key("env/dist/etc/modules/Playerbots.conf")
    b = tuning.file_key("env/dist/etc/modules/playerbots.conf")
    assert (a == b) is same


def test_two_spellings_that_exist_as_two_files_are_two_files_on_a_case_sensitive_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T573 item 3, Codex P2: a Mac volume can be case-sensitive; the disk, not the OS, decides.

    This test disk is case-sensitive, so both spellings exist as separate files, as on such a
    volume. Mutation: answer from the platform alone and the second file is called the first.
    """
    monkeypatch.setattr(tuning, "_disk_ignores_case", lambda: True)
    (tmp_path / "playerbots.conf").write_text("a", encoding="utf-8")
    (tmp_path / "Playerbots.conf").write_text("b", encoding="utf-8")

    assert not tuning.is_one_of("Playerbots.conf", ["playerbots.conf"], root=tmp_path)
    assert tuning.is_one_of("playerbots.conf", ["playerbots.conf"], root=tmp_path)


def test_two_spellings_that_are_one_file_on_disk_stay_one_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case-blind volume: a spelling that reaches the same file (here a hard link) is it.

    Mutation: ignore `samefile` agreeing and a hard-linked spelling gets its own editable button.
    """
    monkeypatch.setattr(tuning, "_disk_ignores_case", lambda: True)
    (tmp_path / "playerbots.conf").write_text("a", encoding="utf-8")
    os.link(tmp_path / "playerbots.conf", tmp_path / "Playerbots.conf")

    assert tuning.is_one_of("Playerbots.conf", ["playerbots.conf"], root=tmp_path)
