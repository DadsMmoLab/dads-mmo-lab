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

import hashlib
import json
import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
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

EXTRACT_CLIENT_RECORD = ".yulon-extract-client.json"
"""In the server folder: where this install's temporary extraction client is, while it may exist.

Written BEFORE the copy is made and removed only after the copy is gone, so a press
that died part way -- inside `play_client.create()` included -- leaves the next
press and Uninstall the path to clean up (T179 Task 3 fix round 1).
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
            Stage("generate-compose", self.stage_generate_compose),
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
        install renames into place, a dropped archive is unlinked), the copy is
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
        behind is removed by the next press before it makes a new one, and by
        Uninstall: both find it through `EXTRACT_CLIENT_RECORD`, which is written
        before the copy exists.
        """
        tc = self._tc()
        temp = extraction_client_dir(original, ctx.server_dir)
        problem = remove_leftover_extraction_client(ctx.server_dir, self.entry.id, also=temp)
        if problem:
            raise InstallerError(f"{problem} Nothing was extracted.")
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
                ),
                cancel=ctx.cancel,
                stage="client-data",
            )
            self._check_cancel(ctx.cancel)
        except BaseException:
            problem = remove_leftover_extraction_client(ctx.server_dir, self.entry.id)
            if problem:
                logger.warning(problem)
            raise
        problem = remove_leftover_extraction_client(ctx.server_dir, self.entry.id)
        yield (
            "Removed the temporary copy of your client."
            if not problem
            else f"warning: {problem} The next press of Install, or Uninstall, removes it."
        )

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
            raise InstallerError(_no_copy_beside(original, exc)) from exc
        left_out = self._drop_unlisted_archives(original, temp)
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

    def _drop_unlisted_archives(self, original: Path, temp: Path) -> list[str]:
        """Unlink from the copy every `.MPQ` under `Data/` that `client_archives` does not keep.

        An allow-list and not a list of what to drop: a player's client may hold a
        patch from another server (`Data/patch-4.MPQ`), an HD pack this server
        offers, or one nobody has heard of, and the extractors read them all.
        Matched case-insensitively, as the client folder is usually on Windows;
        `{locale}` in an entry is the locale folder the file is in. Only the copy's
        NAME goes: the player's own file, which it shares an inode with, is
        untouched (`play_client._remove_file()` puts back a read-only flag a
        Windows delete had to clear).
        """
        kept = self._tc().extract.client_archives
        data = temp / "Data"
        left_out: list[str] = []
        for folder, _dirs, files in os.walk(data):
            for name in files:
                path = Path(folder) / name
                rel = path.relative_to(data).as_posix()
                if not name.casefold().endswith(".mpq") or _kept_archive(rel, kept):
                    continue
                try:
                    # T179: make public after T187 merges (play_client.py is T187's now).
                    play_client._remove_file(path, original / "Data" / rel, os.unlink)
                except OSError as exc:
                    raise InstallerError(
                        f"{path} could not be left out of the temporary copy of your client "
                        f"({exc}), so the map data was not extracted: that archive would be "
                        "read into it. Your own client was not changed."
                    ) from exc
                left_out.append(f"Data/{rel}")
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


def remove_leftover_extraction_client(
    server_dir: Path, game: str, *, also: Path | None = None
) -> str:
    """Remove the temporary extraction client this install left anywhere; `""` when none is left.

    Asked by the client-data stage before it makes a copy and after it is done, and
    by Uninstall (`purge.Uninstaller`), so a copy a crash left behind is found
    again: the one `EXTRACT_CLIENT_RECORD` names, and `also` (the path this press
    would use). For each, the unfinished `<copy>.yulon-partial` a crash INSIDE
    `play_client.create()` leaves and the copy itself -- each only when its marker
    names this game and this server folder, the rule every change to a marked
    folder follows, and each through `play_client`'s own removers, which never
    enter a link and put back a read-only flag a Windows delete had to clear on a
    file the player's client shares. Never `rmtree`. The record goes last, once
    nothing it names is left.

    Returns the sentence saying what was left and why, rather than raising: the
    stage refuses on it, Uninstall reports it and goes on.
    """
    targets = [path for path in (_recorded_target(server_dir), also) if path is not None]
    for target in dict.fromkeys(targets):
        partial = target.with_name(target.name + play_client.PARTIAL_SUFFIX)
        try:
            if os.path.lexists(partial) and not play_client.clean_partials(
                target, game=game, server_dir=server_dir
            ):
                return (
                    f"{partial} is in the way and is not this install's unfinished copy of a "
                    "client, so it was left as it was. Move it away, then try again."
                )
        except play_client.PlayClientError as exc:
            return str(exc)
        if not os.path.lexists(target):
            continue
        marker = play_client.read_marker(target)
        if marker is None or marker.game != game or marker.server_dir != server_dir:
            return (
                f"{target} is in the way of this install's temporary copy of your client, and "
                "it was not made by this install, so it was left as it was. Move it away, "
                "then try again."
            )
        try:
            play_client.remove_folder(target, original=marker.source_client_dir)
        except OSError as exc:
            return f"The temporary copy of your client {target} could not be removed ({exc})."
    try:
        (server_dir / EXTRACT_CLIENT_RECORD).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"could not remove {server_dir / EXTRACT_CLIENT_RECORD}: {exc}")
    return ""


def _no_copy_beside(original: Path, exc: play_client.PlayClientError) -> str:
    """Why the copy could not be made beside the client, and what the player can do about it.

    The copy's archives must be shared with the client's, never copied (about 17 GB
    for a 3.3.5a client), so the two ways this ends are a drive that cannot share
    files (FAT32, exFAT) and a folder the app may not write beside (Program Files).
    `play_client`'s own sentence speaks of a ready-to-play client and of agreeing to
    a full copy, neither of which is this stage's, so the operating system's own
    words are named instead -- the first `OSError` down the cause chain, which
    `create()` keeps under its sentence.
    """
    cause: BaseException | None = exc
    while cause is not None and not isinstance(cause, OSError):
        cause = cause.__cause__
    why = f"{cause}" if cause is not None else f"{exc}"
    return (
        f"The map data is made from a temporary copy of your client beside it, in "
        f"{original.parent}, whose game files are shared with your client rather than copied, "
        f"and that copy could not be made ({why}). Nothing was extracted and your client was "
        "not changed. Move your World of Warcraft folder to an ordinary folder on an NTFS or "
        "ext4 drive -- not under Program Files and not on a FAT32 or exFAT drive -- or run "
        "Yu'lon with the rights to write beside it, then press Install again."
    )


def _kept_archive(rel: str, kept: Sequence[str]) -> bool:
    """Is `rel` (relative to the client's `Data/`) one of `kept`? `{locale}` is its folder."""
    folded = rel.casefold()
    locale = rel.split("/", 1)[0] if "/" in rel else ""
    return any(entry.replace("{locale}", locale).casefold() == folded for entry in kept)
