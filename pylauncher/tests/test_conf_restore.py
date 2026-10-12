"""Individual Progression turns on two core settings, and Remove puts back what was there (T753).

The module's README: "Please ensure that EnablePlayerSettings is set to 1 in worldserver.conf
or progress will not be saved! ... please ensure DBC.EnforceItemAttributes is set to 0". The
pinned core's worldserver.conf.dist (2a2211cd, lines 2118 and 2827) ships 0 and 1, and nothing
set them. They are the core's own keys, so Remove puts back the value from before the install
while the key still reads what the install wrote (`ConfKey.restore`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_apply import _FakeGit, _FakeSql, _untouched
from yulon import module_answers, tuning
from yulon.apply import Applier
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import Manifest, parse_manifest

CORE = "env/dist/etc/worldserver.conf"
STOCK = "EnablePlayerSettings = 0\nRate.XP.Kill = 1\nDBC.EnforceItemAttributes = 1\n"


def _ip() -> Manifest:
    return wotlk_modules.store().load("module", "mod-individual-progression")


def _server(tmp_path: Path, text: str = STOCK) -> Path:
    core = tmp_path / CORE
    core.parent.mkdir(parents=True, exist_ok=True)
    core.write_text(text, encoding="utf-8")
    return core


def _applier(tmp_path: Path) -> Applier:
    files = {"conf/individualProgression.conf.dist": "IndividualProgression.Enable = 1\n"}
    return Applier(tmp_path, git=_FakeGit(files), sql=_FakeSql())


def _values(core: Path) -> tuple[str | None, str | None]:
    text = core.read_text(encoding="utf-8")
    return (
        tuning.conf_value(text, "EnablePlayerSettings"),
        tuning.conf_value(text, "DBC.EnforceItemAttributes"),
    )


def test_install_turns_on_player_settings_and_lets_the_database_set_item_stats(
    tmp_path: Path,
) -> None:
    core = _server(tmp_path)
    _applier(tmp_path).install(_ip())
    assert _values(core) == ("1", "0")


def test_remove_puts_the_core_values_back(tmp_path: Path) -> None:
    core = _server(tmp_path)
    _applier(tmp_path).install(_ip())
    report = _applier(tmp_path).remove(_ip())
    assert core.read_text(encoding="utf-8") == STOCK
    assert any("EnablePlayerSettings = 0" in line for line in report.done), report.done
    assert module_answers.conf_before(tmp_path, _ip()) == {}


def test_a_value_the_player_had_before_the_install_stays_after_remove(tmp_path: Path) -> None:
    """The install found EnablePlayerSettings = 1 already: that 1 was never Yu'lon's."""
    core = _server(tmp_path, STOCK.replace("EnablePlayerSettings = 0", "EnablePlayerSettings = 1"))
    _applier(tmp_path).install(_ip())
    _applier(tmp_path).remove(_ip())
    assert _values(core) == ("1", "1")


def test_a_value_the_player_changed_since_the_install_is_left_alone(tmp_path: Path) -> None:
    core = _server(tmp_path)
    _applier(tmp_path).install(_ip())
    core.write_text(
        core.read_text(encoding="utf-8").replace(
            "DBC.EnforceItemAttributes = 0", "DBC.EnforceItemAttributes = 2"
        ),
        encoding="utf-8",
    )
    report = _applier(tmp_path).remove(_ip())
    assert _values(core) == ("0", "2")
    assert any("DBC.EnforceItemAttributes was changed since" in s for s in report.skipped)


def test_a_key_the_install_added_is_taken_out_again(tmp_path: Path) -> None:
    core = _server(tmp_path, "Rate.XP.Kill = 1\nDBC.EnforceItemAttributes = 1\n")
    _applier(tmp_path).install(_ip())
    assert _values(core) == ("1", "0")
    _applier(tmp_path).remove(_ip())
    assert core.read_text(encoding="utf-8") == "Rate.XP.Kill = 1\nDBC.EnforceItemAttributes = 1\n"


def test_a_second_install_keeps_the_value_from_before_the_first(tmp_path: Path) -> None:
    """The player turns it off in Tuning, an Update writes 1 again: Remove still puts back 0."""
    core = _server(tmp_path)
    _applier(tmp_path).install(_ip())
    core.write_text(
        core.read_text(encoding="utf-8").replace(
            "EnablePlayerSettings = 1", "EnablePlayerSettings = 2"
        ),
        encoding="utf-8",
    )
    _untouched(_applier(tmp_path), _ip()).install(_ip())
    _applier(tmp_path).remove(_ip())
    assert _values(core) == ("0", "1")


def test_an_install_that_changed_nothing_keeps_no_value_to_put_back(tmp_path: Path) -> None:
    """The first install found 1 already; the player then set 0, and an Update wrote 1:
    the value from before Yu'lon's change is the player's 0, not the 1 from the start."""
    core = _server(tmp_path, STOCK.replace("EnablePlayerSettings = 0", "EnablePlayerSettings = 1"))
    _applier(tmp_path).install(_ip())
    core.write_text(
        core.read_text(encoding="utf-8").replace(
            "EnablePlayerSettings = 1", "EnablePlayerSettings = 0"
        ),
        encoding="utf-8",
    )
    _untouched(_applier(tmp_path), _ip()).install(_ip())
    _applier(tmp_path).remove(_ip())
    assert _values(core) == ("0", "1")


def test_restore_needs_a_default_to_put_back() -> None:
    with pytest.raises(ValueError, match="restore"):
        parse_manifest(
            {
                "schema_version": 1,
                "id": "x",
                "name": "X",
                "type": "mod",
                "game": "wow-wotlk",
                "conf": [{"file": CORE, "keys": [{"key": "K", "restore": True}]}],
            }
        )
