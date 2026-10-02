"""Every family branch decides every family (T179 Task 1, Review Focus 5).

The defect this exists for is the shape most family branches had before T179:
`if family == "azerothcore": … elif family == "cmangos": … else: nothing`. A
third family falls into the `else` at every one of them, silently, and nothing
fails -- the feature is just absent. `yulon/catalog/families/decisions.py` is
the registry that says, per site and per family, what that family gets there;
this file holds it to three things:

* **completeness** -- every site names every member of `NativeInstall.family`,
  so adding a family to the Literal turns this red at every site until somebody
  decides it;
* **coverage** -- an AST scan of `yulon/` finds every family branch (`_Scan`
  lists the spellings) and fails for one outside a registered site, for a
  registered site the scan no longer finds, and for a registered site whose
  hits changed since it was decided (`Site.hits`);
* **runway** -- `pending` is trinitycore's alone, and only while the shipped
  catalog installs no trinitycore entry.

The scan reads the AST, not text: a docstring or comment naming `cmangos` is
not a branch. The control test below holds each spelling it must catch,
including the ones a review of the first version found it missing.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import get_args

import pytest

from yulon.catalog.catalog import NativeInstall, load_catalog
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


def _is_family_name(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (node.value in FAMILY_NAMES)
    )


def _class_named(node: ast.AST) -> str | None:
    """The family class `node` names (`X` or `pkg.X`), or None."""
    if isinstance(node, ast.Name) and node.id in FAMILY_CLASSES:
        return node.id
    if isinstance(node, ast.Attribute) and node.attr in FAMILY_CLASSES:
        return node.attr
    return None


def _is_literal_type(node: ast.Subscript) -> bool:
    value = node.value
    return (isinstance(value, ast.Name) and value.id == "Literal") or (
        isinstance(value, ast.Attribute) and value.attr == "Literal"
    )


def _is_family_value(node: ast.AST) -> bool:
    """`x.family`: the value a branch would be taken on.

    Not a bare `family` name: `party.deploy()` loops `for family in <dirs>` over
    Lua folders and calls `family.glob()`, which is no server family. A bare
    name copied from `.family` and then compared is still caught, by the
    constant it is compared against.
    """
    return isinstance(node, ast.Attribute) and node.attr == "family"


class _Scan(ast.NodeVisitor):
    """Every family branch in one module, as `(scope, line, kind)`.

    `scope` is the qualified name of the def or class around the branch
    (`Class.method`), or `<module>` at the top level, so a site survives edits
    that move its line. What counts, each with its `kind`:

    * `'cmangos'` -- ANY string constant equal to a family's name, wherever it
      stands: a compare, a `case`, a dict key, a subscript, `.get(…)`,
      `getattr(…)`, a default, a module constant. Only a `Literal[…]` type is
      exempt: it declares the names, it does not branch on one. A docstring is
      never exactly a family's name, so prose cannot be a hit;
    * `.cmangos` -- a family block's attribute;
    * `cmangos=` -- a keyword argument named after a family (`dict(cmangos=…)`);
    * `isinstance CmangosData` / `case CmangosData()` -- a test on a family's
      block or engine class;
    * `FAMILY` -- a load of a module-level name bound to a family's name, so a
      branch through a constant is caught where it is taken, not only where the
      constant is written;
    * `.family.startswith()` -- a method called on a family value, which
      branches on the name without spelling it.
    """

    def __init__(self, aliases: frozenset[str]) -> None:
        self.aliases = aliases
        self.stack: list[str] = []
        self.hits: list[tuple[str, int, str]] = []

    def _hit(self, node: ast.expr | ast.pattern | ast.keyword, kind: str) -> None:
        self.hits.append((".".join(self.stack) or "<module>", node.lineno, kind))

    def _scoped(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _scoped
    visit_AsyncFunctionDef = _scoped
    visit_ClassDef = _scoped

    def visit_Constant(self, node: ast.Constant) -> None:
        if _is_family_name(node):
            self._hit(node, repr(node.value))

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_literal_type(node):
            return
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FAMILY_NAMES:
            self._hit(node, f".{node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in self.aliases:
            self._hit(node, node.id)

    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg in FAMILY_NAMES:
            self._hit(node, f"{node.arg}=")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in ("isinstance", "issubclass") and node.args:
            tested = node.args[-1]
            for element in tested.elts if isinstance(tested, ast.Tuple) else [tested]:
                named = _class_named(element)
                if named is not None:
                    self._hit(node, f"isinstance {named}")
        if isinstance(func, ast.Attribute) and _is_family_value(func.value):
            self._hit(node, f".family.{func.attr}()")
        self.generic_visit(node)

    def visit_MatchClass(self, node: ast.MatchClass) -> None:
        named = _class_named(node.cls)
        if named is not None:
            self._hit(node, f"case {named}()")
        self.generic_visit(node)


def _aliases(tree: ast.Module) -> frozenset[str]:
    """Module-level names bound to a family's name (`FAMILY = "cmangos"`)."""
    names: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and _is_family_name(statement.value):
            names.update(t.id for t in statement.targets if isinstance(t, ast.Name))
        elif (
            isinstance(statement, ast.AnnAssign)
            and statement.value is not None
            and _is_family_name(statement.value)
            and isinstance(statement.target, ast.Name)
        ):
            names.add(statement.target.id)
    return frozenset(names)


def scan(source: str) -> dict[str, list[str]]:
    """`scope -> ["<line> <kind>", …]` for every family branch in `source`."""
    tree = ast.parse(source)
    visitor = _Scan(_aliases(tree))
    visitor.visit(tree)
    found: dict[str, list[str]] = defaultdict(list)
    for scope, line, kind in sorted(visitor.hits, key=lambda hit: (hit[1], hit[2])):
        found[scope].append(f"{line} {kind}")
    return dict(found)


def kinds(hits: list[str]) -> tuple[str, ...]:
    """The kinds of a scope's hits, without their lines, sorted: what the registry pins."""
    return tuple(sorted(hit.split(" ", 1)[1] for hit in hits))


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


def _shipped_families() -> set[str]:
    return {
        entry.install.native.family
        for entry in load_catalog().games
        if entry.install.native is not None
    }


def test_pending_is_allowed_today_because_no_shipped_entry_is_trinitycore() -> None:
    """`pending` is T179's runway: trinitycore only, and only while nothing ships on it."""
    assert "trinitycore" not in _shipped_families()
    assert decisions.pending_problems(FAMILY_DECISIONS, _shipped_families()) == []


def test_pending_cannot_ship_a_catalog_that_installs_the_family() -> None:
    """The lead's ruling: no manual flag; the shipped catalog is what closes the runway.

    The day an entry with `family: trinitycore` lands in `catalog.json`, every
    site still `pending` for it turns this red -- a Centurion that installs
    while a tab silently lacks its branch cannot be released. A synthetic site
    rather than the registry's own, so the rule is proved even after the last
    real `pending` is gone.
    """
    site = Site(
        "yulon.example",
        "somewhere",
        "a branch still owed",
        {
            "azerothcore": decisions.supported(),
            "cmangos": decisions.supported(),
            "trinitycore": decisions.pending("Task 5 (somewhere)"),
        },
    )
    shipped = _shipped_families() | {"trinitycore"}
    problems = decisions.pending_problems((site,), shipped)
    assert problems == [
        "yulon.example:somewhere is still pending for trinitycore (Task 5 (somewhere)), "
        "and the shipped catalog installs trinitycore"
    ]
    assert decisions.pending_problems((site,), _shipped_families()) == []


def test_pending_is_trinitycores_alone() -> None:
    site = Site(
        "yulon.example",
        "elsewhere",
        "a branch",
        {
            "azerothcore": decisions.supported(),
            "cmangos": decisions.pending("Task 5"),
            "trinitycore": decisions.supported(),
        },
    )
    assert decisions.pending_problems((site,), set()) == [
        "yulon.example:elsewhere is pending for cmangos; pending is trinitycore's alone"
    ]


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
    "site", [site for site in FAMILY_DECISIONS if site.scanned], ids=lambda site: site.scope
)
def test_each_scanned_site_pins_the_branches_it_was_decided_on(site: Site) -> None:
    """A decision is about the branches a scope had when it was made.

    One registered scope could otherwise hide any number of new branches: a
    second `if family == …` added inside `reset_defaults.core_files` is in a
    site that is already "decided". Pinning the kinds of every hit -- not the
    lines, which move with any edit above them -- turns that addition (or a
    removal) red here, and the fix is to look at the decisions again and
    update `hits`.
    """
    found = scanned_sites().get((site.module, site.scope), [])
    assert kinds(found) == site.hits, (
        f"{site.module}:{site.scope} now branches as {kinds(found)}, decided on "
        f"{site.hits}: re-decide each family there, then update `hits`"
    )


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
    """Green above must mean "registered", not "the scanner saw nothing".

    One function per spelling of a branch, including the ones the first
    version of the scan missed (review of 669246f5): a constant alias,
    `getattr`, a subscript, `.get`, a method on `.family`, a `case` alternative,
    a `case` on a block class, a lambda, a set, a keyword argument. Each is
    expected by name, so a rule that stopped firing fails here, not as a green
    scan of `yulon/`.
    """
    fam, other = sorted(FAMILY_NAMES)[:2]
    block = sorted(FAMILY_CLASSES)[0]
    source = f'''"""A docstring naming {fam} and .{other}."""
# a comment naming x.{fam}
from typing import Literal
ALIAS = {fam!r}
Kind = Literal[{fam!r}, {other!r}]
def compare(n):
    return n.family == {fam!r}
def membership(n):
    return n.family not in ({fam!r}, {other!r})
def block(n):
    return n.{other}
def table():
    return {{{fam!r}: 1}}
class Engine:
    def check(self, engine):
        return isinstance(engine, {block})
def matched(n):
    match n.family:
        case "x" | {fam!r}:
            return 1
def via_alias(n):
    return n.family == ALIAS
def via_getattr(n):
    return getattr(n, {other!r})
def via_subscript(table):
    return table[{fam!r}]
def via_get(table):
    return table.get({fam!r})
def via_method(n):
    return n.family.startswith("x")
def via_match_class(b):
    match b:
        case {block}():
            return 1
def via_lambda():
    return lambda n: n.family == {fam!r}
def via_set(n):
    return n.family in {{{fam!r}}}
def via_keyword(n):
    return dict({fam}=1)[n.family]
def annotated(x: Literal[{fam!r}]) -> None:
    pass
def clean(n, family):
    return n.family == "something else" and family.glob("*.lua")
'''
    found = {scope: kinds(hits) for scope, hits in scan(source).items()}
    assert found == {
        "<module>": (repr(fam),),
        "compare": (repr(fam),),
        "membership": tuple(sorted((repr(fam), repr(other)))),
        "block": (f".{other}",),
        "table": (repr(fam),),
        "Engine.check": (f"isinstance {block}",),
        "matched": (repr(fam),),
        "via_alias": ("ALIAS",),
        "via_getattr": (repr(other),),
        "via_subscript": (repr(fam),),
        "via_get": (repr(fam),),
        "via_method": (".family.startswith()",),
        "via_match_class": (f"case {block}()",),
        "via_lambda": (repr(fam),),
        "via_set": (repr(fam),),
        "via_keyword": (f"{fam}=",),
    }, found


def test_the_scan_over_yulon_finds_the_branches_known_today() -> None:
    """A control on the real tree: two sites everyone knows are there."""
    found = scanned_sites()
    assert ("yulon.catalog.families.__init__", "<module>") in found
    assert ("yulon.catalog.catalog", "NativeInstall._exactly_the_family_block") in found
