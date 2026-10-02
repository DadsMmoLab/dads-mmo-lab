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
* `_expand()` -- every run of the SQL plan, with the database-name renames.

The inherited update and corrections routes see a plan with no re-runnable and no
correctable phases, because `native.update_phases()` and `correction_phases()` read
a CMaNGOS block only; T179 Task 6 gives this family its own update route.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import ClassVar, cast

from yulon import client_packs, docker, play_client
from yulon.catalog.catalog import ClientPack, CmangosData, SqlPlan, TrinityCoreData
from yulon.catalog.families import conf, extract, sqlplan
from yulon.catalog.families.cmangos import CATALOG_ERROR_TAIL, ETC_DIR, CmangosInstaller
from yulon.catalog.installer import InstallerError
from yulon.catalog.native import (
    BUILD_CANCEL_NOTE,
    IMPORT_STAGE_CANCEL_NOTE,
    Stage,
    StageContext,
)
from yulon.log import get_logger

logger = get_logger(__name__)

EXTRACT_CLIENT_DIR = ".yulon-extract-client"
"""The temporary extraction client, inside the server folder (T179 spec §1 step 6).

Made by `play_client.create()` -- the ready-to-play client's own engine, so its
game archives are clones or hard links of the player's and cost no space on the
same drive -- with the server's REQUIRED packs laid in, read by the extractors,
and deleted again whether they succeeded or not.
"""

CLIENT_DATA_CANCEL_NOTE = (
    f"{extract.EXTRACT_CANCEL_NOTE} The temporary copy of your client is removed either way."
)
"""What a Stop costs in `client-data`: the extraction's per-tool record, and the copy gone."""


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

    def stages(self) -> tuple[Stage, ...]:
        """The family's stage tuple, in `STAGE_NAMES` order (T179 spec §1, steps 1-9)."""
        return (
            Stage("clone-sources", self._clone_sources),
            Stage("db-password", self._db_password, recorded=False),
            Stage("write-dockerfile", self._write_dockerfile),
            Stage("generate-compose", self._generate_compose),
            Stage("build", self.stage_build, cancel_note=BUILD_CANCEL_NOTE),
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
                f"{self.entry.name} says its family is trinitycore but carries no `trinitycore` "
                f"block. {CATALOG_ERROR_TAIL}"
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

    # -- generate-compose ------------------------------------------------------

    def _generate_compose(self, ctx: StageContext) -> Iterator[str]:
        """The spine's compose stage, with a copy an interrupted press left removed first.

        On an SELinux-enforcing machine this stage relabels the whole server folder
        (`chcon -R`, the `relabel` seam), and a temporary extraction client left in it
        by a press that died holds hard links to the player's own game archives: a
        label is a property of the file, not of the name, so relabelling the copy
        would relabel the player's client. A copy is only ever left behind by a crash
        (`client-data` removes it on every way out), and this is the first stage of a
        resume that writes into the folder as a whole.
        """
        self._clear_leftover_extraction_client(ctx, ctx.server_dir / EXTRACT_CLIENT_DIR)
        yield from self.stage_generate_compose(ctx)

    # -- client-data ---------------------------------------------------------

    def _client_data(self, ctx: StageContext) -> Iterator[str]:
        """Map data from a temporary client with the server's required packs, then its own DBCs.

        In order (T179 spec §1 step 6, facts §3):

        1. **Is the extraction already vouched for?** The evidence file names the
           PLAYER'S client and the required packs' checksums (`extract.run_plan()`'s
           `evidence_client_dir`/`evidence_salt`), so a resume answers this before
           any copy is made, and a changed pack or another client extracts again.
        2. **Otherwise, a temporary extraction client** in `EXTRACT_CLIENT_DIR`:
           `play_client.create()` from the player's client, every file an OPTIONAL
           pack would install taken out of the copy, every REQUIRED pack laid in
           (`client_packs`), the extractors run against it in the server image, and
           the copy deleted whether they succeeded or not. Required packs only,
           whatever the player chose for their ready-to-play client: the patched
           extractors read every lettered patch archive they find (map_extractor
           System.cpp:1152-1218), so an HD pack in the copy would be extracted into
           maps the server does not expect (Review Focus 1).
        3. **The tree's own DBCs** over `data/dbc` (`dbc_overlay_from`): "Use these
           DBCs, not the ones the extractor writes" (README.md:174-180).
        4. **The start check**: maps and vmaps for every `required_maps` id, or a
           refusal naming this step, before a server is started that would stop
           with "Unable to load critical files" (World.cpp:1811-1823).

        The player's own client is never written: the copy shares its archives
        by clone or hard link, every change to the copy replaces a NAME (a pack's
        install renames into place, a dropped file is unlinked), and the copy is
        mounted read-only into the extractors.
        """
        tc = self._tc()
        original = ctx.client_dir
        if original is None:
            raise InstallerError(
                f"{self.entry.name} makes its map data from your game client, and no client "
                "folder was given. Pick the client folder and try again."
            )
        packs = self._required_packs()
        salt = _packs_salt(packs)
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
        packs = tuple(pack for pack in self.entry.client.packs if not pack.optional)
        downloads = [pack.label for pack in packs if pack.source.kind != "checkout"]
        if downloads:
            raise InstallerError(
                f"{self.entry.name}'s catalog makes {', '.join(downloads)} a required client "
                "pack from a download, and the map data is made only from packs in the server's "
                f"own checkout. Nothing was extracted. {CATALOG_ERROR_TAIL}"
            )
        return packs

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
        behind is removed by the next press before it makes a new one.
        """
        tc = self._tc()
        temp = ctx.server_dir / EXTRACT_CLIENT_DIR
        self._clear_leftover_extraction_client(ctx, temp)
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
                ),
                cancel=ctx.cancel,
                stage="client-data",
            )
            self._check_cancel(ctx.cancel)
        except BaseException:
            problem = _remove_extraction_client(temp, original)
            if problem:
                logger.warning(problem)
            raise
        problem = _remove_extraction_client(temp, original)
        yield (
            "Removed the temporary copy of your client."
            if not problem
            else f"warning: {problem} The next press of Install removes it."
        )

    def _clear_leftover_extraction_client(self, ctx: StageContext, temp: Path) -> None:
        """Remove a copy an earlier press left behind if it is this install's; refuse anything else.

        Its marker must name this game and this server folder -- the rule
        `play_client` holds every change to a marked folder to. A folder there
        without one was not made by this app and is never deleted.
        """
        if not os.path.lexists(temp):
            return
        marker = play_client.read_marker(temp)
        if marker is None or marker.game != self.entry.id or marker.server_dir != ctx.server_dir:
            raise InstallerError(
                f"{temp} is in the way of the temporary client copy this step makes, and it was "
                "not made by this install, so it was left as it was. Move it out of the server "
                "folder, then press Install again."
            )
        problem = _remove_extraction_client(temp, marker.source_client_dir)
        if problem:
            raise InstallerError(f"{problem} Remove it yourself, then press Install again.")

    def _make_extraction_client(
        self, ctx: StageContext, original: Path, temp: Path, packs: Sequence[ClientPack]
    ) -> Iterator[str]:
        """`play_client.create()`, the optional packs' files out, the required packs in."""
        yield (
            f"Making a temporary copy of your client {original} in {temp}, with this server's "
            "required packs laid in. Your own client is not changed."
        )
        try:
            play_client.create(
                original,
                temp,
                game=self.entry.id,
                server_dir=ctx.server_dir,
                # A copy made only to be read and deleted: when the server folder is
                # on another drive than the client, its archives are copied rather
                # than the extraction refused (`create()` logs which it did).
                allow_full_copy=True,
            )
        except play_client.PlayClientError as exc:
            raise InstallerError(
                f"The temporary copy of your client for the map data could not be made: {exc}"
            ) from exc
        left_out = self._drop_optional_pack_files(original, temp)
        if left_out:
            yield (
                "Left out of the copy, because this server's map data is made without them: "
                f"{', '.join(left_out)}."
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

    def _drop_optional_pack_files(self, original: Path, temp: Path) -> list[str]:
        """Unlink from the copy every file an optional pack installs or removes when off.

        Named by the packs' own install rules (`to`), their `remove_when_off`, and
        -- when the player's client is itself a ready-to-play client -- the files
        its pack record says an optional pack installed (a `*` rule's files are
        known only there). Matched case-insensitively, as the client folder is
        usually on Windows. Only the copy's NAME goes: the player's own file, which
        it may share an inode with, is untouched (`play_client._remove_file()`
        puts back a read-only flag a Windows delete had to clear).
        """
        optional = [pack for pack in self.entry.client.packs if pack.optional]
        doomed = {
            PurePosixPath(rel).as_posix().casefold()
            for pack in optional
            for rel in (
                *(rule.to for rule in pack.install if rule.to is not None),
                *pack.remove_when_off,
            )
        }
        recorded = client_packs.read_record(temp).packs
        for pack in optional:
            doomed.update(rel.casefold() for rel in recorded.get(pack.id, {}).get("files", {}))
        if not doomed:
            return []
        left_out: list[str] = []
        for folder, _dirs, files in os.walk(temp):
            for name in files:
                path = Path(folder) / name
                rel = path.relative_to(temp).as_posix()
                if rel.casefold() not in doomed:
                    continue
                try:
                    play_client._remove_file(path, original / rel, os.unlink)
                except OSError as exc:
                    raise InstallerError(
                        f"{path} could not be left out of the temporary copy of your client "
                        f"({exc}), so the map data was not extracted: an optional pack's file "
                        "there would be read into it. Your own client was not changed."
                    ) from exc
                left_out.append(rel)
        return sorted(left_out)

    def _refuse_missing_map_data(self, data_dir: Path, original: Path) -> None:
        """Refuse before `up` when the start check would fail, and make the next press extract.

        The evidence file is removed with the refusal, so pressing Install again
        runs this step's extraction again rather than finding it vouched for.
        """
        tc = self._tc()
        missing = extract.missing_map_data(data_dir, tc.required_maps)
        if not missing:
            return
        evidence = data_dir / extract.EVIDENCE_FILE
        try:
            evidence.unlink(missing_ok=True)
            cleared = "Its record was cleared, so pressing Install again runs client-data again."
        except OSError as exc:
            cleared = (
                f"Its record {evidence} could not be cleared ({exc}); delete it, then press "
                "Install again to run client-data again."
            )
        raise InstallerError(
            f"The map data the world server needs at start is not all there "
            f"({'; '.join(missing)}), and without it the server stops with 'Unable to load "
            f"critical files'. The client-data step made it from {original}: check that it is "
            f"a complete {self.entry.client.version} client. Nothing was started. {cleared}"
        )

    # -- conf ------------------------------------------------------------------

    def _conf(self, ctx: StageContext) -> Iterator[str]:
        """The image's `.dist` files copied once, then the table's keys patched in place.

        `conf.materialise()` and `conf.apply_table()`, as the CMaNGOS conf stage
        runs them, over this block's table: `Updates.EnableDatabases = 0` (the model
        refuses a world conf without it), `DataDir`, `LogsDir`, the database
        strings, SOAP, the realm id, and `playerbots.conf` -- written into the SAME
        folder as `worldserver.conf`, the only place the world server reads it
        (worldserver/Main.cpp:242-250, facts §4).

        Without the two CMaNGOS carry-overs: the random-bot count read back from a
        previous press (`bot_count`) is Task 5's for this family, and the bot
        dashboard is the Tortoise module's (`families/decisions.py`).
        """
        tc = self._tc()
        etc_dir = ctx.server_dir / ETC_DIR
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
        try:
            changed = conf.apply_table(tc.conf, etc_dir, self._secret_tokens(ctx))
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


def _packs_salt(packs: Sequence[ClientPack]) -> str:
    """What the extraction was made from beyond the client: each required pack and its checksum.

    A checkout pack always carries one (`ClientPack`'s rule), so a pack the
    server's makers changed is a different salt, and the map data is extracted
    again on the next press.
    """
    return json.dumps([[pack.id, pack.sha256 or pack.md5] for pack in packs], separators=(",", ":"))


def _remove_extraction_client(temp: Path, original: Path) -> str:
    """Delete the temporary client; `""` when it is gone, else the sentence saying why not.

    `play_client.remove_folder()`: links are never entered, and a read-only flag
    a Windows delete had to clear on a shared archive is put back on the
    player's own file.
    """
    if not os.path.lexists(temp):
        return ""
    try:
        play_client.remove_folder(temp, original=original)
    except OSError as exc:
        return f"The temporary copy of your client {temp} could not be removed ({exc})."
    return ""
