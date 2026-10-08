"""T554 Y5b: the Unbound card on the Tuning tab.

Three switches over `mod_unbound.conf`, all off, shown only for an entry whose
`confs_from_dist` names that conf (data, not an id), saved through
`unbound_settings.write` so only 0 and 1 reach the file.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from yulon import unbound_settings
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.ui.controller_view import ControllerServices, ControllerView

CARD = ("core", "unbound")
DIST = (
    "[worldserver]\n"
    "# Unbound.ReagentFree\n"
    "Unbound.ReagentFree = 0\n"
    "Unbound.InstantSummons = 0\n"
    "Unbound.AutoBuff = 0\n"
)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> Any:
    """`test_controller_view`'s Docker-free `runner.run`."""
    from tests.test_controller_view import _Ps
    from yulon import runner

    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def lay(server: Path, text: str = DIST) -> Path:
    path = server / unbound_settings.FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def view_for(entry: CatalogEntry, server: Path) -> ControllerView:
    return ControllerView(entry, ControllerServices.for_entry(entry, server), status_poll_ms=0)


def unbound() -> CatalogEntry:
    return load_catalog().get("wow-unbound")


def card_of(view: ControllerView) -> Any:
    return view.tuning_panel.card(CARD)


def test_the_unbound_card_has_three_switches_all_off_and_says_when_it_takes_effect(
    qapp: object, ps: Any, tmp_path: Path
) -> None:
    lay(tmp_path)
    view = view_for(unbound(), tmp_path)
    card = card_of(view)
    assert card.title_label.text() == "Unbound"
    assert list(card.editors) == [
        "Unbound.ReagentFree",
        "Unbound.InstantSummons",
        "Unbound.AutoBuff",
    ]
    assert [editor.value() for editor in card.editors.values()] == ["0", "0", "0"]
    for editor in card.editors.values():
        assert "Takes effect at the next start" in editor.explain_label.text()
    labels = [e.label.text() for e in card.editors.values()]
    assert labels == [
        "Free casting reagents",
        "Instant class summons",
        "#buffs auto-buff command",
    ]


def test_the_card_comes_after_the_server_rates_card(qapp: object, ps: Any, tmp_path: Path) -> None:
    lay(tmp_path)
    (tmp_path / "env/dist/etc/worldserver.conf").write_text("Rate.XP.Kill = 1\n", encoding="utf-8")
    cards = view_for(unbound(), tmp_path).tuning_panel.cards()
    ids = [(c.card.family, c.card.module_id) for c in cards]
    assert ids.index(("core", "server-rates")) < ids.index(CARD)


def test_wotlk_has_no_unbound_card(qapp: object, ps: Any, tmp_path: Path) -> None:
    lay(tmp_path)  # even with the file lying there
    view = view_for(load_catalog().get("wow-wotlk"), tmp_path)
    assert all((c.card.family, c.card.module_id) != CARD for c in view.tuning_panel.cards())


def test_the_card_is_keyed_on_the_entrys_data_not_on_its_id(
    qapp: object, ps: Any, tmp_path: Path
) -> None:
    """A scratch entry that names the conf gets the card; WotLK's data without it does not."""
    lay(tmp_path)
    data = unbound().model_dump(mode="json", by_alias=True, exclude_none=True)
    data["id"] = "wow-scratch-ac"
    scratch = CatalogEntry.model_validate(data)
    # The scratch id has no controller factory, so ask the pure part of the answer.
    assert unbound_settings.shown_for(scratch)
    plain = copy.deepcopy(data)
    plain["install"]["native"]["azerothcore"]["confs_from_dist"] = [
        "env/dist/etc/modules/playerbots.conf"
    ]
    assert not unbound_settings.shown_for(CatalogEntry.model_validate(plain))
    assert not unbound_settings.shown_for(load_catalog().get("wow-wotlk"))
    assert not unbound_settings.shown_for(load_catalog().get("wow-tbc"))


def test_no_card_before_the_server_has_laid_the_conf(qapp: object, ps: Any, tmp_path: Path) -> None:
    view = view_for(unbound(), tmp_path)
    assert all((c.card.family, c.card.module_id) != CARD for c in view.tuning_panel.cards())


def test_turning_a_switch_on_changes_that_line_only_and_owes_a_restart(
    qapp: object, ps: Any, tmp_path: Path
) -> None:
    path = lay(tmp_path)
    before = path.read_text(encoding="utf-8")
    view = view_for(unbound(), tmp_path)
    card = card_of(view)
    card.editors["Unbound.ReagentFree"].control.setChecked(True)
    card.save_button.click()
    assert path.read_text(encoding="utf-8") == before.replace(
        "Unbound.ReagentFree = 0", "Unbound.ReagentFree = 1"
    )
    assert view._tuning_owed == {"restart": {unbound_settings.FILE}}
    assert card_of(view).editors["Unbound.ReagentFree"].value() == "1"
    assert card_of(view).editors["Unbound.AutoBuff"].value() == "0"


def test_a_switch_turned_back_off_writes_zero(qapp: object, ps: Any, tmp_path: Path) -> None:
    path = lay(tmp_path, DIST.replace("AutoBuff = 0", "AutoBuff = 1"))
    view = view_for(unbound(), tmp_path)
    card = card_of(view)
    assert card.editors["Unbound.AutoBuff"].value() == "1"
    card.editors["Unbound.AutoBuff"].control.setChecked(False)
    card.save_button.click()
    assert "Unbound.AutoBuff = 0" in path.read_text(encoding="utf-8")


def test_a_conf_that_spells_true_is_not_written_a_one_and_is_refused(
    qapp: object, ps: Any, tmp_path: Path
) -> None:
    """The switch writes in the file's own spelling; the module reads only `1`, so `true`
    would silently mean off. The save says so and writes nothing."""
    path = lay(tmp_path, DIST.replace("InstantSummons = 0", "InstantSummons = false"))
    before = path.read_bytes()
    view = view_for(unbound(), tmp_path)
    card = card_of(view)
    card.editors["Unbound.InstantSummons"].control.setChecked(True)
    card.save_button.click()
    assert path.read_bytes() == before
    assert "InstantSummons" in view.tuning_report.toPlainText()
