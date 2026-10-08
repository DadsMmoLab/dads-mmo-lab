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


@pytest.mark.parametrize("bad", ["2", "-1", "yes", "true", "01", "", " 1", "1.0", "on"])
def test_a_value_other_than_zero_or_one_is_refused_and_writes_nothing(
    tmp_path: Path, bad: str
) -> None:
    path = put(tmp_path, DIST)
    with pytest.raises(tuning.TuningError, match="Unbound.ReagentFree"):
        unbound_settings.write(tmp_path, {"Unbound.ReagentFree": bad}, now=NOW)
    assert path.read_text(encoding="utf-8") == DIST
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


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
