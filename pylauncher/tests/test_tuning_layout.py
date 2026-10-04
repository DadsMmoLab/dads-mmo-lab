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
    # The file button asks about the typing first; the player throws it away.
    panel.choose = lambda _title, _question, _choices: tp.CHOICE_DISCARD
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


def _overlaps_on_the_file_side(panel: tp.TuningPanel) -> list[str]:
    """Every pair of shown widgets on the file side drawn over one another.

    The file side's own rows -- the file buttons, the file's name, its notes,
    the editor, the backup line, the guard's verdict and the two presses --
    each in the file side's coordinates, so a row a squeezed layout pushed onto
    the next one is caught whichever widget it lands on.
    """
    from PySide6.QtCore import QRect

    side = _file_side(panel)
    rows: list[tuple[str, QRect]] = []
    for name in (
        "files",
        "file_title",
        "file_note",
        "shadow_warning",
        "editor",
        "backup_label",
        "lint_label",
        "file_revert_button",
        "file_save_button",
    ):
        widget = getattr(panel, name)
        if widget.isVisible():
            rows.append((name, QRect(widget.mapTo(side, widget.rect().topLeft()), widget.size())))
    return [
        f"{a} {ra.getRect()} overlaps {b} {rb.getRect()}"
        for i, (a, ra) in enumerate(rows)
        for b, rb in rows[i + 1 :]
        if ra.intersects(rb)
    ]


def _file_side_faults(panel: tp.TuningPanel) -> list[str]:
    """What is wrong with the file side as drawn: overlaps, lost buttons, a squeezed editor."""
    found = _overlaps_on_the_file_side(panel)
    bar = panel.files
    for button in panel.file_buttons():
        inside = bar.rect().contains(button.geometry())
        if not (button.isVisible() and inside and button.height() > 0):
            found.append(
                f"file button {button.text()!r} at {button.geometry().getRect()} "
                f"outside its {bar.width()}x{bar.height()} bar"
            )
    if bar.height() < bar.flow().heightForWidth(bar.width()):
        found.append(f"the file buttons have {bar.height()}px of the {bar.minimumHeight()} needed")
    lines = panel.editor.viewport().height() // panel.editor.fontMetrics().lineSpacing()
    if lines < 4:
        found.append(f"the editor shows {lines} lines")
    for name in ("file_title", "backup_label"):
        label = getattr(panel, name)
        if label.isVisible() and label.height() < label.minimumSizeHint().height():
            found.append(f"{name}: {label.height()} < {label.minimumSizeHint().height()}")
    return found


def test_the_banner_and_a_named_backup_leave_the_file_side_drawn_without_overlap(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Linux live test at 960x640: the restart banner over the tab and a backup named.

    The file side asked for more height than the tab had left, and Qt cut every
    row of it at once: the backup line drawn across the editor, the second row of
    file buttons gone, Revert file and Save file over the editor's bottom edge --
    with "Last action" open and with it folded. At every size of the drag.
    """
    from PySide6.QtTest import QTest

    view, window, tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    _at(window, MEDIUM)
    assert panel.current_file() == BEAST_CONF, "control: the beastmaster conf is open"
    panel.editor.moveCursor(panel.editor.textCursor().MoveOperation.End)
    QTest.keyClicks(panel.editor, "BeastMaster.HunterOnly = 1")
    QTest.mouseClick(panel.file_save_button, Qt.MouseButton.LeftButton)
    process_events()
    assert panel.backup_label.isVisible(), "control: the save named a backup"
    assert view.tuning_banner.isVisible(), "control: the banner says a restart is owed"
    assert not view.tuning_report_strip.collapsed, "control: the save's report is open"

    found: list[str] = []
    for size in DRAG:
        _at(window, size)
        if size == SMALL:
            panel.edit_file_button.click()
            process_events()
        assert panel.editor.isVisible(), f"control: the editor is shown at {size}"
        assert len(panel.file_buttons()) == 5, "control: five files"
        where = f"{size[0]}x{size[1]}"
        found.extend(f"{where}: {fault}" for fault in _file_side_faults(panel))
        found.extend(f"{where}: {cut}" for cut in _drawn_under_their_minimum(tab))
        if size == SMALL:
            # And again with the report folded away, wframe-79's half.
            QTest.mouseClick(view.tuning_report_strip, Qt.MouseButton.LeftButton)
            process_events()
            assert view.tuning_report_strip.collapsed, "control: the strip folded"
            found.extend(f"{where} folded: {fault}" for fault in _file_side_faults(panel))
            found.extend(f"{where} folded: {cut}" for cut in _drawn_under_their_minimum(tab))
            QTest.mouseClick(view.tuning_report_strip, Qt.MouseButton.LeftButton)
            process_events()
    assert found == [], found


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


def test_the_restart_sentence_is_on_screen_once_and_never_missing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Side by side, the note over the cards and the editor's note said the same sentence.

    The editor's note gives way only while the note over the cards is on
    screen: at 960x640 on the Edit file side the cards are hidden, and the
    sentence must still be there for the file being edited.
    """
    from PySide6.QtWidgets import QLabel

    from yulon import tuning

    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    restart = tuning.apply_sentence("restart")
    assert panel.current_file().endswith("mod_npc_beastmaster.conf"), "control: a restart file"

    def shown() -> list[str]:
        return [
            w.objectName() or w.text()[:20]
            for w in panel.findChildren(QLabel)
            if w.isVisible() and restart in w.text()
        ]

    for size in (MEDIUM, LARGE):
        _at(window, size)
        assert len(shown()) == 1, f"{size}: {shown()}"
    _at(window, SMALL)
    assert len(shown()) == 1, f"960 settings: {shown()}"
    panel.edit_file_button.click()
    process_events()
    assert panel.file_note.isVisible() and restart in panel.file_note.text(), shown()
    assert len(shown()) == 1


# -- the final round: no press loses or quietly overrides what was typed ------


class _Answers:
    """The panel's question, answered as the test says, each one kept."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.asked: list[tuple[str, ...]] = []

    def __call__(self, title: str, question: str, choices: tuple[str, ...]) -> str:
        self.asked.append(tuple(choices))
        return self.answer


BEAST_CONF = "env/dist/etc/modules/mod_npc_beastmaster.conf"


def _card_with(view: ControllerView, key: str) -> tp.CardWidget:
    return next(c for c in view.tuning_panel.cards() if key in c.editors)


def test_saving_one_card_keeps_what_was_typed_on_the_others_and_so_does_reload(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every card was drawn again after one card's Save, and the others' typing went with it."""
    from PySide6.QtTest import QTest

    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    _at(window, MEDIUM)
    transmog_key = "Transmogrification.Enable"
    switch = _card_with(view, transmog_key).editors[transmog_key].control
    assert switch is not None and switch.isChecked(), "control: transmog starts on"
    QTest.mouseClick(switch, Qt.MouseButton.LeftButton)
    assert _card_with(view, transmog_key).edits() == {transmog_key: "0"}, "control: typed"

    _uncheck_and_save(view, "BeastMaster.Enable")
    assert "BeastMaster.Enable = 0" in (tmp_path / BEAST_CONF).read_text(encoding="utf-8")
    beast = _card_with(view, "BeastMaster.Enable")
    assert beast.edits() == {}, "the saved card still counts its saved value as typed"
    saved_switch = beast.editors["BeastMaster.Enable"].control
    assert saved_switch is not None and not saved_switch.isChecked()
    transmog = _card_with(view, transmog_key)
    assert transmog.edits() == {transmog_key: "0"}, "another card's save dropped this typing"
    assert transmog.editors[transmog_key].changed
    assert "Transmogrification.Enable = 1" in (tmp_path / TRANSMOG_CONF).read_text(
        encoding="utf-8"
    ), "control: the typing was never written"

    QTest.mouseClick(view.tuning_reload_button, Qt.MouseButton.LeftButton)
    process_events()
    assert _card_with(view, transmog_key).edits() == {transmog_key: "0"}, "Reload dropped it"
    assert view.tuning_revert_all_button.isEnabled(), "the kept typing no longer counts"


def test_revert_file_puts_back_the_backup_its_label_names(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Save file, then a card's Save on the same file: the label named the first backup and
    Revert file restored the second."""
    from PySide6.QtTest import QTest

    from yulon import tuning

    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    conf = tmp_path / BEAST_CONF
    _at(window, MEDIUM)
    assert panel.current_file() == BEAST_CONF, "control: the beastmaster conf is open"
    panel.editor.moveCursor(panel.editor.textCursor().MoveOperation.End)
    QTest.keyClicks(panel.editor, "BeastMaster.HunterOnly = 1")
    QTest.mouseClick(panel.file_save_button, Qt.MouseButton.LeftButton)
    process_events()
    label = panel.backup_label.text()
    named = label.rsplit(": ", 1)[-1]
    assert named.endswith(".bak"), label

    _uncheck_and_save(view, "BeastMaster.Enable")
    assert len(tuning.backups_of(conf)) == 2, "control: the card's save took a newer backup"
    assert panel.backup_label.text() == label
    wanted = (conf.parent / named).read_text(encoding="utf-8")
    assert wanted != conf.read_text(encoding="utf-8"), "control: Revert has something to do"

    QTest.mouseClick(panel.file_revert_button, Qt.MouseButton.LeftButton)
    process_events()
    assert conf.read_text(encoding="utf-8") == wanted, "Revert restored another backup"
    assert named in view.tuning_report.toPlainText()


def test_a_card_save_under_typing_in_the_editor_says_so_and_save_file_asks(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The editor kept its typing over a card's Save and said nothing; Save file then put
    the card's value back without a word (cold review)."""
    from PySide6.QtTest import QTest

    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    conf = tmp_path / BEAST_CONF
    _at(window, MEDIUM)
    assert panel.current_file() == BEAST_CONF, "control: the beastmaster conf is open"
    panel.editor.moveCursor(panel.editor.textCursor().MoveOperation.End)
    QTest.keyClicks(panel.editor, "# mine")

    _uncheck_and_save(view, "BeastMaster.Enable")
    on_disk = conf.read_text(encoding="utf-8")
    assert "BeastMaster.Enable = 0" in on_disk, "control: the card saved"
    assert "# mine" in panel.editor.toPlainText(), "the typing was thrown away"
    assert panel.file_note.isVisible() and tp.EDITOR_STALE in panel.file_note.text()

    ask = _Answers(tp.CHOICE_CANCEL)
    panel.choose = ask
    QTest.mouseClick(panel.file_save_button, Qt.MouseButton.LeftButton)
    process_events()
    assert ask.asked == [(tp.CHOICE_OVERWRITE, tp.CHOICE_RELOAD, tp.CHOICE_CANCEL)]
    assert conf.read_text(encoding="utf-8") == on_disk, "Cancel wrote the file"


# -- fix round 2: a failed or missing re-read, a gone backup, a put-off reload --


def _type_into_the_editor(panel: tp.TuningPanel, text: str) -> None:
    from PySide6.QtTest import QTest

    panel.editor.moveCursor(panel.editor.textCursor().MoveOperation.End)
    QTest.keyClicks(panel.editor, text)


def test_a_re_read_that_cannot_open_the_file_keeps_the_typing_and_says_why(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 9p hiccup on Reload blanked the editor and made it read-only, typing and all."""
    import builtins

    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    _at(window, MEDIUM)
    assert panel.current_file() == BEAST_CONF, "control: the beastmaster conf is open"
    _type_into_the_editor(panel, "# mine")

    def hiccup(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file).endswith("mod_npc_beastmaster.conf"):
            raise OSError(5, "Input/output error")
        return builtins.open(file, *args, **kwargs)

    monkeypatch.setattr(controller_view_module, "open", hiccup, raising=False)
    _press(view.tuning_reload_button)
    assert panel.editor.toPlainText().endswith("# mine"), "the failed read blanked the typing"
    assert not panel.editor.isReadOnly()
    note = panel.file_note.text()
    assert panel.file_note.isVisible() and "Input/output error" in note, note
    assert tp.EDITOR_UNREAD in note


def test_a_re_read_of_a_file_that_is_no_longer_utf8_keeps_the_typing_and_says_why(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    _at(window, MEDIUM)
    _type_into_the_editor(panel, "# mine")
    (tmp_path / BEAST_CONF).write_bytes(b"BeastMaster.Enable = \xff\n")
    _press(view.tuning_reload_button)
    assert panel.editor.toPlainText().endswith("# mine"), "the failed read blanked the typing"
    assert not panel.editor.isReadOnly()
    note = panel.file_note.text()
    assert "UTF-8" in note and tp.EDITOR_UNREAD in note, note


def _press(button: Any) -> None:
    from PySide6.QtTest import QTest

    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    process_events()


def test_typing_in_a_file_the_reload_no_longer_lists_stays_until_the_player_leaves_it(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The open file gone from the list: the editor jumped to the first file, typing lost."""
    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    _at(window, MEDIUM)
    _type_into_the_editor(panel, "# mine")
    (tmp_path / BEAST_CONF).unlink()
    _press(view.tuning_reload_button)
    assert BEAST_CONF not in [b.toolTip() for b in panel.file_buttons()], "control: not listed"
    assert panel.current_file() == BEAST_CONF
    assert panel.editor.toPlainText().endswith("# mine"), "the reload switched away"
    assert panel.file_note.isVisible() and tp.EDITOR_GONE in panel.file_note.text()
    assert not panel.file_save_button.isEnabled(), "Save file would write a file not offered"
    assert not any(b.isChecked() for b in panel.file_buttons())

    ask = _Answers(tp.CHOICE_CANCEL)
    panel.choose = ask
    _press(panel.file_buttons()[0])
    assert ask.asked == [(tp.CHOICE_DISCARD, tp.CHOICE_CANCEL)], "no Save for a file not offered"
    assert panel.current_file() == BEAST_CONF and panel.editor.toPlainText().endswith("# mine")
    assert not panel.file_buttons()[0].isChecked()


def test_revert_file_says_so_when_the_backup_it_names_is_gone_and_writes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import tuning

    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    panel = view.tuning_panel
    conf = tmp_path / BEAST_CONF
    _at(window, MEDIUM)
    _type_into_the_editor(panel, "BeastMaster.HunterOnly = 1")
    _press(panel.file_save_button)
    named = panel.backup_label.text().rsplit(": ", 1)[-1]
    _uncheck_and_save(view, "BeastMaster.Enable")
    (conf.parent / named).unlink()
    assert len(tuning.backups_of(conf)) == 1, "control: another backup is still there"
    before = conf.read_text(encoding="utf-8")

    _press(panel.file_revert_button)
    assert conf.read_text(encoding="utf-8") == before, "Revert wrote a backup it did not name"
    said = view.tuning_report.toPlainText()
    assert said == controller_view_module.TUNING_NAMED_BACKUP_GONE.format(
        backup=named, file=BEAST_CONF
    ), said


def test_a_cards_revert_whose_reload_waits_for_the_distro_still_drops_that_cards_typing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WSL: the reload ran after the press was over and carried the reverted card's typing."""
    from PySide6.QtTest import QTest

    view, window, _tab = _tuning_window(ps, tmp_path, monkeypatch)
    _at(window, MEDIUM)
    level = _card_with(view, "BeastMaster.MinLevel").editors["BeastMaster.MinLevel"].control
    assert level is not None
    QTest.keyClick(level, Qt.Key.Key_Up)
    transmog_key = "Transmogrification.Enable"
    switch = _card_with(view, transmog_key).editors[transmog_key].control
    assert switch is not None
    QTest.mouseClick(switch, Qt.MouseButton.LeftButton)
    assert _card_with(view, "BeastMaster.MinLevel").edits(), "control: typed"

    view._distro = "stopped"
    revert = _card_with(view, "BeastMaster.MinLevel").revert_button
    assert revert is not None
    _press(revert)
    assert "tuning" in view._waiting_on_distro, "control: the reload was put off"
    view._distro_answered("running")
    process_events()
    assert _card_with(view, "BeastMaster.MinLevel").edits() == {}, "Revert kept the typing"
    assert _card_with(view, transmog_key).edits() == {transmog_key: "0"}
