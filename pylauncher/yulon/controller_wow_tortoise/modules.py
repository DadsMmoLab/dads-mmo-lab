"""Module / mod management for Tortoise, driven by JSON manifests (roadmap 8.7d).

The per-game binding only — the TBC sibling's shape, with this fork's facts:
the `wow-tortoise` game id, where its bundled manifests live, the GitHub
location they are refreshed from. Loading and validating
(`yulon.manifest_store`) and applying (`yulon.apply`) are shared and
game-agnostic (style-guide §4).

**What a shipped item is here.** Three shapes, none of them a compiled module:

* a **configuration activation** — `conf[].keys` written into
  `etc/mangosd.conf`, which the installer materialises out of the image;
* a **SQL mod** — `sql[].statement` run against `tw_world`; or
* a **client add-on** cloned into `sql_scripts/clones/` and copied into the
  ready-to-play client (TortoiseBots Manager, Tortoise GM Manager).

The core DOES compile modules from `modules/<name>/src/` (`modules/README.md`,
`ConfigureModules.cmake`; this docstring said it did not until T596). None is
shipped, and a module from outside is a later Yu'lon: what a player can bring
today is an add-on or a database package (`custom.py`, T596).

**What is NOT inherited from TBC, and this is the whole reason 8.7d is its own
box.** Three facts were measured against this fork's own source on m910q,
2026-09-08, and each of them contradicts the sibling:

1. **There is no `.server motd`.** TBC's gate manifest was chosen because
   `.server motd` states the value back on the console. This fork's
   `serverCommandTable` is `corpses / exit / idlerestart / idleshutdown / info /
   resetallraids / restart / shutdown` (`src/game/Chat/Chat.cpp:700-711`) and
   `motd` appears in no command table at all. The item whose key this server
   DOES report is `perf-report`: `.perf intervalreport` prints
   `Performance report interval is <n>` from the conf key `Perf.ReportInterval`
   (`Commands.cpp:19146-19154`, command row `Chat.cpp:837` with
   `allowConsole = true`).
2. **`world` is `tw_world`**, not `mangos` and not `acore_world`, and this
   install's database password is generated into `<server_dir>/.db_password` —
   so `sql` is REQUIRED here exactly as it is on TBC, and for the same closed
   bug: a second derivation of a password is how a runner authenticates with the
   wrong one.
3. **This fork runs its own database auto-updater at every startup**, inside the
   worldserver, and cancels the world with `exit(1)` on a single failed
   migration. `controller_wow_tortoise.autoupdate` is the guard checklist 2504
   asks for, and `applier()` below returns a `GuardedApplier` rather than a
   plain `Applier` so the object the Modules tab holds is the guarded one. That
   is the only structural difference from the TBC binding.

`modules`/`ale`/`kegs` indexes exist and are empty: "there are none" is a fact
worth writing down, and without the files the Modules tab prints
`!! could not load modules: manifest file missing` for each of the three.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from yulon import module_source, resources
from yulon.apply import (
    CLONE_DIRS,
    Applier,
    ApplyReport,
    CountingGit,
    FolderSource,
    ModuleUpdate,
    SqlBackup,
    SqlRunner,
    cached_module_update,
    cached_module_updates,
    clone_release,
)
from yulon.catalog.native import read_state
from yulon.catalog.upstream import Comparison, Release, github_slug
from yulon.catalog.upstream import cached_row as upstream_cached_row
from yulon.controller_wow_tortoise import autoupdate, custom
from yulon.controller_wow_tortoise.autoupdate import Arming, GuardedApplier
from yulon.controller_wow_wotlk.maintenance import BackupReport, backups_dir

# Explicit re-exports: the user layer's place, the copy and the replace question
# are not game-bound, so this binding names the same functions WotLK's does.
from yulon.controller_wow_wotlk.modules import CustomInstall as CustomInstall
from yulon.controller_wow_wotlk.modules import replacement_question as replacement_question
from yulon.controller_wow_wotlk.modules import user_manifests_dir as user_manifests_dir
from yulon.git import Git, is_behind
from yulon.log import get_logger
from yulon.manifest import Db, Manifest, ManifestType
from yulon.manifest_store import (
    HttpGet,
    ManifestFetcher,
    ManifestStore,
    RefreshResult,
    load_manifest,
    urllib_get,
)
from yulon.module_source import copy_folder

logger = get_logger(__name__)

GAME = "wow-tortoise"

# The bundled manifest tree that ships with the app (source tree or PyInstaller bundle).
BUNDLED_MANIFESTS_DIR = resources.manifests_dir()

# Raw-GitHub prefix that `<game>/<family>.json` is joined onto when refreshing.
MANIFEST_BASE_URL = (
    "https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/pylauncher/manifests"
)


def load_module(manifest_path: Path) -> Manifest:
    """Load and validate a single manifest file into a typed `Manifest`."""
    return load_manifest(manifest_path)


def store(root: Path = BUNDLED_MANIFESTS_DIR, user_root: Path | None = None) -> ManifestStore:
    """The Tortoise manifest store over `root`, with the user layer over it (T596).

    `root` is the bundled tree by default (or a refreshed cache). The second
    layer is where an add-on or database package brought from a link or a folder
    is recorded (`complete()`), so it is a row in the list on the next start, as
    on WotLK. `shipped_ids()` asks the bundled tree alone.
    """
    return ManifestStore(root, GAME, user_root if user_root is not None else user_manifests_dir())


def shipped_ids(kind: ManifestType = "mod") -> tuple[str, ...]:
    """The ids this app SHIPS for that family: the bundled index alone, never the user layer."""
    return ManifestStore(BUNDLED_MANIFESTS_DIR, GAME).load_index(kind).items


def shipped_addons() -> dict[str, str]:
    """The add-on folder names the shipped items copy into the client (lower case) → the item.

    An outside package carrying one of these would be copied over the shipped
    add-on's folder, so `custom.read_package()` refuses it by name.
    """
    names: dict[str, str] = {}
    for manifest in ManifestStore(BUNDLED_MANIFESTS_DIR, GAME).load_all("mod"):
        for step in manifest.client:
            if step.dest == "addons":
                names[(step.name or Path(step.src).name).lower()] = manifest.name
    return names


def layout() -> custom.TortoiseLayout:
    """This core's custom-route layout, knowing the shipped add-ons' names."""
    return custom.TortoiseLayout(shipped_addons=shipped_addons())


def derive_link(text: str) -> Manifest:
    """A Tortoise add-on or database package for the link `text`, or `DeriveError` (T596)."""
    return module_source.derive_link(
        text, GAME, today=date.today(), shipped_ids=shipped_ids(), layout=layout()
    )


def derive_folder(path: Path) -> Manifest:
    """As `derive_link()`, for a folder on this computer; read before anything is copied."""
    return module_source.derive_folder(
        path, GAME, today=date.today(), shipped_ids=shipped_ids(), layout=layout()
    )


def complete(manifest: Manifest, clone: Path) -> Manifest:
    """Fill `manifest` in from what `clone` holds, PERSIST it, and return it.

    The applier's completion hook, as on WotLK: what is persisted is what the
    rest of the press acts on. A refusal raises `apply.CompletionRefused`, and
    the applier takes a first install's folder back.
    """
    completed = custom.complete(manifest, clone, shipped_addons=shipped_addons())
    module_source.persist(user_manifests_dir(), completed, shipped_ids=shipped_ids())
    return completed


def forget(manifest: Manifest) -> bool:
    """Drop `manifest` from the user layer; `True` if there was one. After a remove returned."""
    return module_source.forget(user_manifests_dir(), manifest)


def install_custom(applier: Applier) -> CustomInstall:
    """The Modules tab's custom-install seam over the tab's own (guarded) applier (T596).

    WotLK's shape, with this core's completion; and what the package holds that
    Yu'lon left alone (`custom.unused()`) is added to the report's skipped lines,
    so a file that was not run is said, not silently dropped.
    """

    def install(manifest: Manifest, folder: Path | None, *, replacing: bool = False) -> ApplyReport:
        source = FolderSource(folder, copy_folder) if folder is not None else None
        finished: list[Manifest] = []

        def finish(derived: Manifest, clone: Path) -> Manifest:
            finished.append(complete(derived, clone))
            return finished[-1]

        report = applier.install(
            manifest, None, folder=source, complete=finish, replacing=replacing
        )
        left = custom.unused(finished[-1]) if finished else ()
        return replace(report, skipped=(*report.skipped, *left)) if left else report

    return install


BACKUP_LABEL = "before-{id}"
"""The label an outside item's automatic backup carries in its file names (T596, E2)."""


@dataclass(frozen=True)
class OutsideSqlBackup:
    """`apply.SqlBackup` for Tortoise: a backup before an OUTSIDE item's database changes.

    Owner decision 2026-10-08: automatic, of only the databases the press writes,
    named in the report and again in Remove, which keeps the changes. A shipped
    item takes none here: the one that changes the database (Bigger Stacks) keeps
    its own backup table and undoes itself on Remove.

    `take` is `maintenance.backup(only=..., label=...)` bound to this install; the
    files are found again by their label, so nothing new is recorded anywhere.
    """

    server_dir: Path
    take: Callable[[Sequence[str], str], BackupReport]
    schemas: Mapping[Db, str]

    def before(self, manifest: Manifest, dbs: tuple[Db, ...]) -> str | None:
        if manifest.origin is None:
            return None
        names = [self.schemas.get(db, db) for db in dbs]
        report = self.take(names, BACKUP_LABEL.format(id=manifest.id))
        files = ", ".join(self._rel(dump.path) for dump in report.dumps)
        return f"backed up {', '.join(names)} before {manifest.id}'s database changes: {files}"

    def named(self, manifest: Manifest) -> str | None:
        if manifest.origin is None:
            return None
        folder = backups_dir(self.server_dir)
        label = BACKUP_LABEL.format(id=manifest.id)
        found = sorted(p for p in folder.glob(f"*_{label}_*.sql") if p.is_file())
        if not found:
            return (
                f"no backup taken before its database changes was found in {self._rel(folder)}, "
                "so Yu'lon cannot name one taken before them"
            )
        stamp = found[0].name[: len("YYYYmmdd_HHMMSS")]
        first = [p for p in found if p.name.startswith(stamp)]
        later = len({p.name[: len(stamp)] for p in found}) - 1
        more = f" ({later} later backup(s) were taken before later installs of it)" if later else ""
        # Not "undoes": a restore replaces the tables a dump holds and leaves the
        # rest (`maintenance.restore()`, a merge), so a table the item added stays.
        return (
            f"the backup taken before its first database change, "
            f"{', '.join(self._rel(p) for p in first)}: restoring it puts the tables in it back "
            f"as they were then, losing what was played since; a table the item added is not "
            f"in it and stays{more}"
        )

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.server_dir).as_posix()
        except ValueError:
            return str(path)


def fetcher(cache_root: Path, http: HttpGet = urllib_get) -> ManifestFetcher:
    """A fetcher that mirrors the Tortoise manifests from GitHub into `cache_root`."""
    return ManifestFetcher(MANIFEST_BASE_URL, cache_root, http)


def refresh(cache_root: Path, kind: ManifestType, http: HttpGet = urllib_get) -> RefreshResult:
    """Refresh one family of Tortoise manifests into `cache_root` (ETag-revalidated)."""
    return fetcher(cache_root, http).refresh(GAME, kind)


def applier(
    server_dir: Path,
    *,
    sql: SqlRunner | None,
    arming: Callable[[], Arming],
    world_running: Callable[[], bool | None],
    start_database: Callable[[], bool] | None = None,
    git: Git | None = None,
    client_dir: Path | None = None,
    sql_backup: SqlBackup | None = None,
) -> GuardedApplier:
    """A GUARDED `Applier` for the Tortoise install at `server_dir`.

    `sql` is required rather than defaulted, exactly as on TBC: this game's
    database password is generated at install time into
    `<server_dir>/.db_password` and its schemas are `tw_*`, so the caller that
    already holds the correct `DockerSql` hands it over and `sql=None` means
    "no database", which reports every SQL step as skipped rather than running
    it somewhere else.

    `arming` and `world_running` are the two readings checklist 2504's guard
    needs, and they are callables rather than values because the answer changes
    between the moment the tab is built and the moment a user presses Install —
    a world can be started or stopped in between, and a guard that decided at
    construction time would be guarding a fact about the past. `read_arming()`
    is the real one; a caller with no running install passes something cheap.

    `world_running` answers TWO guards on this game and one everywhere else,
    which is why it is three-valued here since T7. `GuardedApplier`'s own check
    is checklist 2504's (would this restart re-enter the fork's auto-updater?)
    and the base's is 8.7a's (is a live world holding these tables?). They read
    the same fact and must not be able to disagree about it, so one callable
    goes to both — the subclass used to swallow the keyword, leaving 8.7a's
    guard at `None` on this game as on the other three.

    No `dbc=`: `server_dbc` copies DBC files out of a clone, and no Tortoise
    item carries any.

    `sql_backup` is T596's `OutsideSqlBackup`: the backup before an outside
    item's database changes. Absent, none is taken.
    """
    return autoupdate.guarded_applier(
        server_dir,
        sql=sql,
        arming=arming,
        world_running=world_running,
        start_database=start_database,
        git=git,
        client_dir=client_dir,
        sql_backup=sql_backup,
    )


def apply_module(
    manifest: Manifest,
    server_dir: Path,
    values: Mapping[str, str] | None = None,
    *,
    sql: SqlRunner | None,
    arming: Callable[[], Arming],
    world_running: Callable[[], bool | None],
    start_database: Callable[[], bool] | None = None,
    client_dir: Path | None = None,
) -> ApplyReport:
    """Install `manifest` into the Tortoise server at `server_dir`, guard first.

    Returns the `ApplyReport`; the caller decides about the restart it names
    (call down / signal up — this function never touches Docker's lifecycle
    itself). Raises `autoupdate.AutoUpdateRefused` rather than returning a
    report when that restart would re-enter this fork's own auto-updater.
    """
    return applier(
        server_dir,
        sql=sql,
        arming=arming,
        world_running=world_running,
        start_database=start_database,
        client_dir=client_dir,
    ).install(manifest, values)


ADDON_ID = "tortoise-bots-manager"
"""The client addon whose release is paired with the server's bot module (T126)."""

BOTS_REPO = "Sagiroth/TortoiseBots"
"""The catalog source the addon's release is compared against (T126).

Both publish dated releases in lockstep (v2026-09-23, -24, -25 on both, read
2026-09-25), and the addon's README says it degrades gracefully against an older
server (`TBM:CAPS`), so a skew is a note and never a refusal.
"""


def module_updates(
    server_dir: Path,
    *,
    git: CountingGit | None = None,
    newest_release: Callable[[str], Release | None] | None = None,
    compare_commits: Callable[[str, str, str], Comparison | None] | None = None,
    now: int | None = None,
) -> tuple[ModuleUpdate, ...]:
    """ "Check for updates" for this install's clones: mods, and modules too (T126, T596).

    Tortoise's `mod` clones are in `sql_scripts/clones/`: the two shipped client
    addons and any add-on or database package brought from a link (T596); its
    SQL and conf mods clone nothing and are not listed. `module` clones in
    `modules/` are counted the same way, so a server module brought in later is
    not left out of the press; a server with no `modules/` folder gives none.
    Each clone is counted against what its manifest follows -- the newest release
    for TortoiseBots Manager, the branch tip for the others -- through
    `apply.cached_module_updates()`, so a row is kept for a day (an hour when
    nothing answered) and the press costs GitHub nothing while nothing moved.
    """
    rows: list[ModuleUpdate] = []
    kinds: tuple[ManifestType, ...] = ("mod", "module")
    for kind in kinds:
        branches: dict[str, str | None] = {}
        releases: dict[str, str] = {}
        try:
            for manifest in store().load_all(kind):
                if manifest.source is None:
                    continue
                branches[manifest.id] = manifest.source.branch
                if manifest.source.follow == "releases":
                    slug = github_slug(manifest.source.repo)
                    if slug is not None:
                        releases[manifest.id] = slug
        except Exception as exc:  # boundary: a broken manifest tree must not stop the count
            logger.warning(f"could not read the wow-tortoise {kind}s for what they follow: {exc}")
        rows += cached_module_updates(
            server_dir,
            kind=kind,
            git=git,
            branches=branches,
            releases=releases,
            newest_release=newest_release,
            compare_commits=compare_commits,
            now=now,
        )
    return tuple(rows)


def release_note(
    addon: str, server: str, *, addon_moved: bool = False, server_moved: bool = False
) -> str:
    """The TortoiseBots Manager row's extra line: its release, and the server's if known.

    `addon_moved`/`server_moved` say the last count found that side BEHIND a
    newest release of the SAME name: the tag was re-published on newer commits
    since that side was installed. Equal names are then not "in step" -- the
    comparison is of commits, and the names only label them.
    """
    said = f"Installed: release {addon}."
    if not server:
        return said
    said += f" The server's bot module is on {server}."
    if server != addon:
        said += (
            f"\nThe addon ({addon}) and the server's bot module ({server}) are from different "
            "releases. They work together, but updating both keeps them in step."
        )
    elif addon_moved or server_moved:
        which = (
            "both"
            if addon_moved and server_moved
            else "the addon" if addon_moved else "the server's bot module"
        )
        said += (
            f"\nBoth are named {addon}, but an updated release {addon} has come out since "
            f"{which} {'were' if which == 'both' else 'was'} installed. They work together, "
            "but updating both keeps them in step."
        )
    return said


def release_notes(server_dir: Path) -> dict[tuple[str, str], str]:
    """Per-row notes for this install's Modules tab: the addon's release against the server's.

    Small file reads and nothing else: the addon's clone claim (the release its
    install checked out), the install record (the release "Update the server to
    latest…" moved the bot module to), and the two cached counts -- the
    addon's "Check for updates" row and the Server tab's upstream reading --
    which say whether either side is behind a newer build of a release with the
    same name. An addon installed before T126 recorded none and gets no line; a
    server still on its tested pin is on no release, so only the addon's is said.
    """
    clone = server_dir / CLONE_DIRS["mod"] / ADDON_ID
    addon = clone_release(clone, item_id=ADDON_ID)
    if not addon:
        return {}
    state = read_state(server_dir, valid=())
    rev = state.rev_for(BOTS_REPO) if state is not None else None
    server = rev.release if rev is not None else ""
    counted = cached_module_update(server_dir, "mod", ADDON_ID)
    addon_moved = counted is not None and counted.release == addon and is_behind(counted.behind)
    news = upstream_cached_row(server_dir, BOTS_REPO)
    server_moved = news is not None and news.release == server and (news.behind or 0) > 0
    return {
        ("mod", ADDON_ID): release_note(
            addon, server, addon_moved=addon_moved, server_moved=server_moved
        )
    }
