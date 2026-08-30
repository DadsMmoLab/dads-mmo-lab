"""The Catalog schema: typed models for `catalog.json` (roadmap 3.1).

One entry per installable server. Everything an installer, a controller or
the networking helpers need to know about a game — emulator sources, the
install script to wrap (Phase 3a), container names, the auth/world/db port
table (README §13), database names, what client the user must supply
(README §3a) — is data here, not Python (style-guide §3). Acronyms only
(§6): `id`s are `wow-wotlk`, `wow-tbc`, `wow-vanilla`, `wow-tortoise`.
"""

from __future__ import annotations

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
# Keys of `Install.script_variants` — the same names `platform.linux_package_manager()` returns.
PackageManager = Literal["apt", "dnf", "pacman", "zypper"]


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
    def _the_mode_has_its_field(self) -> PasswordPlan:
        if self.mode == "fixed" and not self.value:
            raise ValueError("a fixed password plan needs a non-empty `value`")
        if self.mode == "generated" and not self.file:
            raise ValueError("a generated password plan needs `file`")
        return self


class NativeInstall(_Strict):
    """What the native install engine needs that is a fact about THIS game (roadmap 6.2).

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


class Install(_Strict):
    """How this game is installed: by driving its bash script, or natively.

    Two lists rather than one, because "where can this be installed" and "where
    is the *script* the mechanism" stopped being the same question in roadmap
    6.2. `platforms` still drives the 6.1 refusal; `script_platforms` says where
    the bash script runs, and anything in `platforms` but not in
    `script_platforms` runs `catalog/native.py`'s engine instead. See
    `installer.installer_for()`, which is the one place that decides.
    """

    script: str = Field(
        min_length=1,
        description="Path to the install-*.sh, relative to catalog/installers/",
    )
    default_server_dir: str = Field(min_length=1, description="Default dir name under $HOME")
    password: PasswordPlan = Field(
        description="Where the database root password comes from: fixed, or generated per install."
    )
    requires_client_dir: bool = Field(
        default=False,
        description="The script asks for the user's client folder and loops until given one.",
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
            "Which platforms this entry's install script can actually run on. Data, not a Python "
            "conditional (roadmap 6.1): every v1 installer is a Linux-only bash script today, so "
            "off-Linux clicks must be refused with an honest message instead of streaming a "
            "script that exits 1. 6.2/6.3 add macOS/Windows variants and widen this list."
        ),
    )
    script_platforms: tuple[PlatformId, ...] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Where the bash SCRIPT is the install mechanism. Absent means 'wherever this entry "
            "is installable at all', so every entry written before roadmap 6.2 keeps meaning "
            "exactly what it said. An entry listing macOS in `platforms` and only Linux here is "
            "saying: install macOS with the native engine."
        ),
    )
    native: NativeInstall | None = Field(
        default=None,
        description=(
            "Floors and templates for the native engine. Required for any platform this entry "
            "dispatches natively; the engine refuses to run without it rather than inventing a "
            "template directory."
        ),
    )
    script_variants: dict[PackageManager, str] = Field(
        default_factory=dict,
        description=(
            "Per-package-manager overrides of `script` (keys: apt, dnf, pacman, zypper) for "
            "distros the default script does not cover, same base directory; `script` "
            "itself is the pacman/SteamOS one."
        ),
    )

    def supports(self, platform_id: str) -> bool:
        """True if this entry can be installed on `platform_id` (`platform.detect()`) at all."""
        return platform_id in self.platforms

    def scripted_platforms(self) -> tuple[PlatformId, ...]:
        """Where the bash script is the mechanism — `platforms` when nothing narrower is said.

        A method rather than a validator that fills the field in, so the JSON
        keeps saying what its author wrote: an entry with no `script_platforms`
        is one that has never been asked the question, and reading `None` back
        out of it is how a future migration can tell those apart from an entry
        that answered "the script runs everywhere".
        """
        return self.script_platforms if self.script_platforms is not None else self.platforms

    def uses_script(self, platform_id: str) -> bool:
        """True if installing on `platform_id` means running the bash script."""
        return platform_id in self.scripted_platforms()

    def is_native(self, platform_id: str) -> bool:
        """True if installing on `platform_id` means the native engine.

        Supported here, but not by the script. Deliberately NOT "not scripted":
        a platform the entry does not support at all is the 6.1 refusal, and
        answering True for it would turn an honest "not on Windows yet" into an
        engine that starts and then fails.
        """
        return self.supports(platform_id) and not self.uses_script(platform_id)

    def script_for(self, package_manager: str | None) -> str:
        """The script for a host with `package_manager` (None → default), relative to
        `catalog/installers/`."""
        for pm, script in self.script_variants.items():
            if pm == package_manager:
                return script
        return self.script


class Containers(_Strict):
    """The three container names the controller manages, their services, and the import job."""

    db: str = Field(min_length=1)
    auth: str = Field(min_length=1)
    world: str = Field(min_length=1)
    services: tuple[str, str, str] | None = Field(
        default=None,
        description=(
            "Compose SERVICE names for db/auth/world, in that order, when they differ from the "
            "container names above. AzerothCore names a service and its container the same thing "
            "and may leave this out; every CMaNGOS game does not — its services are "
            "db/realmd/mangosd while its containers are <game>-db/-realmd/-mangosd — and "
            "MUST say so. `docker compose up` takes services, and a container name it does not "
            "know fails outright with `no such service` (Discord report, 2026-08-26)."
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


class Accounts(_Strict):
    """Whether this app can create an account on this core by writing the row itself.

    Three shapes, one per core family. AzerothCore uses `salt`/`verifier` with
    the level in `account_access`. Tortoise uses `sha_pass_hash` with the level
    in `account.rank` — measured against a live server on 2026-08-26, where the
    core logged its own INSERT and `SHA1(UPPER(user):UPPER(pass))` matched it
    exactly. CMaNGOS proper (TBC, Vanilla) keeps SRP6 in `v`/`s` with the level
    in `gmlevel`, which is a THIRD shape and has not been measured, so it is
    declared unsupported rather than assumed to be tortoise's.

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
    accounts: Accounts = Accounts()
    has_manifests: bool = Field(
        default=False, description="Whether manifests/<id>/ exists for module management."
    )

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
