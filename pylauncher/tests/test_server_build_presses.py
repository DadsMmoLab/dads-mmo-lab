"""Every sentence that names a "Server build ▾" press names it as the menu shows it (T155).

The rebuild-owed chip said "Press Rebuild server… on this tab" for weeks after
the press became "Rebuild the server…", and after T89 moved it into the
"Server build ▾" menu. Nothing failed, because the sentence spelled the label by
hand and no test compared the two. This reads every string literal a user can
be shown -- everything but docstrings -- under `yulon/` and `main.py`, and holds
two rules over them:

1. The four labels are spelled in ONE file, `yulon/server_build_presses.py`.
   Anywhere else they are built from its constants, so a rename moves every
   sentence that names the press along with the press.
2. No literal carries a spelling of a press that is not its label: the old
   "Rebuild server…", a bare "press Rebuild", "Update to latest",
   "Return to pin". Those are the drafts a sentence falls back to when it is
   typed rather than built.

Docstrings are left out because nobody using the app reads them; comments are
not in the AST at all.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

from yulon.ui import controller_view as cv

PYLAUNCHER = Path(__file__).resolve().parents[1]
HOME = PYLAUNCHER / "yulon" / "server_build_presses.py"
LABELS = (
    cv.SERVER_BUILD_LABEL,
    cv.REBUILD_BUTTON_LABEL,
    cv.UPDATE_TO_LATEST_BUTTON_LABEL,
    cv.RETURN_TO_PIN_BUTTON_LABEL,
)
STALE = re.compile(
    r"Rebuild server"
    r"|Rebuild the server(?!…)"
    r"|\b[Pp]ress Rebuild\b(?! random bots)"
    r"|Update to latest"
    r"|Return to (?:the tested )?pin\b(?!…)"
    r"|Server build\b(?! ▾)"
)
"""Each alternative is one hand-typed spelling of a press, and none of them is its label."""


def _sources() -> list[Path]:
    return sorted((PYLAUNCHER / "yulon").rglob("*.py")) + [PYLAUNCHER / "main.py"]


def _shown_strings(path: Path) -> Iterator[tuple[int, str]]:
    """Every `str` constant in `path` except docstrings, f-string pieces included."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        # A bare string statement is a docstring wherever it stands: the module's,
        # a def's, or the attribute docstring this codebase puts under a constant.
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            docstrings.add(id(node.value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node.lineno, node.value


def test_the_scan_reads_the_strings_it_is_meant_to() -> None:
    """The ground for the next test: a scan that read nothing would pass it.

    `CHIP_REBUILD_PENDING` is a plain constant and must be found; the module
    docstring of `modules_panel.py` is a docstring and must not be.
    """
    panel = PYLAUNCHER / "yulon" / "ui" / "widgets" / "modules_panel.py"
    found = [text for _line, text in _shown_strings(panel)]
    assert "Rebuild pending" in found
    doc = ast.get_docstring(ast.parse(panel.read_text(encoding="utf-8")), clean=False)
    assert doc and doc not in found
    assert len(_sources()) > 50, "the walk did not reach the package"


def test_every_sentence_naming_a_server_build_press_is_built_from_its_label() -> None:
    """Rule 1 and rule 2 of the module docstring, over every shown string.

    Mutation: put "Rebuild server…" back into `CHIP_ACTION_LABELS`, or retype
    "Update the server to latest…" into `upstream.py`'s news line, and this
    names the file and line.
    """
    offenders: list[str] = []
    for path in _sources():
        if path == HOME:
            continue
        where = path.relative_to(PYLAUNCHER)
        for line, text in _shown_strings(path):
            for label in LABELS:
                if label in text:
                    offenders.append(f"{where}:{line}: spells {label!r} by hand: {text!r}")
            stale = STALE.search(text)
            if stale:
                offenders.append(f"{where}:{line}: names a press as {stale[0]!r}: {text!r}")
    assert not offenders, "\n".join(offenders)
