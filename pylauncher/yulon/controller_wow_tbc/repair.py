"""Does this TBC install's database look imported? — and the one question it cannot answer.

`controller_wow_wotlk/repair.py` is the AzerothCore answer and none of it
transfers. It reads `updates` and `updates_include`, the two tables
AzerothCore's own updater keeps to record which SQL files it has applied; a
CMaNGOS core keeps no such pair, so ported here that probe would report
`absent` — never imported — for a perfectly healthy TBC server and offer the
destructive Repair button on the strength of it. That is not hypothetical: it
is why `install_wiring.import_gate_for()` attaches the AzerothCore pair only to
an entry that names a one-shot import service (review, 2026-08-23).

The evidence this family DOES have is the marker row its own installer writes.
`catalog/families/sqlplan.py` runs the entry's SQL plan and, only after
`verify()` passes, writes `mangos.yulon_install` carrying the plan's hash;
`sqlplan.MarkerGate` is the five-branch probe over that table plus the plan's
`player_data` rules, ordered by what a wrong answer would cost. This module
builds one for the `wow-tbc` entry and hands it the two facts the installer gets
from its own context: which database container, and this install's generated
password.

**The `partial` branch is translated to `unreadable`, and that is the whole
safety argument of this module.** `MarkerGate` reads "the schemas exist, no
marker row, no player rows" as `partial`, which is correct for the installer —
it has just created those schemas itself, so nothing else can have. A
controller is not in that position: it also manages installs adopted through
"Use existing…", and a CMaNGOS server installed by anything other than this app
has no `yulon_install` table and never will. Such a server reads `partial`,
`ImportState.repairable` is True for `partial`, and the Server tab would offer
Repair on a working install whose only accounts are the seeded ones the plan
excludes. Reported as `unreadable` it is what it actually is: a question this
app cannot answer about somebody else's install. The information is not thrown
away — the gate's own sentence is carried into the detail.

So there is no reset here. `reset_unfinished()` names the missing fact and
raises; see its docstring.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from yulon import docker
from yulon.catalog.families import sqlplan
from yulon.catalog.installer import InstallerError as InstallerError
from yulon.controller_wow_tbc import docker_ctl
from yulon.log import get_logger

logger = get_logger(__name__)

PLAN: Final = docker_ctl.CMANGOS.sql
"""The SQL plan whose completion the marker records — `install.native.cmangos.sql`."""

SCHEMAS: Final[dict[str, str]] = {name: name for name in docker_ctl.SCHEMAS}
"""The identity map `sqlplan` looks plan names up in, keyed by NAME and not by role.

A plan spells its databases the way `catalog.json` spells them (`mangos`,
`realmd`, `characters`, `logs`), never `world` or `auth`, so
`CatalogEntry.schema_map()` — which is keyed by the manifest role — answers for
none of them. The map exists so that a plan naming a database this game does
not have is refused by one check, before any statement runs; the same identity
map the CMaNGOS installer builds for itself, which a controller has no way to
reach into.
"""

MARKER_TABLE: Final = sqlplan.MARKER_TABLE
"""`yulon_install`, in `PLAN.marker_db` — imported, so the two cannot drift apart."""

_ADOPTED_INSTALL_NOTE = (
    "{detail}. That marker is written only by this app's own installer, so a CMaNGOS "
    "server installed any other way never has one — which means nothing here can tell an "
    "unfinished import from a finished install this app did not create, and neither the "
    "import nor a reset is offered."
)


class RepairError(RuntimeError):
    """This install's database state could not be asked about at all.

    Raised only by `import_gate()`/`db_password()`, never by `import_state()`,
    which turns every failure into an `unreadable` answer — the caller is a
    button's visibility and a five-second status path, and neither has anywhere
    useful to put an exception.
    """


def db_password(server_dir: Path) -> str:
    """This install's database root password, read from the file its plan named.

    Never the shared `DEFAULT_DB_ROOT_PASSWORD`. This entry's plan is
    `generated`: the spine mints `tbc-` plus 8 random bytes before stage 1,
    writes it to `.db_password` under the server dir and hands it to compose
    through `.env`, so there is no fixed value to fall back on and a fallback
    would authenticate as root with the literal string "password" against a
    server that has never used it.

    Raises:
        RepairError: the entry names no password file, or that file cannot be
            read. `Install.db_password()` returns None for both, which is
            deliberately not the same answer as "use the default": it means this
            install's password is not knowable from here.
    """
    password = docker_ctl.ENTRY.install.db_password(server_dir)
    if password is None:
        named = docker_ctl.ENTRY.install.password.file or "(no file named)"
        raise RepairError(
            f"this install's database password could not be read from {named} in {server_dir}, "
            "so its databases cannot be asked what state they are in"
        )
    return password


def import_gate(
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
    sql_query: sqlplan.SqlQuery = docker.sql_query,
    exec_stdin: sqlplan.ExecStdin = docker.exec_stdin,
) -> sqlplan.MarkerGate:
    """A `MarkerGate` over this install's databases.

    `client=` is `install.native.db.client` and not a literal: `mariadb:11`
    ships neither `mysql` nor `mysqldump`, so the AzerothCore spelling names a
    binary that is not in this container (measured on a live TBC server,
    2026-08-26).

    `wsl_distro` decides which daemon is asked. Asked of the wrong one, a fully
    populated database answers `absent` — the probe reads "nothing is here" for
    a container the daemon has simply never heard of.

    Raises:
        RepairError: the password is not knowable (see `db_password()`).
        InstallerError: the plan names a schema this entry does not have — a
            `catalog.json` error, raised when the gate is BUILT so that
            `probe()` has no way to raise at all.
    """
    return sqlplan.MarkerGate(
        PLAN,
        container=docker_ctl.SPEC.db,
        client=docker_ctl.DB_CLIENT,
        password=db_password(server_dir),
        schemas=SCHEMAS,
        sql_query=sql_query,
        exec_stdin=exec_stdin,
        wsl_distro=wsl_distro,
    )


def import_state(
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
    sql_query: sqlplan.SqlQuery = docker.sql_query,
    exec_stdin: sqlplan.ExecStdin = docker.exec_stdin,
) -> docker.ImportState:
    """What state this install's schemas are in, as a controller may report it. Never raises.

    `MarkerGate.probe()`, with its `partial` branch reported as `unreadable` for
    the reason in this module's docstring: on an install this app did not
    create, "there is no marker row" is not evidence that the import stopped
    part-way, and `partial` is the state that authorises dropping databases.

    Every other branch is passed through unchanged, including `imported` with
    its `complete` flag and `populated` without one.
    """
    try:
        gate = import_gate(
            server_dir, wsl_distro=wsl_distro, sql_query=sql_query, exec_stdin=exec_stdin
        )
    except (RepairError, InstallerError) as exc:
        return docker.ImportState("unreadable", str(exc))
    state = gate.probe()
    if state.state == "partial":
        logger.info(f"{server_dir} has no {MARKER_TABLE} row: {state.detail}")
        return docker.ImportState("unreadable", _ADOPTED_INSTALL_NOTE.format(detail=state.detail))
    return state


def reset_unfinished() -> tuple[str, ...]:
    """Not implemented for this core, and the missing fact is the reason.

    `docker.ResetUnfinished`'s shape, so it can be recognised as the seam it
    would fill, and it refuses rather than acting.

    What a reset needs is proof that a database is half-written, and on this
    family the only proof available is the absence of a marker row this app
    writes. That absence is also what a healthy CMaNGOS install created by
    anything else looks like, so dropping on it would delete a stranger's
    `realmd`, `characters` and `mangos`. AzerothCore has a second, independent
    witness — its updater's own `updates`/`updates_include` tables — and
    CMaNGOS's equivalent bookkeeping, if it has one, has not been established
    by this project.

    The other half is that there would be nothing to run afterwards: this
    stack has no one-shot import service (`containers.db_import` is unset), so
    `docker.repair_import()` refuses before it drops anything. A reset here
    would leave an install with empty schemas and no way to fill them from the
    Server tab.

    Raises:
        NotImplementedError: always.
    """
    raise NotImplementedError(
        f"{docker_ctl.GAME} has no way to prove an import stopped part-way: the only evidence "
        f"this app has is the {MARKER_TABLE} marker its own installer writes, and its absence "
        "is also what a healthy install made by other means looks like. CMaNGOS's own "
        "'which SQL files were applied' bookkeeping (AzerothCore keeps `updates` and "
        "`updates_include`) has not been established for this core, and until it is, nothing "
        "here may drop a database. Re-install into a new server directory instead."
    )
