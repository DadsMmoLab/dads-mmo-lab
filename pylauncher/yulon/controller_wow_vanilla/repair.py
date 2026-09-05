"""Does this Vanilla install's database look imported? — and why the answer is not WotLK's.

One job: answer `docker.ImportProbe` for a CMaNGOS install. It is the only
module in this package that could not be a binding of the WotLK one, because
the EVIDENCE is different, not just the names.

**What AzerothCore's probe reads, and why none of it exists here.**
`controller_wow_wotlk/repair.py` asks three questions, and its third — did the
import finish? — is answered from `updates` and `updates_include`, the two
tables AzerothCore's own SQL updater maintains to record which files it has
applied. That choice was earned by a live gate (yulon-ubuntu, 2026-08-23: an
import killed 19 seconds in left `acore_world` with 3 tables of 316 and a
table-count probe called it imported). CMaNGOS has no such updater and writes
no such tables, so that question has no answer on this core and a copy of that
module would report every healthy Vanilla install as never imported.

**What this install has instead.** Its import is not a compose one-shot at all
— `catalog/families/cmangos.py` applies the SQL plan itself — and after the
plan's `verify` rules pass it writes one row into `<marker_db>.yulon_install`.
That row is the completion record, and `sqlplan.MarkerGate` is the probe built
on it: the same five branches, ordered by what a wrong answer costs
(`imported` before `populated`, `populated` before `partial`, because
`partial` is the only branch that DROPS DATABASES), with everything
unanswerable landing on `unreadable`.

So this module builds that gate for THIS install and hands back the two seams
`yulon.controller.Controller` takes. It reimplements no branch of it.

**The hazard a caller must know about, because nothing here can prevent it.**
`ImportState.repairable` is True for `absent` and `partial`, and the UI shows
its Repair button on that property. But `docker.repair_import()` refuses
outright without a `spec.import_service`, and this entry names none — so a
view that offers Repair on `repairable` alone offers a button that can only
answer "this game never said which service imports". The repair for a CMaNGOS
install is running the installer again, whose `import` stage consults this same
marker and, from `partial`, resets and re-imports. Whoever wires this into
`ui/controller_view.py` has to gate the button on
`docker_ctl.SPEC.import_service` rather than on `repairable`.

`reset()` is exposed for that installer path and is deliberately NOT wired into
the controller by anything here: it drops databases, `repair_import()` refuses
before it could ever call it, and a destructive seam attached to a code path
that cannot reach it is a seam waiting for someone to make it reachable.
"""

from __future__ import annotations

from pathlib import Path

from yulon import docker
from yulon.catalog.catalog import SqlPlan
from yulon.catalog.families import sqlplan
from yulon.catalog.installer import InstallerError
from yulon.controller_wow_vanilla import docker_ctl, entry
from yulon.log import get_logger

logger = get_logger(__name__)

MARKER_TABLE = sqlplan.MARKER_TABLE
"""`yulon_install`, the table whose row IS the import record — imported, not restated."""


def sql_plan() -> SqlPlan:
    """The SQL plan the installer runs for this game, off the catalog entry.

    The probe needs it whole rather than in pieces: `MarkerGate` reads
    `marker_db` to know where the record lives, `player_data` to know which
    table proves somebody has used this server (and which seeded usernames do
    not count), and `plan_hash()` to say in the log whether the finished import
    was written by this app's plan or an older one.

    Raises:
        RuntimeError: the entry has no `install.native.cmangos` block. The
            catalog's own validator refuses that shape — `family: cmangos` and
            no `cmangos` block cannot both be true — so this names a defect
            rather than a state to handle.
    """
    native = entry().install.native
    data = None if native is None else native.cmangos
    if data is None:  # pragma: no cover - the catalog validator refuses this shape
        raise RuntimeError(
            f"{entry().name} has no `install.native.cmangos` block, so there is no SQL plan "
            "to ask about. That is a catalog error in the app."
        )
    return data.sql


def schemas() -> dict[str, str]:
    """The identity mapping over this game's schema names, keyed by name.

    Not keyed by role, and that is the whole point of it existing (contract
    A10). `sqlplan` looks a plan's `marker_db`, `verify.db` and `player_data.db`
    up in here, and every one of those spells a database the way `catalog.json`
    spells it — `mangos`, `realmd` — never `world` or `auth`. A map keyed by
    role would answer for none of them.

    Identity rather than a rename, because the plan's names ARE the server's
    names. What the mapping buys is that a plan naming a database this game
    does not have is refused in one place, by `sqlplan`, ahead of every query.

    The same mapping `CmangosInstaller._schemas()` builds for the install side.
    It is derived from the entry's `databases` block in both places rather than
    passed between them, so an install and a controller cannot disagree about
    which schemas exist while reading one catalog entry.
    """
    db = entry().databases
    return {name: name for name in (db.auth, db.characters, db.world, *db.extra)}


def gate(
    db_root_password: str,
    *,
    wsl_distro: str | None = None,
    sql_query: sqlplan.SqlQuery = docker.sql_query,
    exec_stdin: sqlplan.ExecStdin = docker.exec_stdin,
) -> sqlplan.MarkerGate:
    """The `MarkerGate` for this install: the plan's marker table, asked through docker.

    `client=docker_ctl.DB_CLIENT` is the catalog's declared client and not a
    literal. `mariadb:11` ships no `mysql` binary at all, so a probe that named
    the AzerothCore client would fail to run rather than answer — and a probe
    that cannot run reads as `unreadable`, which is safe, and useless.

    `wsl_distro` names the daemon holding the container. It is not optional
    detail on a probe: asked of the wrong daemon this reads `absent` for a
    fully populated database, which is the answer that leads to a reset.

    The two seams default to the real docker functions and are parameters so a
    test can answer for a server that does not exist.
    """
    return sqlplan.MarkerGate(
        sql_plan(),
        container=docker_ctl.SPEC.db,
        client=docker_ctl.DB_CLIENT,
        password=db_root_password,
        schemas=schemas(),
        sql_query=sql_query,
        exec_stdin=exec_stdin,
        wsl_distro=wsl_distro,
    )


def import_gate(
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
    sql_query: sqlplan.SqlQuery = docker.sql_query,
    exec_stdin: sqlplan.ExecStdin = docker.exec_stdin,
) -> tuple[docker.ImportProbe, docker.ResetUnfinished]:
    """The `(probe, reset)` pair for the install at `server_dir`.

    The shape `Controller.__init__` takes, and the CMaNGOS counterpart of
    `install_wiring.import_gate_for()` — which returns `(None, None)` for this
    entry, because the probe it builds looks for `acore_*` schemas by name and
    attaching it to a CMaNGOS install told a healthy server its databases were
    never imported (review, 2026-08-23).

    The password is read from the install rather than passed in: this entry's
    plan is `generated`, so the installer minted one and wrote it to the file
    the plan names, and there is no fixed value a caller could hold.

    **An unreadable password file does not make this raise.** `ImportProbe`'s
    contract is that it never raises — its callers are a five-second status
    path and a button's visibility, neither of which has anywhere to put an
    exception — so the probe returned in that case answers `unreadable` with
    the reason, which is both true and not `repairable`. `reset` is the
    opposite: it destroys, so it refuses loudly instead.
    """
    password = entry().install.db_password(server_dir)
    if password is None:
        unknown = (
            f"this install's database password is not knowable: "
            f"{entry().install.password.file} could not be read in {server_dir}, so its "
            "databases cannot be asked what state they are in"
        )
        logger.warning(unknown)

        def unreadable() -> docker.ImportState:
            return docker.ImportState("unreadable", unknown)

        def refuse() -> tuple[str, ...]:
            raise InstallerError(f"Nothing was dropped: {unknown}.")

        return unreadable, refuse

    built = gate(password, wsl_distro=wsl_distro, sql_query=sql_query, exec_stdin=exec_stdin)
    return built.probe, built.reset
