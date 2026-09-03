"""Database backup and restore for a TBC install: the shared engine, this game's names.

`controller_wow_wotlk/maintenance.py` is already game-agnostic where it counts.
It asks the server which schemas it has rather than assuming a list, it verifies
every dump before a file wears a backup's name, and it takes the container spec
and the core schema names as arguments. All of that is imported, not copied.

What this module supplies is those arguments, and one of them closes a reported
bug rather than merely tidying: `CORE_DATABASES` defaults to AzerothCore's
`acore_auth, acore_characters, acore_world`, and a backup taken with that
default on a CMaNGOS install reported "expected but absent: acore_auth,
acore_characters, acore_world" on a dump that had taken everything the server
had (Discord report, 2026-08-26). The wrappers below bind
`entry.core_databases()` and this game's `ContainerSpec` so a caller cannot
reach the engine without them.

**The dump client.** `DockerMysql` resolves `mysql`/`mysqldump` by asking the
container which name it answers to, which lands on `mariadb`/`mariadb-dump` for
the `mariadb:11` image this entry names — and `mariadb:11` ships neither of the
`mysql*` symlinks, so the answer matters. That resolution is a probe, not data,
so `mysql_for()` now also hands it `docker_ctl.DB_CLIENT` and the probe still
wins where it can answer.

The UNVERIFIED note that stood here asked whether the probe's fallback (the
first candidate) is ever reached on this image. It no longer decides anything:
the fallback used to be `mysql` and is now the declared `mariadb`, so the two
paths agree and the question is closed by construction rather than by
measurement (review, 2026-09-03).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from yulon.controller_wow_tbc import docker_ctl
from yulon.controller_wow_wotlk import maintenance as _shared
from yulon.controller_wow_wotlk.maintenance import (
    BACKUP_SUBDIR as BACKUP_SUBDIR,
)
from yulon.controller_wow_wotlk.maintenance import (
    CLIENT_CACHE_NOTE as CLIENT_CACHE_NOTE,
)
from yulon.controller_wow_wotlk.maintenance import (
    SYSTEM_SCHEMAS as SYSTEM_SCHEMAS,
)
from yulon.controller_wow_wotlk.maintenance import (
    BackupReport as BackupReport,
)
from yulon.controller_wow_wotlk.maintenance import (
    DockerMysql as DockerMysql,
)
from yulon.controller_wow_wotlk.maintenance import (
    Dump as Dump,
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
from yulon.controller_wow_wotlk.maintenance import (
    backups_dir as backups_dir,
)
from yulon.controller_wow_wotlk.maintenance import (
    forget_interrupted_restore as forget_interrupted_restore,
)
from yulon.controller_wow_wotlk.maintenance import (
    interrupted_restore as interrupted_restore,
)
from yulon.controller_wow_wotlk.maintenance import (
    marker_path as marker_path,
)
from yulon.controller_wow_wotlk.maintenance import (
    server_databases as server_databases,
)
from yulon.controller_wow_wotlk.maintenance import (
    verify_dump as verify_dump,
)

CORE_DATABASES: tuple[str, ...] = docker_ctl.ENTRY.core_databases()
"""`realmd`, `characters`, `mangos` — the three whose absence is worth an alarm.

`logs` is not among them, and that is the entry's answer rather than an
oversight: `core_databases()` reports auth/characters/world only, and a missing
`logs` costs a user nothing they made. It is still dumped, because a backup
takes every schema the server has rather than a list.
"""


def mysql_for(root_password: str, *, wsl_distro: str | None = None) -> DockerMysql:
    """A `DockerMysql` bound to this install's database container (`tbc-db`).

    `wsl_distro` travels with the password: `docker exec -e MYSQL_PWD` forwards
    a variable that arrives EMPTY inside a distro unless `WSLENV` names it, and
    the client then reports an authentication failure against a healthy
    database.
    """
    return DockerMysql(
        docker_ctl.SPEC.db,
        root_password,
        wsl_distro=wsl_distro,
        client=docker_ctl.DB_CLIENT,
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
    """Dump every database this install has, with this game's spec and core names bound."""
    return _shared.backup(
        server_dir,
        mysql,
        only=only,
        label=label,
        spec=docker_ctl.SPEC,
        core_databases=CORE_DATABASES,
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
    """What restoring `backup_file` would do, censused against this game's containers.

    The spec is what makes the "the worldserver is running" refusal true here:
    asked with AzerothCore's, it would look for `ac-worldserver` and find a live
    `tbc-mangosd` perfectly acceptable.
    """
    return _shared.plan_restore(
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
    """Overwrite the databases `plan.backup` names. This destroys player data."""
    return _shared.restore(
        plan,
        mysql,
        confirm=confirm,
        spec=docker_ctl.SPEC,
        running=running,
        wsl_distro=wsl_distro,
        now=now,
    )
