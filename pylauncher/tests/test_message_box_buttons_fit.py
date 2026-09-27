"""T157: every question this app builds as a `QMessageBox` shows its whole button labels.

The "Update the server to latest…" dialog drew "Update without a backu" and
"Back up first, then updat" (Fedora, xcb, 1920×1080, 2026-09-27). The theme's
`QPushButton { min-width: 64px }` is not only a floor under the size hint:
Qt's style-sheet style turns it into an EXPLICIT `setMinimumWidth()` (98px with
the padding and border), and an explicit minimum outranks the button's
`minimumSizeHint()` in every layout. `QMessageBox` then fixes its own width at
its layout's MINIMUM, so each relabelled button was squeezed to that floor.

Each test builds the real dialog through the real function, styled the way the
app styles it (the theme on the dialog's parent, at a window width), shows it
offscreen in place of the modal `exec()`, and measures what is drawn: the
label's advance plus the icon, when the platform gives the button one, against
the style's own contents rectangle for that button.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QWidget,
)

from tests.conftest import process_events
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.ui import catalog_view
from yulon.ui.controller_view import (
    REBUILD_BOTS_TEXT,
    REBUILD_BOTS_TITLE,
    ask_backup_choice,
    ask_to_set_client_dir,
    ask_update_choice,
)
from yulon.ui.theme import apply_dadcraft_theme

UPDATE_TEXT = (
    "Update WoW WotLK in /home/player/wow/server to the newest "
    "mod-playerbots/azerothcore-wotlk code?\n\n"
    "This builds code nobody has tested with this app. It takes as long as your first "
    "build (about 15 minutes on an Apple M4 Pro, 35-72 minutes on the Linux boxes this "
    "project is usually built on, and 68 minutes on a Windows machine that gave Docker "
    "11.7 GB and two compiler jobs) and it can fail — a module may no longer compile, or "
    "the new server may refuse your database. If the build fails, the build you have now "
    "is put back. Anything the new server writes into your database on first start is not "
    "put back — that is what the backup is for."
)
"""The body the defect was photographed with (the path generalised)."""

ARAC = wotlk_modules.load_module(
    wotlk_modules.BUNDLED_MANIFESTS_DIR / "wow-wotlk" / "modules" / "mod-arac.json"
)

Seen = dict[str, tuple[int, int]]
"""Label -> (pixels the label needs, pixels the button's contents rectangle has)."""


def _needed_and_available(button: QPushButton) -> tuple[int, int]:
    """What the label needs and what the button gives it, both in the button's own terms.

    The icon counts when there is one: xcb under a desktop theme gives the
    standard buttons icons (the defect's screenshot has three), offscreen does
    not, and a check that ignored it would pass here and clip there.
    """
    option = QStyleOptionButton()
    button.initStyleOption(option)
    contents = button.style().subElementRect(
        QStyle.SubElement.SE_PushButtonContents, option, button
    )
    needed = button.fontMetrics().horizontalAdvance(button.text())
    if not button.icon().isNull():
        needed += button.iconSize().width() + 4
    return needed, contents.width()


def _measure(box: QMessageBox) -> Seen:
    return {
        button.text(): _needed_and_available(button)
        for button in box.findChildren(QPushButton)
        if button.isVisible()
    }


@pytest.fixture
def themed_parent(qapp: QApplication) -> Iterator[Callable[[int], QWidget]]:
    """A parent styled the way `main.py` styles the window: the theme, at a width."""
    made: list[QWidget] = []

    def make(width: int) -> QWidget:
        parent = QWidget()
        apply_dadcraft_theme(parent, width=width)
        made.append(parent)
        return parent

    yield make
    for parent in made:
        parent.deleteLater()


def _give_icons(box: QMessageBox) -> None:
    """What xcb under a desktop theme does and offscreen does not: an icon on each button.

    `QDialogButtonBox` asks the platform theme whether standard buttons carry
    icons. The defect's screenshot has one on all three; the offscreen platform
    says no. Set before the box is shown, as the button box sets them when it
    makes the buttons.
    """
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor("white"))
    for button in box.findChildren(QPushButton):
        button.setIcon(QIcon(pixmap))


def _show_instead_of_exec(
    monkeypatch: pytest.MonkeyPatch,
    during: Callable[[QMessageBox], None] | None = None,
    *,
    icons: bool = False,
) -> list[Seen]:
    """Replace the modal with a shown, laid-out, measured box that answers Cancel.

    `during`, if given, runs while the box is up, and the box is measured again
    after it: the app re-applies its theme to the window on every resize, and
    that re-styles an open dialog too.
    """
    seen: list[Seen] = []

    def fake_exec(box: QMessageBox) -> int:
        if icons:
            _give_icons(box)
        box.show()
        process_events()
        seen.append(_measure(box))
        if during is not None:
            during(box)
            process_events()
            seen.append(_measure(box))
        box.hide()
        return int(QMessageBox.StandardButton.Cancel)

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return seen


DIALOGS: dict[str, Callable[[QWidget, Path], object]] = {
    "update-to-latest": lambda parent, _tmp: ask_update_choice(
        parent, "Update WoW WotLK to the newest code?", UPDATE_TEXT
    ),
    "rebuild-bots": lambda parent, _tmp: ask_backup_choice(
        parent,
        REBUILD_BOTS_TITLE,
        REBUILD_BOTS_TEXT,
        back_up_first="Back up first, then rebuild",
        without_backup="Rebuild without a backup",
    ),
    "set-client-folder": lambda parent, _tmp: ask_to_set_client_dir(parent, ARAC),
    "install-folder": lambda parent, tmp: catalog_view._qt_suggestion_asker(
        parent, load_catalog().get("wow-wotlk").name, tmp / "wow-wotlk"
    ),
}


def _unfit(seen: Seen) -> dict[str, tuple[int, int]]:
    return {label: sizes for label, sizes in seen.items() if sizes[0] > sizes[1]}


@pytest.mark.parametrize("icons", [False, True], ids=["no-icons", "icons"])
@pytest.mark.parametrize("width", [1280, 960], ids=["1280-wide", "steam-deck-960"])
@pytest.mark.parametrize("dialog", sorted(DIALOGS))
def test_every_button_label_fits_its_button(
    dialog: str,
    width: int,
    icons: bool,
    themed_parent: Callable[[int], QWidget],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seen = _show_instead_of_exec(monkeypatch, icons=icons)
    DIALOGS[dialog](themed_parent(width), tmp_path)

    assert len(seen) == 1, "the dialog was never shown"
    assert len(seen[0]) >= 2, f"expected the dialog's buttons, measured {seen[0]}"
    assert _unfit(seen[0]) == {}, f"clipped (label needs px, button has px): {seen[0]}"


def test_the_labels_still_fit_after_the_window_restyles_the_open_dialog(
    themed_parent: Callable[[int], QWidget],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`main.py` re-applies the theme to the window on every resize, open modal or not.

    The style sheet re-polishes every button under it, and re-polishing puts
    the 98px explicit minimum back. A fix made once, at show time, is undone
    by the first resize.
    """
    parent = themed_parent(1280)
    seen = _show_instead_of_exec(
        monkeypatch, during=lambda _box: apply_dadcraft_theme(parent, width=1100), icons=True
    )
    DIALOGS["update-to-latest"](parent, tmp_path)

    assert len(seen) == 2
    assert _unfit(seen[0]) == {}, f"clipped before the restyle: {seen[0]}"
    assert _unfit(seen[1]) == {}, f"clipped after the restyle: {seen[1]}"


def test_the_labels_fit_in_the_first_frame_the_dialog_is_shown_in(
    themed_parent: Callable[[int], QWidget],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Measured straight after `show()`, before any posted event has run.

    `QMessageBox` fixes its width inside its own show. A fix that waited for a
    later layout pass would first draw the clipped labels and then jump.
    """
    seen: list[Seen] = []

    def fake_exec(box: QMessageBox) -> int:
        _give_icons(box)
        box.show()
        seen.append(_measure(box))
        box.hide()
        return int(QMessageBox.StandardButton.Cancel)

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    DIALOGS["update-to-latest"](themed_parent(1280), tmp_path)

    assert len(seen) == 1
    assert _unfit(seen[0]) == {}, f"clipped in the first frame: {seen[0]}"


QT_HARD_LIMIT_ON_A_HANDHELD = 800
"""The widest `QMessageBox` makes itself on a 1280×800 or 960×640 screen.

`QMessageBoxPrivate::updateSize()` caps the box at `min(screen width - 480,
1000)`, or at the whole width on a screen 1024 or narrower: 800 on the
1280-wide Steam Deck, 960 on a 960-wide window's screen. A box that NEEDS more
than the cap is drawn at the cap, and the buttons are squeezed again.
"""


def test_the_widened_dialog_needs_no_more_than_a_steam_deck_screen_allows(
    themed_parent: Callable[[int], QWidget],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The labels fit by widening the box, so what the box NEEDS must stay under Qt's cap.

    What it needs, not what it got: the box is always drawn within the cap, so
    its drawn width says nothing. Its layout's minimum width is what Qt sizes
    it from, and the icons a Linux desktop adds are counted.
    """
    needs: dict[str, int] = {}

    def fake_exec(box: QMessageBox) -> int:
        _give_icons(box)
        box.show()
        process_events()
        layout = box.layout()
        assert layout is not None
        needs[box.windowTitle()] = layout.totalMinimumSize().width()
        box.hide()
        return int(QMessageBox.StandardButton.Cancel)

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    for name in sorted(DIALOGS):
        DIALOGS[name](themed_parent(1280), tmp_path)

    assert len(needs) == len(DIALOGS)
    too_wide = {title: px for title, px in needs.items() if px > QT_HARD_LIMIT_ON_A_HANDHELD}
    assert too_wide == {}, f"wider than a 1280x800 screen lets a message box be: {needs}"


def test_no_question_is_built_as_a_bare_qmessagebox() -> None:
    """Every `QMessageBox(...)` the app constructs goes through `FittedMessageBox` (T157).

    Read from the syntax tree, not grepped, so a spelling across lines or
    through `QtWidgets.QMessageBox(...)` is caught too. The static calls
    (`QMessageBox.question(...)`) carry only Qt's own short labels and are not
    constructions, so they are not counted.
    """
    import ast

    import yulon

    root = Path(yulon.__file__).parent
    bare: list[str] = []
    for source in sorted(root.rglob("*.py")):
        if source.name == "message_box.py":
            continue
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "QMessageBox":
                bare.append(f"{source.relative_to(root)}:{node.lineno}")
    assert bare == [], f"build these through FittedMessageBox: {bare}"
