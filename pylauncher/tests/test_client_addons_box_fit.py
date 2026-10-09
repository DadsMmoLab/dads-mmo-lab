"""T613 PR-3: the add-on box takes its place in the Modules tab's fit (T83's ladder)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from tests.test_client_addons_box import _manifest, _Route
from tests.test_controller_view import (
    WOTLK,
    _a_rebuild_is_owed,
    _at,
    _controller_in_the_real_window,
    _drawn_under_their_minimum,
    _Ps,
    _services,
    _with_the_card_buttons,
    ps,  # noqa: F401
)
from yulon.ui import controller_view as cv
from yulon.ui.controller_view import ControllerView


def _modules(ps_: _Ps, tmp_path: Path, *, box: bool, rows: bool) -> tuple[ControllerView, Any, Any]:
    services = _services(ps_, tmp_path, [])
    _with_the_card_buttons(services)
    if box:
        services.client_addons = cast(Any, _Route(rows=[_manifest()] if rows else []))
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    window, tab = _controller_in_the_real_window(view, "Modules")
    return view, window, tab


@pytest.mark.parametrize("rows", [True, False], ids=["with-an-add-on-listed", "none-listed"])
@pytest.mark.parametrize("size", [(960, 600), (960, 640), (1280, 800)])
def test_the_list_keeps_its_floor_with_the_box_on_a_tab_that_is_short_of_height(
    qapp: object, ps: _Ps, tmp_path: Path, size: tuple[int, int], rows: bool
) -> None:
    """960x600 is the smallest window; a rebuild banner and a report are the worst state.

    Before the box joined `_TabFit` the list was 40px there (a scrollbar and no row) and
    the layout drew the module panel under its own minimum.
    """
    view, window, tab = _modules(ps, tmp_path, box=True, rows=rows)
    _a_rebuild_is_owed(view)
    _at(window, size)

    assert view.modules_panel.height() >= cv.MODULE_LIST_MIN_HEIGHT, view.modules_panel.height()
    assert _drawn_under_their_minimum(tab) == []


def test_the_box_keeps_its_sentence_where_the_card_does_and_never_takes_it_back(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """One rung with the card: whole at the heights the card is whole, and monotone in height."""
    view, window, tab = _modules(ps, tmp_path, box=True, rows=True)
    _a_rebuild_is_owed(view)
    shown_at: int | None = None
    for height in range(600, 1100, 40):
        _at(window, (1280, height))
        sentence = view.addon_box.note.isVisible()
        assert sentence == view.custom_module_card.isVisible(), height
        assert _drawn_under_their_minimum(tab) == [], height
        if sentence and shown_at is None:
            shown_at = height
        if shown_at is not None:
            assert sentence, f"shown at {shown_at}, gone again at {height}"
    assert shown_at is not None, "control: the sentence is shown at some height"
    assert shown_at > 600, "control: and not at all of them"


def test_a_hidden_box_costs_the_tab_nothing(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    with_box = _modules(ps, tmp_path / "a", box=True, rows=False)
    without = _modules(ps, tmp_path / "b", box=False, rows=False)
    for view, window, _tab in (with_box, without):
        _at(window, (960, 640))
    assert without[0].addon_box.isHidden()
    assert without[0].modules_panel.height() > with_box[0].modules_panel.height()


def test_the_box_bills_at_least_what_it_needs_frame_and_title_included(qapp: object) -> None:
    from yulon.ui.widgets.client_addons_box import ClientAddonsBox

    box = ClientAddonsBox()
    box.resize(800, 300)
    box.show()
    need = box.minimumSizeHint().height()
    assert box.minimum_for(800, True) >= need
    assert box.minimum_for(800, False) < box.minimum_for(800, True)
    box.set_compact(True)
    assert box.minimum_for(800, True) >= need


def test_what_the_box_bills_is_the_height_it_is_drawn_at(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """On a tab short of height the box is drawn at its minimum: that is what the fit bills."""
    view, window, _tab = _modules(ps, tmp_path, box=True, rows=True)
    _a_rebuild_is_owed(view)
    _at(window, (960, 600))
    box = view.addon_box
    assert box.note.isHidden(), "control: a short tab has the compact form"
    assert box.height() == box.minimum_for(box.width(), False)
