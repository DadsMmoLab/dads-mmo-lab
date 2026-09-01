"""The CMaNGOS-lineage install engine: one class for every entry whose `family` is `cmangos`.

Stage ORDER is Python (this tuple); stage PARAMETERS are the entry's typed
`install.native.cmangos` block. The class therefore names no game — a test
asserts, over code with docstrings stripped, that no game literal appears —
and the third CMaNGOS game costs a catalog entry plus templates, no Python
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

import queue
import threading
from collections.abc import Callable, Iterator
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

        **There is no equivalent refusal on the Dockerfile side, and this same
        mapping is what `write-dockerfile` hands to `dockerfile.render()`.**
        `render()` fills through `composegen.fill()`, which minds an UNFILLED
        placeholder and says nothing about a spelled one, so a `Dockerfile.tmpl`
        that named the token would render the password into the build context
        and — through an `ENV` or a `RUN` — into an image layer `docker history`
        prints. No shipped template does;
        `test_no_dockerfile_template_names_the_secret_this_one_mapping_carries`
        is what keeps saying so. Whether the Dockerfile is meant to be able to
        see the secret at all: undecided — the 7.3 contract (A6) specifies this
        single mapping for the Dockerfile, the conf tables, the SQL and verify
        alike, and gives no reason.
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
