"""`ScrollPage` and `page_room` (T191): a sub-tab that scrolls instead of squeezing.

Real widgets, real resizes, and what is asserted is what they did: the body's
height against its own minimum, the scroll bar's range, the viewport's height.
"""

from __future__ import annotations

from typing import Any

from tests.conftest import process_events


def _body(minimum_height: int) -> Any:
    """A sub-tab body whose layout cannot be drawn shorter than `minimum_height`."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    body = QWidget()
    box = QVBoxLayout(body)
    box.setContentsMargins(0, 0, 0, 0)
    tall = QWidget(body)
    tall.setMinimumHeight(minimum_height)
    box.addWidget(tall)
    return body


def _shown(body: Any, width: int, height: int) -> Any:
    from yulon.ui.widgets.page import ScrollPage

    page = ScrollPage(body)
    page.resize(width, height)
    page.show()
    process_events()
    return page


def test_a_body_taller_than_the_page_scrolls_at_its_own_minimum(qapp: object) -> None:
    """900 px of minimum in a 400 px page: drawn whole, and reached by scrolling."""
    body = _body(900)
    page = _shown(body, 300, 400)

    assert body.height() >= body.minimumSizeHint().height() >= 900, (
        f"the body is drawn {body.height()}px against the {body.minimumSizeHint().height()} "
        "it needs: it was squeezed into the page instead of scrolled"
    )
    assert page.verticalScrollBar().maximum() > 0, "nothing to scroll: the rest is out of reach"
    assert page.horizontalScrollBar().maximum() == 0, "a sideways scroll bar on a page that fits"


def test_a_body_that_fits_fills_the_page_and_does_not_scroll(qapp: object) -> None:
    """The other side: a short body takes the whole viewport, so a stretch still stretches."""
    body = _body(100)
    page = _shown(body, 300, 400)

    assert body.height() == page.viewport().height() == 400
    assert page.verticalScrollBar().maximum() == 0


def test_a_body_scrolls_only_when_its_minimum_does_not_fit_not_its_preferred_size(
    qapp: object,
) -> None:
    """A list the tab may shrink is shrunk, not scrolled past (T153's rung keeps working).

    The body asks for far more than the page has (a word-wrapped label makes its
    layout height-for-width, and a list's preferred height is 192 rows of
    pixels) but NEEDS only its minimum, which fits. `QScrollArea`'s own
    resizable mode grows a height-for-width body to its PREFERRED height, and
    this page would then scroll a list past the bottom of a tab that has the
    room to show all of it.
    """
    from PySide6.QtWidgets import QLabel, QListWidget, QVBoxLayout, QWidget

    body = QWidget()
    box = QVBoxLayout(body)
    label = QLabel("A sentence long enough to wrap onto a second line in a narrow page.", body)
    label.setWordWrap(True)
    box.addWidget(label)
    listing = QListWidget(body)
    listing.setMinimumHeight(40)
    box.addWidget(listing)
    page = _shown(body, 300, 200)

    assert (
        box.totalHeightForWidth(body.width()) > page.viewport().height()
    ), "the fixture's preferred height fits the page, so it cannot tell the two apart"
    assert page.verticalScrollBar().maximum() == 0, (
        f"the page scrolls {page.verticalScrollBar().maximum()}px for a body whose minimum "
        f"({box.totalMinimumHeightForWidth(body.width())}px) fits its {page.viewport().height()}"
    )
    assert listing.height() >= 40


def test_a_page_is_not_a_stop_of_its_own(qapp: object) -> None:
    """The pad goes from the sub-tab bar to the first control, not to the area around it."""
    from PySide6.QtCore import Qt

    page = _shown(_body(100), 300, 400)

    assert page.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_page_room_is_the_viewport_and_not_the_body_it_grew(qapp: object) -> None:
    """What height-aware code divides is the room on screen, not the scrolled length."""
    from PySide6.QtWidgets import QWidget

    from yulon.ui.widgets.page import page_room

    body = _body(900)
    page = _shown(body, 300, 400)

    assert body.height() > page.viewport().height(), "the body fits, so the two cannot differ"
    assert page_room(body) == page.viewport().height()

    loose = QWidget()
    loose.resize(120, 333)
    assert page_room(loose) == 333, "a widget outside a page has its own height as its room"


def test_more_room_is_told_to_a_body_that_did_not_change_size(qapp: object) -> None:
    """A taller page under a body still taller than it: the body hears the room moved.

    The body's own size does not change -- it is held at its minimum -- so Qt
    sends it no resize, and code that re-decides on a resize of the tab (the
    Modules tab's fold, the log's quarter-share cap) would keep deciding from
    the room the page USED to have.
    """
    from PySide6.QtCore import QEvent, QObject

    from yulon.ui.widgets.page import page_room

    body = _body(900)
    page = _shown(body, 300, 400)
    heard: list[int] = []

    class _Ear(QObject):
        def eventFilter(self, watched: QObject, event: QEvent) -> bool:
            if event.type() == QEvent.Type.Resize:
                heard.append(page_room(body))
            return False

    ear = _Ear()
    body.installEventFilter(ear)
    before = body.height()
    page.resize(300, 500)
    process_events()

    assert body.height() == before, "the body moved, so this would pass without the page's help"
    assert heard and heard[-1] == page.viewport().height() == 500


def test_a_page_prefers_the_size_its_body_prefers(qapp: object) -> None:
    """The window is first laid out at its contents' preferred size, as it was before pages.

    A scroll area's own hint is the body's CURRENT size (not resizable) or its
    hint cut to 36x24 lines (resizable). Either changes the size the window is
    first laid out at, and the Tuning tab's two columns keep the split they
    were first given: measured, a page around the Modules tab took the cards
    column at 1280x800 from 536px to 136.
    """
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QWidget

    from yulon.ui.widgets.page import ScrollPage

    class _Wide(QWidget):
        def sizeHint(self) -> QSize:  # noqa: N802  (Qt's own name)
            return QSize(1500, 1200)

    body = _Wide()
    page = ScrollPage(body)

    assert page.sizeHint() == QSize(1500, 1200)


def _row_of_a_real_item(window: Any) -> int:
    """How tall the theme draws one row of a list, asked of a list that has one."""
    from PySide6.QtWidgets import QListWidget

    probe = QListWidget(window)
    probe.addItem("Guglu — level 78 — online — ADMIN")
    probe.show()
    process_events()
    height = probe.sizeHintForRow(0)
    probe.deleteLater()
    return int(height)


def test_an_empty_rows_list_asks_for_three_whole_rows_of_the_themes_height(qapp: object) -> None:
    """Empty, it still claims three rows -- the height the theme gives a real row, three times.

    Measured against a list WITH an item, so the floor is the theme's row and
    not a number this test or the widget made up.
    """
    from PySide6.QtWidgets import QListWidget, QMainWindow

    from yulon.ui.theme import apply_dadcraft_theme
    from yulon.ui.widgets.page import RowsList

    window = QMainWindow()
    apply_dadcraft_theme(window, width=960)
    rows = RowsList(rows=3, parent=window)
    plain = QListWidget(window)
    window.show()
    process_events()
    row = _row_of_a_real_item(window)

    assert row > 0
    assert (
        rows.minimumSizeHint().height() == 3 * row + 2 * rows.frameWidth()
    ), f"{rows.minimumSizeHint().height()}px for three rows of {row}"
    assert plain.minimumSizeHint().height() < 3 * row, "a plain list already asks for this"


def test_a_rows_list_follows_the_style_when_the_window_restyles(qapp: object) -> None:
    """Restyled at another width, and restyled with taller rows: the floor follows each.

    The theme's row is floored at its touch target, so 960 and 1920 draw the
    same row today (measured); the second restyle is the one that moves it,
    and a floor computed once at construction would stay where it was.
    """
    from PySide6.QtWidgets import QMainWindow

    from yulon.ui.theme import apply_dadcraft_theme
    from yulon.ui.widgets.page import RowsList

    window = QMainWindow()
    apply_dadcraft_theme(window, width=960)
    rows = RowsList(rows=3, parent=window)
    window.show()
    process_events()
    first = rows.minimumSizeHint().height()
    apply_dadcraft_theme(window, width=1920)
    process_events()
    assert (
        rows.minimumSizeHint().height() == 3 * _row_of_a_real_item(window) + 2 * rows.frameWidth()
    )
    window.setStyleSheet(window.styleSheet() + "\nQListWidget::item { min-height: 80px; }")
    process_events()
    taller = _row_of_a_real_item(window)

    assert taller * 3 + 2 * rows.frameWidth() > first, "the restyle did not make a row taller"
    assert rows.minimumSizeHint().height() == 3 * taller + 2 * rows.frameWidth()
