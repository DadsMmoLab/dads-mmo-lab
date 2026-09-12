"""Tests for `yulon.tuning` — the Tuning tab's reading, writing and guard (T43).

No Qt anywhere in this file: everything the tab decides about a setting is
decided here, against a directory on disk, so the rules are assertable without
a `QApplication`. The widgets are `tests/test_tuning_panel.py`'s.
"""

from __future__ import annotations

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
    assert tuning.apply_rule(_row("etc/somewhere-else.conf")) == "recreate"


def test_a_setting_in_the_modules_own_source_tree_needs_a_rebuild() -> None:
    row = _row("env/dist/etc/modules/mod_npc_beastmaster.conf")
    assert tuning.apply_rule(row, in_clone=True) == "rebuild"


def test_the_bound_directory_is_the_one_this_apps_compose_actually_binds() -> None:
    """A declaration nothing proves is a declaration that can rot (T43's own template).

    Read off the installer template rather than restated, so moving the mount
    breaks this test instead of quietly turning every "restart" on the tab into
    a promise the app cannot keep.
    """
    template = (
        Path(__file__).resolve().parents[1]
        / "catalog"
        / "installers"
        / "wow-wotlk"
        / "native"
        / "base.yml.tmpl"
    ).read_text(encoding="utf-8")
    for prefix in tuning.BOUND_INTO_THE_CONTAINERS:
        assert f"- ./{prefix.rstrip('/')}:" in template


def test_every_rule_has_a_sentence_and_no_sentence_has_no_rule() -> None:
    """The chip and the banner read these; a rule with no words would draw blank."""
    for file, clone in (
        ("env/dist/etc/modules/a.conf", False),
        ("etc/a.conf", False),
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

    def boom(src: object, dst: object) -> None:
        raise OSError("no space left on device")

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
