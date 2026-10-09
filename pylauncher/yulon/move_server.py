"""Packing a WHOLE server, and building it again on another computer from it (T601 level 2).

Level 1 (`move_flows`) moves accounts and characters into a server that already exists. Level 2
moves the server: every database (world included, so custom content and objects a GM placed come
along), the conf files with the player's Tuning, the module answers, the modules themselves (as
the repository and commit each clone was on, never as built files), the derived manifests of
modules added from a link, and the player's own Lua scripts. What a new install makes for
itself never travels: built images, map data, logs, backups, the client, the database password,
the channel credential and every `.yulon-*` record (the folder id among them: a copy must make its
own, T568).

Importing is a NEW install, never a load into an existing one: the catalog entry is copied with
each source's `rev` set to the packed commit (`pinned_entry`), the normal install engine runs on
that copy, and the server it builds is then given the modules, the confs and the data. The owner's
decision (2026-10-09): install at the PACKED version, then let the Server tab offer Update or
Return to the tested pin (`moved_in_revs`).

This file is the pure part and the pack. `MovedInInstall` (further down) is the import.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from yulon import apply, module_answers, move
from yulon.catalog.catalog import (
    LUA_SCRIPTS_DIR,
    CatalogEntry,
    ConfPatch,
    ConfPatchTable,
)
from yulon.catalog.families import conf as conf_patch
from yulon.catalog.git_head import read_head_file
from yulon.log import get_logger
from yulon.manifest import Manifest, ManifestType
from yulon.move import PackedModule, PackedSource, PackFile, ServerFacts, ServerSpec
from yulon.move_flows import MoveError, MoveWorld
from yulon.support import redact
from yulon.support.sources import conf_files

logger = get_logger(__name__)

PASSWORD_PLACEHOLDER = "{{DB_PASSWORD}}"
"""What a packed conf holds where the database password was; the new install's goes back there."""

_NOT_PACKED = "Nothing was packed."

_CLONE_FOLDERS: dict[str, tuple[ManifestType, ...]] = {}
for _kind, _folder in apply.CLONE_DIRS.items():
    _CLONE_FOLDERS.setdefault(_folder, ())
    _CLONE_FOLDERS[_folder] = (*_CLONE_FOLDERS[_folder], _kind)
"""Clone folder -> the manifest types whose clones live there (`ale` and `keg` share one)."""


# --------------------------------------------------------------- what a server is built from


def _sources_of(
    entry: CatalogEntry, server_dir: Path
) -> tuple[tuple[PackedSource, ...], list[str]]:
    refusals: list[str] = []
    found: list[PackedSource] = []
    for source in entry.emulator.sources:
        commit = read_head_file(server_dir / source.dest)
        if commit is None or not re.fullmatch(r"[0-9a-f]{40}", commit):
            refusals.append(
                f"Yu'lon cannot read which commit {source.repo} in {source.dest} is on, so the "
                f"new computer could not build the same server. {_NOT_PACKED}"
            )
            continue
        found.append(
            PackedSource(repo=source.repo, dest=source.dest, commit=commit, catalog_pin=source.rev)
        )
    return tuple(found), refusals


def _modules_of(
    entry: CatalogEntry,
    server_dir: Path,
    load_manifest: Callable[[ManifestType, str], Manifest | None],
) -> tuple[tuple[PackedModule, ...], list[PackFile], list[str]]:
    """Every module installed from a clone: its manifest, its commit, and a link's manifest file.

    The clone folders are read rather than a list kept anywhere, because the folder is what
    `apply.installed_clones` calls installed. A folder the server build itself clones into
    (`mod-playerbots` on WotLK) is the server's source, not a module, and is skipped. Sourceless
    and settings-only mods leave no folder: they live in the databases and the confs, which
    travel, and in the answers file, which travels too.
    """
    from yulon.catalog import native

    builds_into = native.server_source_folders(entry)
    modules: list[PackedModule] = []
    files: list[PackFile] = []
    refusals: list[str] = []
    for folder, kinds in sorted(_CLONE_FOLDERS.items()):
        ids = sorted(apply.installed_clones(server_dir).get(str(kinds[0]), frozenset()))
        for item_id in ids:
            if PurePosixPath(folder) / item_id in builds_into:
                continue
            candidates = [m for m in (load_manifest(kind, item_id) for kind in kinds) if m]
            if len(candidates) != 1:
                refusals.append(
                    f"{folder}/{item_id} is a module folder Yu'lon cannot name (no description of "
                    "it, or more than one), so the new computer could not install it again. "
                    f"Remove it, or add it again on the Modules tab, then pack again. {_NOT_PACKED}"
                )
                continue
            manifest = candidates[0]
            if manifest.origin is not None and manifest.origin.kind == "folder":
                refusals.append(
                    f"{manifest.name} was added from a folder on this computer, so there is "
                    "nothing the new computer could fetch it from again. Remove it, or add it "
                    f"from a link instead, then pack again. {_NOT_PACKED}"
                )
                continue
            if manifest.source is None:
                refusals.append(
                    f"{manifest.name} has a folder but no repository to fetch it from again. "
                    f"{_NOT_PACKED}"
                )
                continue
            commit = read_head_file(server_dir / folder / item_id)
            if commit is None or not re.fullmatch(r"[0-9a-f]{40}", commit):
                refusals.append(
                    f"Yu'lon cannot read which commit {manifest.name} is on, so the new computer "
                    f"could not install the same version. {_NOT_PACKED}"
                )
                continue
            origin = "link" if manifest.origin is not None else "catalog"
            modules.append(
                PackedModule(
                    type=manifest.type,
                    id=manifest.id,
                    origin=origin,
                    repo=manifest.source.repo,
                    commit=commit,
                )
            )
            if origin == "link":
                files.append(
                    PackFile(
                        kind="manifest",
                        target=f"{manifest.type}/{manifest.id}",
                        data=manifest.model_dump_json(indent=2).encode("utf-8"),
                    )
                )
    return tuple(modules), files, refusals


def _relative(path: Path, server_dir: Path) -> str:
    return path.relative_to(server_dir).as_posix()


def _conf_members(server_dir: Path) -> list[PackFile]:
    """Every live `*.conf` under the conf folders, with each database password taken out.

    `support.sources.conf_files`: never a `.dist`, never a file reached through a link (a link
    could bring any file on this machine into the package). A conf that is not UTF-8 is packed
    as it is only if it has no database line to clean, which a decode is needed to know: so it
    is refused instead.
    """
    members: list[PackFile] = []
    for path in conf_files(server_dir):
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MoveError(
                f"{_relative(path, server_dir)} is not a text file Yu'lon can read, so it could "
                f"not take the database password out of it. {_NOT_PACKED}"
            ) from exc
        cleaned = redact.database_info_passwords_replaced(text, PASSWORD_PLACEHOLDER)
        members.append(
            PackFile(kind="conf", target=_relative(path, server_dir), data=cleaned.encode("utf-8"))
        )
    return members


def _lua_members(server_dir: Path) -> list[PackFile]:
    """The files in the Lua engine's script folder, except the ones Yu'lon lays itself.

    `scriptdeploy`'s record names what an install or rebuild writes there, and those come back
    by themselves. The rest is the player's own and the modules' copies; a module's copy is laid
    again by its install, so laying the packed one over it changes nothing unless the player
    edited it, which is the edit that should travel. No link is followed, and no dot-file (the
    records) is packed.
    """
    from yulon.catalog.families import scriptdeploy

    folder = server_dir / LUA_SCRIPTS_DIR
    if not folder.is_dir() or any(
        (server_dir / part).is_symlink() for part in _prefixes(PurePosixPath(LUA_SCRIPTS_DIR))
    ):
        return []
    laid = set(scriptdeploy.read_record(server_dir))
    members: list[PackFile] = []
    for root, dirs, names in os.walk(folder, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs if not d.startswith(".") and not (Path(root) / d).is_symlink()
        )
        for name in sorted(names):
            path = Path(root) / name
            if name.startswith(".") or path.is_symlink() or not path.is_file():
                continue
            target = _relative(path, server_dir)
            if target in laid:
                continue
            members.append(PackFile(kind="lua", target=target, data=path.read_bytes()))
    return members


def _prefixes(path: PurePosixPath) -> Iterator[str]:
    """`a`, `a/b`, `a/b/c` for `a/b/c`: every folder on the way, for the link check."""
    parts = path.parts
    for index in range(1, len(parts) + 1):
        yield "/".join(parts[:index])


def _answers_member(server_dir: Path) -> list[PackFile]:
    path = server_dir / module_answers.ANSWERS_FILE
    if not path.is_file() or path.is_symlink():
        return []
    return [PackFile(kind="answers", target=move.ANSWERS_TARGET, data=path.read_bytes())]


def gather_server_facts(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    load_manifest: Callable[[ManifestType, str], Manifest | None],
    secret_password: str | None,
) -> ServerFacts:
    """Read everything but the databases a whole-server package needs, or refuse with every reason.

    Read before the server is stopped, so a refusal stops nothing. `secret_password` is this
    install's database password when it is a secret (a generated one); a fixed, published one
    (WotLK's `password`) is None, since finding an English word in a file proves nothing. After
    the password fields are taken out of the confs, a secret found in ANY packed file refuses
    the pack: fail closed, never a package that carries it.
    """
    sources, refusals = _sources_of(entry, server_dir)
    modules, manifest_files, module_refusals = _modules_of(entry, server_dir, load_manifest)
    refusals.extend(module_refusals)
    if refusals:
        raise MoveError(" ".join(refusals))
    try:
        files = (
            *_conf_members(server_dir),
            *_answers_member(server_dir),
            *manifest_files,
            *_lua_members(server_dir),
        )
    except OSError as exc:
        raise MoveError(f"Yu'lon could not read the server's files: {exc}. {_NOT_PACKED}") from exc
    if secret_password:
        needle = secret_password.encode("utf-8")
        for packed in files:
            if needle in packed.data:
                raise MoveError(
                    f"The database password is still in {packed.target} after Yu'lon took it out "
                    "of the database lines, so nothing was packed: it must never leave this "
                    "computer. Take it out of that file, then pack again."
                )
    return ServerFacts(spec=ServerSpec(sources=sources, modules=modules), files=tuple(files))


# --------------------------------------------------------------- the pure helpers of an import


def source_difference(entry: CatalogEntry, packed: Sequence[PackedSource]) -> str | None:
    """A sentence when the package's sources are not this entry's (same repository, same folder)."""
    ours = {(s.repo.lower(), s.dest) for s in entry.emulator.sources}
    theirs = {(s.repo.lower(), s.dest) for s in packed}
    if ours == theirs:
        return None
    only_theirs = sorted(f"{repo} in {dest}" for repo, dest in theirs - ours)
    only_ours = sorted(f"{repo} in {dest}" for repo, dest in ours - theirs)
    return (
        f"This Yu'lon builds {entry.name} from other sources than the old computer did "
        f"(the file has {', '.join(only_theirs) or 'nothing more'}; this Yu'lon has "
        f"{', '.join(only_ours) or 'nothing more'}), so it cannot build the same server. "
        "Install the same Yu'lon version on both computers, then pack again."
    )


def pinned_entry(entry: CatalogEntry, packed: Sequence[PackedSource]) -> CatalogEntry:
    """`entry` with each source's `rev` set to the commit the packed server was built from.

    A copy, so every stage of the install reads ONE entry (the clone, the carried patches, the
    compose files); the catalog itself is not touched. Raises `MoveError` when the sources are
    not this entry's, which `source_difference` says in a sentence.
    """
    said = source_difference(entry, packed)
    if said:
        raise MoveError(said)
    commits = {(s.repo.lower(), s.dest): s.commit for s in packed}
    sources = tuple(
        source.model_copy(update={"rev": commits[(source.repo.lower(), source.dest)]})
        for source in entry.emulator.sources
    )
    return entry.model_copy(
        update={"emulator": entry.emulator.model_copy(update={"sources": sources})}
    )


def moved_in_revs(
    entry: CatalogEntry,
    packed: Sequence[PackedSource],
    server_dir: Path,
    *,
    head_version: Callable[[Path], str | None],
    commits_since: Callable[[Path, str], int | None],
) -> tuple[SourceRevRow, ...]:
    """The install record's rows for a server built at the packed commits (owner decision 3).

    Written so the Server tab's existing reading (`native._against_the_catalog`, T588) offers the
    right press without knowing a move happened:

    * a source ON this catalog's pin: no row (an install on its pins has none);
    * a source BEHIND the pin (the pin has every commit the checkout has): a row whose recorded
      pin is the packed commit. That is the shape an install left by an older Yu'lon has, and
      the tab reads it as "the tested commit moved since this server was built" and offers the
      catch-up onto it;
    * a source PAST the pin, or one whose distance cannot be counted (a shallow clone): a row
      against this catalog's pin with the count, which reads as "past the tested pin" and offers
      Return, with its warning that nothing undoes what the newer server wrote. Cannot-count goes
      this way because it is the reading that warns.

    A checkout whose version cannot be read gets no row; the tab then reads its HEAD.
    """
    commits = {(s.repo.lower(), s.dest): s.commit for s in packed}
    rows: list[SourceRevRow] = []
    for source in entry.emulator.sources:
        commit = commits.get((source.repo.lower(), source.dest))
        pin = source.rev
        if commit is None or not pin or commit == pin:
            continue
        dest = server_dir / source.dest
        built = head_version(dest)
        if built is None:
            logger.warning(f"could not read what {dest} was built from; no row for {source.repo}")
            continue
        ahead = commits_since(dest, pin)
        if ahead == 0:
            rows.append(SourceRevRow(repo=source.repo, built=built, pin=commit, ahead=0))
        else:
            rows.append(SourceRevRow(repo=source.repo, built=built, pin=pin, ahead=ahead))
    return tuple(rows)


@dataclass(frozen=True)
class SourceRevRow:
    """`native.SourceRev`'s four facts, without importing the engine into the pure part."""

    repo: str
    built: str
    pin: str
    ahead: int | None


_ACTIVE_KEY = re.compile(r"^(?P<key>[A-Za-z0-9_.]+)\s*=\s*(?P<value>.*?)\s*$")
_DATABASE_INFO_KEY = re.compile(r"^\w*Database\.?Info$")


def machine_keys(entry: CatalogEntry, target: str) -> frozenset[str]:
    """The keys of conf `target` whose value the install fills from THIS machine (`{{TOKEN}}`s).

    From the catalog's conf table: database hosts, users and passwords, the world port. A key
    the table sets to a literal (the bot count, SOAP's port) is not one: the player may have
    changed it on the Tuning tab, and that change travels.
    """
    table = _conf_table(entry)
    if table is None:
        return frozenset()
    name = PurePosixPath(target)
    keys: set[str] = set()
    for file, patch in table.files.items():
        if name.as_posix().endswith("/" + file) or name.as_posix() == file:
            keys.update(k for k, raw in patch.keys.items() if "{{" in raw)
    return frozenset(keys)


def _conf_table(entry: CatalogEntry) -> ConfPatchTable | None:
    native_block = entry.install.native
    if native_block is None:
        return None
    if native_block.cmangos is not None:
        return native_block.cmangos.conf
    if native_block.trinitycore is not None:
        return native_block.trinitycore.conf
    return None


def _last_values(text: str) -> dict[str, str]:
    """Every active key's LAST value (the one the server obeys), line endings stripped."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        match = _ACTIVE_KEY.match(line)
        if match is not None:
            found[match.group("key")] = match.group("value")
    return found


def lay_conf(
    packed: bytes, installed: bytes | None, keys: Iterable[str], password: str | None
) -> bytes:
    """The packed conf as it goes onto this machine: its bytes, with this machine's own values.

    The password placeholder becomes this install's database password, and every key in `keys`
    (`machine_keys`) and every `*DatabaseInfo` key takes the value the install just wrote here,
    when it wrote one. Everything else, line endings included, is the packed file's. Raises
    `MoveError` when the file holds a placeholder and there is no password to put in it.
    """
    try:
        text = packed.decode("utf-8")
        here = installed.decode("utf-8") if installed is not None else ""
    except UnicodeDecodeError as exc:
        raise MoveError(f"A conf file is not text Yu'lon can read: {exc}") from exc
    if PASSWORD_PLACEHOLDER in text:
        if password is None:
            raise MoveError(
                "A packed conf file needs this server's database password, and Yu'lon could not "
                "read it. Nothing more was changed."
            )
        text = text.replace(PASSWORD_PLACEHOLDER, password)
    values = _last_values(here)
    wanted = set(keys) | {k for k in values if _DATABASE_INFO_KEY.match(k)}
    taken = {k: values[k] for k in sorted(wanted) if k in values}
    if taken:
        text = conf_patch.patch(text, ConfPatch(keys=_literal_values(taken)), {})
    return text.encode("utf-8")


def _literal_values(values: Mapping[str, str]) -> dict[str, str]:
    """Values for `conf.patch`, which fills `{{TOKEN}}`s: a literal `{{` is not one of ours."""
    for key, value in values.items():
        if "{{" in value:
            raise MoveError(f"This server's own {key} holds '{{{{', which Yu'lon cannot copy.")
    return dict(values)


def package_digest(manifest: move.Manifest) -> str:
    """One package's identity: the SHA-256 of its manifest, which hashes every member."""
    return hashlib.sha256(manifest.model_dump_json(by_alias=True).encode("utf-8")).hexdigest()


# --------------------------------------------------------------- the import: its plan

MOVE_IN_FILE = ".yulon-move-in.json"
"""In the new server's folder: which package it is being built from, and which steps are done.

What lets a press that stopped part-way (after a two-hour compile) be pressed again on the same
folder and finish, and what tells that folder from somebody's server, which is never a target.
"""

STEPS = ("revs", "answers", "modules", "rebuild", "confs", "data")
"""The steps after the install, in order; each is recorded in `MOVE_IN_FILE` when it is done."""

KIND_CHARACTERS = (
    "This file holds accounts and characters only, not a whole server. Install the game from "
    "the Catalog first, then bring them in on its Maintenance tab (Move to another computer)."
)


def unknown_game(name: str) -> str:
    return (
        f"This file holds a {name} server, a game this Yu'lon does not have. Update Yu'lon on "
        "this computer, then try again."
    )


def gone_from_github(commit: str, repo: str, game: str) -> str:
    return (
        f"The commit this server was built from ({commit[:7]} of {repo}) is no longer on "
        f"GitHub, so it cannot be built again exactly. Install {game} fresh and bring the "
        "accounts and characters in instead (Pack accounts and characters… on the old computer)."
    )


def holds_a_server(folder: Path) -> str:
    return (
        f"{folder} already holds a server. Bringing a server from another computer always makes "
        "a new one, so pick an empty folder."
    )


def not_empty(folder: Path) -> str:
    return f"{folder} is not empty. Pick an empty folder, or one that does not exist yet."


@dataclass(frozen=True)
class ModuleLookup:
    """This computer's module descriptions for one game: the Modules tab's store, two questions."""

    load: Callable[[ManifestType, str], Manifest | None]
    shipped: Callable[[ManifestType, str], bool]


@dataclass(frozen=True)
class ModuleToInstall:
    packed: PackedModule
    manifest: Manifest
    """The description, with its source pinned at the packed commit."""
    carried: Manifest | None = None
    """A link module's own description, to put in this computer's user layer first."""


@dataclass(frozen=True)
class ServerImportPlan:
    """What bringing a whole server in would do, read without changing anything."""

    path: Path
    manifest: move.Manifest | None
    refusals: tuple[str, ...]
    entry: CatalogEntry | None = None
    pinned: CatalogEntry | None = None
    modules: tuple[ModuleToInstall, ...] = ()
    server_dir: Path | None = None
    resuming: bool = False
    notes: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return not self.refusals

    def text(self) -> str:
        """What the dialog says before the player says yes."""
        if not self.allowed or self.manifest is None or self.entry is None:
            return "\n".join(self.refusals)
        m = self.manifest
        made = m.made_by.made[:16].replace("T", " ")
        lines = [
            f"A {m.game.name} server packed on {made} by Yu'lon {m.made_by.yulon}"
            + (f", realm {m.realm_name}" if m.realm_name else "")
            + f": {m.counts.accounts} accounts, {m.counts.characters} characters, "
            f"{m.counts.bot_accounts} bot accounts.",
            f"It is installed as a NEW server in {self.server_dir}, built at the version it was "
            "packed from, then its modules, settings and databases are put in. Building takes as "
            "long as any install.",
            *self.notes,
            *(
                [f"Modules installed again: {', '.join(t.manifest.name for t in self.modules)}."]
                if self.modules
                else []
            ),
            "Not brought: the game client, Steam entries, the network setting (this computer's "
            "own is used) and Yu'lon's command-channel account, which Repair makes again after "
            "the first Start.",
            m.secrets,
        ]
        if self.resuming:
            lines.insert(
                1, "This folder holds an unfinished move of this same file: it carries on."
            )
        return "\n".join(lines)


def _module_plans(
    package: move.Package, lookup: ModuleLookup
) -> tuple[list[ModuleToInstall], list[str]]:
    server = package.manifest.server
    if server is None:
        return [], []
    plans: list[ModuleToInstall] = []
    refusals: list[str] = []
    for packed in server.modules:
        carried: Manifest | None = None
        if packed.origin == "catalog":
            manifest = lookup.load(packed.type, packed.id)
            if manifest is None or manifest.origin is not None:
                refusals.append(
                    f"{packed.id} is not among this Yu'lon's modules, so it cannot be installed "
                    "again here. Update Yu'lon on this computer, or remove the module on the old "
                    "one and pack again."
                )
                continue
        else:
            if lookup.shipped(packed.type, packed.id):
                refusals.append(
                    f"{packed.id} was added from a link on the old computer, and this Yu'lon ships "
                    "a module of that name, so the two cannot be told apart. Remove it on the old "
                    "computer and pack again."
                )
                continue
            try:
                member = package.file("manifest", f"{packed.type}/{packed.id}")
                manifest = Manifest.model_validate_json(package.file_bytes(member))
            except (move.MovePackageError, ValueError) as exc:
                logger.info(f"the carried description of {packed.id} is not usable: {exc}")
                refusals.append(
                    f"The package's description of {packed.id} is missing or damaged. "
                    "Pack again on the old computer."
                )
                continue
            if (
                manifest.id != packed.id
                or manifest.type != packed.type
                or manifest.origin is None
                or manifest.origin.kind != "link"
            ):
                refusals.append(
                    f"The package's description of {packed.id} is missing or damaged. "
                    "Pack again on the old computer."
                )
                continue
            carried = manifest
        if manifest.source is None or manifest.source.repo.lower() != packed.repo.lower():
            refusals.append(
                f"{manifest.name} comes from {packed.repo} in the package, and from "
                f"{manifest.source.repo if manifest.source else 'no repository'} in this Yu'lon, "
                "so the packed version cannot be installed. Update Yu'lon on both computers to the "
                "same version, then pack again."
            )
            continue
        pinned_source = manifest.source.model_copy(
            update={"rev": packed.commit, "follow": "branch"}
        )
        plans.append(
            ModuleToInstall(
                packed=packed,
                manifest=manifest.model_copy(update={"source": pinned_source}),
                carried=carried,
            )
        )
    return plans, refusals


def read_marker(server_dir: Path) -> dict[str, object] | None:
    """The move-in record of `server_dir`, or None when there is none that can be read."""
    try:
        parsed = json.loads((server_dir / MOVE_IN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("package"), str):
        return None
    done = parsed.get("done")
    parsed["done"] = [d for d in done if isinstance(d, str)] if isinstance(done, list) else []
    return parsed


def write_marker(server_dir: Path, digest: str, done: Sequence[str], **more: object) -> None:
    body = {"version": 1, "package": digest, "done": list(done), **more}
    target = server_dir / MOVE_IN_FILE
    partial = target.with_name(target.name + ".partial")
    partial.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, target)


def folder_refusal(server_dir: Path, digest: str) -> tuple[str | None, bool]:
    """`(refusal, resuming)` for the folder a whole server would be built in.

    A folder that is not there, or is empty, is a new install. One that holds a move-in record
    of THIS package is that move, unfinished, and carries on. Anything else is refused: an
    existing Yu'lon server above all, whose databases a move would otherwise replace.
    """
    if not server_dir.exists():
        return None, False
    if not server_dir.is_dir():
        return not_empty(server_dir), False
    marker = read_marker(server_dir)
    if marker is not None and marker.get("package") == digest:
        return None, True
    from yulon.catalog import native

    if (server_dir / native.STATE_FILE).exists():
        return holds_a_server(server_dir), False
    if any(server_dir.iterdir()):
        return not_empty(server_dir), False
    return None, False


def plan_server_import(
    path: Path,
    *,
    catalog: object,
    server_dir_for: Callable[[CatalogEntry], Path],
    platform_id: str,
    lookup_for: Callable[[CatalogEntry], ModuleLookup],
    commit_known: Callable[[str, str], bool | None],
) -> ServerImportPlan:
    """Read a whole-server package and say what building it here would do. Nothing is written.

    `server_dir_for` names the folder (the Catalog's default for that game, or the player's
    pick); `commit_known(repo, sha)` asks GitHub whether a packed commit still exists, None when
    it cannot say (asked last, and only when nothing else refused).
    """
    from yulon.catalog.installer import unsupported_platform_message
    from yulon.move_flows import record_refusals

    try:
        package = move.read_package(path)
    except move.MovePackageError as exc:
        return ServerImportPlan(path=path, manifest=None, refusals=(str(exc),))
    manifest = package.manifest
    if manifest.kind != "server" or manifest.server is None:
        return ServerImportPlan(path=path, manifest=manifest, refusals=(KIND_CHARACTERS,))
    try:
        entry: CatalogEntry = catalog.get(manifest.game.id)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - an id this build has no entry for
        return ServerImportPlan(
            path=path, manifest=manifest, refusals=(unknown_game(manifest.game.name),)
        )
    if not entry.install.supports(platform_id):
        return ServerImportPlan(
            path=path,
            manifest=manifest,
            entry=entry,
            refusals=(unsupported_platform_message(entry, platform_id),),
        )
    refusals = record_refusals(entry.id, entry.name, package)
    schemas = entry.schema_map()
    held = {m.role: m.schema_name for m in manifest.databases}
    for role in ("auth", "characters", "world"):
        if role not in held:
            refusals.append(
                f"This package does not hold the {role} database, so it is not a whole server."
            )
    for role, schema in held.items():
        if schemas.get(role) != schema:
            refusals.append(
                f"This package holds {schema} as its {role} database, which is not what a "
                f"{entry.name} server calls it."
            )
    pinned: CatalogEntry | None = None
    said = source_difference(entry, manifest.server.sources)
    if said:
        refusals.append(said)
    else:
        pinned = pinned_entry(entry, manifest.server.sources)
    modules, module_refusals = _module_plans(package, lookup_for(entry))
    refusals.extend(module_refusals)
    server_dir = server_dir_for(entry)
    refusal, resuming = folder_refusal(server_dir, package_digest(manifest))
    if refusal:
        refusals.append(refusal)
    if not refusals:
        for source in manifest.server.sources:
            if (
                source.commit != source.catalog_pin
                and commit_known(source.repo, source.commit) is False
            ):
                refusals.append(gone_from_github(source.commit, source.repo, entry.name))
        for planned in modules:
            if commit_known(planned.packed.repo, planned.packed.commit) is False:
                refusals.append(
                    gone_from_github(planned.packed.commit, planned.packed.repo, entry.name)
                )
    return ServerImportPlan(
        path=path,
        manifest=manifest,
        refusals=tuple(refusals),
        entry=entry,
        pinned=pinned,
        modules=tuple(modules),
        server_dir=server_dir,
        resuming=resuming,
        notes=_version_notes(entry, manifest.server.sources),
    )


def _version_notes(entry: CatalogEntry, packed: Sequence[PackedSource]) -> tuple[str, ...]:
    pins = {(s.repo.lower(), s.dest): s.rev for s in entry.emulator.sources}
    off = [
        f"{s.repo.rsplit('/', 1)[-1]} {s.commit[:7]} "
        f"(tested: {(pins.get((s.repo.lower(), s.dest)) or '?')[:7]})"
        for s in packed
        if pins.get((s.repo.lower(), s.dest)) != s.commit
    ]
    if not off:
        return ("It was built from the same version this Yu'lon is tested with.",)
    return (
        "It was built from a version this Yu'lon was not tested with: "
        + ", ".join(off)
        + ". It is built at that version all the same, so its data fits; afterwards the Server "
        "tab offers to move it onto the tested version.",
    )


# --------------------------------------------------------------- the import: the run


class ModuleApplier(Protocol):
    """The Modules tab's applier, as far as a move needs it (`apply.Applier`)."""

    def install(
        self, manifest: Manifest, values: Mapping[str, str] | None = None
    ) -> apply.ApplyReport: ...


@dataclass(frozen=True)
class MovedInServer:
    """The new server once it is installed: what the steps after the install act through.

    Built by the wiring from the same `ControllerServices` its tab gets, so a module goes in
    through the Modules tab's applier and the data through the Maintenance tab's engine.
    """

    world: MoveWorld
    applier: ModuleApplier | None
    rebuild: Callable[[threading.Event | None], Iterator[str]] | None
    db_password: str | None
    persist_manifest: Callable[[Manifest], None]
    installed: Callable[[], Mapping[str, frozenset[str]]]
    """`apply.installed_modules` of the new server, for a press that carries on."""


class MovedInInstall:
    """The engine a Catalog tile runs to build a server from a whole-server package (T601 level 2).

    Shaped like the install engine (`preflight`, `run`), so the tile's own install machinery runs
    it, remembers the server when it ends, and shows its lines in the log. `run` is the normal
    install of the PINNED entry (`plan.pinned`), then the steps after it, each recorded in
    `MOVE_IN_FILE` when done, so a second press on the same folder carries on where it stopped.
    """

    def __init__(
        self,
        plan: ServerImportPlan,
        *,
        engine: Any,
        server_for: Callable[[Path, Path | None], MovedInServer],
        record_rows: Callable[[Path, Sequence[SourceRevRow]], bool],
        head_version: Callable[[Path], str | None],
        commits_since: Callable[[Path, str], int | None],
    ) -> None:
        if not plan.allowed or plan.manifest is None or plan.pinned is None:
            raise MoveError(" ".join(plan.refusals) or "This file cannot be brought in.")
        self.plan = plan
        self.entry = plan.pinned
        self._engine = engine
        self._server_for = server_for
        self._record_rows = record_rows
        self._head_version = head_version
        self._commits_since = commits_since

    def preflight(
        self, options: Any, cancel: threading.Event | None = None, *, ask: Any = None
    ) -> None:
        self._engine.preflight(options, cancel, ask=ask)

    def run(
        self,
        options: Any = None,
        *,
        cancel: threading.Event | None = None,
        ask: Any = None,
    ) -> Iterator[str]:
        plan = self.plan
        manifest = plan.manifest
        assert manifest is not None and manifest.server is not None
        server_dir = options.server_dir if options is not None else None
        if server_dir is None or plan.server_dir is None or Path(server_dir) != plan.server_dir:
            raise MoveError(
                "The folder this would be built in is not the one the plan was made for. "
                "Nothing was started."
            )
        package = self._reopen()
        digest = package_digest(manifest)
        refusal, _resuming = folder_refusal(server_dir, digest)
        if refusal:
            raise MoveError(f"{refusal} Nothing was started.")
        yield f"Bringing a {manifest.game.name} server from another computer into {server_dir}"
        yield from self._engine.run(options, cancel=cancel, ask=ask)
        marker = read_marker(server_dir)
        ours = marker is not None and marker.get("package") == digest
        done: list[str] = list(marker["done"]) if ours and marker else []  # type: ignore[call-overload]
        rebuild_needed = bool(marker.get("rebuild_needed")) if marker else False
        write_marker(server_dir, digest, done, rebuild_needed=rebuild_needed)
        server = self._server_for(server_dir, options.client_dir)

        def finished(step: str) -> None:
            done.append(step)
            write_marker(server_dir, digest, done, rebuild_needed=rebuild_needed)

        for step in STEPS:
            if step in done:
                yield f"Already done: {_STEP_NAMES[step]}."
                continue
            _check_cancel(cancel)
            yield f"Now: {_STEP_NAMES[step]}."
            if step == "revs":
                yield from self._revs(server_dir)
            elif step == "answers":
                yield from _lay_answers(package, server_dir)
            elif step == "modules":
                needed = yield from self._modules(server, server_dir)
                rebuild_needed = rebuild_needed or needed
            elif step == "rebuild":
                if rebuild_needed and server.rebuild is not None:
                    yield from server.rebuild(cancel)
                else:
                    yield "No module needs the server built again."
            elif step == "confs":
                yield from _stop_if_running(server.world)
                yield from _lay_files(package, server_dir, self.entry, server.db_password)
            elif step == "data":
                yield from _stop_if_running(server.world)
                yield from load_the_data(server.world, package)
            finished(step)
        yield from self._closing(server_dir)

    def _reopen(self) -> move.Package:
        try:
            package = move.read_package(self.plan.path)
        except move.MovePackageError as exc:
            raise MoveError(f"{exc} Nothing was started.") from exc
        if package.manifest != self.plan.manifest:
            raise MoveError(
                "The file changed after the plan was shown, so the yes given then no longer "
                "covers it. Nothing was started; look at it again."
            )
        return package

    def _revs(self, server_dir: Path) -> Iterator[str]:
        assert self.plan.manifest is not None and self.plan.manifest.server is not None
        catalog_entry = self.plan.entry
        assert catalog_entry is not None
        rows = moved_in_revs(
            catalog_entry,
            self.plan.manifest.server.sources,
            server_dir,
            head_version=self._head_version,
            commits_since=self._commits_since,
        )
        if not rows:
            yield "Every source is on the version this Yu'lon is tested with."
            return
        if not self._record_rows(server_dir, rows):
            yield (
                "!! Yu'lon could not write down which version each source is on, so the Server "
                "tab may not offer to move it onto the tested version."
            )
            return
        for row in rows:
            yield f"{row.repo} is at {row.built}; the Server tab offers the tested version."

    def _modules(self, server: MovedInServer, server_dir: Path) -> Generator[str, None, bool]:
        needed = False
        if not self.plan.modules:
            yield "The old server had no module installed from a repository."
            return False
        if server.applier is None:
            raise MoveError(
                f"{self.entry.name} has no module installer here, so no module was put back."
            )
        installed = server.installed()
        for planned in self.plan.modules:
            manifest = planned.manifest
            if planned.carried is not None:
                server.persist_manifest(planned.carried)
            if manifest.id in installed.get(str(manifest.type), frozenset()):
                yield f"{manifest.name} is already installed."
                continue
            yield f"Installing {manifest.name} at {planned.packed.commit[:7]}"
            values = module_answers.read_answers(server_dir, manifest) or None
            try:
                report = server.applier.install(manifest, values)
            except apply.ApplyError as exc:
                raise MoveError(
                    f"{manifest.name} could not be installed again: {exc} Press Bring from "
                    "another computer… again with the same file and folder to carry on."
                ) from exc
            for line in report.done:
                yield f"  {line}"
            for line in report.skipped:
                yield f"  skipped: {line}"
            needed = needed or report.rebuild_required
        return needed

    def _closing(self, server_dir: Path) -> Iterator[str]:
        manifest = self.plan.manifest
        assert manifest is not None
        yield (
            f"The server from the other computer is in {server_dir}: {manifest.counts.accounts} "
            f"accounts and {manifest.counts.characters} characters, its world, modules and "
            "settings. It is stopped. Start it on its Server tab; if the tab offers Repair for "
            "Yu'lon's command channel, press it (its account was part of what was replaced)."
        )
        yield (
            "Logins and passwords are the old ones. Add to Steam and the ready-to-play client are "
            "offered on the Server tab as for any server."
        )


_STEP_NAMES = {
    "revs": "noting which version each source is on",
    "answers": "the module answers",
    "modules": "installing the modules again at their packed versions",
    "rebuild": "building the server again with the modules",
    "confs": "laying the settings files",
    "data": "putting the databases in",
}


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise MoveError(
            "Stopped. Press Bring from another computer… again with the same file and folder: "
            "it carries on where it stopped."
        )


def _stop_if_running(world: MoveWorld) -> Iterator[str]:
    from yulon.move_flows import _server_is_up

    if _server_is_up(world):
        yield "Stopping the server for this step."
        world.stop_server()


def _write_bytes(target: Path, data: bytes) -> None:
    """Atomic: a unique temp beside the target, then a rename. Never through a link."""
    if target.is_symlink():
        raise MoveError(f"{target} is a link, so Yu'lon will not write through it.")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".yulon-new", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        if target.exists():
            os.chmod(name, target.stat().st_mode & 0o7777)
        os.replace(name, target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def _inside(server_dir: Path, target: str) -> Path:
    path = server_dir.joinpath(*PurePosixPath(target).parts)
    for parent in path.parents:
        if parent == server_dir:
            break
        if parent.is_symlink():
            raise MoveError(f"{parent} is a link, so Yu'lon will not write through it.")
    return path


def _lay_answers(package: move.Package, server_dir: Path) -> Iterator[str]:
    answers = package.files("answers")
    if not answers:
        yield "The old server had no module answers."
        return
    _write_bytes(server_dir / module_answers.ANSWERS_FILE, package.file_bytes(answers[0]))
    yield "The module answers are in place."


def _lay_files(
    package: move.Package, server_dir: Path, entry: CatalogEntry, password: str | None
) -> Iterator[str]:
    """Every packed conf over the one the install wrote, then the player's Lua scripts."""
    for member in package.files("conf"):
        target = _inside(server_dir, member.target)
        installed = target.read_bytes() if target.is_file() else None
        data = lay_conf(
            package.file_bytes(member), installed, machine_keys(entry, member.target), password
        )
        _write_bytes(target, data)
        yield f"Laid {member.target}"
    for member in package.files("lua"):
        _write_bytes(_inside(server_dir, member.target), package.file_bytes(member))
        yield f"Laid {member.target}"


def load_the_data(world: MoveWorld, package: move.Package) -> Iterator[str]:
    """Replace every database of the new server with the packed one, then put its own facts back.

    Under the Maintenance tab's guards (`move_flows._database_session`: the lease, the hold, the
    database alone). Before anything is loaded, the new server's core database version must be
    the package's: a build that did not end on the packed commits is refused here, before any
    data moves. Each load is T217's replacement (`drop_tables_not_in`), not a merge, so a table
    the fresh install has and the old server did not cannot outlive the move. The fresh
    server's realm address and port are read first and written back after; the realm keeps the
    OLD name, which came in the auth dump (lead decision: level 2 keeps the old realm name).
    """
    from yulon import networking
    from yulon.controller_wow_wotlk import maintenance
    from yulon.move_flows import (
        ENGINE_COPY_LABEL,
        SERVER_ROLES,
        _checked_plans,
        _database_session,
        _failed_part_way,
        _fix_ups,
        _present_roles,
        _read_versions,
    )

    manifest = package.manifest
    undone = "Nothing was put in yet."
    with _database_session(world, because="the databases are put in", undone=undone):
        roles = _present_roles(world, SERVER_ROLES)
        here = _read_versions(world, roles)
        said = core_version_difference(manifest.schema_evidence, here)
        if said:
            raise MoveError(said)
        address = _realm_address(world)
        order = {
            world.entry.schema_map()[r]: i
            for i, r in enumerate(SERVER_ROLES)
            if r in world.entry.schema_map()
        }
        schemas = tuple(
            sorted((m.schema_name for m in manifest.databases), key=lambda n: order.get(n, 99))
        )
        folder = maintenance.backups_dir(world.server_dir) / (
            ".move-in-" + world.now().strftime("%Y%m%d-%H%M%S")
        )
        folder.mkdir(parents=True, exist_ok=False)
        safety: list[Path] = []
        loaded: list[str] = []
        try:
            plans = _checked_plans(world, package, schemas, folder)
            for schema, restore_plan in plans:
                keep = maintenance.tables_in_copy(restore_plan.backup).get(schema, ())
                if not keep:
                    raise MoveError(f"The copy of {schema} lists no table, so it was not loaded.")

                def drop(schema: str = schema, keep: tuple[str, ...] = tuple(keep)) -> object:
                    return maintenance.drop_tables_not_in(world.mysql, schema, keep)

                yield f"Putting in {schema}"
                try:
                    report = world.restore(restore_plan, ENGINE_COPY_LABEL, before_load=drop)
                except maintenance.MaintenanceError as exc:
                    raise MoveError(
                        _failed_part_way(loaded, schema, schemas, safety, exc), detail=exc.detail
                    ) from exc
                loaded.append(schema)
                safety.extend(report.safety_backup)
            if address is not None:
                world.mysql.execute(
                    networking.realmlist_sql(world.entry, address[0], address[1])
                    + "\n"
                    + networking.realm_port_sql(world.entry)
                )
                yield f"The realm keeps this computer's address ({address[0]}) and its old name."
            else:
                yield (
                    "!! This computer's realm address could not be read before the load; set it "
                    "on the Server tab's network setting."
                )
            yield from _fix_ups(world, manifest, roles, use_old_realm_name=False)
        finally:
            import shutil

            shutil.rmtree(folder, ignore_errors=True)
    # The engine's safety copies are of the fresh, empty server this load replaced; the package
    # supersedes them. Kept when anything failed (the raise above skips this).
    for path in safety:
        path.unlink(missing_ok=True)
    yield f"Put in: {', '.join(loaded)}."


def core_version_difference(
    package: Mapping[str, move.Evidence], here: Mapping[str, move.Evidence | None]
) -> str | None:
    """A sentence when the new server's core database version is not the package's, else None.

    Core rows only (`digest`): the module rows of a fresh install depend on when its modules'
    SQL ran, and the load replaces that ledger whole with the packed one anyway. Only schemas
    the package has evidence for are asked; one this server cannot answer for refuses.
    """
    differences: list[str] = []
    for schema, theirs in sorted(package.items()):
        ours = here.get(schema)
        if ours is None:
            if schema in here:
                differences.append(f"{schema}: this server's version could not be read")
            continue
        if ours.kind != theirs.kind or ours.digest != theirs.digest:
            differences.append(f"{schema}: {theirs.count} in the file, {ours.count} here")
    if not differences:
        return None
    return (
        "The new server did not end up at the version the package was made from "
        f"({'; '.join(differences)}), so its databases were not replaced. Nothing was put in yet."
    )


def _realm_address(world: MoveWorld) -> tuple[str, str | None] | None:
    """The fresh server's realm address and local address, before the load replaces the row."""
    rl = world.entry.realmlist
    columns = [rl.address_column, *([rl.local_address_column] if rl.local_address_column else [])]
    try:
        out = world.mysql.query(
            f"SELECT {', '.join(f'`{c}`' for c in columns)} FROM "
            f"`{world.entry.databases.auth}`.`{rl.table}` WHERE `id` = {rl.realm_id};"
        )
    except Exception as exc:  # noqa: BLE001 - said in the result, never a failed move
        logger.info(f"could not read the realm address before the load: {exc}")
        return None
    line = out.strip("\r\n").split("\n")[0] if out.strip() else ""
    if not line:
        return None
    fields = line.split("\t")
    address = fields[0].strip()
    if not address:
        return None
    local = fields[1].strip() if len(fields) > 1 and fields[1].strip() else None
    return address, local


__all__ = [
    "MovedInInstall",
    "MovedInServer",
    "core_version_difference",
    "load_the_data",
    "MOVE_IN_FILE",
    "ModuleLookup",
    "ModuleToInstall",
    "ServerImportPlan",
    "folder_refusal",
    "plan_server_import",
    "PASSWORD_PLACEHOLDER",
    "ServerFacts",
    "SourceRevRow",
    "gather_server_facts",
    "lay_conf",
    "machine_keys",
    "moved_in_revs",
    "package_digest",
    "pinned_entry",
    "source_difference",
]
