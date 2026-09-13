"""Finds every place `yulon/` writes, and reads the ledger that must list them.

Test support, not shipped code — it lives beside `support_compose.py` and
`support_native.py` for the same reason: it is machinery a test needs and
nothing in the app should be able to import.

**What counts as a write, and why the list is shaped like this.** Three of the
call spellings here were chosen against a specific way of being wrong:

* `.open(...)` is checked by MODE, because `Path.open("wb")` is how the log
  snapshot writes and a walk that only knew the builtin `open()` would miss it.
* `os.replace` is matched only when it is qualified by `os`, because bare
  `.replace(` in this package is `str.replace` four times out of five and a
  ledger full of string operations is a ledger nobody reads.
* `run_statement`/`run_file` are the SQL seam's write half. `query()` is
  deliberately absent: it is the read half, and `dbreads` is allowed to call it.
* `write_marker` is the fourth SQL write and the narrowest widening this list
  has had (T19). The install engine does not reach a database through the
  `sql` seam at all: `sqlplan` execs the client with the SQL on its stdin, the
  third shape the 2026-09-08 audit found this walk blind to, and the ledger has
  said so in prose since T14 rather than in a row. Teaching the walk that whole
  seam is a bigger change than one ticket -- `apply()` streams whatever a plan
  names -- but the marker row is one function with one spelling, and T19 put a
  BUTTON on it: a press that writes a completion marker on a person's word, and
  a ledger that says "every place" while missing it would be missing the one
  write a user can now ask for by name. So the function is named, exactly as
  `docker volume rm` is named by its argv, and the rest of the seam stays
  recorded in the page's prose as open.

`os.open` counts without its flags being inspected. It is how a file gets a
private mode at creation time, and over-inclusive is the safe direction for a
ledger: a read that gets a row costs a line, while a write that gets none costs
the whole guarantee. It was missing until 2026-09-07, and what found it was this
test's OTHER direction — a hand-written row that resolved to no site.

`mkdir` is deliberately NOT a write here. Creating an empty directory puts no
content anywhere, there are twenty-odd of them, and content still needs one of
the calls above — so nothing can hide behind one. Named rather than omitted.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

_PATH_METHODS = {
    "write_text",
    "write_bytes",
    "unlink",
    "touch",
    "rmdir",
    "symlink_to",
    "chmod",
}
_SQL_WRITE_METHODS = {"run_statement", "run_file", "write_marker"}
_QUALIFIED = {
    ("os", "open"),
    ("os", "write"),
    ("os", "replace"),
    ("os", "rename"),
    ("os", "remove"),
    ("os", "unlink"),
    ("os", "rmdir"),
    ("os", "chmod"),
    ("os", "truncate"),
    ("shutil", "rmtree"),
    # T49: the delete moved behind `yulon.rmtree.remove_tree()` so the read-only
    # retry could be shared. Listed here so the five callers stay VISIBLE in the
    # ledger -- an indirection that hid the most destructive call in the package
    # from its own audit would be the failure `guards-that-prove-declarations`
    # is about, not a refactor.
    ("rmtree", "remove_tree"),
    ("shutil", "copy"),
    ("shutil", "copy2"),
    ("shutil", "copyfile"),
    ("shutil", "copytree"),
    ("shutil", "move"),
    ("shutil", "unpack_archive"),
}
_WRITING_MODE = set("wax+")

_OPENING_MODULES = {"gzip", "bz2", "lzma", "io", "codecs", "tarfile", "zipfile", "tokenize"}
"""Modules whose `open()` takes the PATH first and the mode second.

`part.open("wb")` and `gzip.open(part, "wb")` are both attribute calls, so being
one says nothing about where the mode sits — and reading `gzip.open`'s first
argument as a mode reported `sqlplan.py`'s dump READ as a write the moment
`_mode_of()` learned to answer "unknown". The receiver is what separates them.
"""

_UNKNOWN_MODE = "?"
"""What `_mode_of()` answers for a mode it could not read off the syntax tree.

`part.open("ab" if resumed else "wb")` is a real call (`platform.py`, the
resumable download). The mode is a conditional rather than a constant, so there
is nothing to inspect — and an unknown mode has to count as a write, because the
one answer a walker may never guess is the one that removes a row. Spelt `?`
rather than resolved to a branch: the walker does not know which arm runs, and a
ledger that prints a mode nobody measured is a ledger making things up.
"""

_DESTRUCTIVE_DOCKER_ARGV: tuple[tuple[str, ...], ...] = (
    ("volume", "rm"),
    ("volume", "prune"),
    ("image", "rm"),
    ("image", "prune"),
    ("rmi",),
    ("rm",),
    ("compose", "down"),
    ("compose", "rm"),
    ("container", "prune"),
    ("system", "prune"),
    ("builder", "prune"),
)
"""Docker argv prefixes that DELETE durable state, longest match first below.

A volume holds every character on an install. `docker volume rm` destroys one in
a single command with no undo and nothing on the filesystem to recover from, and
it is the most destructive thing Phase 8 added — yet it was invisible to this
walk until 2026-09-08, because the destruction is not a Python call at all: it
is an argv handed to a subprocess, and this module's whole vocabulary was
`shutil` and `Path`.

Matched on the ARGV and not on the function that runs it. A rule keyed to
`_docker(` is a rule a rename walks past, and this project has already paid for
that once (`audit-by-argv-not-by-string`): the same action spelt as a Python
list is invisible to a guard that knows one shell spelling of it.

**What is deliberately NOT here, named rather than omitted, the way `mkdir` is:**
the verbs that only MAKE things — `run`, `build`, `pull`, `up`, `create`,
`image tag`. Each changes the machine and none can lose anything the user had,
and the ledger's own vocabulary is what can be lost. Including them would add a
dozen rows about creation and bury the four that matter.

**And what this still cannot see:** an argv assembled rather than written out —
`argv = ["volume"]` then `argv.append("rm")`. Nothing in the package does that
today; a walk that tried to follow it would be an interpreter.
"""


@dataclass(frozen=True)
class WriteSite:
    """One call that can change something outside this process."""

    module: str
    function: str
    callee: str
    line: int

    @property
    def key(self) -> str:
        """`module::function::callee` — the ledger's key.

        Not the line number, on purpose: a ledger keyed to lines is rewritten by
        every edit above it, and a table that churns is a table people stop
        reading. Two writes of the same kind in one function are one row.
        """
        return f"{self.module}::{self.function}::{self.callee}"


@dataclass(frozen=True)
class LedgerRow:
    site: str
    writes: str
    running: str


def _opens_for_writing(mode: str) -> bool:
    """Whether an `open()` mode can put bytes anywhere. An unreadable mode counts."""
    return mode == _UNKNOWN_MODE or bool(set(mode) & _WRITING_MODE)


def _argv_prefix(node: ast.expr) -> list[str]:
    """The leading string constants of an argv expression, stopping at the first that is not.

    `["volume", "rm", name]` gives `["volume", "rm"]`, which is the whole verb.
    A concatenation is followed into its left operand, because that is where a
    verb lives when the tail is computed: `["run", "--rm", *flags] + [image]`.
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _argv_prefix(node.left)
    if not isinstance(node, ast.List | ast.Tuple):
        return []
    lead: list[str] = []
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            lead.append(element.value)
        else:
            break
    return lead


def _destructive_docker(node: ast.Call) -> str | None:
    """`docker <verb>` when one of this call's arguments is a destroying argv.

    Longest match first, so `volume rm` is not reported as the bare container
    `rm`. Read off the argv wherever it appears in the call, rather than off the
    name of the function being called: see `_DESTRUCTIVE_DOCKER_ARGV`.
    """
    for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
        lead = _argv_prefix(argument)
        if not lead:
            continue
        for verb in sorted(_DESTRUCTIVE_DOCKER_ARGV, key=len, reverse=True):
            if tuple(lead[: len(verb)]) == verb:
                return "docker " + " ".join(verb)
    return None


def _mode_of(node: ast.Call) -> str:
    """The mode an `open()` call was given: the string, `""` for none, `"?"` for unreadable.

    Three answers and not two. `""` means the call named no mode at all, which is
    a read (`open(path)` and `path.open(encoding=...)` both default to `"r"`);
    `_UNKNOWN_MODE` means a mode was named and this walk cannot read it, which
    must count as a write. Collapsing those two is how `platform.py`'s resumable
    download was invisible until 2026-09-08.
    """
    for keyword in node.keywords:
        if keyword.arg == "mode":
            if isinstance(keyword.value, ast.Constant):
                return str(keyword.value.value)
            return _UNKNOWN_MODE
    # `path.open("w")` carries the mode FIRST, because the path is the receiver;
    # `open(path, "w")` and `gzip.open(path, "w")` carry it second, because it is
    # not. Being an attribute call does not settle that — `gzip.open` is one and
    # takes its path as an argument — so the receiver decides: a bare name that
    # is one of the modules below is a module, and anything else is a path.
    receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
    on_a_path = receiver is not None and not (
        isinstance(receiver, ast.Name) and receiver.id in _OPENING_MODULES
    )
    position = 0 if on_a_path else 1
    if len(node.args) > position:
        given = node.args[position]
        return str(given.value) if isinstance(given, ast.Constant) else _UNKNOWN_MODE
    return ""


def _callee(node: ast.Call) -> str | None:
    """What this call writes with, or `None` if it writes nothing."""
    # The argv check comes first and is deliberately independent of the callee:
    # what destroys a volume is the argument, not the function it is handed to.
    destructive = _destructive_docker(node)
    if destructive is not None:
        return destructive
    func = node.func
    if isinstance(func, ast.Attribute):
        owner = func.value.id if isinstance(func.value, ast.Name) else None
        if (owner, func.attr) in _QUALIFIED:
            return f"{owner}.{func.attr}"
        if func.attr in _PATH_METHODS or func.attr in _SQL_WRITE_METHODS:
            return func.attr
        if func.attr == "open":
            mode = _mode_of(node)
            return f"open({mode})" if _opens_for_writing(mode) else None
        if func.attr == "replace" and len(node.args) == 1 and not node.keywords:
            # `tmp.replace(target)` is `Path.replace` — an atomic rename over
            # whatever was there, and how `state.py` and `manifest_store.py`
            # save. Arity is what separates it from the two calls it is spelt
            # like: `str.replace` needs two arguments and
            # `datetime.replace(tzinfo=…)` passes none positionally. The first
            # version of this walker excluded every bare `.replace(` to keep
            # string operations out of the ledger, and silently lost seven real
            # writes with them.
            return "replace"
        return None
    if isinstance(func, ast.Name) and func.id == "open":
        mode = _mode_of(node)
        return f"open({mode})" if _opens_for_writing(mode) else None
    return None


def walk_tree(tree: ast.AST, *, module: str) -> list[WriteSite]:
    """Every write in one parsed module, attributed to the function it sits in."""
    holder: dict[int, str] = {}
    for parent in ast.walk(tree):
        if isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef):
            for line in range(parent.lineno, (parent.end_lineno or parent.lineno) + 1):
                # The innermost function wins: a walk that took the outermost
                # would collapse two writes in one module onto one row.
                holder[line] = parent.name
    found: list[WriteSite] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _callee(node)
        if callee is None:
            continue
        found.append(WriteSite(module, holder.get(node.lineno, "<module>"), callee, node.lineno))
    return found


def find_write_sites(package: Path) -> list[WriteSite]:
    """Every write in every module under `package`, sorted by key."""
    found: list[WriteSite] = []
    for path in sorted(package.rglob("*.py")):
        module = path.relative_to(package).as_posix()
        found += walk_tree(ast.parse(path.read_text(encoding="utf-8")), module=module)
    return sorted(found, key=lambda site: (site.key, site.line))


def parse_ledger(path: Path) -> list[LedgerRow]:
    """Read the pipe table out of `write-ledger.md`.

    Only rows whose first cell is a backticked `module::function::callee` are
    taken, so the page can carry as much prose and as many other tables as it
    needs without either of them becoming load-bearing by accident.
    """
    rows: list[LedgerRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        site = cells[0].strip("`")
        if "::" not in site:
            continue
        rows.append(LedgerRow(site=site, writes=cells[1], running=cells[2]))
    return rows
