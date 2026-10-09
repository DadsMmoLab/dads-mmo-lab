"""Database backup and restore for Centurion: the shared engine, this entry's names.

`controller_wow_wotlk/maintenance.py` asks the server which schemas it has, dumps
each with `--routines --triggers --events` (`DockerMysql._dump_argv`), verifies the
dump before it wears a backup's name, and restores only with the worldserver
stopped. Centurion needs all three flags: its auth schema carries six triggers
(the `audit_dml_log` writers on `realmlist`, `account_banned`, `ip_banned`), its
characters schema two procedures and its world schema nine (facts §2). The dumps it
was imported from carry no DEFINER, so the import ran them as root, and the
restore runs as root too.

What this module supplies is the entry's container spec and its three core
schemas, so a backup does not report `acore_*` "expected but absent".
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from yulon.catalog.catalog import CatalogEntry
from yulon.controller_wow_wotlk import maintenance as _shared
from yulon.controller_wow_wotlk.maintenance import BackupReport as BackupReport
from yulon.controller_wow_wotlk.maintenance import DockerMysql as DockerMysql
from yulon.controller_wow_wotlk.maintenance import MysqlDocker as MysqlDocker
from yulon.controller_wow_wotlk.maintenance import RestorePlan as RestorePlan
from yulon.controller_wow_wotlk.maintenance import RestoreReport as RestoreReport
from yulon.controller_wow_wotlk.maintenance import RunningNames as RunningNames


def mysql_for(
    entry: CatalogEntry, db_root_password: str, *, wsl_distro: str | None = None
) -> DockerMysql:
    """A `DockerMysql` bound to this entry's database container and declared client."""
    block = entry.install.native
    return DockerMysql(
        entry.container_spec().db,
        db_root_password,
        wsl_distro=wsl_distro,
        client=block.db.client if block is not None else None,
    )


def backup(
    entry: CatalogEntry,
    server_dir: Path,
    mysql: MysqlDocker,
    *,
    only: Sequence[str] | None = None,
    label: str | None = None,
    running: RunningNames | None = None,
    wsl_distro: str | None = None,
    now: datetime | None = None,
) -> BackupReport:
    """Dump every database this install has, with this entry's spec and core names."""
    return _shared.backup(
        server_dir,
        mysql,
        game=_shared.game_of(entry),
        only=only,
        label=label,
        spec=entry.container_spec(),
        core_databases=entry.core_databases(),
        running=running,
        wsl_distro=wsl_distro,
        now=now,
    )


def plan_restore(
    entry: CatalogEntry,
    backup_file: Path,
    server_dir: Path,
    *,
    running: RunningNames | None = None,
    wsl_distro: str | None = None,
    can_start_database: bool = False,
) -> RestorePlan:
    """What restoring `backup_file` would do, censused against THIS entry's containers.

    `can_start_database` is the shared planner's (T216): passed on unchanged.
    """
    return _shared.plan_restore(
        backup_file,
        server_dir,
        game=_shared.game_of(entry),
        spec=entry.container_spec(),
        running=running,
        wsl_distro=wsl_distro,
        can_start_database=can_start_database,
    )


def restore(
    entry: CatalogEntry,
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
        game=_shared.game_of(entry),
        confirm=confirm,
        spec=entry.container_spec(),
        core_databases=entry.core_databases(),
        running=running,
        wsl_distro=wsl_distro,
        now=now,
    )
