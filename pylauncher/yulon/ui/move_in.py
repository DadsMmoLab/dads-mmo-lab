"""The Catalog tile's "Bring from another computer…": its wiring to the real engine (T601 level 2).

`move_server` is the plan and the engine; this binds them to this machine: the catalog, the
platform, the Modules tab's manifest store for the game, GitHub, the install engine of the
PINNED entry, and, once the install has made the server, the same `ControllerServices` its tab
will get (the module applier, the rebuild, the Maintenance engine).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yulon import apply, module_source, move_server, platform
from yulon.catalog import native, upstream
from yulon.catalog.catalog import Catalog, CatalogEntry
from yulon.log import get_logger
from yulon.manifest import Manifest, ManifestType
from yulon.manifest_store import ManifestStore
from yulon.move_flows import MoveError

logger = get_logger(__name__)


@dataclass(frozen=True)
class MoveIn:
    """What the Catalog view needs: a plan for a file and a folder, and the engine for a plan."""

    plan: Callable[[Path, Path], move_server.ServerImportPlan]
    """`(package, server_dir) -> plan`; reads the file and asks GitHub, writes nothing."""
    installer: Callable[[move_server.ServerImportPlan], move_server.MovedInInstall]


def _commit_known(repo: str, sha: str) -> bool | None:
    slug = upstream.github_slug(repo)
    if slug is None:
        return None
    return upstream.commit_exists(slug, sha, get=upstream.https_get)


def _distance(repo: str, pin: str, commit: str) -> int | None:
    """Commits `commit` has that `pin` lacks, from GitHub; None when it cannot say."""
    slug = upstream.github_slug(repo)
    if slug is None:
        return None
    said = upstream.compare(slug, pin, commit, get=upstream.https_get)
    return said.ahead if said is not None else None


def _store_for(entry: CatalogEntry) -> ManifestStore | None:
    """The Modules tab's store for `entry`'s server, through the tab's own factory."""
    from yulon.ui.controller_view import manifest_store_for

    try:
        return manifest_store_for(entry)
    except Exception as exc:  # noqa: BLE001 - no store: no module can be looked up, and says so
        logger.info(f"no manifest store for {entry.id}: {exc}")
        return None


def _lookup(store: ManifestStore | None) -> move_server.ModuleLookup:
    def load(kind: ManifestType, item_id: str) -> Manifest | None:
        if store is None:
            return None
        try:
            return store.load(kind, item_id)
        except Exception:  # noqa: BLE001 - not a module here: the plan says so by name
            return None

    def shipped(kind: ManifestType, item_id: str) -> bool:
        if store is None:
            return False
        try:
            return item_id in store.load_index(kind).items
        except Exception:  # noqa: BLE001 - an index that will not load ships nothing to clash with
            return False

    return move_server.ModuleLookup(load=load, shipped=shipped)


def _server_for(entry: CatalogEntry) -> Callable[[Path, Path | None], move_server.MovedInServer]:
    def build(server_dir: Path, client_dir: Path | None) -> move_server.MovedInServer:
        from yulon.ui.controller_view import ControllerServices

        services = ControllerServices.for_entry(entry, server_dir, client_dir)
        if services.move is None or services.move.world is None:
            raise MoveError(f"{entry.name} has no Maintenance engine here.")
        store = services.store

        def persist(manifest: Manifest) -> None:
            if store is None or store.user_root is None:
                raise MoveError(
                    f"{manifest.name} was added from a link, and this Yu'lon has nowhere to keep "
                    "its description."
                )
            module_source.persist(
                store.user_root, manifest, shipped_ids=store.load_index(manifest.type).items
            )

        custom = services.module_install_custom

        def install_folder(manifest: Manifest, folder: Path) -> apply.ApplyReport:
            # The Modules tab's own "Install from folder": it copies the folder in, completes
            # the description from what landed, and keeps it in this computer's user layer.
            assert custom is not None
            return custom(manifest, folder)

        return move_server.MovedInServer(
            world=services.move.world,
            applier=services.applier,
            rebuild=services.rebuild,
            db_password=entry.install.db_password(server_dir),
            persist_manifest=persist,
            install_folder=install_folder if custom is not None else None,
        )

    return build


def move_in_for_app(catalog: Catalog) -> MoveIn:
    """The real `MoveIn`, for `main.py` to hand the Catalog view."""

    def plan(path: Path, server_dir: Path) -> move_server.ServerImportPlan:
        return move_server.plan_server_import(
            path,
            catalog=catalog,
            server_dir_for=lambda _entry: server_dir,
            platform_id=platform.detect(),
            lookup_for=lambda entry: _lookup(_store_for(entry)),
            commit_known=_commit_known,
        )

    def installer(chosen: move_server.ServerImportPlan) -> move_server.MovedInInstall:
        from yulon.install_wiring import installer_for_app

        assert chosen.pinned is not None and chosen.entry is not None
        engine = installer_for_app(chosen.pinned)
        seams = native.Seams()

        def record(server_dir: Path, rows: object) -> bool:
            revs = [
                native.SourceRev(r.repo, r.built, pin=r.pin, ahead=r.ahead)
                for r in rows  # type: ignore[attr-defined]
            ]
            return bool(engine.record_source_rows(server_dir, revs))  # type: ignore[attr-defined]

        return move_server.MovedInInstall(
            chosen,
            engine=engine,
            server_for=_server_for(chosen.entry),
            record_rows=record,
            head_version=seams.head_version,
            commits_since=seams.commits_since,
            distance=_distance,
        )

    return MoveIn(plan=plan, installer=installer)


__all__ = ["MoveIn", "move_in_for_app"]
