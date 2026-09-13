"""Deriving a module manifest from a link or a folder the user supplied (checklist 8.7).

Game-agnostic and free of Qt, git and Docker (style-guide §3/§4): every function
here is a pure transformation of a string or a directory read, plus three small
writes under the app's own config directory. That is deliberate — it is what lets
every refusal SENTENCE a user can meet be asserted in `tests/test_module_source.py`
without a display to drive, and it is why a refusal can be raised on the GUI
thread before any job is queued.

**Nothing new is taught to the applier about what a module is.** These functions
build a `Manifest` the shipped schema already understands, and the same
`Applier.install()` every catalogued module goes through applies it. What is
derived is only what a bare repository can be known to be: it is a C++ module, it
is named for its own repository, it needs a rebuild, and — once the folder is on
disk — it has whatever `conf/*.conf.dist` and `data/sql/<db>/` the author put in
it. Nothing else is invented: no conf keys, no prompts, no `requires`, no NPCs,
no client files.

Prior art, so the divergences are legible. `dml wow module install --url <u>`
derived a key from the URL basename and refused anything not `mod-*`
(`RUST crates/dml-wow/src/modmgr.rs:198-207`, `modules.rs:42-50`), cloned it
`--depth 1` (`modmgr.rs:1812-1820`), activated a `.conf.dist` only when a REGISTRY
knew the module's conf name — so a custom module got `conf: "none"`
(`moduletail.rs:139-150`, `modules.rs:308`) — persisted nothing, re-discovering
custom modules on every list from `modules/*/.git` (`modules.rs:279-297`), and
described them with the line this module reuses verbatim (`modules.rs:38`). It had
no folder route at all. Two things here go further, each for a reason written at
the function: the conf name is DISCOVERED from the glob rather than looked up, and
the manifest is PERSISTED rather than re-derived, because a manifest is what the
Remove, update-check and SQL routes all take and a folder-copied module has no
`.git` to be re-discovered from.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Container
from datetime import date
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from yulon import rmtree
from yulon.log import get_logger
from yulon.manifest import (
    ALLOWED_REPO_HOSTS,
    Build,
    ConfFile,
    Db,
    Index,
    Manifest,
    ManifestType,
    Origin,
    Slug,
    Source,
    SqlStep,
    parse_manifest,
)
from yulon.manifest_store import FAMILY_FILES

logger = get_logger(__name__)

_SLUG: TypeAdapter[str] = TypeAdapter(Slug)
"""`manifest.Slug`'s own pattern, asked directly rather than respelled here.

The id has to pass BOTH this and `_CUSTOM_ID`, and re-typing the kebab-case rule
in this module is the duplicate style-guide §4 forbids — a widened `Slug` must
widen what a custom module may be called, in one edit."""

LINK_DESCRIPTION = "Custom module (cloned from a URL you provided)."
"""Verbatim from `RUST crates/dml-wow/src/modules.rs:38` (itself from `90-main.sh:5394`).

The Modules list draws `[{type}] {name} — {description}`, so the description IS
what marks a custom module in the list and the view needs no new code to draw one.
"""

FOLDER_DESCRIPTION = "Custom module (copied from a folder you provided)."
"""The folder half. New — rust-main had no folder route to describe."""

_NOTHING_CHANGED = "Nothing on this machine was changed."
"""The tab's own closing clause, true of every refusal here: nothing writes first."""

_CUSTOM_ID = re.compile(r"^mod-[a-z0-9-]{1,64}$")
"""`_valid_cpp_key` (`RUST modules.rs:40-50`), ported exactly.

Why `mod-*` and not any slug: AzerothCore's `modules/` directory holds
`CMakeLists.txt`, `ModulesLoader.cpp.in.cmake` and friends BESIDE the modules, and
the derived id is also the name of the folder that lands there. The rule is the
one thing that keeps a pasted `https://github.com/you/Tools.git` from putting a
folder called `tools` next to the build system's own files. All 21 shipped
`wow-wotlk` module ids start `mod-` too.
"""

_MODULE_MARKERS = ("src", "conf", "data")
"""What a folder must hold at least one of to be taken as a module.

A module contributes code, configuration or data; a folder with none of the three
is the wrong folder, and copying it would put an arbitrary directory under
`modules/` for the next rebuild to compile. ANY ONE is enough on purpose —
AzerothCore has data-only modules that ship SQL and DBC and no C++ at all
(`RUST modmgr.rs:1822-1826` carves one out of the rebuild list by name).

No page names this rule; it is this module's, recorded here rather than assumed.
"""

_CONF_DIST_SUFFIX = ".conf.dist"
_MODULE_CONF_DIR = "env/dist/etc/modules"

_SQL_DBDIRS: dict[str, Db] = {
    "db-world": "world",
    "db_world": "world",
    "db-characters": "characters",
    "db_characters": "characters",
    "db-auth": "auth",
    "db_auth": "auth",
    "playerbots": "playerbots",
}
"""`sql_dbdir_target` (`RUST modmgr.rs:2625-2633`), both spellings, verbatim.

Both because AzerothCore module repos use both in the wild. A directory that is in
neither spelling gets no step at all rather than a guessed database.
"""


class DeriveError(RuntimeError):
    """A link or a folder this app will not derive a module from.

    Carries the whole sentence shown to the user, ending in `_NOTHING_CHANGED` —
    which is a promise, not decoration: every one of these is raised before
    anything is written, and `test_every_refusal_sentence_ends_with_nothing_changed_
    and_no_file_exists` asserts both halves together.
    """


# ---------------------------------------------------------------- deriving


def derive_link(text: str, game: str, *, today: date, shipped_ids: Container[str] = ()) -> Manifest:
    """A minimal module manifest for the repository `text` points at.

    Minimal because a link's CONTENTS are not known until it is cloned: this
    returns the id, name, type, game, description, source, build and notes —
    enough to clone — and `complete()` fills in the conf and SQL from what the
    clone turned out to hold. There is no second clone and no `configure()` pass.

    **The allow-list applies to a link the user pasted, and that is a decision no
    page makes.** `README.md` §3a is about what the app SHIPS, and rust-main
    accepted any `https://` URL (`RUST modmgr.rs:187-193`). It is kept narrow here
    for two reasons. The derived manifest is PERSISTED and read back through
    `parse_manifest` on every start, so a link the store's own validator would
    refuse on reload has to be refused at the press — or the list reads
    `!! could not load modules: …` next morning with nothing the user can do about
    it. And widening `ALLOWED_REPO_HOSTS` is a one-tuple change in `manifest.py`
    if the owner wants it, where a second validator for "user links" would be the
    duplicate style-guide §4 forbids. **A self-hosted Gitea/Forgejo link is
    refused today, by this rule.**

    `shipped_ids` is the ids this game already ships. Answering it here is what
    makes the shadow rule a refusal at the press rather than only at the write.
    """
    text = text.strip()
    if not text:
        raise DeriveError(f"Paste a link first. {_NOTHING_CHANGED}")
    try:
        source = Source(repo=text)
    except ValidationError as exc:
        raise DeriveError(
            f"{text} is not a link this app can clone from — it takes an https link "
            f"on {', '.join(ALLOWED_REPO_HOSTS)}, or owner/name for github.com. "
            f"{_NOTHING_CHANGED}"
        ) from exc
    item_id = _id_from(text.rstrip("/").rsplit("/", 1)[-1])
    if item_id is None:
        raise DeriveError(
            f"The repository is named {text.rstrip('/').rsplit('/', 1)[-1]!r}, and a "
            f"custom module must be named mod-<something> in lowercase letters, "
            f"digits and hyphens — for example https://github.com/you/mod-my-thing. "
            f"{_NOTHING_CHANGED}"
        )
    _refuse_shipped(item_id, shipped_ids)
    return _manifest(
        item_id,
        game,
        description=LINK_DESCRIPTION,
        source=source,
        origin=Origin(kind="link", added=today.isoformat()),
        came_from=text,
        today=today,
    )


def derive_folder(
    path: Path, game: str, *, today: date, shipped_ids: Container[str] = ()
) -> Manifest:
    """A minimal module manifest for the folder at `path`, with no `source` at all.

    The source folder is read BEFORE anything is copied, so all three refusals
    below happen while the only thing that has been touched is a directory listing.
    Order matters: "is it a folder" is asked first, because a FILE named `mod-x`
    would otherwise be refused for a reason that is not the reason.
    """
    if not path.is_dir():
        raise DeriveError(f"{path} is not a folder this app can read. {_NOTHING_CHANGED}")
    item_id = _id_from(path.name)
    if item_id is None:
        raise DeriveError(
            f"The folder is named {path.name!r}, and a custom module must be named "
            f"mod-<something> in lowercase letters, digits and hyphens — rename the "
            f"folder and choose it again. {_NOTHING_CHANGED}"
        )
    if not any((path / marker).is_dir() for marker in _MODULE_MARKERS):
        raise DeriveError(
            f"{path} does not look like a module: it has no "
            f"{', '.join(_MODULE_MARKERS[:-1])} or {_MODULE_MARKERS[-1]} folder. "
            f"{_NOTHING_CHANGED}"
        )
    _refuse_shipped(item_id, shipped_ids)
    return _manifest(
        item_id,
        game,
        description=FOLDER_DESCRIPTION,
        source=None,
        origin=Origin(kind="folder", path=str(path), added=today.isoformat()),
        came_from=str(path),
        today=today,
    )


def complete(manifest: Manifest, clone: Path) -> Manifest:
    """`manifest` plus the conf files and SQL steps the folder at `clone` turned out to hold.

    Handed to the applier as a hook so the SAME `install()` pass that put the
    folder at `modules/<id>` activates the conf it found and reports the SQL it
    found. Anything else would mean a second clone, or a report describing a
    manifest other than the one applied.

    The result is re-validated through `parse_manifest()` rather than assembled by
    a raw `model_copy`: this is the file that gets persisted and read back on every
    start, so a derivation that would not survive its own reload fails here, where
    a person is watching, rather than next morning in the list.
    """
    return parse_manifest(
        {
            **manifest.model_dump(),
            "conf": [c.model_dump() for c in _conf_steps(clone)],
            "sql": [s.model_dump() for s in _sql_steps(clone)],
        }
    )


def _conf_steps(clone: Path) -> tuple[ConfFile, ...]:
    """One `ConfFile` per `conf/*.conf.dist` at the TOP LEVEL of the clone's `conf/`.

    The shape all 20 shipped conf-bearing manifests use, discovered instead of
    declared: `template` is `conf/<name>.conf.dist`, `file` is
    `env/dist/etc/modules/<name>.conf`, and `keys` is empty. `Applier._conf()`
    then does for these exactly what it does for a shipped module — copy the
    template into place if the target does not exist, write no keys — which is
    the outcome rust-main called "activate with defaults" and never overwrote
    (`RUST modmgr.rs:1848-1858`), reached without needing a registry to know the
    conf's NAME first (`moduletail.rs:139-150`).

    Top level only. rust-main also ran a bounded `maxdepth 4` find
    (`moduletail.rs:104-136`), but a `.conf.dist` two levels down is somebody's
    example rather than the module's, and activating it would copy a stranger's
    file into the server's live `etc`.
    """
    conf_dir = clone / "conf"
    return tuple(
        ConfFile(
            file=f"{_MODULE_CONF_DIR}/{dist.name[: -len(_CONF_DIST_SUFFIX)]}.conf",
            template=f"conf/{dist.name}",
            keys=(),
        )
        for dist in sorted(conf_dir.glob(f"*{_CONF_DIST_SUFFIX}"))
        if dist.is_file()
    )


def _sql_steps(clone: Path) -> tuple[SqlStep, ...]:
    """One deferred SQL step per mapped directory under the clone's `data/sql/`.

    `applied_by="db-import"` because that is the default the schema documents for
    a C++ module — AzerothCore's own importer ledgers what it applied in the
    `updates` table, and "applying those by hand breaks that tracking"
    (`SqlStep`'s docstring). The step is therefore REPORTED as pending with the
    files the glob found and applied by the module-SQL route that already exists.

    The glob is recursive (`**`) where the shipped manifests' is flat: a
    derivation cannot know whether the author put files in `base/`/`updates/`
    subfolders (rust-main read `updates/` only, `RUST modmgr.rs:2581-2608`), and
    the only thing this glob decides is what the REPORT lists — whether the
    importer applies a nested file is the importer's business and shows in its own
    `>> Applying update` lines.

    One step per DIRECTORY rather than per database: a repository carrying both
    `db-world/` and `db_world/` gets two steps naming the same database, which is
    what its files actually are. Collapsing them would drop one directory's files
    from the report.
    """
    sql_dir = clone / "data" / "sql"
    if not sql_dir.is_dir():
        return ()
    steps: list[SqlStep] = []
    for entry in sorted(sql_dir.iterdir()):
        if not entry.is_dir():
            continue
        db = _SQL_DBDIRS.get(entry.name)
        if db is None:
            continue
        steps.append(SqlStep(db=db, path=f"data/sql/{entry.name}/**/*.sql", applied_by="db-import"))
    return tuple(steps)


def _id_from(basename: str) -> str | None:
    """The module id a repository or folder basename yields, or `None` if it yields none.

    `.git` stripped ONCE and lower-cased, `RUST modmgr.rs:198-207`. Checked against
    `_CUSTOM_ID` **and** `manifest.Slug`, which is not redundant: `_CUSTOM_ID`
    admits `mod-x-` and `mod--x`, `Slug` forbids a trailing or doubled hyphen, and
    without the second check those two arrive at the user as a pydantic
    `ValidationError` instead of a sentence. (The design page asserted `_CUSTOM_ID`
    was a subset of `Slug`; it is not.)
    """
    candidate = basename
    if candidate.endswith(".git"):
        candidate = candidate[: -len(".git")]
    candidate = candidate.lower()
    if not _CUSTOM_ID.match(candidate):
        return None
    try:
        _SLUG.validate_python(candidate)
    except ValidationError:
        return None
    return candidate


def _manifest(
    item_id: str,
    game: str,
    *,
    description: str,
    source: Source | None,
    origin: Origin,
    came_from: str,
    today: date,
) -> Manifest:
    """The fields a derivation can honestly fill in, and nothing beyond them.

    `name` is the id, because nothing better is known (`RUST modules.rs:303` did
    the same). `build.rebuild` is true because it is a C++ module and the report's
    rebuild sentence has to say so; `build.restart` stays false because the
    derivation cannot know. `prompts` is empty, so the view opens no dialog and
    passes `None` — byte for byte the shipped install path. `requires`,
    `conflicts_with`, `deploy`, `patches`, `client`, `server_dbc` and `npcs` are
    all unknown and are therefore left empty rather than invented.
    """
    return Manifest(
        id=item_id,
        name=item_id,
        type="module",
        game=game,
        description=description,
        source=source,
        origin=origin,
        build=Build(rebuild=True),
        notes=(
            f"Derived by Yu'lon from {came_from} on {today.isoformat()}; "
            "nothing here was written by the module's author.",
        ),
    )


def _refuse_shipped(item_id: str, shipped_ids: Container[str]) -> None:
    """The shadow rule, in the one place it is worded.

    Asked at the press (`derive_*`) and again at the write (`persist()`), because
    a user file can arrive by hand as easily as by a button, and the store also
    refuses one on the way back in. Three answers to the same question so no route
    can be the one that forgets it.
    """
    if item_id in shipped_ids:
        raise DeriveError(
            f"{item_id} is a module this app already ships — select it in the list "
            f"and press Install selected. {_NOTHING_CHANGED}"
        )


# --------------------------------------------------------------- persisting


def persist(user_root: Path, manifest: Manifest, *, shipped_ids: Container[str]) -> None:
    """Write `manifest` into the user layer and rebuild that family's index from disk.

    `<user_root>/<game>/<family>/<id>.json` plus `<user_root>/<game>/<family>.json`
    — the store's own layout, so `ManifestStore(bundled, game, user_root=...)`
    reads it with no new parser. This is what makes a custom module survive a
    restart; rust-main persisted nothing and re-discovered custom modules from
    `modules/*/.git` on every list (`RUST modules.rs:279-297`), which a
    folder-copied module has none of.

    **The index is DERIVED from the directory, never appended to.** A crash between
    the two writes then leaves a file the next persist picks up, rather than an
    index naming a file that is not there — and a second persist of the same id is
    idempotent instead of a duplicate row.

    `shipped_ids` is required rather than defaulted: this is the last point before
    the bytes land, and a caller that has not answered "does the app already ship
    this?" must not be able to write by forgetting to.
    """
    _refuse_shipped(manifest.id, shipped_ids)
    items_dir = _items_dir(user_root, manifest.game, manifest.type)
    items_dir.mkdir(parents=True, exist_ok=True)
    _write_atomically(items_dir / f"{manifest.id}.json", manifest.model_dump_json(indent=2) + "\n")
    _rewrite_index(user_root, manifest.game, manifest.type)


def forget(user_root: Path, manifest: Manifest) -> bool:
    """Remove `manifest` from the user layer; `True` if a file was there to remove.

    A shipped manifest is an OFFER and stays listed whether or not it is installed.
    A custom one is a RECORD of something the user brought, and a record of a
    folder that is gone would be a list entry whose Install re-clones a link the
    user already decided against. So Remove forgets it — after `Applier.remove()`
    returned, never before, the ordering `purge.py` uses for `state.forget()`
    (`phase8-decisions.md`): a refused remove keeps the record, so the module is
    still reachable from the list.

    No page decides this. Recorded here as the decision and its reason.
    """
    item = _items_dir(user_root, manifest.game, manifest.type) / f"{manifest.id}.json"
    if not item.is_file():
        return False
    item.unlink()
    _rewrite_index(user_root, manifest.game, manifest.type)
    return True


def _items_dir(user_root: Path, game: str, kind: ManifestType) -> Path:
    return user_root / game / FAMILY_FILES[kind]


def _rewrite_index(user_root: Path, game: str, kind: ManifestType) -> None:
    """Rebuild `<user_root>/<game>/<family>.json` from the files that are actually there."""
    items_dir = _items_dir(user_root, game, kind)
    index = Index(
        game=game, type=kind, items=tuple(sorted(p.stem for p in items_dir.glob("*.json")))
    )
    _write_atomically(
        items_dir.parent / f"{FAMILY_FILES[kind]}.json",
        index.model_dump_json(indent=2) + "\n",
    )


def _write_atomically(target: Path, text: str) -> None:
    """Write `text` beside `target` and rename it over — never straight into the name.

    `write_clone_claim()` made this argument first and it is the same one here. A
    plain write opens the final name for truncation and then fills it, so a full
    disk or a killed process leaves a HALF file at a name this app reads back on
    every start. A half manifest does not parse; a user index or item that does
    not parse is a `ManifestError`; and the Modules tab draws that as
    `!! could not load modules: …` with NO list at all — so one torn custom file
    takes every shipped module off the screen too, and the only way back is
    finding and deleting a JSON file by hand.

    The temporary file goes in the SAME directory, because a rename across
    filesystems is a copy and a copy is exactly the tearing being avoided. It is
    removed on failure so a refusal leaves no debris under a name nothing reads.
    """
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


# ------------------------------------------------------------------ copying


def copy_folder(src: Path, dest: Path) -> None:
    """Copy the module folder `src` onto `dest`, replacing whatever is there.

    The applier's second way to fill `modules/<id>`: a copy where a shipped module
    gets a clone. Handed to it as a seam, so nothing about a local folder reaches
    `git.py` — `CloneSpec`, its HTTP/1.1 and autocrlf pins and the container mount
    logic are all about cloning, and this is a `copytree`.

    **`.git` is never carried over.** A copy is a SNAPSHOT, not a checkout. 8.7a's
    update check asks git about every folder under `modules/`, and a copy carrying
    the author's `.git` would report a commit count against a remote the user never
    chose and offer an update that would overwrite their folder. Without it the same
    module reports `not a git checkout — nothing to compare`, which is the truth.

    **The destination is replaced, not merged into.** Choosing the same folder again
    is how a newer version is brought over (there is no re-derive), and a merge would
    leave files the newer version deleted sitting inside the module for the next
    rebuild to compile.

    The one refusal is a source inside the destination's own `modules/` directory:
    the folder itself (whose `rmtree` would delete what is about to be copied) and
    any neighbour under it. A source that CONTAINS the destination is not refused —
    no page names that case, and the smaller thing is to leave it alone rather than
    invent a second rule here; `shutil.copytree` snapshots each directory listing
    before it creates the matching destination, so it terminates.
    """
    modules_dir = dest.parent
    if _is_within(src, modules_dir):
        raise DeriveError(
            f"{src} is already inside this server's modules folder — a module is "
            f"copied into it, not from it. {_NOTHING_CHANGED}"
        )
    if dest.exists():
        rmtree.remove_tree(dest)  # T49: the dest may be a previous copy holding a .git
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git"))
    logger.info(f"copied {src} → {dest}")


def _is_within(path: Path, directory: Path) -> bool:
    """Is `path` `directory` itself, or somewhere under it?

    Compared on the resolved paths so a `..` segment or a symlinked route to the
    same folder cannot walk around the check.
    """
    resolved, root = path.resolve(), directory.resolve()
    return resolved == root or root in resolved.parents
