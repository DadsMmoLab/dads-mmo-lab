"""Does this Tortoise install's database look imported? — asked with the evidence this core has.

The WotLK module answering the same question is not a template for this one, and
copying it would have produced a probe that is always wrong. It reads
AzerothCore's own bookkeeping tables, `updates` and `updates_include`, which
that core's updater writes and which no CMaNGOS-lineage core has at all: on a
finished Tortoise install neither table exists in any schema, so that probe
reports `partial` — "they were never finished" — for a server that is complete.
Attached to a `Controller`, that answer lights the Repair button, whose action
drops schemas.

The evidence here is a marker row the installer itself writes at the end of a
successful import: `sqlplan.MARKER_TABLE` in the plan's `marker_db`, written
only after the plan's `verify` rules passed. `sqlplan.MarkerGate` is the probe
that reads it, and it is the probe the install engine already uses for this
family — so an install press and this module ask one question with one
implementation, and cannot disagree about whether an install finished.

Everything this module supplies is data off the entry: the plan, the database
container, the client binary (`mariadb`, from `install.native.db.client` — the
one place in this package that names a client at all), and the identity mapping
over the plan's schema names.

**No reset is exposed, deliberately.** `MarkerGate.reset()` exists and drops the
plan's schemas, and the install spine calls it as part of re-running an import.
Re-exporting it from the controller package would offer a destructive action
with nothing behind it: this entry names no one-shot import service, so nothing
in this package can re-fill what a reset empties. The route back for a broken
install is the installer, which owns both halves.

Two things a reader should know before trusting `partial` here:

* the marker table is written by THIS app's install engine. An install created
  some other way — an older bash installer, a hand-built stack — has no marker
  however complete it is, and this probe calls that `partial`. It is safe
  because nothing acts on it (see above), and it is why the state is reported
  and not acted upon.
* `populated` is checked after `imported` on purpose. A marker is proof; a row
  count is not, and a module that seeds accounts would otherwise turn a
  finished import into "somebody's server".
"""

from __future__ import annotations

from pathlib import Path

from yulon import docker
from yulon.catalog.families import sqlplan

# Re-exported so a caller reading a state does not have to name the marker table
# through two packages to say where the evidence lives.
from yulon.catalog.families.sqlplan import (
    MARKER_TABLE as MARKER_TABLE,
)
from yulon.catalog.installer import InstallerError
from yulon.controller_wow_tortoise import docker_ctl, game


def import_state(server_dir: Path, *, wsl_distro: str | None = None) -> docker.ImportState:
    """What state this install's schemas are in. Never raises.

    Every way of not knowing comes back as `unreadable` carrying the reason —
    an unreadable password file, a catalog the gate refuses to be built from, a
    database that will not answer. That is the same fail-closed direction the
    gate itself takes, and it matters here for one reason: `unreadable` is not
    `repairable`, so nothing offers an action on the strength of a question
    nobody answered.
    """
    password = game.entry().install.db_password(server_dir)
    if password is None:
        plan = game.entry().install.password
        return docker.ImportState(
            "unreadable",
            f"this install's database password is not knowable: {plan.file} could not be read "
            f"in {server_dir}, so its databases could not be asked anything",
        )
    try:
        gate = marker_gate(password, wsl_distro=wsl_distro)
    except InstallerError as exc:
        # `MarkerGate.__init__` refuses a plan it cannot quote a seeded account
        # name from. That is a catalog error, and it must not reach a status
        # poll as an exception: this function is called on a timer and by a
        # button's visibility, and neither has anywhere to put one.
        return docker.ImportState("unreadable", str(exc))
    return gate.probe()


def marker_gate(db_root_password: str, *, wsl_distro: str | None = None) -> sqlplan.MarkerGate:
    """The `MarkerGate` for this install, built from the entry.

    Separate from `import_state()` so a caller that already holds the password —
    or that wants to ask twice without re-reading the file — pays for the gate
    once. The seams are `docker.sql_query` and `docker.exec_stdin`, the same two
    the install engine hands it.

    Raises:
        InstallerError: the SQL plan names something the gate refuses to build
            with. A catalog error in the app, never a state of this machine.
    """
    return sqlplan.MarkerGate(
        game.sql_plan(),
        container=docker_ctl.SPEC.db,
        # From `install.native.db.client`, never the literal `mysql`: MariaDB 11
        # ships no `mysql` binary at all, and this package's only argv that
        # names a client is this one.
        client=game.db().client,
        password=db_root_password,
        schemas=game.plan_schemas(),
        sql_query=docker.sql_query,
        exec_stdin=docker.exec_stdin,
        wsl_distro=wsl_distro,
    )


def import_probe(server_dir: Path, *, wsl_distro: str | None = None) -> docker.ImportProbe:
    """`import_state()` as the no-argument seam `docker.ImportProbe` names.

    Not passed to `TortoiseController` — `controller.controller_for()` says why —
    but this is the shape any caller wanting the answer as a callable needs.
    """

    def probe() -> docker.ImportState:
        return import_state(server_dir, wsl_distro=wsl_distro)

    return probe
