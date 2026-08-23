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


class Install(_Strict):
    """How the Phase 3a installer drives the existing script for this game."""

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
    script_variants: dict[PackageManager, str] = Field(
        default_factory=dict,
        description=(
            "Per-package-manager overrides of `script` (keys: apt, dnf, pacman, zypper) for "
            "distros the default script does not cover, same base directory; `script` "
            "itself is the pacman/SteamOS one."
        ),
    )

    def supports(self, platform_id: str) -> bool:
        """True if this entry's installer runs on `platform_id` (`platform.detect()`)."""
        return platform_id in self.platforms

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
