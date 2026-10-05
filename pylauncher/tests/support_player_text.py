"""What a player can read on screen, checked for developer notes (T194).

`player_text_faults(root)` walks the widgets a player could see under `root`,
reads the kinds of text listed below, and reports each one that reads like a
note to ourselves rather than something meant for them: a ticket number, an
owner decision, the word "manifest", "headless", a Python exception class, a
traceback, a Docker pipe path. Each fault names the widget (its objectName, or its class when it has
none), where the text was found, the rule it broke and the text itself.

What is read: `QLabel.text()`, `QAbstractButton.text()`, `QGroupBox.title()`,
every `QTabBar` tab's text and tooltip, every widget's `toolTip()`, and a
read-only `QPlainTextEdit`'s text and placeholder. Only widgets visible to
`root` count (`isVisibleTo`), so a page behind another tab is not read until
the test makes it current.

Not read: a `QComboBox`'s items, a `QLineEdit`'s text and placeholder, the rows
of a list, table or tree view, a `QTextEdit`, and a window's status bar. A
section that is folded or a panel that is built later is read only in the state
a test puts it in.

Skipped: the install console (`LogPanel`, and everything inside it) and the
Logs tab's file viewer. Both show what a program wrote, a log line naming an
exception class is the point of a log, and neither is text Yu'lon composed.
"""

from __future__ import annotations

import re
from typing import Any

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ticket number", re.compile(r"\bT\d{1,3}\b")),
    ("owner decision", re.compile(r"owner decision")),
    ("manifest", re.compile(r"(?i)\bmanifest")),
    ("headless", re.compile(r"(?i)headless")),
    ("exception class", re.compile(r"\b[A-Z][A-Za-z]*(Error|Exception)\b")),
    ("traceback", re.compile(r"Traceback")),
    ("pipe path", re.compile(r"//\./pipe")),
    ("npipe", re.compile(r"npipe")),
)


def text_faults(text: str) -> list[str]:
    """The names of the rules `text` breaks, in `RULES` order."""
    return [name for name, pattern in RULES if pattern.search(text)]


COMMAND_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("backtick", re.compile(r"`")),
    (
        "docker command",
        re.compile(
            r"\bdocker\s+(?:compose|ps|logs|exec|run|start|stop|restart|rm|volume|image|"
            r"info|version|system|attach|inspect|cp|pull|build|create|kill|network)\b"
        ),
    ),
    ("compose command", re.compile(r"\bcompose\s+(?:logs|up|down|ps|run|build|ls|exec)\b")),
    ("--rm", re.compile(r"--rm\b")),
    ("systemctl", re.compile(r"\bsystemctl\b")),
    ("journalctl", re.compile(r"\bjournalctl\b")),
    ("wsl command", re.compile(r"\bwsl(?:\.exe)?\s+-{1,2}[a-z]")),
    (
        "sudo",
        re.compile(
            r"\bsudo\s+(?:apt|apt-get|pacman|dnf|zypper|systemctl|service|usermod|chcon|passwd)\b"
        ),
    ),
    (
        "package manager",
        re.compile(r"\b(?:apt|apt-get|pacman|dnf|zypper|brew)\s+(?:-S\b|install\b|update\b)"),
    ),
    (
        "git command",
        re.compile(r"\bgit\s+(?:fetch|reset|clone|log|status|stash|pull|-C)\b|\bgit checkout -"),
    ),
    (
        "shell command",
        re.compile(r"\b(?:gunzip|chcon|xcode-select|usermod|passwd|pacman-key|steamos-readonly)\b"),
    ),
    (
        "firewall command",
        re.compile(r"\b(?:firewall-cmd|firewall-offline-cmd)\b|\bufw\s+(?:allow|enable)\b"),
    ),
)
"""A command on a player's line (T248): the line says in words what failed and
where to look, and a command worth running goes under Details (T194, T214).

Kept apart from `RULES` because a command the player has to type stays on the
line on purpose (T194's systemctl banner; the owner's rule for T296): `RULES`
holds for every screen, these for every player line but the exceptions
`test_player_lines_name_no_commands` names.
"""


def command_faults(text: str) -> list[str]:
    """The names of the `COMMAND_RULES` `text` breaks, in that order."""
    return [name for name, pattern in COMMAND_RULES if pattern.search(text)]


def _skipped(widget: Any) -> bool:
    """The install console and everything in it, and the Logs tab's file viewer."""
    from yulon.ui.logs_view import LogsView
    from yulon.ui.widgets.log_panel import LogPanel

    node = widget
    while node is not None:
        if isinstance(node, LogPanel):
            return True
        parent = node.parentWidget()
        if isinstance(parent, LogsView) and node is parent.viewer:
            return True
        node = parent
    return False


def _texts(widget: Any) -> list[tuple[str, str]]:
    """(where, text) for every piece of text `widget` shows."""
    from PySide6.QtWidgets import (
        QAbstractButton,
        QGroupBox,
        QLabel,
        QPlainTextEdit,
        QTabBar,
    )

    found: list[tuple[str, str]] = []
    if isinstance(widget, QLabel):
        found.append(("text", widget.text()))
    if isinstance(widget, QAbstractButton):
        found.append(("text", widget.text()))
    if isinstance(widget, QGroupBox):
        found.append(("title", widget.title()))
    if isinstance(widget, QTabBar):
        for index in range(widget.count()):
            found.append((f"tab {index} text", widget.tabText(index)))
            found.append((f"tab {index} tooltip", widget.tabToolTip(index)))
    if isinstance(widget, QPlainTextEdit) and widget.isReadOnly():
        found.append(("text", widget.toPlainText()))
        found.append(("placeholder", widget.placeholderText()))
    found.append(("tooltip", widget.toolTip()))
    return [(where, text) for where, text in found if text]


def visible_texts(root: Any) -> list[tuple[str, str, str]]:
    """(widget name, where, text) for every piece of text a player can read under `root`."""
    from PySide6.QtWidgets import QWidget

    found: list[tuple[str, str, str]] = []
    for widget in [root, *root.findChildren(QWidget)]:
        if widget is not root and not widget.isVisibleTo(root):
            continue
        if _skipped(widget):
            continue
        name = widget.objectName() or type(widget).__name__
        found += [(name, where, text) for where, text in _texts(widget)]
    return found


def player_text_faults(root: Any) -> list[str]:
    """Every developer note in the texts `visible_texts` reads under `root`; `[]` when none."""
    return [
        f"{name} {where} [{rule}]: {text!r}"
        for name, where, text in visible_texts(root)
        for rule in text_faults(text)
    ]
