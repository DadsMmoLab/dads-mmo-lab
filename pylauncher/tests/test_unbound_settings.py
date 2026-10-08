"""T554 Y5a: the Unbound settings reader and writer (three switches, off by default).

`mod_unbound.conf` carries `Unbound.ReagentFree`, `Unbound.InstantSummons` and
`Unbound.AutoBuff`, all `0` in the shipped `.conf.dist` and off in the compiled code
when the file or a key is missing. This module is the Tuning tab's source for them;
it writes through `tuning.write` and so inherits its keep-everything rules.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from yulon import tuning, unbound_settings

CONF = unbound_settings.FILE
KEYS = ("Unbound.ReagentFree", "Unbound.InstantSummons", "Unbound.AutoBuff")

DIST = (
    "###############################\n"
    "# UNBOUND CONFIGURATION\n"
    "###############################\n"
    "\n"
    "#    Unbound.ReagentFree\n"
    "#        Description: Casting needs no reagents.\n"
    "#        Default:     0\n"
    "\n"
    "Unbound.ReagentFree = 0\n"
    "\n"
    "Unbound.InstantSummons = 0\n"
    "\n"
    "# the chat command\n"
    "Unbound.AutoBuff = 0\n"
)
NOW = datetime(2026, 10, 8, 12, 0, 0)


def put(server_dir: Path, text: str, *, newline: str | None = None) -> Path:
    path = server_dir / CONF
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text if newline is None else text.replace("\n", newline))
    return path


def test_the_file_is_the_modules_conf_under_the_server_dir() -> None:
    assert CONF == "env/dist/etc/modules/mod_unbound.conf"


def test_the_three_rows_read_off_from_the_shipped_conf(tmp_path: Path) -> None:
    put(tmp_path, DIST)
    rows = unbound_settings.rows(tmp_path)
    assert tuple(row.key for row in rows) == KEYS
    assert [row.current for row in rows] == ["0", "0", "0"]
    assert [unbound_settings.is_on(row) for row in rows] == [False, False, False]
    assert all(row.default == "0" and row.file == CONF for row in rows)
    assert all(row.type == "bool" and row.editable for row in rows)
    assert all(row.label and row.explain for row in rows)


def test_a_missing_file_gives_off_rows_with_no_current_value(tmp_path: Path) -> None:
    rows = unbound_settings.rows(tmp_path)
    assert tuple(row.key for row in rows) == KEYS
    assert [row.current for row in rows] == [None, None, None]
    assert not any(unbound_settings.is_on(row) for row in rows)


def test_a_key_the_file_does_not_name_has_no_current_value(tmp_path: Path) -> None:
    put(tmp_path, "Unbound.AutoBuff = 1\n")
    by_key = {row.key: row for row in unbound_settings.rows(tmp_path)}
    assert by_key["Unbound.AutoBuff"].current == "1"
    assert unbound_settings.is_on(by_key["Unbound.AutoBuff"])
    assert by_key["Unbound.ReagentFree"].current is None
    assert not unbound_settings.is_on(by_key["Unbound.ReagentFree"])


def test_a_file_that_is_not_utf8_gives_no_current_value(tmp_path: Path) -> None:
    path = tmp_path / CONF
    path.parent.mkdir(parents=True)
    path.write_bytes(b"Unbound.ReagentFree = 1\n\xff\xfe\n")
    assert [row.current for row in unbound_settings.rows(tmp_path)] == [None, None, None]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_writing_reagent_free_changes_that_line_only_byte_for_byte(
    tmp_path: Path, newline: str
) -> None:
    path = put(tmp_path, DIST, newline=newline)
    before = path.read_bytes()
    unbound_settings.write(tmp_path, {"Unbound.ReagentFree": "1"}, now=NOW)
    after = path.read_bytes()
    expected = before.replace(b"Unbound.ReagentFree = 0", b"Unbound.ReagentFree = 1")
    assert before.count(b"Unbound.ReagentFree = 0") == 1
    assert after == expected
    by_key = {row.key: row.current for row in unbound_settings.rows(tmp_path)}
    assert by_key == {
        "Unbound.ReagentFree": "1",
        "Unbound.InstantSummons": "0",
        "Unbound.AutoBuff": "0",
    }


def test_a_write_keeps_a_backup_of_what_it_replaced(tmp_path: Path) -> None:
    path = put(tmp_path, DIST)
    made = unbound_settings.write(tmp_path, {"Unbound.AutoBuff": "1"}, now=NOW)
    assert made.read_text(encoding="utf-8") == DIST
    assert path.read_text(encoding="utf-8") != DIST


def test_a_key_the_file_lacks_is_appended_and_nothing_else_moves(tmp_path: Path) -> None:
    text = "Unbound.ReagentFree = 0\n"
    put(tmp_path, text)
    unbound_settings.write(tmp_path, {"Unbound.AutoBuff": "1"}, now=NOW)
    written = (tmp_path / CONF).read_text(encoding="utf-8")
    assert written.startswith(text)
    assert written.rstrip("\n").endswith("Unbound.AutoBuff = 1")


@pytest.mark.parametrize("bad", ["2", "-1", "yes", "01", "", " 1", "1.0", "on", "enabled"])
def test_a_value_the_module_cannot_read_is_refused_and_writes_nothing(
    tmp_path: Path, bad: str
) -> None:
    """The C++ reads `GetOption<bool>` and `dml_autobuff.lua` reads `1` or `true`, so a
    value outside 0/1/true/false is one at least one reader of the file cannot take.
    The sentence never repeats the value: on the Tuning tab nobody typed it, they
    ticked a box."""
    path = put(tmp_path, DIST)
    with pytest.raises(tuning.TuningError, match="Unbound.ReagentFree") as refused:
        unbound_settings.write(tmp_path, {"Unbound.ReagentFree": bad}, now=NOW)
    said = str(refused.value)
    assert f"`{bad}`" not in said and f"'{bad}'" not in said and f'"{bad}"' not in said
    assert path.read_text(encoding="utf-8") == DIST
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


@pytest.mark.parametrize(("typed", "written"), [("true", "1"), ("false", "0"), ("True", "1")])
def test_a_true_or_false_is_written_back_as_one_or_zero(
    tmp_path: Path, typed: str, written: str
) -> None:
    """A hand-edited `true`/`false` is a value the module reads (T554 rework item 4). The
    switch flips it in that spelling, and the writer puts the module's own 0/1 back."""
    path = put(tmp_path, DIST.replace("ReagentFree = 0", "ReagentFree = false"))
    unbound_settings.write(tmp_path, {"Unbound.ReagentFree": typed}, now=NOW)
    assert f"Unbound.ReagentFree = {written}\n" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("current", "on"),
    [
        ("1", True),
        ("true", True),
        ("True", True),
        ("0", False),
        ("false", False),
        ("FALSE", False),
        (None, False),
        ("2", False),
    ],
)
def test_true_and_one_are_on_false_and_zero_are_off(
    tmp_path: Path, current: str | None, on: bool
) -> None:
    text = DIST if current is None else DIST.replace("AutoBuff = 0", f"AutoBuff = {current}")
    if current is None:
        text = text.replace("Unbound.AutoBuff = 0\n", "")
    put(tmp_path, text)
    by_key = {row.key: row for row in unbound_settings.rows(tmp_path)}
    assert by_key["Unbound.AutoBuff"].current == current
    assert unbound_settings.is_on(by_key["Unbound.AutoBuff"]) is on


def test_one_bad_value_in_a_save_writes_none_of_them(tmp_path: Path) -> None:
    path = put(tmp_path, DIST)
    with pytest.raises(tuning.TuningError):
        unbound_settings.write(
            tmp_path, {"Unbound.AutoBuff": "1", "Unbound.InstantSummons": "2"}, now=NOW
        )
    assert path.read_text(encoding="utf-8") == DIST


def test_a_key_that_is_not_one_of_the_three_is_refused(tmp_path: Path) -> None:
    path = put(tmp_path, DIST)
    with pytest.raises(tuning.TuningError, match="Unbound.Whatever"):
        unbound_settings.write(tmp_path, {"Unbound.Whatever": "1"}, now=NOW)
    assert path.read_text(encoding="utf-8") == DIST


def test_writing_before_the_file_exists_is_refused_not_created(tmp_path: Path) -> None:
    with pytest.raises(tuning.TuningError, match="mod_unbound.conf"):
        unbound_settings.write(tmp_path, {"Unbound.AutoBuff": "1"}, now=NOW)
    assert not (tmp_path / CONF).exists()


def test_every_switch_defaults_off_in_code_and_in_the_rows() -> None:
    assert unbound_settings.DEFAULT == "0"
    assert tuple(unbound_settings.conf_keys()) == KEYS
    assert {spec.default for spec in unbound_settings.conf_keys().values()} == {"0"}


def test_the_instant_summons_text_never_says_free_and_says_reagents_are_still_used() -> None:
    spec = unbound_settings.conf_keys()["Unbound.InstantSummons"]
    assert "free" not in (spec.label or "").lower()
    assert "free" not in (spec.explain or "").lower().replace("free casting reagents", "")
    assert "no mana" in (spec.explain or "")
    assert "reagents" in (spec.explain or "")


@pytest.mark.parametrize(
    ("key", "current", "on"),
    [
        # Read by the C++ `GetOption<bool>`: StringTo<bool> non-strict, any case.
        ("Unbound.ReagentFree", "yes", True),
        ("Unbound.ReagentFree", "On", True),
        ("Unbound.InstantSummons", "y", True),
        ("Unbound.InstantSummons", "no", False),
        ("Unbound.InstantSummons", "01", False),
        # Read only by dml_autobuff.lua through ALE's GetConfigValue: `true` in any
        # case becomes a boolean, a whole number a number, anything else stays text,
        # and the script turns on for the text `1` or `true` (GlobalMethods.h:64-97).
        ("Unbound.AutoBuff", "yes", False),
        ("Unbound.AutoBuff", "01", True),
        ("Unbound.AutoBuff", "TRUE", True),
    ],
)
def test_a_switch_is_on_where_the_code_that_reads_that_key_says_so(
    tmp_path: Path, key: str, current: str, on: bool
) -> None:
    put(tmp_path, DIST.replace(f"{key} = 0", f"{key} = {current}"))
    by_key = {row.key: row for row in unbound_settings.rows(tmp_path)}
    assert by_key[key].current == current
    assert unbound_settings.is_on(by_key[key]) is on
