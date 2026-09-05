"""Backup and restore for a Tortoise install: the shared engine, bound to this core's facts.

Nothing about dumping and loading a MySQL-family database is game-specific, and
none of it is repeated here. `controller_wow_wotlk/maintenance.py` holds the
whole of it — the `.partial`-then-verify write, the dump header/trailer check
that already had to learn MariaDB's sandbox directive from a live Tortoise
server (2026-08-28), the restore's plan/re-census/safety-dump/marker sequence,
and the interrupted-restore marker. Every one of those functions already takes
the two facts that ARE per-game as arguments.

Those two facts are what this module supplies, from data:

* `spec` — which containers must be up (`tortoise-db`) and which must be down
  (`tortoise-mangosd`, `tortoise-realmd`) before a restore may run. The shared
  default is AzerothCore's, so a restore planned with it would census the wrong
  three names: a running Tortoise worldserver would not be seen, and the
  restore would be overwritten by the server's own saves minutes later.
* `core_databases` — `tw_logon`, `tw_char`, `tw_world`. The shared default is
  `acore_*`, which is how a Tortoise backup reported all three missing on a
  dump that had taken everything (`CatalogEntry.core_databases` records it).

The database client binary IS bound here, as of 2026-09-03. It was not, and
the paragraph that stood here argued it did not need to be: the probe asks the
container, and this entry pins `mariadb:10.6`, which still ships the `mysql*`
symlinks. Both halves were true and the conclusion did not follow. The probe
answers only when it can RUN; with no docker CLI, a timeout or an OSError,
`mysql_client()` returns its first candidate, and unbound that is `mysql` --
a binary no MariaDB 11 image has. `apply.DockerSql` had carried the declared
client since 7.9, so the same question had two answers in one codebase, and
this side's answer was the one that broke on the image bump the old paragraph
named as the thing to watch for (review).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from yulon.controller_wow_tortoise import docker_ctl, game

# The engine's own vocabulary, re-exported so a caller of this package types and
# catches against one import.
from yulon.controller_wow_wotlk.maintenance import (
    CLIENT_CACHE_NOTE as CLIENT_CACHE_NOTE,
)
from yulon.controller_wow_wotlk.maintenance import (
    BackupReport as BackupReport,
)
from yulon.controller_wow_wotlk.maintenance import (
    DockerMysql as DockerMysql,
)
from yulon.controller_wow_wotlk.maintenance import (
    InterruptedRestore as InterruptedRestore,
)
from yulon.controller_wow_wotlk.maintenance import (
    MaintenanceError as MaintenanceError,
)
from yulon.controller_wow_wotlk.maintenance import (
    MysqlDocker as MysqlDocker,
)
from yulon.controller_wow_wotlk.maintenance import (
    RestorePlan as RestorePlan,
)
from yulon.controller_wow_wotlk.maintenance import (
    RestoreReport as RestoreReport,
)
from yulon.controller_wow_wotlk.maintenance import (
    RunningNames as RunningNames,
)
from yulon.controller_wow_wotlk.maintenance import backup as _backup
from yulon.controller_wow_wotlk.maintenance import (
    backups_dir as backups_dir,
)
from yulon.controller_wow_wotlk.maintenance import (
    forget_interrupted_restore as forget_interrupted_restore,
)
from yulon.controller_wow_wotlk.maintenance import (
    interrupted_restore as interrupted_restore,
)
from yulon.controller_wow_wotlk.maintenance import plan_restore as _plan_restore
from yulon.controller_wow_wotlk.maintenance import restore as _restore
from yulon.controller_wow_wotlk.maintenance import (
    server_databases as server_databases,
)
from yulon.controller_wow_wotlk.maintenance import (
    verify_dump as verify_dump,
)


def mysql_for(db_root_password: str, *, wsl_distro: str | None = None) -> DockerMysql:
    """A `MysqlDocker` bound to this install's database container."""
    return DockerMysql(
        docker_ctl.SPEC.db,
        db_root_password,
        wsl_distro=wsl_distro,
        client=game.db().client,
    )


def backup(
    server_dir: Path,
    mysql: MysqlDocker,
    *,
    only: Sequence[str] | None = None,
    label: str | None = None,
    running: RunningNames | None = None,
    wsl_distro: str | None = None,
    now: datetime | None = None,
) -> BackupReport:
    """Dump every database this install has into `backups_dir(server_dir)`.

    `spec` and `core_databases` are not parameters here: they are the two facts
    this package exists to supply, and a caller free to pass AzerothCore's is a
    caller that can produce the alarm this binding removes. Everything else is
    the shared function's, including `only` (the take-a-backup-before-this-SQL
    case) and the guarantee that a file wearing a backup's name has passed
    `verify_dump()`.

    Raises:
        MaintenanceError: the database container is not running, `only` named a
            database this server does not have, there was nothing to dump, or a
            dump failed or came back untrustworthy.
    """
    return _backup(
        server_dir,
        mysql,
        only=only,
        label=label,
        spec=docker_ctl.SPEC,
        core_databases=game.core_databases(),
        running=running,
        wsl_distro=wsl_distro,
        now=now,
    )


def plan_restore(
    backup_file: Path,
    server_dir: Path,
    *,
    running: RunningNames | None = None,
    wsl_distro: str | None = None,
) -> RestorePlan:
    """Work out what restoring `backup_file` into this install would do, without doing it.

    Refusals are collected rather than raised, so the caller can show all of
    them at once. The census is this game's containers, which is what makes
    "the worldserver is running" a refusal that can actually fire here.
    """
    return _plan_restore(
        backup_file,
        server_dir,
        spec=docker_ctl.SPEC,
        running=running,
        wsl_distro=wsl_distro,
    )


def restore(
    plan: RestorePlan,
    mysql: MysqlDocker,
    *,
    confirm: str,
    running: RunningNames | None = None,
    wsl_distro: str | None = None,
    now: datetime | None = None,
) -> RestoreReport:
    """Overwrite the databases `plan.backup` names. This destroys player data.

    `confirm` must be `plan.token`, and the plan is re-censused inside: a plan
    built while the server was down does not authorise a restore once it is up.
    Every table the backup holds is replaced and every table it does not hold is
    left alone — a merge, not a return to the state the backup describes, which
    the shared function measured and says so.

    Raises:
        MaintenanceError: the confirmation does not match, the plan no longer
            holds, the safety dump failed, or the load failed.
    """
    return _restore(
        plan,
        mysql,
        confirm=confirm,
        spec=docker_ctl.SPEC,
        core_databases=game.core_databases(),
        running=running,
        wsl_distro=wsl_distro,
        now=now,
    )
