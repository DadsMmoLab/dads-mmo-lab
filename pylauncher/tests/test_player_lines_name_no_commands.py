"""No command on a player's line (T248): the line is words, a command goes under Details.

T194 took developer notes off the screen and T214 split each failure into
Yu'lon's sentence on the line and the program's words under Details. Repair's
"did not finish" sentences still ended "`docker compose logs ac-db-import` in
<folder> has the rest", a command in backticks on the line. This reads the
source, not a screen, because most of these sentences appear only when
something has gone wrong, which no screen sweep reaches:

* every label constant in the UI modules and the Docker banner's texts: a
  module-level name in capitals (`REPAIR_IDLE`, `_DESKTOP_NOT_RUNNING`) holding
  a string, or a dict or tuple of strings;
* every string the UI hands straight to a widget or a dialog: `setText`,
  `setToolTip`, `_say_under_the_presses`, `QLabel(...)`, `QMessageBox.question`;
* every message given to a type marked `SaidByYulon`, whose text is shown on
  the line as written. `detail=` is not read: that is what goes under Details.

A message assembled out of variables is read as far as it is written in the
source: an f-string's fixed words, a `+` of strings, a module constant, and a
same-module helper's returned strings.

Not read, and left to T296: text that reaches a widget as data rather than by
name (`ProvisionReport.manual_steps`, a job's yielded log lines), and
`InstallerError`, which the Install failed dialog shows as written. Those carry
remedies that are commands to type (`wsl --install`, `sudo pacman -S …`), and
whether each stays as a named exception or moves under Details is that
ticket's decision. The rules name Docker's commands only, for the same reason.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.support_player_text import command_faults

PYLAUNCHER = Path(__file__).resolve().parents[1]
YULON = PYLAUNCHER / "yulon"

LABEL_MODULES: tuple[Path, ...] = (
    *sorted((YULON / "ui").rglob("*.py")),
    YULON / "docker_advice.py",
)
"""Where player-facing label constants live."""

NOT_LABELS: frozenset[str] = frozenset({"yulon/ui/theme.py"})
"""Modules under `yulon/ui` whose capital-named strings are not words: Qt style sheets."""

EXCEPTIONS: dict[tuple[str, str], str] = {
    ("yulon/docker_advice.py", name): (
        "the Linux and Deck 'start the engine' banner names the systemctl line on purpose "
        "(T194): the player has to type it, and there is no press that can"
    )
    for name in (
        "_ENGINE_NOT_RUNNING",
        "_DECK_NOT_RUNNING",
        "_ENGINE_NOT_ANSWERING",
        "_DECK_NOT_ANSWERING",
        "_WSL_NOT_RUNNING",
        "_WSL_UNNAMED",
    )
}
EXCEPTIONS[("yulon/controller_wow_wotlk/console.py", "NO_TTY_HELP")] = (
    "the Console tab's note where Yu'lon cannot open a terminal: typing docker attach in one "
    "is the only way to the console there, and no press can do it"
)
"""(module, constant) -> why it may name a command. Nothing else may."""

NOT_SHOWN: frozenset[tuple[str, str]] = frozenset(
    {("yulon/ui/controller_view.py", "REBUILD_HISTORY")}
)
"""Capital-named strings in the UI modules that are records for readers of the code, never drawn."""

WIDGET_CALLS = frozenset(
    {
        "setText",
        "setToolTip",
        "setPlaceholderText",
        "setTitle",
        "setWindowTitle",
        "_say_under_the_presses",
        "QLabel",
        "QPushButton",
        "QGroupBox",
        "QCheckBox",
        "QRadioButton",
    }
)
DIALOG_CALLS = frozenset({"question", "information", "warning", "critical"})

_CAPITALS = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
_HOLE = "{…}"
_LOGGED = re.compile(r"\b(?:logger|log)\.(?:debug|info|warning|error|exception)$")


@dataclass(frozen=True)
class Line:
    """One player-facing text found in the source."""

    where: str
    what: str
    text: str


def _rel(path: Path) -> str:
    return path.relative_to(PYLAUNCHER).as_posix()


def _name_of(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


class _Module:
    """One parsed source file, with what its texts can be resolved through."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rel = _rel(path)
        self.tree = ast.parse(path.read_text(encoding="utf-8"))
        self.constants: dict[str, ast.expr] = {}
        self.functions: dict[str, list[ast.FunctionDef]] = {}
        self.parents: dict[int, ast.AST] = {}
        for node in self.tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.constants[target.id] = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.value is not None:
                    self.constants[node.target.id] = node.value
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parents[id(child)] = node
            if isinstance(node, ast.FunctionDef):
                self.functions.setdefault(node.name, []).append(node)

    def _enclosing(self, node: ast.AST) -> ast.FunctionDef | None:
        parent = self.parents.get(id(node))
        while parent is not None and not isinstance(parent, ast.FunctionDef):
            parent = self.parents.get(id(parent))
        return parent

    def _logged(self, node: ast.AST) -> bool:
        """Whether `node` is written to the app log rather than shown."""
        parent = self.parents.get(id(node))
        while parent is not None and not isinstance(parent, ast.stmt):
            if isinstance(parent, ast.Call) and _LOGGED.search(ast.unparse(parent.func)):
                return True
            parent = self.parents.get(id(parent))
        return False

    def _sentences(self, function: ast.FunctionDef, depth: int) -> list[str]:
        """Every string a helper builds, docstring and log lines aside: what it can return."""
        found: list[str] = []
        body = function.body[1:] if ast.get_docstring(function) is not None else function.body
        for statement in body:
            for inner in ast.walk(statement):
                if isinstance(inner, ast.JoinedStr) or (
                    isinstance(inner, ast.Constant)
                    and isinstance(inner.value, str)
                    and " " in inner.value.strip()
                ):
                    if not isinstance(self.parents.get(id(inner)), ast.JoinedStr) and not (
                        self._logged(inner)
                    ):
                        found += self.texts(inner, depth + 1)
        return found

    def texts(self, node: ast.expr | None, depth: int = 0) -> list[str]:
        """Every string `node` can be, as far as the source spells it."""
        if node is None or depth > 4:
            return []
        if isinstance(node, ast.Constant):
            return [node.value] if isinstance(node.value, str) else []
        if isinstance(node, ast.JoinedStr):
            return [
                "".join(
                    part.value if isinstance(part, ast.Constant) else _HOLE for part in node.values
                )
            ]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self.texts(node.left, depth + 1) or [_HOLE]
            right = self.texts(node.right, depth + 1) or [_HOLE]
            return [a + b for a in left for b in right]
        if isinstance(node, ast.IfExp):
            return self.texts(node.body, depth + 1) + self.texts(node.orelse, depth + 1)
        if isinstance(node, ast.Name):
            function = self._enclosing(node)
            local = [
                inner.value
                for inner in (ast.walk(function) if function is not None else ())
                if isinstance(inner, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == node.id for t in inner.targets)
            ]
            if local:
                return [text for value in local for text in self.texts(value, depth + 1)]
            if node.id in self.constants:
                return self.texts(self.constants[node.id], depth + 1)
        if isinstance(node, ast.Call):
            name = _name_of(node.func)
            if name == "format" and isinstance(node.func, ast.Attribute):
                return self.texts(node.func.value, depth + 1)
            return [
                text
                for helper in self.functions.get(name, [])
                for text in self._sentences(helper, depth)
            ]
        return []


def _modules() -> list[_Module]:
    return [_Module(path) for path in sorted(YULON.rglob("*.py"))]


def _said_types(modules: list[_Module]) -> set[str]:
    """Every class marked `SaidByYulon`, directly or through a marked base."""
    said = {"SaidByYulon"}
    classes = [
        node
        for module in modules
        for node in ast.walk(module.tree)
        if isinstance(node, ast.ClassDef)
    ]
    grew = True
    while grew:
        grew = False
        for node in classes:
            if node.name not in said and any(_name_of(base) in said for base in node.bases):
                said.add(node.name)
                grew = True
    return said - {"SaidByYulon"}


def _label_constants(module: _Module) -> Iterator[Line]:
    for name, value in module.constants.items():
        if not _CAPITALS.match(name):
            continue
        values: list[ast.expr | None] = [value]
        if isinstance(value, ast.Dict):
            values = list(value.values)
        elif isinstance(value, ast.Tuple | ast.List):
            values = list(value.elts)
        if (module.rel, name) in NOT_SHOWN:
            continue
        for item in values:
            for text in module.texts(item):
                yield Line(module.rel, name, text)


def _widget_texts(module: _Module) -> Iterator[Line]:
    for node in ast.walk(module.tree):
        if not isinstance(node, ast.Call):
            continue
        name = _name_of(node.func)
        if name in WIDGET_CALLS or (
            name in DIALOG_CALLS
            and isinstance(node.func, ast.Attribute)
            and _name_of(node.func.value) == "QMessageBox"
        ):
            for arg in node.args:
                for text in module.texts(arg):
                    yield Line(module.rel, f"{name}() at line {node.lineno}", text)


def _said_messages(module: _Module, said: set[str]) -> Iterator[Line]:
    for node in ast.walk(module.tree):
        if not isinstance(node, ast.Call) or _name_of(node.func) not in said:
            continue
        shown = [*node.args, *(kw.value for kw in node.keywords if kw.arg != "detail")]
        for arg in shown:
            for text in module.texts(arg):
                yield Line(module.rel, f"{_name_of(node.func)}() at line {node.lineno}", text)


def _shown_by_the_ui(modules: list[_Module]) -> set[str]:
    """Every name a UI module reaches into another module for: `docker_advice.X`, `import X`."""
    names: set[str] = set()
    for module in modules:
        if not module.rel.startswith("yulon/ui/"):
            continue
        for node in ast.walk(module.tree):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.ImportFrom):
                names.update(alias.name for alias in node.names)
    return names


def player_lines() -> list[Line]:
    """Every player-facing text this test reads, from the three places the docstring names."""
    modules = _modules()
    said = _said_types(modules)
    shown = _shown_by_the_ui(modules)
    labels = {path.resolve() for path in LABEL_MODULES}
    found: list[Line] = []
    for module in modules:
        if module.rel in NOT_LABELS:
            continue
        constants = list(_label_constants(module))
        if module.path.resolve() in labels:
            found += constants
        else:
            found += [line for line in constants if line.what in shown]
        if module.rel.startswith("yulon/ui/"):
            found += _widget_texts(module)
        found += _said_messages(module, said)
    return found


def command_lines(lines: list[Line]) -> list[str]:
    """Each line that names a command and is not a named exception."""
    return [
        f"{line.where} {line.what} {command_faults(line.text)}: {line.text!r}"
        for line in lines
        if command_faults(line.text) and (line.where, line.what) not in EXCEPTIONS
    ]


def test_the_reader_finds_each_kind_of_player_line() -> None:
    """The three sources are read: a known constant, a widget text and a refusal each turn up."""
    lines = player_lines()
    whats = {(line.where, line.what.split(" at line ")[0]) for line in lines}

    assert ("yulon/ui/controller_view.py", "REPAIR_IDLE") in whats
    assert ("yulon/docker_advice.py", "_DESKTOP_NOT_RUNNING") in whats
    assert ("yulon/ui/controller_view.py", "_say_under_the_presses()") in whats
    assert ("yulon/docker.py", "DockerRefusal()") in whats
    assert ("yulon/apply.py", "ApplyRefusal()") in whats


def test_every_named_exception_is_still_a_line_that_names_its_command() -> None:
    """An exception whose text no longer names a command is a stale permission: drop it."""
    named = {(line.where, line.what) for line in player_lines() if command_faults(line.text)}

    assert set(EXCEPTIONS) <= named, set(EXCEPTIONS) - named


def test_what_is_kept_out_as_never_shown_is_named_by_no_code_in_the_ui() -> None:
    """A `NOT_SHOWN` constant that some code starts drawing is a label again: drop it."""
    for rel, name in NOT_SHOWN:
        tree = ast.parse((PYLAUNCHER / rel).read_text(encoding="utf-8"))
        used = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load)
        ]
        assert used == [], f"{rel} reads {name} at lines {used}"


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("`docker compose logs ac-db-import` has the rest.", "backtick"),
        ("Run docker compose logs ac-db-import to see why.", "docker command"),
        ("Check with docker ps -a.", "docker command"),
        ("compose up did not finish.", "compose command"),
        ("The run used --rm, so nothing is left.", "--rm"),
        ("Run sudo systemctl start docker.", "systemctl"),
        ("Read journalctl -u docker.", "journalctl"),
    ],
)
def test_each_kind_of_command_is_caught(text: str, rule: str) -> None:
    assert rule in command_faults(text)


@pytest.mark.parametrize(
    "text",
    [
        "Your account joins the docker group.",
        "docker-compose.yml differs from what this version of Yu'lon writes.",
        "Its tab will run docker commands against containers.",
        "The database import did not finish. Its last lines are under Details.",
    ],
)
def test_words_about_docker_are_not_commands(text: str) -> None:
    assert command_faults(text) == []


def test_no_player_line_names_a_command() -> None:
    """A17 (T248): words on the line, and a command, when it helps, under Details."""
    found = command_lines(player_lines())

    assert found == [], "\n".join(found)
