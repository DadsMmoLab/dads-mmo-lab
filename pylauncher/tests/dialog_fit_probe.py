"""T243's probe: open each long question for real on one screen size and measure it.

Run by `tests/test_dialogs_fit_the_screen.py` in a child process, because the
screen is the thing under test and a process has only the one its platform
plugin made: `QT_QPA_PLATFORM=offscreen:configfile=<json>` gives the child a
960×640, a 1280×800 or a 1920×1080 screen, where the suite's own offscreen
screen is 800×800 whatever a test wants.

Each question is asked through the real function the app asks it with, and its
real modal `exec()` runs. A zero-delay timer, queued before the ask, fires
inside that modal's own event loop: it measures the dialog on screen, walks it
with the Tab key and with the pad's `Navigator`, and then CLICKS the dialog's
decline button, so the value the function returns is what a real click on that
button answers. Prints one JSON object: dialog name -> measurements.

`python -m tests.dialog_fit_probe` from `pylauncher/`.
"""

from __future__ import annotations

import json
import sys
import tempfile
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QWidget,
)

from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import rebuild_confirmation
from yulon.ui.gamepad import Direction, Navigator
from yulon.ui.theme import apply_dadcraft_theme

PAD_PRESSES = 60
"""More presses than any of these questions has stops; a walk that needs more is stuck."""

TORTOISE = load_catalog().get("wow-tortoise")
"""The longest Rebuild question: a rendered Dockerfile, and databases the update copies."""

SERVER_DIR_NAME = "server"


def _kept_build(server_dir: Path) -> None:
    """A real kept-build record (T224), so the question carries its paragraph."""
    said = native.remember_parked_build(
        server_dir,
        native.ParkedBuild(
            fingerprint="a" * 64,
            images={"tortoise-server:latest": "sha256:" + "b" * 64},
            made_unix=1_791_000_000,
            app="0.9.0",
        ),
    )
    assert said == "", said
    assert native.read_parked_build(server_dir) is not None


def _update_text(server_dir: Path) -> str:
    """The Update-to-latest question with the T217 database copy and a T126 rewritten line."""
    return native.update_to_latest_confirmation(
        TORTOISE,
        server_dir,
        "Penqle/Tortoise-WoW-Server",
        (native.rewritten_line("Penqle/Tortoise-WoW-Server", "v2.4.1", 3),),
        copied=("tw_logon", "tw_char"),
        not_copied=("tw_world",),
    )


def _rebuild_text(server_dir: Path) -> str:
    return rebuild_confirmation(TORTOISE, server_dir, kept_build=True)


def _longest_text(server_dir: Path) -> str:
    """Kept build + Docker Hub note + database copy: the three longest real texts in one."""
    return "\n\n".join(
        (
            _rebuild_text(server_dir),
            native.rebuild_opening_note(renders_dockerfile=True, parked=True),
            _update_text(server_dir),
        )
    )


def _repair_text() -> str:
    from yulon.ui import controller_view

    confs = controller_view.REPAIR_FILES_CONFS.format(
        settings="LogsDir in mangosd.conf and LogsDir in realmd.conf"
    )
    return controller_view.REPAIR_FILES_CONFIRM.format(
        backup="docker-compose.yml.<date>" + native.REPAIR_BACKUP_SUFFIX,
        counts=" (it adds 12 lines and removes 9)",
        confs=confs,
        others="any other .conf setting",
        # T219's paragraph too: every part the Repair question can carry, at once.
        volume=controller_view.REPAIR_FILES_WORLD_DATA.format(gb=4.2),
    )


def _error_text() -> str:
    """A compose failure as the install dialogs show it: a long message, then its output."""
    lines = [
        f"  service {n}: pull access denied for azerothcore/ac-wotlk-worldserver-{n}, "
        "repository does not exist or may require 'docker login': denied: requested access "
        "to the resource is denied"
        for n in range(30)
    ]
    return "The install stopped: docker compose could not start the containers.\n\n" + "\n\n".join(
        lines
    )


def _yes_no(parent: QWidget, title: str, text: str) -> object:
    from yulon.ui.message_box import ask_yes_no

    return ask_yes_no(parent, title, text)


def _warning(parent: QWidget, title: str, text: str) -> object:
    from yulon.ui.message_box import show_warning

    show_warning(parent, title, text)
    return "ok"


def _information(parent: QWidget, title: str, text: str) -> object:
    from yulon.ui.message_box import show_information

    show_information(parent, title, text)
    return "ok"


def _update(parent: QWidget, title: str, text: str) -> object:
    from yulon.ui.controller_view import ask_update_choice

    return ask_update_choice(parent, title, text).value


def dialogs(server_dir: Path) -> dict[str, tuple[Callable[..., object], str, str]]:
    """Name -> (the function the app asks with, title, text)."""
    pin = native.return_to_pin_confirmation(TORTOISE, server_dir, "Penqle/Tortoise-WoW-Server")
    return {
        "rebuild": (_yes_no, f"Rebuild {TORTOISE.name}?", _rebuild_text(server_dir)),
        "update-to-latest": (
            _update,
            f"Update {TORTOISE.name} to the newest code?",
            _update_text(server_dir),
        ),
        "return-to-pin": (
            _yes_no,
            f"Put {TORTOISE.name} back on the tested commit?",
            pin,
        ),
        "repair": (_yes_no, "Repair server files…", _repair_text()),
        "longest": (_yes_no, f"Rebuild {TORTOISE.name}?", _longest_text(server_dir)),
        # T355: the one-button notices, with a docker error's text at its longest.
        "warning-longest": (_warning, "Install failed", _error_text()),
        "information-longest": (_information, "Something else is running", _error_text()),
        "longest-three-way": (
            _update,
            f"Update {TORTOISE.name} to the newest code?",
            _longest_text(server_dir),
        ),
    }


# -- measuring ---------------------------------------------------------------


def _global(widget: QWidget) -> QRect:
    return QRect(widget.mapToGlobal(widget.rect().topLeft()), widget.size())


def _rect(r: QRect) -> list[int]:
    return [r.x(), r.y(), r.width(), r.height()]


def _text_labels(box: QWidget) -> list[QLabel]:
    """Every label in the box that shows words: the question, however it is laid out."""
    return [
        label
        for label in box.findChildren(QLabel)
        if label.isVisibleTo(box) and label.text().strip()
    ]


def _viewport_of(widget: QWidget) -> QWidget | None:
    parent = widget.parentWidget()
    while parent is not None:
        area = parent.parentWidget()
        if isinstance(area, QScrollArea) and area.viewport() is parent:
            return parent
        parent = area
    return None


def _shown(widget: QWidget, screen: QRect) -> QRect:
    """The part of `widget` a player can see: cut by its scroll area and by the screen."""
    seen = _global(widget)
    viewport = _viewport_of(widget)
    if viewport is not None:
        seen = seen.intersected(_global(viewport))
    return seen.intersected(screen)


def _fully_shown(widget: QWidget, screen: QRect) -> bool:
    return _shown(widget, screen) == _global(widget)


def _scroll_areas(box: QWidget) -> list[QScrollArea]:
    return [a for a in box.findChildren(QScrollArea) if a.isVisibleTo(box)]


def measure(box: QMessageBox) -> dict[str, Any]:
    """Everything the test asserts, read off the dialog while its modal loop runs."""
    QApplication.processEvents()
    screen = box.screen().availableGeometry()
    frame = box.frameGeometry()
    buttons = [b for b in box.findChildren(QPushButton) if b.isVisibleTo(box)]
    labels = _text_labels(box)
    out: dict[str, Any] = {
        "screen": _rect(screen),
        "frame": _rect(frame),
        "frame_inside_screen": screen.contains(frame),
        "buttons": {
            b.text(): {
                "inside_box": box.rect().contains(
                    QRect(b.mapTo(box, b.rect().topLeft()), b.size())
                ),
                "fully_on_screen": _fully_shown(b, screen),
            }
            for b in buttons
        },
        "words": " ".join(" ".join(label.text() for label in labels).split()),
        "question": " ".join(box.text().split()),
        "first_focus": _name(QApplication.focusWidget(), box),
        "decline": (
            (
                box.button(QMessageBox.StandardButton.No)
                or box.button(QMessageBox.StandardButton.Cancel)
                or box.button(QMessageBox.StandardButton.Ok)
            ).text()
        ),
    }

    # Shown as it opens, with nothing scrolled.
    out["labels_shown_at_open"] = [_fully_shown(label, screen) for label in labels]

    # Reachable by scrolling: every scroll area to its bottom, then each label in turn.
    for area in _scroll_areas(box):
        area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
    QApplication.processEvents()
    out["last_label_shown_scrolled_to_the_end"] = bool(labels) and _fully_shown(labels[-1], screen)
    reachable = []
    for label in labels:
        for area in _scroll_areas(box):
            if area.widget() is not None and area.widget().isAncestorOf(label):
                area.ensureWidgetVisible(label, 0, 0)
        QApplication.processEvents()
        reachable.append(_fully_shown(label, screen))
    out["labels_reachable_by_scrolling"] = reachable
    for area in _scroll_areas(box):
        area.verticalScrollBar().setValue(0)
    QApplication.processEvents()

    out.update(_walk_with_tab(box, buttons))
    out.update(_walk_with_the_pad(box, buttons, labels, screen))
    return out


def _name(widget: QWidget | None, box: QWidget) -> str:
    if widget is None:
        return "<nothing>"
    if not box.isAncestorOf(widget):
        return f"<outside the dialog: {type(widget).__name__}>"
    if isinstance(widget, QAbstractButton):
        return f"button:{widget.text()}"
    if isinstance(widget, QLabel):
        return "label"
    return type(widget).__name__


def _walk_with_tab(box: QMessageBox, buttons: list[QPushButton]) -> dict[str, Any]:
    """Tab round the dialog: which buttons the keyboard reaches."""
    start = QApplication.focusWidget()
    reached: set[str] = set()
    for _ in range(PAD_PRESSES):
        focused = QApplication.focusWidget()
        if focused is None:
            break
        if isinstance(focused, QPushButton) and focused in buttons:
            reached.add(focused.text())
        QTest.keyClick(focused, Qt.Key.Key_Tab)
        QApplication.processEvents()
    if start is not None:
        start.setFocus(Qt.FocusReason.OtherFocusReason)
        QApplication.processEvents()
    return {"tab_reaches": sorted(reached)}


def _walk_with_the_pad(
    box: QMessageBox, buttons: list[QPushButton], labels: list[QLabel], screen: QRect
) -> dict[str, Any]:
    """The pad's D-pad, through the app's own `Navigator`: up through the text, down to a button,
    and along the buttons. A label counts as read when the pad stopped on it and it showed."""
    navigator = Navigator()
    start = QApplication.focusWidget()
    read = [False] * len(labels)
    lost = False

    def note() -> None:
        nonlocal lost
        focused = QApplication.focusWidget()
        if focused is None or not box.isAncestorOf(focused):
            lost = True
            return
        for i, label in enumerate(labels):
            if label is focused and not _shown(label, screen).isEmpty():
                read[i] = True

    # Up to the top of the question (the pad wraps at an edge, so it stops on arriving)...
    for _ in range(PAD_PRESSES):
        if not navigator.navigate(Direction.UP):
            break
        QApplication.processEvents()
        note()
        if labels and QApplication.focusWidget() is labels[0]:
            break
    # ...and Down from there until it comes to a button.
    for _ in range(PAD_PRESSES):
        if not navigator.navigate(Direction.DOWN):
            break
        QApplication.processEvents()
        note()
        if QApplication.focusWidget() in buttons:
            break
    after_down = QApplication.focusWidget()
    # Along the buttons, both ways, from wherever Down left the focus.
    reached: set[str] = set()
    for direction in (Direction.LEFT, Direction.RIGHT, Direction.LEFT):
        for _ in range(len(buttons) + 1):
            focused = QApplication.focusWidget()
            if isinstance(focused, QPushButton) and focused in buttons:
                reached.add(focused.text())
            navigator.navigate(direction)
            QApplication.processEvents()
            note()
    if start is not None:
        start.setFocus(Qt.FocusReason.OtherFocusReason)
        QApplication.processEvents()
    return {
        "pad_read_labels": read,
        "pad_left_the_dialog": lost,
        "pad_down_ends_on": _name(after_down, box),
        "pad_reaches": sorted(reached),
    }


# -- running -------------------------------------------------------------------


def run() -> dict[str, Any]:
    app = QApplication.instance() or QApplication(sys.argv)
    available = QGuiApplication.primaryScreen().availableGeometry()
    window = QWidget()
    apply_dadcraft_theme(window, width=available.width())
    window.setGeometry(available)
    window.show()
    QApplication.processEvents()

    results: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        server_dir = Path(tmp) / SERVER_DIR_NAME
        server_dir.mkdir()
        _kept_build(server_dir)
        for name, (ask, title, text) in dialogs(server_dir).items():
            seen: dict[str, Any] = {}

            def inspect(seen: dict[str, Any] = seen) -> None:
                box = QApplication.activeModalWidget()
                if not isinstance(box, QMessageBox):
                    seen.setdefault("error", f"no modal message box, found {box!r}")
                    if box is not None:
                        box.close()
                    return
                try:
                    seen.update(measure(box))
                except Exception:  # the measuring failed; say so, and still answer
                    seen["error"] = traceback.format_exc()
                decline = (
                    box.button(QMessageBox.StandardButton.No)
                    or box.button(QMessageBox.StandardButton.Cancel)
                    or box.button(QMessageBox.StandardButton.Ok)
                )
                if decline is None:
                    seen["error"] = "no decline button"
                    box.close()
                    return
                decline.click()

            QTimer.singleShot(0, inspect)
            try:
                seen["answer"] = repr(ask(window, title, text))
            except Exception:
                seen["error"] = traceback.format_exc()
            seen["text"] = " ".join(text.split())
            results[name] = seen
            QApplication.processEvents()
    window.close()
    del app
    return results


if __name__ == "__main__":
    print(json.dumps(run()))
