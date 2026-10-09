"""The move package: a server's accounts and characters, packed to go to another computer (T601).

Level 1 only. The package holds the auth, characters (and, where the game has them, the
playerbots and Lua-engine) databases of ONE game as verified mysqldumps, and a manifest
that says what they are. Nothing else: no world database, no conf, no module, no build.
Importing is for a server of the SAME game that already exists on the other computer.

This module is the file format and the checks that need no Docker: the manifest model,
the writer, the reader, the refusal sentences, and the comparison of two servers'
database versions. `move_flows` is the part that talks to a server.

**What is trusted, and when.** Nothing in a package is believed until it has been read
back: the reader opens every member and compares its length and SHA-256 with the manifest
before it answers, the member names are checked for the shapes a zip can use to write
outside a folder, and the format number is read before the rest of the manifest, so a
file from a newer Yu'lon says so instead of failing a strict parse. Every string the
manifest carries that later reaches a SQL statement (the channel account, the realm name,
the bot prefix) has a grammar here, and a manifest that breaks it is not a package.

**Secrets.** The auth dump holds every account's login verifier and salt. The manifest says
so in `SECRETS`, the dialog says so, and the file name ends `-keep-private` (owner decision
2026-10-09: no password on the package in v1, a plain warning instead). The database
password and the command-channel credential are not in it: neither is a table in these
databases, and no conf file is packed.
"""

from __future__ import annotations

import errno
import hashlib
import json
import re
import sys
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

import yulon
from yulon.controller_wow_wotlk.maintenance import MaintenanceError
from yulon.log import get_logger
from yulon.server_build_presses import UPDATE_TO_LATEST, under_server_build

logger = get_logger(__name__)

FORMAT = 1
"""The package format this build writes and the newest it can read."""

MANIFEST_NAME = "yulon-move.json"
KEEP_PRIVATE = "keep-private"
"""The last word of the file name: the warning the owner asked for, kept with the file."""

SECRETS = (
    "This file holds every account's login verifier and salt, so anyone who has it can try to "
    "work out the passwords. Keep it private and delete it once the move is done. The database "
    "password and the command-channel credential are not in it."
)

NOT_A_PACKAGE = (
    "This file is not a Yu'lon move package, or it is damaged. Pack the accounts and "
    "characters again on the old computer."
)

_CHUNK = 1 << 20
_SCHEMA = re.compile(r"[A-Za-z0-9_]{1,64}")
_CHANNEL_ACCOUNT = re.compile(r"YULON_[A-Z0-9]{1,64}")
_BOT_PREFIX = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_DRIVE = re.compile(r"^[A-Za-z]:")
_DEVICE = re.compile(
    r"^(?:con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³]|conin\$|conout\$)$", re.IGNORECASE
)
"""A Windows device name: `CON`, `NUL.txt` and `COM1.x` open the device, not a file."""
MAX_FILE_MEMBER_BYTES = 256 * 1024**2
"""The most one non-database member may declare: a conf, a script or a module file is read whole."""


class MovePackageError(MaintenanceError):
    """A package that cannot be used, in a sentence the player can read as it is.

    A `MaintenanceError`, so the Maintenance tab shows it the way it shows every other
    refusal of a backup or restore: the sentence as it is, a program's words folded.
    """


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MadeBy(_Strict):
    yulon: str
    platform: str
    made: str
    """ISO time from the clock of the computer that packed it; shown, never decided on."""


class GameRef(_Strict):
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=1, max_length=100)


Role = Literal["auth", "characters", "world", "playerbots", "ale"]
"""`world` only in a whole-server package (level 2)."""

Kind = Literal["characters", "server"]
"""What a package holds: accounts and characters (level 1), or the whole server (level 2)."""

FileKind = Literal["conf", "answers", "manifest", "lua", "module"]
"""The non-database files of a whole-server package, each in its own folder of the zip."""

FILE_FOLDERS: dict[str, str] = {
    "conf": "conf/",
    "answers": "answers/",
    "manifest": "manifests/",
    "lua": "lua/",
    "module": "modfiles/",
}

ANSWERS_TARGET = ".yulon-module-answers.json"
"""The one Yu'lon record that travels: it describes the databases, which travel too."""

NEVER_PACKED = re.compile(
    r"(?:^|/)(?:\.yulon-[^/]*|\.db_password|\.env|db-secrets|credentials)(?:/|$)", re.IGNORECASE
)
"""Paths no file member may name: Yu'lon's records (the install claim, the folder id T568
says a copy must make again), the database password, the channel credentials."""

_SHA = r"^[0-9a-f]{40}$"
_MANIFEST_TARGET = re.compile(r"(?:module|ale|mod|keg)/[a-z0-9]+(?:-[a-z0-9]+)*")
_MODULE_FILE_TARGET = re.compile(r"((?:module|ale|mod|keg)/[a-z0-9]+(?:-[a-z0-9]+)*)/(.+)")
MODULE_FILE_DEPTH = 16
"""Folders above a file inside a folder module's package members."""


class Member(_Strict):
    """One database dump inside the package."""

    schema_name: str = Field(alias="schema")
    role: Role
    file: str
    bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tables: tuple[str, ...] = Field(min_length=1)
    """The tables and views the dump holds: how an import tells what the target has that the
    file does not cover (rows there may point at characters the import replaces)."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @field_validator("schema_name")
    @classmethod
    def _schema_grammar(cls, value: str) -> str:
        if not _SCHEMA.fullmatch(value):
            raise ValueError("a schema name is letters, digits and underscores")
        return value

    @field_validator("tables")
    @classmethod
    def _table_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for name in value:
            if not name or len(name) > 64 or any(ord(c) < 32 for c in name):
                raise ValueError("a table name is printable text of up to 64 characters")
        return value

    @field_validator("file")
    @classmethod
    def _file_is_the_schema_file(cls, value: str) -> str:
        if not re.fullmatch(r"db/[A-Za-z0-9_]{1,64}\.sql", value):
            raise ValueError("a database file is db/<schema>.sql")
        return value


class Evidence(_Strict):
    """What a database says about its own version, reduced to a count and a digest.

    `kind` names where it was read from (`updates`, `migrations`, `db_version`), so two
    servers are only ever compared on the same evidence.
    """

    kind: str = Field(min_length=1, max_length=40)
    count: int = Field(ge=0)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    modules: tuple[str, ...] = ()
    """The module rows of the same ledger, each `name|hash` (AzerothCore/TrinityCore `updates`
    rows that are not core ones; Tortoise `migrations` rows with a module). Kept apart from
    `digest` because they are compared apart: a different module set is a different sentence."""

    @field_validator("modules")
    @classmethod
    def _module_rows(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for row in value:
            if not row or len(row) > 400 or any(ord(c) < 32 for c in row):
                raise ValueError("a module row is printable text of up to 400 characters")
        return value


class Counts(_Strict):
    accounts: int = Field(ge=0)
    characters: int = Field(ge=0)
    bot_accounts: int = Field(ge=0)


def _safe_target(value: str) -> str:
    """A server-relative POSIX path that stays inside the server folder and is not a record."""
    if not value or len(value) > 400 or any(ord(c) < 32 for c in value) or _unsafe_name(value):
        raise ValueError("a file target is a relative path inside the server folder")
    if NEVER_PACKED.search(value):
        raise ValueError("a Yu'lon record, the database password or a credential never travels")
    return value


class PackedSource(_Strict):
    """One server source and the commit the packed server was built from."""

    repo: str = Field(min_length=3, max_length=200)
    dest: str = Field(min_length=1, max_length=200)
    commit: str = Field(pattern=_SHA)
    catalog_pin: str | None = Field(default=None, pattern=_SHA)
    """The pin the packing Yu'lon's catalog named for this source, for the dialog only."""

    @field_validator("dest")
    @classmethod
    def _dest_inside(cls, value: str) -> str:
        if value != "." and _unsafe_name(value):
            raise ValueError("a source folder is a relative path inside the server folder")
        return value


class PackedModule(_Strict):
    """One module installed from a clone, and the commit its clone was on."""

    type: Literal["module", "ale", "mod", "keg"]
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    origin: Literal["catalog", "link", "folder"]
    repo: str | None = Field(default=None, min_length=3, max_length=200)
    commit: str | None = Field(default=None, pattern=_SHA)
    """Both None for a module added from a folder: it has no repository, its files travel."""

    @model_validator(mode="after")
    def _a_folder_module_has_no_repository_and_every_other_has(self) -> PackedModule:
        if (self.origin == "folder") != (self.repo is None and self.commit is None):
            raise ValueError("a folder module has no repository or commit, any other has both")
        if self.origin != "folder" and (self.repo is None or self.commit is None):
            raise ValueError("a catalog or link module names its repository and commit")
        return self


class FileMember(_Strict):
    """One non-database file in a whole-server package, and where it goes."""

    file: str
    kind: FileKind
    target: str
    bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _the_name_is_the_target(self) -> FileMember:
        _check_target(self.kind, self.target)
        if self.file != FILE_FOLDERS[self.kind] + self.target:
            raise ValueError("a file member is stored under its kind's folder and its target")
        return self


def _check_target(kind: str, target: str) -> None:
    if kind == "manifest":
        if not _MANIFEST_TARGET.fullmatch(target):
            raise ValueError("a manifest member is <type>/<id>")
        return
    if kind == "module":
        found = _MODULE_FILE_TARGET.fullmatch(target)
        if found is None:
            raise ValueError("a module file member is <type>/<id>/<path inside the module>")
        inside = found.group(2)
        _safe_target(inside)
        parts = inside.split("/")
        if len(parts) - 1 > MODULE_FILE_DEPTH or any(p.casefold() == ".git" for p in parts):
            raise ValueError("a module file is not inside .git and is not too deep")
        return
    if kind == "answers":
        if target != ANSWERS_TARGET:
            raise ValueError(f"the answers member is {ANSWERS_TARGET}")
        return
    _safe_target(target)
    if kind == "conf" and not target.endswith(".conf"):
        raise ValueError("a conf member is a .conf file")


class ServerPart(_Strict):
    """What a new install needs to rebuild the same server (level 2)."""

    sources: tuple[PackedSource, ...] = Field(min_length=1)
    modules: tuple[PackedModule, ...] = ()
    files: tuple[FileMember, ...] = ()


class Manifest(_Strict):
    format: int
    kind: Kind = "characters"
    made_by: MadeBy
    game: GameRef
    databases: tuple[Member, ...] = Field(min_length=1)
    schema_evidence: dict[str, Evidence] = Field(default_factory=dict)
    realm_name: str | None = None
    channel_account: str | None = None
    bot_prefix: str | None = None
    counts: Counts
    excluded: tuple[str, ...] = ()
    secrets: str
    server: ServerPart | None = None

    @model_validator(mode="after")
    def _a_server_package_and_only_one_has_its_section(self) -> Manifest:
        if (self.kind == "server") != (self.server is not None):
            raise ValueError("a whole-server package, and only one, carries a server section")
        return self

    @field_validator("realm_name")
    @classmethod
    def _realm_name_is_printable(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or len(value) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("a realm name is printable text of up to 100 characters")
        return value

    @field_validator("channel_account")
    @classmethod
    def _channel_account_grammar(cls, value: str | None) -> str | None:
        if value is not None and not _CHANNEL_ACCOUNT.fullmatch(value):
            raise ValueError("the channel account is YULON_ and an install id")
        return value

    @field_validator("bot_prefix")
    @classmethod
    def _bot_prefix_grammar(cls, value: str | None) -> str | None:
        if value is not None and not _BOT_PREFIX.fullmatch(value):
            raise ValueError("a bot prefix is letters, digits and _.-")
        return value


@dataclass(frozen=True)
class Header:
    """What the writer needs to know that the dump files do not say."""

    game_id: str
    game_name: str
    realm_name: str | None
    channel_account: str | None
    bot_prefix: str | None
    counts: Counts
    schema_evidence: Mapping[str, Evidence]
    excluded: Sequence[str]
    made: datetime


@dataclass(frozen=True)
class DumpFile:
    """One verified dump on disk, with the role its schema plays in this game."""

    schema_name: str
    role: Role
    path: Path
    tables: tuple[str, ...]


@dataclass(frozen=True)
class PackFile:
    """One non-database file to pack, already read (confs, answers, manifests and Lua are small)."""

    kind: FileKind
    target: str
    data: bytes

    def __post_init__(self) -> None:
        _check_target(self.kind, self.target)

    @property
    def file(self) -> str:
        return FILE_FOLDERS[self.kind] + self.target


@dataclass(frozen=True)
class ServerSpec:
    """The `server` section's facts the writer is given; it hashes the files itself."""

    sources: tuple[PackedSource, ...]
    modules: tuple[PackedModule, ...] = ()


@dataclass(frozen=True)
class ServerFacts:
    """What a package of the whole server holds besides its databases (`move_server` reads it)."""

    spec: ServerSpec
    files: tuple[PackFile, ...]


def package_filename(game_id: str, made: datetime, *, kind: Kind = "characters") -> str:
    """`yulon-move-[server-]<game>-<YYYYMMDD-HHMM>-keep-private.zip`."""
    word = "server-" if kind == "server" else ""
    return f"yulon-move-{word}{game_id}-{made:%Y%m%d-%H%M}-{KEEP_PRIVATE}.zip"


# ------------------------------------------------------------------- writing


def write_package(
    dest: Path,
    header: Header,
    dumps: Sequence[DumpFile],
    *,
    server: ServerSpec | None = None,
    files: Sequence[PackFile] = (),
) -> Manifest:
    """Write the package to `dest`, read it back, and only then give it its name.

    Streamed: each dump is hashed in a first pass and copied into the zip in a second, so
    memory stays at one chunk whatever the characters weigh. The file is written under
    `<name>.partial`, read back through the same reader an import uses, and renamed; a
    package that does not read back is deleted and never keeps the name of a good one.
    """
    if dest.exists():
        raise MovePackageError(f"{dest.name} already exists, so Yu'lon did not overwrite it.")
    if files and server is None:
        raise MovePackageError("Only a whole-server package carries files besides its databases.")
    if len({f.file for f in files}) != len(files):
        raise MovePackageError("Two files to pack have the same name, so nothing was packed.")
    members: list[Member] = []
    for dump in dumps:
        digest, size = _hash_file(dump.path)
        members.append(
            Member(
                schema=dump.schema_name,
                role=dump.role,
                file=f"db/{dump.schema_name}.sql",
                bytes=size,
                sha256=digest,
                tables=tuple(dump.tables),
            )
        )
    manifest = Manifest(
        format=FORMAT,
        made_by=MadeBy(
            yulon=yulon.__version__, platform=sys.platform, made=header.made.isoformat()
        ),
        game=GameRef(id=header.game_id, name=header.game_name),
        databases=tuple(members),
        schema_evidence=dict(header.schema_evidence),
        realm_name=header.realm_name,
        channel_account=header.channel_account,
        bot_prefix=header.bot_prefix,
        counts=header.counts,
        excluded=tuple(header.excluded),
        secrets=SECRETS,
        kind="server" if server is not None else "characters",
        server=(
            ServerPart(
                sources=server.sources,
                modules=server.modules,
                files=tuple(
                    FileMember(
                        file=f.file,
                        kind=f.kind,
                        target=f.target,
                        bytes=len(f.data),
                        sha256=hashlib.sha256(f.data).hexdigest(),
                    )
                    for f in files
                ),
            )
            if server is not None
            else None
        ),
    )
    partial = dest.with_name(dest.name + ".partial")
    try:
        with zipfile.ZipFile(
            partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True
        ) as archive:
            archive.writestr(MANIFEST_NAME, manifest.model_dump_json(by_alias=True, indent=2))
            for dump, member in zip(dumps, members, strict=True):
                with (
                    dump.path.open("rb") as source,
                    archive.open(member.file, "w", force_zip64=True) as sink,
                ):
                    while chunk := source.read(_CHUNK):
                        sink.write(chunk)
            for packed in files:
                archive.writestr(packed.file, packed.data)
        read_package(partial)
        partial.replace(dest)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        if exc.errno == errno.ENOSPC:
            raise MovePackageError(
                "There is not enough free space where the file goes (it needs up to "
                f"{_megabytes(sum(m.bytes for m in members))} MB). Free some space or pick "
                "another folder, then pack again. Nothing was packed."
            ) from exc
        raise MovePackageError(
            f"Yu'lon could not write {dest.name}: {exc}. Nothing was packed."
        ) from exc
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return manifest


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


# ------------------------------------------------------------------- reading


@dataclass(frozen=True)
class Package:
    """A package that has been read and whose every member matched the manifest."""

    path: Path
    manifest: Manifest

    def member(self, schema: str) -> Member:
        for member in self.manifest.databases:
            if member.schema_name == schema:
                return member
        raise MovePackageError(f"{self.path.name} holds no {schema} database.")

    def files(self, kind: FileKind | None = None) -> tuple[FileMember, ...]:
        """The non-database members, of one kind or all, in the manifest's order."""
        if self.manifest.server is None:
            return ()
        return tuple(f for f in self.manifest.server.files if kind is None or f.kind == kind)

    def file(self, kind: FileKind, target: str) -> FileMember:
        for member in self.files(kind):
            if member.target == target:
                return member
        raise MovePackageError(f"{self.path.name} holds no {kind} file {target}.")

    def file_bytes(self, member: FileMember) -> bytes:
        """One small file member, checked again as it is read."""
        try:
            with zipfile.ZipFile(self.path) as archive, archive.open(member.file) as fh:
                # One byte more than the list says: a member that grew since it was checked is
                # refused without being read whole.
                data = fh.read(member.bytes + 1)
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            raise MovePackageError(_changed(self.path.name)) from exc
        if hashlib.sha256(data).hexdigest() != member.sha256 or len(data) != member.bytes:
            raise MovePackageError(_changed(self.path.name))
        return data

    def head(self, schema: str, size: int = 8192) -> bytes:
        """The first bytes of one dump, for reading its game record without extracting it."""
        member = self.member(schema)
        with zipfile.ZipFile(self.path) as archive, archive.open(member.file, mode="r") as fh:
            return fh.read(size)

    def extract(self, schema: str, folder: Path) -> Path:
        """Write one dump into `folder` as `<schema>.sql`, checking it again as it is written.

        The reader checked the member once, and a file can change between that and this, so
        the bytes that reach the disk are hashed on the way and a mismatch deletes them.
        """
        member = self.member(schema)
        target = folder / f"{member.schema_name}.sql"
        digest = hashlib.sha256()
        size = 0
        try:
            with zipfile.ZipFile(self.path) as archive:
                with archive.open(member.file, mode="r") as source, target.open("xb") as sink:
                    while chunk := source.read(_CHUNK):
                        digest.update(chunk)
                        size += len(chunk)
                        sink.write(chunk)
        except zipfile.BadZipFile as exc:
            target.unlink(missing_ok=True)
            raise MovePackageError(NOT_A_PACKAGE) from exc
        except OSError as exc:
            target.unlink(missing_ok=True)
            if exc.errno == errno.ENOSPC:
                raise MovePackageError(
                    f"There is not enough free space to unpack {self.path.name} (it needs "
                    f"{_megabytes(member.bytes)} MB). Free some space and try again. Nothing "
                    "was brought in."
                ) from exc
            raise MovePackageError(
                f"Yu'lon could not write {target.name} while unpacking {self.path.name}: {exc}. "
                "Nothing was brought in."
            ) from exc
        if digest.hexdigest() != member.sha256 or size != member.bytes:
            target.unlink(missing_ok=True)
            raise MovePackageError(_changed(self.path.name))
        return target


def _megabytes(size: int) -> int:
    """Whole megabytes, rounded up, for a sentence about space."""
    return max(1, -(-size // (1024 * 1024)))


def _changed(name: str) -> str:
    return (
        f"{name} does not match the list inside it (it is damaged, or it changed while it was "
        "being read), so nothing was brought in. Copy it over again, or pack it again."
    )


def _unsafe_name(name: str) -> bool:
    """A member name a zip can use to write somewhere it was not asked to.

    Checked as Windows reads it too, because a package travels between systems: a `:` in ANY
    part (`sub/C:../x` is drive-relative to `C:` once joined there), a trailing dot or space
    (Windows drops them, so `x.` and `x` are one file), a device name (`CON`, `NUL.txt`), a
    backslash, and every control character.
    """
    if not name or name.startswith(("/", "\\")) or "\\" in name or _DRIVE.match(name):
        return True
    if "\x00" in name:
        return True
    for part in name.split("/"):
        if part in ("..", ".", ""):
            return True
        if ":" in part or any(ord(c) < 32 or ord(c) == 127 for c in part):
            return True
        if part.endswith((".", " ")) or _DEVICE.match(part.split(".", 1)[0].rstrip(" ")):
            return True
    return False


def read_package(path: Path) -> Package:
    """Open a package, check it end to end, and return it, or raise `MovePackageError`.

    Order matters. The names are checked before anything is read, the format before the
    rest of the manifest, and every member's length and SHA-256 before the caller is given
    anything: an import never touches a server on the word of a file it has not verified.
    """
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise MovePackageError(NOT_A_PACKAGE) from exc
    with archive:
        names = archive.namelist()
        if any(_unsafe_name(n) for n in names):
            raise MovePackageError(
                f"{path.name} holds a file whose name could write outside the folder it is "
                "unpacked into, so Yu'lon will not open it."
            )
        if len(set(names)) != len(names):
            raise MovePackageError(
                f"{path.name} holds two files of the same name, so it cannot be trusted."
            )
        if MANIFEST_NAME not in names:
            raise MovePackageError(NOT_A_PACKAGE)
        try:
            raw = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            raise MovePackageError(NOT_A_PACKAGE) from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("format"), int):
            raise MovePackageError(NOT_A_PACKAGE)
        if raw["format"] > FORMAT:
            made_by = raw.get("made_by")
            version = made_by.get("yulon") if isinstance(made_by, dict) else None
            said = f" ({version})" if isinstance(version, str) else ""
            raise MovePackageError(
                f"This file was made by a newer Yu'lon{said}. Update Yu'lon on this computer, "
                "then try again."
            )
        try:
            manifest = Manifest.model_validate(raw)
        except ValidationError as exc:
            logger.info(f"{path.name}: manifest rejected: {exc}")
            raise MovePackageError(NOT_A_PACKAGE) from exc
        extra = manifest.server.files if manifest.server is not None else ()
        listed = {m.file for m in manifest.databases} | {f.file for f in extra}
        if len({m.schema_name for m in manifest.databases}) != len(manifest.databases):
            raise MovePackageError(NOT_A_PACKAGE)
        if len({f.file for f in extra}) != len(extra):
            raise MovePackageError(NOT_A_PACKAGE)
        stray = sorted(set(names) - listed - {MANIFEST_NAME})
        if stray:
            raise MovePackageError(
                f"{path.name} holds a file its list does not name ({stray[0]}), so Yu'lon "
                "will not open it."
            )
        checked: list[tuple[str, str, int]] = [
            (m.file, m.sha256, m.bytes) for m in manifest.databases
        ] + [(f.file, f.sha256, f.bytes) for f in extra]
        if any(f.bytes > MAX_FILE_MEMBER_BYTES for f in extra):
            raise MovePackageError(NOT_A_PACKAGE)
        for file, sha256, length in checked:
            if file not in names:
                raise MovePackageError(
                    f"{path.name} is missing {file}, which its list names, so nothing "
                    "was brought in."
                )
            try:
                with archive.open(file, mode="r") as fh:
                    digest, size = _hash_stream(fh, limit=length)
            except (OSError, zipfile.BadZipFile) as exc:
                raise MovePackageError(_changed(path.name)) from exc
            if digest != sha256 or size != length:
                raise MovePackageError(_changed(path.name))
    return Package(path=path, manifest=manifest)


def _hash_stream(fh: IO[bytes], limit: int | None = None) -> tuple[str, int]:
    """SHA-256 and length of a stream; with `limit`, it stops one byte past it (a lying header)."""
    digest = hashlib.sha256()
    size = 0
    while chunk := fh.read(_CHUNK):
        digest.update(chunk)
        size += len(chunk)
        if limit is not None and size > limit:
            break
    return digest.hexdigest(), size


# ------------------------------------------------------------------- the checks


def refuse_another_game(manifest: Manifest, target_id: str, target_name: str) -> None:
    """Characters go into a server of the same game and no other (T603's rule, for a package)."""
    if manifest.game.id != target_id:
        raise MovePackageError(
            f"This file holds {manifest.game.name} characters; this server is {target_name}. "
            "Characters can only go into a server of the same game."
        )


def unlabeled_dump(name: str) -> str:
    return (
        f"{name} inside this package does not say which game it is from, so Yu'lon cannot "
        "tell that it belongs in this server and will not bring it in. Pack the accounts and "
        "characters again with Yu'lon on the old computer."
    )


def dump_from_another_game(name: str, found: str, target_name: str) -> str:
    return (
        f"{name} inside this package is from {found}, not from {target_name}, though the "
        "package says otherwise. Yu'lon will not bring it in."
    )


def version_difference(
    package: Mapping[str, Evidence],
    here: Mapping[str, Evidence | None],
    optional: frozenset[str] = frozenset(),
) -> str | None:
    """A sentence if the package's databases are not at this server's version, else None.

    Equal evidence is the only thing that lets an import go ahead: Yu'lon cannot convert
    characters between database versions, and a start of this app never runs the core's own
    import (`docker.start_staged` leaves it out on purpose), so data one step behind would
    be run on a newer core as it is. `here` holds `None` for a schema that could not be
    asked, which refuses too: an unreadable version is not a matching one.

    The module rows of a ledger are compared apart, after the core ones: the load replaces the
    whole ledger table, so a target with a module the file lacks would lose that module's row
    while its tables stayed, and its next database update would run the module's SQL again.
    Equal module sets make the replacement harmless, so equal is the only way through.

    `optional` names schemas that may have no ledger at all (playerbots, the Lua engine). For
    those a missing record is a state, not an error: none on both sides matches, and a record
    on one side only is a difference, named as that.
    """
    differences: list[str] = []
    for schema in sorted(set(package) | set(optional)):
        theirs = package.get(schema)
        ours = here.get(schema)
        if schema in optional:
            if theirs is None and ours is None:
                continue
            if theirs is None:
                differences.append(
                    f"{schema}: this server has an update record and the file has none"
                )
                continue
            if ours is None:
                differences.append(
                    f"{schema}: the file has an update record and this server has none"
                )
                continue
        elif ours is None:
            differences.append(f"{schema}: this server's version could not be read")
            continue
        elif theirs is None:  # pragma: no cover - the loop covers package keys only
            continue
        if ours.kind != theirs.kind or ours.digest != theirs.digest:
            differences.append(
                f"{schema}: {theirs.count} {_unit(theirs.kind)} in the file, {ours.count} here"
            )
    if differences:
        return (
            "The databases in this file are not at the same version as this server's "
            f"({'; '.join(differences)}), and Yu'lon cannot convert characters between versions. "
            "Put both servers on the same version (use "
            f"{under_server_build(UPDATE_TO_LATEST)} on the one that is behind, and pack again if "
            "it was the old one), then try again."
        )
    return _module_difference(package, here)


def _module_difference(
    package: Mapping[str, Evidence], here: Mapping[str, Evidence | None]
) -> str | None:
    """The sentence for a package whose module rows are not this server's, else None."""
    sentences: list[str] = []
    for schema, theirs in sorted(package.items()):
        ours = here.get(schema)
        if ours is None or set(ours.modules) == set(theirs.modules):
            continue
        only_theirs = set(theirs.modules) - set(ours.modules)
        only_ours = set(ours.modules) - set(theirs.modules)
        sentences.append(
            "This package was made on a server with "
            f"{_module_names(only_theirs) or 'no module this one lacks'}, this one has "
            f"{_module_names(only_ours) or 'no module the package lacks'}: install the same "
            "modules first, or move the whole server (level 2)."
        )
    return " ".join(sentences) or None


def _module_names(rows: set[str]) -> str:
    return ", ".join(sorted({row.partition("|")[0] for row in rows}))


def _unit(kind: str) -> str:
    return {"updates": "updates", "migrations": "migrations", "db_version": "version marks"}.get(
        kind, "version marks"
    )
