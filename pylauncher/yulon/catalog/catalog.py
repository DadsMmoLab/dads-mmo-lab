"""The Catalog schema: typed models for `catalog.json` (roadmap 3.1).

One entry per installable server. Everything an installer, a controller or
the networking helpers need to know about a game — emulator sources, the
`native` block its family engine reads (Phase 6/7), container names, the
auth/world/db port table (README §13), database names, what client the user
must supply (README §3a) — is data here, not Python (style-guide §3). Acronyms only
(§6): `id`s are `wow-wotlk`, `wow-tbc`, `wow-vanilla`, `wow-tortoise`.
"""

from __future__ import annotations

import builtins
import fnmatch
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

from yulon import server_build_presses
from yulon.docker import ContainerSpec
from yulon.manifest import Db, Source
from yulon.platform import PlatformId

CATALOG_FILE = Path(__file__).resolve().with_name("catalog.json")

Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
Status = Literal["stable", "beta", "wip"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EmulatorSource(Source):
    """One repository the installer clones, and where it lands under the server dir.

    `dest` replaced an index rule ("sources[0] is the core, the rest go under
    `modules/`") that only AzerothCore's layout satisfied: CMaNGOS's playerbots
    checkout nests INSIDE the core at `src/mangos-tbc/src/modules/Bots`, which
    no index can say. Relative to the server dir, POSIX-spelled; `"."` means the
    server dir IS the checkout, as it is for AzerothCore.
    """

    dest: str = Field(
        min_length=1,
        description="Clone target relative to the server dir; '.' means the server dir itself.",
    )

    @field_validator("dest")
    @classmethod
    def _dest_stays_inside_the_server_dir(cls, value: str) -> str:
        path = PurePosixPath(value)
        if "\\" in value or path.is_absolute() or ".." in path.parts:
            raise ValueError(
                f"dest must be a relative POSIX path inside the server dir, got {value!r}"
            )
        return value


class Emulator(_Strict):
    """The open-source emulator: a display name and the repos the installer clones."""

    name: str = Field(min_length=1)
    sources: tuple[EmulatorSource, ...] = Field(min_length=1)


class PasswordPlan(_Strict):
    """How this game's database root password comes to exist (phase7-decisions "Password").

    `fixed` is WotLK's `"password"` — a contract with backup, the console and
    every archived guide, spliced into the base compose file as the
    `${DB_ROOT_PASSWORD:-…}` default. `generated` is the CMaNGOS entries':
    resolved by the install spine before stage 1 as `prefix + token_hex(8)`,
    persisted at `file` under the server dir, and reaching compose only
    through `.env`, never the compose text. One model rather than two optional
    strings because the old pair (`db_root_password`,
    `db_root_password_file`) let an entry say both or neither, and nothing
    refused either.
    """

    mode: Literal["fixed", "generated"]
    value: str | None = Field(default=None, description="The password itself; required when fixed.")
    file: str | None = Field(
        default=None,
        description=(
            "File under the server dir holding the generated value, e.g. `.db_password`; "
            "required when generated."
        ),
    )
    prefix: str = Field(
        default="",
        description=(
            "Prefix on a generated value (`tbc-`), so a password seen in a log or a `docker "
            "exec` says which server it belongs to. `resolve_secrets()` mints "
            '`f"{prefix}{secrets.token_hex(8)}"` — the dash is part of the prefix.'
        ),
    )

    @model_validator(mode="after")
    def _the_mode_has_its_field_and_only_its_field(self) -> PasswordPlan:
        """Each mode needs its own field and refuses the other's.

        The "and only its own" half is the load-bearing one, and it was missing
        until B.6: `{"mode": "generated", "value": "x"}` validated, while
        `composegen.render()` reads `.value` to decide what to splice into the
        compose TEXT. An entry could therefore promise a per-install secret that
        never leaves `.env` and hand a real password to a file git can see —
        the leak B.6's `{{DB_PASSWORD}}` refusal exists to stop, arriving
        through the model instead of the template. The mirror clause is cheaper
        but the same shape: a `file` on a fixed plan is two sources of truth for
        one password with nothing saying which wins.
        """
        if self.mode == "generated" and self.value is not None:
            raise ValueError(
                "a generated password plan must not carry a `value`: the secret is minted per "
                "install and lives in `file` and `.env`, never in the catalog or a compose file"
            )
        if self.mode == "fixed" and self.file is not None:
            raise ValueError(
                "a fixed password plan must not name a `file`: `value` is the password, and a "
                "second source of truth for it is a bug waiting for a mismatch"
            )
        if self.mode == "fixed" and not self.value:
            raise ValueError("a fixed password plan needs a non-empty `value`")
        if self.mode == "generated" and not self.file:
            raise ValueError("a generated password plan needs `file`")
        return self


class DbFacts(_Strict):
    """The database the emulator runs on: image, client binary, app user, charset.

    Data because it differs per core (WotLK's `mysql:8.4` and `root`, the CMaNGOS
    entries' MariaDB and a `mangos` user) and because `apply.py`/`maintenance.py`
    spell the client binary today as a literal `mysql` — 7.9 reads it from here.
    """

    image: str = Field(min_length=1)
    client: Literal["mysql", "mariadb"]
    user: str = Field(min_length=1)
    charset: str = "utf8mb4"


class ReadyMarkers(_Strict):
    """What "the server is up" looks like in this game's logs, matched over this run's log.

    `world` is required: it is the line the `ready` stage waits for. `auth` is
    optional (None: do not wait on the auth log at all), `fatal` short-circuits
    the wait to failure. All three take the `{{TOKEN}}` grammar plus
    `REALM_HOST`, filled by the spine through `composegen.fill()`. They are
    LITERAL strings unless `regex` is true: the spine `re.escape`s the filled
    text before building `docker.ReadySpec`, so `127.0.0.1` matches only
    itself. Tortoise's alternations set `regex: true` (7.3). These are the
    literals `docker.py` ("ready...") and `native._READY_REALM_HOST` used to
    carry.
    """

    world: str = Field(min_length=1)
    auth: str | None = None
    fatal: str | None = None
    timeout_s: int = Field(
        default=1800,
        gt=0,
        description=(
            "Seconds to wait for `world` before calling the install failed. Generous on "
            "purpose: `restart_loop` already catches the server that is never coming up, so "
            "this only ever binds on one that is merely SLOW, and cutting a slow one short "
            "tells a user their working server failed. Measured on m910q 2026-09-02, WoW TBC "
            "first boot on 4 cores: container start 15:51:15, first `Avg Diff:` 16:04:28 -- "
            "793s, against the 600 the three CMaNGOS entries then carried. The server was "
            "healthy and idle 44 minutes later; the install had already reported failure. "
            "The DEFAULT was 600 too, and stayed there for a day after the measurement "
            "disproved it -- so a new entry that omitted the field inherited the number known "
            "to call a working server a failed install, and no test would have failed "
            "(review, 2026-09-02)."
        ),
    )
    restart_loop: int = Field(
        default=4,
        ge=1,
        description="RestartCount growth that means a crash loop rather than a slow start.",
    )
    regex: bool = Field(
        default=False,
        description=(
            "True: `world`/`auth`/`fatal` are regular expressions as written. False: they are "
            "literal text the spine escapes before matching."
        ),
    )


class AzerothCoreData(_Strict):
    """The AzerothCore family's own install data: the worldserver env block (A2), and the
    module confs the install writes from their `.dist` (T137)."""

    confs_from_dist: tuple[str, ...] = Field(
        default=(),
        description=(
            "Module confs the install writes as a copy of the `.dist` beside them, relative to "
            "the server dir, when the conf is not there yet; never over one that is. The "
            "image's entrypoint copies `env/ref/etc/*` into the bound-out etc folder and makes "
            "a `.conf` only for its own component, so a module's conf stays a `.dist`, the world "
            "log carries a 'Missing property' line per key and the module runs on compiled "
            "defaults (T137, measured on a fresh install 2026-09-26). The keys Yu'lon sets "
            "for such a module stay in `world_env`, which wins over the file."
        ),
    )

    @field_validator("confs_from_dist")
    @classmethod
    def _confs_are_conf_paths_inside_the_server_dir(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for file in value:
            path = PurePosixPath(file)
            if "\\" in file or path.is_absolute() or ".." in path.parts or file.endswith(".dist"):
                raise ValueError(
                    "confs_from_dist names the conf itself, as a relative POSIX path inside the "
                    f"server dir (its `.dist` is found beside it), got {file!r}"
                )
        return value

    world_env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Per-game runtime settings for the worldserver, merged over composegen's structural "
            "defaults. Data rather than Python because these are facts about ONE game that a "
            "person may reasonably want different: the playerbot population lives here, not in a "
            "module constant (style-guide §3, and an adversarial review that caught it there). "
            "PROVENANCE: WotLK carried 1600/2000, copied from the ONE proven yulon-ubuntu "
            "install where the Linux installer script wrote them, after a `docker compose "
            "config` diff on 2026-08-24 found a native install would otherwise differ from a "
            "script install. Never measured on another machine and never measured at all for "
            "RAM. Lowered to 500/500 by owner decision on 2026-08-28, and the same number went "
            "into the three WotLK scripts, TBC and Vanilla so the script and native paths still "
            "agree — the point of the 2026-08-24 diff, and the thing that went wrong when the "
            "decision sat on one branch while every installer shipped 1600/2000. Still owed an "
            "RSS reading by the first gate."
        ),
    )


MpqDepth = int | Literal["recursive"]
"""How deep under `Data/` the MPQ count looks: a `find -maxdepth` value, or everywhere.

TBC's script searched with no depth limit, Vanilla's with `-maxdepth 1`, Tortoise's with
`-maxdepth 2` — three scripts, three numbers, so it is data (roadmap 7.3)."""


class ClientSpec(_Strict):
    """What the user's client folder must look like before an install may read it.

    Refusals and warnings only — `families/clientdir.py` turns these into preflight
    checks, never into a "Continue anyway?" prompt, because the engine cannot ask.
    """

    required_file: str | None = Field(
        default=None,
        description=(
            "A file that proves the expansion, relative to the client dir (`Data/expansion.MPQ` "
            "for TBC, `Data/dbc.MPQ` for Vanilla). None disables this one rule — Tortoise's "
            "7272 client has no single defining file — while `Data/` and the MPQ count still apply."
        ),
    )
    min_mpq: int = Field(default=5, ge=1, description="Fewer MPQs than this is a WARNING.")
    mpq_depth: MpqDepth = "recursive"
    locale_mpq_required: bool = Field(
        default=False,
        description=(
            "TBC keeps its DBC data in `Data/<locale>/*.MPQ`; none at depth 2 is a warning."
        ),
    )
    near_client_warn_gb: float = Field(
        default=8.0,
        gt=0,
        description=(
            "Warn when the client's volume has less free space than this (extraction scratch)."
        ),
    )
    locales: tuple[Annotated[str, Field(pattern=r"^[A-Za-z]{4}$")], ...] = Field(
        default=(),
        description=(
            "The locale folders (`enUS`) this server's client patches are made for; a client "
            "holding none of them is refused. Empty: any locale. Centurion's patches are "
            "`patch-enUS-*` only (T179 Task 7)."
        ),
    )

    archives_any_case: bool = Field(
        default=False,
        description=(
            "Whether the steps after the check open the client's archives whatever their case "
            "(T227). True only where they do: TrinityCore's client-data stage renames them in "
            "its temporary extraction client to the names its map tools open. False (CMaNGOS, "
            "whose tools read the player's own folder by exact name): on a disk that tells "
            "cases apart, a required file found only under another case is refused, saying so."
        ),
    )

    @field_validator("mpq_depth")
    @classmethod
    def _depth_is_positive(cls, value: MpqDepth) -> MpqDepth:
        if isinstance(value, int) and value < 1:
            raise ValueError("mpq_depth must be >= 1 or 'recursive'")
        return value

    @model_validator(mode="after")
    def _named_locales_are_required(self) -> ClientSpec:
        if self.locales and not self.locale_mpq_required:
            raise ValueError(
                "a client spec naming locales needs locale_mpq_required: the locale is then a "
                "folder the client must have, and its rule reports one that cannot be read"
            )
        return self


class DockerfileSpec(_Strict):
    """Tokens the per-game `Dockerfile.tmpl` takes from data."""

    make_jobs: int = Field(
        default=2,
        ge=1,
        description=(
            "`make -j`. 2 is the scripts' number, chosen for a 16 GB Steam Deck: 2 GB per "
            "compiler job was measured on AzerothCore, and an OOM-killed gcc presents as "
            "'dies at the same % every retry'."
        ),
    )


class ExtractTool(_Strict):
    """One extractor run: its argv inside the image and what it must leave under `data/`."""

    name: str = Field(min_length=1)
    argv: tuple[str, ...] = Field(min_length=1)
    produces: dict[str, int] = Field(
        min_length=1,
        description="Directory under `data/` → minimum file count that means the tool finished.",
    )

    @field_validator("produces")
    @classmethod
    def _counts_are_positive(cls, value: dict[str, int]) -> dict[str, int]:
        for directory, count in value.items():
            if count < 1:
                raise ValueError(f"produces[{directory!r}] must be >= 1")
        return value


class RetrySpec(_Strict):
    """Re-run named tools once when the ending matches — by exit status, or by log text."""

    when_log_matches: str = Field(min_length=1)
    when_returncode_in: tuple[int, ...] = Field(
        default=(),
        description=(
            "Exit statuses that mean 'the failure this recipe is for', checked BEFORE the log "
            "pattern. Added 2026-09-03 because the log pattern alone could not fire on the "
            "failure it names: `Segmentation fault (core dumped)` is printed by a SHELL's job "
            "control, and these tools are exec'd as the container's PID 1 with no shell in "
            "between, so a crashed tool's output does not contain it. A signal death is "
            "128+N, and that number is the only thing the container reliably reports. 139 "
            "(SIGSEGV) is the one this ships: the recipe is named for a stack overflow, which "
            "is resource-dependent and so plausibly transient. 134 (SIGABRT) was in this list "
            "for a day on the theory that it is the same shape, and was removed — an abort in "
            "these tools is a failed assertion on a particular record of the client's data, and "
            "the retry re-runs the identical container over the identical data, so it cannot "
            "change the outcome. Adding it bought a second multi-minute run before the same "
            "failure. Nothing measured said an abort here is ever transient (review, "
            "2026-09-03). The log pattern is kept because a tool that prints a "
            "crash and exits non-zero on its own is a different, real case."
        ),
    )
    tools: tuple[str, ...] = Field(min_length=1)

    @field_validator("when_returncode_in")
    @classmethod
    def _real_failing_statuses(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        """1-255 only: 0 is success and a negative is a sentinel, and both must never retry.

        `_retry_matches()` already refuses `0` and `CANCELLED_RETURNCODE` before
        it looks at anything, so a recipe naming either would be dead text that
        reads as if it did something.
        """
        bad = [code for code in value if not 1 <= code <= 255]
        if bad:
            raise ValueError(
                f"when_returncode_in must be failing exit statuses (1-255), got {bad}; 0 is "
                "success and a negative is a cancel or signal sentinel"
            )
        return value


class ExtractPlan(_Strict):
    """The extraction stage as data: which image's tools, in which order, with what evidence."""

    image: str = Field(min_length=1, description="One of `NativeInstall.images`.")
    tools: tuple[ExtractTool, ...] = Field(min_length=1)
    ulimit_stack_unlimited: bool = Field(
        default=False, description="`--ulimit stack=-1`; Vanilla's vmap tools need it."
    )
    retry: RetrySpec | None = None
    stage_client: bool = Field(
        default=False,
        description=(
            "Fallback if a tool insists on writing beside the client: lay a symlink farm "
            "(`cp -rs /client /work`) on a tmpfs and run there. Still no writes into the client."
        ),
    )

    @model_validator(mode="after")
    def _retry_names_real_tools(self) -> ExtractPlan:
        if self.retry is not None:
            known = {tool.name for tool in self.tools}
            unknown = sorted(set(self.retry.tools) - known)
            if unknown:
                raise ValueError(f"retry names tools that do not exist: {unknown}")
        return self


class MmapPlan(_Strict):
    """The movement-map generator: its argv, and whether a shortfall refuses or warns."""

    argv: tuple[str, ...] = Field(min_length=1)
    min_files: int = Field(default=500, ge=1)
    required: bool = Field(
        default=True,
        description=(
            "False (Tortoise) turns a shortfall into a warning: bots need mmaps, a solo realm "
            "does not."
        ),
    )
    success_codes: tuple[int, ...] = Field(
        default=(0,),
        min_length=1,
        description=(
            "Which exit statuses mean the generator FINISHED. Not a style choice: MoveMapGen's "
            "convention is a property of each upstream tree and they disagree. CMaNGOS "
            "mangos-classic ends `return 0` (contrib/mmap/src/generator.cpp, read 2026-09-03); "
            'the Tortoise fork ends `return silent ? 1 : finish("Movemap build is complete!", '
            "1)` (tools/mmap/src/generator.cpp:352), so a complete Tortoise build exits 1. "
            "Measured, not guessed: the run that forced this wrote 58 maps and 2075 tiles "
            "(2.5 GB) on yulon-ubuntu and was then thrown away as a failure. Its other endings "
            "are -1/-2/-3, which a process reports as 255/254/253, so 1 does not overlap "
            "anything Tortoise says on the way out."
        ),
    )

    @field_validator("success_codes")
    @classmethod
    def _real_exit_statuses(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        """0-255 only, so no entry can declare a sentinel as success.

        `docker.CANCELLED_RETURNCODE` is -1 and `run_attached` spells "killed by
        signal N" as -N. Both are outside the range a process exit status can
        occupy, and both mean something a catalog entry must never be able to
        call finished -- a Stop read as success would record the stage and skip
        it forever after.
        """
        bad = [code for code in value if not 0 <= code <= 255]
        if bad:
            raise ValueError(
                f"success_codes must be process exit statuses (0-255), got {bad}; a negative "
                "value is a cancel or signal sentinel and can never mean success"
            )
        return value


class ConfPatch(_Strict):
    """One conf file's `Key = value` table; values take the `{{TOKEN}}` grammar."""

    keys: dict[str, str] = Field(min_length=1)
    match_commented: bool = Field(
        default=False,
        description=(
            "Also rewrite a `# Key = ...` line. The Vanilla `AiPlayerbot.SyncLevel*` seds relied "
            "on this; everywhere else a commented key is left alone and the value is appended."
        ),
    )
    template: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "The path under `source_dir`, inside the image, that this file is first copied "
            "from. `None` means the default `materialise()` has always used, `<name>.dist`, "
            "and every entry but Tortoise's two leaves it there. Per FILE rather than a new "
            "default, because the shape is per file: measured on `yulon-arch` 2026-09-11 "
            "(T30 Half 1, `12-image-contents.txt`), the Penqle core's image ships "
            "`etc/aiplayerbot.conf` with no `.dist` at all -- `TortoiseBots.cmake:40-46` "
            "`configure_file`s it straight to its live name -- and ships the module's own "
            "template one level down at `etc/modules/tortoise_bots.conf.dist`, beside a live "
            "`tortoise_bots.conf`. Both were an InstallerError before this field, and a "
            "changed DEFAULT would have made the other three games' images answer a question "
            "nobody had asked them."
        ),
    )

    @field_validator("template")
    @classmethod
    def _template_stays_inside_the_staged_copy(cls, value: str | None) -> str | None:
        if value is None:
            return value
        path = PurePosixPath(value)
        if "\\" in value or path.is_absolute() or ".." in path.parts:
            raise ValueError(
                f"template must be a relative POSIX path under source_dir, got {value!r}"
            )
        return value


class ConfPatchTable(_Strict):
    """Where the `.conf.dist` files come from inside the image, and how each is patched."""

    source_dir: str = Field(
        min_length=1, description="Absolute in-image dir of the `.conf.dist` files."
    )
    files: dict[str, ConfPatch] = Field(min_length=1)

    @field_validator("source_dir")
    @classmethod
    def _absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError(f"source_dir must be an absolute in-image path, got {value!r}")
        return value


class SqlPhase(_Strict):
    """One ordered step of the import: files (globs, relative to the server dir) or statements.

    Exactly one of `files`/`statements`; at most one of `into`/`into_each` (neither is a
    schema-less run, e.g. Tortoise's own `create_databases.sql`). `into_each` maps a schema
    to ITS glob, so `files` has no meaning beside it and `statements` cannot be split per db.
    Statements take the `{{TOKEN}}` grammar and are filled by `sqlplan.expand()` (A10).
    """

    name: str = Field(
        min_length=1,
        max_length=191,
        description=(
            "The phase's key in the install's `yulon_install_phase` record (T129): at most 191 "
            "characters (that column's `VARCHAR(191)`), no quote, backslash or control character "
            "(it is written into `'...'` unescaped). Refused here, when the catalog loads, and "
            "not by the writer, which runs only after a whole import and would fail it the same "
            "way on every press."
        ),
    )
    into: str | None = None
    into_each: dict[str, str] | None = None
    files: tuple[str, ...] = ()
    statements: tuple[str, ...] = ()
    notes: tuple[str, ...] = Field(
        default=(),
        description=(
            "Per-tree facts about THIS phase: what it applies, and what already applies (or "
            "fails to apply) the same files on this tree. For a phase whose reason is visible "
            "only on a running server -- `wow-tortoise`'s `character updates` exists because "
            "the fork's own updater is pointed at a different directory, which is invisible "
            "from the JSON -- this is where the measurement lives, beside the value it "
            "explains, rather than in a docstring written per FIELD while the fact is per "
            "field per GAME. Not `description`: that is the game blurb a user reads."
        ),
    )
    gzip: bool = False
    sort: Literal["natural", "name"] = "natural"
    on_error: Literal["fail", "warn"] = Field(
        default="fail",
        description=(
            "`warn` logs every failing file by name and continues — the scripts' "
            "`2>/dev/null`, made visible."
        ),
    )
    rerun_on_marked: bool = Field(
        default=False,
        description=(
            "Apply this phase to an install the import probe already reads as finished -- a "
            "marker row of any hash (`imported`), or `populated` with every schema complete -- "
            "so a phase added to a plan after somebody installed still reaches their "
            "databases. The marker rule (phase7-decisions, 'Probe') is otherwise unchanged: "
            "every phase without this flag is skipped there, and this route writes no marker "
            "and re-asks no `verify` rule, because it is not the whole import those describe. "
            "Only for a phase whose files are idempotent on their own terms -- it runs on "
            "every install press, for the life of the install. `wow-tortoise`'s `character "
            "updates` is the case that produced it: an install made before that phase existed "
            "is one honor-maintenance day from a restart loop the app has no button to fix "
            "(`.notes/gates/7.9-rerun-m910q-2026-09-09/README.md`, finding 1)."
        ),
    )
    assert_update_level: bool = Field(
        default=False,
        description=(
            "After this phase, require each target schema to carry a `required_<stem>` "
            "column naming the LAST file this phase applied to it. CMaNGOS core updates "
            "are a chain — every file's first statement renames the previous file's column "
            "— so that column is the schema's update level, and it is the only thing that "
            "separates a `warn` phase that skipped already-applied work from one that "
            "covered a broken world. Both print the same transcript (2026-09-03)."
        ),
    )
    reapply_when_changed: bool = Field(
        default=False,
        description=(
            "Offer this phase again to an install already imported when a later version of "
            "the app changes it (its `digest()` differs from the one recorded for that "
            "install) or adds it -- on the Server tab, behind a confirmation, never on its "
            "own (T129, bug-checklist §36). Only for a phase that is safe to apply over a "
            "server that already has data: a dump drops and re-creates its tables, a chain of "
            "updates re-runs work the world has moved past, and a statement that sets a value "
            "overwrites whatever a person set since. Every changed phase without this flag is "
            "named and withheld; a new install gets it. Not with `rerun_on_marked`, which "
            "already applies the phase on every press."
        ),
    )

    same_columns: tuple[tuple[str, str], ...] = Field(
        default=(),
        description=(
            "`(table, original)` pairs: after this phase, each `table` in the phase's `into` "
            "schema must have exactly `original`'s columns, in the same order: name, type, "
            "nullability, charset, collation and `extra` (`sqlplan.COLUMN_FIELDS`). Asked by "
            "the corrections press before it records the step; a step whose check fails is "
            "not recorded, and the press says which table and what to do. Not with "
            "`rerun_on_marked`, whose route does not ask it. For a `CREATE TABLE IF NOT "
            "EXISTS ... LIKE` step, which leaves a "
            "table that is already there as it is -- including one somebody made by hand that "
            "`INSERT ... SELECT *` from the original would then fail on (Codex, T159). Not in "
            "`digest()`: it governs whether the step is called landed, not what it applies. "
            "Pairs rather than a mapping because a phase is hashed, and a dict is not."
        ),
    )

    def digest(self) -> str:
        """16 hex of sha256 over what this phase APPLIES, and nothing else (T129).

        The fields that change what lands in the database: the target, the
        sources, gzip and the order. Named one by one rather than taken from
        `model_dump()`, and that is the point of it existing beside
        `SqlPlan.plan_hash()`: that hash moves with a note, a policy, a flag
        and every model field added later, so a marker holding it can say that
        something about the plan moved and never that THIS phase did. The name
        is not in it; it is the key the digest is recorded under.
        """
        applied = {
            "into": self.into,
            "into_each": self.into_each,
            "files": list(self.files),
            "statements": list(self.statements),
            "gzip": self.gzip,
            "sort": self.sort,
        }
        canonical = json.dumps(applied, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    @field_validator("name")
    @classmethod
    def _name_fits_the_record(cls, value: str) -> str:
        bad = sorted(
            {char for char in value if char in "'\\" or ord(char) < 0x20 or char == "\x7f"}
        )
        if bad:
            raise ValueError(
                f"phase name {value!r} carries {' '.join(repr(char) for char in bad)}, which the "
                "install's phase record cannot hold"
            )
        return value

    @model_validator(mode="after")
    def _one_source_one_target(self) -> SqlPhase:
        if self.reapply_when_changed and self.rerun_on_marked:
            # The re-run route applies the phase on every press already, so the
            # offer could never find it stale and the flag would be dead text.
            raise ValueError(
                f"phase {self.name!r}: `rerun_on_marked` already applies it on every press; "
                "`reapply_when_changed` would never be asked"
            )
        if self.assert_update_level and self.statements:
            # The check reads the LAST FILE this phase applied and turns its name
            # into a column. A literal statement has no name to read, so the flag
            # would be dead text on such a phase rather than a weaker check.
            raise ValueError(
                f"phase {self.name!r}: `assert_update_level` reads the name of the last file "
                "applied, so it cannot be set on a `statements` phase"
            )
        if self.same_columns and self.rerun_on_marked:
            # T11's route re-runs the step on every press and records nothing, so
            # there is no landing for the check to withhold; it is not asked there.
            raise ValueError(
                f"phase {self.name!r}: `same_columns` is asked by the corrections press only, "
                "and `rerun_on_marked` is not that press"
            )
        if self.same_columns and self.into is None:
            # The tables are named without a schema; `into` is the one they are in.
            raise ValueError(f"phase {self.name!r}: `same_columns` needs `into`")
        if self.into is not None and self.into_each is not None:
            raise ValueError(f"phase {self.name!r}: `into` and `into_each` are alternatives")
        if self.into_each is not None:
            if self.statements:
                raise ValueError(f"phase {self.name!r}: `into_each` takes globs, not `statements`")
            if self.files:
                raise ValueError(
                    f"phase {self.name!r}: `into_each` carries its own globs; drop `files`"
                )
            if not self.into_each:
                raise ValueError(f"phase {self.name!r}: `into_each` is empty")
            return self
        if bool(self.files) == bool(self.statements):
            raise ValueError(f"phase {self.name!r}: exactly one of `files` or `statements`")
        return self


class VerifyRule(_Strict):
    """A COUNT query that must reach `min` before the import marker may be written."""

    db: str = Field(min_length=1)
    query: str = Field(min_length=1)
    min: int = Field(ge=0)


class PlayerData(_Strict):
    """A table whose rows mean 'somebody's server' — the import probe refuses, never drops."""

    db: str = Field(min_length=1)
    table: str = Field(min_length=1)
    exclude_usernames: tuple[str, ...] = Field(
        default=(),
        description="Seeded accounts (ADMINISTRATOR, GAMEMASTER...) that do not count as players.",
    )


class SqlPlan(_Strict):
    """The whole import: schemas to create, ordered phases, verify rules, the marker's home."""

    create: tuple[str, ...] = Field(
        default=(),
        description=(
            "Schemas phase 0 creates with the app user and grants; empty when upstream's SQL "
            "does it."
        ),
    )
    phases: tuple[SqlPhase, ...] = Field(min_length=1)
    verify: tuple[VerifyRule, ...] = ()
    player_data: tuple[PlayerData, ...] = ()
    marker_db: str = Field(
        min_length=1, description="Where `yulon_install` (the marker table) lives."
    )

    def plan_hash(self) -> str:
        """16 hex of sha256 over the canonical JSON of this plan.

        Recorded in the marker row. Canonical (sorted keys, no whitespace, JSON mode) so a
        reordered `catalog.json` is the same plan and an edited glob is a new one; and a
        DIFFERENT hash in a marker still reads `imported` — a finished import from an older
        plan is never `partial` (phase7-decisions, "Probe").
        """
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class SourcePatch(_Strict):
    """One unified diff applied to one cloned source after `clone-sources` (`patch-sources`).

    Data, because which tree carries which defect is a fact about a pinned
    commit and not about the family: `wow-tbc` and `wow-vanilla` carry the
    doodad-name patch and `wow-tortoise` does not, and the file, the checkout
    it edits and the reason all belong beside the pin they were measured
    against. The apply itself is tolerant and platform-neutral —
    `families/patch.py` says how — and a `rev` on the source is what makes a
    patch against it a promise rather than a race with upstream's next commit.
    """

    file: str = Field(
        min_length=1,
        description="The patch, relative to catalog/installers/ (like `templates`).",
    )
    source: str = Field(
        min_length=1,
        description=(
            "The `dest` of the emulator source this patch is applied inside; must name one of "
            "the entry's own `emulator.sources`, which `CatalogEntry` checks."
        ),
    )
    reason: str = Field(
        min_length=1,
        description="One sentence the install log says when the patch is applied: what it fixes.",
    )

    @field_validator("file")
    @classmethod
    def _file_stays_inside_installers(cls, value: str) -> str:
        path = PurePosixPath(value)
        if "\\" in value or path.is_absolute() or ".." in path.parts:
            raise ValueError(
                f"file must be a relative POSIX path under catalog/installers/, got {value!r}"
            )
        return value


class CmangosData(_Strict):
    """Everything the CMaNGOS family needs that differs per game (roadmap 7.3)."""

    client: ClientSpec

    @field_validator("client")
    @classmethod
    def _archives_by_exact_name(cls, value: ClientSpec) -> ClientSpec:
        if value.archives_any_case:
            raise ValueError(
                "the CMaNGOS extraction opens the client's archives by exact name in the "
                "player's own folder, so its client spec cannot set archives_any_case (T260)"
            )
        return value

    dockerfile: DockerfileSpec
    extract: ExtractPlan
    mmaps: MmapPlan
    conf: ConfPatchTable
    sql: SqlPlan
    patches: tuple[SourcePatch, ...] = Field(
        default=(),
        description=(
            "Source patches applied after the clone, in order. Empty for an entry whose pinned "
            "trees carry no known defect this project works around (2026-09-05: Tortoise)."
        ),
    )


def _below(value: str, field: str, root: str) -> str:
    """`value` as a relative POSIX path strictly below `root`, or a refusal naming `field`.

    One rule, shared by the TrinityCore block's paths: no absolute path, no
    `..`, no backslash, and not `root` itself (`.` or empty) -- a path that
    names the whole of what it is meant to be inside is not a path into it.
    """
    path = PurePosixPath(value)
    if "\\" in value or path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"{field} must be a relative POSIX path inside {root}, got {value!r}")
    return value


_SQL_NAME = re.compile(r"^[A-Za-z0-9_]+$")
"""A database name a rename may name: `centurion/sql/import.sh:21-26` refuses anything else
(facts §2, CENTURION @ faac5fc9), and the names are substituted into SQL text."""

_CMAKE_DEFINE = re.compile(r"^-D[A-Za-z_][A-Za-z0-9_]*(:[A-Z]+)?=[A-Za-z0-9_.,/+=-]*$")
"""One `-DNAME=VALUE` (optionally `-DNAME:TYPE=VALUE`), and no character a shell or a
Dockerfile `RUN` line would read as anything but part of the value."""


class TrinityCoreDockerfile(DockerfileSpec):
    """The TrinityCore Dockerfile's tokens: `make -j` and the CMake defines.

    Data because they are facts about one tree: Centurion's realm comes up with no
    bots unless the build says `-DPLAYERBOT=ON` (`cmake/options.cmake:12` defaults
    it to 0, facts §1, CENTURION @ faac5fc9), and `-DTOOLS=ON` is what builds the
    extractors the client-data stage runs.
    """

    cmake_options: tuple[str, ...] = Field(
        min_length=1,
        description="`-DNAME=VALUE` defines passed to cmake, in order.",
    )

    @field_validator("cmake_options")
    @classmethod
    def _each_is_one_define(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        bad = [option for option in value if not _CMAKE_DEFINE.match(option)]
        if bad:
            raise ValueError(
                f"cmake_options must each be one `-DNAME=VALUE` with no shell characters, got {bad}"
            )
        return value


class TrinityCoreExtractPlan(ExtractPlan):
    """The extraction stage, plus the tree's own DBC files laid over what it extracted.

    Centurion ships its DBCs in the checkout (`centurion/dbc`, 246 files) and says
    to use them, not the extractor's (README.md:174-180, facts §3): the client's
    copies differ from the server's in places.
    """

    dbc_overlay_from: str = Field(
        min_length=1,
        description=(
            "Directory inside the checkout (relative to `TrinityCoreData.checkout`) whose files "
            "are copied over the extracted `data/dbc` after the extractors ran."
        ),
    )

    dbc_overlay_to: str = Field(
        default="dbc",
        min_length=1,
        description=(
            "The folder under `data/` -- the world server's `DataDir` -- the overlay is copied "
            "into: TrinityCore reads its DBCs from `<DataDir>/dbc/` (README.md:174-180, facts "
            "§3). Data rather than a constant in the family module because it is a folder name "
            "a CMaNGOS entry's catalog also spells."
        ),
    )

    @field_validator("dbc_overlay_from")
    @classmethod
    def _inside_the_checkout(cls, value: str) -> str:
        return _below(value, "dbc_overlay_from", "the checkout")

    client_archives: tuple[str, ...] = Field(
        min_length=1,
        description=(
            "The game archives of the player's client the temporary extraction client keeps, "
            "relative to its `Data/` folder; `{locale}` stands for a locale folder's name "
            "(`{locale}/locale-{locale}.MPQ`). Every other `.MPQ` is left out before the "
            "server's required packs are laid in: the patched extractors read every lettered "
            "and numbered patch archive they find (map_extractor System.cpp:1152-1218, facts "
            "§3), so a patch the player installed for another server, or an HD pack, would be "
            "extracted into maps this server does not expect (T179 Task 3, fix round 1)."
        ),
    )

    @field_validator("dbc_overlay_to")
    @classmethod
    def _inside_the_data_folder(cls, value: str) -> str:
        return _below(value, "dbc_overlay_to", "data/")

    @field_validator("client_archives")
    @classmethod
    def _archive_names_inside_data(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for name in value:
            _below(name, "client_archives", "the client's Data/ folder")
            if not name.casefold().endswith(".mpq"):
                raise ValueError(f"client_archives names game archives (.MPQ), got {name!r}")
            if set(name.replace("{locale}", "")) & set("{}*?[]!"):
                raise ValueError(
                    f"client_archives takes plain names and the `{{locale}}` token only, "
                    f"got {name!r}"
                )
        return value


class TrinityCoreMmaps(MmapPlan):
    """The movement-map generator, which on this family runs after the server is up."""

    background: bool = Field(
        default=True,
        description=(
            "True: `mmaps_generator` runs as a background job after `ready`, with pathfinding "
            "switched on at the next restart once it finished (owner decision 4, T179 spec). "
            "It takes hours (README.md:193-194, facts §3) and the server starts without it."
        ),
    )
    threads: Literal["half"] | Annotated[StrictInt, Field(ge=1)] = Field(
        default="half",
        description=(
            "What `{{THREADS}}` in `argv` becomes (`mmaps_generator --threads N`). `half`: half "
            "the cores of the Docker daemon that runs it (Docker Desktop's VM, not this host), at "
            "least 1 -- the generator's own default is every core (PathGenerator.cpp:343), and "
            "the world server runs beside it. A number: exactly that many."
        ),
    )


class TrinityCoreConf(ConfPatchTable):
    """The conf table, the world server's conf in it, and the file that must sit beside it."""

    world_conf: str = Field(
        default="worldserver.conf",
        description=(
            "The world server's conf, one of `files`. Its keys must set "
            "`Updates.EnableDatabases` to 0: the `.dist` ships 7 (worldserver.conf.dist:1470, "
            "facts §2), so a table that leaves the key out leaves the updater on."
        ),
    )
    playerbots_conf: str = Field(
        description=(
            "The bots' conf, one of `files`, written next to `worldserver.conf`: the worldserver "
            "loads it from that directory and nowhere else (worldserver/Main.cpp:242-250, facts "
            "§4), and a missing one leaves every bot setting at the `.dist`'s off."
        ),
    )
    from_checkout: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "File name beside `worldserver.conf` -> the checkout-relative file it is copied "
            "from when it is missing. For a conf the image does not install and the tree ships "
            "whole: Centurion's `AutoBalance.conf` (`centurion/conf/`, the live realm's, "
            "README.md:203-204), which the world server finds in its own conf's folder "
            "(AutoBalanceConfig.cpp:177-213, 761). Never patched and never one of `files`; "
            "Reset to default copies it from the checkout again."
        ),
    )

    @field_validator("playerbots_conf", "world_conf")
    @classmethod
    def _a_bare_file_name(cls, value: str, info: ValidationInfo) -> str:
        if not value or "/" in value or "\\" in value or value in (".", ".."):
            raise ValueError(
                f"{info.field_name} is a file name beside worldserver.conf, not a path: {value!r}"
            )
        return value

    @field_validator("from_checkout")
    @classmethod
    def _names_beside_the_conf_from_inside_the_checkout(
        cls, value: dict[str, str]
    ) -> dict[str, str]:
        for name, source in value.items():
            if not name or "/" in name or "\\" in name or name in (".", ".."):
                raise ValueError(
                    f"from_checkout names a file beside worldserver.conf, not a path: {name!r}"
                )
            _below(source, "from_checkout", "the checkout")
        return value

    @field_validator("files")
    @classmethod
    def _the_database_updater_stays_off(cls, value: dict[str, ConfPatch]) -> dict[str, ConfPatch]:
        """`Updates.EnableDatabases`, wherever the table names it, is `0`.

        Centurion's database is a snapshot that already contains TrinityCore's
        updates; the worldserver's updater must not replay them over it
        (README.md:213-214), and with the shipped `7` and no source tree at run
        time the worldserver shuts down (DBUpdater.cpp:215-218; facts §2).
        """
        for name, patch in value.items():
            setting = patch.keys.get("Updates.EnableDatabases")
            if setting is not None and setting.strip() != "0":
                raise ValueError(
                    f"{name}: Updates.EnableDatabases must be 0 on this family (the snapshot "
                    f"already holds the updates), got {setting!r}"
                )
        return value

    @model_validator(mode="after")
    def _the_named_confs_are_ones_the_table_writes(self) -> TrinityCoreConf:
        for field, name in (
            ("world_conf", self.world_conf),
            ("playerbots_conf", self.playerbots_conf),
        ):
            if name not in self.files:
                raise ValueError(
                    f"{field} {name!r} is not one of the conf table's files {sorted(self.files)}"
                )
        both = sorted(set(self.from_checkout) & set(self.files))
        if both:
            raise ValueError(
                f"{both} is both copied from the checkout and patched from the image's .dist; "
                "one file has one source"
            )
        if "Updates.EnableDatabases" not in self.files[self.world_conf].keys:
            raise ValueError(
                f"the world conf {self.world_conf!r} must set Updates.EnableDatabases to 0: "
                "its .dist ships 7, so leaving the key out leaves the updater on"
            )
        return self


class TrinityCoreSqlPlan(SqlPlan):
    """The import, plus the database-name renames `centurion/sql/import.sh` makes.

    The dumps' triggers and procedures name the live realm's databases
    (`legionnaireauth`, `centurionworld`); import.sh substitutes the target names
    into exactly three files before it loads them (import.sh:41-43, facts §2).
    This plan says the same: which names become which, in which files only.
    """

    renames: tuple[tuple[str, str], ...] = Field(
        default=(),
        description=(
            "`(from, to)` database names substituted in `rename_files`; `to` is one of the "
            "entry's own schemas (`CatalogEntry` checks)."
        ),
    )
    rename_files: tuple[str, ...] = Field(
        default=(),
        description=(
            "The files the renames apply to, relative to the server dir; each must be a file a "
            "phase imports. No other file is touched."
        ),
    )

    @field_validator("renames")
    @classmethod
    def _plain_names(cls, value: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        for pair in value:
            bad = [name for name in pair if not _SQL_NAME.match(name)]
            if bad:
                raise ValueError(
                    f"a rename names databases matching ^[A-Za-z0-9_]+$ only, got {bad} in {pair}"
                )
        return value

    @field_validator("rename_files")
    @classmethod
    def _inside_the_server_dir(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for file in value:
            _below(file, "rename_files", "the server dir")
        return value

    @model_validator(mode="after")
    def _every_renamed_file_is_one_a_phase_imports(self) -> TrinityCoreSqlPlan:
        globs = [glob for phase in self.phases for glob in phase.files]
        globs += [glob for phase in self.phases for glob in (phase.into_each or {}).values()]
        for file in self.rename_files:
            if not any(fnmatch.fnmatchcase(file, glob) for glob in globs):
                raise ValueError(
                    f"rename_files names {file!r}, which no phase imports; a rename there "
                    "would change nothing"
                )
        return self


class TrinityCoreUpdates(_Strict):
    """What "Update the server to latest…" does with a change to the tree's SQL snapshot (T179).

    The owner's decision 2 (T179 spec §3): the snapshot moves with the code, and an
    update compares the commit it left with the one it landed on. A changed file of
    a `reimport_phases` phase is imported again -- each is a whole-table dump that
    drops and re-creates its own table (Centurion's `world/`, facts §2). Every other
    file the import reads goes into a database holding the player's own accounts or
    characters, and a change there refuses the whole update -- unless every line it
    changed starts with one of `skip_lines`' prefixes for that file, which leaves the
    change out (the realm row is Yu'lon's). Data rather than a rule in the family
    module, because which phases are whole-table dumps is a fact about one tree.
    """

    reimport_phases: tuple[str, ...] = Field(
        min_length=1,
        description=(
            "Names of the SQL plan's phases whose changed files are imported again; each must "
            "write into the entry's world database, and each file must DROP and CREATE its own "
            "table."
        ),
    )
    layout_files: tuple[str, ...] = Field(
        default=(),
        description=(
            "Server-dir-relative files that hold a database's layout (its schema dump): a "
            "change to one is refused as a layout change, in the owner's words, naming the file."
        ),
    )
    skip_lines: dict[str, tuple[str, ...]] = Field(
        default_factory=dict,
        description=(
            "Server-dir-relative file -> line prefixes. A change to that file whose every "
            "added and removed line starts with one of them is left out instead of refused: "
            "Centurion's `auth_data.sql` row for the realm, whose address Yu'lon sets itself."
        ),
    )

    @field_validator("layout_files")
    @classmethod
    def _layout_files_inside_the_server_dir(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for file in value:
            _below(file, "layout_files", "the server dir")
        return value

    @field_validator("skip_lines")
    @classmethod
    def _skip_lines_name_files_and_prefixes(
        cls, value: dict[str, tuple[str, ...]]
    ) -> dict[str, tuple[str, ...]]:
        for file, prefixes in value.items():
            _below(file, "skip_lines", "the server dir")
            if not prefixes or any(not prefix.strip() for prefix in prefixes):
                raise ValueError(
                    f"skip_lines[{file!r}] needs at least one non-blank line prefix; an empty "
                    "one would leave out any change at all"
                )
        return value


class TrinityCoreData(_Strict):
    """Everything the TrinityCore family needs that differs per game (T179).

    Shaped after `CmangosData`, with what Centurion (a TrinityCore 3.3.5 fork)
    adds: a sparse checkout, CMake defines, a DBC overlay from the checkout, mmaps
    in the background, a `playerbots.conf` beside `worldserver.conf`, renames in
    the import, and the maps the start check requires. The facts are in
    `.notes/tickets/T179-centurion-facts.md` (CENTURION @ faac5fc9).
    """

    checkout: str = Field(
        min_length=1,
        description=(
            "The `dest` of the emulator source that is the core; every checkout-relative path "
            "below is relative to it. Must be a dest of the entry's own sources."
        ),
    )
    client: ClientSpec = Field(
        description=(
            "What the player's client folder must look like before the client-data stage makes "
            "its temporary extraction client from it (T179 Task 3), as `CmangosData.client` is "
            "for the CMaNGOS extraction."
        )
    )
    sparse_exclude: tuple[str, ...] = Field(
        default=(),
        description=(
            "Paths inside the checkout the clone leaves out. Centurion's `playerbot reference/` "
            "is never compiled (README.md:34) and is 0.96 GB of a 3.15 GB checkout (facts, "
            "repo-level)."
        ),
    )
    dockerfile: TrinityCoreDockerfile
    extract: TrinityCoreExtractPlan
    mmaps: TrinityCoreMmaps
    conf: TrinityCoreConf
    sql: TrinityCoreSqlPlan
    required_maps: tuple[Annotated[int, Field(ge=0)], ...] = Field(
        min_length=1,
        description=(
            "Map ids whose maps and vmaps must exist before the server is started: without them "
            "the worldserver exits 'Unable to load critical files' (World.cpp:1811-1823, facts "
            "§3) -- 0 and 1, and 530 with `Expansion = 2`."
        ),
    )
    updates: TrinityCoreUpdates | None = Field(
        default=None,
        description=(
            "What an update to the latest code, or a return to the tested pin, does with a "
            "change to the SQL snapshot (T179 Task 6). Required when the entry offers "
            "`update_to_latest`."
        ),
    )

    @model_validator(mode="after")
    def _the_update_rules_name_what_the_plan_imports(self) -> TrinityCoreData:
        """Every phase and file `updates` names is one this plan has and imports.

        A misspelt phase would re-import nothing and say so nowhere; a misspelt
        file would turn a refusal or a skip into the refusal for any change.
        """
        if self.updates is None:
            return self
        names = {phase.name for phase in self.sql.phases}
        unknown = [name for name in self.updates.reimport_phases if name not in names]
        if unknown:
            raise ValueError(
                f"updates.reimport_phases names {unknown}, which the SQL plan does not have "
                f"(its phases: {sorted(names)})"
            )
        globs = [glob for phase in self.sql.phases for glob in phase.files]
        globs += [glob for phase in self.sql.phases for glob in (phase.into_each or {}).values()]
        for field_name, files in (
            ("layout_files", self.updates.layout_files),
            ("skip_lines", tuple(self.updates.skip_lines)),
        ):
            for file in files:
                if not any(fnmatch.fnmatchcase(file, glob) for glob in globs):
                    raise ValueError(
                        f"updates.{field_name} names {file!r}, which no phase imports; a rule "
                        "for it would never apply"
                    )
        return self

    @field_validator("checkout")
    @classmethod
    def _checkout_inside_the_server_dir(cls, value: str) -> str:
        """A plain folder strictly inside the server dir, and nothing a build file reads as syntax.

        The checkout is spliced into the generated `.dockerignore` (`!{{CHECKOUT}}`) and
        the Dockerfile's `COPY ["{{CHECKOUT}}", ...]` (Task 2). There `.` or an empty
        name re-includes the whole server folder -- `.db_password`, `.env` and the confs
        -- into the build context; `* ? [ ]` match other paths and `!` negates;
        a leading `#` turns the line into a comment; and `"` or a backslash breaks out
        of the JSON-form `COPY`. Refused rather than escaped: no TrinityCore tree has a
        reason to be cloned under such a name.
        """
        path = PurePosixPath(value)
        if "\\" in value or path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError(
                f"checkout must be a relative POSIX path inside the server dir, got {value!r}"
            )
        special = sorted(set(value) & set('*?[]!"'))
        if special or value.startswith("#"):
            raise ValueError(
                f"checkout {value!r} holds {special or ['#']}, which the build's .dockerignore "
                "or COPY line reads as syntax; name a plain folder"
            )
        return value

    @field_validator("sparse_exclude")
    @classmethod
    def _exclusions_inside_the_checkout(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Plain paths inside the checkout, never git pattern syntax.

        Git reads a sparse-checkout line as a pattern: `!` negates, `#` starts a
        comment, `*`, `?` and `[` match. A path holding one would exclude
        something other than itself, so it is refused rather than escaped. A
        space is a plain character there and stays allowed: Centurion's own
        `playerbot reference` has one.
        """
        for path in value:
            _below(path, "sparse_exclude", "the checkout")
            special = sorted(set(path) & set("!#*?["))
            if special:
                raise ValueError(
                    f"sparse_exclude holds a git pattern character {special} in {path!r}; "
                    "name plain paths only"
                )
        return value


class NativeInstall(_Strict):
    """What the native install engine needs that is a fact about THIS game (roadmap 6.2, 7.1).

    Floors are here rather than in `preflight.py` because a different game
    compiles at a different cost: AzerothCore's numbers are not a rule about
    servers, they are a measurement of one. **Every default below is inherited
    from the earlier Rust launcher's incidents (`pyplan/rust-prior-art.md` §3)
    and none of them was measured by this project** — the first live gates
    record the real peak RAM and the real disk growth and replace them.
    """

    templates: str = Field(
        min_length=1,
        description=(
            "Directory of this game's compose templates, relative to catalog/installers/."
        ),
    )
    family: Literal["azerothcore", "cmangos", "trinitycore"] = Field(
        description=(
            "Which family engine installs this game; must equal the engine's `family`. "
            "This Literal is the first file a new lineage's data touches, so the policy "
            "is stated here too: a family with no registered engine is a DEFECT and not "
            "a supported window — `installer_for()` refuses the entry rather than "
            "falling back to anything. A new lineage is a class in `catalog/families/`, "
            "a line in `FAMILIES`, and then a member here. `trinitycore` was the exception: "
            "added with its model and `families/decisions.py` (T179 Task 1) ahead of its "
            "engine (Task 3) and its first entry (Task 7)."
        )
    )
    images: tuple[str, ...] = Field(
        min_length=1,
        description=(
            "The image suffixes the build overlay produces, in the base file's spelling. The "
            "prefix, the tag and these together are the only description of what a finished "
            "build leaves behind, and `docker.images_built()` asks about them by name — see "
            "`composegen.built_image_refs()`."
        ),
    )
    image_prefix: str = Field(
        min_length=1,
        description=(
            "Where images this machine BUILDS are named. Deliberately not upstream's `acore/`: "
            "a build tags whatever the base file's `image:` says, so reusing an upstream ref "
            "would clobber a pulled image and let a later `docker compose pull` silently "
            "replace this playerbots build with upstream's vanilla worldserver. The first "
            "component contains a dot, so Docker reads it as a registry HOST and can never "
            "resolve it to somebody else's Docker Hub repo — a stale-image mistake then fails "
            "loudly at pull time instead of booting a plausible-looking wrong server."
        ),
    )
    dockerfile_dir: str | None = Field(
        default=None,
        description=(
            "Directory of a Dockerfile.tmpl/dockerignore.tmpl pair, relative to "
            "catalog/installers/ (CMaNGOS, 7.3). None: the checkout ships its own Dockerfile."
        ),
    )
    update_to_latest: bool = Field(
        default=False,
        description=(
            # T155: it said "the Server tab's", and the control has never been
            # there; since T89 it is an entry of the Modules tab's menu.
            "Offer "
            f"{server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)} "
            "for this game (T64). Data rather than a rule about families, because what the "
            "control needs is per ENTRY and is not derivable from anything else here: every "
            "source it would move must be one this app cloned and pins, its upstream must be a "
            "repository a rebuild of this entry can compile, and any patch the entry carries "
            "must be one somebody is prepared to see refused when upstream moves under it. "
            "Default false, so a new entry gets the control by somebody deciding it does — "
            "the safe direction for a button whose whole subject is untested code."
        ),
    )
    db: DbFacts
    ready: ReadyMarkers
    stop_waits_for_world_load: bool = Field(
        default=False,
        description=(
            "True: this game's world server cannot hear a stop while it loads, so every stop "
            "first waits until it can (T158, `docker.wait_for_the_world_to_load()`). A fact "
            "about the server binary, not the family, so it is data. CMaNGOS's mangosd "
            '(Vanilla, TBC, Tortoise) is the container\'s PID 1 (`CMD ["./mangosd"]`, no '
            "init) and calls `_HookSignals()` only after `SetInitialWorldSettings()`, and Linux "
            "drops a signal a namespace's init has no handler for. Measured on Vanilla, "
            "2026-09-27: SigCgt `0000000100000000` throughout the load, `0000000100004002` "
            "once loaded; a SIGTERM 15 s into the load was ignored and the world was SIGKILLed "
            "280.5 s later, exit 137, while a loaded one stopped in 22.2 s, exit 0. "
            "AzerothCore's worldserver installs its SIGINT/SIGTERM `signal_set` before it "
            "loads, so WotLK leaves this false."
        ),
    )
    azerothcore: AzerothCoreData | None = Field(
        default=None, description="Present exactly when `family` is azerothcore (7.3 validates)."
    )
    cmangos: CmangosData | None = None
    trinitycore: TrinityCoreData | None = Field(
        default=None, description="Present exactly when `family` is trinitycore (T179)."
    )
    soap_port: int = Field(default=7878, gt=0, lt=65536)
    min_ram_gb: float = Field(
        default=6.0,
        gt=0,
        description=(
            "Below this the build is refused: 2 GB per compiler job was measured, and under 6 GB "
            "the OOM killer SIGKILLs a compiler — the symptom being 'dies at the same low % "
            "every retry' with a bare `Killed`."
        ),
    )
    warn_ram_gb: float = Field(default=8.0, gt=0)
    min_data_root_gb: float = Field(
        default=40.0,
        gt=0,
        description="Free space the Docker data root needs; images and build cache live there.",
    )
    warn_data_root_gb: float = Field(default=60.0, gt=0)
    min_server_dir_gb: float = Field(
        default=8.0,
        gt=0,
        description="The checkout is 2.4 GB but the clone PEAKS near 3.7 GB.",
    )
    warn_server_dir_gb: float = Field(default=15.0, gt=0)

    def floors_gb(self, *, same_volume: bool) -> tuple[float, float]:
        """(refuse, warn) free-space floors when both needs land on one volume.

        They ADD rather than max out: the build cache and the checkout both
        grow, at the same time, out of the same free space.
        """
        if not same_volume:
            raise ValueError("floors_gb() is for the one-volume case; ask for each floor directly")
        return (
            self.min_data_root_gb + self.min_server_dir_gb,
            self.warn_data_root_gb + self.warn_server_dir_gb,
        )

    @model_validator(mode="after")
    def _exactly_the_family_block(self) -> NativeInstall:
        """`family` names exactly the typed block that is present — no more, no fewer.

        A `cmangos` block on an `azerothcore` entry is a typo that would otherwise be data
        nobody reads; a missing block is an engine that starts and fails at stage two.
        Also pins `extract.image` to a built image, so the extractors run from something
        the build overlay produces.
        """
        blocks = {
            "azerothcore": self.azerothcore,
            "cmangos": self.cmangos,
            "trinitycore": self.trinitycore,
        }
        present = sorted(name for name, block in blocks.items() if block is not None)
        if present != [self.family]:
            raise ValueError(
                f"family is {self.family!r} but the blocks present are {present}; "
                f"exactly the `{self.family}` block must be present"
            )
        extract_images = {
            "cmangos": self.cmangos.extract.image if self.cmangos is not None else None,
            "trinitycore": self.trinitycore.extract.image if self.trinitycore is not None else None,
        }
        for family, image in extract_images.items():
            if image is not None and image not in self.images:
                raise ValueError(
                    f"{family}.extract.image {image!r} is not one of images {list(self.images)}"
                )
        return self


class Install(_Strict):
    """How this game is installed: through the family engine its `native` block names.

    One list, not two. Until 7.2 there were two mechanisms — a bash script per
    platform and per package manager, and the engine — so `platforms` said
    where the entry could be installed at all while `script_platforms` said
    which of those the script owned. 7.2 deleted the scripts, and with a single
    mechanism left the second question has no content: `platforms` drives the
    6.1 refusal and the tile's disabled button, and `native.family` picks the
    engine. See `installer.installer_for()`, which is the one place that
    decides, and `pyplan/phase7-decisions.md`.
    """

    default_server_dir: str = Field(min_length=1, description="Default dir name under $HOME")
    password: PasswordPlan = Field(
        description="Where the database root password comes from: fixed, or generated per install."
    )
    requires_client_dir: bool = Field(
        default=False,
        description=(
            "The user's own client folder is required before the install can start (README "
            "§3a); the view asks for it and the engine's preflight refuses without it."
        ),
    )

    def db_password(self, server_dir: Path) -> str | None:
        """The database root password for an install at `server_dir`, if knowable.

        Not every game has a fixed one: the TBC and Vanilla installers GENERATE
        a password (`tbc$(openssl rand -hex 8)`) and write it to a file under the
        server dir, which is what a `generated` plan's `file` names. That fact was
        declared here and read NOWHERE, so every caller fell back to the shared
        default and authenticated as root with the literal string "password".
        Start and Stop need no database, which is why it would have surfaced
        later - on Create account, Backup and Restore.

        Returns None when the entry names a file that cannot be read. That is
        deliberately not the same answer as the default: it means this install's
        password is not knowable from here, and a caller should decide what to
        do about that rather than be handed a guess.
        """
        if self.password.mode == "fixed":
            return self.password.value
        # `PasswordPlan` refuses a generated plan with no `file`, so the fallthrough
        # below is unreachable through the catalog - but `file` is still typed
        # optional, and guessing a filename here would be worse than saying "unknown".
        if self.password.file:
            try:
                text = (server_dir / self.password.file).read_text(encoding="utf-8")
            except (OSError, ValueError):
                # ValueError covers UnicodeDecodeError, which is what a file
                # written in another encoding raises - it is not an OSError, so
                # it used to escape this handler and crash the caller rather
                # than being reported as "not knowable from here".
                return None
            return text.strip() or None
        return None

    platforms: tuple[PlatformId, ...] = Field(
        default=("linux",),
        min_length=1,
        description=(
            "Which platforms this entry can be installed on. Data, not a Python conditional "
            "(roadmap 6.1): an off-list click is refused with an honest message rather than "
            "starting an install that cannot finish, and the tile disables its button from the "
            "same list. `min_length=1` because an entry installable nowhere would ship a dead "
            "button; every shipped entry has an engine (7.3), so that state is now a mistake "
            "and not a configuration."
        ),
    )
    native: NativeInstall | None = Field(
        default=None,
        description=(
            "Floors, templates and the family for the engine that installs this entry. "
            "`installer_for()` refuses to build an engine without it rather than inventing a "
            "family, so any entry with a non-empty `platforms` needs one."
        ),
    )

    def supports(self, platform_id: str) -> bool:
        """True if this entry can be installed on `platform_id` (`platform.detect()`) at all."""
        return platform_id in self.platforms


class Containers(_Strict):
    """The three container names the controller manages, their services, and the import job."""

    db: str = Field(min_length=1)
    auth: str = Field(min_length=1)
    world: str = Field(min_length=1)
    services: tuple[str, str, str] | None = Field(
        default=None,
        description=(
            "Compose SERVICE names for db/auth/world, in that order. `docker compose up` "
            "takes services, and a container name it does not know fails outright with "
            "`no such service` (Discord report, 2026-08-26). Refused since 2026-09-04 by "
            "`composegen._container_prefix()`, whatever the value, for any entry with an "
            "`install.native` block — and every shipped entry has one (bug-checklist §30): the "
            "generated compose file takes its service keys from the templates "
            "({{CONTAINER_PREFIX}}db/-realmd/-mangosd in shared/cmangos/base.yml.tmpl, the "
            "literal ac-database and friends in wow-wotlk/native/base.yml.tmpl), so the entry "
            "has nothing to declare and the only correct state of this field is absent. The "
            "entry still loads; `composegen.render()` refuses it, so `write_plan()` never gets "
            "a plan to write. "
            "Saying db/realmd/mangosd here, as the three CMaNGOS entries did until 2026-09-01, "
            "named the bash installers' services and would have failed every generated install "
            "at the first `compose up` — and the rule that accepted it then was satisfiable by "
            "that mistake alone. The field stays on the model for `docker.ContainerSpec.services`, "
            "which an adopted project whose services and containers differ really does need."
        ),
    )
    db_import: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Compose SERVICE (not container) that populates the databases, e.g. ac-db-import. "
            "Only `docker.repair_import()` may select it; leaving it out means this game offers "
            "no repair action, which is the right answer for a core whose import is not a "
            "separate one-shot service."
        ),
    )
    client_data: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Compose SERVICE that fetches this game's server-side map/DBC data, e.g. "
            "ac-client-data-init. Named here for the same reason as `db_import`: the native "
            "engine runs it as its own stage and must not guess a service name. Not the "
            "player's client — the app never ships or fetches that (README §3a)."
        ),
    )


class Ports(_Strict):
    """The port table (README §13): auth/realm, world, and the (optional) published DB."""

    auth: int = Field(gt=0, lt=65536)
    world: int = Field(gt=0, lt=65536)
    db: int | None = Field(default=None, gt=0, lt=65536)


class Databases(_Strict):
    """Schema names for the emulator's databases (differ per core)."""

    auth: str = Field(min_length=1)
    characters: str = Field(min_length=1)
    world: str = Field(min_length=1)
    extra: tuple[str, ...] = ()
    playerbots: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "The playerbots schema, for the cores that keep one. Named separately from `extra` "
            "because the applier addresses it by the manifest key `playerbots`, and a name in a "
            "list cannot be looked up by key."
        ),
    )
    ale: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "The ALE (Paragon) schema, same reasoning as `playerbots`. Created by the module "
            "rather than by the installer, so it is a name this core WOULD use, not a promise "
            "the schema exists."
        ),
    )

    def schema_map(self) -> dict[Db, str]:
        """Manifest `db` key → this core's schema name, for `apply.DockerSql`.

        Only the databases this core actually names appear. A key that is absent
        is a database this game does not have, and `DockerSql` refuses it by
        name rather than connecting to somebody else's schema — which is exactly
        the failure this map exists to end: every SQL-backed control used to
        address AzerothCore's `acore_auth` on a CMaNGOS install and die with
        `ERROR 1049 Unknown database` (Discord report, 2026-08-26).
        """
        named: dict[Db, str] = {
            "auth": self.auth,
            "characters": self.characters,
            "world": self.world,
        }
        if self.playerbots:
            named["playerbots"] = self.playerbots
        if self.ale:
            named["ale"] = self.ale
        return named


class Realmlist(_Strict):
    """Where the realm's advertised address lives in the auth DB (README §13 updater)."""

    table: str = "realmlist"
    address_column: str = "address"
    local_address_column: str | None = Field(
        default="localAddress", description="None for cores whose realmlist has no LAN column."
    )
    realm_id: int = 1


class AccountLevel(_Strict):
    """Where THIS core keeps an account's GM level (8.3a).

    Two shapes, and reading the wrong one does not fail: it reports every
    account as level 0, which is a lie shaped like an answer. AzerothCore keeps
    it in a join table (`account_access`, keyed `id`, no realm filter, which is
    how SOAP itself reads it); the CMaNGOS trees keep it on the account row,
    under `gmlevel` on CMaNGOS proper and `rank` on tortoise.

    Absent until that tree's own box measures it, like `observability` and for
    the same reason: an inherited block is a guess wearing the shape of a fact.
    """

    table: str | None = Field(
        default=None,
        description=(
            "The join table holding the level, or null when the level is a column on the "
            "account row itself."
        ),
    )
    account_column: str = Field(
        default="id",
        min_length=1,
        description=(
            "The column in `table` that carries the account id. Ignored when table is null."
        ),
    )
    level_column: str = Field(
        default="gmlevel", min_length=1, description="The column holding the level itself."
    )
    max_level: int = Field(
        default=3,
        ge=1,
        le=9,
        description=(
            "The highest level this core's own command accepts, measured by asking it. Three of "
            "the four trees stop at 3 (`SEC_ADMINISTRATOR`, and Vanilla's own help says `#level "
            "may range from 0 to 3`); the tortoise fork accepts 4, because its check grants at "
            "the caller's own level rather than strictly below it. A surface that draws the "
            "wrong ceiling here does not fail -- it silently offers less than the tree has."
        ),
    )


class Accounts(_Strict):
    """Whether this app can create an account on this core by writing the row itself.

    Three shapes, one per core family. AzerothCore uses `salt`/`verifier` with
    the level in `account_access`. Tortoise uses `sha_pass_hash` with the level
    in `account.rank` — measured against a live server on 2026-08-26, where the
    core logged its own INSERT and `SHA1(UPPER(user):UPPER(pass))` matched it
    exactly. CMaNGOS proper (TBC, Vanilla) keeps SRP6 in `v`/`s` with the level
    in `gmlevel`, a THIRD shape -- measured on 2026-09-07 against both trees with
    real clients (8.3b, 8.3c): `x = SHA1(reverse(s) + SHA1(UPPER(user:pass)))`
    little-endian, `v = 7^x mod N`, recomputed by hand and matching the row.
    This paragraph said "has not been measured, so it is declared unsupported"
    for a day after it was (audit, 2026-09-08).

    Getting this wrong does not fail loudly — it inserts a row that looks
    correct and can never log in.
    """

    scheme: Literal["azerothcore", "mangos_sha", "mangos_srp6", "trinitycore"] | None = Field(
        default="azerothcore",
        description=(
            "How this core stores an account: `azerothcore` is SRP6 in binary salt/verifier "
            "with the level in account_access; `mangos_sha` is sha_pass_hash with the level "
            "in account.rank; `mangos_srp6` is the same SRP6 as AzerothCore stored as hex "
            "text in v/s with the level in account.gmlevel; `trinitycore` is TrinityCore's "
            "salt/verifier with the level in account_access(AccountID, SecurityLevel, RealmID) "
            "with RealmID -1 (T179). None means this app does not "
            "write accounts for this core and "
            "the Accounts tab points at `console_command` instead. Never defaulted onto a "
            "core that has not been measured — a wrong scheme inserts a row that looks "
            "correct and can never log in."
        ),
    )
    console_command: str = Field(
        default="account create <name> <password>",
        min_length=1,
        description="What to type on the worldserver console when `by_sql` is False.",
    )
    level: AccountLevel | None = Field(
        default=None,
        description=(
            "Where this core keeps an account's GM level, for the account list (8.3a). Absent "
            "until that tree's own box measures it: reading the wrong store reports every "
            "account as level 0 rather than failing."
        ),
    )


class Equipped(_Strict):
    """Where THIS tree keeps the item id of a thing a character is wearing.

    Two shapes: AzerothCore's `character_inventory` row carries the item
    INSTANCE guid (`item`) and nothing else, so the template id is one join away
    in `item_instance.itemEntry`; the CMaNGOS rows carry both that guid and
    `item_template` (measured on the TBC install 2026-09-06 and on the Vanilla
    one 2026-09-07), so the flat read is one hop.

    Corrected in 8.4c: the shape that answers "instance guids that look exactly
    like item ids" is not either of those swapped over -- the flat shape on
    AzerothCore is an `Unknown column` error, and the joined shape on CMaNGOS is
    the same answer by a longer road. It is `template_column` set to `item`,
    which is what carrying AzerothCore's column name onto CMaNGOS's flat shape
    produces, and it is the reason this is three fields rather than a default.
    """

    template_column: str = Field(
        min_length=1,
        description=(
            "The column holding the item's template id -- on `item_instance` where "
            "`instance_table` is set, and on the inventory row where it is null."
        ),
    )
    instance_table: str | None = Field(
        default=None,
        description=(
            "The table to join for the template id, or null where the inventory row "
            "already carries it."
        ),
    )
    inventory_column: str = Field(
        default="item",
        min_length=1,
        description="The inventory column that keys the join. Ignored without instance_table.",
    )


class Play(_Strict):
    """What the Characters tab may offer on this tree (8.4a).

    Absent until that tree's own box measures it, like `observability` and
    `accounts.level`, and for the same reason: an inherited block is a guess
    wearing the shape of a fact.
    """

    equipped: Equipped = Field(description="How to read what a character is wearing.")
    teleport_command: str = Field(
        min_length=1,
        description=(
            "This tree's named-teleport verb, measured by asking it: `teleport name` on "
            "AzerothCore, `tele name` on the CMaNGOS trees -- where `teleport` is not a "
            "command at all and the refusal arrives as a closed connection."
        ),
    )
    mail_item_cap: int = Field(
        ge=1,
        description=(
            "How many attachments this tree carries in ONE mail. Twelve on AzerothCore and "
            "CMaNGOS TBC; 8.4c's tree takes exactly one, so a set of gear is that many mails "
            "and the button has to say so before the press."
        ),
    )
    revive_offline: bool | None = Field(
        default=None,
        description=(
            "Whether `revive` does anything to a character who is NOT logged in, or null "
            "where nobody has measured it here -- and the button is offered only on a True. "
            "8.4a and 8.4b both read `characters.health` before and after, saw 0 and 0, and "
            "concluded the command does nothing offline; 8.4c measured the CORPSE instead "
            "and watched it go, on a character that never logged in. The offline branch is "
            "`ConvertCorpseForPlayer` -- 'will resurrected at login without corpse' -- so "
            "health is exactly the column it does not touch. Null on the trees whose own "
            "boxes have not looked again."
        ),
    )
    rename_command: str = Field(
        min_length=1,
        description=(
            "This tree's verb for flagging a rename at the next login. `character rename` on "
            "three of these trees; the tortoise fork registers `rename` at the TOP level "
            "(`Chat.cpp:850`) and its `characterCommandTable` has no rename row at all, so "
            "the sibling spelling arrives there as an unknown SUBcommand and answers with a "
            "list of the subcommands it does have."
        ),
    )
    rename_offline_refusal: str | None = Field(
        default=None,
        description=(
            "What to say instead of offering the at-login rename to a character who is NOT "
            "logged in, or null where this tree flags an offline character the same way it "
            "flags a live one. A sentence rather than a boolean because the trees do not "
            "merely differ in whether it WORKS: on the tortoise fork the same spelling runs "
            "`UPDATE characters SET name = guid` for an offline character "
            "(`Commands.cpp:12624-12635`), which does not flag a rename -- it throws the name "
            "away. That is data loss behind a button, and the sentence says what to do "
            "instead."
        ),
    )
    set_level_command: str | None = Field(
        description=(
            "This tree's verb for putting a character at a level somebody picks -- "
            "`character level` on three of these trees -- or NULL where the console has no "
            "route to one. Required rather than defaulted, because a default is exactly how "
            "a null turns back into a sibling's string on the way to the wire. The tortoise "
            "fork is the null: the only command that writes an arbitrary level is `.levelup`, "
            "whose table row sets `AllowConsole` false (`Chat.cpp:923`), and `CliHandler::"
            "isAvailable` refuses on that field before it looks at security at all -- a "
            "refusal the `command` DB table cannot override, since that table carries "
            "SecurityLevel and Help and nothing else (`Chat.cpp:1730-1770`)."
        ),
    )
    set_level_absent_reason: str | None = Field(
        default=None,
        description=(
            "The sentence the Characters tab draws where the set-level control would be, on a "
            "tree that has no such command. Required exactly where `set_level_command` is "
            "null, and forbidden where it is not -- 8.4d's own clause is that the group is "
            "replaced by a sentence NAMING WHAT DOES EXIST rather than one implying nothing "
            "does, so 'not supported' would satisfy the shape and miss the point. It lives in "
            "the catalog beside the measurement it comes from rather than in the view, "
            "because it is a fact about a server and not a piece of English about a button."
        ),
    )

    @model_validator(mode="after")
    def _an_absent_level_command_says_what_this_tree_has_instead(self) -> Play:
        """The two fields are one fact and neither can hold it alone.

        A null command with no sentence draws an empty space where a group was,
        which is the outcome 8.4d exists to prevent; a sentence beside a command
        that works is a sentence nothing would ever draw, so it would rot
        unread. Asserted here because the relationship has no other owner --
        the view reads both and the catalog file writes both, and neither can
        see the other (`defects live between the parts`).
        """
        if (self.set_level_command is None) != bool(self.set_level_absent_reason):
            raise ValueError(
                "set_level_absent_reason is required exactly where set_level_command is null: "
                f"command={self.set_level_command!r}, reason={self.set_level_absent_reason!r}"
            )
        return self


class Console(_Strict):
    """How this core's worldserver console delimits the answer to a command.

    Two facts, because the string alone is not enough. AzerothCore reads its
    console with GNU readline, which redisplays the prompt in FRONT of what it
    is about to print; CMaNGOS and tortoise read with `fgets` and print theirs
    only after the command finished. Same delimiter, the answer on opposite
    sides of it — so a core that declared only the string would have every reply
    parsed as empty (research, 2026-08-26).
    """

    prompt: str = Field(
        default="AC>",
        min_length=1,
        description="What the console prints when it is ready for the next command.",
    )
    prompt_precedes_answer: bool = Field(
        default=True,
        description="True for a readline console (AzerothCore), False for an `fgets` one.",
    )


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Md5 = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
# A Config.wtf line is `SET key "value"`: a key is one word, and a value may not
# hold the quote or the line break that would end it and start a line of its own.
WtfKey = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")]
WtfValue = Annotated[str, Field(pattern=r'^[^"\r\n]*$')]


def _https_url(value: str, field: str) -> str:
    """Refuse anything but an https URL with a host and no credentials in it.

    Every URL in the client section is something Yu'lon downloads from, or
    sends a person to, without asking. Plain http would let anyone on the path
    swap a 1.4 GB MPQ or a Wow.exe; a user:password@ part would put a secret in
    a catalog that is public and in every log line that names the URL.
    """
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError(f"{field} must be an https URL with a host, got {value!r}")
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"{field} must not carry credentials, got {value!r}")
    return value


def _inside(value: str, field: str, *, names_a_file: bool) -> str:
    """Refuse a path that could leave the folder it is relative to.

    The rule the other relative paths in this file follow, plus `:`: these
    paths land in a WoW client folder, which is a Windows folder for most
    players, and `C:/Windows/x` is relative to PurePosixPath and absolute to
    Windows.
    """
    path = PurePosixPath(value)
    if "\\" in value or ":" in value or path.is_absolute() or ".." in path.parts:
        raise ValueError(
            f"{field} must be a relative POSIX path that stays inside its folder, got {value!r}"
        )
    if names_a_file and not path.parts:
        raise ValueError(f"{field} must name a file, got {value!r}")
    return value


class PackSource(_Strict):
    """Where one client pack's zip comes from: the server's own checkout, or a URL.

    One shape for both so a pack is one list entry either way; `kind` says
    which half is filled, and the validator holds the two halves apart so a
    pack can never be ambiguous about which of two places it trusts.
    """

    kind: Literal["checkout", "url"]
    path: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "The zip, relative to the server dir (`src/<core>/centurion/patches/patch-Y.zip`). "
            "Where the plain file is absent, its `<path>.partNN` files are joined in name order."
        ),
    )
    url: str | None = Field(default=None, description="The zip's https URL.")
    version_url: str | None = Field(
        default=None,
        description=(
            "An https URL whose body is the pack's current version string, so a changed pack "
            "is noticed without downloading it again. URL packs only: a checkout pack's "
            "version is the commit it was checked out at."
        ),
    )

    @field_validator("path")
    @classmethod
    def _path_stays_inside_the_server_dir(cls, value: str | None) -> str | None:
        return value if value is None else _inside(value, "source.path", names_a_file=True)

    @field_validator("url", "version_url")
    @classmethod
    def _urls_are_https(cls, value: str | None) -> str | None:
        return value if value is None else _https_url(value, "source url")

    @model_validator(mode="after")
    def _kind_matches_the_half_that_is_filled(self) -> PackSource:
        if self.kind == "checkout" and (
            self.path is None or self.url is not None or self.version_url is not None
        ):
            raise ValueError("a checkout source names a path, and no url or version_url")
        if self.kind == "url" and (self.url is None or self.path is not None):
            raise ValueError("a url source names a url, and no path")
        return self


class InstallRule(_Strict):
    """One zip member and where it goes in the ready-to-play client.

    A named member goes to a named file, which is how `patch-Y.MPQ` becomes
    `Data/patch-X.MPQ`; `"*"` unpacks the whole zip under a folder, which is how
    21 addon folders reach `Interface/AddOns/`. Exactly one target, and the
    target must suit the member: a named member with only a folder would leave
    the engine guessing the file name, and `"*"` with a file name cannot fit.
    """

    member: str = Field(
        min_length=1, description='A member name inside the zip, or "*" for all of them.'
    )
    to: str | None = Field(
        default=None, description="The target file, relative to the client folder."
    )
    to_dir: str | None = Field(
        default=None,
        min_length=1,
        description=(
            'The target folder for "*", relative to the client folder; "." is the folder itself.'
        ),
    )

    @field_validator("member")
    @classmethod
    def _member_is_star_or_a_relative_name(cls, value: str) -> str:
        return value if value == "*" else _inside(value, "install.member", names_a_file=True)

    @field_validator("to")
    @classmethod
    def _to_stays_inside_the_client(cls, value: str | None) -> str | None:
        return value if value is None else _inside(value, "install.to", names_a_file=True)

    @field_validator("to_dir")
    @classmethod
    def _to_dir_stays_inside_the_client(cls, value: str | None) -> str | None:
        return value if value is None else _inside(value, "install.to_dir", names_a_file=False)

    @model_validator(mode="after")
    def _the_target_suits_the_member(self) -> InstallRule:
        if self.member == "*":
            if self.to_dir is None or self.to is not None:
                raise ValueError(f'member "*" installs into to_dir, and names no to: {self!r}')
        elif self.to is None or self.to_dir is not None:
            raise ValueError(
                f"a named member installs to a file: it takes to, and no to_dir: {self!r}"
            )
        return self


class ClientPack(_Strict):
    """One zip of files a server's ready-to-play client needs, or may have (T181 b).

    A required pack (`optional` false) is installed on every ready-to-play
    client of this server; an optional one only when the player switched it
    on, and `default` is its state before they choose. A checkout pack must
    carry a checksum, because the server's own repo publishes one
    (`patches.md5`) and a joined set of parts is exactly where a missing piece
    goes unnoticed; a URL pack may have none, since the server's site may
    publish none, and then size, zip CRC and the recorded version stand in.

    A checkout pack's checksum is pinned here (`sha256`/`md5`) or read from a
    file of the checkout itself (`md5_file`, T179 Task 7, the lead's ruling):
    a pinned one would refuse every pack the server's makers update, and
    "Update to latest" is meant to bring their new patches along. Reading it
    from their repo moves the trust from Yu'lon's pin to that repo -- the same
    trust as the server code built from it.
    """

    id: Slug
    label: str = Field(min_length=1)
    description: str = ""
    source: PackSource
    sha256: Sha256 | None = None
    md5: Md5 | None = None
    md5_file: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Checkout packs only: a file of `md5sum` lines, relative to the server dir like "
            "`source.path` (`src/centurion/centurion/patches/patches.md5`), whose line for this "
            "zip -- named relative to the file's own folder -- is the md5 the zip must have, "
            "read at the commit the checkout is on."
        ),
    )
    install: tuple[InstallRule, ...] = Field(min_length=1)
    remove_when_off: tuple[str, ...] = Field(
        default=(),
        description=(
            "Files, relative to the client folder, deleted when the pack is off -- beyond the "
            "ones it recorded installing. Centurion's launcher deletes `Data/patch-Y.MPQ` when "
            "world-terrain is off, whoever put it there."
        ),
    )
    optional: bool = False
    default: bool = Field(default=False, description="An optional pack's state before a choice.")
    size_hint: int | None = Field(
        default=None, ge=0, description="Bytes, for the download size the dialog shows."
    )

    @field_validator("remove_when_off")
    @classmethod
    def _removals_stay_inside_the_client(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _inside(item, "remove_when_off", names_a_file=True)
        return value

    @field_validator("md5_file")
    @classmethod
    def _md5_file_stays_inside_the_server_dir(cls, value: str | None) -> str | None:
        return value if value is None else _inside(value, "md5_file", names_a_file=True)

    @model_validator(mode="after")
    def _checksum_and_choice_are_coherent(self) -> ClientPack:
        given = [self.sha256, self.md5, self.md5_file]
        if sum(item is not None for item in given) > 1:
            raise ValueError(
                f"pack {self.id!r}: a pack takes exactly one of sha256, md5 and md5_file"
            )
        if self.md5_file is not None:
            if self.source.kind != "checkout" or self.source.path is None:
                raise ValueError(
                    f"pack {self.id!r}: only a checkout pack takes md5_file, which is read from "
                    "the server's checkout"
                )
            folder = PurePosixPath(self.md5_file).parent
            if not PurePosixPath(self.source.path).is_relative_to(folder):
                raise ValueError(
                    f"pack {self.id!r}: its zip {self.source.path!r} must be inside the folder of "
                    f"its md5_file {self.md5_file!r}, whose lines name files relative to it"
                )
        if self.source.kind == "checkout" and all(item is None for item in given):
            raise ValueError(
                f"pack {self.id!r}: a checkout pack needs a checksum: exactly one of sha256, "
                "md5 and md5_file"
            )
        if (
            self.source.kind == "url"
            and self.sha256 is None
            and self.md5 is None
            and self.source.version_url is None
        ):
            raise ValueError(
                f"pack {self.id!r}: a url pack needs a checksum or a version_url, or a "
                "changed pack would never be fetched again"
            )
        if self.default and not self.optional:
            raise ValueError(
                f"pack {self.id!r}: default only means something on an optional pack; "
                "a required pack is always installed"
            )
        return self


class ExeWrite(_Strict):
    """Bytes written into Wow.exe at one offset: given in hex, or one byte repeated.

    `fill` + `count` exists because Centurion's patch set NOPs runs of 11 and
    22 bytes, and 44 hex digits of `90` is a typo waiting to happen.
    """

    offset: int = Field(ge=0)
    bytes: str | None = Field(
        default=None,
        pattern=r"^(?:[0-9a-fA-F]{2})+$",
        description='The bytes, as hex pairs (`"eb"`, `"313233343200"`).',
    )
    fill: int | None = Field(default=None, ge=0, le=255, description="One byte value, repeated.")
    count: int | None = Field(default=None, gt=0, description="How many times `fill` repeats.")

    @model_validator(mode="after")
    def _one_way_of_saying_the_bytes(self) -> ExeWrite:
        if (self.bytes is None) == (self.fill is None):
            raise ValueError(f"a write takes exactly one of bytes and fill, at {self.offset:#x}")
        if (self.count is None) != (self.fill is None):
            raise ValueError(f"count goes with fill, and only with fill, at {self.offset:#x}")
        return self

    @property
    def length(self) -> int:
        """How many bytes this write covers."""
        if self.bytes is not None:
            return len(self.bytes) // 2
        assert self.count is not None  # the validator pairs count with fill
        return self.count

    def payload(self) -> builtins.bytes:
        """The bytes this write puts at `offset`."""
        if self.bytes is not None:
            return builtins.bytes.fromhex(self.bytes)
        assert self.fill is not None and self.count is not None
        return builtins.bytes([self.fill]) * self.count


class ExeOption(_Strict):
    """A player's on/off choice over some Wow.exe bytes (Centurion: a borderless window).

    `off` may be empty: the exe is always patched from stock bytes, so an
    option whose off state IS stock has nothing to write.
    """

    label: str = Field(min_length=1)
    default: bool
    on: tuple[ExeWrite, ...] = Field(min_length=1)
    off: tuple[ExeWrite, ...]


class CleanSource(_Strict):
    """A zip holding a stock Wow.exe, from which only that member is fetched by range requests."""

    url: str
    member: str = Field(min_length=1, description="The exe's member name inside the zip.")

    @field_validator("url")
    @classmethod
    def _url_is_https(cls, value: str) -> str:
        return _https_url(value, "clean_sources.url")

    @field_validator("member")
    @classmethod
    def _member_is_a_relative_name(cls, value: str) -> str:
        return _inside(value, "clean_sources.member", names_a_file=True)


class ExePatch(_Strict):
    """Byte patches to the ready-to-play client's Wow.exe, applied to stock bytes only (T181 c).

    `expect_sha256`/`expect_size` name the stock exe the offsets were measured
    on; a different exe is never patched, because the same offset in another
    build is another instruction. Every write -- the fixed ones and both
    states of every option -- must end inside `expect_size`, checked here
    once rather than discovered as a short file at Play.
    """

    expect_sha256: Sha256
    expect_size: int = Field(gt=0)
    clean_sources: tuple[CleanSource, ...] = Field(
        min_length=1, description="Where a stock exe is fetched from, tried in order."
    )
    fallback_page: str | None = Field(
        default=None,
        description="A page for a whole clean client, named in the refusal when no source answers.",
    )
    writes: tuple[ExeWrite, ...]
    options: dict[Slug, ExeOption] = Field(default_factory=dict)
    pe_large_address_aware: bool = Field(
        default=False, description="Set IMAGE_FILE_LARGE_ADDRESS_AWARE in the PE header."
    )
    build: int = Field(gt=0, description="The build the patched exe reports, for messages.")

    @field_validator("fallback_page")
    @classmethod
    def _fallback_page_is_https(cls, value: str | None) -> str | None:
        return value if value is None else _https_url(value, "fallback_page")

    @model_validator(mode="after")
    def _every_write_ends_inside_the_stock_exe(self) -> ExePatch:
        named: list[tuple[str, ExeWrite]] = [("writes", write) for write in self.writes]
        for name, option in self.options.items():
            named += [(f"options.{name}.on", write) for write in option.on]
            named += [(f"options.{name}.off", write) for write in option.off]
        for where, write in named:
            if write.offset + write.length > self.expect_size:
                raise ValueError(
                    f"{where}: the write at {write.offset:#x} of {write.length} bytes runs past "
                    f"the end of the stock exe ({self.expect_size} bytes)"
                )
        return self

    @model_validator(mode="after")
    def _no_two_writes_that_can_apply_together_overlap(self) -> ExePatch:
        # The fixed writes always apply; one state of each option applies on
        # top. The two states of ONE option never apply together, so they may
        # share bytes (Centurion's borderless on/off do exactly that).
        tagged: list[tuple[str, tuple[str, str] | None, ExeWrite]] = [
            ("writes", None, write) for write in self.writes
        ]
        for name, option in self.options.items():
            tagged += [(f"options.{name}.on", (name, "on"), w) for w in option.on]
            tagged += [(f"options.{name}.off", (name, "off"), w) for w in option.off]
        for index, (where_a, tag_a, a) in enumerate(tagged):
            for where_b, tag_b, b in tagged[index + 1 :]:
                if tag_a and tag_b and tag_a[0] == tag_b[0] and tag_a != tag_b:
                    continue  # the on and off of one option: only one applies
                if a.offset < b.offset + b.length and b.offset < a.offset + a.length:
                    raise ValueError(
                        f"{where_a} at {a.offset:#x} and {where_b} at {b.offset:#x} "
                        "write the same bytes of the stock exe"
                    )
        return self


class ConfigWtf(_Strict):
    """Settings merged into the ready-to-play client's `WTF/Config.wtf`.

    `always` is set at every Play (where the server is); `seed` only where
    the key is absent (a first-run preference the player may change after).
    A key in both would be a seed that is never a seed, so it is refused.
    """

    always: dict[WtfKey, WtfValue] = Field(default_factory=dict)
    seed: dict[WtfKey, WtfValue] = Field(default_factory=dict)
    remove_locale_realmlists: bool = Field(
        default=False,
        description=(
            "Delete `Data/*/realmlist.wtf` in the ready-to-play client, never the original's."
        ),
    )

    @model_validator(mode="after")
    def _a_key_is_either_always_or_seeded(self) -> ConfigWtf:
        always = {key.casefold() for key in self.always}
        both = sorted(key for key in self.seed if key.casefold() in always)
        if both:
            raise ValueError(f"Config.wtf keys {both} are in both always and seed")
        return self


class Client(_Strict):
    """The client the USER supplies (README §3a) and how to point it at the server.

    `packs`, `exe_patch` and `config_wtf` describe what this server's
    ready-to-play client needs beyond the user's own files (T181 b/c). All
    three are optional, and an entry without them behaves exactly as before.
    """

    version: str = Field(min_length=1)
    build: int = Field(gt=0)
    realmlist_file: str = "realmlist.wtf"
    notes: tuple[str, ...] = ()
    packs: tuple[ClientPack, ...] = ()
    exe_patch: ExePatch | None = None
    config_wtf: ConfigWtf | None = None

    @model_validator(mode="after")
    def _packs_are_distinct(self) -> Client:
        """Ids are unique, and no two packs install the same file.

        The install record is kept per pack id, and switching a pack off removes
        the files it recorded: two packs sharing an id would share a record, and
        two writing one file would let switching one off delete the other's.
        A pack's `remove_when_off` may not name a file another pack installs, for the
        same reason: switching one pack off must never delete another pack's file.
        Compared casefolded, because the client folder is usually on Windows.
        """
        ids = [pack.id for pack in self.packs]
        doubled = sorted({pack_id for pack_id in ids if ids.count(pack_id) > 1})
        if doubled:
            raise ValueError(f"pack ids must be unique, got {doubled} more than once")
        owner: dict[str, str] = {}
        for pack in self.packs:
            for rule in pack.install:
                if rule.to is None:
                    continue
                target = PurePosixPath(rule.to).as_posix().casefold()
                if owner.get(target, pack.id) != pack.id:
                    raise ValueError(
                        f"two packs install {rule.to!r}: {owner[target]!r} and {pack.id!r}"
                    )
                owner[target] = pack.id
        for pack in self.packs:
            for item in pack.remove_when_off:
                target = PurePosixPath(item).as_posix().casefold()
                if owner.get(target, pack.id) != pack.id:
                    raise ValueError(
                        f"pack {pack.id!r} removes {item!r} when off, which pack "
                        f"{owner[target]!r} installs: switching one off must never delete "
                        "the other's file"
                    )
        return self

    def hosts(self) -> frozenset[str]:
        """Every host a download for this client may reach: packs and clean exe sources.

        The allow-list later steps check every fetch against, so a redirect to
        anywhere else is refused. Not `fallback_page`: that is a page a person
        is sent to, never fetched.
        """
        urls: list[str | None] = []
        for pack in self.packs:
            urls += [pack.source.url, pack.source.version_url]
        if self.exe_patch is not None:
            urls += [source.url for source in self.exe_patch.clean_sources]
        hosts = (urlsplit(url).hostname for url in urls if url is not None)
        return frozenset(host for host in hosts if host)


class BotRegistry(_Strict):
    """A table this core's bot module keeps, listing which accounts are bots.

    One of the two arms of the bot marker, and never the only one. On a freshly
    built AzerothCore install this table can hold **zero rows while a thousand
    bot characters play**: the module fills it as it goes, so a detector that
    asks it alone reports every bot as a person and its inverse reports zero
    bots — a failure with nothing about it that looks broken (`botid.rs:4-17`,
    live proof 2026-08-01, recorded in `.notes/phase8-reads/hypeer.md`).
    """

    database: Db = Field(description="Which of this entry's databases the table lives in.")
    table: str = Field(min_length=1)
    account_column: str = Field(min_length=1)
    type_column: str = Field(min_length=1)
    types: tuple[int, ...] = Field(min_length=1, description="The type values that mean `bot`.")


class BotMarker(_Strict):
    """How this tree tells a bot character from a person's.

    `account_prefix` is the module's own compiled default, not the value in
    force: an install may have been given a prefix of its own, so the live value
    is read from `prefix_conf_file` and this is the fallback the server itself
    would use if the key is absent. Which of the two answered is reported to the
    user rather than smoothed over.
    """

    account_prefix: str = Field(
        min_length=1,
        description=(
            "The module's compiled default prefix. Never empty: an empty prefix becomes "
            "`LIKE '%'`, which classifies every account — and so every human — as a bot "
            "(`botid.rs:33-38`)."
        ),
    )
    prefix_conf_file: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Where the live prefix lives, relative to the server dir; null for a tree whose "
            "bot accounts are fixed rows with no prefix setting at all (T179: Centurion's "
            "PLAYERBOTONE..FOUR, `auth_bots.sql`), where `account_prefix` is the whole marker."
        ),
    )
    prefix_conf_key: str | None = Field(default=None, min_length=1)
    registry: BotRegistry | None = None

    @model_validator(mode="after")
    def _a_conf_file_names_its_key(self) -> BotMarker:
        """Both or neither: a file with no key, or a key in no file, reads nothing."""
        if (self.prefix_conf_file is None) != (self.prefix_conf_key is None):
            raise ValueError(
                "prefix_conf_file and prefix_conf_key go together: "
                f"file={self.prefix_conf_file!r}, key={self.prefix_conf_key!r}"
            )
        return self


class CharacterTable(_Strict):
    """The columns of this core's `characters` table that 8.1a reads.

    Named per tree rather than assumed. Tortoise keeps its characters in
    `tw_char`, and a column spelling that is right for one core is a guess about
    every other one.
    """

    table: str = Field(default="characters", min_length=1)
    account: str = Field(default="account", min_length=1)
    online: str = Field(default="online", min_length=1)


class ConfEnable(_Strict):
    """Which conf file turns this tree's channel on, and the keys that do it (8.2c).

    The CMaNGOS lineage reads its configuration from the file and from nowhere
    else: its reader lowercases the key and looks it up in what it parsed
    (`src/shared/Config/Config.cpp:73`, `:91`), with no environment fallback
    anywhere in it. AzerothCore's generic `AC_<UPPER_SNAKE>` rule
    (`Config.cpp:435-438`) has no counterpart here, so `enable_env` cannot serve
    these trees and this exists instead.

    The mechanism is not new: `install.native.cmangos.conf` already patches six
    keys in this same file at install time, through the same
    `families/conf.py` writer. What is new is doing it to an install that
    already exists, on a press, with a backup to roll back to.
    """

    file: str = Field(
        min_length=1,
        description="The conf file, relative to the install directory (`etc/mangosd.conf`).",
    )
    keys: dict[str, str] = Field(
        min_length=1,
        description=(
            "`Key = value` lines to set. `SOAP.IP` is `0.0.0.0` for the reason the "
            "environment route gives: the listener binds every interface INSIDE the "
            "container and the host side is pinned to loopback by the publication."
        ),
    )


class Operations(_Strict):
    """How this tree's command channel is turned on and reached (8.2a).

    Optional on an entry, and absent until that tree's own box measures it. The
    environment keys are not spelled by hand: AzerothCore reads every ini key
    `X` as `"AC_" + upper_snake(X)` (`Config.cpp:435-438`, transform at
    `:370-374`), and the environment wins over both the file and the compiled
    default — including for keys absent from the file (`:540-552`). The catalog
    already relies on that rule for `AiPlayerbot.MinRandomBots`, which is the
    proof it is generic rather than per-key.
    """

    channel: Literal["soap", "attach"]
    namespace: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "The XML namespace this tree's SOAP service answers to: `urn:AC` on AzerothCore, "
            "`urn:MaNGOS` on the CMaNGOS lineage. No default, because a default is one tree's "
            "answer inherited by the rest -- and the failure it produces is invisible. Measured "
            "on m910q, 2026-09-07: the namespace is checked BEFORE the credential, so a wrong "
            "one answers HTTP 500 `method name or namespace not recognized` even for a bad "
            "password, which reads exactly like a world that has not finished loading."
        ),
    )
    port: int | None = Field(
        default=None, gt=0, lt=65536, description="The channel's port inside the container."
    )
    gm_level: int | None = Field(
        default=None,
        ge=0,
        le=9,
        description=(
            "The level the channel needs of its account, on THIS tree's scale. Capped at 3 "
            "until 2026-09-08 -- the MaNGOS/AzerothCore scale -- and Tortoise's administrator "
            "is 4 on a scale that runs to 4 (measured: its SOAP answered a rank-3 account with "
            "'below administrator'). The real bound is the entry's `accounts.level.max_level`, "
            "checked on the entry; this one only keeps the shape sane."
        ),
    )
    enable_env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "What the generated override must carry for the channel to exist. `SOAP.IP` is "
            "`0.0.0.0` on purpose: the listener binds every interface INSIDE the container, "
            "and the host side is pinned to loopback by the compose publication. A listener on "
            "the container's own loopback does not serve a published port and does not refuse "
            "either — measured, `gates/8-spikes/published-port-vs-container-loopback/`."
        ),
    )
    enable_conf: ConfEnable | None = Field(
        default=None,
        description=(
            "The conf file that turns the channel on, for a tree with no environment "
            "route. Exactly one of `enable_env` and `enable_conf` is declared."
        ),
    )
    publish: bool = Field(
        default=False,
        description=(
            "Whether the generated override must publish this port. False where the "
            "install's own compose already does it -- WotLK's base file has carried "
            "`${DOCKER_SOAP_EXTERNAL_PORT:-127.0.0.1:7878}:7878` since before there was a "
            "channel -- and true where it does not, which is every CMaNGOS tree."
        ),
    )
    notes: tuple[str, ...] = Field(
        default=(),
        description=(
            "Per-tree facts about THIS channel, for a human. Nothing reads them. The shape "
            "`SqlPhase.notes` and `Client.notes` already have, and here for the same reason: "
            "which channel a tree can speak is a property of somebody else's build, it "
            "changes when that build changes, and the reasoning behind a `channel` is "
            "invisible from the value. `wow-tortoise` carries the recipe for putting SOAP "
            "back when the Penqle core re-links it, because that entry has been through the "
            "swap in both directions already."
        ),
    )
    must_not_listen: tuple[int, ...] = Field(
        default=(),
        description=(
            "Ports that must be silent inside the container once the press has run. 8888 is "
            "mod-playerbots' command server, which defaults to ON — 8888 in the dist AND in the "
            "compiled fallback (`PlayerbotAIConfig.cpp:468`) — takes `<command>,<bot guid>` "
            "lines on its own detached thread, and this install loads no module conf at all, so "
            "the compiled default is what is in force. 3443 is the telnet remote console."
        ),
    )

    @model_validator(mode="after")
    def _a_soap_channel_says_how_it_is_switched_on(self) -> Operations:
        """A channel nobody can enable is a channel that does not exist.

        Checked here because the relationship has no other owner: `enable_env`
        cannot see `channel`, and a `soap` entry whose keys never mention SOAP
        would install cleanly and answer nothing.
        """
        if self.channel == "attach":
            # Nothing to enable, nothing to publish, nothing to authenticate:
            # this core links neither gsoap nor RASocket, so its only way in is
            # the console the Console tab already types at. A field declared
            # here that cannot apply is worse than a missing one -- it reads as
            # something somebody measured (8.2e).
            claimed = [
                name
                for name, value in (
                    ("port", self.port),
                    ("namespace", self.namespace),
                    ("gm_level", self.gm_level),
                    ("enable_env", self.enable_env or None),
                    ("enable_conf", self.enable_conf),
                )
                if value is not None
            ]
            if claimed:
                raise ValueError(
                    f"an attach channel has no listener, so it declares none of these: "
                    f"{', '.join(claimed)}"
                )
            return self
        for name, value in (
            ("port", self.port),
            ("namespace", self.namespace),
            ("gm_level", self.gm_level),
        ):
            if value is None:
                raise ValueError(f"a {self.channel} channel must state its {name}")
        if bool(self.enable_env) == bool(self.enable_conf):
            raise ValueError(
                "a channel is switched on ONE way: declare either enable_env or "
                "enable_conf, not both and not neither"
            )
        keys = self.enable_env or (self.enable_conf.keys if self.enable_conf else {})
        if self.channel == "soap" and not any("SOAP" in key.upper() for key in keys):
            raise ValueError(
                "a soap channel must name the key that turns SOAP on; " f"these are {sorted(keys)}"
            )
        return self


class Observability(_Strict):
    """What the dashboard needs in order to count this install's population (8.1a).

    Optional on an entry, and absent until that tree's own box measures it: an
    inherited block would be a guess wearing the shape of a fact. 8.1b, 8.1c and
    8.1d each add their own.
    """

    characters: CharacterTable = CharacterTable()
    bots: BotMarker


class CatalogEntry(_Strict):
    """One installable server."""

    id: Slug
    name: str = Field(min_length=1)
    status: Status
    description: str = ""
    emulator: Emulator
    install: Install
    containers: Containers
    ports: Ports
    databases: Databases
    client: Client
    realmlist: Realmlist = Realmlist()
    console: Console = Console()
    accounts: Accounts = Field(default_factory=Accounts)
    play: Play | None = Field(
        default=None,
        description="What the Characters tab may offer here, once this tree has measured it.",
    )
    operations: Operations | None = Field(
        default=None,
        description=(
            "How this tree's command channel is enabled and reached (8.2a). `None` until that "
            "tree's own box has measured it against its own core."
        ),
    )
    observability: Observability | None = Field(
        default=None,
        description=(
            "The per-tree facts the dashboard's counts need (8.1a). `None` until this tree's "
            "own box has measured them against its own schema and its own bot module."
        ),
    )
    has_manifests: bool = Field(
        default=False, description="Whether manifests/<id>/ exists for module management."
    )
    notes: tuple[str, ...] = Field(
        default=(),
        description=(
            "Facts about this server for the people who maintain the entry: which branch and "
            "pull requests it rests on, which ticket measured what. Nothing reads them and "
            "nothing may draw them; `description` is the sentence a player reads on the "
            "Catalog tile (T194). The shape `Client.notes` and `SqlPhase.notes` already have."
        ),
    )

    @model_validator(mode="after")
    def _the_channel_rank_is_one_this_tree_can_hold(self) -> CatalogEntry:
        """`operations.gm_level` may not exceed `accounts.level.max_level`.

        Two blocks written by different boxes about the same account: the channel
        says what rank it needs, the accounts block says what ranks exist. A rank
        above the scale is not a stricter requirement, it is one no row can meet,
        and the first sign of it was a 401 on a fresh install (yulon-arch,
        2026-09-08) rather than a catalog error. Checked here, once, for both.
        """
        ops = self.operations
        if ops is not None and ops.gm_level is not None and self.accounts.level is not None:
            top = self.accounts.level.max_level
            if ops.gm_level > top:
                raise ValueError(
                    f"{self.id}: operations.gm_level {ops.gm_level} is above this tree's own "
                    f"level scale (accounts.level.max_level {top}); no account can hold it"
                )
        return self

    @model_validator(mode="after")
    def _every_patch_names_a_source_this_entry_clones(self) -> CatalogEntry:
        """A `SourcePatch.source` is a `dest` in `emulator.sources`, and the two live apart.

        The relationship has no owner otherwise: `SourcePatch` cannot see the
        sources and `Emulator` knows nothing of patches, so a typo in one would
        be an install that clones everything and then refuses at `patch-sources`
        with "no such file" over a folder that was never meant to exist.
        """
        native = self.install.native
        block = native.cmangos if native is not None else None
        if block is None:
            return self
        dests = {source.dest for source in self.emulator.sources}
        for spec in block.patches:
            if spec.source not in dests:
                raise ValueError(
                    f"patch {spec.file!r} applies inside {spec.source!r}, which is not a dest "
                    f"of any of this entry's sources {sorted(dests)}"
                )
        return self

    @model_validator(mode="after")
    def _the_trinitycore_block_agrees_with_the_entry(self) -> CatalogEntry:
        """The block's checkout, renames and account scheme agree with the rest of the entry.

        Three relationships the block cannot see from inside. A checkout that is
        not a dest of `emulator.sources` is an install that clones and then reads
        paths from a folder nothing made. A rename to a name that is not one of
        `databases` would point the dumps' triggers and procedures at somebody
        else's database (import.sh:41-43). And `accounts.scheme` defaults to
        AzerothCore's, whose `account_access(id, gmlevel)` is not TrinityCore's
        `(AccountID, SecurityLevel)` (facts §5), so the entry must declare
        `trinitycore` or no scheme at all.
        """
        native = self.install.native
        block = native.trinitycore if native is not None else None
        if block is None:
            return self
        dests = {source.dest for source in self.emulator.sources}
        if block.checkout not in dests:
            raise ValueError(
                f"trinitycore.checkout is {block.checkout!r}, which is not a dest of any of "
                f"this entry's sources {sorted(dests)}"
            )
        if self.accounts.scheme not in ("trinitycore", None):
            raise ValueError(
                f"a trinitycore entry's accounts.scheme must be 'trinitycore' or null, got "
                f"{self.accounts.scheme!r}: another core's columns write rows that look right "
                "and grant nothing (an omitted accounts block inherits 'azerothcore')"
            )
        schemas = set(self.databases.schema_map().values())
        for old, new in block.sql.renames:
            if new not in schemas:
                raise ValueError(
                    f"rename {old!r} -> {new!r} lands on {new!r}, which is not one of this "
                    f"entry's schemas {sorted(schemas)}"
                )
        # T179 Task 6: the update route re-imports into the world database only. A
        # whole-table dump imported again over the characters' or the accounts'
        # database would DROP the player's own rows.
        if native is not None and native.update_to_latest and block.updates is None:
            raise ValueError(
                "a trinitycore entry with update_to_latest must say what an update does with its "
                "SQL snapshot (trinitycore.updates)"
            )
        if block.updates is not None:
            phases = {phase.name: phase for phase in block.sql.phases}
            for name in block.updates.reimport_phases:
                phase = phases[name]
                if phase.into != self.databases.world or phase.into_each:
                    raise ValueError(
                        f"updates.reimport_phases names {name!r}, which writes into "
                        f"{phase.into or sorted(phase.into_each or {})!r}, not the world database "
                        f"{self.databases.world!r}: imported again, its dumps would drop that "
                        "database's tables"
                    )
        return self

    def schema_map(self) -> dict[Db, str]:
        """This game's `manifest db key → schema name` map (see `Databases.schema_map`)."""
        return self.databases.schema_map()

    def core_databases(self) -> tuple[str, str, str]:
        """The three schemas whose absence is an alarm, in this core's own names.

        `maintenance.backup()` reports what it could not find; asked of the
        entry so the alarm names schemas this install could plausibly have. The
        module-level default is AzerothCore's, which is why a Tortoise backup
        reported `acore_auth` missing on a dump that had taken everything.
        """
        return (self.databases.auth, self.databases.characters, self.databases.world)

    def container_spec(self) -> ContainerSpec:
        """The `ContainerSpec` a controller for this game would be built from."""
        return ContainerSpec(
            db=self.containers.db,
            auth=self.containers.auth,
            world=self.containers.world,
            ports=(self.ports.auth, self.ports.world),
            services=self.containers.services or (),
            import_service=self.containers.db_import or "",
            stop_waits_for_load=(
                self.install.native is not None and self.install.native.stop_waits_for_world_load
            ),
        )


class Catalog(_Strict):
    """The whole catalog file."""

    schema_version: Literal[1] = 1
    games: tuple[CatalogEntry, ...] = ()

    def get(self, game_id: str) -> CatalogEntry:
        """Look an entry up by id; `KeyError` if unknown."""
        for entry in self.games:
            if entry.id == game_id:
                return entry
        raise KeyError(game_id)


def parse_catalog(data: object) -> Catalog:
    """Validate raw JSON-decoded data into a `Catalog`."""
    return Catalog.model_validate(data)


def load_catalog(path: Path = CATALOG_FILE) -> Catalog:
    """Read + validate `catalog.json` (the bundled one by default)."""
    with path.open(encoding="utf-8") as fh:
        return parse_catalog(json.load(fh, object_pairs_hook=_refuse_duplicate_keys))


def _refuse_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Refuse an object that names one key twice, which `json` would resolve silently.

    JSON keeps the LAST of two equal keys, so two exe options of one name, or
    two `client` blocks in one entry, would load as one and the loser would
    vanish without a word. Refused here because no model can see it: by the
    time pydantic gets a dict, the duplicate is already gone.
    """
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r} in one object of the catalog file")
        seen[key] = value
    return seen
