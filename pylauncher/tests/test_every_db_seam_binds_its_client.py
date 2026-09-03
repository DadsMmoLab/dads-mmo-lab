"""Every `DockerSql(...)` / `DockerMysql(...)` in the app names a client. Audited by AST.

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

So this asks the SOURCE instead. Every module under `yulon/` is parsed, every
call to either constructor is found wherever it appears, and each must pass
`client=`. A site added tomorrow in a module nobody thought to import is caught
by construction, and there is no list to keep current.

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

SEAMS = {"DockerSql", "DockerMysql"}

PACKAGE = Path(__file__).resolve().parent.parent / "yulon"


def _called_name(node: ast.Call) -> str | None:
    """The constructor's own name, however it was reached.

    Both spellings occur in the tree and both must be audited: `DockerSql(...)`
    from a direct import, and `wotlk_maintenance.DockerMysql(...)` through the
    module. Taking only `ast.Name` would silently skip the second, which is the
    spelling of two of the sites this file was written for.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def test_every_database_seam_construction_names_a_client() -> None:
    unbound: list[str] = []
    found = 0
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _called_name(node) not in SEAMS:
                continue
            found += 1
            if not any(keyword.arg == "client" for keyword in node.keywords):
                where = path.relative_to(PACKAGE.parent)
                unbound.append(f"{where}:{node.lineno} {_called_name(node)}(...)")
    assert found >= 8, (
        f"only {found} seam constructions were found in {PACKAGE}; the audit is not "
        "reaching the source it thinks it is"
    )
    assert not unbound, (
        "these build a database seam without naming a client, so they fall back to `mysql` "
        "when the container cannot be probed -- which the three MariaDB games do not have:\n  "
        + "\n  ".join(unbound)
    )
