"""Every family branch decides every family (T179 Task 1, Review Focus 5).

The defect this exists for is the shape most family branches had before T179:
`if family == "azerothcore": … elif family == "cmangos": … else: nothing`. A
third family falls into the `else` at every one of them, silently, and nothing
fails -- the feature is just absent. `yulon/catalog/families/decisions.py` is
the registry that says, per site and per family, what that family gets there;
this file holds it to two things:

* **completeness** -- every site names every member of `NativeInstall.family`,
  so adding a family to the Literal turns this red at every site until somebody
  decides it;
* **coverage** -- an AST scan of `yulon/` finds every family branch (a compare
  against a family's name, a family block's attribute, a dict keyed by family
  names, an isinstance on a family's class, a `case` on a family's name) and
  fails for one outside a registered site, and for a registered site the scan
  no longer finds.

The scan reads the AST, not text: a docstring or comment naming `cmangos` is
not a branch, and `getattr(native, "cmangos")` would still be missed -- nothing
in `yulon/` spells one today, and the control test below is what keeps the
scanner's own rules honest.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import get_args

import pytest

from yulon.catalog.catalog import NativeInstall
from yulon.catalog.families import FAMILIES, decisions
from yulon.catalog.families.decisions import FAMILY_DECISIONS, Site

PACKAGE = Path(decisions.__file__).resolve().parents[2]
"""`yulon/` itself."""

REGISTRY = Path(decisions.__file__).resolve()

FAMILY_NAMES: frozenset[str] = frozenset(get_args(NativeInstall.model_fields["family"].annotation))


def _block_classes() -> frozenset[str]:
    """Each family's block class and engine class, by name: what an isinstance would name."""
    names: set[str] = set()
    for family in FAMILY_NAMES:
        for arg in get_args(NativeInstall.model_fields[family].annotation):
            if arg is not type(None):
                names.add(arg.__name__)
    names.update(cls.__name__ for cls in FAMILIES.values())
    return frozenset(names)


FAMILY_CLASSES = _block_classes()


def _names_a_family(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value in FAMILY_NAMES
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(_names_a_family(element) for element in node.elts)
    return False


def _names_a_family_class(node: ast.AST) -> bool:
    elements = node.elts if isinstance(node, ast.Tuple) else [node]
    for element in elements:
        if isinstance(element, ast.Name) and element.id in FAMILY_CLASSES:
            return True
        if isinstance(element, ast.Attribute) and element.attr in FAMILY_CLASSES:
            return True
    return False


class _Scan(ast.NodeVisitor):
    """Every family branch in one module, as `(scope, line, kind)`.

    `scope` is the qualified name of the def or class around the branch
    (`Class.method`), or `<module>` at the top level, so a site survives edits
    that move its line.
    """

    def __init__(self) -> None:
        self.stack: list[str] = []
        self.hits: list[tuple[str, int, str]] = []

    def _hit(self, node: ast.expr | ast.pattern, kind: str) -> None:
        self.hits.append((".".join(self.stack) or "<module>", node.lineno, kind))

    def _scoped(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _scoped
    visit_AsyncFunctionDef = _scoped
    visit_ClassDef = _scoped

    def visit_Compare(self, node: ast.Compare) -> None:
        if any(_names_a_family(side) for side in (node.left, *node.comparators)):
            self._hit(node, "compare")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FAMILY_NAMES:
            self._hit(node, f".{node.attr}")
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        if any(key is not None and _names_a_family(key) for key in node.keys):
            self._hit(node, "dict")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Name)
            and func.id in ("isinstance", "issubclass")
            and len(node.args) == 2
            and _names_a_family_class(node.args[1])
        ):
            self._hit(node, "isinstance")
        self.generic_visit(node)

    def visit_MatchValue(self, node: ast.MatchValue) -> None:
        if _names_a_family(node.value):
            self._hit(node, "case")
        self.generic_visit(node)


def scan(source: str) -> dict[str, list[str]]:
    """`scope -> ["<line><kind>", …]` for every family branch in `source`."""
    visitor = _Scan()
    visitor.visit(ast.parse(source))
    found: dict[str, list[str]] = defaultdict(list)
    for scope, line, kind in visitor.hits:
        found[scope].append(f"{line}{kind}")
    return dict(found)


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(PACKAGE.parent).with_suffix("").parts)


def scanned_sites() -> dict[tuple[str, str], list[str]]:
    """`(module, scope) -> hits` over all of `yulon/` except the registry itself."""
    found: dict[tuple[str, str], list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.resolve() == REGISTRY:
            continue
        for scope, hits in scan(path.read_text(encoding="utf-8")).items():
            found[(_module_name(path), scope)] = hits
    return found


def _scopes_in(module: str) -> set[str]:
    """Every def/class qualname in `module`, and its top-level assignment targets."""
    path = PACKAGE.parent.joinpath(*module.split(".")).with_suffix(".py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualname = f"{prefix}{child.name}"
                names.add(qualname)
                walk(child, f"{qualname}.")

    walk(tree, "")
    for statement in tree.body:
        targets: list[ast.expr] = []
        if isinstance(statement, ast.Assign):
            targets = list(statement.targets)
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


SITE_IDS = [f"{site.module}:{site.scope}" for site in FAMILY_DECISIONS]


# -- completeness ------------------------------------------------------------------


@pytest.mark.parametrize("site", FAMILY_DECISIONS, ids=SITE_IDS)
def test_every_site_decides_every_family(site: Site) -> None:
    assert set(site.decisions) == FAMILY_NAMES, (
        f"{site.module}:{site.scope} decides {sorted(site.decisions)}, "
        f"the families are {sorted(FAMILY_NAMES)}"
    )


@pytest.mark.parametrize("site", FAMILY_DECISIONS, ids=SITE_IDS)
def test_every_decision_that_withholds_something_says_why(site: Site) -> None:
    for family, decision in site.decisions.items():
        if decision.kind in ("not-available", "not-applicable"):
            assert decision.note.strip(), f"{site.module}:{site.scope} {family}: no reason"
        if decision.kind == "pending":
            assert decision.note.startswith("Task "), (site.module, site.scope, family)


def test_pending_is_trinitycores_alone_and_only_while_the_flag_allows_it() -> None:
    """`pending` is T179's runway, not a fourth way to decide nothing.

    Allowed for `trinitycore` only, and only while `TRINITYCORE_PENDING` is
    True; the flag is turned off at the end of T179 (Task 8), after which every
    site must say `supported`, `not-available` or `not-applicable`. And the flag
    may not outlive the runway: on with nothing pending is a flag nobody turned
    off.
    """
    pending = [
        (site.module, site.scope, family)
        for site in FAMILY_DECISIONS
        for family, decision in site.decisions.items()
        if decision.kind == "pending"
    ]
    assert all(family == "trinitycore" for _, _, family in pending), pending
    if decisions.TRINITYCORE_PENDING:
        assert pending, "TRINITYCORE_PENDING is on but no site is pending: turn it off"
    else:
        assert not pending, pending


def test_no_site_is_registered_twice() -> None:
    assert len(SITE_IDS) == len(set(SITE_IDS)), sorted(SITE_IDS)


def test_my_party_is_not_offered_on_trinitycore() -> None:
    """Spec §2: My Party needs AzerothCore's Lua bridge; it is WotLK-only."""
    party = next(site for site in FAMILY_DECISIONS if site.scope.endswith("for_entry_is_possible"))
    decision = party.decisions["trinitycore"]
    assert decision.kind == "not-available"
    assert "Lua bridge" in decision.note


# -- coverage -------------------------------------------------------------------


def test_every_family_branch_in_yulon_is_a_registered_site() -> None:
    found = scanned_sites()
    registered = {(site.module, site.scope) for site in FAMILY_DECISIONS if site.scanned}
    unregistered = {key: hits for key, hits in found.items() if key not in registered}
    assert not unregistered, (
        "family branches outside the registry -- add each to "
        f"yulon/catalog/families/decisions.py with a decision per family: {unregistered}"
    )


def test_every_registered_scanned_site_still_branches_on_a_family() -> None:
    found = scanned_sites()
    stale = [
        f"{site.module}:{site.scope}"
        for site in FAMILY_DECISIONS
        if site.scanned and (site.module, site.scope) not in found
    ]
    assert not stale, f"registered sites the scan no longer finds: {stale}"


@pytest.mark.parametrize(
    "site", [site for site in FAMILY_DECISIONS if not site.scanned], ids=lambda site: site.scope
)
def test_a_site_the_scan_cannot_see_still_exists_and_is_not_one_it_can(site: Site) -> None:
    """Sites that branch on an id or a data field rather than a family's name.

    The scan cannot hold them, so the registry names them by hand -- and this
    test keeps that hand-kept list from rotting: the scope must still exist,
    and must not be one the scan finds (then it belongs with the scanned ones).
    """
    assert site.scope in _scopes_in(site.module), f"{site.module} has no {site.scope}"
    assert (site.module, site.scope) not in scanned_sites()


# -- the scanner's own control ------------------------------------------------------


def test_the_scan_finds_each_kind_of_branch_and_nothing_in_prose() -> None:
    """Green above must mean "registered", not "the scanner saw nothing"."""
    family, other = sorted(FAMILY_NAMES)[:2]
    block_class = sorted(FAMILY_CLASSES)[0]
    source = (
        f'"""A docstring naming {family} and .{other}."""\n'
        "# a comment naming x.{family}\n"
        "def compare(native):\n"
        f"    return native.family == {family!r}\n"
        "def membership(native):\n"
        f"    return native.family not in ({family!r}, {other!r})\n"
        "def block(native):\n"
        f"    return native.{other}\n"
        "def table():\n"
        f"    return {{{family!r}: 1}}\n"
        "class Engine:\n"
        "    def check(self, engine):\n"
        f"        return isinstance(engine, {block_class})\n"
        "def matched(native):\n"
        "    match native.family:\n"
        f"        case {family!r}:\n"
        "            return 1\n"
        "def clean(native):\n"
        "    return native.family == 'something else'\n"
    )
    found = scan(source)
    assert set(found) == {
        "compare",
        "membership",
        "block",
        "table",
        "Engine.check",
        "matched",
    }, found
    assert found["compare"] == ["4compare"]
    assert found["block"] == [f"8.{other}"]
    assert found["Engine.check"] == ["13isinstance"]
    assert found["matched"] == ["16case"]


def test_the_scan_over_yulon_finds_the_branches_known_today() -> None:
    """A control on the real tree: two sites everyone knows are there."""
    found = scanned_sites()
    assert ("yulon.catalog.families.__init__", "<module>") in found
    assert ("yulon.catalog.catalog", "NativeInstall._exactly_the_family_block") in found
