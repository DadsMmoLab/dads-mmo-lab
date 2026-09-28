"""Every sentence that names a "Server build ▾" press names it as the menu shows it (T155).

The rebuild-owed chip said "Press Rebuild server… on this tab" for weeks after
the press became "Rebuild the server…", and after T89 moved it into the
"Server build ▾" menu. Nothing failed, because the sentence spelled the label by
hand and no test compared the two. This reads the string literals and f-strings
under `yulon/` and `main.py` -- everything but docstrings, and so logger lines
too, which is accepted: a log line that names a press by a name it does not
have misleads whoever reads the bug report the same way -- and holds two rules
over them:

1. The four labels are spelled in ONE file, `yulon/server_build_presses.py`.
   Anywhere else they are built from its constants, so a rename moves every
   sentence that names the press along with the press.
2. No string carries a shorthand for a press in place of its label: the old
   "Rebuild server…", a bare "Rebuild", "press Update", "press Return",
   "Update to latest", "Return to pin". Those are the drafts a sentence falls
   back to when it is typed rather than built.

An f-string is read WHOLE, each `{field}` drawn as `{…}` and, for rule 1, also
dropped; so is a `+` chain of strings. A label split around a field or across
a concatenation is still a label typed by hand. Comments are not in the AST at
all. What this cannot see is a label assembled at run time
(`" ".join(["Rebuild", "the", "server…"])`), which nobody writes by accident.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import installer_for
from yulon.ui import controller_view as cv

PYLAUNCHER = Path(__file__).resolve().parents[1]
HOME = PYLAUNCHER / "yulon" / "server_build_presses.py"
LABELS = (
    cv.SERVER_BUILD_LABEL,
    cv.REBUILD_BUTTON_LABEL,
    cv.UPDATE_TO_LATEST_BUTTON_LABEL,
    cv.RETURN_TO_PIN_BUTTON_LABEL,
)
FIELD = "{…}"
STALE = re.compile(
    r"Rebuild server"
    r"|Rebuild the server(?!…)"
    # A capital "Rebuild" is a press name, and the only true ones are the label
    # itself and the controls that are NOT a server build: the "Rebuild
    # pending" chip, T144's "Rebuild random bots…" and its dialog ("Rebuild the
    # random bots?", "Rebuild without a backup"), and a dialog TITLE naming
    # what is rebuilt ("Rebuild {…}?").
    r"|\bRebuild\b(?! the server…| pending| random bots| the random bots| without a backup"
    r"| \{…\})"
    # "Update" alone is the per-module pull's own button (`apply.py` sends the
    # player back to it), and "Update now" is the app's self-update; any other
    # "press Update" is a server-build press typed short.
    r"|\b[Pp]ress Update\b(?! now\b| to check it again)"
    r"|\b[Pp]ress Return\b"
    r"|Update to latest"
    r"|Return to (?:the tested )?pin\b(?!…)"
    r"|Server build\b(?! ▾)"
)
"""Each alternative is one hand-typed spelling of a press, and none of them is its label."""


def _sources() -> list[Path]:
    return sorted((PYLAUNCHER / "yulon").rglob("*.py")) + [PYLAUNCHER / "main.py"]


def _render(node: ast.AST, field: str) -> str | None:
    """The text of a string expression, each non-literal part drawn as `field`; None if not one."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            str(part.value) if isinstance(part, ast.Constant) else field for part in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _render(node.left, field), _render(node.right, field)
        if left is None and right is None:
            return None
        return (field if left is None else left) + (field if right is None else right)
    return None


def _shown_strings(path: Path) -> Iterator[tuple[int, str, str]]:
    """`(line, text with fields as {…}, text with fields dropped)` for every shown string.

    Only WHOLE strings: a piece of an f-string or of a `+` chain is read as part
    of its whole and never on its own, because a piece has lost its context --
    `f"Rebuild {name}?"`'s first piece is a bare "Rebuild ", and
    `"Press Update the server " + "to latest…"`'s is a bare "Press Update".
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skipped: set[int] = set()
    for node in ast.walk(tree):
        # A bare string statement is a docstring wherever it stands: the module's,
        # a def's, or the attribute docstring this codebase puts under a constant.
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            skipped.add(id(node.value))
        elif isinstance(node, ast.JoinedStr):
            skipped.update(id(part) for part in node.values)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            skipped.update((id(node.left), id(node.right)))
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        shown = _render(node, FIELD)
        if shown is not None:
            yield node.lineno, shown, _render(node, "") or ""


def _offenders(path: Path, root: Path = PYLAUNCHER) -> list[str]:
    where = path.relative_to(root)
    found: set[str] = set()
    for line, shown, joined in _shown_strings(path):
        for label in LABELS:
            if label in shown or label in joined:
                found.add(f"{where}:{line}: spells {label!r} by hand: {shown!r}")
        stale = STALE.search(shown)
        if stale:
            found.add(f"{where}:{line}: names a press as {stale[0]!r}: {shown!r}")
    return sorted(found)


def test_the_scan_reads_the_strings_it_is_meant_to() -> None:
    """The ground for the scan: one that read nothing would pass it.

    `CHIP_REBUILD_PENDING` is a plain constant and must be found; the module
    docstring of `modules_panel.py` is a docstring and must not be; and the
    chip's owed-rebuild sentence is an f-string whose label arrives through a
    field, so it must be read whole with that field drawn in.
    """
    panel = PYLAUNCHER / "yulon" / "ui" / "widgets" / "modules_panel.py"
    found = [shown for _line, shown, _joined in _shown_strings(panel)]
    assert "Rebuild pending" in found
    doc = ast.get_docstring(ast.parse(panel.read_text(encoding="utf-8")), clean=False)
    assert doc and doc not in found
    assert any(s.endswith(f"yet. Press {FIELD}.") for s in found), "no whole f-string was read"
    assert len(_sources()) > 50, "the walk did not reach the package"


def test_the_rules_catch_each_way_a_press_is_typed_by_hand(tmp_path: Path) -> None:
    """Each fixture line breaks one rule, and the last line -- every true spelling -- breaks none.

    Read through the same `_offenders()` the real scan uses, from a real file,
    so a rule that stopped matching, or a rendering that stopped joining an
    f-string's pieces, fails here by name rather than as a green scan.
    """
    cases = {
        "old_label": '"Press Rebuild server… on this tab."',
        "bare_rebuild_press": '"Check Docker, then press Rebuild again."',
        "bare_rebuild_name": '"and Rebuild works from then on."',
        "press_update": '"then press Update again to move it."',
        "press_return": '"press Return to go back."',
        "update_to_latest": '"Update to latest brings it in."',
        "return_to_pin": '"Return to pin undoes it."',
        "menu_without_triangle": '"under Server build on the Modules tab"',
        "label_retyped": '"Press \\u201cRebuild the server\\u2026\\u201d now."',
        "label_split_by_field": 'f"Press Update the server {x}to latest\\u2026 now."',
        "label_split_by_plus": '"Press Update the server " + "to latest\\u2026 now."',
    }
    clean = (
        'f"Press {where(REBUILD)}. Rebuild pending. Rebuild random bots… Press Update now. '
        'Press Update to check it again. Rebuild {name}?"'
    )
    lines = [f"{name} = {text}" for name, text in cases.items()] + [f"clean = {clean}"]
    fixture = tmp_path / "fixture.py"
    fixture.write_text("\n".join(lines) + "\n", encoding="utf-8")

    offenders = _offenders(fixture, tmp_path)
    by_line = {int(o.split(":")[1]) for o in offenders}
    for number, name in enumerate(cases, start=1):
        assert number in by_line, (name, offenders)
    assert len(cases) + 1 not in by_line, offenders


def test_every_sentence_naming_a_server_build_press_is_built_from_its_label() -> None:
    """Rule 1 and rule 2 of the module docstring, over every shown string in the app.

    Mutation: put "Rebuild server…" back into `CHIP_ACTION_LABELS`, or retype
    "Update the server to latest…" into `upstream.py`'s news line, and this
    names the file and line.
    """
    offenders = [o for path in _sources() if path != HOME for o in _offenders(path)]
    assert not offenders, "\n".join(offenders)


def test_only_a_server_build_press_runs_the_recreate_stage() -> None:
    """The ground under the refusals that send the player to "Server build ▾" again.

    `stage_recreate()` and `_keep_rollback()` refuse with "press the same entry
    under “Server build ▾” … again", which is true only if nothing but those
    entries reaches them. Round 1's review read `recreate` as an install stage
    as well, which would send a failed Install to a menu it never pressed.
    Measured here instead of argued: every shipped entry's install tuple ends
    in `up` and holds no `stage_recreate`, and only `rebuild_stages()` does.
    """
    for entry in load_catalog().games:
        engine = installer_for(entry, platform_id=lambda: "linux")
        assert isinstance(engine, native.StagedInstaller), entry.id
        assert "recreate" not in engine.stage_names(), entry.id
        assert engine.stage_recreate not in [s.run for s in engine.stages()], entry.id
        assert engine.stage_recreate in [s.run for s in engine.rebuild_stages()], entry.id


def test_the_refusals_are_reached_only_from_a_rebuild() -> None:
    """The call-site half of the test above, read off the source of the whole package.

    `rebuild()` is the only caller of the rollback keep and restore and of
    `rebuild_stages()`; `stage_recreate` is named only there, inside
    `rebuild()`'s own `recreate`, and in `_restore_rollback()`; and `rebuild()`
    itself is called by the view's Rebuild (`install_wiring.rebuild_for_app`)
    and by Update to latest / Return to the tested pin (`update_to_latest`).
    Those are the three "Server build ▾" entries, and nothing else.
    """
    allowed = {
        "stage_recreate": {"rebuild_stages", "rebuild", "recreate", "_restore_rollback"},
        "_keep_rollback": {"rebuild"},
        "_restore_rollback": {"rebuild"},
        "rebuild_stages": {"rebuild"},
        # `rebuild` is `rebuild_for_app()`'s inner generator, which the walk
        # also credits to the function around it.
        "rebuild": {"update_to_latest", "rebuild", "rebuild_for_app"},
    }
    seen: dict[str, set[str]] = {name: set() for name in allowed}
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                    # `seam.rebuild(...)` is T144's random-bot pool
                    # (`controller_wow_tortoise/poolreset.py`): another press,
                    # which never reaches a compile.
                    if name == "rebuild" and ast.unparse(node.func.value) == "seam":
                        continue
                elif isinstance(node, ast.Attribute) and node.attr == "stage_recreate":
                    name = node.attr
                else:
                    continue
                if name in seen:
                    seen[name].add(function.name)
    for name, callers in seen.items():
        assert callers and callers <= allowed[name], (name, callers)
