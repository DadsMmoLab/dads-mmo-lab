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

import errno
import hashlib
import json
import os
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, cast

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

LEFT_OUT_DIR = ".yulon-left-out"
"""Inside the temporary copy, beside its `Data/`: where the archives it must not hold are moved.

The extractors read `Data/` only (`-i /client`, `-d /client/Data/`), so a file here is
never read; it goes with the copy (fix round 2).
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
        left = remove_leftover_extraction_client(ctx.server_dir, self.entry.id, also=temp)
        if left is not None and left.kind == "foreign":
            raise InstallerError(
                f"{left.path} is in the way of this install's temporary copy of your client, and "
                "Yu'lon did not make it, so it was left as it was. Move it away, then press "
                "Install again. Nothing was extracted."
            )
        if left is not None and left.kind == "ours":
            raise InstallerError(
                f"An earlier temporary copy of your client, {left.path}, could not be removed "
                f"({left.why}). Remove it yourself, then press Install again. Nothing was "
                "extracted."
            )
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
                ),
                cancel=ctx.cancel,
                stage="client-data",
            )
            self._check_cancel(ctx.cancel)
        except BaseException:
            left = remove_leftover_extraction_client(ctx.server_dir, self.entry.id)
            if left is not None:
                logger.warning(left.for_uninstall())
            raise
        left = remove_leftover_extraction_client(ctx.server_dir, self.entry.id)
        if left is None:
            yield "Removed the temporary copy of your client."
        elif left.kind == "ours":
            yield (
                f"warning: the temporary copy of your client {left.path} could not be removed "
                f"({left.why}). Uninstalling this server removes it."
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
        left_out = self._drop_unlisted_archives(temp)
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
                raise InstallerError(
                    f"{path} could not be moved out of the temporary copy of your client's Data "
                    f"folder ({exc}), so the map data was not extracted: that archive would be "
                    f"read into it. Your own client was not changed.{_held_open(exc)}"
                ) from exc
            left_out.append(rel.as_posix())
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


Leftover = Literal["ours", "foreign", "record"]
"""What a `LeftoverProblem` is about, because each is said and handled differently.

`ours`: this install's own copy (its marker and its place both check out) that
could not be removed -- the person may delete it. `foreign`: a folder at a path
this install would use that Yu'lon did not make there -- it is left alone and must
not be deleted on our word. `record`: the record names no folder this install could
have made; nothing is touched and nothing is blocked on it.
"""


@dataclass(frozen=True)
class LeftoverProblem:
    """Why `remove_leftover_extraction_client()` left something where it is."""

    kind: Leftover
    path: Path
    why: str = ""

    def for_uninstall(self) -> str:
        """The warning Uninstall shows; only our own copy is offered to the person to delete."""
        if self.kind == "ours":
            return (
                f"The temporary copy of your game client this server made for its map data, "
                f"{self.path}, could not be removed ({self.why}); delete that folder yourself."
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


def remove_leftover_extraction_client(
    server_dir: Path, game: str, *, also: Path | None = None
) -> LeftoverProblem | None:
    """Remove the temporary extraction client this install left anywhere; None when none is left.

    Asked by the client-data stage before it makes a copy and after it is done, and
    by Uninstall (`purge.Uninstaller`), so a copy a crash left behind is found
    again: the one `EXTRACT_CLIENT_RECORD` names, and `also` (the path this press
    would use). For each, the unfinished `<copy>.yulon-partial` a crash INSIDE
    `play_client.create()` leaves and the copy itself -- each only when it is ours
    (`_is_ours()`: its marker names this game and this server folder AND it sits at
    the place `extraction_client_dir()` gives its own source client, so a record
    pointed at this server's ready-to-play client, whose marker is the same, cannot
    reach it), each with its moved-aside archives put back home first
    (`_put_left_out_back()`; a copy whose archives cannot go home is left, and the
    record with it), and each through `play_client.remove_folder()`, which never
    enters a link and puts back a read-only flag a Windows delete had to clear on a
    file the player's client shares. Never `rmtree`. The record goes last, once
    nothing it names is left.

    Returns what was left and why rather than raising: the stage refuses on `ours`
    and `foreign`, Uninstall reports each in its own words and goes on.
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
    for target in targets:
        for folder in (target.with_name(target.name + play_client.PARTIAL_SUFFIX), target):
            if not os.path.lexists(folder):
                continue
            marker = play_client.read_marker(folder)
            if marker is None or not _is_ours(target, marker, game, server_dir):
                return LeftoverProblem("foreign", folder)
            put_back = _put_left_out_back(folder)
            if put_back:
                # Not removed: `remove_folder()` would clear the read-only flag on a
                # name it cannot map to the player's file and leave it cleared there.
                return LeftoverProblem("ours", folder, put_back)
            try:
                play_client.remove_folder(folder, original=marker.source_client_dir)
            except OSError as exc:
                return LeftoverProblem("ours", folder, str(exc))
    try:
        (server_dir / EXTRACT_CLIENT_RECORD).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"could not remove {server_dir / EXTRACT_CLIENT_RECORD}: {exc}")
    return noted


def _put_left_out_back(copy: Path) -> str:
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
    empty); it is the copy's own, never the player's, and it goes first.

    Returns `""` when nothing is left aside, else what stopped it -- and then the
    copy must NOT be removed.
    """
    aside_root = copy / LEFT_OUT_DIR
    if not os.path.lexists(aside_root):
        return ""
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
        return (
            f"an archive it had moved aside could not be put back before removing it ({exc}); "
            f"it was left so your own client's read-only flags stay as they are.{_held_open(exc)}"
        )
    return ""


_SHARING_VIOLATION = 32
"""Windows' ERROR_SHARING_VIOLATION: another program has the file open."""


def _held_open(exc: OSError) -> str:
    """The remedy for a file another program holds open, when that is what `exc` is."""
    if getattr(exc, "winerror", None) == _SHARING_VIOLATION or exc.errno in (
        errno.EACCES,
        errno.EPERM,
        errno.EBUSY,
    ):
        return (
            " Close World of Warcraft (and any program using the client's files), then press "
            "Install again."
        )
    return ""


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


def _kept_archive(rel: str, kept: Sequence[str]) -> bool:
    """Is `rel` (relative to the client's `Data/`) one of `kept`? `{locale}` is its folder."""
    folded = rel.casefold()
    locale = rel.split("/", 1)[0] if "/" in rel else ""
    return any(entry.replace("{locale}", locale).casefold() == folded for entry in kept)
