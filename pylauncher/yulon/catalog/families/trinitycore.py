"""The TrinityCore-lineage install engine (T179): one class for every `trinitycore` entry.

Centurion (a TrinityCore 3.3.5 fork) is the first such entry; the facts each stage
rests on are in `.notes/tickets/T179-centurion-facts.md` (CENTURION @ faac5fc9) and
the stage list is the T179 spec's §1. Like `cmangos.py`, the class names no game:
every parameter is the entry's typed `install.native.trinitycore` block.

**Why it is a `CmangosInstaller`.** Six of its eleven stages are stage kinds the
CMaNGOS engine already runs and proves -- the generated database password and the
refusal that guards it, the Dockerfile written from this repo's template with the
secret kept out of the build context, the conf table patched over the image's
`.dist`, the marker-gated SQL plan with its probe, reset, verify and marker -- and
a TrinityCore block carries exactly the same shapes for them (`TrinityCoreData`'s
parts subclass `CmangosData`'s). So this class inherits those bodies and views its
block through `_data()` as the CMaNGOS shape, with no source patches. What differs
is overridden here, by name:

* `stages()` -- its own tuple: no `patch-sources`, no `extract`/`mmaps` (the movement
  maps run after the server is up, Task 4), and `client-data` in their place;
* `_clone_sources()` -- the core checkout is sparse (`sparse_exclude`);
* `_client_data()` -- the temporary extraction client, the tree's DBC overlay and
  the start check, all new;
* `_conf()` -- the conf table alone, without the CMaNGOS bot-count carry-over and
  the Tortoise bot dashboard (`families/decisions.py` records both sites);
* `_expand()` -- every run of the SQL plan, with the database-name renames;
* `after_ready()` / `before_rebuild()` -- the spine's hooks: the movement maps start
  as a background job once the server is up, and stop before any rebuild route
  (`families/mmaps.py`, Task 4).

* `check_moved_sources()` / `servers_down_work()` / `after_update()` -- the update
  route's hooks (Task 6): what the move changed in the tree's SQL snapshot is read
  before the compile (a change to the characters' or accounts' layout refuses the
  update), the changed world tables are imported again with the rebuild's servers
  stopped, before the new build starts (and the old ones again on a rollback), and a
  change to the DBC files or a required client pack flags the map data for
  `reextract()`. A world update that did not finish is finished by the next update
  or by `finish_world_reimport()` (fix round 1).

The inherited "database updates" and corrections presses see a plan with no
re-runnable and no correctable phases, because `native.update_phases()` and
`correction_phases()` read a CMaNGOS block only: this family's SQL moves with its
code, and "Update the server to latest…" is where a change to it is applied.
"""

from __future__ import annotations

import errno
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import threading
import time
from collections.abc import Generator, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import ClassVar, Literal, cast

from yulon import client_names, client_packs, docker, platform, play_client, server_build_presses
from yulon.catalog import bot_count
from yulon.catalog.catalog import (
    CatalogEntry,
    ClientPack,
    CmangosData,
    EmulatorSource,
    SqlPhase,
    SqlPlan,
    TrinityCoreData,
)
from yulon.catalog.families import conf, extract, mmaps, sqlplan
from yulon.catalog.families.cmangos import CATALOG_ERROR_TAIL, ETC_DIR, CmangosInstaller
from yulon.catalog.installer import InstallerError, InstallOptions, UpdateRefused
from yulon.catalog.native import (
    IMPORT_STAGE_CANCEL_NOTE,
    InstallState,
    Seams,
    ServersDownWork,
    Stage,
    StageContext,
    _speaking,
    _stop_control,
    build_cancel_note,
    folder_start_refusal,
    owed_start_refusal,
    past_the_tested_pin,
    read_state,
)
from yulon.catalog.snapshot import DatabaseSnapshot
from yulon.log import get_logger
from yulon.manifest import Db

logger = get_logger(__name__)

EXTRACT_CLIENT_RECORD = ".yulon-extract-client.json"
"""In the server folder: where this install's temporary extraction client is, while it may exist.

Written BEFORE the copy is made and removed only after the copy is gone, so a press
that died part way -- inside `play_client.create()` included -- leaves the next
press and Uninstall the path to clean up (T179 Task 3 fix round 1).
"""

STOCK_BUILDS: Mapping[str, int] = {"3.3.5a": 12340}
"""The build a stock client of each version reports; a player's own client is one."""

LEFT_OUT_DIR = ".yulon-left-out"
"""Inside the temporary copy, beside its `Data/`: where the archives it must not hold are moved.

The extractors read `Data/` only (`-i /client`, `-d /client/Data/`), so a file here is
never read; it goes with the copy (fix round 2).
"""

CLIENT_DATA_CANCEL_NOTE = (
    f"{extract.EXTRACT_CANCEL_NOTE} The temporary copy of your client is removed either way."
)
"""What a Stop costs in `client-data`: the extraction's per-tool record, and the copy gone."""

REEXTRACT_FILE = ".yulon-reextract.json"
"""In the server folder: the map data must be extracted again, and which changed files say so.

Written by an update or a return whose move changed the server's DBC files or a
required client pack (Task 6), removed only by a `reextract()` that finished. Its
own file rather than a field of `.yulon-install.json`: it outlives presses that
rewrite that record, and Uninstall removes it with the folder.
"""

REEXTRACT_BUTTON = "Re-extract map data"
"""The Server tab's press for `reextract()`, named in the sentences that ask for it."""

REEXTRACT_PUT_BACK = (
    "The map data from before this press was put back as it was, with its pathfinding data if "
    "it had finished, so the server runs on it as before."
)
"""What a failed `reextract()` ends with once the old map data is back in place (T241)."""


def reextract_kept_tiles(kept: int) -> str:
    """What a failed `reextract()` adds when a part-made pathfinding run's tiles stay (T263).

    Said only when the next run really continues from them (`mmaps.continues_from()`):
    the old map data came back unchanged, and the run that stopped part-way was made
    from it. The live test of T241 saw exactly that (35 tiles kept, continued to 80)
    while the message said nothing of it.
    """
    return (
        f"The {kept} finished tiles of the pathfinding data that had stopped part-way were "
        f"kept too, and \u201c{mmaps.START_PRESS}\u201d on the Server tab continues from them."
    )


REEXTRACT_CANCEL_NOTE = (
    "A Stop puts the map data from before this press back as it was. The temporary copy of "
    "your client is removed either way."
)
"""What a Stop costs in the re-extraction: nothing, the old map data comes back (T241)."""

WORLD_REIMPORT_FILE = ".yulon-world-reimport.json"
"""In the server folder: world table files an update still has to import again (fix round 1).

`{"version": 1, "reimport": [rel, ...], "parts": [stem, ...]}`, written whole (a
temporary file renamed into place) BEFORE the world server is stopped for the
tables, and removed only once the last of them went in. A failure, a Stop or a
crash in between leaves it, and the next "Update the server to latest…" or
"Return to the tested pin…" imports what it names along with its own changes, or
"Finish the world update" (`TrinityCoreInstaller.finish_world_reimport()`) does
without a compile. Its own file for `REEXTRACT_FILE`'s reasons.
"""

FINISH_WORLD_BUTTON = "Finish the world update"
"""The Server tab's press for `finish_world_reimport()`, named in the sentences that ask for it."""

WORLD_TABLES_CANCEL_NOTE = (
    "Stopping now stops between two table files, and the update is rolled back: the tables "
    "already imported again are imported once more from the sources the old build was made from."
)
"""What a Stop costs while the update route imports the changed world tables (Task 6)."""

FINISH_CANCEL_NOTE = (
    "Stopping now stops between two table files; the world server is left stopped, and every "
    f"file stays waiting for “{FINISH_WORLD_BUTTON}”."
)
"""What a Stop costs while "Finish the world update" imports the waiting tables (fix round 1)."""

_REALM_INSERT = re.compile(
    r"^\s*INSERT\s+INTO\s+`?realmlist`?\s*(?:\((?P<columns>[^)]*)\))?\s*VALUES\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
"""One realm-row line of a dump: its column list (with or without backticks), then its rows."""
_REALM_COLUMNS = (
    "id",
    "name",
    "address",
    "localAddress",
    "localSubnetMask",
    "port",
    "icon",
    "flag",
    "timezone",
    "allowedSecurityLevel",
    "population",
    "gamebuild",
)
"""TrinityCore's `realmlist` columns, in the order a dump without a column list writes them."""

_YULONS_REALM_COLUMNS = frozenset({"address", "localAddress"})
"""The realm row's columns Yu'lon sets itself (the Networking tab's address): never taken over."""

_PART = re.compile(r"^(?P<stem>.+)\.\d+\.sql$")
"""A table dumped across numbered files: `broadcast_text_locale.1.sql`, `.2.sql` (facts §2).

Only the first part drops and creates the table; the next ones only insert. So a
change to any part imports every part again, in order: the second alone would
insert rows that are there already, and the first alone would drop the second's.
"""


@dataclass(frozen=True)
class SnapshotChanges:
    """What an update's move changed in the tree's SQL snapshot and map inputs (Task 6).

    Every path is server-dir-relative, as the SQL plan's globs and the log name it.
    `check_moved_sources()` makes it before the compile, after every refusal;
    `after_update()` applies it once the new build is up.
    """

    reimport: tuple[str, ...] = ()
    """Whole-table world files added or changed: imported again."""
    parts: tuple[str, ...] = ()
    """`_PART` stems (server-dir-relative) of split tables a part of which changed."""
    left: tuple[str, ...] = ()
    """World files removed upstream: their tables are left as they are."""
    left_parts: tuple[str, ...] = ()
    """Parts of split tables removed upstream: the whole table is left, never imported in part."""
    skipped: tuple[tuple[str, str], ...] = ()
    """`(file, what went beyond the address)` changed only in `skip_lines` lines: left out.

    The second is "" for a change to the address Yu'lon sets alone, else what the
    realm row's change touched beyond it (`gamebuild 12342 -> 12343`), logged.
    """
    map_data: tuple[str, ...] = ()
    """Changed DBC files and required client packs: the map data must be extracted again."""
    everything: bool = False
    """An unreadable `WORLD_REIMPORT_FILE`: every file of the re-import phases goes in again."""
    notes: tuple[str, ...] = ()
    """What the press says after the update: a realm row change left out, a pending file gone."""
    flagged_before: tuple[str, ...] | None = None
    """`REEXTRACT_FILE` as it was before this press (None: absent), for a rollback to put back."""

    def imports(self) -> bool:
        """Is any world table file imported again?"""
        return bool(self.reimport or self.parts or self.everything)


class TrinityCoreInstaller(CmangosInstaller):
    """Install a TrinityCore server: sparse clone, build, client data, conf, SQL plan, start."""

    family = "trinitycore"
    STAGE_NAMES: ClassVar[tuple[str, ...]] = (
        "clone-sources",
        "db-password",
        "write-dockerfile",
        "generate-compose",
        "build",
        "client-data",
        "conf",
        "start-db",
        "import",
        "up",
        "ready",
    )

    def __init__(
        self,
        entry: CatalogEntry,
        *,
        installers_root: Path | None = None,
        import_probe: docker.ImportProbe | None = None,
        reset_unfinished: docker.ResetUnfinished | None = None,
        seams: Seams | None = None,
        mmaps_runner: mmaps.Runner | None = None,
        database_snapshot: DatabaseSnapshot | None = None,
    ) -> None:
        """The spine's constructor, plus the Docker seam of the movement-map job (Task 4).

        Its own seam and not a `Seams` field: only this family has the job. With
        none given it is bound to the distro the engine's seams are (`Seams.in_wsl`,
        T179 final round): an update of a server inside a WSL distro asks about and
        stops the job through that distro's Docker, never this host's.
        """
        super().__init__(
            entry,
            installers_root=installers_root,
            import_probe=import_probe,
            reset_unfinished=reset_unfinished,
            seams=seams,
            database_snapshot=database_snapshot,
        )
        self._mmaps_runner = (
            mmaps_runner
            if mmaps_runner is not None
            else mmaps.DockerRunner(wsl_distro=self._seams.distro)
        )

    def stages(self) -> tuple[Stage, ...]:
        """The family's stage tuple, in `STAGE_NAMES` order (T179 spec §1, steps 1-9)."""
        return (
            Stage("clone-sources", self._clone_sources),
            Stage("db-password", self._db_password, recorded=False),
            Stage("write-dockerfile", self._write_dockerfile),
            Stage("generate-compose", self.stage_generate_compose),
            Stage("build", self.stage_build, cancel_note=build_cancel_note()),
            Stage("client-data", self._client_data, cancel_note=CLIENT_DATA_CANCEL_NOTE),
            Stage("conf", self._conf),
            Stage("start-db", self.stage_start_db, recorded=False),
            Stage("import", self._import, cancel_note=IMPORT_STAGE_CANCEL_NOTE),
            Stage("up", self.stage_up, recorded=False),
            Stage("ready", self.stage_ready, recorded=False),
        )

    # -- the block ---------------------------------------------------------

    def _tc(self) -> TrinityCoreData:
        """The typed block every stage of this family reads; its absence is a catalog error."""
        data = self._native().trinitycore
        if data is None:
            raise InstallerError(
                f"{self.entry.name} says its family is trinitycore but carries no trinitycore "
                f"settings. {CATALOG_ERROR_TAIL}"
            )
        return data

    def _data(self) -> CmangosData:
        """The block in the CMaNGOS shape the inherited stage bodies read, with no source patches.

        Not a second copy of anything: each part IS the TrinityCore block's own
        object (`TrinityCoreDockerfile` is a `DockerfileSpec`, `TrinityCoreSqlPlan` a
        `SqlPlan`, and so on), already validated when the catalog loaded, which is
        why `model_construct()` is enough. `patches` is empty because this family
        carries none, so the inherited patch and dirty-tree code finds nothing to do.
        """
        tc = self._tc()
        return CmangosData.model_construct(
            client=tc.client,
            dockerfile=tc.dockerfile,
            extract=tc.extract,
            mmaps=tc.mmaps,
            conf=tc.conf,
            sql=tc.sql,
            patches=(),
        )

    # -- clone-sources -------------------------------------------------------

    def _clone_sources(self, ctx: StageContext) -> Iterator[str]:
        """Every source to its `dest`, the core checkout leaving out what is never compiled.

        Centurion's `playerbot reference/` (0.96 GB, README.md:34) and its own
        `centurion/launcher/` are left out of the checkout (facts, repo-level), so
        a 3.15 GB tree is about 2.2 GB. The branch and the pinned `rev` come from
        the entry's source, as for every family: the repository's default branch
        is an old `master`, so the catalog always names `CENTURION`.
        """
        tc = self._tc()
        if tc.sparse_exclude:
            yield (
                f"The {tc.checkout} checkout leaves out {', '.join(tc.sparse_exclude)}: nothing "
                "in them is compiled."
            )
        yield from self.stage_clone_sources(
            ctx,
            self.entry.emulator.sources,
            recorded_as="clone-sources",
            sparse_exclude={tc.checkout: tc.sparse_exclude},
        )

    # -- client-data ---------------------------------------------------------

    def _client_data(self, ctx: StageContext) -> Iterator[str]:
        """Map data from a temporary client with the server's required packs, then its own DBCs.

        In order (T179 spec §1 step 6, facts §3):

        1. **Is the extraction already vouched for?** The evidence file names the
           PLAYER'S client and the required packs' checksums (`extract.run_plan()`'s
           `evidence_client_dir`/`evidence_salt`), so a resume answers this before
           any copy is made, and a changed pack or another client extracts again.
        2. **Otherwise, a temporary extraction client** BESIDE the player's client
           (`extraction_client_dir()`: the same parent folder, so the same drive):
           `play_client.create()` with no full copy, every `.MPQ` that is not one of
           the block's `client_archives` (the stock archives) taken out of the copy,
           every REQUIRED pack laid in (`client_packs`), the extractors run against it
           in the server image, and the copy deleted whether they succeeded or not.
           Stock archives and required packs only, whatever the player installed or
           chose: the patched extractors read every lettered and numbered patch
           archive they find (map_extractor System.cpp:1152-1218), so an HD pack or
           another server's patch in the copy would be extracted into maps this
           server does not expect (Review Focus 1).
        3. **The tree's own DBCs** over `data/dbc` (`dbc_overlay_from`): "Use these
           DBCs, not the ones the extractor writes" (README.md:174-180).
        4. **The start check**: maps and vmaps for every `required_maps` id, or a
           refusal naming this step, before a server is started that would stop
           with "Unable to load critical files" (World.cpp:1811-1823).

        The player's own client is never written: the copy shares its archives
        by clone or hard link, every change to the copy replaces a NAME (a pack's
        install renames into place, a dropped archive is moved aside within it), the copy is
        mounted read-only into the extractors, and it is removed through
        `play_client.remove_folder()`, which never clears a flag on a shared file
        for good.
        """
        tc = self._tc()
        original = ctx.client_dir
        if original is None:
            raise InstallerError(
                f"{self.entry.name} makes its map data from your game client, and no client "
                "folder was given. Pick the client folder and try again."
            )
        packs = self._required_packs()
        salt = _packs_salt(packs, ctx.server_dir)
        data_dir = self._data_dir(ctx)
        if self._extraction_vouched_for(data_dir, original, salt):
            yield (
                f"The map data in {data_dir} was already made from {original} with these "
                "packs; leaving it."
            )
        else:
            yield from self._extract_through_a_temporary_client(
                ctx, original, data_dir, packs, salt
            )
        source = ctx.server_dir / tc.checkout / tc.extract.dbc_overlay_from
        target = data_dir / tc.extract.dbc_overlay_to
        copied = extract.overlay_files(source, target)
        yield (
            f"Laid the server's own DBC files from {tc.extract.dbc_overlay_from} over {target} "
            f"({copied} copied; the rest were already there)."
        )
        self._refuse_missing_map_data(data_dir, original)
        maps = ", ".join(str(map_id) for map_id in tc.required_maps)
        yield f"The map data the world server checks at start (maps {maps}) is in place."

    def _required_packs(self) -> tuple[ClientPack, ...]:
        """The client packs every client of this server has, refused if one is a download.

        The extraction client is made from the server's own checkout and nothing
        else: a required pack from a URL would make the map data depend on what a
        website serves on the day, and nothing in this stage could tell a resume
        that it had changed.
        """
        packs = self._map_inputs()
        downloads = [pack.label for pack in packs if pack.source.kind != "checkout"]
        if downloads:
            raise InstallerError(
                f"{self.entry.name}'s catalog makes {', '.join(downloads)} a required client "
                "pack from a download, and the map data is made only from packs in the server's "
                f"own checkout. Nothing was extracted. {CATALOG_ERROR_TAIL}"
            )
        return packs

    def _players_client(self) -> str | None:
        """The PLAYER's client, as the shortfall refusal names it (T179 final round).

        The entry's `build` is the realm's: with an exe patch only the ready-to-play
        copy reports it (Centurion: 12342), and the client the map data is made from
        is the player's own stock one. None (the entry's build is said) without a
        patch, or for a version whose stock build this table does not know.
        """
        client = self.entry.client
        stock = STOCK_BUILDS.get(client.version)
        if client.exe_patch is None or stock is None:
            return None
        return (
            f"your {client.version} client, build {stock} (the build {client.build} is what "
            f"{self.entry.name}'s patches make its ready-to-play copy report)"
        )

    def _map_inputs(self) -> tuple[ClientPack, ...]:
        """The required packs that lay a game archive under `Data/`: the map data's inputs.

        The extractors read `Data/` only (`-i /client`, `-d /client/Data/`), so a
        pack of addons (`Interface/AddOns`) or a `dinput8.dll` is no input of the
        map data (T179 final round): it is not laid into the extraction copy, not
        part of the salt, and a change to it never asks for a re-extraction.
        """
        return tuple(
            pack for pack in self.entry.client.packs if not pack.optional and _lays_archives(pack)
        )

    def _extraction_vouched_for(self, data_dir: Path, original: Path, salt: str) -> bool:
        """Does `data/`'s evidence vouch for every tool, for this client and these packs?

        The same three-part rule `extract.run_plan()` skips a tool on
        (`tool_satisfied`), asked of the same expected evidence it would write, so
        this answer and the one the run would give cannot disagree.
        """
        tc = self._tc()
        expected = extract.expected_evidence(
            tc.extract, original, tc.client.required_file, salt=salt
        )
        current = extract.read_evidence(data_dir)
        return all(
            extract.tool_satisfied(tool, data_dir, current, expected) for tool in tc.extract.tools
        )

    def _extract_through_a_temporary_client(
        self,
        ctx: StageContext,
        original: Path,
        data_dir: Path,
        packs: Sequence[ClientPack],
        salt: str,
    ) -> Iterator[str]:
        """Make the copy, run the extractors against it, and delete it -- on every way out.

        The removal is in an `except BaseException` and after the body rather than
        in a `finally`, so that a removal which fails on the way out of a failure
        is logged and does not replace the failure the person has to read, while
        one which fails after a success is said as a warning. A copy that stays
        behind is removed by the next press before it makes a new one, and by
        Uninstall: both find it through `EXTRACT_CLIENT_RECORD`, which is written
        before the copy exists.
        """
        tc = self._tc()
        temp = extraction_client_dir(original, ctx.server_dir)
        lost: list[play_client.LostFlag] = (
            []
        )  # the player's files whose read-only flag stayed cleared (T198)
        left = remove_leftover_extraction_client(
            ctx.server_dir, self.entry.id, also=temp, flags_lost=lost
        )
        if lost:
            yield f"warning: {play_client.flags_lost_warning(lost)}"
            lost.clear()
        if left is not None and left.kind == "foreign":
            raise InstallerError(
                f"{left.path} is in the way of this install's temporary copy of your client, and "
                "Yu'lon did not make it, so it was left as it was. Move it away, then press "
                "Install again. Nothing was extracted."
            )
        if left is not None and left.kind in ("ours", "linked"):
            raise InstallerError(f"{left.for_install()} Nothing was extracted.")
        if left is not None:
            yield f"warning: {left.for_uninstall()}"
        try:
            _write_record(ctx.server_dir, temp, original)
        except OSError as exc:
            raise InstallerError(
                f"{ctx.server_dir / EXTRACT_CLIENT_RECORD} could not be written ({exc}), so the "
                "temporary copy of your client was not made: without that note a copy left by a "
                "crash could not be found again. Check that the server folder can be written."
            ) from exc
        try:
            yield from self._make_extraction_client(ctx, original, temp, packs)
            image_ref = self._image_ref(ctx, tc.extract.image)
            user_args = self._user_args()
            yield f"Extracting map data from the copy into {data_dir} (the copy is read-only)."
            yield from self._stream(
                lambda sink: extract.run_plan(
                    tc.extract,
                    image_ref=image_ref,
                    client_dir=temp,
                    data_dir=data_dir,
                    run_container=self._seams.run_container,
                    user_args=user_args,
                    sink=sink,
                    cancel=ctx.cancel,
                    required_file=tc.client.required_file,
                    client_build=self.entry.client.build,
                    selinux_enforcing=self._seams.ask_selinux,
                    evidence_client_dir=original,
                    evidence_salt=salt,
                    client_named=self._players_client(),
                ),
                cancel=ctx.cancel,
                stage="client-data",
            )
            self._check_cancel(ctx.cancel)
        except BaseException as failure:
            left = remove_leftover_extraction_client(ctx.server_dir, self.entry.id, flags_lost=lost)
            if left is not None:
                logger.warning(left.for_install())
            if lost:
                told = play_client.flags_lost_warning(lost)
                logger.warning(told)
                if not isinstance(failure, GeneratorExit):  # a closed stream takes no line
                    yield f"warning: {told}"  # the install panel shows it whatever ends this
                if isinstance(failure, InstallerError):  # its words are what the person reads
                    failure.args = (f"{failure} {told}",)  # same object: its type is kept
            raise
        left = remove_leftover_extraction_client(ctx.server_dir, self.entry.id, flags_lost=lost)
        if lost:
            yield f"warning: {play_client.flags_lost_warning(lost)}"
        if left is None:
            yield "Removed the temporary copy of your client."
        elif left.kind == "ours":
            yield (
                f"warning: a temporary copy of your game client at {left.path} could not be "
                f"removed yet ({left.why}); {_DO_NOT_DELETE}. {left.close_first()}"
                "Uninstalling this server removes it safely."
            )
        else:
            yield f"warning: {left.for_uninstall()}"

    def _make_extraction_client(
        self, ctx: StageContext, original: Path, temp: Path, packs: Sequence[ClientPack]
    ) -> Iterator[str]:
        """`play_client.create()` beside the client, the non-stock archives out, the packs in."""
        yield (
            f"Making a temporary copy of your client {original} in {temp}, with only its stock "
            "game archives and this server's required packs. Your own client is not changed."
        )
        try:
            play_client.create(
                original,
                temp,
                game=self.entry.id,
                server_dir=ctx.server_dir,
                # Never a full copy: beside the client is the same drive, so its
                # archives are shared; a drive that cannot share them is refused
                # (`_no_copy_beside()`) rather than copied in full.
                allow_full_copy=False,
            )
        except play_client.PlayClientError as exc:
            raise InstallerError(_no_copy_beside(original, temp, exc)) from exc
        self._spell_data_as_the_extractors_open_it(temp)
        left_out = self._drop_unlisted_archives(temp)
        self._spell_archives_as_the_extractors_open_them(temp)
        if left_out:
            yield (
                "Left out of the copy, because this server's map data is made from the stock "
                f"archives and its own packs only: {', '.join(left_out)}."
            )
        for pack in packs:
            yield f"Laying {pack.label} into the copy."
            try:
                fetched = client_packs.fetch_checkout(pack, ctx.server_dir)
                client_packs.install(
                    temp, pack, fetched, game=self.entry.id, server_dir=ctx.server_dir
                )
            except client_packs.PackError as exc:
                raise InstallerError(f"{exc} The map data was not extracted.") from exc

    @staticmethod
    def _spell_data_as_the_extractors_open_it(temp: Path) -> None:
        """Rename the copy's `data/` folder to `Data/`, the one name the extractors read (T261).

        `-i /client` and `-d /client/Data/` (map_extractor, vmap4extractor) open
        `Data/` by exact name, and a client unpacked with `unzip -LL` has `data/`:
        on a disk that tells cases apart the copy would give them nothing. The copy
        is Yu'lon's, so the folder is renamed there; the player's client keeps its
        name. A folder already named `Data` is left as it is, whatever else is
        beside it, and so is a disk that ignores case, where `Data` reaches `data`.
        A folder rename touches no file's flag, and `play_client.remove_folder()`
        finds the player's file at its own case when the copy goes.
        """
        exact = temp / client_names.DATA_FOLDER
        if os.path.lexists(exact):
            return
        # A real folder whenever it is found: `play_client.plan()` refused a client
        # whose `Data` (found the same way) is a link or no folder, and the copy
        # reproduces no link.
        found = client_names.find(temp, client_names.DATA_FOLDER)
        if found is None:
            return
        try:
            os.rename(found, exact)
        except OSError as exc:
            remedy = _close_and_press(exc, "Install")
            raise InstallerError(
                f"{found} could not be renamed to {exact.name} in the temporary copy of your "
                f"client ({exc}), so the map data was not extracted: the extractors read the "
                f"client's {exact.name} folder only by that name. Your own client was not "
                f"changed.{remedy}"
            ) from exc

    def _drop_unlisted_archives(self, temp: Path) -> list[str]:
        """Move out of the copy's `Data/` every `.MPQ` that `client_archives` does not keep.

        An allow-list and not a list of what to drop: a player's client may hold a
        patch from another server (`Data/patch-4.MPQ`), an HD pack this server
        offers, or one nobody has heard of, and the extractors read them all.
        Matched case-insensitively, as the client folder is usually on Windows;
        `{locale}` in an entry is the locale folder the file is in.

        A RENAME into `LEFT_OUT_DIR` inside the copy, never a delete (fix round 2,
        the lead's ruling): the copy's archive is a hard link of the player's, and a
        read-only file a Windows delete refuses would need its flag cleared -- a flag
        the inode shares with the player's own file. A rename needs no flag, and the
        extractors read `Data/` only. Before the copy is removed, `_put_left_out_back()`
        renames each one back to its own path (fix round 3): `play_client.remove_folder()`
        puts a cleared flag back on the player's file at the SAME relative path, and
        under `LEFT_OUT_DIR` there is no such file, so the flag would stay cleared.
        """
        kept = self._tc().extract.client_archives
        data = temp / "Data"
        unlisted = [
            Path(folder) / name
            for folder, _dirs, files in os.walk(data)
            for name in files
            if name.casefold().endswith(".mpq")
            and not _kept_archive((Path(folder) / name).relative_to(data).as_posix(), kept)
        ]
        left_out: list[str] = []
        for path in unlisted:
            rel = path.relative_to(temp)
            aside = temp / LEFT_OUT_DIR / rel
            try:
                aside.parent.mkdir(parents=True, exist_ok=True)
                os.rename(path, aside)
            except OSError as exc:
                remedy = _close_and_press(exc, "Install")
                raise InstallerError(
                    f"{path} could not be moved out of the temporary copy of your client's Data "
                    f"folder ({exc}), so the map data was not extracted: that archive would be "
                    f"read into it. Your own client was not changed.{remedy}"
                ) from exc
            left_out.append(rel.as_posix())
        return sorted(left_out)

    def _spell_archives_as_the_extractors_open_them(self, temp: Path) -> None:
        """Rename each kept archive in the copy to the catalog's spelling (T227).

        The extractors open every archive by a fixed name (map_extractor
        System.cpp:114-124, :1152-1218; vmapexport.cpp:295-331), and on a
        case-sensitive disk `Data/lichking.mpq` is not `Data/lichking.MPQ`: a client
        named in lower case would give them nothing to read. The copy is Yu'lon's,
        so the rename happens there; the player's client keeps its own names.

        A name the catalog's spelling already reaches is left alone: on a disk that
        does not tell cases apart that is the same file, and on one that does it is
        the catalog's own archive. A locale folder follows the retail `xxYY`
        spelling (`enus` becomes `enUS`).

        A rename and never a link: one name per archive, so the extractors see what
        they would see in a retail client. On a case-sensitive disk the player's
        file at the old name is never asked for its flag (an unlink there needs
        only the folder's write bit), which is what `play_client.remove_folder()`
        would otherwise put back.
        """
        kept = self._tc().extract.client_archives
        data = temp / "Data"
        moves: list[tuple[Path, Path]] = []
        for folder, _dirs, files in os.walk(data):
            for name in files:
                path = Path(folder) / name
                canonical = _canonical_archive(path.relative_to(data).as_posix(), kept)
                if canonical is not None and canonical != path.relative_to(data).as_posix():
                    moves.append((path, data.joinpath(*canonical.split("/"))))
        for path, target in moves:
            if os.path.lexists(target):
                continue
            try:
                target.parent.mkdir(exist_ok=True)
                os.rename(path, target)
            except OSError as exc:
                remedy = _close_and_press(exc, "Install")
                raise InstallerError(
                    f"{path} could not be renamed to {target.name} in the temporary copy of your "
                    f"client ({exc}), so the map data was not extracted: the extractors open it "
                    f"only by that name. Your own client was not changed.{remedy}"
                ) from exc

    def _refuse_missing_map_data(self, data_dir: Path, original: Path) -> None:
        """Refuse before `up` when the start check would fail, and make the next press extract.

        The evidence file is removed with the refusal, so pressing Install again
        runs this step's extraction again rather than finding it vouched for. Not
        said during "Re-extract map data" (its old data kept in
        `extract.PREVIOUS_DIR`): that press puts the old map data and its record
        back and says so itself (T241).
        """
        tc = self._tc()
        missing = extract.missing_map_data(data_dir, tc.required_maps)
        if not missing:
            return
        evidence = data_dir / extract.EVIDENCE_FILE
        try:
            evidence.unlink(missing_ok=True)
        except OSError as exc:
            cleared = (
                f" Its record {evidence} could not be cleared ({exc}); delete it, then press "
                "Install again to run client-data again."
            )
        else:
            cleared = " Its record was cleared, so pressing Install again runs client-data again."
        if os.path.lexists(data_dir / extract.PREVIOUS_DIR):
            cleared = ""
        raise InstallerError(
            f"The map data the world server needs at start is not all there "
            f"({'; '.join(missing)}), and without it the server stops with 'Unable to load "
            f"critical files'. The client-data step made it from {original}: check that it is "
            f"a complete {self.entry.client.version} client. Nothing was started.{cleared}"
        )

    # -- conf ------------------------------------------------------------------

    def _conf(self, ctx: StageContext) -> Iterator[str]:
        """The image's `.dist` files copied once, then the table's keys patched in place.

        `conf.materialise()` and `conf.apply_table()`, as the CMaNGOS conf stage
        runs them, over this block's table: `Updates.EnableDatabases = 0` (the model
        refuses a world conf without it), `DataDir`, `LogsDir`, the database
        strings, SOAP, the realm id, and `playerbots.conf` -- written into the SAME
        folder as `worldserver.conf`, the only place the world server reads it
        (worldserver/Main.cpp:242-250, facts §4). Then each `from_checkout` file
        the folder lacks, copied whole from the checkout (`place_from_checkout()`:
        Centurion's `AutoBalance.conf`, T179 Task 8).

        The player's random-bot count is carried over, as on CMaNGOS (T117, T179
        Task 5): read back from `playerbots.conf` BEFORE `materialise()`, and only
        once this stage has finished before -- until then a pair in the file is
        the `.dist`'s. The bot dashboard is the Tortoise module's, not carried here.
        """
        tc = self._tc()
        etc_dir = ctx.server_dir / ETC_DIR
        table = (
            bot_count.conf_table(
                tc.conf,
                etc_dir,
                name=tc.conf.playerbots_conf,
                low=bot_count.TC_MIN_KEY,
                high=bot_count.TC_MAX_KEY,
            )
            if ctx.state.has("conf")
            else tc.conf
        )
        # A finished pathfinding set stays switched on (T179 final round).
        table = mmaps.overlay(table, self.entry, ctx.server_dir)
        image_ref = self._image_ref(ctx, tc.extract.image)
        try:
            copied = conf.materialise(
                tc.conf,
                image_ref=image_ref,
                etc_dir=etc_dir,
                copy_from_image=cast("conf.CopyFromImage", self._seams.copy_from_image),
            )
        except docker.DockerCommandError as exc:
            raise InstallerError(
                f"The configuration files could not be copied out of the server image "
                f"{image_ref}: {exc}"
            ) from exc
        except OSError as exc:
            raise InstallerError(f"{etc_dir} could not be written: {exc}") from exc
        for path in copied:
            yield f"Copied {path.name} out of the server image."
        for path in place_from_checkout(tc, ctx.server_dir, etc_dir):
            yield f"Copied {path.name} from the server's source beside {tc.conf.world_conf}."
        try:
            changed = conf.apply_table(table, etc_dir, self._secret_tokens(ctx))
        except InstallerError:
            raise
        except (RuntimeError, OSError) as exc:
            raise InstallerError(
                f"the configuration files in {etc_dir} could not be patched "
                f"({type(exc).__name__}: {exc})."
            ) from exc
        if not changed:
            yield "The configuration files already say what this install needs."
        for path in changed:
            yield f"Patched {path.name}."
        yield (
            f"{tc.conf.playerbots_conf} is beside {tc.conf.world_conf} in {etc_dir}, the one "
            "place the world server reads it."
        )

    # -- movement maps, in the background (Task 4) ----------------------------

    def after_ready(self, server_dir: Path) -> Iterator[str]:
        """Start the movement maps once the server is up; a warning, never a failure.

        Owner decision 4: the install finishes and the player plays first. A job
        already running or a set already made is left alone (`mmaps.start_mmaps()`
        reconciles first), so a resumed install or a rebuild never starts a second.
        Not while the map data is flagged stale (`REEXTRACT_FILE`, fix round 1):
        a set made from it would be made again after "Re-extract map data", which
        starts the job itself once the flag is gone.
        """
        if mmaps.background_block(self.entry) is None:
            return
        if _read_flag(server_dir) is not None:
            yield (
                "The pathfinding data was not started: the map data must be extracted again "
                f"first (“{REEXTRACT_BUTTON}” on the Server tab), and it is made after that."
            )
            return
        try:
            yield self.start_mmaps(server_dir)
        except (InstallerError, OSError, docker.DockerCommandError) as exc:
            yield (
                f"warning: the pathfinding data could not be started in the background ({exc}). "
                "The server runs without it; it can be started again from the Server tab."
            )

    def before_rebuild(
        self, server_dir: Path, route: str, press: str = server_build_presses.REBUILD
    ) -> Iterator[str]:
        """Stop a running movement-map job before `route`; pathfinding stays off (spec §3).

        T209 (owner, 2026-10-04): a Rebuild keeps the server's code, so the job's
        finished tiles stay and the run started once it is ready continues from
        them; every other press (Update to latest, Return to the tested pin) may
        change the generator, so its tiles go and the next run starts from 0 %.
        """
        said = mmaps.stop_for_route(
            server_dir,
            self.entry,
            route,
            clear=press != server_build_presses.REBUILD,
            press=press,
            runner=self._mmaps_runner,
            install_id=self._install_id(server_dir),
        )
        if said is not None:
            yield said

    def mmaps_status(self, server_dir: Path) -> mmaps.MmapsStatus:
        """The job's state for the Server tab (T179 Task 5): `mmaps.mmaps_status()`."""
        return mmaps.mmaps_status(
            server_dir,
            self.entry,
            runner=self._mmaps_runner,
            install_id=self._install_id(server_dir),
        )

    def start_mmaps(self, server_dir: Path) -> str:
        """Start the job with this engine's image, user and install id: `mmaps.start_mmaps()`."""
        return mmaps.start_mmaps(
            server_dir,
            self.entry,
            runner=self._mmaps_runner,
            platform_id=self._seams.platform_id,
            install_id=self._install_id(server_dir),
            user_args=self._user_args(),
        )

    def stop_mmaps(self, server_dir: Path) -> str:
        """Stop the job and keep its finished tiles (T209): `mmaps.stop_mmaps()`."""
        return mmaps.stop_mmaps(
            server_dir,
            self.entry,
            runner=self._mmaps_runner,
            install_id=self._install_id(server_dir),
        )

    # -- the update route (Task 6) ----------------------------------------------

    def check_moved_sources(
        self,
        server_dir: Path,
        moved: Sequence[tuple[EmulatorSource, Path, str]],
        *,
        to_pin: bool,
    ) -> Generator[str, None, object]:
        """What the core checkout's move changed in the SQL snapshot and the map inputs.

        Asked of git in the checkout, read-only, about the commit it moved from and
        the one it stands on now -- the same question in both directions, so
        "Return to the tested pin…" reads the pin's side as the update reads
        upstream's. Before anything is built, written or stopped (owner decision
        2, Review Focus 4): a change to the characters' or the accounts' database
        refuses the whole press here, and the route puts the checkout back.

        A world update an earlier press did not finish (`WORLD_REIMPORT_FILE`) is
        merged in (fix round 1): its files go in again with this press's, so an
        update with no new change of its own still finishes it.
        """
        tc = self._tc()
        changes = SnapshotChanges()
        for source, dest, old in moved:
            if posixpath.normpath(source.dest) != posixpath.normpath(tc.checkout):
                continue
            new = self._seams.head_sha(dest)
            pairs = (
                self._seams.changed_files(dest, old, new, self._watched_paths())
                if new is not None
                else None
            )
            if new is None or pairs is None:
                raise InstallerError(
                    f"Yu'lon could not read what {source.repo} changed between {old[:7]} and "
                    f"{(new or 'the new commit')[:7]} in {dest}, so it cannot tell whether this "
                    f"would change the layout of your characters' or accounts' databases. "
                    f"Nothing was changed."
                )
            changes = self._read_changes(dest, old, new, pairs, repo=source.repo, to_pin=to_pin)
            break
        changes = replace(
            self._with_pending(server_dir, changes), flagged_before=_read_flag(server_dir)
        )
        if changes.everything:
            yield (
                f"Every world table file in {tc.checkout}'s SQL snapshot is imported again with "
                "the new build's servers stopped, before it starts: an earlier world update did "
                "not finish, and what it had left to do could not be read."
            )
        elif changes.reimport or changes.parts:
            count = len(changes.reimport) + len(changes.parts)
            yield (
                f"{count} world table file(s) in {tc.checkout}'s SQL snapshot are imported again "
                "with the new build's servers stopped, before it starts."
            )
        return changes

    def _watched_paths(self) -> tuple[str, ...]:
        """The checkout-relative folders the route asks git about: what the import and maps read."""
        tc = self._tc()
        found = {tc.extract.dbc_overlay_from.strip("/")}
        prefix = f"{tc.checkout.rstrip('/')}/"
        globs = [glob for phase in tc.sql.phases for glob in phase.files]
        globs += [glob for phase in tc.sql.phases for glob in (phase.into_each or {}).values()]
        paths = [*globs, *(pack.source.path or "" for pack in self._map_packs())]
        for path in paths:
            if path.startswith(prefix):
                found.add(posixpath.dirname(path[len(prefix) :]))
        return tuple(sorted(path for path in found if path))

    def _map_packs(self) -> tuple[ClientPack, ...]:
        """The required packs the map data is made from: the server's own, in its checkout."""
        return tuple(pack for pack in self._map_inputs() if pack.source.kind == "checkout")

    def _is_map_data(self, rel: str) -> bool:
        """Is this server-dir-relative file one the map data is made from?"""
        tc = self._tc()
        dbc = posixpath.join(tc.checkout, tc.extract.dbc_overlay_from.strip("/"))
        if rel.startswith(f"{dbc}/"):
            return True
        for pack in self._map_packs():
            path = pack.source.path or ""
            if rel == path or rel.startswith(f"{path}.part"):
                return True
        return False

    def _phase_reading(self, rel: str) -> SqlPhase | None:
        """The plan's phase whose glob matches this file -- directory exact, name by pattern."""
        folder, name = posixpath.split(rel)
        for phase in self._tc().sql.phases:
            for glob in (*phase.files, *(phase.into_each or {}).values()):
                where, pattern = posixpath.split(glob)
                if where == folder and fnmatch.fnmatchcase(name, pattern):
                    return phase
        return None

    def _read_changes(
        self,
        dest: Path,
        old: str,
        new: str,
        pairs: Sequence[tuple[str, str]],
        *,
        repo: str = "",
        to_pin: bool = False,
    ) -> SnapshotChanges:
        """Sort each changed file into what the route does with it, or refuse the press.

        A split table (`_PART`) is imported again whole when any part changed, and
        left whole when any part was removed (fix round 1): what is left of it
        would not be the table upstream has, and importing it would drop the rows
        the removed part held.
        """
        tc = self._tc()
        updates = tc.updates
        reimport_phases = set(updates.reimport_phases) if updates is not None else set()
        skip_lines = updates.skip_lines if updates is not None else {}
        reimport: list[str] = []
        parts: list[str] = []
        left: list[str] = []
        left_parts: list[str] = []
        skipped: list[tuple[str, str]] = []
        map_data: list[str] = []
        notes: list[str] = []
        for status, path in pairs:
            rel = posixpath.join(tc.checkout, path)
            if self._is_map_data(rel):
                map_data.append(rel)
                continue
            phase = self._phase_reading(rel)
            if phase is None:
                logger.info(f"update: {rel} changed, and the install does not import it")
                continue
            if phase.name in reimport_phases:
                part = _PART.match(rel)
                if part is not None and status == "D":
                    left_parts.append(rel)
                elif part is not None:
                    parts.append(part["stem"])
                elif status == "D":
                    left.append(rel)
                else:
                    reimport.append(rel)
                continue
            prefixes = skip_lines.get(rel)
            if prefixes:
                lines = self._seams.changed_lines(dest, old, new, path)
                if lines and all(line[1:].startswith(tuple(prefixes)) for line in lines):
                    beyond = _realm_change_beyond_the_address(lines)
                    if beyond:
                        logger.info(
                            f"update: {rel}'s realm row changed beyond its address: {beyond}"
                        )
                    skipped.append((rel, beyond))
                    continue
            raise UpdateRefused(self._refusal(rel, phase, to_pin=to_pin), repo=repo, commit=new)
        # A split table upstream rewrote as one file (`<stem>.sql` added, its parts
        # removed) is imported again whole from that file, and nothing is left.
        whole = {rel[: -len(".sql")] for rel in reimport}
        left_parts = [
            rel for rel in left_parts if cast(re.Match[str], _PART.match(rel))["stem"] not in whole
        ]
        gone = {cast(re.Match[str], _PART.match(rel))["stem"] for rel in left_parts}
        return SnapshotChanges(
            reimport=tuple(reimport),
            parts=tuple(stem for stem in dict.fromkeys(parts) if stem not in gone | whole),
            left=tuple(left),
            left_parts=tuple(left_parts),
            skipped=tuple(skipped),
            map_data=tuple(map_data),
            notes=tuple(notes),
        )

    def _with_pending(self, server_dir: Path, changes: SnapshotChanges) -> SnapshotChanges:
        """`changes` with the files a world update before this press did not finish (fix round 1).

        A file it names that is no longer in the checkout is left, and said; a
        split table any part of which this move removed is left whole. An
        unreadable record imports every file of the re-import phases again: what
        it named is lost, that something is waiting is not.
        """
        pending = _read_pending(server_dir)
        if pending is None:
            return changes
        if pending.unreadable:
            return replace(
                changes,
                everything=True,
                notes=(
                    *changes.notes,
                    f"{server_dir / WORLD_REIMPORT_FILE} could not be read, so every world table "
                    f"file of {self.entry.name}'s snapshot was imported again.",
                ),
            )
        reimport = list(changes.reimport)
        parts = list(changes.parts)
        left = list(changes.left)
        notes = list(changes.notes)
        gone = {cast(re.Match[str], _PART.match(rel))["stem"] for rel in changes.left_parts}
        # A file this move's commit deleted (`changes.left`) is left, owed or not (fix
        # round 5): the new build replaces the kept one, so nothing it needed is owed
        # any more. A file gone from the disk alone is left only when no kept build
        # needs it (`required`); one that does is imported, and refused as missing.
        for rel in pending.reimport:
            if rel in reimport or rel in left:
                continue
            if not (server_dir / rel).is_file() and rel not in pending.required:
                left.append(rel)
                continue
            # A file the kept build needs is excused only when this move's commit
            # deleted it (`changes.left`, above); gone from the disk alone, it is
            # still imported, and the plan refuses it as missing (fix round 6).
            reimport.append(rel)
        # Split tables the same way (fix round 7): one a part of which this move's
        # commit deleted (`gone`) is left whole; one gone from the disk alone is left
        # only when no kept build needs it, else the plan refuses it as missing.
        for stem in pending.parts:
            if stem in parts or stem in gone:
                continue
            if not _parts_on_disk(server_dir, stem) and stem not in pending.required:
                notes.append(
                    f"{stem}.*.sql is no longer in {self.entry.name}'s snapshot; its table is left "
                    "in your world database as it is."
                )
                continue
            parts.append(stem)
        return replace(
            changes,
            reimport=tuple(reimport),
            parts=tuple(parts),
            left=tuple(left),
            notes=tuple(notes),
        )

    def _refusal(self, rel: str, phase: SqlPhase, *, to_pin: bool = False) -> str:
        """The sentence for a change the route will not apply: which database, which file.

        The owner's words for a characters layout change (Review Focus 4), and the
        same shape for the accounts and for what either database starts with --
        then what the player can do next (T179 final round): the server keeps its
        version; after an update, the way back stays available and the update can
        be taken once Yu'lon supports the change.
        """
        return f"{self._refused_change(rel, phase)} {_refusal_next(to_pin)}"

    def _refused_change(self, rel: str, phase: SqlPhase) -> str:
        """`_refusal()`'s first half: what changed, where, and that nothing was changed."""
        tc = self._tc()
        name = self.entry.name
        databases = self.entry.databases
        layout = tc.updates is not None and rel in tc.updates.layout_files
        if phase.into == databases.characters:
            what, whose = "characters", "your characters"
        elif phase.into == databases.auth:
            what, whose = "accounts", "your accounts"
        else:
            return (
                f"{name} changed {rel}, which goes into its {phase.into or 'own'} database; "
                f"Yu'lon can't apply that change over your server safely yet. Nothing was changed."
            )
        if layout:
            return (
                f"{name} changed its {what} database layout ({rel}); Yu'lon can't move {whose} "
                f"to it safely yet. Nothing was changed."
            )
        return (
            f"{name} changed what its {what} database starts with ({rel}); Yu'lon can't merge "
            f"that into {whose} safely yet. Nothing was changed."
        )

    def databases_a_new_build_changes(self) -> tuple[Db, ...]:
        """Nothing: this tree's world server applies no update of its own at start (T217).

        `Updates.EnableDatabases` is 0 in its conf table, and the catalog refuses
        any other value for this family
        (`catalog.TrinityCoreConf._the_database_updater_stays_off`). The world
        tables an update changes are imported with the servers stopped by
        `servers_down_work()`, whose own `back()` imports them again from the old
        checkout; the characters and accounts layouts are refused outright.
        """
        return ()

    def start_refusal(self, server_dir: Path, *, rebuilding: bool = False) -> str | None:
        """The spine's refusal, then a world update left unfinished (T179).

        A Rebuild with the mixed-tags record there too (T197 fix round 8) is not refused
        for the world update: it is the one way out of the pair. The finish refuses on
        mixed tags (`finish_world_reimport()`), so the Rebuild goes first and leaves the
        world update for the finish. Both records cannot co-occur today: the mixed-tags
        record needs a rollback that moved one image tag of several, and a TrinityCore
        server builds one image.
        """
        refused = super().start_refusal(server_dir, rebuilding=rebuilding)
        if refused is not None:
            return refused
        if owed_start_refusal(server_dir) is not None:
            return None  # reached by the Rebuild alone: any other press was refused above
        return self.family_start_refusal(server_dir)

    def family_start_refusal(self, server_dir: Path) -> str | None:
        """A world update left unfinished (T179); the spine's question, answered (T223)."""
        return world_update_start_refusal(server_dir, press_here=self._seams.distro is None)

    def servers_down_work(
        self, server_dir: Path, changes: object, *, press: str
    ) -> ServersDownWork | None:
        """The changed world tables, imported while the rebuild's servers are down (fix round 1).

        Not after the new build is up, as until fix round 1: a build whose code
        needs a new world column would fail its ready wait on the old table, and be
        rolled back, on every press. So:

        * `prepare()` -- before anything stops -- finds every file and writes
          `WORLD_REIMPORT_FILE` naming them; a file missing from the checkout or
          a record that cannot be written stops the press with nothing stopped;
        * `forward()` -- the new build's servers stopped, before it starts --
          flags the map data, then imports each file as root into the world
          database (the world read again right before the first), and removes the
          record once the last went in. A failure rolls the rebuild back;
        * `back()` -- the rollback's window, the checkout already back on the old
          commit -- puts the map-data flag back as it was and imports the same
          files from the old checkout, so the old build meets its own tables (a
          table only the new version has stays, unread by the old code). If that
          fails, the record stays and the sentence says how to finish it;
        * `keep()` -- the rollback stopped before the old build was back on its
          tags (T197), so the new build stays: when `forward()` never began, the
          record `prepare()` wrote (or, if it never ran, writes now) and the map
          data's flag are left for the new build, so it does not start on the old
          tables -- "Finish the world update" imports them from its checkout.
        """
        if not isinstance(changes, SnapshotChanges):
            return None
        # A record left by an earlier press keeps the work too (T197 fix round 5): this
        # move may have excused every file it named, and then `done()` clears it once the
        # new build is up -- otherwise the rebuild's own start check would refuse the
        # press that repairs it.
        waiting = _read_pending(server_dir) is not None
        if not changes.imports() and not changes.map_data and not waiting:
            return None
        runs: list[sqlplan.PhaseRun] = []
        # What `WORLD_REIMPORT_FILE` held before `prepare()` wrote it (`None`: absent),
        # and whether `forward()` or `back()` began: `settle()` puts the record back
        # as it was only when neither did, so nothing was imported (fix rounds 2-3).
        before: list[bytes | None] = []
        started: list[bool] = []
        prepared: list[bool] = []
        # The record as `forward()` found it, and whether every table it had went in
        # (T197 fix round 6): nothing is removed until the new build is up (`done()`),
        # and a rollback puts back what was owed before this press (`back()`).
        snapshot: list[bytes | None] = []
        imported: list[bool] = []

        def original() -> bytes | None:
            """The record as it was before this press touched it."""
            if before:
                return before[0]
            return snapshot[0] if snapshot else None

        def prepare() -> Iterator[str]:
            if not changes.imports():
                prepared.append(True)
                return
            ctx = self._world_ctx(server_dir, None)
            runs[:] = self._reimport_runs(ctx, changes)
            before[:] = [_read_bytes(server_dir / WORLD_REIMPORT_FILE)]
            self._write_pending(server_dir, runs)
            prepared.append(True)
            yield (
                f"{server_dir / WORLD_REIMPORT_FILE} names the {len(runs)} world table file(s) "
                "to import again, until the last is in."
            )

        def forward(ctx: StageContext) -> Iterator[str]:
            started.append(True)
            snapshot[:] = [_read_bytes(server_dir / WORLD_REIMPORT_FILE)]
            if changes.map_data:
                yield self._flag_map_data(server_dir, changes.map_data)
            if not runs:
                imported.append(True)
                return
            names = [run.rel for run in runs]
            yield (
                f"Importing {len(runs)} world tables again into {self.entry.databases.world} "
                "while the servers are stopped, so the new build starts on them: each replaces "
                "its table whole."
            )
            yield WORLD_TABLES_CANCEL_NOTE
            self._refuse_unless_the_world_is_down(names)
            yield from self._import_runs(ctx, runs, cancel_note=WORLD_TABLES_CANCEL_NOTE)
            imported.append(True)
            yield f"The {len(runs)} world tables are in; the new build starts on them."

        def done() -> Iterator[str]:
            # The new build is up (or kept after its banner): what the record owed is in,
            # or was excused by this move, so it goes now and not before.
            if imported:
                yield from self._forget_pending(server_dir)

        def back(ctx: StageContext) -> Iterator[str]:
            # Like `forward()`: from here the record may name a table this press
            # dropped, so `settle()` must leave it (fix round 3).
            started.append(True)
            if changes.map_data:
                yield from self._put_flag_back(server_dir, changes.flagged_before)
            # The record as it was before this press (fix round 6), names this move
            # excused included: the old build is the one that will run, and it still
            # needs them.
            if snapshot or before:
                _put_back(server_dir / WORLD_REIMPORT_FILE, original())
            if not changes.imports():
                return
            present = self._reimport_runs(ctx, changes, missing_ok=True)
            names = [run.rel for run in present]
            if not present:
                return
            yield (
                f"Importing the same {len(present)} world tables again from the sources the old "
                "build was made from, so it starts on its own tables."
            )
            try:
                self._write_pending(server_dir, present)
                self._refuse_unless_the_world_is_down(names)
                yield from self._import_runs(ctx, present, cancel_note="")
            except InstallerError as exc:
                raise InstallerError(
                    f"{_finish_advice(names, press)} The world tables could not all be put back "
                    f"for the build from before this update: {exc}{_backup_advice(press)}"
                ) from exc
            yield from self._drop_from_pending(server_dir, names)
            yield f"The {len(present)} world tables are back as the old build had them."

        def settle() -> None:
            if started or not before:
                return
            _put_back(server_dir / WORLD_REIMPORT_FILE, before[0])

        def keep() -> Iterator[str]:
            if started:
                # `forward()` began: it flagged the map data first, and what it did
                # not import is still in the record it leaves; all of it in, the
                # kept build has its tables and the record goes.
                yield from done()
                return
            # The flag first (fix round 2): it never raises, and a record that
            # cannot be written below must not cost it.
            if changes.map_data:
                yield self._flag_map_data(server_dir, changes.map_data)
            if prepared:
                return
            try:
                yield from prepare()
            except (InstallerError, OSError) as exc:
                # Fix round 3: the record is written from the names the move
                # already read, without the plan `prepare()` could not expand, so
                # every start refuses and "Finish the world update" (or the next
                # update, which folds it in) imports them from this checkout. A
                # record nobody can read is left: it already means every table.
                waiting = _read_pending(server_dir)
                if not changes.imports() or (waiting is not None and waiting.unreadable):
                    raise
                try:
                    self._write_names(
                        server_dir,
                        set(changes.reimport),
                        set(changes.parts),
                        required=frozenset((*changes.reimport, *changes.parts)),
                    )
                except InstallerError as also:
                    # T223: the clause about Start only when nothing else refuses one --
                    # an untouched exit has already left the untested start refusal.
                    stops = (
                        ""
                        if owed_start_refusal(server_dir) is not None
                        else ", so nothing stops this server starting its new build on the "
                        "old world tables"
                    )
                    raise InstallerError(
                        f"{exc} The world tables the new build needs could not be recorded "
                        f"either ({also.__cause__ or also}){stops}."
                    ) from exc
                raise

        return ServersDownWork(
            prepare=prepare, forward=forward, back=back, settle=settle, keep=keep, done=done
        )

    def after_update(
        self,
        server_dir: Path,
        changes: object,
        *,
        press: str,
        cancel: threading.Event | None,
    ) -> Iterator[str]:
        """Once the new build runs: what the update did not apply, and the map data's sentence.

        The world tables went in while the rebuild's servers were down
        (`servers_down_work()`); this says what is left as it was and why.
        """
        if not isinstance(changes, SnapshotChanges):
            return
        if changes.map_data:
            said = needs_reextract(server_dir, self.entry, press_here=self._seams.distro is None)
            if said is not None:
                yield said
        for rel, beyond in changes.skipped:
            if not beyond:
                yield (
                    f"{rel} changed only the realm row's address, which Yu'lon sets itself, so "
                    "that change is left out."
                )
                continue
            yield (
                f"{rel} changed the realm row, which is left as Yu'lon set it; the changes beyond "
                f"the address Yu'lon sets were logged: {beyond}."
            )
        for rel in changes.left:
            yield (
                f"{rel} is no longer in {self.entry.name}'s snapshot; its table is left in your "
                "world database as it is."
            )
        for rel in changes.left_parts:
            stem = cast(re.Match[str], _PART.match(rel))["stem"]
            yield (
                f"{rel} is no longer in {self.entry.name}'s snapshot, so the table it is part of "
                f"({posixpath.basename(stem)}) is left in your world database as it is: its "
                "other parts alone are not the whole table."
            )
        yield from changes.notes

    # -- finishing a world update that did not finish (fix round 1) -----------------

    def finish_world_reimport(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
    ) -> Iterator[str]:
        """Import the tables `WORLD_REIMPORT_FILE` names with no compile: "Finish the world update".

        The retry for an update whose world tables did not all go in: a failure, a
        Stop or a crash left the record. Refused unless the folder is one this app
        rebuilds and the checkout is on the commit the running build was made
        from -- the files are read from it. The world server is stopped (T158's
        stop and wait) and read again right before the first file; the record goes
        once the last is in; then the server is started and waited for.

        Raises:
            InstallerError: a refusal, or a file that did not go in; the record
                stays, so the press is still offered.
        """
        opts = options or InstallOptions()
        server_dir = self.server_dir(opts)
        state = self._refuse_unless_rebuildable(server_dir)
        pending = _read_pending(server_dir)
        if pending is None:
            raise InstallerError(
                f"No world update of {self.entry.name}'s is waiting to be finished. Nothing was "
                "changed."
            )
        # T197 fix round 8: the finish ends in a start, and mixed image tags refuse every
        # start but the Rebuild's, which goes first (`start_refusal()`). Cannot happen
        # today: a TrinityCore server builds one image, so its tags are never mixed.
        mixed = folder_start_refusal(server_dir, self._seams.image_id)
        if mixed is not None:
            raise InstallerError(f"{mixed} Nothing was changed.")
        self._refuse_unless_the_checkout_is_built(server_dir, state)
        changes = SnapshotChanges(
            reimport=pending.reimport, parts=pending.parts, everything=pending.unreadable
        )
        ctx = self._world_ctx(server_dir, cancel, state=state)
        runs = self._reimport_runs(ctx, changes, missing_ok=True)
        names = [run.rel for run in runs]
        found = set(names) | {m["stem"] for rel in names if (m := _PART.match(rel)) is not None}
        # T197 (fix rounds 4-5): a file the kept build needs is never left out, whether
        # it is gone from the sources or only from the import plan that reads them; nor
        # is a split table it needs, none of whose parts the plan reads (fix round 7).
        missing = [
            f"{rel}.*.sql" if rel in pending.parts else rel
            for rel in pending.required
            if rel not in found
        ]
        if missing:
            raise InstallerError(
                _needed_and_missing(self.entry, missing, past_the_pin=past_the_tested_pin(state))
            )
        for rel in pending.reimport:
            if rel not in found:
                yield (
                    f"{rel} is no longer in {self.entry.name}'s sources; its table is left in "
                    "your world database as it is."
                )
        # T219: the start this ends in refuses on a map-data fingerprint that can be
        # neither written nor removed; asked before the world is stopped for the tables.
        warned = self._refresh_world_data(server_dir)
        if warned is not None:
            yield warned
        if runs:
            yield (
                f"Importing {len(runs)} world tables again into {self.entry.databases.world}, "
                "the ones the last update left waiting: each replaces its table whole."
            )
            yield FINISH_CANCEL_NOTE
            yield from self._stop_the_world_for_tables(ctx, names)
            self._refuse_unless_the_world_is_down(names)
            try:
                yield from self._import_runs(ctx, runs, cancel_note=FINISH_CANCEL_NOTE)
            except InstallerError as exc:
                raise InstallerError(
                    f"{_finish_advice(names, None)} {exc} The world server was left stopped."
                ) from exc
        yield from self._forget_pending(server_dir)
        yield f"The world update is finished. Starting {self.entry.name}'s world server again."
        yield from self._start_after_finish(ctx)
        yield from self.stage_ready(ctx)

    def _start_after_finish(self, ctx: StageContext) -> Iterator[str]:
        """Start the servers once the finish removed the record: replaced, not just started.

        A rollback that could not put every world table back leaves the old build's
        tags and the failed build's STOPPED containers (it will not start a server
        on half its tables, T179 final round). Whether a plain `compose up` replaces
        a container whose image tag moved is not recorded anywhere in this repo
        (`docker.staged_up_argv`), so the finish asks for the replacement outright,
        as a rebuild does; on a server with nothing to replace it costs a recreate
        of containers whose world was already stopped. The volumes are untouched.

        Asks the mixed-tags record again (T197 fix round 8), the belt under the finish's
        own refusal of it: this start must not run two builds side by side either.
        """
        mixed = folder_start_refusal(ctx.server_dir, self._seams.image_id)
        if mixed is not None:
            raise InstallerError(
                f"The world update is finished, but {mixed} The server was not started."
            )
        yield "Starting the server."
        warned = self._put_back_the_zone_file(ctx.server_dir)
        if warned is not None:
            yield warned
        warned = self._refresh_world_data(ctx.server_dir)
        if warned is not None:
            yield warned
        spec = self.entry.container_spec()
        control = _stop_control(ctx, rollback=False)
        try:
            self._seams.recreate(spec, ctx.server_dir, control=control)
        except docker.StopAbandoned as exc:
            raise InstallerError(
                f"The world update is finished, but starting the server was cancelled: {exc}. "
                "Press Start on the Server tab."
            ) from exc
        except docker.DockerCommandError as exc:
            raise InstallerError(
                f"The world update is finished, but the server would not start: {exc}"
            ) from exc

    def _refuse_unless_the_checkout_is_built(self, server_dir: Path, state: InstallState) -> None:
        """The checkout must be on the commit the install record says the running build is from.

        The files are read from the checkout; a checkout on another commit (a
        crash part way through an update, before the commit was recorded) would
        put another version's tables under the running build. The record's
        `source_revs` row when there is one (`head_version`'s spelling), else the
        catalog pin the install was made on.
        """
        tc = self._tc()
        source = next(
            (
                source
                for source in self.entry.emulator.sources
                if posixpath.normpath(source.dest) == posixpath.normpath(tc.checkout)
            ),
            None,
        )
        if source is None:
            return
        dest = server_dir / source.dest
        recorded = state.rev_for(source.repo)
        if recorded is not None:
            now = self._seams.head_version(dest)
            same = None if now is None else now == recorded.built
            built = recorded.built
        else:
            sha = self._seams.head_sha(dest)
            same = None if sha is None else sha == source.rev
            built = (source.rev or "")[:7]
        if same is None:
            raise InstallerError(
                f"Yu'lon could not read which commit {dest} is on, so it cannot tell whether its "
                "world tables are the running build's. Nothing was changed."
            )
        if not same:
            raise InstallerError(
                f"{dest} is not on the commit the running build was made from ({built}), as "
                "after an interrupted update, so the world tables waiting there may not be that "
                "build's. Press "
                f"{server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)}"
                " again: it builds and imports them together. Nothing was changed."
            )

    # -- the world tables' import, shared by the route and the retry -------------------

    def _world_ctx(
        self,
        server_dir: Path,
        cancel: threading.Event | None,
        *,
        state: InstallState | None = None,
    ) -> StageContext:
        """A context for the world-table import and the start after it, outside any stage."""
        return StageContext(
            server_dir=server_dir,
            client_dir=None,
            state=state
            or read_state(server_dir, valid=self.stage_names())
            or InstallState(
                game_id=self.entry.id,
                install_id=self._install_id(server_dir),
                family=self.family,
            ),
            cancel=cancel,
            secrets=self.resolve_secrets(server_dir),
        )

    def _import_runs(
        self, ctx: StageContext, runs: Sequence[sqlplan.PhaseRun], *, cancel_note: str
    ) -> Iterator[str]:
        """Each run through `sqlplan.apply()` as root, one at a time; a failure names the rest."""
        db = self._native().db
        container = self.entry.container_spec().db
        done = 0
        try:
            for run in runs:

                def apply_one(
                    sink: docker.OutputSink, one: sqlplan.PhaseRun = run
                ) -> Iterator[str]:
                    return sqlplan.apply(
                        (one,),
                        container=container,
                        client=db.client,
                        password=ctx.secrets.db_password,
                        exec_stdin=self._seams.exec_stdin,
                        sink=sink,
                        cancel=ctx.cancel,
                        cancel_note=cancel_note,
                    )

                yield from self._stream(apply_one, cancel=ctx.cancel, stage="world-tables")
                done += 1
        except InstallerError as exc:
            remaining = [run.rel for run in runs[done:]]
            raise InstallerError(
                f"{exc} {_listed(remaining)} {'was' if len(remaining) == 1 else 'were'} not "
                "imported again."
            ) from exc

    def _reimport_runs(
        self, ctx: StageContext, changes: SnapshotChanges, *, missing_ok: bool = False
    ) -> tuple[sqlplan.PhaseRun, ...]:
        """The re-import phases' runs for the changed files, in plan order, renames and all.

        `missing_ok` is the rollback's and the retry's: a file that is not in the
        checkout they read is a table the old build never had (or one upstream
        removed), and it is left as it is.
        """
        updates = self._tc().updates
        phases = set(updates.reimport_phases) if updates is not None else set()
        plan = self._tc().sql
        cut = plan.model_copy(
            update={"phases": tuple(phase for phase in plan.phases if phase.name in phases)}
        )
        try:
            every = self._expand(cut, ctx.server_dir, self._secret_tokens(ctx))
        except InstallerError:
            raise
        except (RuntimeError, OSError) as exc:
            raise InstallerError(
                f"The world tables this update changed could not be prepared ({exc}); nothing "
                "was imported again, and the world server was not stopped."
            ) from exc
        wanted = set(changes.reimport)
        stems = set(changes.parts)
        runs = tuple(
            run
            for run in every
            if run.path is not None
            and (
                changes.everything
                or run.rel in wanted
                or ((part := _PART.match(run.rel)) is not None and part["stem"] in stems)
            )
        )
        found = {run.rel for run in runs}
        found_stems = {m["stem"] for rel in found if (m := _PART.match(rel)) is not None}
        # A split table none of whose parts the plan reads is missing too (fix round 7).
        missing = sorted(wanted - found) + sorted(f"{stem}.*.sql" for stem in stems - found_stems)
        if missing and not missing_ok:
            raise InstallerError(
                f"The world tables this update changed are not all in the server's sources "
                f"({_listed(missing)}); nothing was imported again, and the world server was "
                "not stopped."
            )
        return runs

    def _write_pending(self, server_dir: Path, runs: Sequence[sqlplan.PhaseRun]) -> None:
        """`WORLD_REIMPORT_FILE`, whole: what is waiting now, with what was waiting before.

        Raises `InstallerError` when it cannot be written: then nothing is stopped
        or imported, since a failure after it would leave nobody knowing what to
        finish.
        """
        reimport = {run.rel for run in runs if _PART.match(run.rel) is None}
        parts = {match["stem"] for run in runs if (match := _PART.match(run.rel)) is not None}
        self._write_names(server_dir, reimport, parts)

    def _write_names(
        self,
        server_dir: Path,
        reimport: set[str],
        parts: set[str],
        *,
        required: frozenset[str] = frozenset(),
    ) -> None:
        """`_write_pending()` from the files' names and split tables' stems (T197 fix round 3).

        `required`: files, and split tables' stems (fix round 7), a kept build needs
        (fix round 4), which "Finish the world update" and the next update refuse to
        leave out when they are missing.
        """
        path = server_dir / WORLD_REIMPORT_FILE
        before = _read_pending(server_dir)
        needed = set(required)
        if before is not None and not before.unreadable:
            reimport |= set(before.reimport)
            parts |= set(before.parts)
            needed |= set(before.required)
        body: dict[str, object] = {
            "version": 1,
            "reimport": sorted(reimport),
            "parts": sorted(parts),
        }
        if needed:
            body["required"] = sorted(needed)
        staged = path.with_name(path.name + ".yulon-new")
        try:
            staged.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
            os.replace(staged, path)
        except OSError as exc:
            try:
                staged.unlink(missing_ok=True)
            except OSError as also:
                logger.warning(f"could not remove {staged}: {also}")
            raise InstallerError(
                f"{path} could not be written ({exc}), and it is what says which world tables "
                "are still to be imported if this stops part way, so the world server was not "
                "stopped and nothing was imported again."
            ) from exc

    def _drop_from_pending(self, server_dir: Path, names: Sequence[str]) -> Iterator[str]:
        """Take the files just imported off `WORLD_REIMPORT_FILE`; the rest stays owed (T197).

        The rollback's half: what it imported from the old checkout is in, and what the
        record owed before the press and it did not import stays. Gone once empty.
        """
        pending = _read_pending(server_dir)
        if pending is None or pending.unreadable:
            # Nothing to take off, or a record that means every table: it stays.
            return
            yield ""  # pragma: no cover - makes this a generator
        done = set(names)
        stems = {m["stem"] for rel in names if (m := _PART.match(rel)) is not None}
        reimport = [rel for rel in pending.reimport if rel not in done]
        parts = [stem for stem in pending.parts if stem not in stems]
        if not reimport and not parts:
            yield from self._forget_pending(server_dir)
            return
        body: dict[str, object] = {"version": 1, "reimport": reimport, "parts": parts}
        required = [rel for rel in pending.required if rel in reimport or rel in parts]
        if required:
            body["required"] = required
        _put_back(server_dir / WORLD_REIMPORT_FILE, (json.dumps(body, indent=2) + "\n").encode())

    def _forget_pending(self, server_dir: Path) -> Iterator[str]:
        """Remove `WORLD_REIMPORT_FILE` once every file it names went in; a warning if it stays."""
        path = server_dir / WORLD_REIMPORT_FILE
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            yield (
                f"warning: {path} could not be removed ({exc}); the Server tab will go on "
                f"offering “{FINISH_WORLD_BUTTON}” for tables that are already in."
            )

    def _stop_the_world_for_tables(self, ctx: StageContext, names: Sequence[str]) -> Iterator[str]:
        """Stop a running world server before its tables are replaced; T158's stop and wait."""
        container = self.entry.container_spec().world
        try:
            running: bool | None = self._seams.ask_world_running(container)
        except Exception:  # noqa: BLE001 - "could not ask" is said below in words
            running = None
        if running is False:
            return
        if running is None:
            raise InstallerError(
                f"Yu'lon could not tell whether {self.entry.name}'s world server is running, so "
                f"it could not stop it, and these world tables were not imported again: "
                f"{_listed(names)}. Check that Docker is running, then press "
                f"“{FINISH_WORLD_BUTTON}” again."
            )
        yield (
            f"Stopping the world server ({container}) so its tables can be replaced; it saves "
            "as it does on Stop."
        )
        spec = self.entry.container_spec()
        control = _stop_control(ctx, rollback=False)

        def stop_it(say: docker.OutputSink) -> None:
            self._seams.stop_world(
                [container],
                known=(spec,),
                control=replace(control, say=say),
                deadline=docker.STOP_PROCESS_DEADLINE_SECONDS,
            )

        try:
            yield from _speaking(stop_it, control.abandon)
        except (docker.StopAbandoned, docker.DockerCommandError) as exc:
            raise InstallerError(
                f"Yu'lon could not stop {self.entry.name}'s world server ({exc}), so these world "
                f"tables were not imported again: {_listed(names)}. Press "
                f"“{FINISH_WORLD_BUTTON}” again once it can be stopped."
            ) from exc
        yield "The world server is stopped."

    def _refuse_unless_the_world_is_down(self, names: Sequence[str]) -> None:
        """The second reading, right before the first table is written: down, or nothing is.

        Its own sentence, never followed by "the world server was left stopped":
        a world that is running again was not left stopped (fix round 1).
        """
        container = self.entry.container_spec().world
        try:
            running: bool | None = self._seams.ask_world_running(container)
        except Exception:  # noqa: BLE001 - any failure to ask is "could not ask"
            running = None
        if running is False:
            return
        raise InstallerError(
            f"{self.entry.name}'s world server "
            f"{'is running again' if running else 'could not be read'}, and it holds its "
            "tables in memory and writes back over them, so none of these was imported again: "
            f"{_listed(names)}."
        )

    def _flag_map_data(self, server_dir: Path, changed: Sequence[str]) -> str:
        """Record that the map data must be extracted again; the Server tab's sentence."""
        path = server_dir / REEXTRACT_FILE
        before = _read_flag(server_dir) or ()
        try:
            _write_flag(server_dir, sorted({*before, *changed}))
        except OSError as exc:
            where = (
                f"press \u201c{REEXTRACT_BUTTON}\u201d on the Server tab once the server is "
                "stopped"
                if self._seams.distro is None
                else "open Yu'lon inside the WSL distro this server lives in, stop the server "
                f"and press \u201c{REEXTRACT_BUTTON}\u201d on its Server tab there (this server "
                "is inside a WSL distro)"
            )
            return (
                f"warning: {_listed(changed)} changed, so {self.entry.name}'s map data must be "
                f"extracted again, and {path} could not be written to remember it ({exc}): "
                f"{where}."
            )
        said = needs_reextract(server_dir, self.entry, press_here=self._seams.distro is None)
        return said or f"{self.entry.name}'s map data must be extracted again."

    def _put_flag_back(self, server_dir: Path, before: tuple[str, ...] | None) -> Iterator[str]:
        """The rollback's half of `_flag_map_data()`: the flag as it was before this press."""
        path = server_dir / REEXTRACT_FILE
        try:
            if before is None:
                path.unlink(missing_ok=True)
            else:
                _write_flag(server_dir, before)
        except OSError as exc:
            yield (
                f"warning: {path} could not be put back as it was ({exc}); the Server tab may ask "
                "for an extraction the old build does not need."
            )

    # -- extracting the map data again (Task 6) ---------------------------------

    def reextract(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
    ) -> Iterator[str]:
        """Run the client-data stage again, from a new temporary client; the movement maps go.

        The press `needs_reextract()` asks for. With the world server stopped (it
        reads its map files while it runs) and any movement-map job stopped, the
        map data it replaces -- every extraction tool's folders and the record --
        is moved aside within `data/` (`extract.set_aside()`, T241), so the tools
        start into empty folders: `vmap4extractor` refuses a `Buildings/` holding
        the `dir_bin` every finished extraction leaves. Then `client-data` runs as
        the install runs it: the same temporary extraction client, the same DBC
        overlay, the same start check.

        Nothing old goes before the new data is in. Only then are the movement
        maps thrown away through `mmaps.discard()` (they describe the old maps),
        the set-aside data deleted, the flag removed, and the movement maps started
        again in the background. A failure, a Stop or a closed stream puts the old
        map data back with its record (`extract.put_back()`), and the movement maps
        and pathfinding are as they were. A press that died harder than that (a
        crash) leaves the old data aside, and the next press puts it back first;
        only a press whose new data is in marks the old as superseded
        (`extract.supersede()`), and only that is ever deleted.
        The player's own client is the folder given, or the one the map data was
        last made from.

        Raises:
            InstallerError: the folder is not one this app installed, no client
                folder is known, the world server is or may be running, or a step
                failed; the flag stays, so the press is still offered.
        """
        opts = options or InstallOptions()
        server_dir = self.server_dir(opts)
        state = self._refuse_unless_rebuildable(server_dir)
        probe = StageContext(
            server_dir=server_dir,
            client_dir=opts.client_dir,
            state=state,
            cancel=cancel,
            secrets=self.resolve_secrets(server_dir),
        )
        data_dir = self._data_dir(probe)
        client = opts.client_dir
        if client is None:
            evidence = extract.read_evidence(data_dir)
            client = Path(evidence.client_path) if evidence and evidence.client_path else None
            if client is None:
                kept = extract.read_evidence(data_dir / extract.PREVIOUS_DIR)
                client = Path(kept.client_path) if kept and kept.client_path else None
        if client is None:
            raise InstallerError(
                f"Yu'lon does not know which game client {self.entry.name}'s map data was made "
                "from. Pick the client folder, then press "
                f"“{REEXTRACT_BUTTON}” again. Nothing was changed."
            )
        self._refuse_a_running_world_for_maps()
        yield f"Extracting {self.entry.name}'s map data again into {data_dir}, from {client}."
        background = mmaps.background_block(self.entry) is not None
        ident = self._install_id(server_dir) if background else ""
        if background:
            stopped = mmaps.stop_for_route(
                server_dir,
                self.entry,
                "the extraction",
                # Not cleared (T241): the old map data comes back unchanged if the
                # extraction does not finish, and the tiles made from it with it;
                # `mmaps.discard()` below throws them away once the new data is in.
                clear=False,
                press=REEXTRACT_BUTTON,
                runner=self._mmaps_runner,
                install_id=ident,
                kept_note=(
                    "If the extraction does not finish, the run continues from them; once it "
                    "has, they are removed with the old map data."
                ),
            )
            if stopped is not None:
                yield stopped
        yield from self._settle_an_earlier_press(server_dir, data_dir)
        plan = self._tc().extract
        extract.set_aside(data_dir, extract.replaced_names(plan, also=(plan.dbc_overlay_to,)))
        ctx = replace(probe, client_dir=client)
        stage = replace(
            self.stage_named("client-data"), recorded=False, cancel_note=REEXTRACT_CANCEL_NOTE
        )
        try:
            # Inside the `try`: a stream closed at this very line is a way out too.
            yield (
                f"The map data in {data_dir} was moved aside, so the tools start into empty "
                "folders; it is put back if this extraction does not finish."
            )
            yield from self._staged((stage,), ctx)
        except BaseException as failure:
            told = self._put_the_old_map_data_back(data_dir)
            if background:  # asked of `data/` as it is now: old data not back says 0
                tiles = self._kept_for_the_next_run(server_dir, ident)
                if tiles:
                    told = f"{told} {reextract_kept_tiles(tiles)}"
            if isinstance(failure, InstallerError):  # its words are what the person reads
                failure.args = (f"{failure} {told}",)  # same object: its type is kept
            raise
        try:
            extract.supersede(data_dir)
        except OSError as exc:
            yield (
                f"warning: the map data from before this press, in "
                f"{data_dir / extract.PREVIOUS_DIR}, could not be deleted ({exc}). It takes "
                "space; delete that folder when the server is stopped. Until it is gone, a "
                f"later “{REEXTRACT_BUTTON}” may put it back if the map data in place no longer "
                "matches the server's files by then."
            )
        if background:
            # After the new map data is in, so a failure here must not fail the press
            # (scoped re-review of e457b29e): the map data is done; this is said.
            try:
                mmaps.discard(server_dir, self.entry, install_id=ident)
            except mmaps.MmapsError as exc:
                logger.warning(f"the old pathfinding data could not be removed: {exc}")
                yield (
                    "warning: the pathfinding data made from the old map data could not be "
                    f"removed ({exc}), and the server may go on using it. Stop the server and "
                    f"delete {data_dir / mmaps.MMAPS_DIR}; Yu'lon then switches pathfinding off, "
                    "and “Make the pathfinding data” on the Server tab makes it again from the "
                    "new map data."
                )
            else:
                yield (
                    "The pathfinding data made from the old map data was removed, and "
                    "pathfinding is off until it has been made again."
                )
        try:
            (server_dir / REEXTRACT_FILE).unlink(missing_ok=True)
        except OSError as exc:
            yield (
                f"warning: {server_dir / REEXTRACT_FILE} could not be removed ({exc}); the "
                "Server tab will go on asking for an extraction that has been done."
            )
        yield from self.after_ready(server_dir)
        yield (
            f"{self.entry.name}'s map data was extracted again. Press Start on the Server tab "
            "to run the server on it."
        )

    def _settle_an_earlier_press(self, server_dir: Path, data_dir: Path) -> Iterator[str]:
        """What an earlier press left under `data/`: replaced data deleted, kept data put back.

        `extract.PREVIOUS_DIR` is deleted when it is marked replaced
        (`extract.SUPERSEDED_MARK`), or when it is not but still holds its own
        record while the map data in place is whole (`_map_data_whole()`): that
        press finished and could not make the mark (cold review of 4d672a26), and
        putting the old data back would throw the new away. Otherwise it is old
        map data a press set aside and never settled -- it died part way, in the
        extraction or in putting the data back -- so it is the last whole map
        data, and it is put back over whatever is in place. Never decided from
        the record in `data/` alone: a put-back restores the record first
        (`extract.put_back()`), and an old record put back says nothing about the
        folders still aside (Codex adversarial review, T241).
        """
        aside = data_dir / extract.PREVIOUS_DIR
        try:
            if extract.drop_superseded(data_dir) or not os.path.lexists(aside):
                return
            if os.path.lexists(aside / extract.EVIDENCE_FILE) and self._map_data_whole(
                server_dir, data_dir
            ):
                extract.supersede(data_dir)
                yield (
                    f"An earlier “{REEXTRACT_BUTTON}” finished but left the map data from before "
                    "it; that was deleted first."
                )
                return
            extract.put_back(data_dir)
        except OSError as exc:
            raise InstallerError(
                f"An earlier “{REEXTRACT_BUTTON}” left map data in {aside}, and it could not be "
                f"settled ({exc}), so nothing was extracted. Close whatever is using that "
                f"folder, then press “{REEXTRACT_BUTTON}” again."
            ) from exc
        yield (
            f"An earlier “{REEXTRACT_BUTTON}” did not finish; the map data from before it was "
            "put back first."
        )

    def _map_data_whole(self, server_dir: Path, data_dir: Path) -> bool:
        """Is the map data in `data/` what a finished client-data stage leaves? (T241)

        Every tool recorded for its argv with its counts met, the server's own
        DBCs laid over the extracted ones (`extract.overlaid()`), and the start
        check's maps and vmaps there -- the three things the stage does, each
        read off the disk. Anything unread answers False, and the old data comes
        back.
        """
        tc = self._tc()
        current = extract.read_evidence(data_dir)
        if current is None:
            return False
        if not all(
            extract.satisfied(tool.name, tool.argv, tool.produces, data_dir, current, current)
            for tool in tc.extract.tools
        ):
            return False
        if extract.missing_map_data(data_dir, tc.required_maps):
            return False
        return extract.overlaid(
            server_dir / tc.checkout / tc.extract.dbc_overlay_from,
            data_dir / tc.extract.dbc_overlay_to,
        )

    def _kept_for_the_next_run(self, server_dir: Path, ident: str) -> int:
        """`mmaps.continues_from()`, or 0 when it cannot be asked: a failure is being told."""
        try:
            return mmaps.continues_from(server_dir, self.entry, install_id=ident)
        except Exception as exc:  # noqa: BLE001 - the press's own failure is what is said
            logger.warning(f"could not tell whether the pathfinding tiles are kept: {exc}")
            return 0

    def _put_the_old_map_data_back(self, data_dir: Path) -> str:
        """After a failed, stopped or closed extraction: the old map data back; what to say."""
        try:
            extract.put_back(data_dir)
        except OSError as exc:
            logger.warning(f"the map data set aside in {data_dir} could not be put back: {exc}")
            return (
                f"The map data from before this press could not be put back ({exc}); it is kept "
                f"in {data_dir / extract.PREVIOUS_DIR}, and pressing “{REEXTRACT_BUTTON}” again "
                "settles it first: it keeps the new map data if that is whole, and puts this "
                "back otherwise."
            )
        return REEXTRACT_PUT_BACK

    def _refuse_a_running_world_for_maps(self) -> None:
        """A world server that is or may be running reads the map files about to be replaced."""
        container = self.entry.container_spec().world
        try:
            running: bool | None = self._seams.ask_world_running(container)
        except Exception:  # noqa: BLE001 - any failure to ask is "could not ask"
            running = None
        if running is False:
            return
        if running is None:
            raise InstallerError(
                f"Yu'lon could not tell whether {self.entry.name}'s world server is running, and "
                "a running one reads the map files this replaces. Check that Docker is running, "
                f"press Stop on the Server tab if the server is up, then press "
                f"“{REEXTRACT_BUTTON}” again. Nothing was changed."
            )
        raise InstallerError(
            f"{self.entry.name}'s world server is running, and it reads the map files this "
            f"replaces. Press Stop on the Server tab, then press “{REEXTRACT_BUTTON}” "
            "again. Nothing was changed."
        )

    # -- import ----------------------------------------------------------------

    def _expand(
        self, plan: SqlPlan, server_dir: Path, tokens: Mapping[str, str]
    ) -> tuple[sqlplan.PhaseRun, ...]:
        """The plan's runs with the block's database-name renames on the files it lists.

        `centurion/sql/import.sh:41-43` (facts §2): the schema and routine dumps'
        triggers and procedures name the live realm's databases, and `sed` puts the
        installed names in before they load. The renames ride on those files' runs
        only; every other file streams as it lies on disk.
        """
        sql = self._tc().sql
        return sqlplan.expand(
            plan,
            server_dir,
            self._schemas(),
            tokens,
            renames=sql.renames,
            rename_files=sql.rename_files,
        )


def checkout_conf_source(tc: TrinityCoreData, server_dir: Path, name: str) -> Path:
    """Where `name`, one of `conf.from_checkout`'s, is copied from: inside the core checkout."""
    return server_dir / tc.checkout / tc.conf.from_checkout[name]


def place_from_checkout(tc: TrinityCoreData, server_dir: Path, etc_dir: Path) -> tuple[Path, ...]:
    """Copy each `conf.from_checkout` file that is missing from `etc_dir`; return those made.

    Centurion's `AutoBalance.conf` (the lead's ruling, T179 Task 8): its README says to
    copy the live realm's `centurion/conf/AutoBalance.conf` (README.md:203-204), the
    image installs no `.dist` of it, and the world server finds it in its own conf's
    folder (`AutoBalance.Conf` defaults to `conf/AutoBalance.conf` and falls back to
    the conf's directory, AutoBalanceConfig.cpp:177-213, 761). As `conf.materialise()`
    treats the image's files: one already there is never touched, because by the
    second press it may be the player's own; Reset to default puts the checkout's
    back. Written to a temporary name and renamed, so a press that dies part way
    leaves no half file the next press would take for the player's.

    Raises:
        InstallerError: the checkout lacks the file, or it could not be copied.
    """
    made: list[Path] = []
    for name in tc.conf.from_checkout:
        target = etc_dir / name
        if target.exists():
            continue
        source = checkout_conf_source(tc, server_dir, name)
        partial = etc_dir / f"{name}.yulon-partial"
        try:
            body = source.read_bytes()
        except OSError as exc:
            raise InstallerError(
                f"{name} could not be read from the server's source at {source} ({exc}), so it "
                f"was not placed beside {tc.conf.world_conf}."
            ) from exc
        try:
            etc_dir.mkdir(parents=True, exist_ok=True)
            partial.write_bytes(body)
            os.chmod(partial, conf.CONF_MODE)
            os.replace(partial, target)
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise InstallerError(f"{target} could not be written: {exc}") from exc
        made.append(target)
    return tuple(made)


def _refusal_next(to_pin: bool) -> str:
    """What a refused update or return leaves the player able to do (T179 final round)."""
    keeps = "Your server keeps running the version it has."
    if to_pin:
        return keeps
    back = server_build_presses.RETURN_TO_PIN
    return (
        f"{keeps} \u201c{back}\u201d stays available; this update can be taken once Yu'lon "
        "supports the change."
    )


def _backup_advice(press: str | None) -> str:
    """Where the tables as they were may be, for the press that changed them (fix round 1).

    Only "Update the server to latest…" offers a backup before it starts, and
    only if the player took it; "Return to the tested pin…" offers none, so it
    says nothing about one.
    """
    if press != server_build_presses.UPDATE_TO_LATEST:
        return ""
    return (
        " If you took the backup offered before the update, it has them as they were (Restore on "
        "the Maintenance tab)."
    )


def _finish_advice(names: Sequence[str], press: str | None) -> str:
    """The remedy for world tables that did not go in: the retry press, or the same press again."""
    again = (
        "the update again"
        if press is None
        else f"\u201c{press}\u201d under \u201c{server_build_presses.SERVER_BUILD}\u201d again"
    )
    return (
        f"Press \u201c{FINISH_WORLD_BUTTON}\u201d on the Server tab (or {again}) to import "
        f"{_listed(names)} again."
    )


def _listed(paths: Sequence[str]) -> str:
    return ", ".join(paths)


def _read_flag(server_dir: Path) -> tuple[str, ...] | None:
    """The changed files `REEXTRACT_FILE` names; None with no flag, `()` when it cannot be read.

    A flag nobody can read still asks for the extraction: what it would have said
    is lost, the need is not.
    """
    path = server_dir / REEXTRACT_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(f"{path} could not be read ({exc}); the map data is still flagged")
        return ()
    try:
        changed = json.loads(text)["changed"]
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning(f"{path} is not one Yu'lon wrote ({exc}); the map data is still flagged")
        return ()
    if not isinstance(changed, list) or not all(isinstance(item, str) for item in changed):
        return ()
    return tuple(changed)


def _write_flag(server_dir: Path, changed: Sequence[str]) -> None:
    """`REEXTRACT_FILE`, whole: a temporary file renamed into place, removed on failure."""
    path = server_dir / REEXTRACT_FILE
    staged = path.with_name(path.name + ".yulon-new")
    try:
        staged.write_text(
            json.dumps({"version": 1, "changed": list(changed)}, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staged, path)
    except OSError:
        staged.unlink(missing_ok=True)
        raise


def _read_bytes(path: Path) -> bytes | None:
    """A file's bytes, or None when there is none; an unreadable one reads as nothing to keep."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning(f"could not read {path} ({exc}); it is not put back after a failed press")
        return None


def _put_back(path: Path, body: bytes | None) -> None:
    """`path` as it was: `body` written whole (staged and renamed), or removed. Never raises."""
    staged = path.with_name(path.name + ".yulon-new")
    try:
        if body is None:
            path.unlink(missing_ok=True)
            return
        staged.write_bytes(body)
        os.replace(staged, path)
    except OSError as exc:
        logger.warning(f"could not put {path} back as it was before the press: {exc}")
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass


@dataclass(frozen=True)
class _Pending:
    """What `WORLD_REIMPORT_FILE` names; `unreadable` when it is there and says nothing usable."""

    reimport: tuple[str, ...] = ()
    parts: tuple[str, ...] = ()
    unreadable: bool = False
    required: tuple[str, ...] = ()
    """Files a kept build needs (T197 fix round 4), and the stems of split tables it needs
    (fix round 7): one missing is never "left", it refuses."""


def _read_pending(server_dir: Path) -> _Pending | None:
    """The world update left waiting; None with no record. A record nobody can read still waits."""
    path = server_dir / WORLD_REIMPORT_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(f"{path} could not be read ({exc}); every world table is imported again")
        return _Pending(unreadable=True)
    try:
        raw = json.loads(text)
        reimport, parts = raw["reimport"], raw["parts"]
        required = raw.get("required", [])
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.warning(
            f"{path} is not one Yu'lon wrote ({exc}); every world table is imported again"
        )
        return _Pending(unreadable=True)
    if not all(
        isinstance(names, list) and all(isinstance(name, str) for name in names)
        for names in (reimport, parts, required)
    ):
        return _Pending(unreadable=True)
    return _Pending(reimport=tuple(reimport), parts=tuple(parts), required=tuple(required))


def _needed_and_missing(entry: CatalogEntry, missing: Sequence[str], *, past_the_pin: bool) -> str:
    """Why "Finish the world update" stops: a file the kept build needs is not imported (T197).

    Names the two ways out: the file put back, or a new build in place of the kept one
    -- "Return to the tested pin…" only where the tab offers it (`past_the_pin`).
    """
    one = len(missing) == 1
    presses = f"\u201c{server_build_presses.UPDATE_TO_LATEST}\u201d" + (
        f" or \u201c{server_build_presses.RETURN_TO_PIN}\u201d" if past_the_pin else ""
    )
    return (
        f"{_listed(missing)} {'is' if one else 'are'} not among the world table files "
        f"{entry.name}'s import reads from its sources, and the build this server was left on "
        f"needs {'that table' if one else 'those tables'}, so the world update was not "
        "finished and the server is still refused a start. Nothing was imported. Put "
        f"{'it' if one else 'them'} back in the sources and press "
        f"\u201c{FINISH_WORLD_BUTTON}\u201d again, or press {presses} under "
        f"\u201c{server_build_presses.SERVER_BUILD}\u201d on the Modules tab, which builds a "
        "new version in place of the one this server was left on."
    )


WORLD_UPDATE_UNFINISHED = (
    "This server's last update didn't finish importing its world tables. Press "
    f"\u201c{FINISH_WORLD_BUTTON}\u201d first."
)
"""Why no start is allowed while `WORLD_REIMPORT_FILE` is there (T179 final round, lead ruling)."""


def world_update_start_refusal(server_dir: Path, *, press_here: bool = True) -> str | None:
    """Why this server must not start now: a world update left unfinished, or None.

    The world server would load tables half of one version and half of another.
    A record that is there but cannot be read refuses too: what it named is lost,
    that something waits is not (`_read_pending`). Asked by every start -- the
    controller's `start_guard` and the engine's `start_refusal()` -- except the
    finish itself, which removes the record before it starts the server.
    `press_here` False is a tab with no "Finish the world update" (a server in a
    WSL distro): the update press, which finishes it too, is named instead.
    """
    if _read_pending(server_dir) is None:
        return None
    if press_here:
        return WORLD_UPDATE_UNFINISHED
    update = server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)
    return (
        "This server's last update didn't finish importing its world tables. Press "
        f"{update} again first."
    )


def _parts_on_disk(server_dir: Path, stem: str) -> bool:
    """Is any `<stem>.<n>.sql` part of a split table in the checkout?"""
    folder = (server_dir / stem).parent
    name = posixpath.basename(stem)
    try:
        return any(
            (match := _PART.match(child.name)) is not None and match["stem"] == name
            for child in folder.iterdir()
        )
    except OSError:
        return False


def pending_world_reimport(
    server_dir: Path, entry: CatalogEntry, *, press_here: bool = True
) -> str | None:
    """The Server tab's sentence when a world update did not finish; None when none waits (fix 1).

    A file read and nothing else, like `needs_reextract()`. Set before an update
    route stops the world for its tables, cleared once the last went in, by that
    press or by `TrinityCoreInstaller.finish_world_reimport()`. `press_here` False
    is a tab with no "Finish the world update": the update press is named instead.
    """
    native = entry.install.native
    if native is None or native.trinitycore is None:
        return None
    pending = _read_pending(server_dir)
    if pending is None:
        return None
    names = [*pending.reimport, *(f"{stem}.*.sql" for stem in pending.parts)]
    what = _listed(names) if names and not pending.unreadable else "the world tables it changed"
    said = (
        f"{entry.name}'s last update did not finish importing its world tables: {what} still "
        "have to go in, and the world server may be stopped until they do."
    )
    if not press_here:
        update = server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)
        return f"{said} Press {update} again to import them."
    return f"{said} Press \u201c{FINISH_WORLD_BUTTON}\u201d on the Server tab to import them."


def _realm_rows(
    lines: Sequence[str],
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """The realm rows the `-` and `+` lines insert, by id: `{id: {column: value}}` each side."""
    sides: tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]] = ({}, {})
    for line in lines:
        side = sides[0] if line.startswith("-") else sides[1]
        found = _REALM_INSERT.match(line[1:])
        if found is None:
            continue
        names: tuple[str, ...] = _REALM_COLUMNS
        if found["columns"] is not None:
            names = tuple(name.strip().strip("`").strip() for name in found["columns"].split(","))
        for row in _sql_tuples(found["rest"]):
            columns = dict(zip(names, row, strict=False))
            side[columns.get("id", "")] = columns
    return sides


def _sql_tuples(text: str) -> list[list[str]]:
    """`(1,'a,b','x'),(2,...)` as lists of raw values, quotes taken off; a crude dump reader."""
    rows: list[list[str]] = []
    row: list[str] | None = None
    value: list[str] = []
    quoted = False
    index = 0
    while index < len(text):
        char = text[index]
        if quoted:
            if char == "\\" and index + 1 < len(text):
                value.append(text[index + 1])
                index += 2
                continue
            if char == "'":
                if text[index + 1 : index + 2] == "'":
                    value.append("'")
                    index += 2
                    continue
                quoted = False
            else:
                value.append(char)
        elif char == "'":
            quoted = True
        elif char == "(" and row is None:
            row, value = [], []
        elif char == "," and row is not None:
            row.append("".join(value).strip())
            value = []
        elif char == ")" and row is not None:
            row.append("".join(value).strip())
            rows.append(row)
            row, value = None, []
        elif row is not None:
            value.append(char)
        index += 1
    return rows


def _realm_change_beyond_the_address(lines: Sequence[str]) -> str:
    """What the realm row's change touches beyond the address Yu'lon sets; "" when nothing.

    `gamebuild` 12342 -> 12343, a new `name`: said and logged, never applied
    (fix round 1). A row added or removed, or lines that cannot be read as rows,
    is said as such.
    """
    before, after = _realm_rows(lines)
    if not before and not after:
        return "lines Yu'lon could not read as realm rows"
    said: list[str] = []
    for ident in sorted({*before, *after}):
        old, new = before.get(ident), after.get(ident)
        if old is None or new is None:
            said.append(f"realm {ident or '?'} {'added' if old is None else 'removed'}")
            continue
        for column in sorted({*old, *new} - _YULONS_REALM_COLUMNS):
            if old.get(column) != new.get(column):
                said.append(f"{column} {old.get(column, '?')} -> {new.get(column, '?')}")
    return ", ".join(said)


def needs_reextract(
    server_dir: Path, entry: CatalogEntry, *, press_here: bool = True
) -> str | None:
    """The Server tab's sentence when the map data must be extracted again; None when not (Task 6).

    A file read and nothing else -- no Docker, no git -- so the tab can ask it on
    every reload. Set by an update or a return that changed the server's DBC files
    or a required client pack; cleared by `TrinityCoreInstaller.reextract()`.
    `press_here` False is a tab with no such press (a server inside a WSL distro,
    fix round 1): the sentence says where the press is instead.
    """
    native = entry.install.native
    if native is None or native.trinitycore is None:
        return None
    changed = _read_flag(server_dir)
    if changed is None:
        return None
    what = _listed(changed) if changed else "the files its map data is made from"
    said = f"{entry.name}'s map data must be extracted again: the server's update changed {what}."
    if not press_here:
        return (
            f"{said} This Yu'lon cannot extract it for a server inside a WSL distro: open "
            "Yu'lon inside that distro, stop the server and press "
            f"\u201c{REEXTRACT_BUTTON}\u201d on its Server tab there."
        )
    return (
        f"{said} Stop the server, then press \u201c{REEXTRACT_BUTTON}\u201d on the Server tab; "
        "it uses your game client, and the pathfinding data is made again after it."
    )


def _lays_archives(pack: ClientPack) -> bool:
    """Does `pack` put a `.MPQ` under the client's `Data/`, where the extractors read?

    A named member counts when its target is a `Data/...MPQ` file; a whole-zip
    member (`"*"`) when its folder is under `Data/`, whose files cannot be named
    here and may be archives.
    """
    for rule in pack.install:
        target = rule.to if rule.to is not None else rule.to_dir
        if target is None:
            continue
        parts = PurePosixPath(target).parts
        if not parts or parts[0].casefold() != "data":
            continue
        if rule.to is None or rule.to.casefold().endswith(".mpq"):
            return True
    return False


def _packs_salt(packs: Sequence[ClientPack], server_dir: Path) -> str:
    """What the extraction was made from beyond the client: each required pack and its checksum.

    The checksum the pack must have in `server_dir`'s checkout as it is now
    (`client_packs.checkout_checksum`): pinned in the catalog, or read from the
    checkout's `md5_file`. So a pack the server's makers changed, with its line
    in that file, is a different salt, and the map data is extracted again on
    the next press instead of being vouched for from the old pack.
    """
    salted: list[list[str]] = []
    for pack in packs:
        try:
            salted.append([pack.id, client_packs.checkout_checksum(pack, server_dir)])
        except client_packs.PackError as exc:
            raise InstallerError(f"{exc} The map data was not extracted.") from exc
    return json.dumps(salted, separators=(",", ":"))


def extraction_client_dir(original: Path, server_dir: Path) -> Path:
    """Where `server_dir`'s temporary extraction client is made: BESIDE the player's client.

    Same parent, so the same drive by construction, and so the copy's archives are
    clones or hard links of the player's and cost no space (`play_client.create()`
    with no full copy). Named after the server folder, with a digest of its path so
    two servers' folders of one name do not meet, and saying what it is, so a person
    who finds it beside their client knows whose it is and that it may go. Outside
    the server folder on purpose: nothing that relabels or deletes that folder
    (SELinux `chcon -R`, Uninstall's tree removal) can reach the player's files
    through the copy's links (T179 Task 3 fix round 1).
    """
    digest = hashlib.sha256(os.fspath(server_dir).encode("utf-8", "replace")).hexdigest()[:8]
    return original.parent / (
        f"{original.name} (Yu'lon map data for {server_dir.name}, temporary {digest})"
    )


def _write_record(server_dir: Path, temp: Path, original: Path) -> None:
    """`EXTRACT_CLIENT_RECORD`, written through a temporary name renamed into place."""
    record = server_dir / EXTRACT_CLIENT_RECORD
    staged = record.with_name(record.name + ".yulon-new")
    staged.write_text(
        json.dumps({"version": 1, "target": os.fspath(temp), "original": os.fspath(original)})
        + "\n",
        encoding="utf-8",
    )
    os.replace(staged, record)


def _recorded_target(server_dir: Path) -> Path | None:
    """The extraction client `EXTRACT_CLIENT_RECORD` names, or None; an unreadable one is None."""
    try:
        raw = json.loads((server_dir / EXTRACT_CLIENT_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    target = raw.get("target") if isinstance(raw, dict) else None
    return Path(target) if isinstance(target, str) and target else None


Leftover = Literal["ours", "foreign", "record", "linked"]
"""What a `LeftoverProblem` is about, because each is said and handled differently.

`ours`: this install's own copy (its marker and its place both check out) that
could not be removed yet -- never offered to the person to delete by hand, because
deleting it outside Yu'lon (Explorer) clears the read-only flag on archives the
player's own client shares; Yu'lon retries (fix round 4). `foreign`: a folder at a
path this install would use that Yu'lon did not make there -- left alone, and not
to be deleted on our word. `record`: the record names no folder this install could
have made; nothing is touched and nothing is blocked on it. `linked`: our copy holds a
link where Yu'lon sets archives aside, which Yu'lon did not make; the copy is not
removed through it, now or later.
"""

_DO_NOT_DELETE = "don't delete it yourself -- that can change your own client's files"
"""Why our own copy is never left to the person: Explorer clears a read-only flag on the
hard-linked archives to delete them, and the flag is the player's file's too."""


@dataclass(frozen=True)
class LeftoverProblem:
    """Why `remove_leftover_extraction_client()` left something where it is."""

    kind: Leftover
    path: Path
    why: str = ""
    held_open: bool = False
    """The reason is a file another program holds open (`_held_open()`): close it first."""

    def close_first(self) -> str:
        """ "Close World of Warcraft ..." when a held-open file is the reason; else nothing."""
        if not self.held_open:
            return ""
        return "Close World of Warcraft (and any program using the client's files). "

    def for_uninstall(self) -> str:
        """The warning Uninstall shows; our own copy is retried by Yu'lon, never left to the person.

        Uninstall goes on after it (the record moved to Yu'lon's own folder,
        `remember_leftover()`), so its remedy is the next start of the app, never
        another press of a button of an install that is gone.
        """
        if self.kind == "ours":
            return (
                f"A temporary copy of your game client at {self.path} could not be removed yet "
                f"({self.why}). {self.close_first()}Yu'lon will remove it safely the next time it "
                f"starts; {_DO_NOT_DELETE}."
            )
        if self.kind == "linked":
            return (
                f"The temporary copy of your game client at {self.path} holds a link Yu'lon did "
                f"not make ({self.why}), so Yu'lon will not remove the copy through it; it was "
                "left as it is."
            )
        if self.kind == "foreign":
            return (
                f"{self.path} is where this server would keep a temporary copy of your client, "
                "but Yu'lon did not make the folder that is there, so it was left alone. Do not "
                "delete it unless you know what it is."
            )
        return (
            f"This server's note of its temporary client copy names {self.path}, which is not "
            "a folder Yu'lon makes, so nothing was removed there."
        )

    def for_install(self) -> str:
        """The same fact for a press of Install, whose own next press retries the removal."""
        if self.kind != "ours":
            return self.for_uninstall()
        return (
            f"A temporary copy of your game client at {self.path} could not be removed yet "
            f"({self.why}); {_DO_NOT_DELETE}. {self.close_first()}The next press of Install, or "
            "Uninstalling this server, removes it safely."
        )


def remove_leftover_extraction_client(
    server_dir: Path,
    game: str,
    *,
    also: Path | None = None,
    flags_lost: list[play_client.LostFlag] | None = None,
) -> LeftoverProblem | None:
    """Remove the temporary extraction client this install left anywhere; None when none is left.

    Asked by the client-data stage before it makes a copy and after it is done, and
    by Uninstall (`purge.Uninstaller`, through `remove_for_uninstall()`), so a copy a
    crash left behind is found again: the one `EXTRACT_CLIENT_RECORD` names, and
    `also` (the path this press would use). Each through `_remove_target()`. The
    record goes last, once nothing it names is left.

    Returns what was left and why rather than raising: the stage refuses on `ours`
    and `foreign`, Uninstall reports each in its own words and goes on. A file of
    the player's client whose read-only flag could not be put back is appended to
    `flags_lost` (`play_client.remove_folder()`, T198), for the caller to say.
    """
    recorded = _recorded_target(server_dir)
    noted: LeftoverProblem | None = None
    targets: list[Path] = []
    for target in (recorded, also):
        if target is None or target in targets:
            continue
        if not target.name:
            # `/`, `.` or a drive root: a record no press of this app wrote.
            noted = LeftoverProblem("record", target)
            continue
        targets.append(target)
    lost = flags_lost if flags_lost is not None else []
    for target in targets:
        problem = _remove_target(target, game, server_dir, flags_lost=lost)
        if problem is not None:
            return problem
    try:
        (server_dir / EXTRACT_CLIENT_RECORD).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"could not remove {server_dir / EXTRACT_CLIENT_RECORD}: {exc}")
    return noted


def _remove_target(
    target: Path, game: str, server_dir: Path, *, flags_lost: list[play_client.LostFlag]
) -> LeftoverProblem | None:
    """Remove one extraction client and its `.yulon-partial`; None when neither is left.

    Each only when it is ours (`_is_ours()`: its marker names this game and this
    server folder AND it sits at the place `extraction_client_dir()` gives its own
    source client, so a record pointed at this server's ready-to-play client, whose
    marker is the same, cannot reach it), each with its moved-aside archives put
    back home first (`_put_left_out_back()`; a copy whose archives cannot go home is
    left whole), and each through `play_client.remove_folder()`, which never enters
    a link and puts back a read-only flag a Windows delete had to clear on a file
    the player's client shares. Never `rmtree`. The server folder need not exist:
    the check is of the marker and of paths.
    """
    for folder in (target.with_name(target.name + play_client.PARTIAL_SUFFIX), target):
        if not os.path.lexists(folder):
            continue
        marker = play_client.read_marker(folder)
        if marker is None or not _is_ours(target, marker, game, server_dir):
            return LeftoverProblem("foreign", folder)
        put_back = _put_left_out_back(folder)
        if put_back is not None:
            # Not removed: `remove_folder()` would clear the read-only flag on a name
            # it cannot map to the player's file and leave it cleared there.
            return put_back
        try:
            play_client.remove_folder(
                folder, original=marker.source_client_dir, flags_lost=flags_lost
            )
        except OSError as exc:
            return LeftoverProblem("ours", folder, str(exc), _held_open(exc))
    return None


LEFTOVERS_FILE = "leftover-extraction-clients.json"
"""In Yu'lon's config folder: our own extraction clients Uninstall could not remove yet.

A list of `{"target", "game", "server_dir"}`. Uninstall moves a server's record here
BEFORE that server's folder (and the record in it) goes; `remove_recorded_leftovers()`
retries each on the app's next start and drops what it removed (fix round 4).
"""


def _leftovers_path(config_dir: Path | None) -> Path:
    return (config_dir if config_dir is not None else platform.config_dir()) / LEFTOVERS_FILE


class LeftoversUnreadable(OSError):
    """`LEFTOVERS_FILE` is there and could not be read (or set aside): never treat it as empty.

    Reading it as an empty list and writing back would forget every copy it notes, and
    those copies are ones nobody else will remove safely (fix round 5).
    """


def _read_leftovers(path: Path) -> list[dict[str, str]]:
    """The noted copies; `[]` only when there is no file at all.

    Any other failure to read is `LeftoversUnreadable`. A file that reads but does not
    parse as the list this module writes is moved aside to
    `<name>.corrupt-<stamp>` -- kept for the person or for support, never dropped --
    and reads as empty; if it cannot be moved aside, that too is `LeftoversUnreadable`.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        raise LeftoversUnreadable(f"{path} could not be read ({exc})") from exc
    keys = ("target", "game", "server_dir")
    try:
        raw = json.loads(text)
    except ValueError:
        raw = None
    if isinstance(raw, list) and all(
        isinstance(item, dict) and all(isinstance(item.get(key), str) for key in keys)
        for item in raw
    ):
        return [{key: str(item[key]) for key in keys} for item in raw]
    aside = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
    try:
        os.replace(path, aside)
    except OSError as exc:
        raise LeftoversUnreadable(
            f"{path} is not a list Yu'lon wrote and could not be set aside ({exc})"
        ) from exc
    logger.warning(f"{path} was not a list Yu'lon wrote; kept as {aside}")
    return []


def _write_leftovers(path: Path, entries: Sequence[dict[str, str]]) -> None:
    """The list, whole or not at all: a temporary file renamed into place, removed on failure."""
    if not entries:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".yulon-new")
    try:
        staged.write_text(json.dumps(list(entries), indent=2) + "\n", encoding="utf-8")
        os.replace(staged, path)
    except OSError:
        try:
            staged.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"could not remove {staged}: {exc}")
        raise


class LeftoverNotNoted(RuntimeError):
    """Uninstall could neither remove our copy nor note it anywhere else: keep the server folder.

    The record in the server folder is then the only way back to the copy, so the
    folder must not go (fix round 5, the lead's ruling); `purge` turns this into its
    refusal after the containers and images, before the folder.
    """


def remove_for_uninstall(server_dir: Path, game: str, *, config_dir: Path | None = None) -> str:
    """Uninstall's call: remove a leftover copy, or hand it to Yu'lon's own folder; the warning.

    Our own copy that cannot be removed yet is remembered in `LEFTOVERS_FILE` --
    BEFORE the server folder, and the record in it, are removed -- for
    `remove_recorded_leftovers()` to retry. `""` when nothing is left.

    Raises:
        LeftoverNotNoted: our copy could not be removed and the note could not be
            written (or the list could not be read): the server folder must be kept.
    """
    recorded = _recorded_target(server_dir)
    lost: list[play_client.LostFlag] = []
    left = remove_leftover_extraction_client(server_dir, game, flags_lost=lost)
    told = play_client.flags_lost_warning(lost)  # T198: "" when every flag went back
    if left is None:
        return told
    if left.kind == "ours" and recorded is not None:
        path = _leftovers_path(config_dir)
        entry = {"target": os.fspath(recorded), "game": game, "server_dir": os.fspath(server_dir)}
        try:
            entries = [item for item in _read_leftovers(path) if item != entry]
            _write_leftovers(path, [*entries, entry])
        except OSError as exc:
            raise LeftoverNotNoted(
                f"The server folder {server_dir} was kept: a temporary copy of your game client "
                f"at {left.path} could not be removed yet ({left.why}), and Yu'lon could not note "
                f"it anywhere else ({exc}), so the note in that folder is the only way to remove "
                "it safely. The containers and images are already gone. Close World of Warcraft "
                f"and any other program using the client's files, then press Uninstall again; "
                f"{_DO_NOT_DELETE}. {told}".rstrip()
            ) from exc
    return f"{left.for_uninstall()} {told}".rstrip()


def recorded_leftover_targets(*, config_dir: Path | None = None) -> list[str] | None:
    """The temporary client copies still noted for removal; `None` if the list is unreadable."""
    try:
        return [entry["target"] for entry in _read_leftovers(_leftovers_path(config_dir))]
    except LeftoversUnreadable:
        return None


def remove_recorded_leftovers(
    *, config_dir: Path | None = None, flags_lost: list[play_client.LostFlag] | None = None
) -> list[str]:
    """Retry every copy Uninstall could not remove; drop the ones now gone; the warnings left.

    Called once at the app's start (`main.sweep_leftover_client_copies`). The same
    `_remove_target()` every other route uses, so the same checks hold: a folder
    that is not ours at its place is never touched, and stays listed with a warning
    rather than being forgotten. A list that cannot be read is left exactly as it
    is, with one warning; it is never rewritten from nothing. A file of the player's
    client whose read-only flag could not be put back is said in one warning and
    appended to `flags_lost`, for the start-up notice (T198).
    """
    path = _leftovers_path(config_dir)
    try:
        entries = _read_leftovers(path)
    except LeftoversUnreadable as exc:
        return [
            f"Yu'lon could not read its list of temporary client copies to remove ({exc}); it "
            "was left as it is and is tried again the next time Yu'lon starts."
        ]
    kept: list[dict[str, str]] = []
    warnings: list[str] = []
    lost = flags_lost if flags_lost is not None else []
    for entry in entries:
        target = Path(entry["target"])
        if not target.name:
            warnings.append(LeftoverProblem("record", target).for_uninstall())
            kept.append(entry)
            continue
        problem = _remove_target(target, entry["game"], Path(entry["server_dir"]), flags_lost=lost)
        if problem is None:
            continue
        kept.append(entry)
        if problem.kind in ("foreign", "linked"):
            warnings.append(problem.for_uninstall())
        else:
            logger.info(f"the temporary client copy {target} is still there: {problem.why}")
    if lost:
        warnings.append(play_client.flags_lost_warning(lost))  # T198
    try:
        _write_leftovers(path, kept)
    except OSError as exc:
        warnings.append(f"Yu'lon could not update {path} ({exc}).")
    return warnings


def _put_left_out_back(copy: Path) -> LeftoverProblem | None:
    """Rename every archive `_drop_unlisted_archives()` moved aside back to its own path.

    Before ANY removal of the copy (fix round 3, the lead's ruling).
    `play_client.remove_folder()` maps each name it removes to the player's file at
    the same relative path, and on Windows -- where a read-only file must have its
    flag cleared before it can be deleted, a flag every hard link shares -- it puts
    the flag back on that file. `.yulon-left-out/Data/patch-4.MPQ` maps to nothing in
    the player's client, so the flag would stay cleared on the player's own
    `Data/patch-4.MPQ`. Back at `Data/patch-4.MPQ` it maps to it, as every other
    archive does. A rename changes no flag.

    A name already at the path is a required pack's file, laid in after the archive
    was moved aside (a pack installs over the name the player's own patch had left
    empty); it is the copy's own, never the player's, and it goes first. A
    `.yulon-left-out` that is itself a link (or a junction) is never walked: it was
    not made by this app, and walking it would rename files somewhere else.

    Returns None when nothing is left aside, else what stopped it -- and then the
    copy must NOT be removed.
    """
    aside_root = copy / LEFT_OUT_DIR
    if not os.path.lexists(aside_root):
        return None
    # T179: `_is_link` public after T187 merges (play_client.py is T187's now).
    if play_client._is_link(aside_root):
        logger.warning(
            f"{aside_root} is a link Yu'lon did not make; the copy {copy} is not removed "
            "through it"
        )
        return LeftoverProblem("linked", copy, f"{aside_root} is a link to another folder")
    try:
        for folder, _dirs, files in os.walk(aside_root):
            for name in files:
                aside = Path(folder) / name
                home = copy / aside.relative_to(aside_root)
                if os.path.lexists(home):
                    os.unlink(home)
                home.parent.mkdir(parents=True, exist_ok=True)
                os.rename(aside, home)
        for folder, _dirs, _files in sorted(os.walk(aside_root), key=lambda x: -len(x[0])):
            os.rmdir(folder)
    except OSError as exc:
        return LeftoverProblem(
            "ours",
            copy,
            f"an archive it had set aside could not be put back first ({exc})",
            _held_open(exc),
        )
    return None


_HELD_OPEN_WINERRORS = frozenset({32, 33})
"""Windows' ERROR_SHARING_VIOLATION and ERROR_LOCK_VIOLATION: another program has it open."""


def _held_open(exc: OSError) -> bool:
    """Is `exc` another program holding the file -- and nothing else?

    Only a Windows sharing or lock violation, or EBUSY, says so (fix round 4). A
    plain EACCES or EPERM is a permission, a read-only folder or a policy as often
    as an open file, and is told as itself.
    """
    return getattr(exc, "winerror", None) in _HELD_OPEN_WINERRORS or exc.errno == errno.EBUSY


def _close_and_press(exc: OSError, press: str) -> str:
    """The remedy for a held-open file, for a button the person can press again."""
    if not _held_open(exc):
        return ""
    return (
        f" Close World of Warcraft (and any program using the client's files), then press "
        f"{press} again."
    )


def _is_ours(target: Path, marker: play_client.Marker, game: str, server_dir: Path) -> bool:
    """This game's and this server's marker, at the place its own source client puts it."""
    return (
        marker.game == game
        and marker.server_dir == server_dir
        and target == extraction_client_dir(marker.source_client_dir, server_dir)
    )


_CANNOT_SHARE_HERE = frozenset({errno.EXDEV, errno.EPERM, errno.EACCES})
"""The causes the place is the remedy for: another drive, a drive that cannot link, no rights."""


def _no_copy_beside(original: Path, target: Path, exc: play_client.PlayClientError) -> str:
    """Why the copy could not be made beside the client, and what to do about THAT cause.

    The copy's archives must be shared with the client's, never copied (about 17 GB
    for a 3.3.5a client). Three kinds of ending, each with its own remedy:

    * the place -- a drive that cannot share files (FAT32, exFAT: EPERM and the
      other link refusals `play_client._cannot_link()` knows), another drive under
      the same folder (EXDEV), or a folder the app may not write in (EACCES,
      Program Files): move the client, or give Yu'lon the rights;
    * a full drive (ENOSPC): free space, with the size the copy's own files need;
    * `play_client.plan()`'s refusal of the client folder itself (`Data` is a link,
      no game archives), which has no operating-system cause: its own sentence.

    `play_client`'s sentences speak of a ready-to-play client and of agreeing to a
    full copy, neither of which is this stage's, so where there is an `OSError` down
    the cause chain its own words are named instead.
    """
    cause: BaseException | None = exc
    while cause is not None and not isinstance(cause, OSError):
        cause = cause.__cause__
    head = (
        f"The map data is made from a temporary copy of your client beside it, in "
        f"{original.parent}, whose game files are shared with your client rather than copied"
    )
    tail = "Nothing was extracted and your client was not changed."
    if exc.flags_lost:  # T198: removing the unfinished copy cleared a flag of the player's
        tail = f"Nothing was extracted. {play_client.flags_lost_warning(exc.flags_lost)}"
    if not isinstance(cause, OSError):
        return f"{head}, and that copy could not be made: {exc} {tail}"
    if cause.errno == errno.ENOSPC:
        try:
            need = f"about {play_client.plan(original, target).own_bytes / 1024**3:.1f} GB"
        except (play_client.PlayClientError, OSError):
            need = "the size of the client's files other than its game archives"
        return (
            f"{head}, and the drive ran out of space while it was being made ({cause}). {tail} "
            f"Free {need} on that drive, then press Install again."
        )
    # T179: `_cannot_link` public after T187 merges (play_client.py is T187's now).
    if cause.errno in _CANNOT_SHARE_HERE or play_client._cannot_link(cause):
        return (
            f"{head}, and that copy could not be made ({cause}). {tail} Move your World of "
            "Warcraft folder to an ordinary folder on an NTFS or ext4 drive -- not under Program "
            "Files and not on a FAT32 or exFAT drive -- or run Yu'lon with the rights to write "
            "beside it, then press Install again."
        )
    return f"{head}, and that copy could not be made ({cause}). {tail} Fix that, then try again."


def _canonical_archive(rel: str, kept: Sequence[str]) -> str | None:
    """`rel`'s spelling in `kept`, `{locale}` as the retail `xxYY` (T227); None when not kept."""
    folded = rel.casefold()
    locale = rel.split("/", 1)[0] if "/" in rel else ""
    spelled = locale[:2].lower() + locale[2:].upper() if len(locale) == 4 else locale
    for entry in kept:
        if entry.replace("{locale}", locale).casefold() == folded:
            return entry.replace("{locale}", spelled)
    return None


def _kept_archive(rel: str, kept: Sequence[str]) -> bool:
    """Is `rel` (relative to the client's `Data/`) one of `kept`? `{locale}` is its folder."""
    folded = rel.casefold()
    locale = rel.split("/", 1)[0] if "/" in rel else ""
    return any(entry.replace("{locale}", locale).casefold() == folded for entry in kept)
