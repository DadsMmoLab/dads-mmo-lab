"""The CMaNGOS-lineage install engine: one class for every entry whose `family` is `cmangos`.

Stage ORDER is Python (this tuple); stage PARAMETERS are the entry's typed
`install.native.cmangos` block. The class therefore names no game — checked
by hand against this file and clean (review, 2026-09-01); the test that will
assert it, over code with docstrings stripped and for every family module, is
`test_family_modules_contain_no_game_literal` in
`tests/test_catalog_invariants.py`, and G.7 is the task that lands it — and
the third CMaNGOS game costs a catalog entry plus templates, no Python
(`pyplan/phase7-decisions.md`, "Family engines — chosen").

Every body here is a thin wrapper: it pulls one typed block off the entry,
resolves the few values only the engine knows (the built image reference, the
uid:gid policy, the token mapping, the secret) and hands them to a stage-kind
module (`dockerfile`, `extract`, `conf`, `sqlplan`). The spine
(`StagedInstaller`) owns clone, compose, build, start-db, up and ready, it
owns the import branch table, and it yields every stage's cancel note — this
family only supplies the `MarkerGate` and runs the SQL plan when the table
says run.

`db-password` is the one stage with a body of its own, because its evidence is
a file and not the state file: a state file must never be the thing that
claims a secret exists.

`STAGE_NAMES` is the whole tuple from the start; `stages()` binds the six the
spine already owns, and K.3-K.7 insert the rest in that order. The two are
allowed to disagree in the meantime because nothing in the app reads
`STAGE_NAMES` — `stage_names()`, derived from `stages()`, is what the spine
validates a resume against — and because this class is not in `FAMILIES` until
the tuple is whole. K.8 pins the equality.
"""

from __future__ import annotations

import os
import queue
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import ClassVar, cast

from yulon import docker, platform
from yulon.catalog import composegen
from yulon.catalog.catalog import CmangosData, NativeInstall
from yulon.catalog.installer import InstallerError
from yulon.catalog.native import (
    BUILD_CANCEL_NOTE,
    INSTALL_REALM_HOST,
    Stage,
    StageContext,
    StagedInstaller,
)
from yulon.log import get_logger

logger = get_logger(__name__)

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


class CmangosInstaller(StagedInstaller):
    """Install a CMaNGOS server: clone, Dockerfile, build, extract, conf, SQL plan, start."""

    family = "cmangos"
    STAGE_NAMES: ClassVar[tuple[str, ...]] = (
        "clone-sources",
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
            Stage("db-password", self._db_password, recorded=False),
            Stage("generate-compose", self.stage_generate_compose),
            Stage("build", self.stage_build, cancel_note=BUILD_CANCEL_NOTE),
            Stage("start-db", self.stage_start_db, recorded=False),
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
                "but names no file to keep it in. That is a catalog error in the app, not "
                "something to fix on this machine."
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

    # -- what only the engine knows ---------------------------------------

    def _native(self) -> NativeInstall:
        native_block = self.entry.install.native
        if native_block is None:  # preflight refuses first; this keeps the type honest
            raise InstallerError(f"{self.entry.name} has no `install.native` section.")
        return native_block

    def _data(self) -> CmangosData:
        """The typed block every family stage reads.

        Its absence is a bug in the app's catalog, never something wrong with
        this machine, and the refusal says so.
        """
        data = self._native().cmangos
        if data is None:
            raise InstallerError(
                f"{self.entry.name} says its family is cmangos but carries no `cmangos` block. "
                "That is a bug in the app's catalog, not something to fix on this machine."
            )
        return data

    def _tokens(self, ctx: StageContext) -> dict[str, str]:
        """The one token mapping for the Dockerfile, the conf tables, the SQL statements and verify.

        `composegen.entry_tokens()` is the catalog-derived set the compose
        templates use (`DB_IMAGE` … `MAKE_JOBS`, `CORE_DIR` being the in-image
        install prefix, `LOGS_DB` absent rather than empty when the entry has
        no extra schema); this adds what only an install knows. The same
        grammar and the same `composegen.fill()` everywhere — a value between
        a template and a conf table cannot become a silent literal, because an
        unknown token is refused.

        `DB_PASSWORD` is in here because the conf tables need it: conf files
        are written 0600 and the emulator reads them, and the compose templates
        never see it in generated mode — `composegen.generate()` refuses a
        compose template that so much as spells `{{DB_PASSWORD}}` in that mode,
        by name, so the secret stays in `.env`.

        This same mapping is what `write-dockerfile` hands to
        `dockerfile.render()`, whole — and the Dockerfile side now takes the
        same decision by itself rather than trusting the caller with it.
        `dockerfile.SECRET_TOKEN` refuses a template that so much as spells
        `{{DB_PASSWORD}}`, by name and by path, and drops the key from the
        mapping it fills with, so nothing this mapping carries can reach a file
        in the build context. It is the harder case of the two: a compose file
        holding the secret is the user's own file (delete it, rotate, done),
        while a Dockerfile is copied into a content-addressed image layer that
        `docker history` prints long after the file is deleted.

        So: the Dockerfile may NOT see the secret — the question 7.3 left
        undecided when contract A6 specified one mapping for the Dockerfile, the
        conf tables, the SQL and verify alike. Handing the whole mapping over
        stays A6's shape; what changed is that `render()` no longer takes the
        password from it.
        """
        native_block = self._native()
        server_dir = ctx.server_dir
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
            "DB_PASSWORD": ctx.secrets.db_password,
            "REALM_HOST": INSTALL_REALM_HOST,
        }

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
            f"({', '.join(self._native().images)}). That is a catalog error in the app, not "
            "something to fix on this machine."
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

    def _stream(self, call: Callable[[docker.OutputSink], Iterator[str]]) -> Iterator[str]:
        """Run a stage-kind generator that ALSO takes a sink, and yield both streams live.

        `extract.run_plan()` and `sqlplan.apply()` yield their own progress
        lines and push container output into a sink. The spine's `_pump()`
        bridges a push-only call; this is the same queue-and-worker bridge for a
        call that is both, so a two-hour extraction shows line by line rather
        than as a list at the end.

        Deliberately the same shape as `_pump()`, including what that shape does
        NOT do: if the consumer abandons this generator, `GeneratorExit` is
        raised at the `yield` and `worker.join()` is never reached, so the
        worker keeps running and keeps pushing into a queue nobody reads. The
        app has exactly one abandonment path — `log_panel._StreamWorker.run()`
        breaking out of its `for`, which drops the last reference and closes the
        generator chain — and `LogPanel.stop()` sets the cancel event BEFORE it
        asks the worker to stop, so `run_container(cancel=…)` returns and the
        thread ends on its own. A join in a `finally` would be worse than the
        leak: it would block whoever is abandoning for as long as the
        extraction has left to run. An abandonment WITHOUT a cancel would leak a
        live extraction, and nothing prevents one — undecided; `_pump()` has
        shipped with the same hole since 7.1 and no note says it was weighed.
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
        while True:
            item = lines.get()
            if item is None:
                break
            yield item
        worker.join()
        if failure:
            exc = failure[0]
            if isinstance(exc, InstallerError):
                raise exc
            raise InstallerError(f"the step could not be run: {exc}") from exc


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
