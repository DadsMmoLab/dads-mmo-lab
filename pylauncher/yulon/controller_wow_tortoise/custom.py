"""What Tortoise takes from a link or a folder (T596 step 2).

The per-game layout `module_source` is handed for this core, kept beside the
binding because every fact in it is this core's. Three kinds:

* **A client add-on.** A folder whose `.toc` has the folder's name, the way the
  1.12 client loads one: at the top of the repository (`MobStats.toc`, the
  repository IS the add-on, like the two shipped ones), or one level down
  under `addon/`, `addons/`, `AddOns/`, `Interface/AddOns/` or a top folder of
  its own name. Copied into the ready-to-play client by the shipped `client`
  step, exactly as TortoiseBots Manager is.
* **A database package.** `.sql` files directly inside `data/sql/auth`,
  `data/sql/character` (or `char`) or `data/sql/world` -- the core's own module
  layout (`modules/README.md`), and the files its updater would read there,
  not recursively (`AutoUpdater.cpp:102-106`). Each is one direct SQL step that
  Yu'lon runs itself and records in that database's `migrations` table under
  this package's id (`SqlStep.migration_module`), so the server's updater and
  Yu'lon never run the same file twice.
* **A server module (C++).** The core compiles every `modules/<name>/src/` it
  finds into the server (`-DMODULES=static`, `ConfigureModules.cmake`), and
  Yu'lon's image recipe lays `<server>/modules/` over the core's, so a module
  cloned or copied there is built in at the next Rebuild. The loader's name is
  the FOLDER's (`Add<folder>Scripts`), so the folder keeps the author's name and
  the name has to be exactly `mod-<x>` or `tw-mod-<x>` (lower case: the shared
  Tortoise modules' names). Its `conf/*.conf.dist` goes to `etc/modules/<n>.conf`
  at once, because a world built with a module whose settings file is missing,
  or has no `[Section]`, does not start (`Config.cpp:207-231`).

The name decides the kind because a link's content is unknown until it is
cloned, and the kind decides the folder it is cloned to (`apply.CLONE_DIRS`).
A repository named like a server module that holds no C++ in `src/` is read as a
database package or add-on in `modules/`, which the core ignores (no `src/`, no
module), and one that holds C++ but is not named like one is refused.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pydantic import TypeAdapter, ValidationError

from yulon.apply import CompletionRefused
from yulon.manifest import (
    Build,
    ClientFile,
    ConfFile,
    Db,
    Manifest,
    ManifestType,
    Slug,
    parse_manifest,
)
from yulon.module_source import NOTHING_CHANGED, DeriveError

_SLUG: TypeAdapter[str] = TypeAdapter(Slug)

_SERVER_MODULE_NAME = re.compile(r"(tw-)?mod-[a-z0-9-]{1,64}")
"""The names the shared Tortoise server modules carry (`tw-mod-*`, `mod-*-twow`, `mod-twow-*`).

Matched whole and in lower case, NOT lower-cased first (WotLK's rule lower-cases the
id; here the id is the loader's name, which comes from the folder: `Add<folder,
non-alphanumerics to _>Scripts`, `modules/README.md:73-74`, so a renamed folder no
longer links)."""

_LOOKS_LIKE_A_SERVER_MODULE = re.compile(r"(tw-)?mod-", re.IGNORECASE)
"""A name that starts like one: refused when it is not exactly one, never taken as an add-on.

`TW-Mod-A` is a server module's name typed in another case, and taking it as an
add-on would put a C++ module beside the add-ons."""

_MAX_ID = 64
"""As WotLK's `mod-[a-z0-9-]{1,64}`: an id is a folder name and a list key."""

CONF_SUFFIX = ".conf.dist"
MODULE_CONF_DIR = "etc/modules"
"""Where the core reads a module's settings file, under the server dir."""

CPP_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx")
"""What the core's module CMake globs as source (`ConfigureModules.cmake:54-68`)."""

SQL_DIRS: Mapping[str, Db] = {
    "auth": "auth",
    "character": "characters",
    "char": "characters",
    "world": "world",
}
"""`data/sql/<dir>` → database: this fork's shipped updater folder names, and the `char`
spelling module authors also use. Read in this order, as the updater reads them."""

ADDON_FOLDERS = ("addon", "addons", "interface/addons")
"""Folders (compared case-blind) whose children are add-ons, besides the repository's top."""

_INTERFACE = re.compile(r"^##\s*Interface:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_CLIENT_INTERFACE_CEILING = 20000
"""An add-on whose every `## Interface` is at or above this was written for a later client.

The Tortoise client is 1.12 (Interface 11200); 2xxxx is TBC and up."""

_VARIANT = re.compile(
    r"^(?P<base>.+?)[-_](tbc|bcc|wotlk|wrath|classic|vanilla|mainline|cata)$", re.I
)
"""`pfUI-tbc.toc` beside `pfUI.toc`: the same add-on's file for another game version."""

UNUSED_PREFIX = "Not used: "
"""How a completed manifest's notes mark what it holds that Yu'lon does not install."""

LINK_DESCRIPTION = "Custom mod (cloned from a link you provided)."
FOLDER_DESCRIPTION = "Custom mod (copied from a folder you provided)."

WHAT_IS_READ = (
    "Yu'lon installs three things on Tortoise: a server module (a repository named mod-<name> "
    "or tw-mod-<name>, with C++ in src/ and its settings file in conf/), an add-on (a folder "
    "whose .toc file has the folder's name, at the top or under addons/ or Interface/AddOns/) "
    "and database changes (.sql files directly inside data/sql/auth, data/sql/character or "
    "data/sql/world)."
)

SERVER_MODULE_NAME_RULE = (
    "a server module's repository must be named exactly mod-<name> or tw-mod-<name>, in lower "
    "case letters, digits and hyphens (up to 64 after the prefix)"
)
"""The rule in one clause, for the refusals: the server finds a module by its folder's name."""

_SECTION = re.compile(r"^[ \t]*\[[^\]\r\n]+\]", re.MULTILINE)


def is_server_module_name(name: str) -> bool:
    """Is `name` exactly a server module's repository name (`mod-<x>` / `tw-mod-<x>`)?"""
    return _SERVER_MODULE_NAME.fullmatch(name) is not None


def _kind_of(name: str) -> ManifestType:
    return "module" if is_server_module_name(name) else "mod"


@dataclass(frozen=True)
class TortoiseLayout:
    """`module_source.Layout` for this core: the name rule, the folder check, the build."""

    link_description: str = LINK_DESCRIPTION
    folder_description: str = FOLDER_DESCRIPTION
    shipped_addons: Mapping[str, str] = field(default_factory=dict)
    """Add-on folder names the shipped items use (lower case) → the shipped item's name."""

    def identify(self, basename: str, *, folder: bool) -> tuple[ManifestType, str, str]:
        if is_server_module_name(basename):
            return "module", basename, basename
        if _LOOKS_LIKE_A_SERVER_MODULE.match(basename):
            raise DeriveError(
                f"{basename} is named like a Tortoise server module, but "
                f"{SERVER_MODULE_NAME_RULE}: "
                "the server finds a module by the name of its folder, so Yu'lon cannot rename "
                f"it to fit. {NOTHING_CHANGED}"
            )
        item_id = re.sub(r"[^a-z0-9]+", "-", basename.lower()).strip("-")
        try:
            if len(item_id) > _MAX_ID:
                raise ValueError(item_id)
            _SLUG.validate_python(item_id)
        except (ValidationError, ValueError):
            what = "folder" if folder else "repository"
            raise DeriveError(
                f"The {what} is named {basename!r}, which gives Yu'lon no name to keep it "
                f"under: it needs letters or digits, at most {_MAX_ID}. {NOTHING_CHANGED}"
            ) from None
        return "mod", item_id, basename

    def refuse_folder(self, path: Path, name: str) -> str | None:
        found = read_package(path, name, self.shipped_addons, _kind_of(name))
        return found if isinstance(found, str) else None

    def build(self, kind: ManifestType) -> Build:
        # A server module asks for a Rebuild (`complete()` takes it back when the
        # folder holds no C++ for the core to build). Nothing else compiles, and
        # the restart a database change wants is said by the report from the SQL
        # itself (`ApplyReport.restart_recommended`).
        return Build(rebuild=kind == "module", restart=False)


LAYOUT = TortoiseLayout()


@dataclass(frozen=True)
class Package:
    """What a repository or folder holds that Yu'lon installs, and what it leaves alone."""

    sql: tuple[tuple[Db, str], ...]
    client: tuple[ClientFile, ...]
    unused: tuple[str, ...]
    conf: tuple[ConfFile, ...] = ()
    cpp: bool = False
    """C/C++ source under `src/`: the core will compile this folder into the server."""


def read_package(
    root: Path, name: str, shipped_addons: Mapping[str, str], kind: ManifestType = "mod"
) -> Package | str:
    """Read `root` as a Tortoise server module, add-on and/or database package, or say why not.

    A pure read; the same answer for a folder before it is copied and for a
    clone once it landed, so the folder route refuses early and both routes
    agree. A refusal is a sentence without a closing clause: whether anything
    was changed is the caller's to say. `kind` is what the NAME made of it
    (`TortoiseLayout.identify`).
    """
    cpp_in_src = _has_cpp_in_src(root)
    if kind != "module":
        cpp = _first_cpp(root)
        if cpp is not None:
            return (
                f"{name} holds C++ source ({cpp.relative_to(root).as_posix()}), so it is a "
                f"server module, and {SERVER_MODULE_NAME_RULE}; this one is named {name}."
            )
    elif not cpp_in_src:
        elsewhere = _first_cpp(root)
        if elsewhere is not None:
            return (
                f"{name} holds C++ source in {elsewhere.relative_to(root).as_posix()}, but the "
                "server builds a module only from the src/ folder of its own folder, so it "
                "would be left out of the server without a word."
            )
    sql = _sql_files(root)
    client = _addons(root, name)
    if isinstance(client, str):
        return client
    for step in client:
        assert step.name is not None
        shipped = shipped_addons.get(step.name.lower())
        if shipped is not None:
            return (
                f"{name} carries the add-on {step.name}, which Yu'lon already ships as "
                f"{shipped}: install that one from its row in the list."
            )
    conf = _conf_steps(root, name) if cpp_in_src else ()
    if isinstance(conf, str):
        return conf
    unused = _unused(root, {path for _db, path in sql}, client, compiled=cpp_in_src)
    if not sql and not client and not cpp_in_src:
        also = f" It holds: {'; '.join(unused)}." if unused else ""
        return f"Yu'lon found nothing in {name} it can install on Tortoise. {WHAT_IS_READ}{also}"
    return Package(sql=sql, client=client, unused=unused, conf=conf, cpp=cpp_in_src)


def complete(manifest: Manifest, clone: Path, *, shipped_addons: Mapping[str, str]) -> Manifest:
    """`manifest` with the settings files, add-ons and SQL the clone turned out to hold.

    Re-validated. Raises `CompletionRefused` with the sentence of what it is not;
    the applier takes a first install's folder back and closes the sentence.
    """
    found = read_package(clone, manifest.name, shipped_addons, manifest.type)
    if isinstance(found, str):
        raise CompletionRefused(found)
    return parse_manifest(
        {
            **manifest.model_dump(),
            "build": Build(rebuild=found.cpp, restart=False).model_dump(),
            "conf": [step.model_dump() for step in found.conf],
            "client": [step.model_dump() for step in found.client],
            "sql": [
                {"db": db, "path": path, "migration_module": manifest.id} for db, path in found.sql
            ],
            "notes": [*manifest.notes, *(UNUSED_PREFIX + line for line in found.unused)],
        }
    )


def unused(manifest: Manifest) -> tuple[str, ...]:
    """The lines `complete()` put in the notes about what the item holds and Yu'lon left alone."""
    return tuple(
        note[len(UNUSED_PREFIX) :] for note in manifest.notes if note.startswith(UNUSED_PREFIX)
    )


# ------------------------------------------------------------------ reading


def _has_cpp_in_src(root: Path) -> bool:
    """C/C++ source under a folder named exactly `src` (the core's glob is case-sensitive)."""
    if not root.is_dir():
        return False
    return any(
        child.name == "src"
        and not child.is_symlink()
        and child.is_dir()
        and _first_cpp(child) is not None
        for child in root.iterdir()
    )


def _first_cpp(root: Path) -> Path | None:
    """The first C/C++ source anywhere in the package (`.git` aside), in name order.

    Anywhere, not only `src/` (Codex review): a module's code in `Src/`, at the
    top or one folder down is still server code, and taking the package as an
    add-on would leave it unused without a word.
    """
    if not root.is_dir():
        return None
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts:
            continue
        if path.suffix.lower() in CPP_SUFFIXES and path.is_file():
            return path
    return None


def _sql_files(root: Path) -> tuple[tuple[Db, str], ...]:
    """Every `.sql` directly inside a mapped `data/sql/<dir>`, by database then by name."""
    by_db: dict[Db, list[str]] = {}
    sql_dir = root / "data" / "sql"
    for folder, db in SQL_DIRS.items():
        where = sql_dir / folder
        if not where.is_dir():
            continue
        by_db.setdefault(db, []).extend(
            f"data/sql/{folder}/{path.name}"
            for path in where.iterdir()
            if path.is_file() and path.suffix == ".sql"
        )
    order: tuple[Db, ...] = ("auth", "characters", "world")
    return tuple(
        (db, path)
        for db in order
        for path in sorted(by_db.get(db, ()), key=lambda p: PurePosixPath(p).name)
    )


def _addons(root: Path, name: str) -> tuple[ClientFile, ...] | str:
    """The add-ons in `root`: the top itself, else the folders under the add-on folders."""
    top = sorted(p for p in root.glob("*.toc") if p.is_file())
    if top:
        main = _main_toc(top, name)
        if main is None:
            listed = ", ".join(p.name for p in top)
            return (
                f"{name} has several .toc files at its top ({listed}), and Yu'lon cannot tell "
                "which one is the add-on."
            )
        refusal = _interface_refusal(name, main)
        if refusal:
            return refusal
        return (ClientFile(src=".", dest="addons", name=main.stem),)
    found: list[ClientFile] = []
    for folder in _addon_parents(root):
        for child in sorted(p for p in folder.iterdir() if p.is_dir()):
            toc = child / f"{child.name}.toc"
            if not toc.is_file():
                continue
            refusal = _interface_refusal(name, toc)
            if refusal:
                return refusal
            rel = child.relative_to(root).as_posix()
            found.append(ClientFile(src=rel, dest="addons", name=child.name))
    return tuple(found)


def _addon_parents(root: Path) -> list[Path]:
    """The top folder, then each add-on folder (`addons/`, `Interface/AddOns/`, …) present."""
    parents = [root]
    for wanted in ADDON_FOLDERS:
        here = root
        for part in wanted.split("/"):
            match = (
                next(
                    (p for p in sorted(here.iterdir()) if p.is_dir() and p.name.lower() == part),
                    None,
                )
                if here.is_dir()
                else None
            )
            if match is None:
                break
            here = match
        else:
            parents.append(here)
    return parents


def _main_toc(tocs: list[Path], name: str) -> Path | None:
    stems = {p.stem for p in tocs}
    main = [p for p in tocs if not _is_variant(p.stem, stems)]
    if len(main) == 1:
        return main[0]
    named = [p for p in main if p.stem.lower() == name.lower()]
    return named[0] if len(named) == 1 else None


def _is_variant(stem: str, stems: set[str]) -> bool:
    found = _VARIANT.match(stem)
    return found is not None and found["base"] in stems


def _interface_refusal(name: str, toc: Path) -> str:
    text = toc.read_text(encoding="utf-8", errors="replace")
    said = _INTERFACE.search(text)
    if said is None:
        return ""
    numbers = [int(n) for n in re.findall(r"\d+", said[1])]
    if numbers and min(numbers) >= _CLIENT_INTERFACE_CEILING:
        return (
            f"{name}'s add-on {toc.stem} is made for another game version (Interface "
            f"{', '.join(map(str, numbers))}); the Tortoise client is 1.12 (Interface 11200)."
        )
    return ""


def _conf_steps(root: Path, name: str) -> tuple[ConfFile, ...] | str:
    """One step per top-level `conf/*.conf.dist`, put at `etc/modules/<n>.conf` (where it is read).

    The core's build lists every enabled module's `conf/*.conf.dist` by name
    (`ConfigureModules.cmake:277-305`) and the world refuses to start when
    `<etc>/modules/<n>.conf` is missing or cannot be read as sections
    (`Config.cpp:207-231`, `mangosd/Main.cpp:157-162`). So the file is laid at
    install, long before the Rebuild that makes the world need it, and a dist with no
    `[Section]` line is refused here rather than found out as a world that will not
    start.
    """
    found: list[ConfFile] = []
    conf = root / "conf"
    if not conf.is_dir():
        return ()
    for dist in sorted(p for p in conf.iterdir() if p.is_file() and p.name.endswith(CONF_SUFFIX)):
        stem = dist.name[: -len(CONF_SUFFIX)]
        if not stem:
            continue
        text = dist.read_text(encoding="utf-8-sig", errors="replace")
        if _SECTION.search(text) is None:
            return (
                f"{name}'s settings file conf/{dist.name} has no [Section] line. The server "
                "reads a module's settings file by its sections, and one it cannot read "
                "stops the whole server from starting, so Yu'lon will not install this "
                "module until the file has one."
            )
        found.append(
            ConfFile(file=f"{MODULE_CONF_DIR}/{stem}.conf", template=f"conf/{dist.name}", keys=())
        )
    return tuple(found)


def _unused(
    root: Path, run: set[str], client: tuple[ClientFile, ...], *, compiled: bool = False
) -> tuple[str, ...]:
    """What the package holds that is neither run nor copied, one line per kind."""
    lines: list[str] = []
    inside = [step.src for step in client]
    loose = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.sql")
        if ".git" not in path.relative_to(root).parts and path.is_file()
    )
    loose = [
        rel
        for rel in loose
        if rel not in run and not any(src == "." or rel.startswith(src + "/") for src in inside)
    ]
    if loose:
        shown = ", ".join(loose[:5]) + (f" and {len(loose) - 5} more" if len(loose) > 5 else "")
        lines.append(
            f"{shown} (not run: Yu'lon runs only the .sql files directly inside data/sql/auth, "
            "data/sql/character or data/sql/world, as the server's own updater reads them)"
        )
    if (root / "conf").is_dir() and not compiled:
        lines.append(
            "conf/ (a settings file is read only for a module the server compiles, and there "
            "is no C++ in src/ here)"
        )
    return tuple(lines)
