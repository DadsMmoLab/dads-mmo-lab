"""The Catalog schema: typed models for `catalog.json` (roadmap 3.1).

One entry per installable server. Everything an installer, a controller or
the networking helpers need to know about a game — emulator sources, the
`native` block its family engine reads (Phase 6/7), container names, the
auth/world/db port table (README §13), database names, what client the user
must supply (README §3a) — is data here, not Python (style-guide §3). Acronyms only
(§6): `id`s are `wow-wotlk`, `wow-tbc`, `wow-vanilla`, `wow-tortoise`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    """The AzerothCore family's own install data — only the worldserver env block (A2)."""

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

    @field_validator("mpq_depth")
    @classmethod
    def _depth_is_positive(cls, value: MpqDepth) -> MpqDepth:
        if isinstance(value, int) and value < 1:
            raise ValueError("mpq_depth must be >= 1 or 'recursive'")
        return value


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

    name: str = Field(min_length=1)
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
            "(`pyplan/gates/7.9-rerun-m910q-2026-09-09/README.md`, finding 1)."
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

    @model_validator(mode="after")
    def _one_source_one_target(self) -> SqlPhase:
        if self.assert_update_level and self.statements:
            # The check reads the LAST FILE this phase applied and turns its name
            # into a column. A literal statement has no name to read, so the flag
            # would be dead text on such a phase rather than a weaker check.
            raise ValueError(
                f"phase {self.name!r}: `assert_update_level` reads the name of the last file "
                "applied, so it cannot be set on a `statements` phase"
            )
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
    family: Literal["azerothcore", "cmangos"] = Field(
        description=(
            "Which family engine installs this game; must equal the engine's `family`. "
            "This Literal is the first file a new lineage's data touches, so the policy "
            "is stated here too: a family with no registered engine is a DEFECT and not "
            "a supported window — `installer_for()` refuses the entry rather than "
            "falling back to anything. A new lineage is a class in `catalog/families/`, "
            "a line in `FAMILIES`, and then a member here."
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
    db: DbFacts
    ready: ReadyMarkers
    azerothcore: AzerothCoreData | None = Field(
        default=None, description="Present exactly when `family` is azerothcore (7.3 validates)."
    )
    cmangos: CmangosData | None = None
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
        blocks = {"azerothcore": self.azerothcore, "cmangos": self.cmangos}
        present = sorted(name for name, block in blocks.items() if block is not None)
        if present != [self.family]:
            raise ValueError(
                f"family is {self.family!r} but the blocks present are {present}; "
                f"exactly the `{self.family}` block must be present"
            )
        if self.cmangos is not None and self.cmangos.extract.image not in self.images:
            raise ValueError(
                f"cmangos.extract.image {self.cmangos.extract.image!r} is not one of images "
                f"{list(self.images)}"
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

    scheme: Literal["azerothcore", "mangos_sha", "mangos_srp6"] | None = Field(
        default="azerothcore",
        description=(
            "How this core stores an account: `azerothcore` is SRP6 in binary salt/verifier "
            "with the level in account_access; `mangos_sha` is sha_pass_hash with the level "
            "in account.rank; `mangos_srp6` is the same SRP6 as AzerothCore stored as hex "
            "text in v/s with the level in account.gmlevel. None means this app does not "
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


class Client(_Strict):
    """The client the USER supplies (README §3a) and how to point it at the server."""

    version: str = Field(min_length=1)
    build: int = Field(gt=0)
    realmlist_file: str = "realmlist.wtf"
    notes: tuple[str, ...] = ()


class BotRegistry(_Strict):
    """A table this core's bot module keeps, listing which accounts are bots.

    One of the two arms of the bot marker, and never the only one. On a freshly
    built AzerothCore install this table can hold **zero rows while a thousand
    bot characters play**: the module fills it as it goes, so a detector that
    asks it alone reports every bot as a person and its inverse reports zero
    bots — a failure with nothing about it that looks broken (`botid.rs:4-17`,
    live proof 2026-08-01, recorded in `pyplan/phase8-reads/hypeer.md`).
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
    prefix_conf_file: str = Field(
        min_length=1, description="Where the live prefix lives, relative to the server dir."
    )
    prefix_conf_key: str = Field(min_length=1)
    registry: BotRegistry | None = None


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
        return parse_catalog(json.load(fh))
