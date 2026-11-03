"""What Tortoise takes from a link or a folder (T596 step 2).

The per-game layout `module_source` is handed for this core, kept beside the
binding because every fact in it is this core's. Three kinds:

* **A client add-on.** A folder holding a `.toc`, read by the shared add-on
  reader every game uses (`addon_layout`, T613 PR-2): at the top of the
  repository (`MobStats.toc`, the repository IS the add-on, like the two shipped
  ones), inside one wrapping folder (`pfUI-master/`), or one level down under
  `addon/`, `addons/`, `AddOns/` or `Interface/AddOns/`. It is installed under
  its `.toc`'s name, because the 1.12 client loads `AddOns/<X>/<X>.toc` and
  nothing else, and only for Interface 10000-11200. Copied into the
  ready-to-play client by the shipped `client` step, exactly as TortoiseBots
  Manager is, with a receipt per file so its Remove can take them back.
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
  the FOLDER's (`Add<folder>Scripts`), so the folder keeps the author's name
  exactly, capitals included (T861). Its `conf/*.conf.dist` goes to
  `etc/modules/<n>.conf` at once, because a world built with a module whose
  settings file is missing, or has no `[Section]`, does not start
  (`Config.cpp:207-231`).

The CONTENTS decide the kind since T861. A folder is read before it is copied:
C++ in `src/` makes it a server module, anything else an add-on and/or database
package. A LINK is always typed "module" and cloned into `modules/<folder>`: its
contents are unknown until it is cloned, and the core ignores a folder there with
no C++ in `src/`, which is the path a non-C++ repository already took when it was
named like a module. `complete()` then sets `build.rebuild` from what the clone
holds, so an add-on or SQL-only link asks for no rebuild.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from yulon import addon_archive, addon_layout
from yulon.apply import CompletionRefused
from yulon.manifest import (
    Build,
    ClientFile,
    ConfFile,
    Db,
    Manifest,
    ManifestType,
    is_inside_path,
    parse_manifest,
)
from yulon.module_source import id_for_folder

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

CLIENT_INTERFACE = 11200
"""The Tortoise client's `## Interface:` number: 1.12, as `catalog.Client.addon_interface` says.

Read by the shared add-on reader (`addon_layout`, T613 PR-2), which takes
10000-11200: a Classic Era add-on (115xx) is refused like a TBC one."""

ADDON_NOTE_PREFIX = "Add-on: "
"""How a completed manifest's notes mark what the add-on reader said (an older patch, ...)."""

UNUSED_PREFIX = "Not used: "
"""How a completed manifest's notes mark what it holds that Yu'lon does not install."""

LINK_DESCRIPTION = "Custom mod (cloned from a link you provided)."
FOLDER_DESCRIPTION = "Custom mod (copied from a folder you provided)."

WHAT_IS_READ = (
    "Yu'lon installs three things on Tortoise: a server module (C++ in a folder's src/, "
    "whatever the folder is called, with its settings file in conf/), an add-on (a folder "
    "holding a .toc file, which names it, at the top or under addons/ or Interface/AddOns/) "
    "and database changes (.sql files directly inside data/sql/auth, data/sql/character or "
    "data/sql/world)."
)

_SECTION = re.compile(r"^[ \t]*\[[^\]\r\n]+\]", re.MULTILINE)


@dataclass(frozen=True)
class TortoiseLayout:
    """`module_source.Layout` for this core: the folder check and the build (T861)."""

    link_description: str = LINK_DESCRIPTION
    folder_description: str = FOLDER_DESCRIPTION
    shipped_addons: Mapping[str, str] = field(default_factory=dict)
    """Add-on folder names the shipped items use (lower case) → the shipped item's name."""

    def identify(self, basename: str, *, folder: bool) -> tuple[ManifestType, str, str]:
        """This core derives no kind from a name: a link is a module, a folder is read."""
        return "module", id_for_folder(basename), basename

    def kind_for_folder(self, path: Path) -> ManifestType:
        """A folder with C++ in `src/` is a server module; anything else is a mod."""
        return "module" if _has_cpp_in_src(path) else "mod"

    def refuse_folder(self, path: Path, name: str) -> str | None:
        found = read_package(path, name, self.shipped_addons, self.kind_for_folder(path))
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
    notes: tuple[str, ...] = ()
    """What the add-on reader said about the add-ons (an older patch, a missing library)."""


def read_package(
    root: Path, name: str, shipped_addons: Mapping[str, str], kind: ManifestType = "mod"
) -> Package | str:
    """Read `root` as a Tortoise server module, add-on and/or database package, or say why not.

    A pure read; the same answer for a folder before it is copied and for a
    clone once it landed, so the folder route refuses early and both routes
    agree. A refusal is a sentence without a closing clause: whether anything
    was changed is the caller's to say. `kind` is what the CONTENTS made of it
    (`TortoiseLayout.kind_for_folder`) or the link route's "module".
    """
    cpp_in_src = _has_cpp_in_src(root)
    if kind == "module" and not cpp_in_src:
        elsewhere = _first_cpp(root)
        if elsewhere is not None:
            return (
                f"{name} holds C++ source in {elsewhere.relative_to(root).as_posix()}, but the "
                "server builds a module only from the src/ folder of its own folder, so it "
                "would be left out of the server without a word."
            )
    sql = _sql_files(root)
    found = _addons(root, name, shipped_addons)
    if isinstance(found, str):
        return found
    client, notes = found
    conf = _conf_steps(root, name) if cpp_in_src else ()
    if isinstance(conf, str):
        return conf
    unused = _unused(root, {path for _db, path in sql}, client, compiled=cpp_in_src)
    if not sql and not client and not cpp_in_src:
        also = f" It holds: {'; '.join(unused)}." if unused else ""
        return f"Yu'lon found nothing in {name} it can install on Tortoise. {WHAT_IS_READ}{also}"
    return Package(sql=sql, client=client, unused=unused, conf=conf, cpp=cpp_in_src, notes=notes)


def complete(manifest: Manifest, clone: Path, *, shipped_addons: Mapping[str, str]) -> Manifest:
    """`manifest` with the settings files, add-ons and SQL the clone turned out to hold.

    Re-validated. Raises `CompletionRefused` with the sentence of what it is not;
    the applier takes a first install's folder back and closes the sentence.
    """
    if manifest.origin is not None and manifest.origin.addon:
        # T613 review round 1: read as a package here, a route add-on whose repository
        # holds data/sql/ became database changes. Only the route reads its own items.
        raise CompletionRefused(
            f"{manifest.name} came from Yu'lon's add-on route, which reads it as add-ons "
            "alone; Tortoise's package reader does not read it."
        )
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
            "notes": [
                *(
                    note
                    for note in manifest.notes
                    if not note.startswith((UNUSED_PREFIX, ADDON_NOTE_PREFIX))
                ),
                *(UNUSED_PREFIX + line for line in found.unused),
                *(ADDON_NOTE_PREFIX + line for line in found.notes),
            ],
        }
    )


def addon_notes(manifest: Manifest) -> tuple[str, ...]:
    """What the add-on reader said when `complete()` read the item (T613 PR-2), one per line."""
    return tuple(
        note[len(ADDON_NOTE_PREFIX) :]
        for note in manifest.notes
        if note.startswith(ADDON_NOTE_PREFIX)
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


def _addons(
    root: Path, name: str, shipped_addons: Mapping[str, str]
) -> tuple[tuple[ClientFile, ...], tuple[str, ...]] | str:
    """The add-ons in `root` and the reader's notes, read by the shared engine (T613 PR-2).

    `addon_layout.find_addons()`: the name is the `.toc`'s stem (`pfUI-master/pfUI.toc`
    is `pfUI`), the Interface band is 1.12's (10000-11200, so Classic Era is refused),
    only the first number counts, a byte-order mark hides nothing, and a shipped
    add-on's name is refused. Each add-on folder is then held to the zip route's rules
    (`addon_archive.check_folder()`: no links, no program files, the size caps), as it
    is copied into the player's game client. None at all is no refusal here: the
    package may be database changes or a server module alone. A refusal is returned
    without its closing clause, which is the caller's to say.
    """
    found = addon_layout.find_addons(
        root, interface=CLIENT_INTERFACE, shipped=shipped_addons, label=name, required=False
    )
    if isinstance(found, addon_layout.Refusal):
        return _unclosed(found.sentence)
    for addon in found.addons:
        folder = root if addon.src == "." else root / addon.src
        try:
            addon_archive.check_folder(folder, label=addon.name)
        except addon_archive.AddonRefusal as refused:
            return _unclosed(str(refused))
    client = tuple(ClientFile(src=a.src, dest="addons", name=a.name) for a in found.addons)
    return client, found.notes


def _unclosed(sentence: str) -> str:
    """A reader's refusal without its closing clause: `read_package()`'s callers close it."""
    return sentence.removesuffix(" " + addon_layout.NOTHING_CHANGED)


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
        if not is_inside_path(f"conf/{dist.name}"):
            return (
                f"{name}'s settings file conf/{dist.name} has a name Yu'lon cannot use for a "
                "server file (a colon or a backslash in it). The server needs every module "
                "settings file it lists, so Yu'lon will not install this module until the file "
                "is renamed."
            )
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
