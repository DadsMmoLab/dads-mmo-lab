"""Every database seam built under `yulon/` names a client, and names a real one. By AST.

This file exists because the same defect was fixed three times in one day and
was still live afterwards. The sequence is the argument for auditing rather
than enumerating:

* `d157001d` bound the client into `maintenance.mysql_for()` in four packages
  and shipped a test whose docstring said "every package".
* `f8cacafc` found `accounts.sql_for()` -- one module over, four packages,
  unbound -- by CREATING AN ACCOUNT on a live server and reading `client=None`
  off the seam it printed. It widened the test to "discover" builders.
* A review then found `ui/controller_view.py:_mysql_for()`, the seam the Server
  tab's backup and restore buttons actually build, thirty-four lines below a
  sibling that binds it correctly; and `install_wiring.py`, which builds the
  import gate for whichever game is being installed.

Three fixes, each convinced it was general, each naming its subjects. The
per-function tests all stayed green through every one of those misses, because
a test that iterates a hand-written list can only ever prove something about
the names its author already thought of.

**The first version of THIS file made the same class of mistake one level up.**
It asked whether the `client` keyword was PRESENT. The defect it was written
for was a seam printing `client=None` -- a keyword that is present and carries
nothing. A review mutated `install_wiring.py` to

    client = None if (native := entry.install.native) is not None else None

and the whole suite stayed green with both install-path seams falling back to
`mysql`. So this version asks what the keyword is BOUND TO, and rejects an
expression that can only ever be `None`. A call, an attribute or a parameter is
accepted -- their value is a runtime question and other tests answer it -- but
a literal `None`, or a local whose every assignment in the same function is
statically `None`, is the defect itself written out longhand.

**Two ways an audit like this goes quietly blind, both closed here.** It read
only `ast.Name` and `ast.Attribute` in the callee position, so `_Sql = DockerSql`
one line above the call hid the site completely; and its floor of `found >= 8`
against thirteen real sites left five sites' worth of slack for a disappearance
to hide in. Aliases are now resolved, the count is exact, and any OTHER mention
of a seam name -- handed to `functools.partial`, stashed in a dict, returned
from a factory, subclassed -- fails the audit by name rather than passing
silently. An audit that cannot see a site must say so; that is the whole
difference between this file and the list it replaced.

Why it matters: `apply.mysql_client()` asks the container `command -v` and
believes it, so an unbound seam is correct exactly as long as the probe can
run. When it cannot -- no docker CLI, a timeout, an OSError -- it falls back to
its first candidate, and unbound that is `mysql`. `mariadb:11` ships neither
`mysql` nor `mysqldump`, and three of the four games this app installs run
MariaDB.
"""

from __future__ import annotations

import ast
from pathlib import Path

SEAMS = {"DockerSql", "DockerMysql", "MarkerGate"}
"""The three constructors that turn a password into database argv.

`MarkerGate` joined the two `Docker*` classes after a review pointed out it was
absent: it takes `client=` at four sites (three `repair.py` modules and
`families/cmangos.py`), it builds the probe that decides whether an import runs
over somebody's populated server, and the original audit did not look at it.
"""

PACKAGE = Path(__file__).resolve().parent.parent / "yulon"

EXPECTED_SITES = 17
"""Exactly how many seam constructions exist today.

A floor would let a site vanish without a word -- which is the failure this
whole file is about. If you add or remove a seam, this number moves in the same
commit, and the diff says which way.
"""


def _direct_name(node: ast.expr) -> str | None:
    """The name an expression spells, for the two spellings that reach a constructor.

    Both occur in the tree and both must be audited: `DockerSql(...)` from a
    direct import, and `wotlk_maintenance.DockerMysql(...)` through the module.
    Taking only `ast.Name` would silently skip the second, which is the spelling
    of two of the sites this file was written for.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _annotation_nodes(tree: ast.AST) -> set[int]:
    """Every node inside a type annotation, by `id()`.

    `def _sql_for(...) -> DockerSql:` mentions a seam without building one. The
    blindness check below would otherwise report the return annotation of the
    very function it is auditing.
    """
    marked: set[int] = set()
    for node in ast.walk(tree):
        annotations: list[ast.expr | None] = []
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            annotations.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        elif isinstance(node, ast.arg):
            annotations.append(node.annotation)
        for annotation in annotations:
            if annotation is not None:
                marked.update(id(sub) for sub in ast.walk(annotation))
    return marked


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Local rebindings of a seam constructor: `_Sql = DockerSql` -> {"_Sql": "DockerSql"}.

    A review rebound both constructors one line above their call sites, dropped
    `client=` entirely, and the suite stayed green -- the audit was matching the
    callee's spelling rather than what it referred to.
    """
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        seam = _direct_name(node.value)
        if seam not in SEAMS:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[target.id] = seam
    return found


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    return {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _enclosing_function(node: ast.AST, parents: dict[int, ast.AST]) -> ast.AST | None:
    current: ast.AST | None = parents.get(id(node))
    while current is not None:
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            return current
        current = parents.get(id(current))
    return None


def _only_none(node: ast.expr, scope: ast.AST | None, depth: int = 0) -> bool:
    """Can this expression be nothing but `None`, decided statically?

    Deliberately narrow. A `Call`, an `Attribute` or a bare parameter answers
    False -- `client=_db_client(entry)` is legitimate and returns `None` for an
    entry with no `native` block. What it does answer True for is the shape the
    defect actually takes: a literal, or a local variable whose every assignment
    in the same function is itself only-None, which is how a real fallback
    decays when someone edits the condition out of it.
    """
    if isinstance(node, ast.Constant) and node.value is None:
        return True
    if isinstance(node, ast.IfExp):
        return _only_none(node.body, scope, depth) and _only_none(node.orelse, scope, depth)
    if isinstance(node, ast.Name) and scope is not None and depth < 4:
        assigned = [
            value.value
            for value in ast.walk(scope)
            if isinstance(value, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == node.id for t in value.targets)
        ]
        return bool(assigned) and all(_only_none(value, scope, depth + 1) for value in assigned)
    return False


def test_every_database_seam_construction_binds_a_client_to_a_real_value() -> None:
    unbound: list[str] = []
    sites = 0
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        where = path.relative_to(PACKAGE.parent)
        aliases = _aliases(tree)
        parents = _parents(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = _direct_name(node.func)
            seam = called if called in SEAMS else aliases.get(called or "")
            if seam is None:
                continue
            sites += 1
            bound = next((kw for kw in node.keywords if kw.arg == "client"), None)
            if bound is None:
                unbound.append(f"{where}:{node.lineno} {seam}(...) names no client at all")
            elif _only_none(bound.value, _enclosing_function(node, parents)):
                unbound.append(f"{where}:{node.lineno} {seam}(client=...) can only ever be None")
    assert sites == EXPECTED_SITES, (
        f"found {sites} seam constructions under {PACKAGE}, expected {EXPECTED_SITES}. "
        "A site was added or removed: move EXPECTED_SITES in the same commit, so that a "
        "site DISAPPEARING is never mistaken for the audit still passing"
    )
    assert not unbound, (
        "these build a database seam without a usable client, so they fall back to `mysql` "
        "when the container cannot be probed -- which the three MariaDB games do not have:\n  "
        + "\n  ".join(unbound)
    )


def test_no_seam_constructor_is_referred_to_in_a_way_this_audit_cannot_read() -> None:
    """A mention that is not a call and not an alias makes the audit blind; say so.

    `functools.partial(DockerSql, ...)`, `{"sql": DockerSql}[kind](...)`, a
    factory returning the class, a subclass -- each hides a construction site
    from the test above, and each would have passed it in silence. There are
    none today. If one arrives, this fails with its file and line, and whoever
    added it decides whether to call the constructor directly or to teach
    `_aliases()` the new shape. The one thing that cannot happen is the audit
    quietly covering less than its docstring claims.
    """
    blind: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        where = path.relative_to(PACKAGE.parent)
        annotations = _annotation_nodes(tree)
        aliases = _aliases(tree)
        called_here = {
            id(node.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (_direct_name(node.func) in SEAMS or _direct_name(node.func) in aliases)
        }
        alias_values = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and _direct_name(node.value) in SEAMS
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Name | ast.Attribute):
                continue
            if _direct_name(node) not in SEAMS:
                continue
            if id(node) in called_here or id(node) in alias_values or id(node) in annotations:
                continue
            blind.append(f"{where}:{node.lineno} mentions {_direct_name(node)}")
    assert not blind, (
        "a seam constructor is referred to without being called or aliased, so the audit "
        "above cannot see whether that path binds a client:\n  " + "\n  ".join(blind)
    )
