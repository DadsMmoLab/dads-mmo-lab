"""The Tuning tab's built-in Server rates card (T302).

One card for every game: XP from kills, quests and exploring, gold, the common
item qualities, reputation and honor, with plain labels and a 0-100 range.

The keys are spelled out here as LITERALS and never read from
`yulon.server_rates`: a test that asked the module which key to expect would
pass for any key the module named (`nine-ways-a-test-proves-nothing` #12).
The conf fixtures are the pinned forks' own lines, copied from each `conf.dist`
at the catalog's pin (cited per fixture), with the neighbouring keys a wrong
match would hit left in: TBC's `.Vanilla`/`.BC` twins, Vanilla's
`Rate.Pet.XP.Kill`, WotLK's `Rate.XP.Quest.DF`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from yulon import server_rates, tuning
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.manifest import ConfKey

RATE = ConfKey(key="Rate.XP.Kill", type="float", min=0, max=100)


# -- the number rule ----------------------------------------------------------


def test_a_rate_spelled_with_an_underscore_is_refused_by_the_spelling_rule() -> None:
    """`1_0` is 10 to Python's `float()` and 1 to the core's `atof`: two numbers, one line.

    In bounds either way, so only the spelling rule can refuse it -- `10` is
    accepted on the same key, which proves the bound is not what fired.
    """
    tuning.check(RATE, "10")
    with pytest.raises(tuning.TuningError, match=r"Rate\.XP\.Kill: `1_0` is not a number"):
        tuning.check(RATE, "1_0")


def test_a_rate_in_another_scripts_digits_is_refused_although_python_reads_it() -> None:
    """`float("١.٥")` is 1.5; the server's `std::stof` or `atof` reads no number at all.

    Codex review 2026-10-05. In bounds as 1.5, so only the spelling rule can refuse it.
    """
    assert float("١.٥") == 1.5
    with pytest.raises(tuning.TuningError, match="is not a number"):
        tuning.check(RATE, "١.٥")


def test_a_decimal_too_long_to_be_a_number_is_refused_on_a_key_with_no_bounds() -> None:
    """Codex review 2026-10-05: 400 digits pass the spelling rule and are `inf` to `float()`.

    No bounds on this key, so no bound can be what refuses it.
    """
    huge = "9" * 400
    tuning.check(ConfKey(key="K", type="float"), "9" * 30)
    with pytest.raises(tuning.TuningError, match="is too large to be a number"):
        tuning.check(ConfKey(key="K", type="float"), huge)


def test_a_decimal_past_what_the_servers_float_holds_is_refused_although_python_reads_it() -> None:
    """The cores read a rate into a C `float`, whose largest is about 3.4e38.

    39 nines is a finite Python number and `std::stof` throws `out_of_range` on it
    (mangos-classic and mangos-tbc `Config.cpp:134-139`), which ends the world server
    at start. No bounds on this key, so only the size rule can refuse it.
    """
    tuning.check(ConfKey(key="K", type="float"), "9" * 38)
    with pytest.raises(tuning.TuningError, match="is too large to be a number"):
        tuning.check(ConfKey(key="K", type="float"), "9" * 39)


def test_a_rate_with_more_than_four_decimals_is_refused_so_none_is_too_small_to_read() -> None:
    """Cold review 2026-10-05: `0.000...1` under a float's smallest normal value.

    In bounds (0 to 100), so only the decimals rule can refuse it -- and without that
    rule `std::stof` sets ERANGE and throws at world start. Four places keeps the
    smallest non-zero rate at 0.0001, which every core reads.
    """
    tiny = "0." + "0" * 40 + "1"
    tuning.check(RATE, "0.0001")
    tuning.check(RATE, "2.5000")
    with pytest.raises(tuning.TuningError, match="at most 4 digits after the point"):
        tuning.check(RATE, tiny)
    with pytest.raises(tuning.TuningError, match="at most 4 digits after the point"):
        tuning.check(RATE, "1.23456")


@pytest.mark.parametrize("value", ["1,5", "nan", "inf", "2x", "", " "])
def test_a_rate_that_is_not_a_decimal_number_is_refused(value: str) -> None:
    with pytest.raises(tuning.TuningError, match="is not a number"):
        tuning.check(RATE, value)


@pytest.mark.parametrize("value", ["0", "1", "2.5", "0.5", ".5", "100", " 3 ", "100.0"])
def test_a_decimal_rate_inside_its_bounds_is_accepted(value: str) -> None:
    tuning.check(RATE, value)


def test_a_rate_above_its_ceiling_is_refused_naming_the_ceiling() -> None:
    """`100.5` and not `101`: a check that compared whole numbers would let it through."""
    with pytest.raises(tuning.TuningError, match="100.5 is above the largest allowed value 100"):
        tuning.check(RATE, "100.5")


def test_a_rate_below_its_floor_is_refused_naming_the_floor() -> None:
    with pytest.raises(tuning.TuningError, match="-0.5 is below the smallest allowed value 0"):
        tuning.check(RATE, "-0.5")


def test_a_float_key_may_carry_bounds_and_a_text_key_still_may_not() -> None:
    assert ConfKey(key="K", type="float", min=0, max=100).max == 100
    with pytest.raises(ValueError, match="`min`/`max` are for"):
        ConfKey(key="K", type="text", min=0)


# -- how the panel draws a rate -------------------------------------------------


def _rate_row(**over: object) -> tuning.TuningRow:
    fields: dict[str, object] = {
        "module_id": "server-rates",
        "module_name": "Server rates",
        "family": "core",
        "file": "etc/mangosd.conf",
        "key": "Rate.XP.Kill",
        "label": "XP from kills",
        "explain": None,
        "type": "float",
        "min": 0,
        "max": 100,
        "default": "1",
        "current": "1",
        "installed": True,
        "backend": "conf",
        "read_only_reason": None,
    }
    return tuning.TuningRow(**{**fields, **over})  # type: ignore[arg-type]


def test_a_rate_gets_a_box_with_its_range_beside_it_and_no_free_text_chip() -> None:
    from yulon.ui.widgets import tuning_panel as tp

    row = _rate_row()
    assert tp.control_kind(row) == "box"
    assert tp.bounds_note(row) == "0–100"
    assert tp.CHIP_FREE_TEXT not in tp.row_chips(row)


def test_a_rate_box_takes_a_decimal_and_refuses_a_letter(qapp: object) -> None:
    """The box's own rule, typed through Qt's validator the way a key press is."""
    from PySide6.QtGui import QValidator
    from PySide6.QtWidgets import QLineEdit

    from yulon.ui.widgets import tuning_panel as tp

    editor = tp.RowEditor(_rate_row())
    field = editor.control
    assert isinstance(field, QLineEdit)
    rule = field.validator()
    assert rule is not None
    assert rule.validate("2.5", 3)[0] == QValidator.State.Acceptable
    assert rule.validate("2.5x", 4)[0] == QValidator.State.Invalid
    assert rule.validate("2,5", 3)[0] == QValidator.State.Invalid
    assert rule.validate("١.٥", 3)[0] == QValidator.State.Invalid
    field.setText("2.5")
    assert editor.changed and editor.value() == "2.5"


def test_a_rate_the_file_spells_oddly_is_shown_as_written_and_can_still_be_edited(
    qapp: object,
) -> None:
    """`1,5` in the file: shown, not blanked, and the box does not lock the person out."""
    from PySide6.QtWidgets import QLineEdit

    from yulon.ui.widgets import tuning_panel as tp

    editor = tp.RowEditor(_rate_row(current="1,5"))
    field = editor.control
    assert isinstance(field, QLineEdit)
    assert field.text() == "1,5"
    assert field.validator() is None, "a rule the text breaks would refuse every edit of it"
    field.setText("1.5")
    assert field.validator() is not None


# -- the card's rows, per game ----------------------------------------------------
#
# Each fixture is that fork's own lines at the catalog's pin, in the file's own
# order and spacing, with every value made DISTINCT (kills 2, quests 3, exploring
# 4, gold 5, grey..purple 6..10, reputation 11, honor 12) so a row mapped to the
# wrong key reads the wrong number. The neighbours a loose match would hit are
# kept and set to 0.25.

WOTLK_RATES = (
    # mod-playerbots/azerothcore-wotlk @7f12e89e,
    # worldserver.conf.dist:2439,2499-2503,2573,2850-2858
    "Rate.Reputation.Gain = 11\n"
    "Rate.XP.Kill      = 2\n"
    "Rate.XP.Quest     = 3\n"
    "Rate.XP.Quest.DF  = 0.25\n"
    "Rate.XP.Explore   = 4\n"
    "Rate.XP.Pet       = 0.25\n"
    "Rate.Honor = 12\n"
    "Rate.Drop.Item.Poor             = 6\n"
    "Rate.Drop.Item.Normal           = 7\n"
    "Rate.Drop.Item.Uncommon         = 8\n"
    "Rate.Drop.Item.Rare             = 9\n"
    "Rate.Drop.Item.Epic             = 10\n"
    "Rate.Drop.Item.Legendary        = 0.25\n"
    "Rate.Drop.Money                 = 5\n"
)
TBC_RATES = (
    # cmangos/mangos-tbc @75f9ae68, mangosd.conf.dist.in:1560-1594 (an excerpt)
    "Rate.Drop.Item.Poor = 6\n"
    "Rate.Drop.Item.Normal = 7\n"
    "Rate.Drop.Item.Uncommon = 8\n"
    "Rate.Drop.Item.Rare = 9\n"
    "Rate.Drop.Item.Epic = 10\n"
    "Rate.Drop.Item.Legendary = 0.25\n"
    "Rate.Drop.Item.Quest = 0.25\n"
    "Rate.Drop.Money = 5\n"
    "Rate.Pet.XP.Kill = 0.25\n"
    "Rate.XP.Kill    = 2\n"
    "Rate.XP.Quest   = 3\n"
    "Rate.XP.Explore = 4\n"
    "Rate.Pet.XP.Kill.Vanilla = 0.25\n"
    "Rate.XP.Kill.Vanilla  = 0.25\n"
    "Rate.XP.Kill.BC       = 0.25\n"
    "Rate.XP.Quest.Vanilla = 0.25\n"
    "Rate.XP.Quest.BC      = 0.25\n"
    "Rate.XP.Explore.Vanilla = 0.25\n"
    "Rate.XP.Explore.BC      = 0.25\n"
    "Rate.Honor = 12\n"
    "Rate.Reputation.Gain = 11\n"
    "Rate.Reputation.LowLevel.Kill    = 0.25\n"
)
VANILLA_RATES = (
    # cmangos/mangos-classic @8ec338a1, mangosd.conf.dist.in:1499-1526 (an excerpt)
    "Rate.Drop.Item.Poor = 6\n"
    "Rate.Drop.Item.Normal = 7\n"
    "Rate.Drop.Item.Uncommon = 8\n"
    "Rate.Drop.Item.Rare = 9\n"
    "Rate.Drop.Item.Epic = 10\n"
    "Rate.Drop.Item.Quest = 0.25\n"
    "Rate.Drop.Money = 5\n"
    "Rate.Pet.XP.Kill = 0.25\n"
    "Rate.XP.Kill    = 2\n"
    "Rate.XP.Quest   = 3\n"
    "Rate.XP.Explore = 4\n"
    "Rate.Honor = 12\n"
    "Rate.Reputation.Gain = 11\n"
    "Rate.Reputation.LowLevel.Kill    = 0.25\n"
)
TORTOISE_RATES = (
    # tortoise-wow/tortoise-wow @187af788, mangosd.conf.dist.in:1702-1716,1744,1776
    "Rate.Drop.Item.Poor = 6\n"
    "Rate.Drop.Item.Normal = 7\n"
    "Rate.Drop.Item.Uncommon = 8\n"
    "Rate.Drop.Item.Rare = 9\n"
    "Rate.Drop.Item.Epic = 10\n"
    "Rate.Drop.Item.Legendary = 0.25\n"
    "Rate.Drop.Money = 5\n"
    "\n"
    "# Rate.XP. XP rates (player's favorite, haha)\n"
    "\n"
    "Rate.XP.Kill = 2\n"
    "Rate.XP.Quest = 3\n"
    "Rate.XP.Explore = 4\n"
    "Rate.Honor = 12\n"
    "Rate.Reputation.Gain = 11\n"
)
CENTURION_RATES = (
    # thomasjteachey/TrinityCore112 @faac5fc9, worldserver.conf.dist:2492-2520,2585,2606
    "Rate.Drop.Item.Poor             = 6\n"
    "Rate.Drop.Item.Normal           = 7\n"
    "Rate.Drop.Item.Uncommon         = 8\n"
    "Rate.Drop.Item.Rare             = 9\n"
    "Rate.Drop.Item.Epic             = 10\n"
    "Rate.Drop.Item.Legendary        = 0.25\n"
    "Rate.Drop.Money                 = 5\n"
    "Rate.Drop.Item.ReferencedAmount = 0.25\n"
    "Rate.XP.Kill    = 2\n"
    "Rate.XP.Quest   = 3\n"
    "Rate.XP.Explore = 4\n"
    "Rate.Honor = 12\n"
    "Rate.Reputation.Gain = 11\n"
)

GAMES = {
    "wow-wotlk": ("env/dist/etc/worldserver.conf", WOTLK_RATES),
    "wow-tbc": ("etc/mangosd.conf", TBC_RATES),
    "wow-vanilla": ("etc/mangosd.conf", VANILLA_RATES),
    "wow-tortoise": ("etc/mangosd.conf", TORTOISE_RATES),
    "wow-centurion": ("etc/worldserver.conf", CENTURION_RATES),
}
"""Each game's world conf, server-relative, and its rate lines. Literals, never the module's."""

SHOWN = (
    ("Rate.XP.Kill", "XP from kills", "2"),
    ("Rate.XP.Quest", "XP from quests", "3"),
    ("Rate.XP.Explore", "XP from exploring", "4"),
    ("Rate.Drop.Money", "Gold dropped", "5"),
    ("Rate.Drop.Item.Poor", "Grey item drops", "6"),
    ("Rate.Drop.Item.Normal", "White item drops", "7"),
    ("Rate.Drop.Item.Uncommon", "Green item drops", "8"),
    ("Rate.Drop.Item.Rare", "Blue item drops", "9"),
    ("Rate.Drop.Item.Epic", "Purple item drops", "10"),
    ("Rate.Reputation.Gain", "Reputation gained", "11"),
    ("Rate.Honor", "Honor gained", "12"),
)
"""The card's rows, in order, with the value each fixture gives it."""


def _entry(game: str) -> CatalogEntry:
    return load_catalog().get(game)


def _lay(server: Path, game: str, text: str | None = None) -> Path:
    file, rates = GAMES[game]
    path = server / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rates if text is None else text, encoding="utf-8")
    return path


@pytest.mark.parametrize("game", list(GAMES))
def test_every_game_shows_the_eleven_rates_from_its_own_world_conf(
    tmp_path: Path, game: str
) -> None:
    _lay(tmp_path, game)
    rows = server_rates.rows(_entry(game), tmp_path)
    assert [(r.key, r.label, r.current) for r in rows] == list(SHOWN)
    file = GAMES[game][0]
    for row in rows:
        assert (row.file, row.type, row.min, row.max) == (file, "float", 0, 100), row.key
        assert (row.family, row.module_id, row.module_name) == (
            "core",
            "server-rates",
            "Server rates",
        )
        assert row.default == "1", "every fork's compiled default for these keys is 1.0f"
        assert row.editable and row.backend == "conf"
        assert tuning.apply_rule(row) == "restart"


@pytest.mark.parametrize("game", list(GAMES))
def test_a_save_check_uses_the_same_range_the_rows_show(game: str) -> None:
    spec = server_rates.conf_keys(_entry(game))
    assert list(spec) == [key for key, _label, _value in SHOWN]
    for key in spec.values():
        assert (key.type, key.min, key.max, key.default) == ("float", 0, 100, "1")


def test_a_rate_the_file_does_not_carry_still_has_its_row_at_the_compiled_default(
    tmp_path: Path,
) -> None:
    _lay(tmp_path, "wow-tbc", TBC_RATES.replace("Rate.Honor = 12\n", ""))
    honor = next(r for r in server_rates.rows(_entry("wow-tbc"), tmp_path) if r.key == "Rate.Honor")
    assert honor.current is None and honor.default == "1"


def test_no_card_while_the_world_conf_is_not_on_disk(tmp_path: Path) -> None:
    assert server_rates.rows(_entry("wow-tbc"), tmp_path) == ()


def test_a_game_the_card_has_no_reading_for_gets_no_card(tmp_path: Path) -> None:
    _lay(tmp_path, "wow-tbc")
    other = _entry("wow-tbc").model_copy(update={"id": "wow-other"})
    assert server_rates.rows(other, tmp_path) == ()
    assert server_rates.conf_keys(other) == {}
    assert server_rates.card_file(other) is None


@pytest.mark.parametrize("game", list(GAMES))
def test_the_rates_were_read_at_the_pin_this_game_builds(game: str) -> None:
    """A pin bump turns this red: the keys, defaults and lines cited are that commit's.

    Read them again at the new pin (`server_rates` names each file and line) and
    move `read_at` only once they still hold.
    """
    source = _entry(game).emulator.sources[0]
    assert server_rates.read_at(_entry(game)) == (source.repo, source.rev)


# -- the XP Rate Customization mod -----------------------------------------------


def _module_row(file: str, key: str) -> tuning.TuningRow:
    return _rate_row(
        module_id="xp-rates",
        module_name="XP Rate Customization",
        family="mod",
        file=file,
        key=key,
        type="text",
        min=None,
        max=None,
    )


def test_a_mod_row_on_a_key_the_card_writes_in_the_same_file_becomes_read_only() -> None:
    card = (_rate_row(file="etc/mangosd.conf", key="Rate.XP.Kill"),)
    (row,) = server_rates.yield_to_card([_module_row("etc/mangosd.conf", "Rate.XP.Kill")], card)
    assert not row.editable
    assert row.read_only_reason == server_rates.ON_THE_RATES_CARD


def test_the_same_key_in_another_file_is_another_setting_and_keeps_its_row() -> None:
    """One rule differs from the case above -- the file -- and the row stays editable."""
    card = (_rate_row(file="etc/mangosd.conf", key="Rate.XP.Kill"),)
    mine = _module_row("etc/modules/mine.conf", "Rate.XP.Kill")
    assert server_rates.yield_to_card([mine], card) == (mine,)


def test_another_key_in_the_same_file_keeps_its_row() -> None:
    """And here only the key differs."""
    card = (_rate_row(file="etc/mangosd.conf", key="Rate.XP.Kill"),)
    mine = _module_row("etc/mangosd.conf", "Rate.XP.Kill.Elite")
    assert server_rates.yield_to_card([mine], card) == (mine,)


# -- through the real Tuning tab ---------------------------------------------------


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The view's jobs run inline, as in `test_controller_view`."""
    from yulon.ui import controller_view as view_module
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(view_module, "threaded_job_runner", lambda _parent: run_inline)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> Any:
    """`test_controller_view`'s Docker-free `runner.run`."""
    from tests.test_controller_view import _Ps
    from yulon import runner

    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


STORES = {
    "wow-wotlk": "yulon.controller_wow_wotlk.modules",
    "wow-tbc": "yulon.controller_wow_tbc.modules",
    "wow-vanilla": "yulon.controller_wow_vanilla.modules",
    "wow-tortoise": "yulon.controller_wow_tortoise.modules",
}


def _view(
    ps: Any, server: Path, game: str, mods: frozenset[str] = frozenset(), *, reset: bool = False
) -> Any:
    """The real `ControllerView` for `game`, its catalog's own store, these mods installed.

    `reset` wires Reset to default the way the app does (`route_for_app`), quiet.
    """
    import importlib

    from tests.test_controller_view import RESET_QUIET, _services
    from yulon import reset_defaults
    from yulon.ui.controller_view import ControllerView

    services = _services(ps, server, [])
    if game in STORES:
        object.__setattr__(services, "store", importlib.import_module(STORES[game]).store())
    object.__setattr__(services, "installed_modules", lambda: {"mod": mods} if mods else {})
    if reset:
        route = reset_defaults.route_for_app(_entry(game), server, seams=RESET_QUIET)
        object.__setattr__(services, "reset_settings", route)
    return ControllerView(_entry(game), services, status_poll_ms=0)


def _rates_card(view: Any) -> Any:
    return view.tuning_panel.card(("core", "server-rates"))


@pytest.mark.parametrize("game", list(GAMES))
def test_the_tuning_tab_shows_the_rates_card_first_with_the_files_values(
    qapp: object, ps: Any, tmp_path: Path, game: str
) -> None:
    _lay(tmp_path, game)
    view = _view(ps, tmp_path, game)
    cards = view.tuning_panel.cards()
    assert (cards[0].card.family, cards[0].card.module_id) == ("core", "server-rates")
    assert cards[0].title_label.text() == "Server rates"
    editors = _rates_card(view).editors
    assert [(k, e.label.text(), e.value()) for k, e in editors.items()] == list(SHOWN)
    assert editors["Rate.XP.Kill"].bounds_label.text() == "0–100"
    assert _rates_card(view).save_button is not None


def test_no_rates_card_before_the_server_has_its_world_conf(
    qapp: object, ps: Any, tmp_path: Path
) -> None:
    view = _view(ps, tmp_path, "wow-tbc")
    assert all(c.card.module_id != "server-rates" for c in view.tuning_panel.cards())


@pytest.mark.parametrize("game", list(GAMES))
def test_a_saved_rate_lands_in_this_games_world_conf_and_owes_a_restart(
    qapp: object, ps: Any, tmp_path: Path, game: str
) -> None:
    path = _lay(tmp_path, game)
    before = path.read_text(encoding="utf-8")
    view = _view(ps, tmp_path, game)
    card = _rates_card(view)
    card.editors["Rate.Drop.Money"].control.setText("2.5")
    card.editors["Rate.XP.Quest"].control.setText("0.5")
    card.save_button.click()

    after = path.read_text(encoding="utf-8")
    want = before.replace("Money = 5", "Money = 2.5").replace(
        "Rate.XP.Quest   = 3", "Rate.XP.Quest   = 0.5"
    )
    want = want.replace("Rate.XP.Quest     = 3", "Rate.XP.Quest     = 0.5")
    want = want.replace("Rate.XP.Quest = 3", "Rate.XP.Quest = 0.5")
    want = want.replace(
        "Rate.Drop.Money                 = 5", "Rate.Drop.Money                 = 2.5"
    )
    assert after == want, "only the two moved values changed, every other byte kept"
    (backup,) = tuning.backups_of(path)
    assert backup.read_text(encoding="utf-8") == before
    assert view._tuning_owed == {"restart": {GAMES[game][0]}}
    assert not view.tuning_banner.isHidden()
    redrawn = _rates_card(view).editors
    assert redrawn["Rate.Drop.Money"].value() == "2.5" and not redrawn["Rate.Drop.Money"].changed


def test_tbc_keeps_its_old_world_and_outland_xp_twins_when_kill_xp_moves(
    qapp: object, ps: Any, tmp_path: Path
) -> None:
    path = _lay(tmp_path, "wow-tbc")
    view = _view(ps, tmp_path, "wow-tbc")
    card = _rates_card(view)
    card.editors["Rate.XP.Kill"].control.setText("3")
    card.save_button.click()
    text = path.read_text(encoding="utf-8")
    assert "Rate.XP.Kill    = 3\n" in text
    assert "Rate.XP.Kill.Vanilla  = 0.25\n" in text and "Rate.XP.Kill.BC       = 0.25\n" in text
    assert "Rate.Pet.XP.Kill = 0.25\n" in text
    assert "Rate.XP.Kill.Vanilla" in card.editors["Rate.XP.Kill"].explain_label.text()


@pytest.mark.parametrize(
    ("typed", "why"),
    [
        ("100.5", "100.5 is above the largest allowed value 100"),
        ("-1", "-1 is below the smallest allowed value 0"),
        ("1_0", "`1_0` is not a number"),
    ],
)
def test_a_rate_out_of_range_is_refused_at_save_and_nothing_is_written(
    qapp: object, ps: Any, tmp_path: Path, typed: str, why: str
) -> None:
    path = _lay(tmp_path, "wow-vanilla")
    before = path.read_bytes()
    view = _view(ps, tmp_path, "wow-vanilla")
    card = _rates_card(view)
    card.editors["Rate.XP.Kill"].control.setText("7")  # a good value on the same card
    card.editors["Rate.Honor"].control.setText(typed)
    card.save_button.click()

    assert path.read_bytes() == before, "one bad value on the card writes none of them"
    assert not list(tmp_path.rglob("*.bak"))
    said = view.tuning_report.toPlainText()
    assert "nothing was written" in said and f"Rate.Honor: {why}" in said
    assert view.tuning_banner.isHidden()


def test_reset_to_default_puts_the_rates_back_and_the_card_shows_them(
    qapp: object, ps: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WotLK's default is the `.dist` beside the conf (T94); the card reads it again after."""
    from tests.test_controller_view import (
        TUNING_RESET_ALL,
        _menu_action,
        _reset_yes,
        _wotlk_server,
    )

    _wotlk_server(tmp_path)
    path = _lay(tmp_path, "wow-wotlk")
    dist = path.with_name(path.name + ".dist")
    dist.write_text(WOTLK_RATES.replace("= 2\n", "= 1\n").replace("= 5\n", "= 1\n"), "utf-8")
    view = _view(ps, tmp_path, "wow-wotlk", reset=True)
    assert _rates_card(view).editors["Rate.XP.Kill"].value() == "2"
    _reset_yes(monkeypatch)
    _menu_action(view, TUNING_RESET_ALL).trigger()

    assert path.read_text(encoding="utf-8") == dist.read_text(encoding="utf-8")
    editors = _rates_card(view).editors
    assert editors["Rate.XP.Kill"].value() == "1" and editors["Rate.Drop.Money"].value() == "1"


@pytest.mark.parametrize("game", ["wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise"])
def test_with_the_xp_mod_installed_the_card_is_the_one_writer_of_its_keys(
    qapp: object, ps: Any, tmp_path: Path, game: str
) -> None:
    path = _lay(tmp_path, game)
    view = _view(ps, tmp_path, game, mods=frozenset({"xp-rates"}))
    order = [(c.card.family, c.card.module_id) for c in view.tuning_panel.cards()]
    assert order == [("core", "server-rates"), ("mod", "xp-rates")], "the built-in card first"
    mod = view.tuning_panel.card(("mod", "xp-rates"))
    assert sorted(mod.editors) == ["Rate.XP.Explore", "Rate.XP.Kill", "Rate.XP.Quest"]
    for editor in mod.editors.values():
        assert editor.control is None, "a second control on the same key is a second writer"
        assert editor.reason_label.text() == server_rates.ON_THE_RATES_CARD
    assert mod.editors["Rate.XP.Kill"].value_label.text() == "2", "the file's value, shown"
    assert mod.save_button is None

    rates = _rates_card(view)
    rates.editors["Rate.XP.Kill"].control.setText("4")
    rates.save_button.click()
    assert tuning.conf_value(path.read_text(encoding="utf-8"), "Rate.XP.Kill") == "4"
    assert (
        view.tuning_panel.card(("mod", "xp-rates")).editors["Rate.XP.Kill"].value_label.text()
        == "4"
    )


def test_reset_to_default_still_keeps_the_xp_an_installed_mod_set(
    qapp: object, ps: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner decision 5 is unchanged: the mod's keys keep their value, the rest go back."""
    from tests.test_controller_view import (
        TUNING_RESET_ALL,
        _menu_action,
        _reset_yes,
        _wotlk_server,
    )

    _wotlk_server(tmp_path)
    path = _lay(tmp_path, "wow-wotlk")
    path.with_name(path.name + ".dist").write_text(
        "Rate.XP.Kill      = 1\nRate.Drop.Money                 = 1\n", "utf-8"
    )
    view = _view(ps, tmp_path, "wow-wotlk", mods=frozenset({"xp-rates"}), reset=True)
    _reset_yes(monkeypatch)
    _menu_action(view, TUNING_RESET_ALL).trigger()

    editors = _rates_card(view).editors
    assert editors["Rate.XP.Kill"].value() == "2", "the mod's key was carried over"
    assert editors["Rate.Drop.Money"].value() == "1", "the card's own key went back"


def test_tortoise_says_its_slow_and_steady_characters_get_no_kill_xp_boost(
    tmp_path: Path,
) -> None:
    """`Formulas.h:140-166` at the pin: the challenge skips `Rate.XP.Kill`."""
    _lay(tmp_path, "wow-tortoise")
    rows = {r.key: r for r in server_rates.rows(_entry("wow-tortoise"), tmp_path)}
    assert "Slow and Steady" in (rows["Rate.XP.Kill"].explain or "")
    assert "Slow and Steady" not in (rows["Rate.XP.Quest"].explain or "")
    tbc = {r.key: r for r in server_rates.rows(_entry("wow-tbc"), _lay_dir(tmp_path, "wow-tbc"))}
    assert "Slow and Steady" not in (tbc["Rate.XP.Kill"].explain or "")


def _lay_dir(tmp_path: Path, game: str) -> Path:
    server = tmp_path / game
    _lay(server, game)
    return server


def test_a_key_a_game_does_not_have_gets_no_row_on_that_game(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No fork lacks one of the eleven today; the day one does, its row is left out."""
    from dataclasses import replace

    tbc = server_rates._GAMES["wow-tbc"]
    fewer = tuple(key for key in tbc.keys if key != "Rate.Honor")
    monkeypatch.setitem(server_rates._GAMES, "wow-tbc", replace(tbc, keys=fewer))
    _lay(tmp_path, "wow-tbc")
    keys = [r.key for r in server_rates.rows(_entry("wow-tbc"), tmp_path)]
    assert keys == [key for key, _label, _value in SHOWN if key != "Rate.Honor"]
    assert "Rate.Honor" not in server_rates.conf_keys(_entry("wow-tbc"))


def test_the_raw_editor_still_lists_the_world_conf_the_xp_mod_named(
    qapp: object, ps: Any, tmp_path: Path
) -> None:
    """The mod's rows are read-only only on screen: its file stays one the editor opens."""
    _lay(tmp_path, "wow-tbc")
    view = _view(ps, tmp_path, "wow-tbc", mods=frozenset({"xp-rates"}))
    assert "etc/mangosd.conf" in view._tuning_files()


def test_with_the_xp_mod_installed_the_cards_xp_rows_say_what_the_mod_still_does(
    qapp: object, ps: Any, tmp_path: Path
) -> None:
    """Codex adversarial review 2026-10-05: the mod's removal and Reset to default still act.

    Said on the card where the value is changed, and only on the rows the mod names.
    """
    _lay(tmp_path, "wow-tbc")
    view = _view(ps, tmp_path, "wow-tbc", mods=frozenset({"xp-rates"}))
    editors = _rates_card(view).editors
    for key in ("Rate.XP.Kill", "Rate.XP.Quest", "Rate.XP.Explore"):
        said = editors[key].explain_label.text()
        assert "XP Rate Customization is installed" in said, key
        assert "Reset to default keeps this value" in said and "back to 1" in said
    assert "XP Rate Customization" not in editors["Rate.Drop.Money"].explain_label.text()

    bare = _view(ps, tmp_path, "wow-tbc")
    assert (
        "XP Rate Customization"
        not in _rates_card(bare).editors["Rate.XP.Kill"].explain_label.text()
    )


def test_only_the_xp_mod_names_a_rate_the_card_writes_and_its_removal_writes_1() -> None:
    """What the card's sentence about the mod says, held against every shipped manifest.

    A second module naming one of these keys (in its conf rows or a patch) would be
    a writer the card's sentences do not describe; the xp mod's remove patches are
    what "back to 1" claims.
    """
    import json
    import re

    keys = [key for key, _label, _value in SHOWN]
    root = Path(__file__).resolve().parents[1] / "manifests"
    naming: list[str] = []
    for path in sorted(root.glob("wow-*/**/*.json")):
        text = path.read_text(encoding="utf-8")
        if any(key in text or re.escape(key).replace("\\", "\\\\") in text for key in keys):
            naming.append(path.relative_to(root).as_posix())
    assert naming == [
        f"wow-{game}/mods/xp-rates.json" for game in ("tbc", "tortoise", "vanilla", "wotlk")
    ]
    for name in naming:
        data = json.loads((root / name).read_text(encoding="utf-8"))
        removes = [p["replace"] for p in data["patches"] if p["when"] == "remove"]
        assert removes and all(re.search(r"=\s*1$", r) for r in removes), name


def test_a_mod_row_the_card_owns_is_chipped_as_set_there_not_as_a_later_version() -> None:
    """`read-only in this version` would promise a version that writes it; none will."""
    from yulon.ui.widgets import tuning_panel as tp

    card = (_rate_row(file="etc/mangosd.conf", key="Rate.XP.Kill"),)
    (row,) = server_rates.yield_to_card([_module_row("etc/mangosd.conf", "Rate.XP.Kill")], card)
    assert tp.row_chips(row) == ("set on the Server rates card",)
    lua = _module_row("x.lua", "K")
    lua = tuning.TuningRow(**{**lua.__dict__, "read_only_reason": tuning.LUA_IS_NOT_IN_V1})
    assert tp.row_chips(lua) == (tp.CHIP_READ_ONLY,)


def test_the_card_names_only_a_module_that_writes_the_same_key_in_the_same_file() -> None:
    """A module naming `Rate.XP.Kill` in a conf of its own sets a different value."""
    card = (_rate_row(file="etc/mangosd.conf", key="Rate.XP.Kill", explain="Kills."),)
    elsewhere = _module_row("etc/modules/mine.conf", "Rate.XP.Kill")
    assert server_rates.shared_with(card, [elsewhere]) == card
    (said,) = server_rates.shared_with(card, [_module_row("etc/mangosd.conf", "Rate.XP.Kill")])
    assert said.explain == "Kills. " + server_rates.SHARED_WITH.format(
        module="XP Rate Customization"
    )


@pytest.mark.parametrize(
    ("game", "quests", "level_cap"),
    [
        ("wow-wotlk", False, False),  # `Rate.RewardQuestMoney`, QuestDef.cpp:255
        ("wow-tbc", True, False),  # Quests/QuestDef.cpp:216-222
        ("wow-vanilla", True, False),  # Quests/QuestDef.cpp:211-217
        ("wow-tortoise", True, True),  # QuestDef.cpp:204-222
        ("wow-centurion", False, False),  # `Rate.Money.Quest`, QuestDef.cpp:317-319
    ],
)
def test_the_gold_row_says_where_it_also_multiplies_quest_money(
    tmp_path: Path, game: str, quests: bool, level_cap: bool
) -> None:
    """Codex adversarial review 2026-10-05: CMaNGOS forks scale quest rewards by this key."""
    _lay(tmp_path, game)
    gold = next(r for r in server_rates.rows(_entry(game), tmp_path) if r.key == "Rate.Drop.Money")
    said = gold.explain or ""
    assert ("money quests reward" in said) is quests
    assert ("level cap" in said) is level_cap


# -- installing the XP mod after the card has set XP (cold review 2026-10-05) -----


@pytest.mark.parametrize(
    ("game", "want"),
    [
        ("wow-wotlk", {"kill": "2", "quest": "3", "explore": "4"}),
        ("wow-tbc", {"kill": "2", "quest": "3", "explore": "4"}),
        ("wow-vanilla", {"kill": "2", "quest": "3", "explore": "4"}),
        # One answer for all three keys: it starts at the first, XP from kills.
        ("wow-tortoise", {"xp_rate": "2"}),
    ],
)
def test_the_xp_mods_questions_start_at_what_the_card_says_now(
    tmp_path: Path, game: str, want: dict[str, str]
) -> None:
    import importlib

    _lay(tmp_path, game)
    manifest = importlib.import_module(STORES[game]).store().load("mod", "xp-rates")
    rows = server_rates.rows(_entry(game), tmp_path)
    assert server_rates.prompt_values(manifest, rows) == want


def test_a_module_whose_questions_feed_no_rate_starts_nowhere_new(tmp_path: Path) -> None:
    import importlib

    _lay(tmp_path, "wow-wotlk")
    store = importlib.import_module(STORES["wow-wotlk"]).store()
    rows = server_rates.rows(_entry("wow-wotlk"), tmp_path)
    assert server_rates.prompt_values(store.load("module", "mod-ah-bot"), rows) == {}


def test_installing_the_xp_mod_asks_from_the_cards_values_and_says_it_replaces_them(
    qapp: object, ps: Any, tmp_path: Path
) -> None:
    """The box shows the card's 2, not the mod's default 1 nor last install's answer."""
    _lay(tmp_path, "wow-tbc")
    seen: dict[str, Any] = {}

    def asker(parent: object, manifest: Any, prompts: Any, **kw: Any) -> dict[str, str]:
        seen["defaults"] = {p.key: p.default for p in prompts}
        seen.update(kw)
        return {p.key: p.default for p in prompts}

    view = _view(ps, tmp_path, "wow-tbc")
    view._prompt_asker = asker
    applier = view.services.applier
    applier.remembered_answers = lambda manifest: {"kill": "9", "quest": "9", "explore": "9"}
    view.modules_panel.select("xp-rates")
    view._module_action("install")

    assert seen["defaults"] == {"kill": "2", "quest": "3", "explore": "4"}
    assert "kill" not in (seen.get("remembered") or {}), "an old answer must not beat the card"
    (note,) = seen["notes"]
    assert "Server rates card" in note and "XP from kills" in note
    assert applier.values == [{"kill": "2", "quest": "3", "explore": "4"}]


def test_a_module_that_feeds_no_rate_is_asked_exactly_as_before(
    qapp: object, ps: Any, tmp_path: Path
) -> None:
    _lay(tmp_path, "wow-wotlk")
    seen: list[dict[str, Any]] = []

    def asker(parent: object, manifest: Any, prompts: Any, **kw: Any) -> dict[str, str]:
        seen.append(kw)
        return {"bot_guid": "42", "bot_account": "7"}

    view = _view(ps, tmp_path, "wow-wotlk")
    view._prompt_asker = asker
    view.modules_panel.select("mod-ah-bot")
    view._module_action("install")
    assert seen and "notes" not in seen[0]


def test_the_install_question_shows_the_note_it_is_handed(qapp: object) -> None:
    from PySide6.QtWidgets import QLabel

    from yulon.controller_wow_tbc import modules as tbc_modules
    from yulon.ui.widgets.manifest_prompt import ManifestPromptDialog

    manifest = tbc_modules.store().load("mod", "xp-rates")
    dialog = ManifestPromptDialog(None, manifest, manifest.prompts, notes=("The card note.",))
    assert "The card note." in [label.text() for label in dialog.findChildren(QLabel)]


# -- one answer, three keys; and the answer itself (scoped re-review 2026-10-05) ---

SAME_NUMBER = (
    "It starts at XP from kills. XP from quests and XP from exploring will be set to the "
    "same number."
)


def test_tortoises_one_xp_answer_says_it_sets_all_three_when_the_card_differs(
    tmp_path: Path,
) -> None:
    from yulon.controller_wow_tortoise import modules as tortoise_modules

    _lay(tmp_path, "wow-tortoise")  # kills 2, quests 3, exploring 4
    manifest = tortoise_modules.store().load("mod", "xp-rates")
    note = server_rates.prompt_note(manifest, server_rates.rows(_entry("wow-tortoise"), tmp_path))
    assert note is not None and note.endswith(" " + SAME_NUMBER)


def test_tortoises_one_xp_answer_says_nothing_more_when_the_card_agrees(tmp_path: Path) -> None:
    from yulon.controller_wow_tortoise import modules as tortoise_modules

    same = TORTOISE_RATES.replace("= 3\n", "= 2\n").replace("= 4\n", "= 2\n")
    _lay(tmp_path, "wow-tortoise", same)
    manifest = tortoise_modules.store().load("mod", "xp-rates")
    note = server_rates.prompt_note(manifest, server_rates.rows(_entry("wow-tortoise"), tmp_path))
    assert note is not None and "same number" not in note and "starts at XP" not in note


def test_wotlks_three_answers_never_say_they_set_one_number(tmp_path: Path) -> None:
    """Three questions for three keys: the card differing is no reason for the sentence."""
    from yulon.controller_wow_wotlk import modules as wotlk_modules

    _lay(tmp_path, "wow-wotlk")
    manifest = wotlk_modules.store().load("mod", "xp-rates")
    note = server_rates.prompt_note(manifest, server_rates.rows(_entry("wow-wotlk"), tmp_path))
    assert note is not None and "same number" not in note


@pytest.mark.parametrize(
    ("game", "answers"),
    [
        ("wow-tortoise", {"xp_rate": "{v}"}),
        ("wow-wotlk", {"kill": "1", "quest": "{v}", "explore": "1"}),
    ],
)
@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("1e-45", "is not a number"),
        ("1.23456", "use at most 4 digits after the point"),
        ("1" + "0" * 39, "is too large to be a number"),
        ("150", "is above the largest allowed value 100"),
    ],
)
def test_an_xp_mod_answer_the_server_cannot_read_is_refused_and_nothing_is_installed(
    qapp: object, ps: Any, tmp_path: Path, game: str, answers: dict[str, str], value: str, why: str
) -> None:
    """The mod's own question took `1e-45` (Tortoise's is a `string`), and mangosd died at start."""
    _lay(tmp_path, game)
    given = {k: v.format(v=value) for k, v in answers.items()}
    view = _view(ps, tmp_path, game)
    view._prompt_asker = lambda parent, manifest, prompts, **_: given
    view.services.client_dir = tmp_path / "client"
    view.modules_panel.select("xp-rates")
    view._module_action("install")

    applier = view.services.applier
    assert applier.installed == [], "a value the server cannot read reached the applier"
    said = view.module_report.toPlainText()
    assert "nothing on this machine was changed" in said and why in said, said


@pytest.mark.parametrize(
    ("game", "answers"),
    [
        ("wow-tortoise", {"xp_rate": "2.5"}),
        ("wow-wotlk", {"kill": "0.0001", "quest": "3", "explore": "100"}),
    ],
)
def test_an_xp_mod_answer_the_server_reads_installs(
    qapp: object, ps: Any, tmp_path: Path, game: str, answers: dict[str, str]
) -> None:
    _lay(tmp_path, game)
    view = _view(ps, tmp_path, game)
    view._prompt_asker = lambda parent, manifest, prompts, **_: answers
    view.services.client_dir = tmp_path / "client"
    view.modules_panel.select("xp-rates")
    view._module_action("install")
    assert view.services.applier.installed == ["xp-rates"]
    assert view.services.applier.values == [answers]
