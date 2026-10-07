"""No command on a player's line (T248): the line is words, a command goes under Details.

T194 took developer notes off the screen and T214 split each failure into
Yu'lon's sentence on the line and the program's words under Details. Repair's
"did not finish" sentences still ended "`docker compose logs ac-db-import` in
<folder> has the rest", a command in backticks on the line. This reads the
source, not a screen, because most of these sentences appear only when
something has gone wrong, which no screen sweep reaches:

* every label constant in the UI modules and the Docker banner's texts, and
  every capital-named constant a UI module reaches into another module for: a
  module-level name in capitals (`REPAIR_IDLE`, `_DESKTOP_NOT_RUNNING`) holding
  a string, or a dict or tuple of strings;
* every string the UI hands straight to a widget or a dialog: `setText`,
  `setToolTip`, `_say_under_the_presses`, `QLabel(...)`, `QMessageBox.question`;
* every message given to a type whose text is shown as written: one marked
  `SaidByYulon`, `InstallerError` (the Install failed dialog), `ConsoleError`
  (the Console tab's note) -- including text such a type builds in its own
  `__init__` -- every `ProvisionReport.manual_steps` line and preflight
  `Check`, and every line a job yields to its log panel. `detail=` is not
  read: that is what goes under Details.

A message assembled out of variables is read as far as the source spells it in
the same module: an f-string's fixed words and the module constants it names, a
`+` or `%` of strings, `" ".join(...)` of literals or of a list built up with
`append`, a module constant, a local, a helper's returned strings, and a
parameter, through the calls that pass it.

The owner's rule (T296, 2026-10-05): a command the PLAYER has to type, because
no press can do it for them (`sudo systemctl start docker`, `wsl --install`,
`passwd`), stays on the line as a named exception, on a line of its own so it
is easy to copy and never in backticks. Every other command goes under Details.
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
    YULON / "networking.py",
)
"""Where player-facing label constants live. networking.py is here because its warnings
and refusals are constants the Networking tab shows through `NetworkPlan.warnings`
(T248 live check: `UFW_ENABLE_WITHHELD` reached the Warnings line unread)."""

SHOWN_LISTS = frozenset({"warnings", "refusals", "notes", "manual_steps"})
"""Lists whose items a tab shows line by line: `warnings.append(UFW_ENABLE_WITHHELD)`."""

NOT_LABELS: frozenset[str] = frozenset({"yulon/ui/theme.py"})
"""Modules under `yulon/ui` whose capital-named strings are not words: Qt style sheets."""

SHOWN_AS_WRITTEN_ROOTS = frozenset({"SaidByYulon", "InstallerError", "ConsoleError"})
"""Exception types whose message a player reads as written; their subclasses too."""

_TYPED = "a command the player has to type, because no press can do it for them (owner, T296)"

_WSL_RESTART = frozenset({"wsl --shutdown"})
_DOCKER_SERVICE = frozenset(
    {"sudo service docker start", "sudo systemctl start docker", "sudo usermod -aG docker $USER"}
)
_COMPOSE = frozenset(
    {
        "docker compose version",
        "sudo apt install docker-compose-v2",
        "sudo pacman -S docker-compose",
    }
)
_LIST_ZONES = frozenset(
    {
        "sudo firewall-cmd --permanent --list-all-zones",
        "sudo firewall-offline-cmd --list-all-zones",
    }
)
_TURN_ON = frozenset(
    {
        "sudo systemctl enable --now firewalld",
        "sudo systemctl enable --now firewalld && sudo firewall-cmd --reload",
    }
)
_SSH_PORT = frozenset(
    {
        "sudo firewall-cmd --permanent --zone=<zone> --add-port=<your ssh port>/tcp",
        "sudo firewall-offline-cmd --zone=<zone> --add-port=<your ssh port>/tcp",
    }
)
_ANY_PORT = frozenset(
    {
        "sudo firewall-cmd --permanent --zone=<zone> --add-port=<port>/tcp",
        "sudo firewall-offline-cmd --zone=<zone> --add-port=<port>/tcp",
    }
)
_FIREWALLD = {
    "firewalld_start_withheld": _LIST_ZONES | _TURN_ON | _SSH_PORT,
    "_zone_refusal": _LIST_ZONES | _TURN_ON | _ANY_PORT | {"sudo firewall-cmd --reload"},
    "_ssh_refusal": _LIST_ZONES
    | _TURN_ON
    | _SSH_PORT
    | {"sudo firewall-cmd --reload", "sudo ufw allow <your ssh port>/tcp", "sudo ufw enable"},
}
_CONSOLE = frozenset({"docker attach --sig-proxy=false {container}"})

EXCEPTIONS: dict[tuple[str, str], tuple[str, frozenset[str]]] = {
    **{
        ("yulon/docker_advice.py", name): (
            f"the Linux, Deck and WSL 'start the engine' banner (T194): {_TYPED}",
            frozenset({command}),
        )
        for name, command in (
            ("_ENGINE_NOT_RUNNING", "sudo systemctl start docker"),
            ("_DECK_NOT_RUNNING", "sudo systemctl start docker"),
            ("_ENGINE_NOT_ANSWERING", "sudo systemctl restart docker"),
            ("_DECK_NOT_ANSWERING", "sudo systemctl restart docker"),
            ("_WSL_NOT_RUNNING", "sudo systemctl start docker"),
            ("_WSL_UNNAMED", "sudo systemctl start docker"),
        )
    },
    ("yulon/controller_wow_wotlk/console.py", "NO_TTY_HELP"): (
        f"the Console tab's note where Yu'lon cannot open a terminal: {_TYPED}",
        _CONSOLE,
    ),
    ("yulon/controller_wow_wotlk/console.py", "send_command"): (
        f"NO_TTY_HELP, raised where a console command is sent: {_TYPED}",
        _CONSOLE,
    ),
    ("yulon/controller_wow_wotlk/console.py", "_send_inside_distro"): (
        f"NO_SCRIPT_HELP: installing util-linux in the distro, and the console by hand: {_TYPED}",
        frozenset(
            {
                "sudo apt install bsdutils",
                "sudo pacman -S util-linux",
                "wsl -d {distro} -- docker attach --sig-proxy=false {container}",
            }
        ),
    ),
    **{
        ("yulon/catalog/preflight.py", owner): (
            f"starting Docker, or joining its group: {_TYPED}",
            _DOCKER_SERVICE,
        )
        for owner in ("_docker_check", "_daemon_remedy")
    },
    **{
        ("yulon/catalog/preflight.py", owner): (
            f"installing the Compose plugin: {_TYPED}",
            _COMPOSE,
        )
        for owner in ("_compose_check", "_compose_remedy")
    },
    **{
        ("yulon/catalog/preflight.py", owner): (
            f"restarting WSL after resizing it: {_TYPED}",
            _WSL_RESTART,
        )
        for owner in ("_ram_check", "_cpu_check", "_memory_floor_remedy", "_jobs_remedy")
    },
    ("yulon/catalog/preflight.py", "_selinux_check"): (
        f"relabelling the server folder for SELinux: {_TYPED}",
        frozenset({"chcon -Rt container_file_t <server folder>"}),
    ),
    ("yulon/catalog/native.py", "_check_run"): (
        f"BUILDER_LOST: reading Docker's memory and restarting WSL: {_TYPED}",
        frozenset({"docker info", "wsl --shutdown"}),
    ),
    ("yulon/catalog/native.py", "build_stalled_notice"): (
        f"a stalled build's notice on the build log: checking Docker answers: {_TYPED}",
        frozenset({"docker info"}),
    ),
    ("yulon/catalog/native.py", "stage_generate_compose"): (
        f"relabelling the server folder for SELinux when Yu'lon could not: {_TYPED}",
        frozenset({"chcon -Rt container_file_t {…}"}),
    ),
    **{
        ("yulon/catalog/families/cmangos.py", owner): (
            f"deleting a database volume whose password is lost: {_TYPED}",
            frozenset({"docker volume rm {…}_db-data"}),
        )
        for owner in ("_db_password", "_password_origin_note")
    },
    ("yulon/catalog/families/cmangos.py", "_refuse_to_patch_what_will_not_be_rebuilt"): (
        f"removing the images so the next install compiles again: {_TYPED}",
        frozenset({"docker image rm"}),
    ),
    ("yulon/git.py", "_streamed_capture"): (
        f"removing a clone's container that Stop could not remove: {_TYPED}",
        frozenset({"docker rm -f {…}"}),
    ),
    **{
        # The line is built in `_claimed_note()` and raised in `reextract()`; the walk
        # meets it in both.
        ("yulon/catalog/families/trinitycore.py", owner): (
            f"removing a folder reservation an earlier run of this Yu'lon left (T543): {_TYPED}",
            frozenset({"docker rm -f {…}"}),
        )
        for owner in ("_claimed_note", "reextract")
    },
    ("yulon/catalog/families/trinitycore.py", "_refuse_a_tool_still_writing"): (
        f"removing, on Linux, a map tool's container an earlier press left running: {_TYPED}",
        frozenset({"docker rm -f {…}"}),
    ),
    **{
        ("yulon/networking.py", owner): (
            f"allowing SSH in firewalld or ufw before turning the firewall on: {_TYPED}",
            frozenset(commands),
        )
        for owner, commands in _FIREWALLD.items()
    },
    ("yulon/networking.py", "UFW_ENABLE_WITHHELD"): (
        f"allowing SSH in ufw before turning it on: {_TYPED}",
        frozenset({"sudo ufw allow <your ssh port>/tcp", "sudo ufw enable"}),
    ),
    ("yulon/networking.py", "_guard_the_way_back_in"): (
        f"UFW_ENABLE_WITHHELD and firewalld_start_withheld, on the Warnings line: {_TYPED}",
        frozenset(
            _FIREWALLD["firewalld_start_withheld"]
            | {"sudo ufw allow <your ssh port>/tcp", "sudo ufw enable"}
        ),
    ),
    ("yulon/networking.py", "plan"): (
        f"settling firewalld's zones by hand, on the Warnings line: {_TYPED}",
        frozenset(
            {
                "sudo firewall-cmd --permanent --zone=<zone> --change-interface=<interface>",
                "sudo firewall-cmd --set-default-zone=<zone>",
                "sudo firewall-offline-cmd --list-all-zones",
                "sudo firewall-cmd --permanent --get-zone-of-interface=<interface>",
                "sudo firewall-cmd --permanent --zone=<zone> --add-port=<port>/tcp",
            }
        ),
    ),
    **{
        ("yulon/networking.py", name): (
            f"taking a game port back out of a zone it was written to: {_TYPED}",
            frozenset(commands),
        )
        for name, commands in (
            (
                "_TAKE_BACK",
                (
                    "sudo firewall-cmd --permanent --zone=<zone> --remove-port=<port>/tcp",
                    "sudo firewall-cmd --reload",
                ),
            ),
            (
                "_TAKE_BACK_OFFLINE",
                ("sudo firewall-offline-cmd --zone=<zone> --remove-port=<port>/tcp",),
            ),
        )
    },
    **{
        ("yulon/catalog/native.py", owner): (
            f"putting a source folder back on the commit its build came from (T217): {_TYPED}",
            frozenset({"git -C {…} checkout --detach --force {…}"}),
        )
        for owner in ("sources_off_refusal", "_refuse_sources_off_their_build")
    },
    ("yulon/controller_wow_wotlk/maintenance.py", "plan_restore"): (
        f"unpacking a compressed backup so it can be restored: {_TYPED}",
        frozenset({"gunzip -k {…}"}),
    ),
    ("yulon/networking.py", "_zone_breadth_note"): (
        f"taking a game port back out of a zone it was written to: {_TYPED}",
        frozenset(
            {
                "sudo firewall-cmd --permanent --zone=<zone> --remove-port=<port>/tcp",
                "sudo firewall-cmd --reload",
                "sudo firewall-offline-cmd --zone=<zone> --remove-port=<port>/tcp",
            }
        ),
    ),
    ("yulon/networking.py", "_default_zone_refusal"): (
        f"allowing the ports in the default zone the file names: {_TYPED}",
        frozenset(
            {
                "sudo firewall-cmd --permanent --zone=<that zone> --add-port=<port>/tcp",
                "sudo firewall-cmd --reload",
                "sudo firewall-offline-cmd --get-default-zone",
            }
        ),
    ),
    ("yulon/platform.py", "ensure_wsl2"): (
        f"installing WSL from an Administrator PowerShell: {_TYPED}",
        frozenset({"wsl --install --no-distribution"}),
    ),
    ("yulon/platform.py", "_repair_docker_after_steamos_update"): (
        f"setting a sudo password on a Deck by hand: {_TYPED}",
        frozenset({"passwd"}),
    ),
    ("yulon/platform.py", "docker_setup_remedy"): (
        f"setting up pacman's keyring on SteamOS: {_TYPED}",
        frozenset(
            {
                "sudo steamos-readonly disable",
                "sudo pacman-key --init",
                "sudo pacman-key --populate archlinux holo",
            }
        ),
    ),
}
"""(module, function or constant) -> why it may name a command, and the exact command lines.

Each command stands on a line of its own, in no backticks, and is one of the
lines listed: a new command in the same function is not excepted
(`test_a_named_exception_names_only_its_own_commands_on_their_own_lines`).
"""


def owner_of(line: Line) -> tuple[str, str]:
    """The exception key a line belongs to: its module and its function or constant."""
    what = line.what
    for marker in ("() in ", "yield in ", *(f"{name} in " for name in sorted(SHOWN_LISTS))):
        if marker in what:
            return line.where, what.split(marker, 1)[1]
    return line.where, what.removesuffix("()")


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
_PERCENT = re.compile(r"%(?:\([^)]*\))?[-#0 +]*\d*(?:\.\d+)?[sdrifxa]")
_DEPTH = 6


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


_Function = ast.FunctionDef | ast.AsyncFunctionDef


class _Module:
    """One parsed source file, with what its texts can be resolved through."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rel = _rel(path)
        self.tree = ast.parse(path.read_text(encoding="utf-8"))
        self.constants: dict[str, ast.expr] = {}
        self.functions: dict[str, list[_Function]] = {}
        self.parents: dict[int, ast.AST] = {}
        self.calls: dict[str, list[ast.Call]] = {}
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
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                self.functions.setdefault(node.name, []).append(node)
            if isinstance(node, ast.Call):
                self.calls.setdefault(_name_of(node.func), []).append(node)

    def enclosing(self, node: ast.AST) -> _Function | None:
        parent = self.parents.get(id(node))
        while parent is not None and not isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef):
            parent = self.parents.get(id(parent))
        return parent

    def _class_of(self, function: _Function) -> ast.ClassDef | None:
        parent = self.parents.get(id(function))
        return parent if isinstance(parent, ast.ClassDef) else None

    def _logged(self, node: ast.AST) -> bool:
        """Whether `node` is not shown on a line: written to the app log, handed to `re`,
        or passed as `detail=` (what goes under Details)."""
        child: ast.AST = node
        parent = self.parents.get(id(node))
        while parent is not None and not isinstance(parent, ast.stmt):
            if isinstance(parent, ast.Call) and _LOGGED.search(ast.unparse(parent.func)):
                return True
            if isinstance(parent, ast.Call) and ast.unparse(parent.func).startswith("re."):
                return True
            if isinstance(parent, ast.keyword) and parent.arg == "detail":
                return True
            child, parent = parent, self.parents.get(id(parent))
        if isinstance(parent, ast.Assign) and child is parent.value:
            # `rules = …` built for a `detail=`, or a pattern for `re`: judged where used.
            names = [t.id for t in parent.targets if isinstance(t, ast.Name)]
            return any(name.endswith(("_PATTERN", "_RE")) for name in names)
        return False

    def _sentences(self, function: _Function, depth: int) -> list[str]:
        """Every string a helper builds, docstring and log lines aside: what it can return."""
        found: list[str] = []
        body = function.body[1:] if ast.get_docstring(function) is not None else function.body
        for statement in body:
            for inner in ast.walk(statement):
                if (
                    isinstance(inner, ast.Name)
                    and isinstance(inner.ctx, ast.Load)
                    and inner.id in self.constants
                    and _CAPITALS.match(inner.id)
                    and not self._logged(inner)
                ):
                    found += self.texts(self.constants[inner.id], depth + 1)
                    continue
                if (
                    isinstance(inner, ast.Call)
                    and _name_of(inner.func) in self.functions
                    and _name_of(inner.func) != function.name
                    and depth < _DEPTH - 2
                    and not self._logged(inner)
                ):
                    # A helper whose words this one hands on (`parts.extend(_take_back(…))`).
                    for helper in self.functions[_name_of(inner.func)]:
                        found += self._sentences(helper, depth + 2)
                    continue
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

    def _passed(self, function: _Function, name: str, depth: int) -> list[str]:
        """What the calls in this module pass for `function`'s parameter `name`."""
        params = [arg.arg for arg in (*function.args.posonlyargs, *function.args.args)]
        keyword_only = [arg.arg for arg in function.args.kwonlyargs]
        if name not in params and name not in keyword_only:
            return []
        owner = self._class_of(function)
        callee = owner.name if owner is not None and function.name == "__init__" else function.name
        bound = owner is not None and params[:1] in (["self"], ["cls"])
        index = params.index(name) - (1 if bound else 0) if name in params else None
        found: list[str] = []
        for call in self.calls.get(callee, []):
            given: ast.expr | None = None
            if index is not None and 0 <= index < len(call.args):
                given = call.args[index]
            for keyword in call.keywords:
                if keyword.arg == name:
                    given = keyword.value
            if given is not None:
                found += self.texts(given, depth + 1)
        return found

    def _elements(self, node: ast.expr, depth: int) -> list[list[str]]:
        """The texts of each element of a list `node` is or names: a literal, or one appended to."""
        if isinstance(node, ast.List | ast.Tuple | ast.Set):
            return [self.texts(item, depth + 1) for item in node.elts]
        if isinstance(node, ast.GeneratorExp | ast.ListComp):
            return [self.texts(node.elt, depth + 1)]
        if isinstance(node, ast.Name):
            function = self.enclosing(node)
            if function is None:
                value = self.constants.get(node.id)
                return self._elements(value, depth + 1) if value is not None else []
            found: list[list[str]] = []
            for inner in ast.walk(function):
                if isinstance(inner, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == node.id for t in inner.targets
                ):
                    found += self._elements(inner.value, depth + 1)
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr in ("append", "extend")
                    and isinstance(inner.func.value, ast.Name)
                    and inner.func.value.id == node.id
                    and inner.args
                ):
                    if inner.func.attr == "append":
                        found.append(self.texts(inner.args[0], depth + 1))
                    else:
                        found += self._elements(inner.args[0], depth + 1)
            return found
        return [self.texts(node, depth + 1)]

    def _placeholder(self, part: ast.FormattedValue, depth: int) -> str:
        """An f-string hole: a module constant's own words, else a hole."""
        value = part.value
        if isinstance(value, ast.Name):
            function = self.enclosing(part)
            local = [
                inner.value
                for inner in (ast.walk(function) if function is not None else ())
                if isinstance(inner, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == value.id for t in inner.targets)
            ]
            source = local[0] if local else self.constants.get(value.id)
            texts = self.texts(source, depth + 1) if source is not None else []
            if texts:
                return texts[0]
        return _HOLE

    def texts(self, node: ast.expr | None, depth: int = 0) -> list[str]:
        """Every string `node` can be, as far as the source spells it."""
        if node is None or depth > _DEPTH:
            return []
        if isinstance(node, ast.Constant):
            return [node.value] if isinstance(node.value, str) else []
        if isinstance(node, ast.JoinedStr):
            return [
                "".join(
                    (
                        part.value
                        if isinstance(part, ast.Constant)
                        else (
                            self._placeholder(part, depth)
                            if isinstance(part, ast.FormattedValue)
                            else _HOLE
                        )
                    )
                    for part in node.values
                )
            ]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self.texts(node.left, depth + 1) or [_HOLE]
            right = self.texts(node.right, depth + 1) or [_HOLE]
            return [a + b for a in left for b in right][:16]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            return [_PERCENT.sub(_HOLE, text) for text in self.texts(node.left, depth + 1)]
        if isinstance(node, ast.IfExp):
            return self.texts(node.body, depth + 1) + self.texts(node.orelse, depth + 1)
        if isinstance(node, ast.BoolOp):
            return [text for value in node.values for text in self.texts(value, depth + 1)]
        if isinstance(node, ast.Name):
            function = self.enclosing(node)
            if function is not None:
                local = [
                    inner.value
                    for inner in ast.walk(function)
                    if isinstance(inner, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == node.id for t in inner.targets)
                ]
                if local:
                    return [text for value in local for text in self.texts(value, depth + 1)]
                passed = self._passed(function, node.id, depth)
                if passed:
                    return passed
            if node.id in self.constants:
                return self.texts(self.constants[node.id], depth + 1)
            return []
        if isinstance(node, ast.Call):
            name = _name_of(node.func)
            if name == "format" and isinstance(node.func, ast.Attribute):
                return self.texts(node.func.value, depth + 1)
            if (
                name == "join"
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Constant)
                and isinstance(node.func.value.value, str)
                and node.args
            ):
                parts = self._elements(node.args[0], depth)
                joined = node.func.value.value.join(texts[0] for texts in parts if texts)
                return [joined, *(text for texts in parts for text in texts)]
            return [
                text
                for helper in self.functions.get(name, [])
                for text in self._sentences(helper, depth)
            ]
        return []


def _modules() -> list[_Module]:
    return [_Module(path) for path in sorted(YULON.rglob("*.py"))]


def _shown_types(modules: list[_Module]) -> set[str]:
    """Every exception class whose text is shown as written, directly or through a base."""
    shown = set(SHOWN_AS_WRITTEN_ROOTS)
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
            if node.name not in shown and any(_name_of(base) in shown for base in node.bases):
                shown.add(node.name)
                grew = True
    return shown - {"SaidByYulon"}


def _site(module: _Module, call: ast.Call, label: str) -> str:
    function = module.enclosing(call)
    return f"{label}() in {function.name if function is not None else '<module>'}"


def _label_constants(module: _Module) -> Iterator[Line]:
    for name, value in module.constants.items():
        if not _CAPITALS.match(name) or (module.rel, name) in NOT_SHOWN:
            continue
        values: list[ast.expr | None] = [value]
        if isinstance(value, ast.Dict):
            values = list(value.values)
        elif isinstance(value, ast.Tuple | ast.List):
            # An argv (`("firewall-cmd", "--state")`) is a command Yu'lon runs, not words.
            values = [
                item
                for item in value.elts
                if not (isinstance(item, ast.Constant) and " " not in str(item.value))
            ]
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
                    yield Line(module.rel, _site(module, node, name), text)


def _shown_messages(module: _Module, shown: set[str]) -> Iterator[Line]:
    """Messages handed to a shown type, and the text such a type builds in its own `__init__`."""
    for node in ast.walk(module.tree):
        if not isinstance(node, ast.Call):
            continue
        name = _name_of(node.func)
        if name in shown:
            given = [*node.args, *(kw.value for kw in node.keywords if kw.arg != "detail")]
            for arg in given:
                for text in module.texts(arg):
                    yield Line(module.rel, _site(module, node, name), text)
        elif name == "Check":
            # A preflight refusal's answer and remedy are the Install failed dialog's words.
            for arg in [*node.args[2:], *(kw.value for kw in node.keywords)]:
                for text in module.texts(arg):
                    yield Line(module.rel, _site(module, node, "Check"), text)
        elif name == "ProvisionReport":
            steps = [kw.value for kw in node.keywords if kw.arg == "manual_steps"]
            for step in steps:
                for texts in module._elements(step, 0):
                    for text in texts:
                        yield Line(module.rel, _site(module, node, "manual_steps"), text)
        elif name == "__init__" and isinstance(node.func, ast.Attribute):
            function = module.enclosing(node)
            owner = None if function is None else module.parents.get(id(function))
            if isinstance(owner, ast.ClassDef) and owner.name in shown and node.args:
                first = node.args[0]
                if isinstance(first, ast.Name) and first.id == "self" and len(node.args) > 1:
                    first = node.args[1]
                for text in module.texts(first):
                    yield Line(module.rel, f"{owner.name}.__init__", text)


PLAYER_TEXT_FUNCTIONS = re.compile(
    r"(?:_notice|_refusal|_remedy|_withheld|_message|_note|_hint|_steps|_help|_sentence)$"
)
"""Functions named for the player text they return: a notice a job yields later, a refusal
the Networking tab shows, a remedy the install dialog quotes (T248 review)."""


def _named_texts(module: _Module) -> Iterator[Line]:
    """Every string a function named for player text builds (`PLAYER_TEXT_FUNCTIONS`)."""
    for name, functions in module.functions.items():
        if not PLAYER_TEXT_FUNCTIONS.search(name):
            continue
        for function in functions:
            for text in module._sentences(function, 0):
                yield Line(module.rel, f"{name}()", text)


def _list_items(module: _Module) -> Iterator[Line]:
    """What is appended to a list a tab shows line by line (`SHOWN_LISTS`), wherever it is built."""
    for node in ast.walk(module.tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("append", "extend")
            and _name_of(node.func.value) in SHOWN_LISTS
            and node.args
        ):
            continue
        function = module.enclosing(node)
        where = f"{_name_of(node.func.value)} in {function.name if function else '<module>'}"
        items = (
            [text for texts in module._elements(node.args[0], 0) for text in texts]
            if node.func.attr == "extend"
            else module.texts(node.args[0])
        )
        for text in items:
            yield Line(module.rel, where, text)


def _log_lines(module: _Module) -> Iterator[Line]:
    """What a job yields: the lines its log panel shows the player as it runs (T296)."""
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Yield) and node.value is not None:
            function = module.enclosing(node)
            where = f"yield in {function.name if function is not None else '<module>'}"
            for text in module.texts(node.value):
                yield Line(module.rel, where, text)


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
    """Every player-facing text this test reads, from the places the docstring names."""
    modules = _modules()
    shown_types = _shown_types(modules)
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
        found += _shown_messages(module, shown_types)
        found += _log_lines(module)
        found += _named_texts(module)
        found += _list_items(module)
    return found


def command_lines(lines: list[Line]) -> list[str]:
    """Each line that names a command and is not a named exception."""
    return sorted(
        {
            f"{line.where} {line.what} {command_faults(line.text)}: {line.text!r}"
            for line in lines
            if command_faults(line.text) and owner_of(line) not in EXCEPTIONS
        }
    )


def typed_command_faults(text: str, allowed: frozenset[str]) -> list[str]:
    """How an excepted text fails the owner's rule (T296, T248).

    A backtick; a command inside a sentence rather than on a line of its own,
    where it is easy to copy; or a command line its exception does not name,
    which is a new command in the same function.
    """
    faults = ["backtick"] if "`" in text else []
    for line in text.split("\n"):
        if not command_faults(line):
            continue
        stripped = line.strip()
        if stripped in allowed:
            continue
        starts = {
            match.start()
            for rule, pattern in _COMMAND_PATTERNS
            if rule != "backtick"
            for match in pattern.finditer(stripped)
        }
        if 0 in starts:
            faults.append(f"command its exception does not name: {stripped!r}")
        else:
            faults.append(f"command inside a sentence: {line!r}")
    return faults


def _patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    from tests.support_player_text import COMMAND_RULES

    return COMMAND_RULES


_COMMAND_PATTERNS = _patterns()


def test_the_reader_finds_each_kind_of_player_line() -> None:
    """Each source is read: constants, widget texts, refusals, install failures, manual steps."""
    lines = player_lines()
    whats = {(line.where, line.what) for line in lines}

    assert ("yulon/ui/controller_view.py", "REPAIR_IDLE") in whats
    assert ("yulon/docker_advice.py", "_DESKTOP_NOT_RUNNING") in whats
    assert ("yulon/ui/controller_view.py", "_say_under_the_presses() in repair_import") in whats
    assert ("yulon/docker.py", "DockerRefusal() in verify_import") in whats
    assert ("yulon/catalog/native.py", "build_stalled_notice()") in whats, "a notice a job yields"
    assert ("yulon/networking.py", "_zone_refusal()") in whats, "a refusal the Networking tab shows"
    assert ("yulon/networking.py", "UFW_ENABLE_WITHHELD") in whats, "a warning constant"
    assert ("yulon/networking.py", "warnings in plan") in whats, "a line the Warnings list shows"
    assert ("yulon/apply.py", "ApplyRefusal() in _require_own_clone") in whats
    assert ("yulon/catalog/native.py", "InstallerError() in stage_ready") in whats
    assert ("yulon/platform.py", "manual_steps() in ensure_wsl2") in whats


@pytest.mark.parametrize(
    ("source", "words"),
    [
        ('X = "run %s now" % "docker ps"', "run {…} now"),
        ('C = "docker ps"\ndef f():\n    raise E(f"run {C} now")', "run docker ps now"),
        ('def f():\n    raise E(" ".join(["one", "docker ps"]))', "one docker ps"),
        (
            "def f():\n    lines = []\n    lines.append('docker ps')\n"
            "    raise E(' '.join(lines))",
            "docker ps",
        ),
        ("def g(why):\n    raise E(why)\ndef f():\n    g('docker ps')", "docker ps"),
        (
            "class E(SaidByYulon):\n    def __init__(self, n):\n"
            "        super().__init__(f'stop {n} with docker stop')\n",
            "stop {…} with docker stop",
        ),
        (
            "def _h():\n    return 'take it back with docker rm x'\n"
            "def _x_note():\n    parts = []\n    parts.extend(_h())\n    return ' '.join(parts)",
            "take it back with docker rm x",
        ),
        (
            "def build_x_notice():\n    return f'run docker info {_X}'\n_X = 'now'",
            "run docker info now",
        ),
    ],
)
def test_the_reader_follows_each_shape_a_message_is_built_in(
    tmp_path: Path, source: str, words: str
) -> None:
    """T248 review: `%`, a constant in an f-string, a join, a parameter, a type's own `__init__`,
    a helper a named function hands on, and a notice a job yields later."""
    path = YULON / "_t248_probe.py"
    module = _Module.__new__(_Module)
    module.path = path
    module.rel = "yulon/_t248_probe.py"
    module.tree = ast.parse(
        "class SaidByYulon(Exception): ...\nclass E(SaidByYulon): ...\n" + source
    )
    module.constants, module.functions, module.parents, module.calls = {}, {}, {}, {}
    for node in module.tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    module.constants[target.id] = node.value
    for node in ast.walk(module.tree):
        for child in ast.iter_child_nodes(node):
            module.parents[id(child)] = node
        if isinstance(node, ast.FunctionDef):
            module.functions.setdefault(node.name, []).append(node)
        if isinstance(node, ast.Call):
            module.calls.setdefault(_name_of(node.func), []).append(node)

    texts = (
        {line.text for line in _label_constants(module)}
        | {line.text for line in _shown_messages(module, {"E"})}
        | {line.text for line in _named_texts(module)}
    )

    assert words in texts, texts


def test_every_named_exception_is_still_a_line_that_names_its_command() -> None:
    """An exception, or one of its commands, that no line names any more is stale: drop it."""
    named: dict[tuple[str, str], set[str]] = {}
    for line in player_lines():
        for said in line.text.split("\n"):
            if command_faults(said):
                named.setdefault(owner_of(line), set()).add(said.strip())
    stale = {
        key: sorted(commands - named.get(key, set()))
        for key, (_why, commands) in EXCEPTIONS.items()
        if commands - named.get(key, set())
    }

    assert stale == {}, stale


def test_a_named_exception_names_only_its_own_commands_on_their_own_lines() -> None:
    """The owner's rule (T296): a command to type is on a line of its own, never in backticks,
    and it is one its exception names."""
    faults = sorted(
        {
            f"{line.where} {line.what}: {fault}"
            for line in player_lines()
            if owner_of(line) in EXCEPTIONS
            for fault in typed_command_faults(line.text, EXCEPTIONS[owner_of(line)][1])
        }
    )

    assert faults == [], "\n".join(faults)


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
        ("run: wsl --install --no-distribution, then reboot", "wsl command"),
        ("then sudo pacman -S docker-compose", "sudo"),
        ("sudo pacman -S docker-compose", "package manager"),
        ("It would run git reset --hard over it.", "git command"),
        ("run gunzip -k backup.sql.gz first", "shell command"),
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
        "Choose a password you will remember.",
        "git could not say whether the folder has changes in it.",
    ],
)
def test_words_about_docker_are_not_commands(text: str) -> None:
    assert command_faults(text) == []


@pytest.mark.parametrize(
    ("text", "faults"),
    [
        ("Open a terminal and run this:\nsudo systemctl start docker", []),
        ('Open a terminal and run "sudo systemctl start docker".', ["command inside a sentence"]),
        ("Run this:\n`sudo systemctl start docker`", ["backtick", "command inside a sentence"]),
        (
            "Run this:\nsudo systemctl start docker\nThen this:\nsudo systemctl restart docker",
            ["command its exception does not name"],
        ),
    ],
)
def test_a_typed_command_must_stand_on_its_own_line_and_be_named(
    text: str, faults: list[str]
) -> None:
    found = typed_command_faults(text, frozenset({"sudo systemctl start docker"}))
    assert [fault.split(":")[0] for fault in found] == faults, found


def test_no_player_line_names_a_command() -> None:
    """A17 (T248): words on the line, and a command, when it helps, under Details."""
    found = command_lines(player_lines())

    assert found == [], "\n".join(found)
