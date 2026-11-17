"""Names `main.py` imports only for type checking are never used at run time without an import.

T888 (PR #477 Windows test): `forever_play` sat in `main.py`'s `if TYPE_CHECKING:` block and
`_trust_record_live()` called `forever_play.TrustRecord(...)`, so building every single-server
tab raised NameError and WoW Forever's tab never appeared. Annotations are strings under
`from __future__ import annotations`, so only a use OUTSIDE an annotation needs the name at
run time, and such a use must sit in a function that imports it itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

MAIN = Path(__file__).resolve().parent.parent / "main.py"


def _type_checking_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        ):
            for stmt in node.body:
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    names.update((a.asname or a.name).split(".")[0] for a in stmt.names)
    return names


def _annotation_nodes(tree: ast.AST) -> set[int]:
    """ids of every node inside an annotation (arguments, returns, AnnAssign targets)."""
    inside: set[int] = set()

    def mark(node: ast.AST | None) -> None:
        if node is not None:
            inside.update(id(n) for n in ast.walk(node))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mark(node.returns)
            args = node.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
                if arg is not None:
                    mark(arg.annotation)
        elif isinstance(node, ast.AnnAssign):
            mark(node.annotation)
    return inside


def _local_imports(func: ast.AST) -> set[str]:
    """Names `func` itself imports: not those of functions nested in it (a sibling's local
    import binds nothing here -- exactly how the T888 NameError hid beside the removal code's
    own `from yulon import forever_play`)."""
    found: set[str] = set()
    pending = list(ast.iter_child_nodes(func))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found.update((a.asname or a.name).split(".")[0] for a in node.names)
        pending.extend(ast.iter_child_nodes(node))
    return found


def runtime_uses_without_an_import(source: str) -> list[str]:
    """`name@line` for each run-time use of a TYPE_CHECKING-only name with no import in scope."""
    tree = ast.parse(source)
    guarded = _type_checking_names(tree)
    annotations = _annotation_nodes(tree)
    bad: list[str] = []

    def visit(node: ast.AST, imported: frozenset[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            imported = imported | _local_imports(node)
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in guarded
            and node.id not in imported
            and id(node) not in annotations
        ):
            bad.append(f"{node.id}@{node.lineno}")
        for child in ast.iter_child_nodes(node):
            visit(child, imported)

    for top in tree.body:
        if not (isinstance(top, ast.If) and isinstance(top.test, ast.Name)):
            visit(top, frozenset())
    return bad


def test_main_uses_no_type_checking_only_name_at_run_time() -> None:
    assert runtime_uses_without_an_import(MAIN.read_text(encoding="utf-8")) == []


def test_the_check_sees_the_t888_shape() -> None:
    """The guard itself: the NameError's exact shape is reported, the fixed shape is not."""
    broken = (
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from yulon import forever_play\n"
        "def outer():\n"
        "    def removal():\n"
        "        from yulon import forever_play\n"
        "        return forever_play\n"
        "    def live(x: forever_play.TrustRecord) -> forever_play.TrustRecord:\n"
        "        return forever_play.TrustRecord()\n"
    )
    assert runtime_uses_without_an_import(broken) == ["forever_play@10"]
    fixed = broken.replace(
        "        return forever_play.TrustRecord()",
        "        from yulon import forever_play\n        return forever_play.TrustRecord()",
    )
    assert runtime_uses_without_an_import(fixed) == []
