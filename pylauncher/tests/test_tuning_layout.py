"""The Tuning tab's shape across the window sizes a player drags it to (T190, T186).

Measured on the real widgets inside the window `main.build_window()` builds,
resized the way a drag resizes it (`_at`), never by calling the layout code
directly: the defect T190 fixes lived in the ORDER of resizes -- the window
always passes a narrow layout before it reaches a wide one, and the narrow
split's 20/80 heights were carried over as 20/80 widths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt

from tests.conftest import process_events
from tests.test_controller_view import (
    TRANSMOG_CONF,
    _at,
    _clipped,
    _controller_in_the_real_window,
    _deploy,
    _drawn_under_their_minimum,
    _Ps,
    _services,
    _with_the_core_confs,
    _wotlk_override,
)
from yulon import runner, server_time_zone
from yulon.catalog import composegen, time_zone
from yulon.catalog.catalog import load_catalog
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import TIME_ZONE_HOST, ControllerView
from yulon.ui.widgets import tuning_panel as tp
from yulon.ui.widgets.job import run_inline

WOTLK = load_catalog().get("wow-wotlk")
OSLO = "Europe/Oslo"
SMALL, MEDIUM, LARGE = (960, 640), (1280, 800), (1920, 1080)
DRAG = (SMALL, MEDIUM, LARGE, SMALL, MEDIUM)
"""The order a session really goes through: narrow first, then wide, then back.

Each wide size is reached from a narrow one, and 1280x800 is reached TWICE --
the second time after 1920 and 960 -- so a reset that happened only once, on
the first switch, answers differently the second time.
"""


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _tuning_window(
    ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_tab: str = "Tuning"
) -> tuple[ControllerView, Any, Any]:
    """Two modules' cards, the core confs, and this computer's time zone, in the real window.

    The core confs because every real WotLK install has them, and they are what
    T190 needed to show: with five file buttons the editor's half asks for more
    than with two, and the 219px column only appears with them on disk.
    """
    _with_the_core_confs(tmp_path)
    monkeypatch.setattr(time_zone, "host_zone", lambda: OSLO)
    installed = _wotlk_override(tmp_path)
    # Both servers name this computer's zone -- `test_time_zone_view`'s own
    # spelling of an applied zone -- so the box reads "Same as this computer".
    (tmp_path / composegen.OVERRIDE_FILE).write_text(
        installed + f'      TZ: "{OSLO}"\n  ac-authserver:\n    environment:\n      TZ: "{OSLO}"\n',
        encoding="utf-8",
    )
    _deploy(tmp_path, "env/dist/etc/modules/mod_npc_beastmaster.conf", "BeastMaster.Enable = 1\n")
    _deploy(tmp_path, TRANSMOG_CONF, "[worldserver]\nTransmogrification.Enable = 1\n")
    services = _services(ps, tmp_path, [])
    object.__setattr__(
        services,
        "installed_modules",
        lambda: {"module": frozenset({"mod-npc-beastmaster", "mod-transmog"})},
    )
    object.__setattr__(services, "time_zone", server_time_zone.time_zone_route(WOTLK, tmp_path))
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    window, tab = _controller_in_the_real_window(view, first_tab)
    return view, window, tab


def _file_side(panel: tp.TuningPanel) -> Any:
    """The editor's half of the split: the splitter child that is not the cards."""
    return panel.split.widget(1)


# -- the rules, as data -------------------------------------------------------


def test_the_cards_get_half_the_width_between_their_minimum_and_their_maximum() -> None:
    assert tp.split_sizes(1144, 440) == (572, 572)
    assert tp.split_sizes(1784, 440) == (tp.CARDS_MAX_WIDTH, 1784 - tp.CARDS_MAX_WIDTH)
    assert tp.split_sizes(820, 440) == (440, 380), "under half, the minimum wins"
    assert tp.split_sizes(2000, 900) == (900, 1100), "a header wider than the cap still fits"


def test_narrow_is_under_900_or_under_what_both_halves_need() -> None:
    assert tp.is_narrow(899, 440) is True
    assert tp.is_narrow(900, 440) is False
    assert tp.is_narrow(900, 600) is True, "600 + the editor's 360 do not fit in 900"
    assert tp.is_narrow(960, 600) is False


# -- the real window, dragged ---------------------------------------------------


def test_every_wide_window_gives_the_cards_a_real_column_after_a_narrow_one(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A1/B2: after 960x640 the cards were left a 219px column at 1280 and 351 at 1920.

    Mutation: drop the narrow->wide `setSizes` and the narrow split's ratio
    comes back as widths; do it only once and the second 1280 fails.
    """
    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    area = panel._area
    found: list[str] = []
    for size in DRAG:
        _at(window, size)
        if size == SMALL:
            continue
        where = f"{size[0]}x{size[1]}"
        if area.viewport().width() < tp.CARDS_MIN_WIDTH:
            found.append(f"{where}: the cards have {area.viewport().width()}px")
        if area.widget().width() > area.viewport().width():
            found.append(
                f"{where}: the cards are {area.widget().width()}px in a "
                f"{area.viewport().width()}px column, the rest is cut off"
            )
        if area.horizontalScrollBar().isVisible():
            found.append(f"{where}: the cards scroll sideways")
        half = min((panel.width() - panel.split.handleWidth()) // 2, tp.CARDS_MAX_WIDTH)
        if area.width() < half:
            found.append(f"{where}: the cards have {area.width()}px of a {panel.width()}px panel")
        if _file_side(panel).width() < tp.EDITOR_MIN_WIDTH:
            found.append(f"{where}: the editor has {_file_side(panel).width()}px")
        if not (area.isVisible() and _file_side(panel).isVisible()):
            found.append(f"{where}: both halves are not shown side by side")
    assert found == [], found


def test_a_narrow_window_gives_one_side_the_whole_height_and_a_press_swaps_them(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A1/B2 at 960x640: the cards had a 72px strip over the file editor.

    Now a "Settings | Edit file" switch shows one of them at a time, the
    choice is kept across a trip to a wide window, and it starts on Settings.
    """
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractButton

    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    area = panel._area
    _at(window, SMALL)

    switch = (panel.settings_button, panel.edit_file_button)
    assert panel.side_buttons.isVisible(), "no switch at 960x640"
    for button in switch:
        assert isinstance(button, QAbstractButton) and button.isEnabled() and button.isVisible()
    assert [b.text() for b in switch] == ["Settings", "Edit file"]
    assert area.isVisible() and not _file_side(panel).isVisible(), "it starts on the settings"
    assert (
        area.viewport().height() >= 0.8 * panel.height()
    ), f"the cards have {area.viewport().height()}px of the panel's {panel.height()}"

    QTest.mouseClick(panel.edit_file_button, Qt.MouseButton.LeftButton)
    process_events()
    assert not area.isVisible(), "the cards are still drawn beside the editor"
    assert panel.editor.isVisible() and panel.file_save_button.isVisible()
    assert all(b.isVisible() for b in panel.file_buttons()) and panel.file_buttons()
    assert (
        _file_side(panel).height() == panel.split.height()
    ), f"the file side has {_file_side(panel).height()}px of the split's {panel.split.height()}"
    lines = panel.editor.viewport().height() // panel.editor.fontMetrics().lineSpacing()
    assert lines >= 4, f"the editor shows {lines} lines"

    _at(window, MEDIUM)
    assert not panel.side_buttons.isVisible(), "the switch is still there with room for both"
    assert area.isVisible() and _file_side(panel).isVisible()
    _at(window, SMALL)
    assert panel.edit_file_button.isChecked() and not area.isVisible(), "the choice was lost"

    QTest.mouseClick(panel.settings_button, Qt.MouseButton.LeftButton)
    process_events()
    assert area.isVisible() and not _file_side(panel).isVisible()


def _edit_field_width(combo: Any) -> int:
    """The part of the combo its text is drawn in, from the style that draws it."""
    from PySide6.QtWidgets import QStyle, QStyleOptionComboBox

    option = QStyleOptionComboBox()
    combo.initStyleOption(option)
    rect = combo.style().subControlRect(
        QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, combo
    )
    return rect.width()


def test_the_time_zone_box_shows_this_computers_zone_whole_at_every_size(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T186: "Same as this computer (Europe/O" -- a 22-character box for 35 characters.

    And the "Now Europe/Oslo" after the press, which was given 0px at 1280x800
    once the box above it had eaten the column.
    """
    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    combo, note = view.time_zone_where, view.time_zone_note
    whole = TIME_ZONE_HOST.format(zone=OSLO)
    assert combo.currentText() == whole, "control: the file names this computer's zone"
    assert note.text() == f"Now {OSLO}", "control: the status says so"
    found: list[str] = []
    for size in DRAG:
        _at(window, size)
        where = f"{size[0]}x{size[1]}"
        need = combo.fontMetrics().horizontalAdvance(whole)
        if _edit_field_width(combo) < need:
            found.append(f"{where}: {_edit_field_width(combo)}px box for {need}px of text")
        said = note.fontMetrics().horizontalAdvance(note.text())
        if size != SMALL and note.width() < said:
            found.append(f"{where}: the status has {note.width()}px for {said}px")
    assert found == [], found


def test_nothing_on_the_tuning_tab_is_cut_at_any_step_of_the_drag(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No button with its label cut, nothing drawn under its minimum, either side at 960."""
    from PySide6.QtWidgets import QPushButton

    view, window, tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    found: list[str] = []

    def look(where: str) -> None:
        found.extend(f"{where}: {cut}" for cut in _drawn_under_their_minimum(tab))
        for button in tab.findChildren(QPushButton):
            if button.isVisible() and (why := _clipped(button)) is not None:
                found.append(f"{where}: {why}")

    for size in DRAG:
        _at(window, size)
        look(f"{size[0]}x{size[1]}")
        if size == SMALL:
            panel.edit_file_button.click()
            process_events()
            look(f"{size[0]}x{size[1]} editing")
            panel.settings_button.click()
            process_events()
    assert found == [], found


def _drag_the_handle(panel: tp.TuningPanel, dx: int) -> None:
    """Drag the split's handle `dx` pixels, with the mouse, as a player does."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    handle = panel.split.handle(1)
    start = handle.rect().center()
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(handle, start + QPoint(dx // 2, 0))
    QTest.mouseMove(handle, start + QPoint(dx, 0))
    QTest.mouseRelease(
        handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(dx, 0)
    )
    process_events()


def test_a_dragged_split_is_kept_while_wide_and_reset_after_a_narrow_window(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The handle is the player's until the window goes narrow; coming back is a fresh split.

    Two arrivals at 1280x800, each after a narrow window, with a drag in
    between: a reset made only on the first arrival leaves the drag in place
    the second time. And a resize that stays wide keeps the drag, so a reset on
    EVERY resize fails too.
    """
    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    area = panel._area
    _at(window, SMALL)
    _at(window, MEDIUM)
    fresh = area.width()
    _drag_the_handle(panel, 120)
    dragged = area.width()
    assert dragged >= fresh + 100, f"control: the drag moved the handle {dragged - fresh}px"

    _at(window, (1300, 800))
    assert area.width() >= dragged, f"a wide resize undid the drag: {area.width()}px"

    _at(window, SMALL)
    _at(window, MEDIUM)
    assert area.width() == fresh, f"the drag outlived a narrow window: {area.width()} != {fresh}"


def test_the_switch_paints_the_chosen_side_differently_from_the_other(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both buttons looked the same, so only the content said which side was showing.

    Pressed with the keyboard (Space, as the pad's A presses) and sampled with
    the focus elsewhere, so neither focus ring nor hover is what differs. The
    second press swaps the paint, so a style that only ever marked one button
    fails.
    """
    from PySide6.QtTest import QTest

    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    _at(window, SMALL)

    def press(button: Any) -> None:
        button.setFocus()
        QTest.keyClick(button, Qt.Key.Key_Space)
        button.clearFocus()
        process_events()

    def sample(button: Any) -> tuple[int, int, int]:
        image = button.grab().toImage()
        colour = image.pixelColor(4, image.height() // 2)
        return colour.red(), colour.green(), colour.blue()

    press(panel.edit_file_button)
    assert panel.edit_file_button.isChecked() and not panel.settings_button.isChecked()
    chosen, other = sample(panel.edit_file_button), sample(panel.settings_button)
    assert chosen != other, f"the chosen side paints {chosen}, the other {other}"

    press(panel.settings_button)
    assert sample(panel.settings_button) == chosen, "the paint did not follow the press"
    assert sample(panel.edit_file_button) == other


# -- Task 1, cold review round 1 ----------------------------------------------


def test_left_and_right_walk_the_switch_with_the_keys_a_player_presses(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Right from Settings went UP to "Reload from disk": Edit file was no stop at all.

    An exclusive `QButtonGroup` takes Tab focus off its unchecked buttons, and
    the pad stops only on widgets with Tab focus. Left is pressed from a focus
    put there by other means, so it is the row rule that answers and not the
    pad's memory of the Right it would undo.
    """
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from tests.test_controller_view import _pad_describe
    from yulon.ui.gamepad import install_gamepad_navigation

    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    _at(window, SMALL)
    _nav, keyboard, gamepad = install_gamepad_navigation(window)
    try:
        panel.settings_button.setFocus()
        process_events()
        QTest.keyClick(panel.settings_button, Qt.Key.Key_Right)
        process_events()
        landed = QApplication.focusWidget()
        assert landed is panel.edit_file_button, f"Right went to {_pad_describe(landed, window)}"

        view.tuning_reload_button.setFocus()
        panel.edit_file_button.setFocus()
        process_events()
        QTest.keyClick(panel.edit_file_button, Qt.Key.Key_Left)
        process_events()
        landed = QApplication.focusWidget()
        assert landed is panel.settings_button, f"Left went to {_pad_describe(landed, window)}"
        assert panel.settings_button.isChecked(), "control: moving the focus chose nothing"
    finally:
        keyboard.stop()
        gamepad.stop()


def test_a_tab_first_shown_in_a_wide_window_starts_on_the_half_and_half_split(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first layout sets the split up even when it is not a switch from narrow.

    The window is already 1280x800 when the Tuning tab is first opened, so the
    panel's first resize is a wide one with no narrow layout before it.
    """
    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch, first_tab="Modules")
    panel = view.tuning_panel
    assert not panel.isVisible(), "control: the Tuning tab is shown before this test opens it"
    _at(window, MEDIUM)
    view._tabs.setCurrentIndex(view._tabs.indexOf(view.tuning_panel.parentWidget()))
    process_events()
    assert panel.isVisible()
    half = (panel.width() - panel.split.handleWidth()) // 2
    assert panel._area.width() >= half, f"the cards have {panel._area.width()}px, half is {half}"


def test_a_header_wider_than_the_column_sets_the_columns_floor(qapp: object) -> None:
    """The header is drawn whole: the column is never narrower than it, and the
    panel takes turns sooner rather than cut it -- at a width where, without the
    header, the halves would still sit side by side."""
    from PySide6.QtWidgets import QLabel

    plain = tp.TuningPanel()
    headed = tp.TuningPanel()
    header = QLabel("H" * 80)
    headed.set_header(header)
    assert header.sizeHint().width() > tp.CARDS_MIN_WIDTH, "control: the header is the wider one"
    # Room for the header and the editor, but not for the column's own chrome
    # round the header as well: only a floor that counts the header binds here.
    width = max(tp.NARROW_WIDTH, header.sizeHint().width() + tp.EDITOR_MIN_WIDTH)
    for panel in (plain, headed):
        panel.show()
        panel.resize(width, 600)
    try:
        process_events()
        assert not plain.side_buttons.isVisible(), f"control: {width}px holds both halves"
        assert headed.side_buttons.isVisible(), "the header was cut instead of taking turns"
        headed.resize(width + 400, 600)
        process_events()
        assert not headed.side_buttons.isVisible()
        assert (
            header.width() >= header.sizeHint().width()
        ), f"the header is {header.width()}px of the {header.sizeHint().width()} it needs"
        area = headed._area
        assert area.widget().width() <= area.viewport().width()
    finally:
        plain.close()
        headed.close()


def test_going_narrow_with_unsaved_typing_in_the_editor_shows_the_editor(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shrinking the window must not hide what the player is typing behind Settings.

    The same drag with nothing typed stays on Settings, and after the file is
    opened again the typing is gone, so the next narrow window follows the
    switch, not the typing.
    """
    from PySide6.QtTest import QTest

    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    _at(window, MEDIUM)
    _at(window, SMALL)
    assert panel.settings_button.isChecked(), "control: nothing typed, the settings show"
    _at(window, MEDIUM)

    panel.editor.setFocus()
    QTest.keyClicks(panel.editor, "BeastMaster.Enable = 0")
    _at(window, SMALL)
    assert panel.edit_file_button.isChecked(), "the typing went behind the Settings side"
    assert panel.editor.isVisible() and not panel._area.isVisible()

    QTest.keyClick(panel.settings_button, Qt.Key.Key_Space)
    process_events()
    _at(window, MEDIUM)
    panel.file_buttons()[0].click()
    process_events()
    _at(window, SMALL)
    assert panel.settings_button.isChecked(), "a file opened again still counted as typed in"


# -- Task 3: one Reload that re-reads, the file named, the chosen file shown ---


def test_at_960_every_file_button_is_as_wide_as_its_label_needs(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five file buttons in one row that could not wrap: "od_npc_beastmaster.co"."""
    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    _at(window, SMALL)
    panel.edit_file_button.click()
    process_events()
    buttons = panel.file_buttons()
    assert len(buttons) == 5, "control: the five files of this install are listed"
    short = [
        f"{b.text()}: {b.width()} < {b.sizeHint().width()}"
        for b in buttons
        if b.width() < b.sizeHint().width()
    ]
    assert short == [], short
    assert all(b.isVisible() for b in buttons)


def _uncheck_and_save(view: ControllerView, key: str) -> None:
    """Switch `key` off on its card and press the card's Save, with the mouse."""
    from PySide6.QtTest import QTest

    card = next(c for c in view.tuning_panel.cards() if key in c.editors)
    switch = card.editors[key].control
    assert switch is not None and switch.isChecked(), "control: the switch starts on"
    QTest.mouseClick(switch, Qt.MouseButton.LeftButton)
    assert card.save_button is not None
    QTest.mouseClick(card.save_button, Qt.MouseButton.LeftButton)
    process_events()


def test_a_card_save_reaches_the_file_open_in_the_editor(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A28: after a card's save the editor still said `= 1`, and Save file then wrote it back.

    Twice, the second time after a Save file of the editor's own: a saved
    editor counts as clean again, so the next card save is read in too.
    """
    from PySide6.QtTest import QTest

    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    conf = tmp_path / "env/dist/etc/modules/mod_npc_beastmaster.conf"
    _at(window, MEDIUM)
    assert panel.current_file().endswith("mod_npc_beastmaster.conf")

    _uncheck_and_save(view, "BeastMaster.Enable")
    assert "BeastMaster.Enable = 0" in conf.read_text(encoding="utf-8"), "control: it saved"
    assert "BeastMaster.Enable = 0" in panel.editor.toPlainText(), panel.editor.toPlainText()

    panel.editor.moveCursor(panel.editor.textCursor().MoveOperation.End)
    QTest.keyClick(panel.editor, Qt.Key.Key_Return)
    QTest.keyClicks(panel.editor, "BeastMaster.HunterOnly = 1")
    QTest.mouseClick(panel.file_save_button, Qt.MouseButton.LeftButton)
    process_events()
    assert "BeastMaster.HunterOnly = 1" in conf.read_text(encoding="utf-8"), "control: saved"
    assert panel.file_revert_button.isEnabled(), "the save's backup was dropped by the re-read"

    _uncheck_and_save(view, "BeastMaster.HunterOnly")
    assert "BeastMaster.HunterOnly = 0" in panel.editor.toPlainText(), panel.editor.toPlainText()


def test_the_open_files_button_paints_as_chosen(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Which file the editor shows was a checked state the theme never drew."""
    from PySide6.QtTest import QTest

    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    _at(window, LARGE)
    first, second = panel.file_buttons()[:2]

    def sample(button: Any) -> tuple[int, int, int]:
        image = button.grab().toImage()
        colour = image.pixelColor(4, image.height() // 2)
        return colour.red(), colour.green(), colour.blue()

    assert first.isChecked() and not second.isChecked()
    chosen, other = sample(first), sample(second)
    assert chosen != other, f"the open file paints {chosen}, the other {other}"
    second.setFocus()
    QTest.keyClick(second, Qt.Key.Key_Space)
    second.clearFocus()
    process_events()
    assert second.isChecked() and not first.isChecked()
    assert (sample(second), sample(first)) == (chosen, other), "the paint did not follow"
