"""The CMaNGOS-lineage install engine: one class for every entry whose `family` is `cmangos`.

Stage ORDER is Python (this tuple); stage PARAMETERS are the entry's typed
`install.native.cmangos` block. The class therefore names no game — held since
G.7 by `test_family_modules_contain_no_game_literal` in
`tests/test_catalog_invariants.py`, which asks it of every family module over
the string constants that RUN, against the values `catalog.json` gives each
game; before that it was a reading done by hand (review, 2026-09-01) — and
the third CMaNGOS game costs a catalog entry plus templates, no Python
(`pyplan/phase7-decisions.md`, "Family engines — chosen").

Every body here is a thin wrapper: it pulls one typed block off the entry,
resolves the few values only the engine knows (the built image reference, the
uid:gid policy, the right token mapping of the two, the secret) and hands them
to a stage-kind module (`dockerfile`, `extract`, `conf`, `sqlplan`). Two
mappings and not one since 7.3: `_public_tokens()` for whatever writes into the
build context, `_secret_tokens()` for the consumers that must have the
password. Contract A6 said one; mutation M15 said why that was wrong. The spine
(`StagedInstaller`) owns clone, compose, build, start-db, up and ready, it
owns the import branch table, and it yields every stage's cancel note — this
family only supplies the `MarkerGate` and runs the SQL plan when the table
says run.

`db-password` is the one stage with a body of its own, because its evidence is
a file and not the state file: a state file must never be the thing that
claims a secret exists.

`STAGE_NAMES` and `stages()` name the same stages in the same order — twelve
as of K.7, which bound `import`, the last one then outstanding; thirteen since
2026-09-05, when `patch-sources` went in after `clone-sources` to carry the
`vmap_extractor` fix `pyplan/upstream-cmangos-doodad-drop.md` §10 argued for
(the stage kind is `families/patch.py`; the data is `CmangosData.patches`).
They were allowed to
disagree while the family was being built, because nothing in the app reads
`STAGE_NAMES` — `stage_names()`, derived from `stages()`, is what the spine
validates a resume against — and because this class was not in `FAMILIES` until
K.8 put it there. Both of those allowances are spent: K.8 registered `cmangos`,
and `test_stage_names_is_the_pinned_tuple_now_that_every_stage_is_bound` spells
the literal `stage_names() == STAGE_NAMES`.

The agreement is held by tests in `tests/test_families_cmangos.py`, and the two
DIRECTIONS are held by different ones. Measured 2026-09-02 at `f6ed1b9a`, whole
suite each time against a 1974-passed/3-skipped baseline, by deleting `import`
from each side in turn. Take the `Stage` out of `stages()` and FIVE fail:
`test_the_bound_stages_run_in_order_and_record_the_recorded_ones`, which
restates every `--- <name>` line one whole install said,
`test_the_import_cancel_note_is_said_at_the_import_and_nowhere_else`,
`test_import_is_recorded_and_sits_between_start_db_and_up`, and the two
equality tests below. Take the name out of `STAGE_NAMES` and FOUR fail:
`test_family_and_stage_names_are_the_contract_tuple`, which restates the tuple,
`test_stages_are_unique_and_a_subset_of_the_pinned_names_in_order`, and those
same two — `test_stage_names_is_the_pinned_tuple_now_that_every_stage_is_bound`
and `test_every_cmangos_entry_dispatches_to_an_engine_that_runs_this_familys_stages`,
which read `stage_names()` back off the object dispatch returns and so catch
either side. The subset check is the weak one: it stayed green over a `stages()`
short a stage, seeing the tuple's direction only, which is why the equality was
added rather than relied on.

This paragraph read "three", "two", "is not in `FAMILIES` until K.8" and "what
no test spells" for as long as K.8 had been merged. Counts and registrations
rot; the mutation run above is how they were re-checked rather than re-copied.
"""

from __future__ import annotations

import os
import queue
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import ClassVar, cast

from yulon import docker, platform
from yulon.catalog import composegen
from yulon.catalog.catalog import CmangosData, NativeInstall, SourcePatch
from yulon.catalog.families import conf, dockerfile, extract, patch, sqlplan
from yulon.catalog.installer import InstallerError
from yulon.catalog.native import (
    BUILD_CANCEL_NOTE,
    IMPORT_CANCEL_NOTE,
    INSTALL_REALM_HOST,
    ImportGate,
    Secrets,
    Stage,
    StageContext,
    StagedInstaller,
    secret_token_name,
    stop_abandoned_worker,
)
from yulon.log import get_logger

logger = get_logger(__name__)

CATALOG_ERROR_TAIL = "That is a catalog error in the app, not something to fix on this machine."
"""The one sentence every refusal here about a malformed catalog entry ends in.

Three refusals said it in two spellings until 2026-09-01 — two "a catalog
error in the app", one "a bug in the app's catalog" — and a second wording for
one thing drifts further from the first every time either is edited.
`test_the_family_s_catalog_refusals_end_in_one_tail_and_not_two` holds them
together, and derives WHICH refusals those are from this module's AST rather
than restating a count: that test opened "Three refusals" on the day a fourth
user was added in the same diff.

Checked 2026-09-01: `sqlplan.py` carries `_CATALOG_ERROR`, the same sentence
without "in the app", private to that module and ending six refusals there.
Whether the two become one string is undecided, and is not decided here.

Not every refusal in this module belongs to it: see `DECLARATION_ERROR_TAIL`,
which is for the ones no catalog file can cause.
"""

DECLARATION_ERROR_TAIL = (
    "That is a bug in the app's own declarations, not something to fix on this machine "
    "and not anything a catalog file can cause."
)
"""For a refusal a catalog entry cannot trigger, however malformed.

`CATALOG_ERROR_TAIL` sends a reader to the catalog, and that is right for the
refusals it ends: each of those fires on a shape a `catalog.json` entry can
actually take. `_secret_tokens()`'s collision refusal is not one of them and
was ending in it until 2026-09-02. It fires only when a field of
`native.Secrets` — a Python dataclass field, in `yulon/catalog/native.py` — is
named like a key the public half already spells, and the public half's keys are
literals in this file plus `composegen.entry_tokens()`'s. No catalog data
reaches either set, so a user sent to the catalog by that sentence would have
found nothing to fix there.

Deliberately a second CONSTANT rather than a second wording spelled inline: one
sentence written out twice is exactly the drift `CATALOG_ERROR_TAIL` exists to
have stopped.
"""

REBUILD_ACTION = "Stop and remove containers…"
"""The Server tab's own label for the action `_patch_sources()`'s refusal names.

Spelled here rather than imported from `yulon.ui.controller_view`: a family
engine must not depend on a widget module, and this file is imported by the
headless install path. So it is a COPY, and a copy of a label is a copy that
can go stale --
`test_the_refusal_names_an_action_the_server_tab_actually_offers` asserts the
two are the same string, which is the only thing that makes naming a button in
a sentence safe.
"""

DATA_DIR = "data"
"""Where extraction lands, relative to the server dir; the template binds it to /opt/*/data."""

ETC_DIR = "etc"
"""Where the patched `.conf` files live, relative to the server dir."""

DB_DATA_VOLUME = "db-data"
"""The named volume key in `catalog/installers/shared/cmangos/base.yml.tmpl`.

Compose names the volume `<project>_<key>`; `db-password` asks the daemon
about exactly that name. `test_families_cmangos.py` reads all three constants
back out of that template so the two cannot drift apart silently.
"""

SECRET_FILE_MODE = 0o600
"""The mode `db-password` creates `.db_password` with: the password itself is in it.

**Deliberately not `conf.CONF_MODE`, which is the same number for a different
reason.** That constant's docstring says it is only correct while the images run
as root, because the conf files it governs are written by the host user and read
by the emulator INSIDE the container; the day a `USER` lands in a Dockerfile that
number has to change or the server cannot read its own config. Nothing reads
`.db_password` from a container — it is a host-only file that the family reads
back in `resolve_secrets()` and spells into `.env` — so a widening made for the
container's sake must not reach this file. Sharing one constant would make that
widening silent and invisible; two constants naming each other is the cheaper
half of that trade.

What the number actually buys is not the same on every platform this app ships
to — see `_write_secret`, which measured it.
"""


def secret_token_map(secrets: Secrets) -> dict[str, str]:
    """Every field of the `Secrets` handed in, as `{FIELD_NAME_UPPERCASED: value}`.

    DERIVED from the secret type's own fields, not listed here. A secret added
    to `native.Secrets` is therefore carried by `_secret_tokens()` and absent
    from `_public_tokens()` by construction, with nobody to remember either
    half. Listing the names instead is what K.4 had, and what mutation M15
    defeated on 2026-09-01: a second secret key added to the single `_tokens()`
    mapping rendered `ENV ROOT_PASSWORD=<the password>` into a Dockerfile while
    all 1872 tests passed — the whole suite as it stood that day, recorded at
    `9e198c05`; it is 1974 at `f6ed1b9a` — because the protection covered the
    NAME `DB_PASSWORD` and not the property.

    `fields()` is asked of the INSTANCE, so a subclass carrying a second secret
    answers with both fields — which is how
    `test_the_secret_mapping_carries_a_second_secret_nobody_listed` adds one
    without editing `native.py`, and why the derivation is testable at all.

    The spelling `db_password` -> `DB_PASSWORD` is the token grammar every
    template in the app already uses, and
    `test_the_secret_mapping_spells_each_field_the_way_the_templates_do` pins
    it against the shipped conf tables, which name `{{DB_PASSWORD}}` literally.
    It is spent from `native.secret_token_name()` rather than written out here,
    because `dockerfile.secret_tokens()` derives the names it REFUSES from the
    same declaration and the two have to agree exactly. Written out in both
    files they could drift, and the drift is silent in the dangerous direction:
    this mapping would carry the value under a new spelling while that refusal
    still looked for the old one.
    """
    return {secret_token_name(f.name): getattr(secrets, f.name) for f in fields(secrets)}


class CmangosInstaller(StagedInstaller):
    """Install a CMaNGOS server: clone, Dockerfile, build, extract, conf, SQL plan, start."""

    family = "cmangos"
    STAGE_NAMES: ClassVar[tuple[str, ...]] = (
        "clone-sources",
        "patch-sources",
        "db-password",
        "write-dockerfile",
        "generate-compose",
        "build",
        "extract",
        "mmaps",
        "conf",
        "start-db",
        "import",
        "up",
        "ready",
    )

    def stages(self) -> tuple[Stage, ...]:
        """The family's stage tuple, in `STAGE_NAMES` order."""
        return (
            Stage("clone-sources", self._clone_sources),
            Stage("patch-sources", self._patch_sources),
            Stage("db-password", self._db_password, recorded=False),
            Stage("write-dockerfile", self._write_dockerfile),
            Stage("generate-compose", self.stage_generate_compose),
            Stage("build", self.stage_build, cancel_note=BUILD_CANCEL_NOTE),
            Stage("extract", self._extract, cancel_note=extract.EXTRACT_CANCEL_NOTE),
            Stage("mmaps", self._mmaps, cancel_note=extract.MMAPS_CANCEL_NOTE),
            Stage("conf", self._conf),
            Stage("start-db", self.stage_start_db, recorded=False),
            Stage("import", self._import, cancel_note=IMPORT_CANCEL_NOTE),
            Stage("up", self.stage_up, recorded=False),
            Stage("ready", self.stage_ready, recorded=False),
        )

    # -- stage bodies ----------------------------------------------------

    def _clone_sources(self, ctx: StageContext) -> Iterator[str]:
        """Every source to its own `dest` — one stage, because no CMaNGOS state file predates it.

        `recorded_as` is this family's own name for the body, and the spine
        cannot know it: without it a resume consults no record and fetches and
        resets every checkout on each press, which is the incident
        `stage_clone_sources()`'s docstring is about.
        """
        yield from self.stage_clone_sources(
            ctx, self.entry.emulator.sources, recorded_as="clone-sources"
        )

    def _patch_sources(self, ctx: StageContext) -> Iterator[str]:
        """Apply every `SourcePatch` the entry carries to the checkout it names; say what happened.

        The stage `pyplan/upstream-cmangos-doodad-drop.md` §10 asked for, in
        the shape it asked for: a new stage after `clone-sources` rather than a
        step inside its loop (that loop's one job is "clone what the manifest
        names"), a patch file committed as data beside the family's templates,
        a record in the state file, and a TOLERANT apply — `patch.apply()`
        skips a hunk whose fix is already present and refuses, naming the file
        and the line, when upstream has moved under it. The pins on
        `Source.rev` came first (`test_catalog.py`, `GATE_PINS`), because a
        patch against a moving tip breaks the day upstream touches those lines,
        including the day they fix the defect themselves.

        **The record is not what skips this stage.** A resume carrying
        `patch-sources` in `completed` reaches this body exactly like a first
        run — the same rule
        `_write_dockerfile` and `_conf` are written against, and here for a
        sharper reason: `clone-sources` re-clones a checkout that was DELETED
        on the strength of its own disk evidence (`already_cloned()`'s
        `remote is None` case) while this stage's record survives, so a body
        that trusted the record would leave a fresh clone unpatched under a
        state file saying otherwise. The file is the evidence; a second press
        reads "already carries" off the bytes, and costs one read per file.

        `ctx.state` IS read here, but never to skip: the `build` record is half
        of `_refuse_to_patch_what_will_not_be_rebuilt()`'s question, and that
        method stops the press rather than passing over it. Until 2026-09-05
        this paragraph said "`ctx.state` is not read here at all", which was
        true and was also the reason the stage could patch a source tree whose
        build the same press was about to skip.

        What the record buys instead is the `Already finished:` line and the
        progress count, and the refusal's position: a refusal here raises
        before `db-password`, so no secret is minted for an install that is
        about to stop, and before `write-dockerfile`, so no build context
        exists for a tree that is not the one the patch was measured against.

        Three refusals, three shapes, and none of them shares a tail with
        another. A patch file the catalog names and the tree does not ship is a
        catalog error (`CATALOG_ERROR_TAIL`, raised in `_patch_text()`); a
        patch that does not apply is `patch.PatchError`'s own sentence, which
        already names the file and the line and says nothing was changed — a
        class name in front of it would be noise, as `_write_dockerfile` says
        of `DockerfileError`; and a checkout whose build this press would skip
        is `_refuse_to_patch_what_will_not_be_rebuilt()`, whose sentence ends
        in the two things to do about it. None is a catalog error and none is
        an app bug, so none takes a shared tail.
        """
        data = self._data()
        if not data.patches:
            yield "This server carries no source patches."
            return
        loaded = [(spec, self._patch_text(spec)) for spec in data.patches]
        self._refuse_to_patch_what_will_not_be_rebuilt(ctx, loaded)
        for spec, text in loaded:
            root = ctx.server_dir / spec.source
            yield f"Applying {spec.file} inside {spec.source}: {spec.reason}"
            results = self._resolve(spec, text, root)
            for result in results:
                if result.applied and result.present:
                    yield (
                        f"Patched {result.path} ({result.applied} of "
                        f"{result.applied + result.present} hunks; the rest were already there)."
                    )
                elif result.applied:
                    yield f"Patched {result.path}."
                else:
                    yield f"{result.path} already carries the fix in {spec.file}; leaving it."
        yield "Source patches are in place."

    def _patch_text(self, spec: SourcePatch) -> str:
        """The patch file's bytes, or the catalog refusal for one this build does not ship."""
        path = self.installers_root / spec.file
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InstallerError(
                f"{self.entry.name}'s catalog names a source patch {spec.file} that this "
                f"build does not ship ({exc}). {CATALOG_ERROR_TAIL}"
            ) from exc

    def _resolve(
        self, spec: SourcePatch, text: str, root: Path, *, dry_run: bool = False
    ) -> tuple[patch.FileResult, ...]:
        """`patch.apply()` with this module's refusal wrapping, dry or wet.

        `patch.PatchError`'s own sentence already names the file and the line
        and says nothing was changed, so a class name in front of it would be
        noise, as `_write_dockerfile` says of `DockerfileError`.
        """
        try:
            return patch.apply(text, root, name=spec.file, dry_run=dry_run)
        except patch.PatchError as exc:
            raise InstallerError(str(exc)) from exc

    def _refuse_to_patch_what_will_not_be_rebuilt(
        self, ctx: StageContext, loaded: Sequence[tuple[SourcePatch, str]]
    ) -> None:
        """Refuse to edit a source tree whose compiled form this press is going to skip.

        **The case.** Every CMaNGOS install made before this stage existed
        records twelve stages and not `patch-sources`. Read on m910q
        2026-09-05: `~/tbc-7.4c` and `~/vanilla-75b` both hold exactly
        `clone-sources, write-dockerfile, generate-compose, build, extract,
        mmaps, conf, import`, and both have a `data/.yulon-extract.json`
        vouching for all four extraction tools. So on the first press after
        this stage ships, `patch-sources` is the ONE stage such a folder has
        never run. Driven the same day against a state file in that shape: the
        press said `Patched contrib/vmap_extractor/vmapextract/...` four times,
        then `The server is already built; skipping the compile.`, and finished
        with "is installed and running". The source tree carried the fix; the
        image compiled from it did not; the vmaps were still short their 14.8%.
        Nothing in those forty lines said so.

        **Why a refusal and not an invalidation.** The other repair -- drop
        `build` from the record and delete the extraction evidence, so the
        press rebuilds and re-extracts -- makes the press correct and makes it
        catastrophic. It turns a resume into a multi-hour recompile nobody
        asked for, and the re-extraction rewrites `data/` underneath a server
        that may be running out of it. This lane already made that trade once,
        in the other direction: `DoodadCheck` warns and never refuses, because
        "a refusal would take a working server away over a defect only a
        rebuild mends". Refusing HERE takes nothing away. It stops a press that
        was going to achieve nothing, changes not one byte on disk, and leaves
        Start, Stop, Repair and the running server exactly as they were --
        those are separate actions and none of them comes through this method.

        **What it costs, stated plainly.** The install button stops working for
        that folder until the user acts on the sentence. That is the price, and
        it is the right one: the press it refuses is a press whose only effect
        would have been to write a lie into the source tree.

        **Narrow on purpose, in three ways.** It fires only when the patch
        would actually change a file -- an already-patched checkout, which is
        every ordinary second press of an install made by THIS build, resolves
        to "already carries" and never reaches the refusal. It fires only when
        `build_would_be_skipped()` -- the spine's own name for `stage_build`'s
        skip rule, record AND images -- says the compile is not going to
        happen; a recorded build whose images the user has deleted, and a
        daemon that will not answer, both rebuild, and a rebuild picks the
        patch up. And it runs before anything is written: the dry resolution
        touches no file, and this method is called before the first `apply()`.

        **The remedy, and the one it replaced.** Until the review of
        2026-09-05 this sentence ended "use “Stop and remove containers…” on
        the Server tab, delete {server_dir}, and install it again", and that
        sequence is a loop that ends in lost characters. Driven twice around
        its own instructions on m910q that day (`CmangosInstaller.run()`, a
        state file in the `~/tbc-7.4c` shape): press 1 refused; the remedy
        removed the containers, which by `docker.remove_staged()`'s design
        passes no `-v` and keeps the database volume, and deleted the folder --
        taking `.db_password`, which lives inside it, with it; `install_id()`
        is a digest of the ABSOLUTE path, so the reinstall came back to the
        same volume name (ffb3ef7e before and after the `rmtree`) and press 2
        stopped at `_db_password` with "that database cannot be opened again:
        `docker volume rm ...` deletes it, and every character in it". That
        method's own docstring had already refused to send anyone down this
        road: "nothing in this app deletes a named volume, and sending the user
        there would send them round a loop that ends at this same message".

        What is named instead takes away the two pieces of evidence that make
        this press skip, and the one folder the re-extraction cannot start
        into. The IMAGE (`built_image_refs()`, the same strings
        `built_images()` asked the daemon about, so the command cannot name a
        tag this install does not have) turns `build_would_be_skipped()` False,
        so the compile runs and picks the patch up. `data/.yulon-extract.json`
        is what makes the fix visible, because `extract.run_plan()` skips a
        tool that has a record and `run_mmaps()` reads its record out of that
        same file, so deleting it re-runs the extraction and the movement maps
        built from it. The install folder, `.db_password` and the database
        volume are all left alone, and `stage_import()` leaves an
        already-imported database alone, so the characters survive.

        **The third thing, and the review that put it here.** Until the review
        of 2026-09-05 this docstring claimed the deletion re-ran the extraction
        with "`empty_out_dirs()` clears each `produces` folder first". That was
        false, and reading it was cheaper than measuring it: `empty_out_dirs()`
        has ONE call site in the package, in `run_plan()`'s retry pass, and its
        own docstring says "The retry path only, never a first run". The
        ordinary loop only ever called `make_out_dirs()`, which creates.
        Measured on m910q 2026-09-05, driving the real `run_plan()` over a
        `data/` in the `~/tbc-7.4c` shape with the evidence file deleted: `ad`
        finished, `vmap_extractor` exited 1 saying "Your output directory seems
        to be polluted, please use an empty directory!", the re-created
        evidence recorded `dbc and maps` alone, and press 3 died identically --
        so following the sentence bought a recompile and a wedged install. The
        remedy now names `data/Buildings` alongside the evidence file, from
        `extract.clear_before_rerun()` rather than as a literal, so a `data/`
        with nothing blocking is not told to delete a folder that is not there;
        `run_plan()` refuses with the folder named if anyone arrives in that
        state by another road. The other three folders are NOT named, and that
        is a reading of the pinned sources rather than a hope: none of `ad`,
        `vmap_assembler` and `MoveMapGen` refuses a non-empty output folder.
        `ad` and `vmap_assembler` also overwrite what they write, through
        `fopen(.., "wb")` (`ad`'s one output-path `FileExists()` is on
        `Cameras/`, and it skips past a camera file already there);
        `MoveMapGen` does NOT -- `MapBuilder::shouldSkipTile`
        keeps an existing `.mmtile` whose magic and versions match and rebuilds
        the rest -- and it does not need to, because the mmaps stage wipes
        `mmaps/` itself when no finished record vouches for it. "No refusal"
        and "overwrites" are separate claims and only the first is what this
        paragraph rests on; until the sixth pass this sentence asserted the
        first of the two of all three (`extract.DIRTY_MARKERS` records where
        each was read).

        The price is stated rather than hidden, and it is smaller than the
        price the old sentence hid: hours of compiling and extracting, against
        a reinstall that loses the world.

        **What it does not cover, said rather than implied.** A press that
        DOES rebuild -- because the user removed the image and not the
        evidence, or because Docker would not answer -- still skips the
        extraction. That case is audible rather than silent: `_extract()` runs
        `DoodadCheck` on every press, and stale `Buildings/` is exactly what
        its warning is for.
        """
        if not self.build_would_be_skipped(ctx):
            return
        stale = [
            spec
            for spec, text in loaded
            if any(
                result.applied
                for result in self._resolve(spec, text, ctx.server_dir / spec.source, dry_run=True)
            )
        ]
        if not stale:
            return
        named = ", ".join(spec.file for spec in stale)
        images = " ".join(self.built_image_refs(ctx))
        data_dir = ctx.server_dir / DATA_DIR
        evidence = data_dir / extract.EVIDENCE_FILE
        # The plain path, not `_data_dir()`: that one refuses a `data/` that
        # resolves elsewhere, and a refusal about patches is the wrong place to
        # raise a different one.
        blocking = extract.clear_before_rerun(self._data().extract, data_dir)
        doomed = " and ".join(str(path) for path in (evidence, *blocking))
        why = (
            (
                f" {' and '.join(str(path) for path in blocking)} goes with it because the "
                f"extractor refuses to start into a folder that already holds "
                f"{' or '.join(extract.DIRTY_MARKERS)}; a press that leaves it there stops "
                f"there instead. {extract.DIRTY_OUTPUT_NOTE}"
            )
            if blocking
            else ""
        )
        raise InstallerError(
            f"{self.entry.name} in {ctx.server_dir} was built before this app carried "
            f"{named}, and this press would skip the compile: the build is recorded and its "
            "images are still here. Patching the source now would leave the checkout holding "
            "a fix that the built server does not have and cannot get, because the extractor "
            "runs from the image and the maps it already wrote would not be rebuilt either. "
            "Nothing was changed, and the server you have goes on working exactly as it did. "
            f"To get the fix, keep this folder and take away what this press would skip: "
            f"use “{REBUILD_ACTION}” on the Server tab, then `docker image rm {images}` "
            f"and delete {doomed}, then install again into the same folder with the same "
            "client. That recompiles the server and extracts the maps a second time, which "
            "takes hours, and it leaves the install folder, its database and the characters in "
            f"it alone.{why} {self._why_not_to_delete_the_folder(ctx)}"
        )

    def _why_not_to_delete_the_folder(self, ctx: StageContext) -> str:
        """Why "delete it and install again" is the one repair not to reach for here.

        Split out because it is an argument about THIS install's database and
        not about patches: the same loop is waiting for any advice that treats
        the server directory as disposable while the volume beside it is not.

        Two wordings, because the trap has two sizes. A `generated` password is
        a secret that exists in exactly one place -- a file inside the folder --
        and deleting the folder makes the volume unopenable; a `fixed` one is in
        the catalog, so the same deletion merely fails to give the fresh start
        it looks like, because `install_id()` hashes the path and the reinstall
        lands back on the same volume. Neither branch names a file that is not
        there. Every CMaNGOS entry that ships a patch today is `generated`
        (`wow-tbc`, `wow-vanilla`, both `.db_password`); `wow-tortoise` is the
        one with no patches at all, so the refusal above cannot reach it, and
        the second wording is unreachable from shipped data.
        """
        plan = self.entry.install.password
        if plan.mode != "generated" or plan.file is None:
            return (
                f"Do not delete {ctx.server_dir} looking for a fresh start either: this "
                f"install's database is in the Docker volume {self._db_volume(ctx.server_dir)}, "
                "which removing the containers keeps and which a fresh install into the same "
                "folder comes straight back to."
            )
        volume = self._db_volume(ctx.server_dir)
        return (
            f"Do not delete {ctx.server_dir} instead: {plan.file} is inside it and is the only "
            f"copy of the password this install's database volume {volume} was created with. "
            "Removing the containers keeps that volume, so a fresh install into the same folder "
            "would stop for a lost password, and the only way past that stop deletes the volume "
            "and every character in it."
        )

    def _db_password(self, ctx: StageContext) -> Iterator[str]:
        """Persist the generated secret, or refuse to replace one the database already has.

        The spine resolved `ctx.secrets` before the stages ran — reading the
        file when it exists, minting `prefix + token_hex(8)` otherwise — so
        this stage's job is narrow: the evidence is the FILE (never recorded in
        the state file), and a missing file next to an existing `db-data`
        volume is a refusal. That volume was initialised with the password the
        file held; writing a new one would leave every later stage unable to
        log in, and the scripts' answer to that (wipe the volume silently) is
        exactly what the design rejects.

        Three outcomes, kept apart by `docker.volume_exists()`, which raises
        rather than guessing: the volume exists (refuse), no such volume (safe
        to write), Docker would not say (refuse). Collapsing the third into the
        second is the destructive edit, and it is the one that looks like
        robustness.

        The refusal names `docker volume rm` rather than a button. Checked
        2026-09-01: the Server tab's only removal action is "Stop and remove
        containers…", `docker.remove_staged()` passes no `-v` on purpose, and
        its own armed warning tells the user the volume is kept — so nothing in
        this app deletes a named volume, and sending the user there would send
        them round a loop that ends at this same message.
        """
        plan = self.entry.install.password
        if plan.mode != "generated":
            yield "This server uses a fixed database password; nothing to write."
            return
        if plan.file is None:
            # `PasswordPlan`'s validator refuses this shape and `resolve_secrets()`
            # refuses it again before stage 1; this keeps the type honest, and says
            # which defect it is rather than reporting a broken catalog as a design
            # choice the user should go looking for.
            raise InstallerError(
                f"{self.entry.name}'s catalog entry says its database password is generated "
                f"but names no file to keep it in. {CATALOG_ERROR_TAIL}"
            )
        path = ctx.server_dir / plan.file
        if path.is_file():
            yield f"The database password is already in {plan.file}; keeping it."
            return
        volume = self._db_volume(ctx.server_dir)
        try:
            exists = self._seams.volume_exists(volume)
        except docker.DockerCommandError as exc:
            # `DockerCliMissingError` subclasses this and lands here too, on
            # purpose: preflight already refused a machine with no Docker, the
            # outcome is identical (nothing is written), and `exc` carries that
            # error's own install-Docker help verbatim, which is the actionable
            # half. This module raises no such error of its own — deliberately
            # not spelling the constant, because `test_platform.py`'s
            # `test_the_missing_cli_help_names_every_module_that_raises_it`
            # finds raisers by searching module TEXT for the name, so a mention
            # here would be read as a raise.
            raise InstallerError(
                f"Docker would not say whether the database volume {volume} exists ({exc}), so "
                "this install cannot prove a new password is safe to write. Nothing was written."
            ) from exc
        if exists:
            raise InstallerError(
                f"{path} is gone, but this install's database volume {volume} still exists and "
                "was created with the password that file held. A new password would lock this "
                f"install out of its own database, so nothing was written. Put {plan.file} back "
                "if you have a copy of it. If it is lost, that database cannot be opened again: "
                f"`docker volume rm {volume}` deletes it, and every character in it, so this "
                "install can start over. Removing the containers does not delete it."
            )
        try:
            _write_secret(path, ctx.secrets.db_password)
        except OSError as exc:
            raise InstallerError(f"{path} could not be written: {exc}") from exc
        yield (
            f"Wrote this install's database password to {plan.file}. Back that file up: the "
            f"database in {volume} cannot be opened without it."
        )

    def _db_volume(self, server_dir: Path) -> str:
        """`<compose project>_<volume key>` — what `docker volume ls` shows for this install."""
        project = composegen.project_name(
            self.entry.id, server_dir, platform_id=self._seams.platform_id
        )
        return f"{project}_{DB_DATA_VOLUME}"

    def _password_origin_note(self, ctx: StageContext) -> str:
        """Where this install's database password came from — the half `render()` cannot know.

        Appended to `CarriedSecretError` only. `dockerfile.render()` compares values
        against a `Secrets` and is handed no path, so the remedies it can name are both
        about the KEY ("drop it", "file it under `DB_PASSWORD`"). Measured on m910q
        2026-09-05 over the three shipped `_public_tokens()` mappings: 1046 distinct
        password strings collide with a value in them (1031 by containment, 15 by
        equality), among them `characters`, `mariadb:11`, `tw_logon` and `vanilla-`. A
        user holding one of those is refused for a key this app put in the mapping
        itself — `CHAR_DB`, `DB_IMAGE` — so "drop the key" is advice they cannot act on,
        and the remedy in their hands, changing their own password, went unnamed.

        **A second route exists and this note deliberately does not offer it.** TWO values
        per mapping carry an 8-hex digest of the install folder, not three: measured on
        m910q 2026-09-05 by calling `_public_tokens(Path("/tmp/fixedsrv/srv"))` for the
        three shipped entries and filtering on `[0-9a-f]{8}`, each mapping's 18 keys give
        `['IMAGE_TAG', 'PROJECT_NAME']` and four such values across the 34-value union.
        `9bff3e81` said three and named `CONTAINER_PREFIX` as the third; that key is
        `tbc-` / `vanilla-` / `tortoise-` and carries no digest, so a user whose password
        is literally `tbc-` would have been sent to a folder that changes nothing. A
        different folder does clear a collision with the other two — and it is not cheaper
        than the remedy below: `PROJECT_NAME` is what `_db_volume()` is built from, and the
        same probe at `/tmp/othersrv/srv` returned `yulon-wow-tbc-85a2c58f_db-data` against
        `/tmp/fixedsrv/srv`'s `yulon-wow-tbc-f33d5256_db-data`. Moving the install
        abandons the database exactly as changing the password does, while clearing only 2
        of the 18 keys, so the sentence a user reads names the password and stays one
        sentence long.

        The volume is named because changing that password is not free once a database
        exists: the same fact `_db_password` refuses on, said before the user acts rather
        than after. Neither this note nor the refusal it joins ever prints the password.
        """
        plan = self.entry.install.password
        if plan.mode != "generated" or plan.file is None:
            return (
                "This server's database password comes from its catalog entry rather than from "
                "a file on this machine, so a collision between it and a value this app renders "
                f"is a bug in the app. {CATALOG_ERROR_TAIL}"
            )
        volume = self._db_volume(ctx.server_dir)
        return (
            "This install's database password is yours: it is the contents of "
            f"{ctx.server_dir / plan.file}. If you did not add the key named above, the thing "
            "to change is that password. It is not free — the database "
            f"volume {volume}, if it already exists, was created with whatever password the "
            "file held at the time, so "
            f"changing it means starting that database over (`docker volume rm {volume}` "
            "deletes it, and every character in it)."
        )

    def _write_dockerfile(self, ctx: StageContext) -> Iterator[str]:
        """Render `Dockerfile` + `.dockerignore` from the entry's template dir, marker rule applied.

        Marked and identical → nothing written, so the mtime does not move and a
        later `compose build` sees no change; marked and different → rewritten;
        unmarked → refused by `dockerfile.write()`, because a Dockerfile this
        engine did not write is somebody's own build. `.git` is excluded by the
        template so the ~1 GB build context is the tree and not its history
        (7.4a records the transfer time).

        **The record is not what skips this stage.** `dockerfile.write()`
        compares the TEXT it is about to write with the text on disk; `ctx.state`
        is not read here at all, and a resume that has `write-dockerfile` in
        `completed` reaches this body exactly like a first run.

        **Why `_public_tokens(ctx.server_dir)` and not `_secret_tokens(ctx)`.**
        K.4 handed `render()` one mapping WHOLE, password included, and leaned
        on the renderer to drop `DB_PASSWORD` and refuse a template naming it.
        That covered a name, and K.4's own review broke it (M15, 2026-09-01):
        a second secret key added to the single mapping —
        `"ROOT_PASSWORD": ctx.secrets.db_password` — rendered
        `ENV ROOT_PASSWORD=tbc-0123456789abcdef` into a Dockerfile with all
        1872 tests green (the whole suite on 2026-09-01, recorded at
        `9e198c05`). Splitting the mapping by capability is contract A6's
        answer, taken at 7.3 before K.7's SQL and verify arrived to inherit the
        old shape.

        The narrowing is NOT at this call site: `_public_tokens()` is the
        method a later stage writing into the build context asks for too, so
        the safe set is had by NAMING it rather than by remembering a rule
        here. What that buys is cost, not impossibility — `_public_tokens()`'s
        own docstring records what was measured about the difference.

        `render()`'s by-name refusal and its key drop STAY, as defence in
        depth: the glob-bypass test in `test_families_cmangos.py` hands
        `render()` the SECRET-bearing mapping on purpose, so the renderer's own
        refusal is still proved by a test this stage does not go through.

        **`secrets=ctx.secrets` is passed here and not from `_public_tokens()`,
        and the difference is the point.** §29's value half needs the real
        secret VALUES to compare against, and `render()` cannot know which of
        the opaque strings it is handed are secret — measured on m910q
        2026-09-05 at `0cc637c7`, a key spelled `BUILD_ARG` carrying the
        install's password rendered `ENV BUILD_ARG=tbc-0123456789abcdef` into a
        Dockerfile on disk with nothing to say about it. This body is where the
        two facts meet: `ctx.secrets` is in scope, and the mapping is one call
        away. `_public_tokens(server_dir)` keeps its narrow parameter list —
        nothing about 7.3's split is undone, because the secret is named as the
        thing that must NOT be emitted rather than added to what is.

        The argument is keyword-only and REQUIRED, so this stage cannot lose
        the guard by forgetting it and neither can the next caller; §29
        rejected an OPTIONAL one, and rightly.
        `test_the_write_dockerfile_stage_refuses_a_bland_key_carrying_the_install_password`
        drives THIS body rather than `render()` directly, because a guard
        proved only at the function is a guard nobody has shown reaches the
        production path ([[reviews-check-functions-not-call-sites]]).
        """
        native_block = self._native()
        if native_block.dockerfile_dir is None:
            raise InstallerError(
                f"{self.entry.name} names no `dockerfile_dir`, so there is nothing to build "
                "from. That is a catalog error in the app, not something to fix on this machine."
            )
        template_dir = self.installers_root / native_block.dockerfile_dir
        try:
            text, ignore = dockerfile.render(
                template_dir,
                self._public_tokens(ctx.server_dir),
                secrets=ctx.secrets,
            )
            written = dockerfile.write(ctx.server_dir, text, ignore)
        except dockerfile.CarriedSecretError as exc:
            # AHEAD of the `DockerfileError` arm below, which it subclasses.
            # `render()` proved a token value carries this install's password and
            # can name two remedies; the third — that the password is the user's
            # own and is the thing to change — needs the FILE, which `render()`
            # is never handed and this body has. See `_password_origin_note`.
            # Widened to the base class on purpose ONCE, as a mutation on
            # 2026-09-05: the note is then appended to "that file is not ours to
            # replace" as well, and the test below named
            # `test_write_dockerfile_passes_the_modules_sentence_through_and_...`
            # is the one that fails.
            raise InstallerError(f"{exc} {self._password_origin_note(ctx)}") from exc
        except dockerfile.DockerfileError as exc:
            # Already the sentence a user reads. A class name in front of
            # "that file was not written by Yu'lon" would be noise, not evidence.
            #
            # BOTH calls inside this `try` are covered - `render()` and `write()`
            # each raise `DockerfileError`, and each is translated here.
            #
            # This comment used to add that `stage_generate_compose` passed
            # `ComposeGenError` through the same way. It did not, and it
            # described an ASYMMETRY as if it were symmetry. Measured by AST on
            # 2026-09-02: eight functions in `composegen.py` raise
            # `ComposeGenError`, `render()` reaches seven of them, and the spine
            # translated only `write_plan` - the one of the eight `render()`
            # cannot reach. Measured through `install_wiring.main()` the same
            # day: a traceback instead of a sentence, and no `last_error`
            # recorded, on the install path of EVERY shipped game, because
            # `generate-compose` is the spine's own body and every family binds
            # it. That sentence is why nobody looked.
            #
            # The spine wraps `render()` now, so the TRANSLATION matches. The
            # COVERAGE still does not: the broad `(RuntimeError, OSError)` arm
            # below has no counterpart there, and `stage_build`'s
            # `built_image_refs()` call is bare - unreachable today behind
            # preflight's own check, but bare.
            raise InstallerError(str(exc)) from exc
        except InstallerError:
            # MUST stay ahead of the broad clause whatever else changes:
            # `InstallerError` subclasses `RuntimeError` (its `class` line in
            # `catalog/installer.py`; cited here as `installer.py:100` until
            # 2026-09-02, by which time it had moved to 92 — the symbol keeps,
            # the number does not), so
            # the clause below would otherwise catch a refusal and rewrap its
            # sentence inside a second one. Nothing in the `try` raises one as
            # this stands — `_native()` has already succeeded above, so the copy
            # of that check inside `_public_tokens()` cannot fire — precisely
            # why the ordering has to be written down rather than remembered.
            raise
        except (RuntimeError, OSError) as exc:
            # Defence in depth: `dockerfile.py` wraps every `OSError` it can raise
            # today, and `run()` catches `InstallerError` and nothing else, so an
            # escape from here is a traceback in the user's face instead of a
            # dialog. The class is NAMED because this arm by definition cannot
            # know what it caught, and a bare `OSError`'s own words say which
            # file and which errno but never which kind of failure — the same
            # reading `sqlplan._read_failure()` took of a read that went wrong.
            raise InstallerError(
                f"the Dockerfile for {self.entry.name} could not be written "
                f"({type(exc).__name__}: {exc})."
            ) from exc
        # A line per file, rather than the one collapsed "nothing changed" line
        # `stage_generate_compose` yields over its three compose files. With two
        # files the collapsed sentence has to either name one file while being
        # about both, or go silent about the file that did not move when the
        # other one did — and that mixed case is a real one here, because the
        # `.dockerignore` changes far less often than the Dockerfile.
        done = {path.name for path in written}
        for name in (dockerfile.DOCKERFILE, dockerfile.DOCKERIGNORE):
            if name in done:
                yield f"Wrote {name}"
            else:
                yield f"{name} is already exactly what this install needs."

    def _extract(self, ctx: StageContext) -> Iterator[str]:
        """Pull dbc/maps/vmaps out of the client, mounted read-only: one container per tool.

        The Tortoise script's model (`-i /client -o /out`, `:ro`, `-u`) adopted
        for every CMaNGOS game: nothing is ever written into the client, no
        `sudo chown` afterwards, and an interrupted run leaves only our own
        partial folders under `data/`. Skipping is per TOOL and lives in
        `extract.run_plan()`: a completion record, the `produces` counts and
        the stage-level facts (plan hash, client path, required-file size and
        mtime) must all agree — which is why the required file and the client
        build are handed over here. This body only resolves what the module
        cannot know: which image, which uid, where the client is, and what this
        machine's SELinux says.

        **Every seam is passed, none is left to default — and NOT for the
        reason written here until 2026-09-01.** That reason was that
        `run_plan`'s `selinux_enforcing` is bound at IMPORT like
        `platform.container_user_args()`'s `platform_id`, so a test faking the
        platform would never be seen. It was wrong, and it was wrong in the
        direction that gets cited. Asked of the interpreter rather than read
        (CPython 3.13.14, 2026-09-01):
        `signature(extract.run_plan).parameters["selinux_enforcing"].default`
        is `None`, and
        `signature(platform.container_user_args).parameters["platform_id"].default
        is platform.detect` is `True`. The second is the import-bound trap
        `_user_args()` is written against; the first is not one.
        `extract.py:755` had it right all along — the module attribute is
        resolved INSIDE the call, so a `monkeypatch` of
        `platform.selinux_enforcing` is seen whether this stage passes the seam
        or not.

        What passing it actually buys: `self._seams.selinux_enforcing` and the
        `platform` attribute are DIFFERENT objects, and
        `stage_generate_compose` asks the seam. Omitting the argument would put
        one question about one machine — is SELinux enforcing? — to two
        answerers inside a single install, so a faked Fedora would relabel for
        one answer and extract under the other. Deleting the argument was
        mutated on 2026-09-01 and
        `test_the_selinux_answer_reaches_every_extraction_container_and_no_mmaps_one`
        killed it; the decision is load-bearing even though the old reason for
        it was not true.
        """
        data = self._data()
        client_dir = ctx.client_dir
        if client_dir is None:
            raise InstallerError(
                f"{self.entry.name} needs the game client folder to extract its maps from, and "
                "none was given. Pick the client folder and try again."
            )
        data_dir = self._data_dir(ctx)
        image_ref = self._image_ref(ctx, data.extract.image)
        user_args = self._user_args()
        yield f"Extracting server data from {client_dir} into {data_dir} (the client is read-only)."
        yield from self._stream(
            lambda sink: extract.run_plan(
                data.extract,
                image_ref=image_ref,
                client_dir=client_dir,
                data_dir=data_dir,
                run_container=self._seams.run_container,
                user_args=user_args,
                sink=sink,
                cancel=ctx.cancel,
                required_file=data.client.required_file,
                client_build=self.entry.client.build,
                selinux_enforcing=self._seams.ask_selinux,
            ),
            cancel=ctx.cancel,
        )
        self._check_cancel(ctx.cancel)
        # Option C of `pyplan/upstream-cmangos-doodad-drop.md`, built as the
        # gate that proves `patch-sources` took rather than as a shipped
        # remedy: `DoodadCheck.line()` says why it warns and never refuses.
        check = extract.doodad_placements(data_dir / extract.BUILDINGS_DIR)
        if check is not None:
            yield check.line()
        yield "Extraction finished."

    def _mmaps(self, ctx: StageContext) -> Iterator[str]:
        """Generate movement maps from the extracted maps; the long stage after the build.

        Same evidence rule as one extraction tool (record + count), and
        `required: false` (Tortoise) turns a shortfall into a warning inside
        `extract.run_mmaps()`, which also refuses when `data/` holds no
        extraction evidence at all. A resume after a kill wipes `data/mmaps`
        and starts over — that is the cancel note, and it is true because the
        generator has no resumable state of its own.

        **`ctx.client_dir` is not read here, and that is the whole safety
        argument of the stage.** `run_mmaps()` removes `data_dir / MMAPS_DIR`
        before it generates, and the proof that no folder of the user's can
        reach that `rmtree` had three legs inside `extract.py` before anything
        called it — the signature takes no client path (unlike `run_plan`
        above), `MMAPS_DIR` is one relative component, and `MmapPlan` carries
        no folder field. This is the first caller, so this line is the fourth
        leg: `data_dir` is the server's own directory and a `ctx.client_dir`
        put in its place would read identically on the page.
        `test_no_mmaps_container_is_handed_anything_that_names_the_users_client`
        is what asserts it rather than trusting the reading — by ENUMERATING
        `ContainerRun`'s fields, since until 2026-09-01 it listed five of the
        eight by hand and a mutant that put `ctx.client_dir` into `user_args`
        as a real `-v` bind passed it (killed only by a neighbouring test that
        happens to pin `user_args`).

        The fifth leg is `_data_dir()`: `data_dir` itself must resolve inside
        the server directory, because `run_mmaps()` deletes `data/mmaps` and a
        `data/` that is a symlink into a client would carry that delete there
        without any of the four legs above noticing.

        **No `label:disable`, and deliberately not by precedent.** The extract
        stage above turns SELinux confinement off for its containers because
        they mount the user's client, which lives outside the server directory
        and which no `chcon` of ours reaches (`yulon-fedora-gate`, Fedora 44
        Enforcing, Docker 29.7.2, 2026-09-01). This container has no client
        mount at all: its single bind is `data/` under the server directory,
        measured readable and writable while confined on that same box, because
        `data/` inherits `container_file_t` from the relabel
        `stage_generate_compose` does. Copying the flag across would disable a
        container's confinement to buy nothing.

        That argument holds only while `generate-compose` runs BEFORE this
        stage. It does — `stages()` and `STAGE_NAMES` both put it at index 4
        (index 3 until `patch-sources` went in on 2026-09-05)
        against 5 and 6 — and
        `test_the_relabel_that_lets_mmaps_run_confined_happens_before_the_first_extraction`
        asserts the order over a live install, because a guard whose
        correctness is its position in a sequence is disarmed silently by a
        reordering that looks unrelated.
        """
        data = self._data()
        data_dir = self._data_dir(ctx)
        image_ref = self._image_ref(ctx, data.extract.image)
        user_args = self._user_args()
        yield "Generating movement maps (this can take an hour or more)."
        yield from self._stream(
            lambda sink: extract.run_mmaps(
                data.mmaps,
                image_ref=image_ref,
                data_dir=data_dir,
                run_container=self._seams.run_container,
                user_args=user_args,
                sink=sink,
                cancel=ctx.cancel,
            ),
            cancel=ctx.cancel,
        )
        self._check_cancel(ctx.cancel)
        yield "Map generation finished."

    def _conf(self, ctx: StageContext) -> Iterator[str]:
        """Materialise the `.conf.dist` files once, then patch the table's keys in place.

        Copy-once and patch-in-place is the whole difference from the scripts,
        which overwrote every conf on every run: a user's other edits survive a
        resume. `conf.materialise()` copies only what is missing, in one
        `docker cp` of the whole source dir; `conf.apply_table()` rewrites
        `Key = value` lines through the same token grammar as everything else
        and returns the files whose bytes changed — none means every key
        already read back equal, which is this stage's evidence.

        **The record is not what skips the copy.** `materialise()` asks `etc/`,
        `ctx.state` is not read here at all, and a resume carrying `conf` in
        `completed` reaches this body exactly like a first run — the same rule
        `_write_dockerfile` is written against, for the same reason: the files
        are the thing, and a state file that outlived them would leave an
        install with no confs and nothing to notice it.

        The password reaches these files because the emulator reads files, and
        `conf.CONF_MODE` is what that costs. What the mode buys is not the same
        everywhere — see `_write_secret` below, which measured the same
        question for `.db_password` — and `conf.py`'s constant carries the
        other half: `0o600` is correct only while the images run as root. No
        line this stage yields carries the value.

        Two `except` shapes, and they are not interchangeable. `materialise()`
        re-raises `copy_from_image`'s errors untouched (its `CopyFromImage`
        docstring says why: `DockerCliMissingError` subclasses
        `DockerCommandError` and carries the only actionable help), so this is
        where they become a sentence. Its OWN refusal — the image not shipping
        a `.dist` the table names — is an `InstallerError` and passes straight
        through: `InstallerError` subclasses `RuntimeError`, so a clause
        broadened to catch it would dress a catalog/image disagreement up as a
        machine that could not copy a file.

        The cast is about two spellings of one seam and nothing else, and it is
        the shape `_user_args()` already carries. `Seams.copy_from_image` is a
        positional `Callable[[str, str, Path], None]` (K.2) while
        `conf.CopyFromImage` is a Protocol declaring the parameter NAMES, and a
        `Callable` has no names to offer, so mypy refuses the pass-through.
        Checked 2026-09-01: `docker.copy_from_image`, `Recorder.copy_from_image`
        and every double in `test_families_cmangos.py` spell them `image`,
        `src` and `dest`, and `materialise()` calls the seam positionally, so
        nothing can be relying on a name it does not have.
        """
        data = self._data()
        etc_dir = ctx.server_dir / ETC_DIR
        image_ref = self._image_ref(ctx, data.extract.image)
        try:
            copied = conf.materialise(
                data.conf,
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
            # `materialise()` wraps none of its own file work — the mkdir, the
            # moves and the chmods are bare — so this arm is the one that turns
            # a full disk or a read-only server directory into a sentence.
            raise InstallerError(f"{etc_dir} could not be written: {exc}") from exc
        for path in copied:
            yield f"Copied {path.name} out of the server image."
        try:
            changed = conf.apply_table(data.conf, etc_dir, self._secret_tokens(ctx))
        except InstallerError:
            # MUST stay ahead of the broad clause: every refusal `apply_table()`
            # raises is already the sentence a user reads, and naming a file it
            # has just named would say it twice.
            raise
        except (RuntimeError, OSError) as exc:
            # Defence in depth, the same shape `_write_dockerfile` carries and
            # for the same reason: `run()` catches `InstallerError` and nothing
            # else, so an escape from here is a traceback in a dialog.
            raise InstallerError(
                f"the configuration files in {etc_dir} could not be patched "
                f"({type(exc).__name__}: {exc})."
            ) from exc
        if not changed:
            yield "The configuration files already say what this install needs."
        for path in changed:
            yield f"Patched {path.name}."

    def _import(self, ctx: StageContext) -> Iterator[str]:
        """The spine decides (probe → skip/refuse/reset); this family imports when it says run.

        `stage_import()` is called with `service=None`, so it runs the
        five-branch table over this family's `ImportGate` and returns without
        importing anything (A7): there is no compose one-shot in a CMaNGOS
        install, the SQL is applied from here. What the table DECIDED is read
        off the gate's last answer rather than probed a second time — see
        `_Remembering` — because a second probe is a second question, and
        between the two the answer can differ.

        Then, in order: phase 0 (the schemas, the app user and its grants),
        every phase in the plan's own order with its statements filled through
        the one token mapping, `sqlplan.verify()`, and only then the marker.

        Phase 0 is skipped by the `if plan.create:` below, HERE and not inside
        `create_schemas()`, so for Tortoise — whose `create` is empty — that
        function is never entered at all. This bypasses its deliberate
        ordering: it judges the plan's schema names before its own
        empty-`create` shortcut, precisely so a plan naming a database this
        game does not have is refused whether or not it creates anything.
        Nothing is lost today, because `sqlplan.expand()` a line below calls
        the same `_check_plan_schemas()` over the same mapping and raises the
        same sentence; the guard here is about not saying "Creating the
        databases ()" over an empty list.

        **The marker is written after verify and never before**, and that
        ordering is the whole of this stage's safety argument: `MarkerGate`
        reads a marker row as `imported` whatever the plan hash says, so a
        marker over a database that failed its checks is a hollow world the
        next install press would leave alone forever. A verify that fails, or
        that could not be run at all, therefore raises with no marker written,
        and the next press imports again.

        The mapping is `_secret_tokens()` and not `_public_tokens()`: a phase
        statement may legitimately carry `{{DB_PASSWORD}}` — the shipped
        Tortoise plan's `CREATE USER ... IDENTIFIED BY` does — and none of what
        is filled here is written into the build context. `sqlplan.expand()`
        fills statements only; a dump file is streamed as it lies on disk.
        """
        plan = self._data().sql
        gate = _Remembering(self._gate(ctx))
        yield from self.stage_import(ctx, gate, None)
        seen = gate.last
        if seen is not None and (
            seen.state == "imported" or (seen.state == "populated" and seen.complete)
        ):
            return
        db = self._native().db
        container = self.entry.container_spec().db
        password = ctx.secrets.db_password
        schemas = self._schemas()
        try:
            if plan.create:
                yield f"Creating the databases ({', '.join(plan.create)}) and the {db.user} user."
                sqlplan.create_schemas(
                    plan,
                    container=container,
                    client=db.client,
                    password=password,
                    schemas=schemas,
                    user=db.user,
                    charset=db.charset,
                    exec_stdin=self._seams.exec_stdin,
                )
            runs = sqlplan.expand(plan, ctx.server_dir, schemas, self._secret_tokens(ctx))
        except InstallerError:
            # Ahead of the broad clause, as everywhere else in this class:
            # `InstallerError` subclasses `RuntimeError`, and every refusal
            # `create_schemas()` and `expand()` raise is already the sentence a
            # user reads.
            raise
        except (RuntimeError, OSError) as exc:
            raise InstallerError(f"The database import could not be prepared: {exc}") from exc
        yield f"Importing {len(runs)} SQL steps over {len(plan.phases)} phases. This takes a while."
        yield from self._stream(
            lambda sink: sqlplan.apply(
                runs,
                container=container,
                client=db.client,
                password=password,
                exec_stdin=self._seams.exec_stdin,
                sink=sink,
                cancel=ctx.cancel,
            ),
            cancel=ctx.cancel,
        )
        self._check_cancel(ctx.cancel)
        try:
            failing = sqlplan.verify(
                plan,
                container=container,
                client=db.client,
                password=password,
                sql_query=self._query_seam(),
            )
            # Asked in the same `try` as the rules above, and BEFORE the marker,
            # because it answers the question the rules cannot. A COUNT rule
            # says a schema has enough tables; this says it reached the update
            # level its own phase applied. `wow-vanilla` passed every count
            # while 171 of its 172 core updates had failed, and the transcript
            # of that is byte-identical to the transcript of a broken world
            # (2026-09-03; the world turned out to be fine, which nobody could
            # have known from the run).
            failing += sqlplan.check_update_levels(
                runs,
                container=container,
                client=db.client,
                password=password,
                sql_query=self._query_seam(),
            )
        except InstallerError:
            raise
        except (RuntimeError, OSError) as exc:
            raise InstallerError(
                f"The import finished but its databases could not be checked "
                f"({type(exc).__name__}: {exc}). No completion marker was written, so the next "
                "install press checks again."
            ) from exc
        if failing:
            rules = "; ".join(
                f"{rule.db}: `{rule.query}` must answer at least {rule.min}" for rule in plan.verify
            )
            raise InstallerError(
                f"The import finished but these checks failed: {', '.join(failing)}. The rules "
                f"are {rules}. No completion marker was written, so the next install press "
                "imports again rather than starting a server with an empty world."
            )
        levels = sqlplan.update_levels(runs)
        yield f"Checked {len(plan.verify)} database rule(s); every one holds."
        if levels:
            yield (
                "Update level confirmed for "
                + ", ".join(
                    f"{level.schema} ({level.column})"
                    for level in sorted(levels, key=lambda x: x.schema)
                )
            )
        try:
            sqlplan.write_marker(
                plan,
                container=container,
                client=db.client,
                password=password,
                exec_stdin=self._seams.exec_stdin,
            )
        except InstallerError:
            # `write_marker()` goes through `sqlplan._run_sql()`, which turns
            # both of its failures into an `InstallerError` already naming the
            # marker. Without this arm the broad clause below would catch that
            # refusal — `InstallerError` is a `RuntimeError` — and wrap one
            # finished sentence inside another.
            raise
        except (RuntimeError, OSError) as exc:
            raise InstallerError(
                f"The import finished but its completion marker could not be written "
                f"({type(exc).__name__}: {exc})."
            ) from exc
        yield "The databases are imported and marked complete."

    def _gate(self, ctx: StageContext) -> ImportGate:
        """The family's `ImportGate`: the SQL plan's marker table, asked through the seams.

        A method rather than a value on the class because it needs the password
        out of `ctx.secrets`, which the spine resolves after construction. It
        is also the one seam this family's own tests replace, so the gate they
        drive and the gate an install drives are the same argument to
        `stage_import()`.
        """
        db = self._native().db
        return sqlplan.MarkerGate(
            self._data().sql,
            container=self.entry.container_spec().db,
            client=db.client,
            password=ctx.secrets.db_password,
            schemas=self._schemas(),
            sql_query=self._query_seam(),
            exec_stdin=self._seams.exec_stdin,
        )

    def _query_seam(self) -> sqlplan.SqlQuery:
        """`Seams.sql_query` under the name `sqlplan` declares for it. A cast, nothing else.

        The same shape `_conf` carries for `copy_from_image` and `_user_args`
        for `platform_id`, and here it is the `wsl_distro` erasure `Seams`
        documents: `Seams.sql_query` is spelled
        `Callable[[str, str, str, str | None, str], str]` — five positionals
        and no keyword — while `sqlplan.SqlQuery` is a Protocol that also
        declares `wsl_distro`, so mypy refuses the pass-through even though
        every real implementation takes it.

        Checked 2026-09-02: `docker.sql_query`, `Recorder.sql_query` and every
        double in `test_families_cmangos.py` accept `wsl_distro`, and the
        keyword is passed on every call made through this seam —
        `sqlplan.verify()` spells `wsl_distro=wsl_distro` unconditionally and
        `MarkerGate` spells `wsl_distro=self._wsl_distro`. What is always
        `None` from this family is the VALUE, because no install-side caller
        holds a distro (see `Seams`). So the cast asserts a keyword that is
        present and travelling, not one that is missing.

        Widening `Seams.sql_query` to spell the keyword would delete this cast
        and `_query_seam()` with it. That is a change to the seam contract and
        not to this stage; `native.py` records the erasure as open and
        undecided, and it is left where it is recorded.
        """
        return cast("sqlplan.SqlQuery", self._seams.sql_query)

    def _schemas(self) -> dict[str, str]:
        """The identity mapping over this game's schema NAMES, keyed by name (A10).

        Not keyed by role. `sqlplan` looks a plan's `into`, `into_each` keys,
        `create`, `marker_db`, `verify.db` and `player_data.db` up in here, and
        every one of those spells a database the way `catalog.json` spells it —
        `mangos`, `realmd`, `tw_world` — never `world` or `auth`. A mapping
        keyed by role would answer for none of them, and `expand()` refuses a
        name it cannot find rather than passing it through, so the import would
        stop before it wrote anything.

        Identity and not a rename, because the plan's names ARE the server's
        names; the mapping exists so that a plan naming a database this game
        does not have is refused by one check in `sqlplan`, in one place, ahead
        of every listing and every statement.
        """
        db = self.entry.databases
        return {name: name for name in (db.auth, db.characters, db.world, *db.extra)}

    # -- what only the engine knows ---------------------------------------

    def _data_dir(self, ctx: StageContext) -> Path:
        """`data/` under the server directory — refused if it resolves anywhere else.

        Both stages that use it write into this folder through a bind mount and
        `run_mmaps()` deletes `data/mmaps` outright, so where the name LANDS is
        a safety question and not a convenience one.

        Measured on this branch before the check existed (2026-09-01): with
        `data` made a symlink into a game client, `mkdir(exist_ok=True)`
        succeeds without noticing, `shutil.rmtree(data/mmaps)` followed the link
        and removed real client content, and the extraction bind would have
        written into the client the `:ro` mount exists to protect. The sibling
        shape is safe by accident and only by accident: when `data/mmaps` itself
        is the symlink, `rmtree` refuses with "Cannot call rmtree on a symbolic
        link" — which is `shutil`'s rule, not ours, and it does not cover the
        parent.

        Nothing in this app produces either shape; the path is built from
        `ctx.server_dir` and carries no client-derived component. The check is
        therefore about what somebody else can put on disk between two runs —
        it costs one `resolve()` per stage, it refuses instead of repairing, and
        it says which link it found. Someone symlinking `data/` onto a bigger
        disk is refused too, deliberately: the install folder as a whole can be
        put wherever they like, and telling them so is cheaper than deciding
        which outside destinations are the harmless ones.

        "This stage ran nothing and removed nothing" is scoped to the STAGE,
        and read "Nothing was run and nothing was removed" until the sixth
        pass of 2026-09-05. Both callers are stage bodies (`_extract`,
        `_mmaps`) and each asks this before it starts a container or deletes
        anything, so the narrow claim is true of each; the wide one was a
        claim about the press, and the press runs `build` before either of
        them. Measured on yulon-fedora 2026-09-05 through the real `run()`
        with the images gone: `build` logs "compiling" and "The build
        finished.", and this function's refusal lands three log lines after
        that (`Step …`, `--- extract`, the stage's cancel note; `run_plan()`'s
        refusal, one yield further on, lands four -- the round-10 review's
        press probe, `pyplan/gates/doodad-2026-09-05/round10-press-probe.txt`),
        so a sentence reading "Nothing was run" would be read directly under
        a line saying the build did.
        `extract.blocked_message()` carries the same scope for the same
        reason, and
        `test_a_data_folder_that_leads_out_of_the_install_is_refused_before_anything_runs`
        asserts this one on both stages and both attempts.
        """
        server_dir = ctx.server_dir
        data_dir = server_dir / DATA_DIR
        landing = data_dir.resolve()
        if not landing.is_relative_to(server_dir.resolve()):
            raise InstallerError(
                f"{data_dir} leads to {landing}, which is outside this install's folder "
                f"({server_dir}). This install writes extracted game data into that folder and "
                "deletes a folder inside it when it regenerates movement maps, so a link pointing "
                "elsewhere would let it overwrite and delete files that are not its own — a game "
                "client's, if that is where the link goes. This stage ran nothing and removed "
                f"nothing. Remove the link so {DATA_DIR} can be this install's own folder, or "
                "install this server in the folder you want its data to live in."
            )
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir

    def _native(self) -> NativeInstall:
        native_block = self.entry.install.native
        if native_block is None:  # preflight refuses first; this keeps the type honest
            raise InstallerError(f"{self.entry.name} has no `install.native` section.")
        return native_block

    def _data(self) -> CmangosData:
        """The typed block every family stage reads.

        Its absence is a catalog error in the app, never something wrong with
        this machine, and the refusal says so in `CATALOG_ERROR_TAIL`'s words.
        """
        data = self._native().cmangos
        if data is None:
            raise InstallerError(
                f"{self.entry.name} says its family is cmangos but carries no `cmangos` block. "
                f"{CATALOG_ERROR_TAIL}"
            )
        return data

    def _public_tokens(self, server_dir: Path) -> dict[str, str]:
        """The token mapping a writer into the BUILD CONTEXT gets: catalog plus install, no secret.

        **What the narrow parameter list bought, and what it did not.** This
        method is handed a `server_dir`, never a `StageContext`, so
        `ctx.secrets` is not in scope in this body and no key added here can
        read one out of the context. That is a different kind of protection
        from refusing a name: `dockerfile.render()` drops `DB_PASSWORD`, one
        key, and K.4's review broke it by adding a SECOND (M15, 2026-09-01 —
        `"ROOT_PASSWORD": ctx.secrets.db_password` next to the first). With the
        single mapping that mutation rendered
        `ENV ROOT_PASSWORD=tbc-0123456789abcdef` into a Dockerfile and all 1872
        tests still passed — the whole suite on 2026-09-01, recorded at
        `9e198c05`.

        **It is a price, not a wall, and the price was measured.** An earlier
        draft of this docstring said the secret was structurally out of reach
        here. A review disproved that BY EXECUTION on 2026-09-02, on the merge
        of this branch with `yulon-phase7`. `resolve_secrets(server_dir)` is a
        PUBLIC inherited method (`native.py`) taking exactly the one argument
        this body already holds, and K.3's `db-password` stage (`STAGE_NAMES`
        index 1) runs ONE stage ahead of `write-dockerfile` (index 2) — this
        said "two stages" until 2026-09-02, the same off-by-one the stage list
        below was rewritten to stop — and writes the password into that very
        `server_dir` — so by the time this runs, `resolve_secrets()` no longer
        mints anything: it reads the install's real password back off disk.
        Six lines,

            _root_pw: str = ""                                   # on the class
            if not type(self)._root_pw:                          # in a helper
                type(self)._root_pw = self.resolve_secrets(server_dir).db_password
            "ROOT_PASSWORD": self._root_pw_via_resolve(server_dir),   # here

        rendered `ENV ROOT_PASSWORD=tbc-deadbeefcafe1234` — the value read from
        `.db_password`, not a minted one — into a Dockerfile on disk, with the
        whole suite at 1884 passed, 3 skipped (2026-09-02, recorded at
        `2116231b`). (The cache is load-bearing in
        the mutation, not in the argument: resolving on every call makes this
        mapping non-deterministic and a dict-equality test then kills the edit
        for a reason that has nothing to do with secrecy. A leak that is
        careful enough to be consistent is not caught at all.)

        **The price is not a figure, and both figures written here were refuted
        by execution.** The first draft said "structurally out of reach" and the
        route above disproved it. The second said "about SIX lines that have to
        name a public method and cache its answer", and a review disproved that
        on 2026-09-02 with a cheaper route (M-R2, reproduced here the same day):
        `generate-compose` merges `DB_ROOT_PASSWORD=<the plaintext install
        password>` into `<server_dir>/.env`, so a plain helper reading that
        file — naming no public method, caching nothing, holding nothing
        between calls — put the real password in this mapping under
        `"ROOT_PASSWORD"` and rendered `ENV ROOT_PASSWORD=tbc-0123456789abcdef`
        into a Dockerfile, with the suite at 1889 passed, 3 skipped (2026-09-02,
        recorded at `e176af17`) and mypy, ruff and black clean. That route needs
        `.env` to be on disk already: `generate-compose` was index 3 and this
        method's only build-context caller, `_write_dockerfile`, index 2 (as
        measured; `patch-sources` moved every index below up by one on
        2026-09-05, and the ORDER, which is the argument, did not change) — so
        it reads an empty hand on a FIRST install and the real password on any
        run where a previous attempt reached `generate-compose`. A price, still,
        just not one paid on the first press.

        So what is claimed for the split is only what has been measured, and it
        is about visibility rather than reach. Three leaks have now been
        measured into this mapping — M15 through `ctx.secrets`, the
        `resolve_secrets()` cache, and M-R2 through `.env` — and each had to add
        a line naming something outside this body (a context field, an
        inherited method, a file path) where the pre-split single mapping needed
        only one more key next to a password it already held. Nobody has
        measured a cheaper route than M-R2; nobody has shown there is none, and
        the two attempts to write down a floor were both wrong within a day. It
        is not a guarantee and nothing below should be read as one — the
        guarantees are `render()`'s key-drop and its by-name refusal, which
        cover the names in `SECRET_TOKENS` for any mapping any caller passes.
        `pyplan/bug-checklist.md` §29 states the general form: a secret minted
        inside a function rather than passed into it was never a field of
        anything, so no signature can exclude it.

        `composegen.entry_tokens()` is the catalog-derived set the compose
        templates use (`DB_IMAGE` … `MAKE_JOBS`, `CORE_DIR` being the in-image
        install prefix, `LOGS_DB` absent rather than empty when the entry has
        no extra schema); this adds what only an install knows — the compose
        project name, the image tag and prefix, the host ports, the realm host.
        The same grammar and the same `composegen.fill()` everywhere, so a
        value between a template and a conf table cannot become a silent
        literal: an unknown token is refused.

        **The server dir `docker build` is pointed at is not secret-free, and
        has not been since K.3.** An earlier draft said two stages wrote into it
        before the build; the count was wrong twice over, and a count in prose
        cannot go red, so what follows is the stages themselves, each read off
        `STAGE_NAMES` and each verified on 2026-09-02 by running the stage and
        reading the file it left:

        * `db-password` (index 1 when measured; 2 since 2026-09-05) writes the
          plaintext password to the file `install.password` names, at the ROOT
          of the server dir.
        * `generate-compose` (index 3 when measured, 4 since 2026-09-05 — either
          way the stage IMMEDIATELY before `build`) merges
          `DB_ROOT_PASSWORD=<that same plaintext>` into
          `<server_dir>/.env` for every generated-password entry, which is all
          three CMaNGOS games. This is the one the earlier draft's "two stages"
          left out, and it is the one M-R2 read.
        * `conf` (index 7) writes password-bearing `.conf` files under `etc/`
          in the same tree, after the build rather than before — a later
          rebuild in the same folder still finds them there.

        The first two are asserted rather than described:
        `test_the_build_context_already_holds_the_plaintext_password_before_the_build_stage`
        reads both orderings off `STAGE_NAMES` and both files off the disk the
        stages wrote, so a renamed stage stops it dead and a reordered one
        turns it red. It does NOT go red when a stage is merely added —
        `test_family_and_stage_names_are_the_contract_tuple` is the one that
        does, by restating the tuple in full.

        Only the leading `*` in each shipped `dockerignore.tmpl` keeps any of
        the three out of what the daemon receives. Two tests go red when it is
        deleted; nothing on this branch is checking that `.env` and `etc/` in
        particular stay out. What each one actually asserts, measured
        2026-09-02:

        * `test_every_shipped_dockerignore_excludes_the_entrys_password_file`
          holds the line for the password FILE, and only for that. It asks
          `install.password` for the name rather than restating `.db_password`,
          and its matcher answers only for a name at the ROOT of the context —
          the helper has no parent-directory walk and its own docstring says
          so. It cannot speak for `etc/mangosd.conf` or, though it sits at the
          root, for anything the templates re-include: appending `!etc` to
          `wow-tbc/native/dockerignore.tmpl` left all three of its
          parametrisations green.
        * `test_every_cmangos_dockerignore_admits_only_the_core_tree_it_copies`
          (`test_composegen.py`, arrived with `yulon-phase7`) is what caught
          that `!etc` edit. It pins the SHAPE — first line `*`, exactly ONE `!`
          line, and that line names the core tree — so a second re-include of
          any spelling turns it red.

        Said plainly, because the credit for this used to sit in the wrong
        place: the exclusion of `etc/*.conf` is asserted by NOTHING on this
        branch. What stands between those confs and an image layer is that
        shape test refusing a second `!` line, plus Docker's own rule that an
        excluded directory takes its children with it — a rule this app does
        not implement and no test here exercises.

        Of the stages that take a token mapping from this class,
        `write-dockerfile` takes THIS one through `dockerfile.render()`.
        `generate-compose` takes none at all — `composegen.generate()` builds
        its own from `entry_tokens(entry)` and refuses a compose template
        naming `{{DB_PASSWORD}}` in generated mode. That is a statement about
        TOKEN MAPPINGS only and must not be read as one about the build
        context: the same stage still writes the plaintext password into `.env`
        in that directory, which is the second bullet above.

        Why the Dockerfile is the harder half: a secret in a compose file sits
        in a file the user owns — delete it, rotate, done — while a Dockerfile
        is copied into a content-addressed image LAYER that `docker history`
        prints long after the file is gone, and undoing that means finding
        every image built from the layer.
        """
        native_block = self._native()
        platform_id = self._seams.platform_id
        return {
            **composegen.entry_tokens(self.entry),
            "PROJECT_NAME": composegen.project_name(
                self.entry.id, server_dir, platform_id=platform_id
            ),
            "IMAGE_PREFIX": native_block.image_prefix,
            "IMAGE_TAG": composegen.image_tag(server_dir, platform_id=platform_id),
            "DB_PORT": str(self.entry.ports.db),
            "AUTH_PORT": str(self.entry.ports.auth),
            "WORLD_PORT": str(self.entry.ports.world),
            "REALM_HOST": INSTALL_REALM_HOST,
        }

    def _secret_tokens(self, ctx: StageContext) -> dict[str, str]:
        """`_public_tokens()` plus every secret in `ctx.secrets` — for consumers that need one.

        Opting IN is the whole point, and it is opt-in by asking for a
        different method rather than by passing a flag: a flag defaults safe
        but leaves the secret in scope in one body, so the M15 mutation stays
        writable inside the safe branch and a flipped default reaches every
        caller at once.

        `conf` is the consumer today, and it is the case that proves the
        abstraction needs two sets rather than none: the emulator reads its
        `.conf` files, so the password has to be IN them, and `conf.CONF_MODE`
        (0600) is what that costs. K.7's `import` stage is the next — the
        shipped SQL carries `CREATE USER … IDENTIFIED BY '{{DB_PASSWORD}}'`,
        and verify connects with it.

        The secret half is derived by `secret_token_map()` from the fields of
        the `Secrets` instance, so adding a secret to `native.Secrets` extends
        this mapping and cannot extend `_public_tokens()`.

        **The two halves must not overlap, and the merge is what makes that
        matter.** `{**public, **secret}` lets the secret half WIN. A field
        added to `native.Secrets` under a name the public half already spells
        — `db_host`, `db_user`, `core_dir` are all one plausible field away —
        would silently replace that token's value with a password in every
        `.conf` value, every SQL statement and every verify connection that
        spends this mapping, and each of them would still be filled, still be
        syntactically fine, and still pass a test that only checks
        `{{DB_PASSWORD}}` came out right. So the collision is refused rather
        than merged.

        It ends in `DECLARATION_ERROR_TAIL` and not `CATALOG_ERROR_TAIL`,
        corrected 2026-09-02. Both halves of the collision are Python: the
        secret half's names come from the fields of `native.Secrets`, and the
        public half's from string literals in this file and in
        `composegen.entry_tokens()`. No catalog file can reach either, so the
        catalog tail sent a reader somewhere there was nothing to find.
        """
        public = self._public_tokens(ctx.server_dir)
        secret = secret_token_map(ctx.secrets)
        shadowed = sorted(set(public) & set(secret))
        if shadowed:
            raise InstallerError(
                f"{', '.join(shadowed)} is both a public install token and a field of "
                "`native.Secrets`, so building the conf mapping would put the password where "
                "the public value belongs. Rename the `native.Secrets` field. "
                f"{DECLARATION_ERROR_TAIL}"
            )
        return {**public, **secret}

    def _image_ref(self, ctx: StageContext, service: str) -> str:
        """The fully qualified reference of the image `service` names in `native.images`.

        The one image per CMaNGOS game is extractor, conf source and runtime at
        once; the data block names it by service key, and the reference is the
        same string `stage_build` asked the daemon about.
        """
        prefix = self._native().image_prefix
        refs = composegen.built_image_refs(
            self.entry, ctx.server_dir, platform_id=self._seams.platform_id
        )
        for ref in refs:
            if ref.startswith(f"{prefix}{service}:"):
                return ref
        raise InstallerError(
            f"{service} is not one of the images this install builds "
            f"({', '.join(self._native().images)}). {CATALOG_ERROR_TAIL}"
        )

    def _user_args(self) -> tuple[str, ...]:
        """`--user uid:gid` on Linux, nothing on Docker Desktop — one policy, `platform.py`'s.

        The seam is passed rather than left to default: `container_user_args`'s
        `platform_id` default is bound at import, so an engine that omitted it
        would ask the real host and hand a Linux install no `--user` under
        every test that fakes the platform. The cast is only about the type
        `Seams` chose (`Callable[[], str]`, wider than `PlatformId` since 7.1);
        the function compares against `"linux"` and treats every other string
        as Docker Desktop, so a seam answering something else is safe.
        """
        ask = cast("Callable[[], platform.PlatformId]", self._seams.platform_id)
        return tuple(platform.container_user_args(platform_id=ask))

    def _stream(
        self, call: Callable[[docker.OutputSink], Iterator[str]], *, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Run a stage-kind generator that ALSO takes a sink, and yield both streams live.

        `extract.run_plan()` and `sqlplan.apply()` yield their own progress
        lines and push container output into a sink. The spine's `_pump()`
        bridges a push-only call; this is the same queue-and-worker bridge for a
        call that is both, so a two-hour extraction shows line by line rather
        than as a list at the end.

        Deliberately the same shape as `_pump()`, abandonment included. Until
        2026-09-04 that shape leaked: `GeneratorExit` at the `yield` skipped
        `worker.join()` and the worker went on running — for the extract stage,
        a live multi-hour extraction with no owner. What stood in for a fix was
        an ORDERING somewhere else: `LogPanel.stop()` sets the cancel event
        BEFORE it asks its worker to stop, so `run_container(cancel=…)`
        returned and the thread ended by itself. Nothing made that ordering
        load-bearing, and an abandonment without a cancel was prevented by
        nothing at all. `stop_abandoned_worker()` now does it here, where the
        abandonment is, so a reorder up in the panel is no longer the only
        thing between an extraction and running forever.
        """
        lines: queue.Queue[str | None] = queue.Queue()
        failure: list[BaseException] = []

        def work() -> None:
            try:
                for line in call(lines.put):
                    lines.put(line)
            except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread below
                failure.append(exc)
            finally:
                lines.put(None)

        worker = threading.Thread(target=work, daemon=True, name="yulon-cmangos-output")
        worker.start()
        try:
            while True:
                item = lines.get()
                if item is None:
                    break
                yield item
        except BaseException:
            # See `_pump()`: abandonment or an exception thrown into this
            # frame, never the normal path, which leaves by `break`. Widened
            # from `GeneratorExit` on 2026-09-04 for the reason measured there
            # — a Ctrl+C lands in `lines.get()` as a `KeyboardInterrupt`.
            stop_abandoned_worker(worker, cancel, what="the CMaNGOS stage output")
            raise
        worker.join()
        if failure:
            exc = failure[0]
            if isinstance(exc, InstallerError):
                raise exc
            raise InstallerError(f"the step could not be run: {exc}") from exc


@dataclass
class _Remembering:
    """An `ImportGate` that keeps its last probe, so the family need not probe twice.

    `stage_import()` yields strings and returns nothing, so a caller that has
    to know WHICH branch it took has two choices: probe again, or watch. This
    watches. Probing again is not the same question asked twice — between the
    two answers the import could have been finished by another press, and the
    branch this stage then takes would be the one the spine did not.

    `last` is None only before the first probe, and `stage_import()`'s first
    act is `gate.probe()`, so `_import` reads the attribute after that has
    happened.
    `test_the_remembering_gate_keeps_the_last_answer_and_has_none_before_the_first_probe`
    holds this class's own half of that: the None before anything is asked, the
    inner gate's answer passed through rather than replaced, the LAST of
    several answers rather than the first, and `reset()` reaching the inner
    gate. The `is not None` in `_import` is defence over a state that call
    cannot leave behind, and it reads as one — measured 2026-09-02, removing it
    left the suite green.
    """

    inner: ImportGate
    last: docker.ImportState | None = None

    def probe(self) -> docker.ImportState:
        self.last = self.inner.probe()
        return self.last

    def reset(self) -> tuple[str, ...]:
        return self.inner.reset()


def _write_secret(path: Path, value: str) -> None:
    """Write the password, asking for `SECRET_FILE_MODE` at creation. One trailing newline.

    The newline is a contract: `resolve_secrets()` reads this file back with
    `.strip()` (A8), and a file the user opened in Notepad and saved gets one
    whether we write it or not.

    **What the mode buys, measured rather than assumed** (PKGAME-LAPTOP,
    Windows 11 26200, CPython 3.13.14, 2026-09-01): on POSIX the mode is
    applied by `open(2)` itself, so the file is owner-only from its first byte
    and never has a window at 0644. On Windows it does nothing at all — the
    file lands at `st_mode 0o666`, byte-identical to a plain `open(path, "w")`,
    and `icacls` shows only the ACEs inherited from the parent folder; under a
    folder granting `BUILTIN\\Users:(RX)` the secret is readable by every local
    account. A following `os.chmod(path, 0o600)` changes neither, so no
    rearrangement of these two calls buys anything there.

    So this is a POSIX guarantee and a Windows no-op, and the sentence the
    stage yields does not promise otherwise.
    `test_the_secret_file_is_owner_only_on_posix_and_only_inherits_the_folder_acl_on_windows`
    pins both halves; the Windows half goes red if that ever changes.

    **Open: Windows ACLs.** Making this owner-only on Windows means an explicit
    DACL (`pywin32`, or `icacls /inheritance:r /grant:r`) on a file the user may
    move, copy or restore from a backup — a new dependency or a new subprocess,
    on every path that touches the file. Not attempted here; it is a decision
    about the app's Windows security posture, not about this stage, and
    `conf.py` writes the same password into `*.conf` under the same limitation.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SECRET_FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(value + "\n")
