"""Database backup and restore for a Vanilla install: the shared machinery, this game's names.

Nothing about dumping, verifying, planning or restoring is game-specific, and
none of it is repeated here. `controller_wow_wotlk/maintenance.py` owns the
whole of it — the `.partial`-then-rename rule, `verify_dump()`'s three checks,
the plan/token/re-census restore, the in-flight marker and the carry-forward of
an earlier interrupted restore — and every per-game fact already reaches it as
an argument (`spec=`, `core_databases=`).

Two of those arguments are what this module exists to supply:

* **`spec`** — this install's containers. The refusals that keep a restore from
  running under a live worldserver are by container NAME, so a WotLK spec would
  ask whether `ac-worldserver` is up and cheerfully restore over a running
  `vanilla-mangosd`.
* **`core_databases`** — `realmd`, `characters`, `mangos`. The module-level
  default is AzerothCore's, and it is not a cosmetic difference: a CMaNGOS
  install reported "expected but absent: acore_auth, acore_characters,
  acore_world" on every successful backup, on a dump that had taken everything
  it had (Discord report, 2026-08-26). The alarm is only worth having if it
  names schemas this install could plausibly have.

The database CLIENT is the third CMaNGOS difference, and it takes BOTH
answers. `DockerMysql` resolves it through `apply.mysql_client()`, which asks
the container `command -v mysqldump || command -v mariadb-dump` and caches what
it says; that is what makes a dump work against `mariadb:11`, which ships
neither `mysql` nor `mysqldump`. `mysql_for()` also passes
`docker_ctl.DB_CLIENT`, what the entry DECLARES — which changes nothing while
the container can be asked, and decides it when it cannot. This said "needs no
argument" until 2026-09-03, on the strength of the probe alone; the unbound
fallback was `mysql`, so the one path that was left to a guess guessed the
AzerothCore answer on a MariaDB server (review).

`verify_dump()`'s banner check already admits MariaDB's sandbox directive
(`/*M!999999\\- enable the sandbox mode */`), measured on a live Tortoise server
in 2026-08-28 — which is the same client family this game runs, and the reason
a backup taken here identifies itself as a dump at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from yulon.controller_wow_vanilla import docker_ctl, entry

# The machinery, imported rather than copied.
from yulon.controller_wow_wotlk import maintenance as shared
from yulon.controller_wow_wotlk.maintenance import (
    BACKUP_SUBDIR as BACKUP_SUBDIR,
)
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

CORE_DATABASES: tuple[str, str, str] = entry().core_databases()
"""The three schemas whose absence is an alarm, in THIS core's names.

`realmd`, `characters`, `mangos` — read off the entry, never spelled here. The
entry's `databases.extra` (`logs`) is deliberately not among them: a backup
takes every schema the server has, and this tuple is only the list whose
ABSENCE is worth reporting.
"""


def mysql_for(db_root_password: str, *, wsl_distro: str | None = None) -> DockerMysql:
    """A `MysqlDocker` bound to this install's database container.

    `wsl_distro` because the dump goes through `docker exec` into whichever
    daemon holds the container. It is asked separately of the census below,
    which is a different question to the same daemon (`docker ps`), and the two
    were once answered by different daemons — a Console tab that streamed while
    Back up now said "Docker could not be found on this machine".
    """
    return DockerMysql(
        docker_ctl.SPEC.db,
        db_root_password,
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
    """Dump every database this install has, with this game's spec and core names.

    Every argument and every refusal is the shared `backup()`'s; see it for
    what `only` and `label` are for and for what happens to a dump that fails
    part way. `spec` and `core_databases` are supplied here and are not
    overridable, because a caller that wanted a different install's containers
    wants a different game's package.
    """
    return shared.backup(
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
    """What restoring `backup_file` into this install would do — nothing is written.

    The refusal that matters is by container name: `vanilla-mangosd` or
    `vanilla-realmd` running means no restore, because a live worldserver holds
    character state in memory and writes it back within minutes. That is why
    the spec is this game's and not the default.
    """
    return shared.plan_restore(
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

    `confirm` must be `plan.token`. The plan is re-censused inside the shared
    `restore()` with the same spec passed here, so a plan built while this
    install was down does not authorise a restore once it is up again.
    """
    return shared.restore(
        plan,
        mysql,
        confirm=confirm,
        spec=docker_ctl.SPEC,
        running=running,
        wsl_distro=wsl_distro,
        now=now,
    )
