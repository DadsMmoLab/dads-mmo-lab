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
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from yulon.docker import ContainerSpec
from yulon.manifest import Source
from yulon.platform import PlatformId

CATALOG_FILE = Path(__file__).resolve().with_name("catalog.json")

Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
Status = Literal["stable", "beta", "wip"]
# Keys of `Install.script_variants` — the same names `platform.linux_package_manager()` returns.
PackageManager = Literal["apt", "dnf", "pacman", "zypper"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Emulator(_Strict):
    """The open-source emulator: a display name and the repos the installer clones."""

    name: str = Field(min_length=1)
    sources: tuple[Source, ...] = Field(min_length=1)


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
            "PROVENANCE of WotLK's 1600/2000: copied from the ONE proven yulon-ubuntu install, "
            "where the Linux installer script wrote them, after a `docker compose config` diff "
            "on 2026-08-24 found a native install would otherwise differ from a script install. "
            "Not measured on any other machine and not measured at all for RAM — one desktop's "
            "population, carried so the two paths agree, owed an RSS reading by the first gate."
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
    db_root_password: str | None = Field(
        default=None, description="Fixed root password the installer uses, if any."
    )
    db_root_password_file: str | None = Field(
        default=None, description="File under the server dir holding a generated password."
    )
    requires_client_dir: bool = Field(
        default=False,
        description="The script asks for the user's client folder and loops until given one.",
    )
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
    """The three container names the controller manages, and the import job's service."""

    db: str = Field(min_length=1)
    auth: str = Field(min_length=1)
    world: str = Field(min_length=1)
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


class Realmlist(_Strict):
    """Where the realm's advertised address lives in the auth DB (README §13 updater)."""

    table: str = "realmlist"
    address_column: str = "address"
    local_address_column: str | None = Field(
        default="localAddress", description="None for cores whose realmlist has no LAN column."
    )
    realm_id: int = 1


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
    has_manifests: bool = Field(
        default=False, description="Whether manifests/<id>/ exists for module management."
    )

    def container_spec(self) -> ContainerSpec:
        """The `ContainerSpec` a controller for this game would be built from."""
        return ContainerSpec(
            db=self.containers.db,
            auth=self.containers.auth,
            world=self.containers.world,
            ports=(self.ports.auth, self.ports.world),
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
