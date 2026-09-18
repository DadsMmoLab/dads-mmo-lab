"""Module / ALE / mod / keg management for WotLK, driven by JSON manifests (roadmap 2.3).

This is the per-game binding only: the WotLK game id, where its bundled
manifests live, the GitHub location they are refreshed from, and the
AzerothCore database container the SQL steps run in. The behavior —
loading/validating (`yulon.manifest_store`), applying (`yulon.apply`) — is
shared and game-agnostic (style-guide §4). Nothing here knows what any
particular module does; that is all in `manifests/wow-wotlk/` (§3).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Protocol

from yulon import docker, module_source, platform, resources
from yulon.apply import (
    Applier,
    ApplyReport,
    ComposeDbc,
    DbcCopier,
    DockerSql,
    FolderSource,
    ModuleUpdate,
    SqlRunner,
)
from yulon.apply import module_updates as apply_updates
from yulon.controller_wow_wotlk import docker_ctl
from yulon.git import BehindReader, Git, RunnerGit

# Explicit re-export (the `as` form is what mypy's --no-implicit-reexport asks for):
# the value lives in `install_wiring` since 7.1, and this name stays so no importer moves twice.
from yulon.install_wiring import DEFAULT_DB_ROOT_PASSWORD as DEFAULT_DB_ROOT_PASSWORD
from yulon.log import get_logger
from yulon.manifest import Manifest, ManifestType
from yulon.manifest_store import (
    HttpGet,
    ManifestFetcher,
    ManifestStore,
    RefreshResult,
    load_manifest,
    urllib_get,
)

# Explicit re-export: copying a folder is not game-bound in any way, so the
# WotLK binding is the same function under this package's name rather than a
# wrapper that could come to disagree with it (style-guide §4).
from yulon.module_source import copy_folder as copy_folder

logger = get_logger(__name__)

GAME = "wow-wotlk"

# The bundled manifest tree that ships with the app (source tree or PyInstaller bundle).
BUNDLED_MANIFESTS_DIR = resources.manifests_dir()

# Raw-GitHub prefix that `<game>/<family>.json` is joined onto when refreshing.
MANIFEST_BASE_URL = (
    "https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/pylauncher/manifests"
)


def load_module(manifest_path: Path) -> Manifest:
    """Load and validate a single manifest file into a typed `Manifest`.

    Raises `yulon.manifest_store.ManifestError` for a missing/unreadable file and
    `pydantic.ValidationError` for anything the schema rejects — including a
    `source.repo` outside the allow-list (README §3a).
    """
    return load_manifest(manifest_path)


def user_manifests_dir(config_dir: Path | None = None) -> Path:
    """Where manifests this app DERIVED are kept: `<config_dir()>/manifests/user/`.

    `platform.config_dir()`'s own docstring already reserves that directory for
    "cached manifests", and `credentials/`, `logs/`, `downloads/` and `state.json`
    live beside it. Resolved in a function rather than frozen into a module
    constant at import time, which is what every sibling that writes under the
    config directory does (`state.py`, `dbsecret.py`, `channel_setup.py`) — the
    parameter is how a test gets a `tmp_path` there.
    """
    root = config_dir if config_dir is not None else platform.config_dir()
    return root / "manifests" / "user"


def shipped_ids(kind: ManifestType = "module") -> tuple[str, ...]:
    """The ids this app SHIPS for that family — the bundled index alone, never the user layer.

    What the shadow rule is answered against. Asked of the bundled store directly
    rather than of `store()`, because `store()` is the merged view and a custom
    module already in it would then be reported as shipped and refuse its own
    re-derivation.
    """
    return ManifestStore(BUNDLED_MANIFESTS_DIR, GAME).load_index(kind).items


def store(root: Path = BUNDLED_MANIFESTS_DIR, user_root: Path | None = None) -> ManifestStore:
    """The WotLK manifest store over `root`, with the user layer over it.

    `root` is the bundled tree by default (or a refreshed cache); the second layer
    is `user_manifests_dir()` unless a caller names one. Every existing call site
    gets the merged view with no change, which is the point: the Modules tab reads
    `store().load_all("module")` and a custom module appears in that list on the
    next start without the view learning anything new. A caller that wants the
    bundled tree ALONE builds `ManifestStore(root, GAME)` itself — `shipped_ids()`
    is the one that does.
    """
    return ManifestStore(root, GAME, user_root if user_root is not None else user_manifests_dir())


def derive_link(text: str) -> Manifest:
    """A WotLK module manifest for the link `text`, or `module_source.DeriveError`.

    Minimal: what a link can be known to say before it is cloned. `complete()`
    below is the other half, run once the clone is on disk.
    """
    return module_source.derive_link(text, GAME, today=date.today(), shipped_ids=shipped_ids())


def derive_folder(path: Path) -> Manifest:
    """A WotLK module manifest for the folder `path`, or `module_source.DeriveError`."""
    return module_source.derive_folder(path, GAME, today=date.today(), shipped_ids=shipped_ids())


def complete(manifest: Manifest, clone: Path) -> Manifest:
    """Fill `manifest` in from what `clone` holds, PERSIST it, and return it.

    Handed to the applier as its completion hook, so the manifest that is
    persisted is the same one the rest of the install pass acts on and the same
    one the report describes — there is no window in which the file on disk says
    something the run did not do.

    The persist is here rather than in `module_source.complete()` because
    completing is a pure read and persisting is a write, and the applier's hook
    contract is about the manifest, not about the config directory.
    """
    completed = module_source.complete(manifest, clone)
    module_source.persist(user_manifests_dir(), completed, shipped_ids=shipped_ids())
    return completed


def forget(manifest: Manifest) -> bool:
    """Drop `manifest` from the user layer; `True` if there was one to drop.

    Called AFTER a remove returned, never before. `False` for every shipped
    module, which is how the caller learns a shipped one has nothing to forget
    without reading `manifest.origin` itself.
    """
    return module_source.forget(user_manifests_dir(), manifest)


class CustomInstall(Protocol):
    """What `install_custom()` hands back: the two-argument call, plus T47's `replacing`.

    Declared here rather than imported from `yulon.ui.controller_view`, which
    declares the same shape as `CustomModuleInstall`: this package does not
    import the view (style-guide §3, and nothing under `controller_wow_*/`
    names `yulon.ui` today). The two are structurally identical, which is what
    makes the wiring type-check, and a drift between them is a type error at
    the wiring line rather than a silent widening.
    """

    def __call__(
        self, manifest: Manifest, folder: Path | None, *, replacing: bool = False
    ) -> ApplyReport: ...


def install_custom(applier: Applier) -> CustomInstall:
    """The Modules tab's one custom-install seam, over the applier the tab already holds.

    Lane C's deviation D1 from the design (§3.3/§3.5): the view hands over the
    manifest it derived and the folder the user chose (`None` for a link), and
    everything the design listed as `module_copy_folder` and `module_complete`
    is bound HERE — the applier's `folder=` is `FolderSource(path, copy_folder)`
    and its `complete=` is this module's `complete()`, which persists what it
    finished. A view that built `apply.FolderSource` itself would know one thing
    more about the engine than `ui/*_view.py` is allowed to (style-guide §3).

    Over the SAME `Applier` object the tab installs shipped modules with, rather
    than a second one built here from `server_dir`: one applier, one SQL runner,
    one client dir, so a custom module cannot be installed against a database
    or a client the shipped route is not (style-guide §4).
    """

    def install(manifest: Manifest, folder: Path | None, *, replacing: bool = False) -> ApplyReport:
        source = FolderSource(folder, copy_folder) if folder is not None else None
        return applier.install(
            manifest, None, folder=source, complete=complete, replacing=replacing
        )

    return install


def replacement_question(applier: Applier) -> Callable[[Manifest], str | None]:
    """The tab's seam onto `Applier.replacement_question()`, over the SAME applier.

    Over the same object the install runs through, for `install_custom()`'s
    reason: a question answered about one server directory and an install
    carried out against another would be the worst possible version of this.
    """

    def question(manifest: Manifest) -> str | None:
        return applier.replacement_question(manifest)

    return question


def fetcher(cache_root: Path, http: HttpGet = urllib_get) -> ManifestFetcher:
    """A fetcher that mirrors the WotLK manifests from GitHub into `cache_root`."""
    return ManifestFetcher(MANIFEST_BASE_URL, cache_root, http)


def refresh(cache_root: Path, kind: ManifestType, http: HttpGet = urllib_get) -> RefreshResult:
    """Refresh one family of WotLK manifests into `cache_root` (ETag-revalidated)."""
    return fetcher(cache_root, http).refresh(GAME, kind)


SERVER_DATA_DIR = "/azerothcore/env/dist/data"
"""Where AzerothCore's data volume is mounted, in the containers that mount it (T62).

The worldserver reads its DBCs from `<DataDir>/dbc/`, and `DataDir` is this path:
`AC_DATA_DIR: "/azerothcore/env/dist/data"` on `ac-worldserver`, which mounts
`client-data:/azerothcore/env/dist/data/:ro`, while `ac-client-data-init` mounts
the same volume read-write at the same path
(`catalog/installers/wow-wotlk/native/base.yml.tmpl`). A bash-installer server
has the identical mounts under the volume name `ac-client-data`
(`tests/data/wotlk-compose-config-script.json`), and the bash launcher copied
into `<volume>/dbc/` resolved from the worldserver's mount at this destination
(`copy_server_dbc`, `guides/wow-wotlk/wow-manage.sh` on `upstream/main`), as did
the Rust launcher's `client-patch` (`cli/dml` on `origin/rust-main`).
"""


def dbc_copier(server_dir: Path, *, service: str, wsl_distro: str | None = None) -> ComposeDbc:
    """The WotLK `DbcCopier`: DBC files into this install's data volume through `service`.

    `service` is the catalog's `containers.client_data` — the one-shot that
    mounts the volume read-write — handed in by the caller that holds the entry,
    the way `DockerSql` is handed the database container.
    """
    return ComposeDbc(server_dir, service, SERVER_DATA_DIR, wsl_distro=wsl_distro)


def applier(
    server_dir: Path,
    *,
    world_running: Callable[[], bool | None],
    start_database: Callable[[], bool] | None = None,
    git: Git | None = None,
    sql: SqlRunner | None = None,
    client_dir: Path | None = None,
    dbc: DbcCopier | None = None,
    db_root_password: str = DEFAULT_DB_ROOT_PASSWORD,
) -> Applier:
    """An `Applier` for the WotLK install at `server_dir`.

    Defaults to running direct SQL through the WotLK DB container
    (`docker_ctl.SPEC.db`); pass `sql=None` explicitly via a caller that has no
    database to get every SQL step reported as skipped instead.

    `world_running` is REQUIRED, and that is the whole of T7 on this function.
    It is checklist 8.7a's guard (`apply.py`, `_refuse_direct_sql_into_a_running
    _world`), and the seam defaults to absent on `Applier` itself so the engine
    keeps its old behaviour for a caller that has no Docker to ask. Every caller
    HERE has one, and for a day none of them passed it: the guard shipped
    returning at its first line, and T2's press recorded that this tab would
    have written 7 219 rows into a live world without a word. A required keyword
    is why that cannot recur quietly — a new caller does not compile, rather
    than silently opting out. `docker.world_running()` is the real one; a caller
    with no install passes something cheap.

    `start_database` is optional because absent means the behaviour this route
    had before T7 — the one T2 measured ending in `container ... is not
    running`. Callers that can reach Docker pass `docker.start_database`, which
    is what makes the refusal's own *"Press Stop, then install again"* a thing
    that succeeds.
    """
    runner: SqlRunner | None = sql
    if runner is None:
        runner = DockerSql(docker_ctl.SPEC.db, db_root_password, client=docker_ctl.DB_CLIENT)
    return Applier(
        server_dir,
        git=git,
        sql=runner,
        client_dir=client_dir,
        dbc=dbc,
        world_running=world_running,
        start_database=start_database,
    )


def module_updates(
    server_dir: Path,
    *,
    git: BehindReader | None = None,
) -> tuple[ModuleUpdate, ...]:
    """How far behind each module installed at `server_dir` is (checklist 8.7a).

    The per-game binding only: which folder the clones live in is `apply.py`'s,
    and the counting is `git.commits_behind()`'s. What is WotLK's here is the
    branch table — each module's `source.branch` out of this game's own manifest
    store, because the manifest's branch is what an update would fetch. Every
    one of the 21 shipped `wow-wotlk` module manifests omits it, so the table is
    ordinarily empty and every fetch is `origin HEAD`; it is built anyway,
    because the first manifest that names a branch must not silently be counted
    against the wrong ref.

    A module on disk that the store has never heard of still gets a row — it is
    installed, and `docker.allowed_modules()` will hand its name to the importer
    whatever the catalog thinks.

    Costs one `git fetch` per installed checkout, so it belongs behind a control
    the user pressed rather than on the status poll.
    """
    reader: BehindReader = git if git is not None else RunnerGit()
    branches: dict[str, str | None] = {}
    try:
        for manifest in store().load_all("module"):
            branches[manifest.id] = manifest.source.branch if manifest.source else None
    except Exception as exc:  # boundary: a broken manifest tree must not stop the count
        logger.warning(f"could not read the wow-wotlk manifests for their branches: {exc}")
    return apply_updates(server_dir, git=reader, branches=branches)


def apply_module(
    manifest: Manifest,
    server_dir: Path,
    values: Mapping[str, str] | None = None,
    *,
    world_running: Callable[[], bool | None],
    start_database: Callable[[], bool] | None = None,
    client_dir: Path | None = None,
) -> ApplyReport:
    """Install `manifest` into the WotLK server at `server_dir` (the roadmap 2.3 entry point).

    Returns the `ApplyReport`; the caller decides about the rebuild/restart it
    names (call down / signal up — this function never touches Docker's
    lifecycle itself).

    `world_running` is required for `applier()`'s reason and passed straight
    through: an entry point that answered the guard's question on its caller's
    behalf would be the one construction site with no seam, which is the defect
    T7 closed.
    """
    return applier(
        server_dir,
        world_running=world_running,
        start_database=start_database,
        client_dir=client_dir,
    ).install(manifest, values)


def apply_module_sql(
    server_dir: Path,
    *,
    output: Callable[[str], None] | None = None,
    wsl_distro: str | None = None,
) -> docker.AttachedRun:
    """Run the AzerothCore importer over `server_dir` for the modules that are ON DISK.

    The route that was missing, and the reason it had to exist. `apply_module()`
    installs a C++ module by cloning it and activating its conf; its
    `data/sql/db-world/*.sql` is left "to `ac-db-import` on next start", which
    is a promise nothing in this app kept — `docker.start_staged()` names the
    three long-running services so a Start can never reach the importer, and
    `docker.repair_import()` refuses any install whose databases are already
    complete. Measured on yulon-ubuntu 2026-09-07: even when the importer IS
    run, it logs `Loading modules: all` and applies nothing, because `"all"` is
    the list compiled into the image and not the folder on disk
    (`docker.allowed_modules()` has the numbers).

    Per-game and nothing else: which compose service imports is
    `docker_ctl.SPEC.import_service`, asked rather than respelled here
    (style-guide §3). Every refusal — ownership, strangers, and 8.7a's "not
    while the world is running" — belongs to `docker.apply_module_sql()`, so
    this route and the repair cannot come to disagree about whose install it is
    or when it is safe to write.

    `output` receives the importer's lines as they arrive; the `>> Applying
    update <file>.sql` lines among them are the only evidence that a module's
    SQL was applied, so a caller reporting to a user should pass one.

    `wsl_distro` says which daemon this install's containers are inside, and it
    is here because the route below takes one and this binding is what a caller
    holding the install reaches it through. Without it the whole run — the
    `compose config` probe, the database start and the importer itself — goes
    to the Windows host's Docker, which has never heard of `ac-database`, and
    the user reads "Docker could not be found on this machine" from a tab whose
    other buttons are working (the shape of the 2026-08-27 Discord report).
    `test_controller_view.py`'s seam scan is what found this one, on the day
    its first call site was written.
    """
    return docker.apply_module_sql(
        docker_ctl.SPEC, server_dir, output=output, wsl_distro=wsl_distro
    )
