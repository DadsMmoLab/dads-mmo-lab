"""Tests for `said_yes()` (T33) and the identity-comparison trap it replaces.

PySide6 6.11.2's static `QMessageBox.question()` returns a plain `int` -- not a
`QMessageBox.StandardButton` member -- so `answer is StandardButton.Yes` is
always False, on every platform running that version
(`pyplan/gates/t33-yes-reads-as-no-2026-09-11/static_probe.py`). `said_yes()` is the one place that
comparison is spelled correctly; the second test here is the tripwire that
keeps every other call site using it rather than reinventing the bug.
"""

from __future__ import annotations

import ast
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

import yulon
from yulon.ui.answers import said_yes


def test_said_yes_reads_the_static_calls_plain_int_as_yes() -> None:
    """`16384`, what the static call actually returns for Yes -- not the enum member.

    This is the case the identity spelling got wrong, and the one `==` fixes:
    with `is`, `int(StandardButton.Yes) is StandardButton.Yes` is False.
    """
    assert said_yes(int(QMessageBox.StandardButton.Yes)) is True


def test_said_yes_reads_the_enum_member_as_yes() -> None:
    """The shape every existing test fake already returns -- must keep working."""
    assert said_yes(QMessageBox.StandardButton.Yes) is True


def test_said_yes_refuses_the_static_calls_plain_int_for_no() -> None:
    """`65536`, what the static call returns for No."""
    assert said_yes(int(QMessageBox.StandardButton.No)) is False


def test_said_yes_refuses_the_enum_member_for_no() -> None:
    assert said_yes(QMessageBox.StandardButton.No) is False


def test_said_yes_refuses_a_dismissed_dialogs_nobutton() -> None:
    """Escape or the window's close button return `NoButton` (0), not consent."""
    assert said_yes(QMessageBox.StandardButton.NoButton) is False


def test_said_yes_refuses_a_bare_zero() -> None:
    """`NoButton`'s own value, spelled as a plain `int` rather than the enum member."""
    assert said_yes(0) is False


def _standard_button_identity_comparisons() -> list[str]:
    """Every `is`/`is not` comparison in the shipped `yulon` package that names `StandardButton`.

    Walks every `.py` file `yulon` ships (`ast.walk`, nested defs and lambdas
    included) for an `ast.Compare` using `Is`/`IsNot` where either side,
    unparsed, mentions `StandardButton` -- the shape T33's six sites all shared
    and `said_yes()` exists to replace. Returns `"path:lineno: source"` for
    each hit, so a failure names exactly where.
    """
    hits: list[str] = []
    root = Path(yulon.__file__).parent
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not any(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops):
                continue
            operands = [node.left, *node.comparators]
            if any("StandardButton" in ast.unparse(operand) for operand in operands):
                hits.append(f"{path.relative_to(root.parent)}:{node.lineno}: {ast.unparse(node)}")
    return hits


def test_no_identity_comparison_against_a_standard_button() -> None:
    """T33's actual guard: no `is`/`is not StandardButton...` anywhere `yulon` ships.

    `==` and `is` agree for the enum member every test fake returns, which is
    exactly why this shipped unnoticed the first time -- so the six sites this
    caught are switched to `said_yes()`, and this is what stops a seventh from
    being written the same way.
    """
    assert _standard_button_identity_comparisons() == []
