"""The declarative apply engine: turn a `Manifest` into install/configure/remove steps.

This is the one place the manifest primitives (`source`, `deploy`, `patches`,
`sql`, `conf`, `client`, `server_dbc`) acquire behavior (roadmap 2.3). It is
game-agnostic and shared by every `controller_<acronym>/modules.py`
(style-guide §4); all per-item knowledge comes from the manifest, never from
a conditional here. Everything that reaches outside the process goes through
a small seam (`Git`, `SqlRunner`, `DbcCopier`) so the engine is unit-testable
without git, Docker or a network, and so the real implementations live next
to the other subprocess code.

Nothing is ever skipped silently, and nothing is ever CLAIMED silently either:
every step a run could not perform (no SQL runner, no client dir, no DBC copier)
is named in `ApplyReport.skipped`; SQL left to AzerothCore's own importer is
named in `ApplyReport.pending_sql` rather than in `done`, with the glob resolved
so the file count is one this run took (`PendingSql` records what that cost);
and `rebuild_required` says whether the worldserver must be rebuilt before the
change is live. The engine does not restart, rebuild, or touch Docker itself —
that is the controller's call (call down / signal up, §5).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path, PurePosixPath
from string import Formatter
from typing import IO, Any, Literal, Protocol

from yulon import docker, module_answers, platform, rmtree, runner
from yulon.catalog import composegen
from yulon.dbreads import SqlReader
from yulon.git import (
    CLONE_MARKER,
    BehindReader,
    CloneSpec,
    ContainerGit,
    Git,
    GitError,
    HistoryReader,
    RemoteReader,
    RunnerGit,
    TreeReader,
    git_available,
    same_repo,
)
from yulon.log import get_logger
from yulon.manifest import (
    Db,
    Deploy,
    ExistsCheck,
    Manifest,
    ManifestType,
    Patch,
    Prompt,
    SqlStep,
    When,
)
from yulon.ownership import Ownership

logger = get_logger(__name__)

# Where each family's clone lives under the server dir (mirrors wow-manage.sh).
CLONE_DIRS: dict[ManifestType, str] = {
    "module": "modules",
    "ale": "ale_scripts",
    "keg": "ale_scripts",
    "mod": "sql_scripts/clones",
}


def _default_git(server_dir: Path) -> Git:
    """Host git where it can actually run, containerised git otherwise (T58).

    The SERVER install has always cloned through `ContainerGit`, so Docker alone
    is enough to get a running server and a user never finds out whether they
    have git. The applier then defaulted to `RunnerGit()`, which meant the
    Modules tab -- and only the Modules tab -- required a tool the rest of the
    app had carefully avoided needing. Two users reported the same thing on the
    same day: a working server, 500 bots, and not one module installable, with

        install mod-aoe-loot FAILED: [WinError 2] The system cannot find the file specified

    `git_available()` is the right question and is careful in a way
    `shutil.which("git")` is not: on a Mac with no Command Line Tools,
    `/usr/bin/git` exists as a stub whose only behaviour is to open a modal
    installer and block a launcher forever.

    Host git is still PREFERRED where it works: it needs no daemon, no image
    pull and no bind mount, and `ContainerGit` already falls back to it for the
    same reason.
    """
    if git_available():
        return RunnerGit()
    # No host git. The containerised seam BIND-MOUNTS the destination, and
    # Docker Desktop refuses a `\\wsl.localhost\...` mount source -- so for a
    # WSL-backed install it cannot work either, and picking it would trade one
    # failure for a slower one after pulling an image (review, 2026-09-14).
    # Refuse precisely instead; `wsl_linux_path()` is the same test the rest of
    # the app uses to spot that shape.
    if platform.wsl_linux_path(server_dir) is not None:
        raise ApplyError(
            f"This server lives inside WSL, at {server_dir}. Yu'lon clones modules with git, "
            f"and this machine has none it can run: the container it would fall back to cannot "
            f"reach a \\\\wsl.localhost path. Install Git inside the distro (or on Windows) "
            f"and try again. Nothing was changed."
        )
    return ContainerGit()


def installed_clones(server_dir: Path) -> dict[str, frozenset[str]]:
    """What is on disk per family, read from each family's own clone directory (T41).

    `CLONE_DIRS` is the whole point: a module lands in `modules/`, an ale and a
    keg in `ale_scripts/`, a mod in `sql_scripts/clones/`. Marking every family
    against `modules/` said an ale was installed because a module of the same
    id was, and never marked a real ale at all.

    `ale` and `keg` share one folder, so they share its answer. Nothing here can
    tell which of the two a directory belongs to without opening it, and this
    function is the cheap read that runs on every reload — the row it might
    over-mark is one whose manifest the user can still read.
    """
    return {
        str(kind): docker.clone_names(server_dir / folder) for kind, folder in CLONE_DIRS.items()
    }


def conflicting_installed(
    manifest: Manifest, installed: Mapping[str, frozenset[str]]
) -> tuple[tuple[str, ManifestType], ...]:
    """Every `(id, family)` listed as installed that `manifest` cannot sit beside, in order.

    One reading, two readers: the Modules tab greys a row on the first answer,
    and `Applier._conflict_refusal()` refuses an install on the first answer
    whose folder holds anything. They were nearly written twice, which is how a
    tab comes to offer what the applier will refuse (T55).

    **The two can still differ, in one direction only, and on purpose.** An
    EMPTY leftover folder is listed here -- `installed_clones()` counts every
    non-hidden directory -- so the tab greys the row, while the applier looks
    inside and lets the install through (T59). The tab is then stricter than
    the applier, never looser, and the row it points at shows as installed with
    a Remove that clears the empty folder. The other direction is the bug this
    exists to prevent. Opening every folder on each reload to close the gap
    would change what "installed" means for every badge in the tab.

    Asked of what is INSTALLED, never of the declaration. Four of the mods name
    each other (buff/xbuff/nerf/baby-mobs), so refusing on the presence of a
    conflict rather than of the conflicting CLONE would make all four
    permanently uninstallable.

    Every family is searched, not the manifest's own: ids are unique across the
    catalog, and a conflict that reaches across families is exactly the one a
    same-family check would miss.
    """
    return tuple(
        (other, kind)
        for other in manifest.conflicts_with
        for kind in CLONE_DIRS
        if other in installed.get(str(kind), frozenset())
    )


def missing_requirements(
    manifest: Manifest, installed: Mapping[str, frozenset[str]]
) -> tuple[str, ...]:
    """Every id in `manifest.requires` with no clone directory here, in declared order.

    `conflicting_installed()`'s mirror, and written beside it for the same
    reason: one reading, two readers. The Modules tab locks a row's Install on
    the first answer and `Applier._requires_refusal()` refuses the install on
    it, so the tab cannot offer a press whose only outcome is a refusal (T69,
    the T55 shape).

    **A target that is not a catalog item is answered the same way, and that is
    the whole reason this asks the DISK rather than the catalog.**
    `mod-playerbots` is cloned by the SERVER install -- `catalog.json` lists it
    among wow-wotlk's emulator sources with `dest: modules/mod-playerbots` --
    and has no manifest of its own. Looking `requires` up as a catalog id would
    refuse `mod-city-bots` forever on the installs where its requirement is in
    fact present. A folder under `modules/` is the same evidence for a
    server-cloned module as for one this tab cloned.

    Every family folder is searched, not the manifest's own: `Manifest.requires`
    names an id and never a family, and an ale script requiring a module
    (`mod-ale`, nine of the eleven shipped `requires`) is the ordinary case
    rather than the exotic one.

    **Deliberately WITHOUT `_conflict_refusal()`'s empty-folder reading.** There,
    opening the folder makes the applier kinder than the tab, which is the safe
    direction. Here the test is inverted -- a folder means the requirement is
    MET -- so calling an empty one absent would make the applier refuse what the
    tab offers, which is exactly the disagreement this function exists to
    prevent. An empty leftover `modules/mod-ale` lets the install through, and
    the row above it says installed.
    """
    return tuple(
        needed
        for needed in manifest.requires
        if not any(needed in installed.get(str(kind), frozenset()) for kind in CLONE_DIRS)
    )


def requirement_refusal(item_id: str, needed: str) -> str:
    """Why `item_id` cannot be installed without `needed` — one spelling, two readers (T69).

    The applier raises it and the Modules tab writes it into the row's tooltip
    and its report line. `needed` is the target's NAME where the catalog knows
    one and its id otherwise, which is why the caller passes it in rather than
    this looking it up: the applier has ids and the tab has names, and neither
    should be made to guess the other's.
    """
    return (
        f"{item_id} needs {needed}, which is not installed here: this manifest names it in "
        f"`requires`, and {item_id} does nothing without it. Install {needed} first."
    )


# Manifest `db` → MySQL schema name (AzerothCore defaults; acore_ale is Paragon's).
DB_NAMES: dict[Db, str] = {
    "auth": "acore_auth",
    "characters": "acore_characters",
    "world": "acore_world",
    "playerbots": "acore_playerbots",
    "ale": "acore_ale",
}

WORLD_HELD_DBS: frozenset[Db] = frozenset({"characters", "world", "playerbots"})
"""The databases a running worldserver holds in memory and writes back over.

The set `_refuse_direct_sql_into_a_running_world()` refuses into, enumerated
once so the guard and any reader of it cannot disagree. It is the union of what
the pages name, and no more: owner answer 7 says `characters` and `world`
(`phase8-parity-decisions.md:44`), `checklist.md:2501` says *the character or
world database*, and `phase8-designs/c-operators-risk.md:90` adds `playerbots`.

`auth` is deliberately outside it. Its writes are account rows, which the
worldserver does not cache and write back, and every page that names this rule
names it as a database the guard does not cover.

**`ale` is outside it because no page names it, not because a page allows it.**
It is the ALE Lua engine's own schema, which lives inside the worldserver
process, so the reason the other three are here plausibly applies to it too;
one shipped step targets it (`manifests/wow-wotlk/ale/paragon.json`,
`sql/0[2-9]_*.sql`, install-time). Owner answer 7, checklist 8.7a and the two
design pages are all silent on it, so it is left running rather than quietly
decided for here — an owner question, recorded in `pyplan/write-ledger.md`.
"""

_CLIENT_PROBE_TIMEOUT_SECONDS = 30.0
"""Bounded, because this runs before any SQL and a wedged daemon must not turn
one statement into an indefinite wait — but not tightly. 10s was the first
value and it was too short: `docker exec` against a remote context has to bring
up its transport first, so the probe timed out, fell back to the classic name,
and every statement then failed against a container that does not have it. The
answer is cached, so this is paid once per container per run."""

_CONF_KEY_WRITE_SUFFIXES = (".conf",)


class ApplyError(RuntimeError):
    """A step failed in a way that must stop the run (missing template value, git failure, ...)."""


class SqlNotSent(ApplyError):
    """A SQL runner failed before anything could reach the server: no process ever ran (T115).

    The ONE failure a caller may read as "the database was not changed".
    Anything else out of `run_statement()` -- a non-zero `mysql` exit, ERROR 2013,
    a transport error -- can come after the server ran COMMIT (Codex, T115), so
    it proves nothing either way. `DockerSql` raises this where there is no
    docker CLI to start; it is an `ApplyError`, so every existing handler still
    catches it.
    """


CLAIM_FILE = CLONE_MARKER
"""What this app writes INSIDE a clone it made, so it can recognise it later.

The evidence half of `Ownership`. It is written after a clone succeeds and read
before the next one starts, and it is the only thing that separates "this app's
own `modules/mod-x`" from "a `modules/mod-x` the user put there by hand" — the
convention every AzerothCore user follows, at a path this app picks from a
catalog id it did not ask the user about.

Inside the clone rather than beside it for three reasons: `modules/` is scanned
by AzerothCore's CMake and a sibling of the module directories would be a new
kind of entry there; `git reset --hard` does not remove untracked files, so the
claim survives the very update path it authorises; and `remove()` deleting the
clone deletes the claim with it, with no second place to forget about. It joins
`include.sh` as the second file this engine writes into a clone.

The name itself is `git.CLONE_MARKER`, because `is_unmodified()` has to know it
too (T66) and `apply` imports `git`, not the other way round. This is the name
every OTHER use in the tree reads.
"""

_NOT_FOR_THE_CLIENT = shutil.ignore_patterns(".git", CLAIM_FILE)
"""What a `client` copy leaves in the clone: git's own directory, and the claim.

T65, and `Applier._client()` carries the measurement. Both names are this app's
or git's bookkeeping, neither is ever read by the game, and `.git`'s 0444 pack
files are what made the second install of a `src: "."` addon die.

A pattern list rather than a top-level name check, so a nested repository
inside somebody's addon is left behind too — it has the same read-only packs
and the same nothing to offer a WoW client.
"""

CLAIM_VERSION = 1
"""Bumped only for a change this version could not read. A reader that does not
recognise the version answers `UNKNOWN`, which refuses — never `UNCLAIMED`,
which would let a newer app's clone be treated as a stranger's.

NOT bumped for `client_files` (T67): the key is additive and every reader of an
older claim, which simply has no such key, gets the empty tuple and the
"no record" arm — which leaves the file alone. A bump would have made every
clone installed by an earlier build read `UNKNOWN`, i.e. unremovable."""


@dataclass(frozen=True)
class ClientCopy:
    """One file this app copied into the game client's `Data/`, and the bytes it copied.

    The receipt `remove()` needs in order to be allowed to delete anything from a
    folder full of the user's own game (T67). `path` is the destination as it was
    written, absolute and in the OS's own spelling; `sha256` is of the bytes that
    landed there, taken from the DESTINATION after the copy rather than from the
    source, so a copy that truncated is recorded as what is really on disk.

    `step` is the manifest step's `src`, and `_unclient()` DOES match on it: a
    step's receipts have to be found per step, because "this step has no record"
    is the sentence a user gets about a step whose files must be left alone, and
    it cannot be said by a lookup keyed on path alone. What identifies the FILE
    is still `path`; `step` only groups.
    """

    step: str
    path: str
    sha256: str

    def as_json(self) -> dict[str, str]:
        return {"step": self.step, "path": self.path, "sha256": self.sha256}


def sha256_of(path: Path) -> str:
    """The file's SHA-256, read in chunks. An MPQ is hundreds of megabytes."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_client_copies(clone: Path, *, item_id: str) -> tuple[ClientCopy, ...]:
    """The client-file receipts this app wrote into `clone`'s claim, or `()`.

    `()` for every doubt: no claim, a claim that will not parse, a claim of
    ANOTHER item, a `client_files` that is not a list, an entry that is not an
    object of three strings. Every one of those means "this app cannot show that
    it put that file there", and `remove()`'s rule is that it deletes nothing
    from the user's game client it cannot show it put there.

    Note what is NOT checked: `clone_id`. A claim written before the server
    folder was moved names the old location and reads `UNKNOWN` for ownership —
    but the client path inside it is still a path this app wrote, with the hash
    of the bytes it wrote, and the hash is what authorises the delete. Ownership
    of the clone is a separate question, asked separately, before this runs.
    """
    parsed = _parse_clone_claim(clone)
    if parsed is None or parsed.get("item_id") != item_id:
        return ()
    raw = parsed.get("client_files")
    if not isinstance(raw, list):
        return ()
    out: list[ClientCopy] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        step, path, digest = entry.get("step"), entry.get("path"), entry.get("sha256")
        if isinstance(step, str) and isinstance(path, str) and isinstance(digest, str):
            out.append(ClientCopy(step=step, path=path, sha256=digest))
    return tuple(out)


COMPLETED_KEY = "install_completed"
"""The claim's key for "every step of `install()` ran", written by `install()` twice.

`False` when the claim goes in, right after the clone or the copy; `True` from
the LAST thing `install()` does. Between those two writes the folder exists and
the install has not finished, which is the state T68 is about: the T7 direct-SQL
guard refuses while the world runs, the clone is already at `modules/<id>`, and
the row read `Remove` with no Install left to press.

**Additive, and deliberately NOT a `CLAIM_VERSION` bump.** A bump makes every
older claim unreadable, which answers `UNKNOWN`, which refuses both install and
remove — a permanent lockout on every clone an older build made, for a key that
says nothing about ownership. So the version stays 1 and this key is read as a
three-state: `False` is this build saying the install stopped, `True` is this
build saying it finished, and ABSENT is a claim written before this key existed.

Absent counts as finished (`clone_install_unfinished()` answers False), and that
is the only reading that cannot make things worse. Older builds wrote the claim
in the same place this one does — immediately after the clone and before
`_deploy`/`_sql`/`_conf`/`_client`/`_dbc` — so an older half-install and an
older whole install leave byte-identical claims and nothing on disk tells them
apart. Reading absent as UNFINISHED would therefore flip every module installed
by every previous build back to an Install button; reading it as finished leaves
those rows exactly as they are today.
"""


def read_clone_claim(clone: Path, *, item_id: str) -> Ownership:
    """Did THIS app clone THIS item into THIS folder? The three-answer version.

    Deliberately a copy of `catalog.native.read_claim()`'s SHAPE and none of its
    contents, because the two claims prove different things. There, one record
    covers a whole server install and the clone stages corroborate it against
    `git remote get-url origin`, since the record says nothing about any
    particular sub-checkout. Here the record is per-clone: it is inside the very
    directory in question and names the item whose catalog id chose the path, so
    it is the corroboration rather than something needing it.

    `UNKNOWN` for a file that will not open, will not parse, is not an object,
    carries a version this code does not know, or names another folder or
    another item. All of those are "there is something here and this app cannot
    read it as its own", and the native engine paid for treating that as absent:
    a corrupt state file made it MORE confident than a missing one, and
    `git reset --hard` ran over a user's checkout. Not repeated here.

    The identity is `composegen.install_id()` over the clone's own path — the
    same normalisation (absolute, forward slashes, case-folded on Windows) the
    install engine uses on a server dir — so a COPIED server folder carries
    claims that describe directories somewhere else, and answers `UNKNOWN`
    rather than authorising a reset inside the copy.
    """
    if not (clone / CLAIM_FILE).is_file():
        return Ownership.UNCLAIMED
    parsed = _parse_clone_claim(clone)
    if parsed is None:
        return Ownership.UNKNOWN
    if parsed.get("item_id") != item_id or parsed.get("clone_id") != composegen.install_id(clone):
        return Ownership.UNKNOWN
    return Ownership.OWNED


def _parse_clone_claim(clone: Path) -> dict[str, object] | None:
    """The claim file's object, or `None` if there is nothing usable at that name.

    Split out of `read_clone_claim()` because two questions are asked of the same
    file and only one of them is ownership. The other is
    `claim_written_by_this_app()`'s: not "is this folder mine?" but "is this a
    record THIS app wrote, that has stopped matching the folder it is in?"

    The absent/unreadable distinction stays with the CALLER, which is where it
    has to be: `read_clone_claim()` answers `UNCLAIMED` for a missing file and
    `UNKNOWN` for an unreadable one, and collapsing those two is the native
    engine's 2026-08-31 bug (`native.read_claim()`).
    """
    path = clone / CLAIM_FILE
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            parsed = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning(f"{path} could not be read, so this folder is not treated as ours: {exc}")
        return None
    if not isinstance(parsed, dict) or parsed.get("version") != CLAIM_VERSION:
        return None
    return parsed


def claim_written_by_this_app(clone: Path, *, item_id: str) -> bool:
    """A claim that parses, is this version's, names THIS item — and another folder.

    Strictly weaker than `Ownership.OWNED`: everything matches except the one
    field that says WHERE the clone was when it was written. Only `remove()`
    acts on it, and `Applier._require_own_clone()` argues why.

    `clone_id` must be a string that simply differs. A record with the key
    missing or of the wrong type is malformed, not relocated, and stays
    `UNKNOWN` — the point of this predicate is that it recognises this app's own
    handwriting, not that it is lenient.
    """
    parsed = _parse_clone_claim(clone)
    if parsed is None:
        return False
    return (
        parsed.get("item_id") == item_id
        and isinstance(parsed.get("clone_id"), str)
        and parsed.get("clone_id") != composegen.install_id(clone)
    )


def clone_install_completed(clone: Path, *, item_id: str) -> bool | None:
    """Did THIS app's install of `item_id` into `clone` finish? Three answers (T68).

    `None` is "this app has no claim of its own here" — no file, an unreadable
    one, another app's, another item's, another folder's — and it is neither of
    the other two: there is no install of ours to have finished or not.

    `True` for a claim of ours whose `COMPLETED_KEY` is anything but the boolean
    `False`, which deliberately includes ABSENT (an older build's claim, see
    `COMPLETED_KEY`) and anything malformed. `False` only for the exact boolean
    this app writes. The asymmetry is the direction that cannot make things
    worse: `False` is what puts an Install button back on a row whose folder is
    on disk, so only this app's own handwriting may produce it.
    """
    if read_clone_claim(clone, item_id=item_id) is not Ownership.OWNED:
        return None
    parsed = _parse_clone_claim(clone)
    if parsed is None:
        return None
    return parsed.get(COMPLETED_KEY) is not False


def clone_install_unfinished(clone: Path, *, item_id: str) -> bool:
    """Does THIS app's claim in `clone` say the install stopped before it finished? (T68)

    The one state `clone_install_completed()` answers `False` for, under the
    name the Modules tab asks the question in. `None` — no claim of ours — is
    not unfinished: a row offering Install for a folder this app did not make
    is a press `_require_own_clone()` refuses, and the tab must not offer it.
    """
    return clone_install_completed(clone, item_id=item_id) is False


def unfinished_clones(server_dir: Path) -> dict[str, frozenset[str]]:
    """Which clones per family have a claim saying their install never finished (T68).

    Shaped like `installed_clones()` and read the same way, one folder per
    family, so the Modules tab can ask the two questions side by side and key
    both answers by `(family, id)`. A name here is always a name there: this
    walks the same directories, and every id it returns is one `installed_clones
    ()` listed.

    `item_id=name` is the directory name, which is what `Applier.clone_dir()`
    built the path from (`CLONE_DIRS[type] / manifest.id`) — so a claim that
    names a different item is a folder some other install put here under a name
    this one uses, and it answers False rather than offering a press.

    Costs one small JSON read per clone on top of the directory listing, paid on
    every reload beside `installed_clones()`. That is the same order of work as
    the listing itself and nothing like `module_updates()`' fetch per checkout,
    which is why it rides with the cheap half.
    """
    return {
        str(kind): frozenset(
            name
            for name in docker.clone_names(server_dir / folder)
            if clone_install_unfinished(server_dir / folder / name, item_id=name)
        )
        for kind, folder in CLONE_DIRS.items()
    }


def server_dir_claim(server_dir: Path) -> Ownership:
    """Did THIS app create THIS server directory? The install engine's own record.

    Evidence from OUTSIDE any module clone, which is what makes it worth asking
    at all: `.yulon-install.json` sits at the server dir, is written only by
    `catalog.native`'s staged installer, and records the `install_id()` of the
    directory it was written in. A user hand-installing a module into their own
    AzerothCore tree cannot produce one, and a COPIED server folder carries one
    that names the original's path — so this answers `UNKNOWN` there for the same
    reason `read_clone_claim()` does.

    The `install_id` comparison is done here rather than left to
    `native.read_claim()`, which only says whether the file parsed:
    `StagedInstaller.claimed_this_folder()` checks the identity against the state
    the guard already validated, and this caller has no such state to check
    against — only the folder.

    `valid=()` because the stage names are the install engine's business and the
    only field read here is the identity; an unknown stage name being dropped
    from `completed` cannot change that answer.

    `catalog.native` is imported INSIDE this function, and it is the only thing
    in this module that wants it. Naming it at module scope would make the
    game-agnostic apply engine — imported by `networking`, `accounts`,
    `maintenance`, `repair` and the UI — drag the whole native install engine
    (docker, the staged installer, threads and queues) in behind it, for one
    JSON file at the server dir. It is not a cycle today; it is a dependency
    nobody asking `Applier` to run some SQL should have to load. `Applier`
    takes this as a seam, so the production default is the only caller.
    """
    from yulon.catalog import native

    claim = native.read_claim(server_dir, valid=())
    state = claim.state
    if state is None:
        return claim.ownership
    if state.install_id != composegen.install_id(server_dir):
        return Ownership.UNKNOWN
    return Ownership.OWNED


_SERVER_DIR_CLAIM: Callable[[Path], Ownership] = server_dir_claim
"""`server_dir_claim()` under a name that `Applier.__init__`'s parameter cannot shadow.

The seam is called `server_dir_claim` because that is the question it answers,
and inside that signature the name is the parameter. This is how the default
still reaches the function."""


def write_clone_claim(
    clone: Path,
    *,
    item_id: str,
    url: str,
    completed: bool = False,
    client_files: Sequence[ClientCopy] = (),
) -> None:
    """Record that this app put `item_id`'s clone here. Raises `OSError` if it cannot.

    `url` is written for a human reading the file; it is never what ownership is
    decided on, because a URL is what everybody with the same catalog entry has.

    `completed` is `COMPLETED_KEY`'s value and defaults to the honest answer at
    the moment a claim is first written for a NEW clone: the folder is filled and
    none of the install's remaining steps has run. `install()` writes it again
    with `True` once they all have.

    **EVERY call writes the WHOLE record, so a key this call does not carry is a
    key the claim loses.** That is the atomic-rename argument below — a
    read-modify-write would give a torn claim a second chance to exist — and it
    is the one thing a caller adding a field here has to know. It is why
    `install()` reads the claim's current `completed` BEFORE the clone and hands
    it back to the first write (a re-install that fails mid-way must not demote
    a module that was finished), and why the SECOND write is a single call in
    `_finish_claim()` rather than one call per fact: two writes, each carrying
    its own key and defaulting the other's, would leave whichever ran first
    erased. A new fact belongs in this signature as another keyword with a
    default, and in `_finish_claim()`'s one call — not in a write of its own.

    **Written somewhere else in the same directory and then renamed over the
    real name, never straight into it.** A plain write opens `CLAIM_FILE` for
    truncation and then fills it, so a full disk, a killed process or a lost
    power cable at the wrong instant leaves a HALF file at that name — and a
    half file is the worst of the three possible states. It does not parse; a
    claim that does not parse reads `UNKNOWN`; `UNKNOWN` refuses every caller;
    and `remove()`'s relocation licence needs a claim that PARSES, so it refuses
    too. The user is then left with a module this app installed, that this app
    will not uninstall, and no route back that does not involve finding and
    deleting a dotfile by hand. That is a permanent lockout caused entirely by
    metadata this app alone writes and reads.

    `os.replace()` of a fully written file is atomic on POSIX and on Windows, so
    a reader sees the old claim or the new one and never a fraction of either.
    The temporary file has to be in the SAME directory — a rename across
    filesystems is a copy, which is exactly the tearing being avoided — and the
    clone directory is where it goes. It is cleaned up on failure so a refusal
    never leaves debris inside a checkout `git status` will report on.

    `client_files` are T67's receipts for what went into the user's game client.
    They are written by a SECOND call, after the client copy has happened, over
    the claim the install already wrote — the claim has to exist from the moment
    the clone does (a killed install leaves a folder this app must still
    recognise), and the receipts cannot exist until the bytes have landed. The
    two orders of failure both fail safe: a claim with no receipts leaves the
    client file alone, and there is no state in which a receipt exists for a copy
    that did not happen.
    """
    payload: dict[str, object] = {
        "version": CLAIM_VERSION,
        "item_id": item_id,
        "clone_id": composegen.install_id(clone),
        "url": url,
        COMPLETED_KEY: completed,
    }
    if client_files:
        payload["client_files"] = [copy.as_json() for copy in client_files]
    fd, name = tempfile.mkstemp(dir=clone, prefix=CLAIM_FILE + ".", suffix=".tmp")
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, clone / CLAIM_FILE)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


_CLIENT_NAMES: dict[str, tuple[str, ...]] = {
    "mysql": ("mysql", "mariadb"),
    "mysqldump": ("mysqldump", "mariadb-dump"),
}
"""Every name each tool may have inside the database container, AzerothCore's first.

The order is the order the container is asked in, and it is only a guess. A
caller that knows which client this game's image ships passes it, and
`_candidates()` moves that spelling to the front — see `_DECLARED_SPELLINGS`."""

_DECLARED_SPELLINGS: dict[str, dict[str, str]] = {
    "mysql": {"mysql": "mysql", "mariadb": "mariadb"},
    "mysqldump": {"mysql": "mysqldump", "mariadb": "mariadb-dump"},
}
"""How each declared `install.native.db.client` spells each tool.

The catalog's `DbFacts.client` is one of `mysql` / `mariadb` and names the
FAMILY, not a binary: the dump tool of the `mariadb` family is `mariadb-dump`,
not `mariadb`. This table is where that translation lives, so no caller has to
know that `mysqldump` and `mariadb-dump` are the pair.

A `tool` or a `client` this table does not know leaves `_CLIENT_NAMES`' order
untouched rather than inventing a name."""

_client_cache: dict[tuple[str, str, str], str] = {}
"""Resolved names, keyed by container, tool AND declared client.

The client is in the key because it decides the ORDER the container is asked
in, and the order decides the answer for an image that ships both spellings
(`command -v a || command -v b` short-circuits on the first that exists)."""


def _candidates(tool: str, client: str | None) -> tuple[str, ...]:
    """Every spelling of `tool`, with the one this game's entry declares first.

    The declared spelling is put in FRONT of the others rather than used
    instead of them, which is the whole difference between reading the catalog
    and trusting it: an image that has been rebuilt, or an entry whose `client`
    was written from the image tag rather than from the image, still resolves
    to the binary that is actually in the container. What the declaration buys
    is the case where nobody can be asked — see `mysql_client()`'s fallback.
    """
    names = _CLIENT_NAMES.get(tool, (tool,))
    declared = _DECLARED_SPELLINGS.get(tool, {}).get(client or "")
    if declared is None or declared not in names:
        return names
    return (declared, *(name for name in names if name != declared))


def mysql_client(db_container: str, tool: str = "mysql", *, client: str | None = None) -> str:
    """The name `db_container` actually answers to for `tool`.

    `client` is the catalog's `install.native.db.client` for this game, or None
    from a caller that does not hold an entry. It changes two things and
    nothing else: which spelling is tried first, and — the reason 7.9 asks for
    it — which one is used when the container cannot be asked at all. Left to
    the guess, that fallback is `mysql`, and a CMaNGOS install on `mariadb:11`
    has no such binary, so every statement afterwards died with `executable
    file not found` rather than with a database error.

    **`mariadb:11` ships neither `mysql` nor `mysqldump`.** MariaDB deprecated
    the `mysql*` symlinks and removed them in 11, leaving only `mariadb` and
    `mariadb-dump`. wow-tbc and wow-vanilla run `mariadb:11`, so every statement
    this app sent them died before it reached a database; wow-tortoise pins
    `mariadb:10.6`, which still has the symlinks, which is why it worked and
    hid this (measured on a live TBC server, 2026-08-26).

    Asked of the container rather than derived from the image tag: the tag is
    not visible from here, images get rebuilt, and `command -v` is the same
    question the shell would ask. The answer is cached per container because it
    cannot change without the container being replaced.

    Falls back to the first candidate when the probe cannot run at all, so a
    daemon hiccup produces the same failure it always did rather than a new one.
    """
    key = (db_container, tool, client or "")
    cached = _client_cache.get(key)
    if cached is not None:
        return cached
    candidates = _candidates(tool, client)
    resolved = _probe_client(db_container, candidates)
    if resolved is None:
        return candidates[0]
    if resolved != candidates[0]:
        logger.info(f"{db_container} has no `{tool}`; using `{resolved}`")
    _client_cache[key] = resolved
    return resolved


def _probe_client(db_container: str, candidates: tuple[str, ...]) -> str | None:
    """Ask the container which of `candidates` it has, or None if it cannot say.

    Its own function so tests can answer for it without also intercepting the
    statements under test — every caller here runs `docker exec`, and a probe
    sharing that seam would show up in argv assertions that are about SQL.
    """
    program = platform.docker_program()
    if program is None:
        return None
    probe = " || ".join(f"command -v {name}" for name in candidates)
    try:
        proc = subprocess.run(
            [program, "exec", db_container, "sh", "-c", probe],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            # `os.environ`, not a bare `child_env()`: the calls this probe is
            # resolving for run with the process environment, so DOCKER_HOST and
            # friends must reach the probe too. Without it the probe talks to a
            # different daemon than the statements do, silently answers "no
            # mariadb here", and every statement then names a binary the real
            # container does not have.
            env=runner.child_env(dict(os.environ)),
            creationflags=runner.creationflags(),
            timeout=_CLIENT_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        # WARNING, not DEBUG: falling back is a guess, and when the guess is
        # wrong every statement afterwards fails with "executable file not
        # found" — a failure that reads like a broken database rather than an
        # unanswered question.
        logger.warning(f"could not ask {db_container} which client it has: {exc}")
        return None
    found = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not found:
        return None
    return found[0].rsplit("/", 1)[-1]


def mysql_env(root_password: str, wsl_distro: str | None = None) -> dict[str, str]:
    """This process's environment plus `MYSQL_PWD`, so the password never enters argv.

    `docker exec -e MYSQL_PWD` (no `=value`) forwards the variable from OUR
    environment into the container, where the client reads it instead of
    prompting. `-p<password>` would put the secret in a command line every local
    process can read (`ps`, Task Manager, `/proc/<pid>/cmdline`).

    Module-level rather than a `DockerSql` method because `maintenance.py` runs
    `mysqldump`, not `mysql`, and so cannot reuse `DockerSql` itself — but the
    one rule that must never be re-derived is how the password is handed over
    (style-guide §4). `wsl_distro` is part of that rule now: a variable set here
    does NOT reach a process inside a distro unless `WSLENV` names it, so both
    callers get the crossing right by using this rather than by remembering.
    """
    if wsl_distro is not None:
        # Crossing into a distro, the variable does not follow just because it
        # is set here - measured, it arrives EMPTY, and mysql then reports an
        # authentication failure against a perfectly healthy database.
        # `wsl_env()` names it in WSLENV, which is what carries it across.
        return platform.wsl_env({"MYSQL_PWD": root_password})
    env = dict(os.environ)
    env["MYSQL_PWD"] = root_password
    return env


# ------------------------------------------------------------------- seams


class SqlRunner(Protocol):
    """Run SQL against one of the server's databases."""

    def run_file(self, db: Db, path: Path) -> None: ...

    def run_statement(self, db: Db, statement: str) -> None: ...


class DbcCopier(Protocol):
    """Copy DBC files from a host directory into the server's `data/dbc/` volume."""

    def copy_dbc_dir(self, src: Path) -> None: ...


class FolderCopier(Protocol):
    """Put the folder at `src` at `dest`, replacing whatever is at `dest`.

    The second way to fill `modules/<id>`, beside `Git.clone`, and a seam for
    the reason `Git` is one: what it does reaches outside the process, and the
    engine's own tests must be able to state what it was handed without a real
    tree on disk. It is deliberately NOT a `Git` variant — a path is not a clone
    URL, and routing a copy through `CloneSpec` would drag the HTTP/1.1 and
    `core.autocrlf` pins, the container mount logic and a `file://` URL into a
    job that is a directory copy.

    Two obligations the implementation carries and this engine does not check:
    the destination is REPLACED rather than merged into (a copy is a snapshot of
    the folder, not a union with an older one), and no `.git` is carried across
    (a copy has no upstream, and a half-copied one would answer `remote_url()`
    with a repository this install has nothing to do with). Raising `OSError` is
    how it reports failure; `Applier._copy_folder()` turns that into the
    applier's own vocabulary.
    """

    def __call__(self, src: Path, dest: Path) -> None: ...


@dataclass(frozen=True)
class FolderSource:
    """A folder to copy in, and the copier that will do it.

    The two travel together because neither is usable alone: a path with no
    copier is an install that silently puts nothing anywhere, and the engine
    must not be able to be handed one. `install(folder=...)` is therefore all or
    nothing, and the dataclass is what makes that true at the call site rather
    than in a runtime check.
    """

    path: Path
    copier: FolderCopier


Completer = Callable[[Manifest, Path], Manifest]
"""Finish a manifest from the content that is now at its clone path.

A manifest DERIVED from a link or a folder — rather than shipped — knows its
id, its name and its type, and cannot know one thing more until the content is
on disk: which `conf/*.conf.dist` to activate, which `data/sql/<db>/` to report.
So the derivation is completed here, inside the install that fetched the
content, rather than by a second clone into a scratch directory followed by a
second install. Handed the clone AFTER it is filled and before any step reads
the manifest; whatever it returns is what every later step reads.

`None` is the shipped case — a manifest whose author wrote every field — and
then nothing is called and nothing changes.
"""


@dataclass(frozen=True)
class DockerSql:
    """`SqlRunner` over `docker exec <db_container> mysql`, like wow-manage.sh does."""

    db_container: str
    root_password: str = field(repr=False)
    """Kept out of the repr, like `maintenance.DockerMysql.root_password`.

    A frozen dataclass reprs every field by default, and this object is handed
    to worker threads and closed over by the seams the tabs call: a pytest
    assertion diff, a logged object or a traceback frame dump in a UI error
    handler would each print the database password. Its `DockerMysql` sibling
    closed this channel on 2026-08-23 and this one was missed, because the two
    are built side by side at every call site (2026-08-30).
    """
    wsl_distro: str | None = None
    """The WSL2 distro this server's docker lives in, if it is not local."""
    schemas: Mapping[Db, str] = field(default_factory=lambda: DB_NAMES)
    """This server's `manifest db key → schema name` map.

    Defaults to `DB_NAMES` so every AzerothCore caller reads as it did, and is
    overridden from `CatalogEntry.schema_map()` for a game whose schemas are
    named anything else. It is per-instance rather than a module constant
    because one process can hold two installs of different cores at once.
    """
    client: str | None = None
    """Which client family this server's database image ships: `install.native.db.client`.

    The sibling of `schemas` and carried for the same reason — a per-install
    fact that used to be a literal in this file. `schemas` says which database
    to connect to; this says which BINARY does the connecting, and on a CMaNGOS
    install both were AzerothCore's.

    None means "nothing was declared", and the resolver then keeps the order it
    always had, so every caller that passes nothing behaves exactly as it did.
    It is a hint and not an instruction either way: `mysql_client()` still asks
    the container, and this only decides the order and the answer when the
    container cannot be asked.
    """

    def run_file(self, db: Db, path: Path) -> None:
        with path.open("rb") as fh:
            proc = self._mysql(db, stdin=fh)
        _check_sql(proc, f"{path.name} → {self._schema(db)}")

    def run_statement(self, db: Db, statement: str) -> None:
        # Over stdin, never `-e <sql>`: argv is world-readable (`ps`, Task
        # Manager, /proc/<pid>/cmdline) and a statement can carry a password.
        proc = self._mysql(db, statement=statement)
        _check_sql(proc, f"inline → {self._schema(db)}")

    def query(self, db: Db, statement: str) -> str:
        """Run one SELECT and return its rows, tab-separated, one per line.

        `run_statement()` discards stdout, which is right for the applier — it
        only ever asserts a step succeeded. Account creation genuinely has to
        read (does this username exist, and what id did it get), so this is the
        read half of the same seam rather than a second one beside it.

        `--skip-column-names` because every caller wants values, not a header,
        and `--batch` so the separator is a tab whether or not the client
        decided it was talking to a terminal.

        The exit code is checked for the same reason `run_statement()` checks
        it, and one more: a reader cannot tell "no rows" from "the query never
        ran". `accounts._account_id()` reads no rows as "this username is free"
        and inserts, so a `query()` that returned "" on failure would turn an
        unreachable database into a green light to write.

        Raises:
            ApplyError: no docker CLI (from `_mysql()`), or `mysql` exited
                non-zero.
        """
        proc = self._mysql(db, statement=statement, extra=("--batch", "--skip-column-names"))
        _check_sql(proc, f"query → {self._schema(db)}")
        return proc.stdout

    def _mysql(
        self,
        db: Db,
        *,
        stdin: IO[bytes] | None = None,
        statement: str | None = None,
        extra: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        """Run one `docker exec ... mysql`, with the missing-CLI guards in one place.

        Exactly one of `stdin` (a file to pipe in) and `statement` (a string to
        pipe in) is given; both arrive as the child's stdin, and neither is ever
        put in argv — see `run_statement()`.

        `subprocess.run` is called here rather than `yulon.runner`, which is a
        style-guide §3 deviation the file already carried: `run_file()` needs
        `stdin=<open file>` and `runner.run()` has no way to express it. Left as
        it was found, and not widened — the point of this method is that the two
        callers stop repeating the call, not that a third gets added.

        Raises:
            ApplyError: There is no docker CLI to run. Both roads to that, since
                the resolution cache remembers a hit: never resolved at all
                (`_argv()`), and resolved earlier to a `docker.exe` that has
                since been uninstalled or moved by a Docker Desktop update
                (the `OSError` here). The second used to surface as a bare
                `[WinError 2]` (review, 2026-08-23).
        """
        argv = self._argv(db, extra=extra)
        try:
            return subprocess.run(
                argv,
                stdin=stdin,
                input=statement,
                capture_output=True,
                text=True,
                # Not the default strict decode. `text=True` alone raises
                # UnicodeDecodeError out of here on any byte mysql emits that is
                # not UTF-8 -- a binary column selected as text, or a latin1
                # error message -- and that type is neither `ApplyError` nor the
                # `AccountError` that `accounts.create_account` documents as the
                # only one a caller has to handle. `runner.py` already decodes
                # this way. Found by a live query against a real server
                # (2026-08-23).
                errors="replace",
                check=False,
                env=runner.child_env(self._env()),
                creationflags=runner.creationflags(),
            )
        except OSError as exc:
            # Logged with the real errno first, the way `docker._docker()` does, so a
            # docker.exe blocked by an ACL or by AV leaves evidence instead of being
            # reported to the user as "install Docker Desktop" with nothing in the log
            # to contradict it (review finding, 2026-08-23).
            logger.warning(f"{argv[0]} could not be started: {exc}")
            raise SqlNotSent(platform.DOCKER_CLI_MISSING_HELP) from exc

    def _env(self) -> dict[str, str]:
        # The distro goes with the password. Without it `mysql_env()` builds an
        # environment with no WSLENV, so `docker exec -e MYSQL_PWD` forwards a
        # variable that arrives EMPTY inside the distro and mysql reports an
        # authentication failure against a healthy database. The unit test for
        # WSLENV called `mysql_env()` directly and so never saw this.
        return mysql_env(self.root_password, self.wsl_distro)

    def _argv(self, db: Db, *, extra: tuple[str, ...] = ()) -> list[str]:
        """`docker exec ... mysql <db>`, with the CLI name this host can start.

        `extra` carries client flags that only one caller wants (`query()`'s
        output formatting). It defaults to empty so the write path's argv is
        byte-identical to what it was before the read path existed.

        Raises:
            ApplyError: no docker CLI here. A manifest apply runs straight
                after an install, on the same process that may still be blind
                to the PATH Docker Desktop's installer wrote — see
                `platform.docker_program()`.
        """
        prefix = platform.docker_prefix(self.wsl_distro)
        if prefix is None:
            raise SqlNotSent(platform.DOCKER_CLI_MISSING_HELP)
        return [
            *prefix,
            "exec",
            "-i",
            "-e",
            "MYSQL_PWD",  # value taken from OUR env by `docker exec`, not written here
            self.db_container,
            mysql_client(self.db_container, client=self.client),
            "-uroot",
            *extra,
            self._schema(db),
        ]

    def _schema(self, db: Db) -> str:
        """The schema name for `db` on THIS server, or a refusal naming it.

        A missing key is a database this core does not have, and there is no
        safe fallback: connecting to the AzerothCore name instead is what
        produced `Unknown database 'acore_auth'` on every CMaNGOS install, and
        connecting to some other schema of this server's would be worse.
        Raised before the argv is built, so nothing runs.
        """
        try:
            return self.schemas[db]
        except KeyError:
            raise ApplyError(
                f"this server has no {db} database; it has " f"{', '.join(sorted(self.schemas))}"
            ) from None


_DBC_WRITE = 'cat > "$1.yulon-part" && mv -f "$1.yulon-part" "$1"'
"""The shell each DBC file is written with, the destination passed as `$1`.

A positional argument rather than a name spliced into the script, so a file
name is never parsed as shell. Written beside and then renamed over, so a copy
that dies part-way leaves the server's own file whole rather than truncated —
a half-written `CharBaseInfo.dbc` is a worldserver that will not start. No
`mkdir -p`: a data volume with no `dbc/` folder holds no server data at all, and
creating one to put three files in would report a copy into a server that
cannot run."""


@dataclass(frozen=True)
class ComposeDbc:
    """`DbcCopier` over the compose service that owns the server's data volume (T62).

    Until this existed nothing implemented `DbcCopier`, so every `server_dbc`
    step — `mod-arac`'s three race/class DBCs and the Season of Discovery keg's
    — was reported skipped and never reached the server: new race/class
    combinations started with no gear and the worldserver deleted their skills
    (`heyitsbench/mod-arac#49`, `#50`).

    The worldserver mounts that volume `:ro`, so the copy goes through the
    one-shot service that mounts it read-write and filled it in the first place
    (`ac-client-data-init` for AzerothCore). That service runs as the user that
    wrote the files already there, so the new ones are owned alike. How the
    bytes travel is `docker.compose_run_stdin()`'s docstring: over stdin, one
    short-lived container per file, which is the bash launcher's own shape
    (`copy_server_dbc`, `wow-manage.sh` on `upstream/main`) without its bind
    mount or its `alpine` pull.

    The DBCs are read at worldserver start only, so a copy changes nothing
    until a restart; `ApplyReport.restart_recommended` already says so for any
    manifest with a `server_dbc` step.
    """

    server_dir: Path
    service: str
    """The compose SERVICE that mounts the data volume read-write."""
    data_dir: str
    """Where that service mounts the volume; the DBCs go in `<data_dir>/dbc/`."""
    wsl_distro: str | None = None
    """The WSL2 distro this server's docker lives in, if it is not local."""

    def copy_dbc_dir(self, src: Path) -> None:
        if not src.is_dir():
            raise ApplyError(f"server DBC folder missing in clone: {src}")
        files = sorted(p for p in src.iterdir() if p.is_file() and p.suffix == ".dbc")
        if not files:
            # Refused, not passed over: `_dbc()` writes a done line after this
            # returns, and "copied" over a folder holding nothing is the claim
            # this seam was built to stop the report making.
            raise ApplyError(f"no .dbc files in {src}, so there was nothing to copy")
        for path in files:
            dest = f"{self.data_dir.rstrip('/')}/dbc/{path.name}"
            try:
                with path.open("rb") as fh:
                    proc = docker.compose_run_stdin(
                        self.server_dir,
                        self.service,
                        "sh",
                        ["-c", _DBC_WRITE, "sh", dest],
                        fh,
                        wsl_distro=self.wsl_distro,
                    )
            except (docker.DockerCommandError, docker.SourceUnreadableError) as exc:
                raise ApplyError(str(exc)) from exc
            if proc.returncode != 0:
                reason = proc.stderr.strip() or proc.stdout.strip()
                raise ApplyError(
                    f"could not copy {path.name} into the server's data volume through "
                    f"{self.service}: {reason}"
                )


def _check_sql(proc: subprocess.CompletedProcess[str], what: str) -> None:
    """Raise with the reason, wherever the reason happens to be.

    `docker exec` reports its OWN failures on STDOUT, not stderr — a container
    missing the client binary answers

        OCI runtime exec failed: ... exec: "mysql": executable file not found

    on stdout with stderr empty. Reading only stderr turned that into
    `SQL failed (query -> realmd):` with nothing after the colon, which is the
    least useful message this app can produce; it cost an hour of looking in the
    wrong place (2026-08-26). mysql's own errors still arrive on stderr, so
    stderr stays first and stdout is the fallback.
    """
    if proc.returncode == 0:
        return
    reason = proc.stderr.strip() or proc.stdout.strip()
    raise ApplyError(f"SQL failed ({what}): {reason}")


# ------------------------------------------------------------------ report


@dataclass(frozen=True)
class PendingSql:
    """SQL this run put on disk for AzerothCore's importer and did NOT apply.

    `applied_by="db-import"` hands a module's `data/sql/**` to upstream's own
    `UpdateFetcher`, which is right — applying those files by hand leaves the
    `updates` ledger without their hashes and a later real import re-runs them.
    But "right to defer" was written down as `done`: until 2026-09-07 `_sql()`
    appended `sql <glob> → <db>: left to ac-db-import on next start` to the done
    list having run nothing and checked nothing, not even that the glob matched
    a file, and `controller_view._format_report()` ticks every done entry. The
    live applier on yulon-ubuntu therefore reported

        DONE: sql data/sql/db-world/*.sql -> world: left to ac-db-import on next start

    over an install where nothing whatever had been applied and nothing was
    going to be: `docker.start_staged()` names the three long-running services
    so a Start never reaches the importer, and `docker.repair_import()` refuses
    a database that is already complete.

    So the deferral is a value now, and it is a value rather than a sentence
    because the caller that has to tell "applied" from "not applied yet" must
    not do it by grepping English — that would be this same defect one layer up.

    `files` is the glob RESOLVED against the clone, so the count a reader is
    shown is one this run actually took:

    * a tuple of clone-relative paths — what is on disk waiting,
    * `()` — the glob matched nothing, which is not an error and is NOT "this
      module has no SQL". Measured on the real clones, yulon-ubuntu 2026-09-07:
      `mod-aoe-loot` keeps its one file in `data/sql/db-world/base/`, one
      directory below the `data/sql/db-world/*.sql` its manifest names, while
      `mod-solocraft` (1 file) and `mod-transmog` (3, plus an `updates/` folder)
      put theirs exactly there. The layout is per-repository and this pattern is
      Yu'lon's own bookkeeping — `UpdateFetcher.cpp:159-186` joins the module's
      `data/sql` path and walks it itself, so upstream applies what this misses.
      An empty answer therefore means "this app cannot count", and the caller
      must not draw it as "nothing to do",
    * `None` — the path carries a `{key}` and this run could not resolve it.
      A third answer for the same reason `docker.importer_sees_modules()` keeps
      one: globbing the raw `{key}` would match nothing and report a confident
      "no files". No shipped manifest has such a path today (all 20 db-import
      steps across 15 manifests are literal `data/sql/db-*/*.sql`), and
      `_action_templates()` deliberately leaves db-import paths out of
      `required_prompts()`, so there is no value to render one with.
    """

    db: Db
    path: str
    files: tuple[str, ...] | None


@dataclass(frozen=True)
class ApplyReport:
    """What one install/configure/remove run did, did not do, and still needs.

    Four lists, because a run has four outcomes and only the first two were ever
    written down: `done` is what happened, `skipped` is what could not happen,
    `pending_sql` is what was deliberately left to another program to do (see
    `PendingSql`), and `rebuild_required` is what no program in this app can do
    at all. A caller can answer "is this module's SQL in the database?" from
    `pending_sql` alone, without reading a word of the report.
    """

    action: When
    item_id: str
    family: ManifestType = field(kw_only=True)
    """Which family `item_id` belongs to (T48). Required, and by keyword.

    An id alone does not name a row: two families can ship the same id, and
    since T42 round 2 the view keys its manifests, chips and banner by
    `(family, id)`. The report was the one thing that still forgot. No default,
    because a default would be a guess at the family -- and a chip on the wrong
    family's row is a lie, where a chip missing is only a gap.
    """
    done: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    rebuild_required: bool = False
    restart_recommended: bool = False
    pending_sql: tuple[PendingSql, ...] = ()
    left_behind: tuple[str, ...] = ()
    """What a REMOVE did not take back, one entry per step, each a plain phrase (T62).

    `remove()` deletes the clone and what `deploy` put elsewhere, and runs any
    remove-time SQL. It never touches the server's data volume, and it does not
    undo install-time SQL a manifest gave no remove-time counterpart. A report of
    `rm -r modules/mod-arac` and nothing else reads as a clean uninstall of a
    module whose DBCs, client patch and database rows are all still in place — so
    those are named here. Empty for every other action.

    Since T67 the client half of this list is a fact about THIS RUN rather than a
    reading of the manifest. A `Data/` patch this app can show it copied and
    that nobody has edited since IS taken back, and says so in `done`; what is
    named here is what was left and WHY — changed since install, no record of the
    copy, no game client folder to reach, or an addon folder, which is never
    deleted because the game can be told to ignore it instead.
    """


@dataclass
class _Log:
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    pending_sql: list[PendingSql] = field(default_factory=list)
    # Set by `_conf()` the moment it actually writes a byte to a deployed conf
    # file — a template copied in, or a key set — never by a conf step that
    # found nothing to do (the file already there, or no keyed value to write).
    # See `_report()`: this is the fourth thing `restart_recommended` can now see.
    conf_restart: bool = False
    # T67. Filled by `_client()` on an install (the receipts that go into the
    # claim) and by `_unclient()` on a remove (the client lines of `left_behind`,
    # which on a remove replace the ones `_left_behind()` reads off the manifest).
    client_copies: list[ClientCopy] = field(default_factory=list)
    client_left_behind: list[str] = field(default_factory=list)


class _NoAdoption(Enum):
    """Why an existing checkout was not adopted — one value per fact, per answer.

    `Applier._adoption_refusal()` asks three questions of a checkout whose
    `origin` already matches, and each of the last two has THREE answers, not
    two. Carrying only "adopted / not adopted" out of that made one sentence do
    the work of five, and one of the five was a lie: fact 4 fetches, so a user
    with no internet connection reaches it having passed facts 2 and 3 — the
    folder is provably one this app installed and provably has nothing
    uncommitted in it — and was then told there was no record of it and that
    continuing would throw away their changes. Both halves untrue, to the one
    user who had done nothing at all.

    So the answer that leaves the guard is the fact that stopped it, and
    `_no_adoption_message()` turns each into its own sentence. Collapsing states
    into one answer is the fault this whole guard exists to undo (`Ownership`
    counts the three times it has bitten this codebase); it would be a poor joke
    to re-commit it in the message that reports it.

    Fact 2 is the one place two answers still share a value, and it is on
    purpose: a server dir with no `.yulon-install.json` and one whose file will
    not parse both arrive as `NO_RECORD`. Neither says this app installed the
    folder, so the user's situation and their remedy are the same either way —
    unlike facts 3 and 4, where "no" and "could not ask" describe two different
    people. Written down rather than left to be discovered.
    """

    NO_RECORD = "no-record"
    EDITED = "edited"
    TREE_UNSEEN = "tree-unseen"
    COMMITTED = "committed"
    HISTORY_UNSEEN = "history-unseen"


def _no_adoption_message(refusal: _NoAdoption, rel: str, retry: str) -> str:
    """The sentence each refusal gets: what was found, and what to do about it.

    Written out one by one rather than assembled from clauses, because the whole
    point is that they differ — a template with a hole in it is how they became
    the same sentence in the first place. What they share is the remedy, and the
    remedy is shared because it really is the same: move the folder aside and
    press the button again. Except when nobody could look, where the remedy is
    to make looking possible.
    """
    aside = (
        "Move that folder aside — a module clone holds nothing but the module, so re-cloning "
        f"it costs only the download — and then {retry}."
    )
    messages = {
        _NoAdoption.NO_RECORD: (
            f"{rel} is already a git checkout and there is no record here of one this app "
            f"made. Continuing would run `git fetch` and `git reset --hard` over it, which "
            f"throws away anything you have changed there, so nothing was touched. {aside}"
        ),
        _NoAdoption.EDITED: (
            f"{rel} is a checkout of the repository this module comes from, but it has "
            f"changes in it that were never committed. Continuing would run `git fetch` and "
            f"`git reset --hard` over it, which throws those changes away, so nothing was "
            f"touched. {aside}"
        ),
        _NoAdoption.TREE_UNSEEN: (
            f"{rel} is a checkout of the repository this module comes from, but git would "
            f"not say whether anything in it has been changed. Adopting it would mean "
            f"running `git fetch` and `git reset --hard` over a folder this app could not "
            f"look inside first, so nothing was touched. {aside}"
        ),
        _NoAdoption.COMMITTED: (
            f"{rel} is a checkout of the repository this module comes from with nothing "
            f"uncommitted in it, but it carries commits of your own. Continuing would run "
            f"`git fetch` and `git reset --hard` over it, which moves those commits off the "
            f"branch and leaves them reachable only through git's reflog, so nothing was "
            f"touched. {aside}"
        ),
        _NoAdoption.HISTORY_UNSEEN: (
            f"{rel} is a checkout of the repository this module comes from and nothing in it "
            f"has been changed, but this app could not reach that repository to check whether "
            f"the checkout also carries commits of its own. It will not run `git fetch` and "
            f"`git reset --hard` over a folder it could not finish checking, so nothing was "
            f"touched. That check needs the internet: get back online and {retry}. If the "
            f"checkout is your own work rather than an older install, move that folder aside "
            f"and {retry} instead."
        ),
    }
    return messages[refusal]


class _Destroys(Enum):
    """What a `git fetch` + `git reset --hard` over an existing clone would cost.

    The three questions T44 gave `update()`, lifted out of it so `install()` can
    ask exactly the same ones over exactly the same folder (T47). One value per
    fact per answer, for `_NoAdoption`'s reasons — `None` out of a seam is "git
    could not be asked", which is a different person from "no".

    They are the questions a RESET must answer, not questions about updating, so
    nothing here names a button: `_destroys_message()` takes the word for what
    the user pressed and is the only place either route's vocabulary appears.
    """

    REPO_UNSEEN = "repo-unseen"
    OTHER_REPO = "other-repo"
    EDITED = "edited"
    TREE_UNSEEN = "tree-unseen"
    COMMITTED = "committed"
    HISTORY_UNSEEN = "history-unseen"


@dataclass(frozen=True)
class _Reset:
    """A `_Destroys` and the `origin` that was read while finding it.

    `remote` travels with the fact because two of the six sentences name it and
    re-reading it to write one would be a second `git remote get-url` whose
    answer could differ from the one the guard actually decided on.
    `""` where the fact was found without asking (the tree and HEAD questions
    are reached only once the repository question has passed, so those carry a
    real one; `REPO_UNSEEN` is the case where git would not say).
    """

    fact: _Destroys
    remote: str


def _destroys_message(found: _Reset, rel: str, item_id: str, url: str, doing: str) -> str:
    """The sentence for one refused reset, in the words of the button that was pressed.

    `doing` is "Updating" or "Installing" — the ONE thing that differs between
    the two callers, and a parameter rather than two copies of six sentences.
    T44 wrote these for `update()`; T47 found `install()` reaching the same
    `reset --hard` over the same folder with none of them, and a copy would have
    been the third place this vocabulary lives.

    Every one ends "Nothing was changed.", which is true of both routes: the
    guard runs before the clone seam, before the folder copy, and before this
    app writes anything into the checkout.
    """
    lower = doing.lower()
    messages = {
        _Destroys.REPO_UNSEEN: (
            f"{rel} is a git checkout, but git would not say what it is a checkout of, so "
            f"{item_id} was not {'updated' if doing == 'Updating' else 'installed'} and "
            f"nothing was changed."
        ),
        _Destroys.OTHER_REPO: (
            f"{rel} is a checkout of {found.remote}, not of {url}. {doing} {item_id} would "
            f"reset that folder to {url} and then deploy this module over it. Nothing was "
            f"changed."
        ),
        _Destroys.EDITED: (
            f"{rel} has changes in it that are not committed. {doing} {item_id} "
            f"runs `git reset --hard`, which would delete them. Commit them, stash "
            f"them, or copy them somewhere else first. Nothing was changed."
        ),
        _Destroys.TREE_UNSEEN: (
            f"git could not say whether {rel} has uncommitted changes in it, and {lower} "
            f"{item_id} would run `git reset --hard` over whatever is there. Nothing "
            f"was changed."
        ),
        _Destroys.COMMITTED: (
            f"{rel} carries commits of its own that {url} does not have. {doing} "
            f"{item_id} runs `git reset --hard`, which would move off them and "
            f"leave them reachable only through git's reflog. Nothing was changed."
        ),
        _Destroys.HISTORY_UNSEEN: (
            f"Yu'lon could not reach {url} to see what {lower} {item_id} would bring "
            f"in, so it did not touch {rel}. Check this machine's connection and try "
            f"again. Nothing was changed."
        ),
    }
    return messages[found.fact]


# --------------------------------------------------- the answers to prompts

_INT = re.compile(r"[+-]?\d+")

_BOOL_WORDS = frozenset({"0", "1", "true", "false", "yes", "no", "on", "off"})
_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})


def _lua_spelling(manifest: Manifest, vals: Mapping[str, str]) -> dict[str, str]:
    """`vals`, with every `bool` prompt's answer spelled `true`/`false` for a Lua file.

    The dialog answers a `bool` as `"1"`/`"0"` because that is what a
    worldserver conf holds (`manifest_prompt._BOOL_CHOICES`), and `check_answer`
    takes either spelling. A `.lua` target reads `ENABLED = 0` as TRUE -- only
    `nil` and `false` are false in Lua -- so a "no" written in conf spelling
    turns a feature ON, and the manifest's `(true|false)` regex then never
    matches that line again.
    """
    kinds = {prompt.key: prompt.kind for prompt in manifest.prompts}
    out = dict(vals)
    for key, value in vals.items():
        if kinds.get(key) == "bool":
            out[key] = "true" if value.strip().lower() in _TRUE_WORDS else "false"
    return out


_SAFE_IN_QUERY = re.compile(r"^[A-Za-z0-9_.-]+$")
"""What an answer may contain before it is pasted into an `ExistsCheck.query`.

Not an escape: a refusal, exactly as `dbreads._SAFE_PREFIX` is one and for the
same reason. Every answer that reaches a check today is a character GUID, and
the set of characters a GUID needs does not include a quote, a semicolon or a
backslash — so the answer to anything else is "I will not use this", rather than
an escaping rule that has to be right on four cores and two SQL modes.
"""


def _fields(template: str) -> set[str]:
    """The `{key}` names a `_render()` of this template would look up."""
    return {
        name.split(".")[0].split("[")[0] for _, name, _, _ in Formatter().parse(template) if name
    }


def _whens(action: When) -> tuple[When, ...]:
    """Which `when` labels an action runs.

    Install runs the configure-time steps too, as the item's FIRST configure
    (T92). Every value-bearing step in the shipped catalog is `when:
    configure` -- the six ALE scripts' Lua keys, `mod-ale`'s conf, `xp-rates`'
    worldserver.conf lines, `battlepass`'s enable row -- and nothing in the
    app calls `configure()`, so until this an install deployed the upstream
    defaults, asked no question (`required_prompts` counted install-time
    templates only, and there were none) and, for `xp-rates`, did nothing at
    all. Measured on yulon-ubuntu 2026-09-22: `sitmeanrest` installed with
    DELAY 10 / RATE 60 answered still read 30 / 5.0 on disk.
    """
    return ("install", "configure") if action == "install" else (action,)


def _action_templates(manifest: Manifest, action: When) -> list[str]:
    """Every string this action would put through `_render()`, in the engine's own order.

    Read off the `Applier` methods below rather than guessed: `_patches` and
    `_sql` filter on `when`, `_sql` returns before rendering for a `db-import`
    step (which is why `mod-ah-bot`'s SQL glob is absent here), and `_conf` runs
    for install and configure only and skips a glob or a non-`.conf` file. A
    template this misses is a value the user is never asked for; a template it
    invents is a question nobody needs to answer — so the two must be kept in
    step, and `test_required_prompts_are_only_the_ones_the_action_actually_renders`
    is what says they are.
    """
    whens = _whens(action)
    out = [patch.replace for patch in manifest.patches if patch.when in whens]
    for step in manifest.sql:
        if step.when not in whens or step.applied_by != "direct":
            continue
        out.append(step.statement if step.statement is not None else step.path or "")
        out += list(step.then)
    if action in ("install", "configure"):
        for conf in manifest.conf:
            if _is_glob(conf.file) or not conf.file.endswith(_CONF_KEY_WRITE_SUFFIXES):
                continue
            out += [key.default for key in conf.keys if key.default is not None]
    return out


def required_prompts(manifest: Manifest, action: When) -> tuple[Prompt, ...]:
    """The manifest's prompts whose value this action would actually render.

    The question the Modules tab has to answer before it can ask a human
    anything, and the reason it is per-ACTION rather than per-manifest: removing
    `mod-ah-bot` renders nothing at all, and a dialog asking for the AH bot's
    GUID before deleting it would be a question about nothing.

    Order is the manifest's own, so a check that reads an earlier answer (
    `mod-ah-bot`'s `bot_account`, whose `ExistsCheck` also names `{bot_guid}`)
    is run after the answer it depends on has been validated.
    """
    wanted: set[str] = set()
    for template in _action_templates(manifest, action):
        wanted |= _fields(template)
    return tuple(prompt for prompt in manifest.prompts if prompt.key in wanted)


def must_ask(
    prompt: Prompt, action: When = "install", remembered: Mapping[str, str] | None = None
) -> bool:
    """Whether a press of `action` puts this required prompt to a person before it runs.

    **Every question, on install** (the owner, 2026-09-24, T104: "ask all,
    remember answers"). The dialog is handed each prompt pre-filled -- with the
    answer this install remembers for it, else the manifest's default -- so
    accepting is one click. Until T104 only a prompt with no default (and, from
    T100, a `choice`) was asked, and every other answer was the default whether
    the player wanted it or not: Stackables on Tortoise/Vanilla/TBC was always
    200, the mob multipliers always theirs.

    **On remove, only what this install has no usable record of.** A remove
    that renders an answer renders the one install USED: the mob multipliers
    divide by the same `{hp}` install multiplied by. With a record
    (`remembered`, from `Applier.remembered_answers()`) the applier fills it in
    and nothing is asked. Without one -- an install made before T104, or a
    damaged file -- the default would be a GUESS, and dividing by it in silence
    leaves every creature multiplied whenever the player picked another value
    (cold review + Codex, fix wave). So it is asked, pre-filled with the default
    and with a note saying why. A prompt with no default is asked either way.

    It is asked only of a prompt `required_prompts()` returned: a question a
    manifest declares but no template renders is never put to anyone
    (`test_module_answers.py::test_no_shipped_manifest_declares_a_question_
    nothing_uses` holds the shipped catalog to having none).

    One function because two places answer "will Install ask me something?" --
    the dialog gate in `ControllerView._module_values()` and the Modules row's
    "asks a question" chip -- and they must not disagree.
    """
    if action == "remove":
        return prompt.default is None or prompt.key not in (remembered or {})
    return True


def reapplies_on_top(manifest: Manifest) -> bool:
    """Whether running this install again applies its answers on top of the last run.

    Read off the manifest's own shape: a REMOVE that renders install's answers
    undoes them relative to what is there (`HealthModifier/{hp}` after
    `HealthModifier*{hp}`), so the install is relative too, and a second one --
    an Update, or Install over it -- would compound. The four mob multipliers,
    pinned by `test_module_answers.py`.

    Since T115 such a re-run does not compound: the applier keeps a record of
    the values it applied (`module_answers.record_applied()`) and runs the
    manifest's own remove statements with them, then the install statements
    with the new ones, in one transaction (`Applier._run_relative()`). Measured
    before that: Install at x2 then again at x3 left creatures at x6.
    """
    install = {prompt.key for prompt in required_prompts(manifest, "install")}
    return any(prompt.key in install for prompt in required_prompts(manifest, "remove"))


def reapply_steps_problem(manifest: Manifest) -> str:
    """Why this relative manifest's re-run cannot be ONE text, or `""` if it can (T115).

    The undo-then-apply is sent as one `START TRANSACTION; ... COMMIT;` text, so
    every install and remove step it covers must be an inline `direct`
    statement on one database with no precondition and no verify: a file or a
    `db-import` step is not text this run holds, and a per-step check has no
    place inside one transaction. The four mob multipliers qualify
    (`test_mob_multiplier_rerun.py`); a future manifest that does not is refused
    a re-run rather than compounded.
    """
    steps = [s for s in manifest.sql if s.when in ("install", "remove")]
    dbs = {s.db for s in steps}
    for step in steps:
        if step.statement is None or step.applied_by != "direct":
            return f"its {step.when} SQL is not an inline statement"
        if step.precondition is not None or step.verify:
            return f"its {step.when} SQL carries a check that cannot run inside one transaction"
    if len(dbs) > 1:
        return "its SQL writes to more than one database"
    return ""


def check_answer(prompt: Prompt, value: str) -> str:
    """Why this answer cannot be used, or `""` if it can. Never raises.

    A returned sentence, not an exception, because both callers want to say it
    rather than to unwind: the dialog puts it under the box the user is still
    typing in, and the applier wraps it in a refusal that names the module.
    """
    text = value.strip()
    if not text:
        return "this cannot be left empty"
    if prompt.kind == "int":
        return "" if _INT.fullmatch(text) else "this must be a whole number"
    if prompt.kind == "float":
        try:
            float(text)
        except ValueError:
            return "this must be a number"
        return ""
    if prompt.kind == "bool":
        return "" if text.lower() in _BOOL_WORDS else "this must be yes or no"
    if prompt.kind == "choice":
        return "" if text in prompt.choices else "choose one of: " + ", ".join(prompt.choices)
    return ""


# ------------------------------------------------------------------- engine


class Applier:
    """Apply manifests to one server install rooted at `server_dir`.

    Optional seams default to "absent": without a `SqlRunner` every direct SQL
    step is reported as skipped (never run half a migration), without a
    `client_dir` client files are skipped, without a `DbcCopier` DBCs are.
    """

    def __init__(
        self,
        server_dir: Path,
        *,
        git: Git | None = None,
        sql: SqlRunner | None = None,
        client_dir: Path | None = None,
        dbc: DbcCopier | None = None,
        remote_url: Callable[[Path], str | None] | None = None,
        unmodified: Callable[[Path, str], bool | None] | None = None,
        no_local_commits: Callable[[Path, str | None], bool | None] | None = None,
        server_dir_claim: Callable[[Path], Ownership] | None = None,
        world_running: Callable[[], bool | None] | None = None,
        start_database: Callable[[], bool] | None = None,
    ) -> None:
        self.server_dir = server_dir
        # Not resolved here: `_default_git()` probes for git, and an Applier is
        # built on UI paths that never clone. Three controller tests that assert
        # exactly which commands a tab runs went red on the extra
        # `git --version` (2026-09-14). Resolved on first use instead, once.
        self._git: Git | None = git
        self.sql = sql
        # "Is this install's worldserver up?" — a seam, because the answer lives
        # in Docker and this module does not touch Docker (module docstring,
        # style-guide §3/§5). Three-valued for the reason every other reader
        # here is: `None` is "could not ask", which is not "no". Default absent,
        # and absent means the behaviour every caller has today — no guard —
        # which is `phase8-designs/c-operators-risk.md:345`'s own requirement
        # and the reason wiring it is a separate, later change.
        #
        # PRIVATE, and named the way `party.py:996` names the same seam, because
        # a public `self.world_running` here would collide with
        # `controller_wow_tortoise.autoupdate.GuardedApplier`, which already
        # carries one (`autoupdate.py:461`) for its own updater guard and
        # assigns it AFTER `super().__init__`. Sharing the name would have wired
        # this guard live on Tortoise alone, by accident, with a `bool` seam
        # where this one is `bool | None` — one game guarded, three not, and no
        # page saying so. That subclass is nonetheless the one caller in the
        # tree that ALREADY holds the fact this guard needs.
        self._world_running = world_running
        # "Put this install's database back, alone." The other half of the
        # refusal above, and a seam for the same reason: the primitive is
        # `docker.start_database()` and this module never touches Docker.
        #
        # It exists because the refusal's own instruction could not be followed.
        # T2 pressed *"Press Stop, then install again"* through the app's own
        # Stop on 2026-09-09 and the retry died on `container ... is not
        # running`: `stop_staged()` takes the database down with the world, and
        # `DockerSql` is a `docker exec` into a container that is no longer
        # there. `docker.start_database()` put it back alone in 6.6 s with the
        # world still down, which is exactly the state the guard permits. So the
        # route was missing a caller, not a primitive
        # (`8.7a-direct-sql-yulon-ubuntu2-2026-09-09/README.md`, *The dead end*).
        #
        # Returns whether it HAD to start it, so the report can say so only when
        # something happened: a `done` line for a start that did not take place
        # is `PendingSql`'s closed bug wearing a different hat. Absent means the
        # behaviour every caller had before this landed, byte for byte.
        self._start_database = start_database
        self.client_dir = client_dir
        self.dbc = dbc
        # Whose checkout is already at the clone path? Asked through the SAME
        # git the clones go through whenever that git can answer (`RunnerGit`
        # and the containerized one both can), because a machine with no host
        # git must not get a `None` here — `None` is a refusal, and a refusal
        # for the wrong reason is still a wrong answer. A `Git` that only
        # clones falls back to the host CLI, which is what a fake wants.
        # Through a lambda, not resolved here: reading `self.git` in `__init__`
        # would resolve the lazy seam and spawn `git --version` on every Applier
        # built, including the UI ones that never clone (review, 2026-09-14).
        self.remote_url: Callable[[Path], str | None] = (
            remote_url if remote_url is not None else self._reader("remote_url", RemoteReader)
        )
        # "Is this path exactly what the checkout's HEAD committed?", narrowed
        # the same way and for the same reasons. Two guards need it: the
        # adoption rule in `_require_own_clone()`, which will not adopt a
        # checkout somebody has edited, and the one defence against an upstream
        # repository that tracks a file at `CLAIM_FILE`'s name.
        # Through a lambda, not resolved here: reading `self.git` in `__init__`
        # would resolve the lazy seam and spawn `git --version` on every Applier
        # built, including the UI ones that never clone (review, 2026-09-14).
        self.unmodified: Callable[[Path, str], bool | None] = (
            unmodified if unmodified is not None else self._reader("is_unmodified", TreeReader)
        )
        # "Does HEAD carry commits the update would throw away?", narrowed the
        # same way again. The third question and not a rephrasing of the second:
        # `unmodified` compares the tree and the index against HEAD, so it
        # answers "clean" for a checkout somebody has committed their own work
        # into. Only `_adoption_refusal()` asks this, and it is the fact that
        # keeps adoption from meaning "clean tree, therefore nothing of yours
        # here".
        # Through a lambda, not resolved here: reading `self.git` in `__init__`
        # would resolve the lazy seam and spawn `git --version` on every Applier
        # built, including the UI ones that never clone (review, 2026-09-14).
        self.no_local_commits: Callable[[Path, str | None], bool | None] = (
            no_local_commits
            if no_local_commits is not None
            else self._reader("no_local_commits", HistoryReader)
        )
        # "Did this app create the server directory this clone is under?" — a
        # seam rather than a module-level call, for the reason the two above are
        # seams: it reaches outside the process, and a test of the guard should
        # not need a real native-engine state file on disk to state its answer.
        # The default is this module's own `server_dir_claim()`, which is where
        # the `catalog.native` import lives and why it lives inside a function.
        self.server_dir_claim: Callable[[Path], Ownership] = (
            server_dir_claim if server_dir_claim is not None else _SERVER_DIR_CLAIM
        )

    # -- public ------------------------------------------------------------

    def _reader(self, member: str, protocol: type) -> Any:
        """One of git's read-only questions, bound at CALL time.

        The seam is chosen lazily, so its type is not known while `__init__`
        runs — and asking would defeat the laziness. This returns a callable
        that decides on first use: the configured seam if it answers that
        question, and host git if it does not (a `Git` that only clones still
        has to be askable about a checkout).
        """

        def ask(*args: object) -> Any:
            seam = self.git
            bound = (
                getattr(seam, member)
                if isinstance(seam, protocol)
                else getattr(RunnerGit(), member)
            )
            return bound(*args)

        return ask

    @property
    def git(self) -> Git:
        """The git seam, chosen once, on the first clone that actually needs one."""
        if self._git is None:
            self._git = _default_git(self.server_dir)
        return self._git

    @git.setter
    def git(self, value: Git) -> None:
        self._git = value

    def clone_dir(self, manifest: Manifest) -> Path:
        """Where this item's clone lives (`modules/<id>`, `ale_scripts/<id>`, ...)."""
        return self.server_dir / CLONE_DIRS[manifest.type] / manifest.id

    def install(
        self,
        manifest: Manifest,
        values: Mapping[str, str] | None = None,
        *,
        folder: FolderSource | None = None,
        complete: Completer | None = None,
        replacing: bool = False,
        first_configure_sql: bool = True,
    ) -> ApplyReport:
        """Clone or copy, deploy, patch, run install-time SQL, activate conf, copy client/DBC.

        `first_configure_sql` is False from `update()` only: a fresh deploy
        just replaced the script, so its configure-time PATCHES are re-run,
        but the configure-time SQL writes rows the person may have changed by
        hand since (`battlepass_config`), and an update is not a reason to
        put those back to the prompt defaults.

        `folder` is the second way to fill `modules/<id>`: the bytes come from a
        directory on the user's own disk through `FolderSource.copier` instead
        of from a repository through `Git.clone`. It is the ONLY thing that
        changes — the claim, `include.sh` and every step after them are the
        same statements, over whatever is now at the clone path. A manifest that
        also carries a `source` is a caller contradiction and is refused before
        either route runs.

        `complete` finishes a DERIVED manifest from the content that has just
        landed; see `Completer`. It runs after the clone or the copy, and
        everything from `_deploy()` onwards reads what it returned.

        `replacing` is the user's answer to `replacement_question()`, and it is
        the ONLY thing that lets this install reset a clean app-owned checkout
        of a different repository (T47). Everything else `_costly_reset()`
        finds is a refusal in the same sentences `update()` uses, over the same
        folder — `update()` asks its own copy of those questions first and the
        second pass here is deliberate: this is the method that reaches the
        clone seam, and a guard that only one of two callers runs is how the
        asymmetry T47 closes came about.

        `_check_values()` is NOT re-run against the completed manifest: it is
        the caller's answers that are being checked, and the fields a completer
        fills (conf files to activate, SQL to report) carry no prompts to
        answer. Named here because it is the one step the completed manifest
        does not reach.
        """
        vals = self._values(manifest, values)
        log = _Log()
        self._check_values(manifest, "install", vals, log)
        # T115, before anything is written: a relative install run again over
        # values nobody can read would compound them, so it is refused here.
        undo = self._undo_values(manifest)
        clash = self._conflict_refusal(manifest)
        if clash:
            raise ApplyError(clash)
        # After the conflict and before anything is written. The two guards are
        # independent -- one is about what is here that must not be, the other
        # about what is not here and must be -- and a row can only be told one
        # thing at a time, so the conflict keeps the order it had.
        missing = self._requires_refusal(manifest)
        if missing:
            raise ApplyError(missing)
        clone = self.clone_dir(manifest)
        # Whether a claim of OURS is at `clone`, so the completion mark at the
        # end knows whether there is a record to update. False for the two
        # routes that write no claim (a sourceless manifest, a claim whose write
        # failed), and neither of those may be handed one late: the first never
        # had this app's handwriting on the folder, and for the second the
        # report has already said the folder will not be recognised next time.
        claimed = False
        url = ""
        # Read BEFORE the clone or the copy touches that folder, because
        # `update()` runs this whole method again over a module that is already
        # installed and finished. The first claim write below carries this value
        # back, so a re-install or an update that dies half-way leaves the module
        # as finished as it was — the folder's contents may now be half a version
        # newer, but the INSTALL it had is not undone by a failed attempt at a
        # second one, and demoting it would take Remove off a working module's
        # row. A clone this app has no claim on (`None`) is a fresh install and
        # starts unfinished.
        was_completed = clone_install_completed(clone, item_id=manifest.id) is True
        # Read BEFORE the clone or the copy fills that folder, because `update()`
        # runs this whole method again and the claim write below would otherwise
        # drop the receipts of the files ALREADY in the user's client — leaving
        # them "no record", i.e. never taken back, if any later step raised
        # before `_record_client_copies()` wrote the new ones (round 1 review).
        previous_copies = read_client_copies(clone, item_id=manifest.id)
        if folder is not None and manifest.source is not None:
            raise ApplyError(
                f"{manifest.id}: one source, not two — this manifest is cloned from "
                f"{manifest.source.url} and was also handed the folder {folder.path} to copy. "
                f"Nothing was changed."
            )
        if folder is not None:
            self._require_own_clone(manifest, clone, "install")
            self._costly_reset(manifest, clone, replacing)
            self._copy_folder(folder, clone, log)
        elif manifest.source is None:
            # A manifest with no source never clones, so the guard used to sit
            # entirely inside the branch below — and that left `install()` with
            # the hole `configure()` was given a guard for. An install-time
            # `in_clone` patch rewrites a line in a file at `modules/<id>`, and
            # for a SOURCELESS manifest every file there was put there by
            # somebody else BY DEFINITION: this app never cloned it. So it is
            # the case that needs the question asked most, not least. Gated on
            # such a patch actually existing, like `configure()`, so a refusal
            # is never about a folder this run would not have touched.
            if clone.exists() and any(
                p.in_clone and p.when in _whens("install") for p in manifest.patches
            ):
                self._require_own_clone(manifest, clone, "install")
        else:
            self._require_own_clone(manifest, clone, "install")
            self._costly_reset(manifest, clone, replacing)
            try:
                self.git.clone(
                    CloneSpec(
                        url=manifest.source.url,
                        dest=clone,
                        branch=manifest.source.branch,
                        sparse_path=manifest.source.sparse_path,
                        rev=manifest.source.rev,
                    )
                )
            except FileNotFoundError as exc:
                # ONLY a missing executable, and only this exception type. The
                # first version caught every OSError around the whole clone --
                # but both seams `rmtree()` and `mkdir()` the destination BEFORE
                # spawning git, so a permission error mid-delete would have been
                # reported as "git could not be started ... Nothing was changed"
                # with part of the destination already gone. That sentence would
                # have been false about data loss (review, 2026-09-14).
                raise ApplyError(
                    f"{manifest.id} could not be installed because git could not be started "
                    f"({exc}). Yu'lon clones modules with git, and runs it inside a container "
                    f"when the machine has none -- so this means neither was available. "
                    f"Install Git, or start Docker, and try again. Nothing was changed."
                ) from exc
            except GitError as exc:  # one failure vocabulary for the whole applier
                raise ApplyError(str(exc)) from exc
            log.done.append(f"clone {manifest.source.url} → {_rel(self.server_dir, clone)}")
        if folder is not None or manifest.source is not None:
            # This app filled the folder, by either route, so both of the files
            # it writes INTO a checkout go in — and they are written here rather
            # than in a helper each branch calls, because the ledger row
            # `apply.py::install::touch` names this function and a walker that
            # stopped finding it would report a write site that had gone.
            url = manifest.source.url if manifest.source is not None else ""
            try:
                # `completed` is `False` for a new clone -- the folder is filled
                # and not one of the steps below has run -- and whatever it
                # already was for a clone this app has installed before. The
                # matching `True` is the last thing this function does, in
                # `_finish_claim()`, and between the two writes the claim is what
                # tells the Modules tab to keep offering Install (T68).
                # `client_files` carries T67's receipts for the copies ALREADY in
                # the user's client forward, because every write replaces the
                # whole record.
                write_clone_claim(
                    clone,
                    item_id=manifest.id,
                    url=url,
                    completed=was_completed,
                    client_files=previous_copies,
                )
                claimed = True
            except OSError as exc:
                # Never fatal — the clone is on disk and the rest of the install
                # is what the user asked for — but never silent either: without
                # the claim the NEXT install of this item is refused, and the
                # report is the only place that can say so in advance.
                log.skipped.append(
                    f"{CLAIM_FILE}: could not be written ({exc}), so this app will not "
                    f"recognise {_rel(self.server_dir, clone)} as its own next time"
                )
            if manifest.type == "module":
                # CMake's CollectSourceFiles() silently skips a module without include.sh.
                include = clone / "include.sh"
                if not include.exists():
                    include.touch()
                    log.done.append("touch include.sh")
        if complete is not None:
            manifest = self._completed(manifest, clone, complete)
        self._deploy(manifest, clone, log)
        self._patches(manifest, clone, vals, "install", log)
        # Both SQL passes are refused as one, BEFORE either runs: the guard's
        # own sentence says no rows were written, and after the install-time
        # pass that would be false of the configure-time one.
        if first_configure_sql:
            self._refuse_direct_sql_into_a_running_world(manifest, "configure")
        self._sql(manifest, clone, vals, "install", log, undo=undo)
        self._conf(manifest, clone, vals, log)
        # Then the configure-time steps, as this item's first configure
        # (`_whens`): a value the person answered is written now, not left for
        # a `configure()` nothing calls. After `_conf()`, because a configure
        # patch may target the conf that step activates (`mod-ale`'s does),
        # which is the state a later `configure()` always finds.
        self._patches(manifest, clone, vals, "configure", log)
        if first_configure_sql:
            self._sql(manifest, clone, vals, "configure", log)
        self._client(manifest, clone, log)
        self._dbc(manifest, clone, log)
        self._finish_claim(manifest, clone, url, claimed, log, log.client_copies or previous_copies)
        self._remember(manifest, values, log)
        return self._report("install", manifest, log)

    def _finish_claim(
        self,
        manifest: Manifest,
        clone: Path,
        url: str,
        claimed: bool,
        log: _Log,
        client_files: Sequence[ClientCopy] = (),
    ) -> None:
        """The ONE claim write that happens after the steps: this install finished (T68).

        LAST, after every step that can raise. `_sql()` is the one T68 was
        reported for -- the T7 direct-SQL guard refuses while the world runs --
        but deploy, patches, conf, client files and DBCs all leave through the
        same exception, and each of them leaves the clone on disk with its
        install unfinished. This is the one point reached only when all of them
        returned, so what the claim says is "every step ran" and not "the last
        step I thought of ran".

        **One call, not one call per fact, and that is the whole reason this is a
        method.** `write_clone_claim()` writes the WHOLE record, so a second
        after-the-steps write carrying its own key and defaulting this one's
        would erase whichever ran first. Anything else a step learns and the
        claim must carry goes into THIS call as another keyword -- never into a
        write of its own. T67's `client_files` receipts are the first such fact
        and they come in here: the copies this run landed if it landed any, and
        otherwise the ones the claim already carried, because a run that copied
        nothing into the client (no client folder set) has not taken back what
        an earlier run put there.

        `claimed` is False for a manifest this app never wrote a claim for (no
        source, no folder) and for one whose first write failed. Neither may be
        handed a record late: the first never had this app's handwriting on the
        folder, and for the second the report has already told the user the
        folder will not be recognised next time.
        """
        if not claimed:
            return
        try:
            write_clone_claim(
                clone,
                item_id=manifest.id,
                url=url,
                completed=True,
                client_files=client_files,
            )
        except OSError as exc:
            # Not fatal for the same reason the first write is not: the install
            # DID happen and the user is owed the report of it. The cost is one
            # row that keeps offering Install for an install that finished, and
            # pressing it re-runs steps this applier already re-runs from the
            # menu -- so the failure is visible and harmless, where raising here
            # would report a finished install as a failure. One write carries
            # both facts, so one failure loses both and says so twice.
            if client_files:
                log.skipped.append(
                    f"{CLAIM_FILE}: the record of what went into your game client could not be "
                    f"written ({exc}), so removing {manifest.id} will leave those files in place"
                )
            log.skipped.append(
                f"{CLAIM_FILE}: the finished mark could not be written ({exc}), so "
                f"{_rel(self.server_dir, clone)} will keep offering Install"
            )

    def update(self, manifest: Manifest, values: Mapping[str, str] | None = None) -> ApplyReport:
        """Fast-forward this module's clone and re-apply it — refusing anything a reset destroys.

        `install()` over a folder that is already a checkout IS the pull: the
        clone seam runs `git fetch` then `git reset --hard FETCH_HEAD` and
        re-applies the pin, and everything after it — deploy, patches, SQL,
        conf, client files — is what has to be redone once the source has
        moved. So this runs `install()` and does not reimplement any of it.

        **What it adds is the three questions `install()` does not ask, and it
        adds them here rather than inside `_require_own_clone()` on purpose.**
        That guard returns the moment this app's own claim reads `OWNED` —
        before `origin`, before `is_unmodified()` and before
        `no_local_commits()` — and for a FIRST install into a folder this app
        made that is right: there is nothing there to lose, and asking would
        cost a network round trip on every install. For an update the same
        silence is a `reset --hard` over work nobody looked at, which is the
        one thing T44's ticket says must not happen: "a dirty clone is not
        fast-forwarded".

        The three, in the order of what costs least and is most certain:

        1. **The repository.** The update never consults `CloneSpec.url`: the
           clone seam fetches whatever `origin` the checkout already has. So an
           app claim — a JSON file under a path this app can write — would
           otherwise authorise resetting a folder that is a DIFFERENT
           repository and then deploying this manifest's files, patching them
           and running its SQL over the result. A local read (`git remote
           get-url`), so it is asked first and both names go in the refusal.
        2. **The working tree.** `reset --hard` destroys precisely what `git
           status` reports, minus the two files this app itself put in the
           checkout: its own `CLAIM_FILE` (T66 — see `git._status_pathspec()`;
           until it was fixed that one untracked file refused an update on
           every clone this app had ever made) and an untracked, EMPTY
           `include.sh` it touched into a C++ module whose upstream ships none
           (T47 — `git._without_the_generated_include()`, which reads the
           status CODE so that a tracked `include.sh` somebody changed is still
           their work). Also a local read.
        3. **HEAD.** `status` compares the tree and the index against HEAD and
           says nothing about what HEAD itself carries, so a user who
           COMMITTED their work passes 1 and 2. `no_local_commits()` counts
           `FETCH_HEAD..HEAD` after running the update's own fetch, which is
           why it is asked LAST — it is the one that costs a round trip — and
           why it also refuses a remote that has REWOUND and a detached HEAD
           with commits of its own: all three are "HEAD carries something the
           new tip does not", counted the same way whoever put it there.

        `is True` throughout and never truthiness: `None` is "git could not be
        asked", which fails closed and says so in its own words. Telling an
        offline user that they have uncommitted changes is telling them
        something nobody established (`Ownership`'s three outcomes).

        A folder that is not there at all is refused rather than quietly
        installed: the press the user made was Update, and an Update that
        silently installs is a different action wearing the same button.
        """
        refusal = self._update_refusal(manifest)
        if refusal is not None:
            raise ApplyError(refusal)
        return self.install(manifest, values, first_configure_sql=False)

    def _update_refusal(self, manifest: Manifest) -> str | None:
        """Why this clone must not be fast-forwarded, in the user's words, or `None`.

        A sentence rather than an enum, unlike `_adoption_refusal()`: every one
        of these is raised by exactly one caller and read by exactly one
        person, where an adoption refusal is a fact three branches of
        `_require_own_clone()` share.
        """
        clone = self.clone_dir(manifest)
        rel = _rel(self.server_dir, clone)
        if manifest.source is None:
            return (
                f"{manifest.id} is not cloned from anywhere, so there is nothing to update. "
                f"Nothing was changed."
            )
        if not (clone / ".git").is_dir():
            return (
                f"{manifest.id} is not installed here as a git checkout ({rel}), so there is "
                f"nothing to update. Install it first. Nothing was changed."
            )
        found = self._reset_cost(manifest, clone)
        if found is None:
            return None
        return _destroys_message(found, rel, manifest.id, manifest.source.url, "Updating")

    def _reset_cost(
        self, manifest: Manifest, clone: Path, *, repository: bool = True, history: bool = True
    ) -> _Reset | None:
        """What a `git reset --hard` over `clone` would cost, or `None` for nothing.

        The three questions, in one place, for the two callers that run that
        reset: `update()` through `_update_refusal()`, and `install()` through
        `_costly_reset()` (T47 — until then only one of them asked, over the
        very same folder). The order is the order of what costs least and is
        most certain, and it is argued in `update()`'s docstring.

        `is True` / `is not True` throughout and never truthiness: `None` out of
        a seam is "git could not be asked", which fails closed under its own
        name rather than telling an offline user they have uncommitted changes.

        Two questions can be switched off, and both switches belong to
        `install()`:

        - `repository=False` is "the user has already been shown the two
          repository names and said replace it anyway". Only that question is
          dropped; the tree and HEAD are still asked, so an agreement to replace
          a DIFFERENT repository is never also an agreement to throw away
          uncommitted work in it.
        - `history=False` is `replacement_question()`, which runs on the GUI
          thread before any job is queued and so may not fetch. Dropping the
          HEAD question there can only make the question ASKED where the
          install would then refuse — a refusal after a Yes, which changes
          nothing on disk — never the reverse.

        A manifest with no `source` has no repository to be a checkout of, so
        the first question and `no_local_commits()`'s branch have nothing to
        compare against and the folder is judged on its working tree alone.
        """
        source = manifest.source
        url = source.url if source is not None else ""
        remote = self.remote_url(clone) if url else ""
        if url and repository:
            if remote is None:
                return _Reset(_Destroys.REPO_UNSEEN, "")
            if not same_repo(remote, url):
                return _Reset(_Destroys.OTHER_REPO, remote)
        known = remote or ""
        clean = self.unmodified(clone, ".")
        if clean is not True:
            return _Reset(_Destroys.EDITED if clean is False else _Destroys.TREE_UNSEEN, known)
        if source is None or not history:
            return None
        nothing_of_theirs = self.no_local_commits(clone, source.branch)
        if nothing_of_theirs is not True:
            return _Reset(
                _Destroys.COMMITTED if nothing_of_theirs is False else _Destroys.HISTORY_UNSEEN,
                known,
            )
        return None

    def _costly_reset(self, manifest: Manifest, clone: Path, replacing: bool) -> None:
        """Refuse an install that would `reset --hard` over a checkout worth keeping (T47).

        `_require_own_clone()` answers *whose folder is this*; it returns the
        moment this app's own claim reads `OWNED` and never asks whether there
        is anything in it worth keeping. For a FIRST install that is right —
        case 0 has nothing to lose, and this method returns at its first line
        for it, so the ordinary install grows no question and no round trip.
        For an install over a checkout that is already there it was a silent
        `git reset --hard` over somebody's work, which is the one thing T44's
        ticket says must not happen — and the same folder, one button over,
        already refused it.

        Reachable and reached: "Install Selected Module" on the Modules tab's
        context menu is offered for a row whose clone exists (the row BUTTON is
        not, which is why T44 recorded this as unreachable from there), and both
        custom-module routes derive an id that may already name a clone.

        `replacing` is the user's own answer to `replacement_question()`, and it
        buys exactly one of the three questions: the two repository names were
        put to them and they said go ahead. It never buys the other two —
        `_reset_cost()` still asks them — so a Yes to "replace this checkout of
        another repository" is not a Yes to deleting uncommitted work in it.
        """
        if not (clone / ".git").is_dir():
            return
        found = self._reset_cost(manifest, clone)
        if replacing and found is not None and found.fact is _Destroys.OTHER_REPO:
            # **`is OTHER_REPO` is load-bearing, and my first reading of it was
            # wrong** (review, round 1). The repository question has TWO
            # refusals, not one: `REPO_UNSEEN` is git declining to say what the
            # folder is a checkout of, and `repository=False` drops both. So
            # without this line an agreement to replace a NAMED repository
            # would also wave through the folder whose name nobody could read —
            # the fail-closed answer being spent on a question the user was
            # never shown. Only the fact they were actually asked about is
            # re-asked without.
            found = self._reset_cost(manifest, clone, repository=False)
        if found is None:
            return
        url = manifest.source.url if manifest.source is not None else ""
        raise ApplyError(
            _destroys_message(found, _rel(self.server_dir, clone), manifest.id, url, "Installing")
        )

    def replacement_question(self, manifest: Manifest) -> str | None:
        """What a user must agree to before installing over the clone already at this path.

        `None` means there is nothing to ask about: no clone, a clone this app
        did not make (which `_require_own_clone()` refuses outright, and a
        refusal is not a question — T41/T142 are not weakened by anything here),
        a clone of the repository this manifest names, or one the install is
        going to refuse anyway.

        A sentence rather than a raised error, because the only honest answer
        comes from the person: `modules/<id>` is named for an id, two different
        repositories can carry the same module name, and a clean checkout of
        another one at that path is either a fork the user has finished with or
        the one they meant to keep. This app cannot tell, and resetting it
        unasked is T47.

        **It asks git nothing that leaves this machine.** `history=False`: the
        caller is a view, on the GUI thread, before any job is queued, and a
        `git fetch` there would freeze the window for as long as the network
        takes. The HEAD question is asked by `_costly_reset()` inside the run,
        so a clone that also carries commits of its own is refused after a Yes
        rather than replaced — a refusal that changes nothing on disk.
        """
        clone = self.clone_dir(manifest)
        if manifest.source is None or not (clone / ".git").is_dir():
            return None
        if read_clone_claim(clone, item_id=manifest.id) is not Ownership.OWNED:
            return None
        found = self._reset_cost(manifest, clone, history=False)
        if found is None or found.fact is not _Destroys.OTHER_REPO:
            return None
        if self._reset_cost(manifest, clone, repository=False, history=False) is not None:
            # The repository is not the only thing wrong with this folder, and a
            # question whose Yes leads straight to a refusal is worse than the
            # refusal: it reads as though answering it were enough. The install
            # says which of the other questions stopped it, in its own words.
            return None
        rel = _rel(self.server_dir, clone)
        return (
            f"{rel} is a checkout of {found.remote}, not of {manifest.source.url}.\n\n"
            f"Installing {manifest.id} here runs `git fetch` and `git reset --hard` over that "
            f"folder to make it a checkout of {manifest.source.url}. Anything in it that "
            f"{found.remote} does not have is lost.\n\nReplace it?"
        )

    def configure(self, manifest: Manifest, values: Mapping[str, str] | None = None) -> ApplyReport:
        """Re-apply the value-bearing steps: configure-time patches/SQL and conf keys.

        **Nothing in the shipped app calls this yet** — every `.configure(` in
        the tree is in a test (grepped, 2026-09-01). The ownership guard below
        is correct and it is not live protection for anything a user can press:
        the Modules tab binds Install and Remove only. Kept because the manifest
        primitive it applies is real and its UI is a roadmap item, but do not
        count it when reasoning about what actually guards a user today.
        """
        vals = self._values(manifest, values)
        log = _Log()
        self._check_values(manifest, "configure", vals, log)
        clone = self.clone_dir(manifest)
        if clone.exists() and any(p.in_clone and p.when == "configure" for p in manifest.patches):
            # The third writer through `clone_dir()`, and the smallest: an
            # `in_clone` patch edits a file INSIDE the checkout. It cannot
            # delete anything, but it is still this app rewriting a line in a
            # file it did not put there, so it asks the same question. Gated on
            # the manifest actually having such a patch: a refusal about a
            # folder this run would never touch would be a refusal about
            # nothing.
            self._require_own_clone(manifest, clone, "configure")
        self._patches(manifest, clone, vals, "configure", log)
        self._sql(manifest, clone, vals, "configure", log)
        self._conf(manifest, clone, vals, log)
        self._remember(manifest, values, log)
        return self._report("configure", manifest, log)

    def remove(self, manifest: Manifest, values: Mapping[str, str] | None = None) -> ApplyReport:
        """Run remove-time patches/SQL, delete deployed files and the clone. DB rows are kept.

        A relative manifest (`reapplies_on_top()`) divides by the values its
        applied record holds (T115) -- what the database was multiplied by --
        ahead of the remembered answers, which T104 keeps after a Remove and so
        cannot say that. Values the caller hands in still win: the Modules tab
        asks only when there is no usable record, and then a person answered.
        """
        vals = self._values(manifest, values)
        relative = reapplies_on_top(manifest)
        applied, _why = self.applied_record(manifest) if relative else (None, "")
        vals.update(applied or {})
        vals.update(values or {})
        log = _Log()
        self._check_values(manifest, "remove", vals, log)
        clone = self.clone_dir(manifest)
        if clone.exists():
            # Before the SQL, not next to the `rmtree` below: a refusal must
            # leave the install exactly as it was, and remove-time SQL is not
            # undoable. The same guard as `install()`, because the exposure is
            # the same one and worse — that `rmtree` needs no git seam to
            # destroy a directory whose only crime is matching a catalog id.
            self._require_own_clone(manifest, clone, "remove")
        self._patches(manifest, clone, vals, "remove", log)
        self._sql(manifest, clone, vals, "remove", log)
        for step in manifest.deploy:
            self._undeploy(step, clone, log)
        # T67, and BEFORE the `rmtree` below: the receipts that say which client
        # files are this app's own live in the clone's claim file.
        self._unclient(manifest, clone, log)
        if clone.exists():
            # T49: not `shutil.rmtree`. Git writes packs read-only on Windows and
            # a bare rmtree stops at the first one, having already deleted an
            # unknown part of the checkout. Reported from a real install:
            # `remove sod FAILED: [WinError 5] Access is denied: ...\\pack-2623....idx`.
            rmtree.remove_tree(clone)
            log.done.append(f"rm -r {_rel(self.server_dir, clone)}")
        return self._report("remove", manifest, log)

    # -- filling the clone from somewhere that is not git ------------------

    def _copy_folder(self, folder: FolderSource, clone: Path, log: _Log) -> None:
        """Run the copier, and refuse anything short of a folder at the clone path.

        Two failures, one vocabulary. `OSError` — an unreadable source, a full
        disk, a permission — becomes `ApplyError`, exactly as `GitError` does
        for a clone: every caller of `install()` handles that one type, and a
        bare `PermissionError` reaching the Modules tab arrives as an unhandled
        worker exception rather than as a report line.

        And a copier that returned having put NOTHING at `clone` is refused
        too, rather than believed. It is the failure that costs most if it is
        not caught here: the very next statements write this app's claim into
        that path and touch `include.sh` in it, so the run would go on to
        manufacture the evidence that the folder is a module this app
        installed — and report a rebuild for content that is not there. The
        check is `is_dir()` on the destination, which is the one thing every
        implementation of this seam must have produced.
        """
        try:
            folder.copier(folder.path, clone)
        except OSError as exc:
            raise ApplyError(
                f"{folder.path} could not be copied into "
                f"{_rel(self.server_dir, clone)}: {exc}. Nothing was changed."
            ) from exc
        if not clone.is_dir():
            raise ApplyError(
                f"copying {folder.path} left nothing at {_rel(self.server_dir, clone)}, so there "
                f"is no module there to install. Nothing was changed."
            )
        log.done.append(f"copy {folder.path} → {_rel(self.server_dir, clone)}")

    def _completed(self, manifest: Manifest, clone: Path, complete: Completer) -> Manifest:
        """The completer's manifest, once it is still a manifest for the SAME item.

        `id`, `type` and `game` are the three fields everything already done
        depends on: `clone_dir()` is built from `type` and `id`, so they name
        the folder that has just been filled and the claim written inside it,
        and `game` is which install this manifest belongs to at all. A completer
        that changed one of them would have this run report an install of an
        item nothing installed, over another item's clone — so the difference is
        raised rather than relabelled.

        Only those three. A completer's whole job is to add conf files, SQL
        steps and the rest from what it found on disk, and checking those would
        be checking that it did nothing.

        Exceptions out of `complete` itself are NOT wrapped: this seam is the
        caller's own derivation code rather than a subprocess or a filesystem,
        and its failures are its own to name. `_copy_folder()` wraps `OSError`
        because a copier IS the filesystem.
        """
        finished = complete(manifest, clone)
        changed = [
            f"{field} ({getattr(manifest, field)!r} → {getattr(finished, field)!r})"
            for field in ("id", "type", "game")
            if getattr(finished, field) != getattr(manifest, field)
        ]
        if changed:
            raise ApplyError(
                f"{manifest.id}: finishing this manifest from what is at "
                f"{_rel(self.server_dir, clone)} changed {', '.join(changed)}, so it is no longer "
                f"the item that was installed there. Nothing further was changed."
            )
        return finished

    # -- the answers -------------------------------------------------------

    def _check_values(
        self, manifest: Manifest, action: When, vals: Mapping[str, str], log: _Log
    ) -> None:
        """Refuse an answer this action cannot use, BEFORE anything is written.

        This used to happen at the end. `install()` cloned the repository, wrote
        `include.sh`, ran the deploy and the patches, and only reached the
        missing value inside `_conf()` — so a `mod-ah-bot-plus` install with no
        GUID left a full checkout at `modules/mod-ah-bot-plus` on disk and
        reported `conf AuctionHouseBot.GUIDs: no value for {bot_guid}`. Half an
        install is worse than none of one: the next attempt then meets the
        ownership guard over a folder this app itself abandoned.

        Nothing here writes, and the two things it reads are the answers it was
        handed and (through `_check_exists`) the server's own database.
        """
        for prompt in required_prompts(manifest, action):
            value = vals.get(prompt.key)
            if value is None:
                raise ApplyError(
                    f"{manifest.id}: {prompt.question} — no value for {{{prompt.key}}}. "
                    f"Nothing was changed."
                )
            problem = check_answer(prompt, value)
            if problem:
                raise ApplyError(
                    f"{manifest.id}: {prompt.question} — {problem}, and {value!r} is not. "
                    f"Nothing was changed."
                )
            self._check_exists(manifest, prompt, vals, log)

    def _check_exists(
        self, manifest: Manifest, prompt: Prompt, vals: Mapping[str, str], log: _Log
    ) -> None:
        """Ask the database whether the thing this answer names is really there.

        Three outcomes, and the difference between the last two is the whole
        point of writing it this way:

        * a row came back — nothing is said, the install goes on;
        * **no row came back** — the answer is refused by name, because a GUID
          that matches no character is not an error inside the AH bot module,
          it is a module that silently posts nothing;
        * **the question could not be put** — no reader on this seam, or the
          query itself failed — which is reported in `skipped` and does NOT
          refuse. A database is legitimately stopped while a module is being
          installed, and an install that a stopped server can veto would be a
          worse defect than the one this check is here for.
        """
        check = prompt.exists
        if check is None:
            return
        unsafe = sorted(
            key
            for key in _fields(check.query) | _fields(check.missing)
            if not _SAFE_IN_QUERY.fullmatch(vals.get(key, ""))
        )
        if unsafe:
            raise ApplyError(
                f"{manifest.id}: {', '.join(unsafe)} cannot be used in a database question "
                f"(letters, digits, dot, dash and underscore only). Nothing was changed."
            )
        if not isinstance(self.sql, SqlReader):
            log.skipped.append(
                f"{prompt.key}={vals[prompt.key]}: NOT checked against the database "
                f"(this install has no database reader), so a wrong answer here will look "
                f"like a module that does nothing"
            )
            return
        statement = _render(check.query, vals, f"prompt {prompt.key}")
        try:
            rows = self.sql.query(check.db, statement)
        except Exception as exc:  # noqa: BLE001 - every seam failure is one answer here
            logger.warning(f"could not check {prompt.key} against {check.db}: {exc}")
            log.skipped.append(
                f"{prompt.key}={vals[prompt.key]}: NOT checked against the database "
                f"({exc}), so a wrong answer here will look like a module that does nothing"
            )
            return
        if not rows.strip():
            raise ApplyError(
                f"{manifest.id}: {_render(check.missing, vals, f'prompt {prompt.key}')}. "
                f"Nothing was changed."
            )

    # -- the guard ---------------------------------------------------------

    def _conflict_refusal(self, manifest: Manifest) -> str | None:
        """Why this cannot be installed beside what is already here, or `None`.

        `conflicts_with` had been in the schema, in the catalog and in a test
        that asserts it round-trips, and NOTHING read it (T53). Yu'lon installed
        `mod-ah-bot` and `mod-ah-bot-plus` together and the user discovered it
        when the linker stopped 21 minutes into a rebuild with `multiple
        definition of Addmod_ah_botScripts()`. A declaration nothing acts on is
        worse than no declaration: it reads, to anyone auditing the catalog, as
        a guarantee that was being kept.

        Asked of the DISK, never of the declaration. Four of the mods name each
        other (buff/xbuff/nerf/baby-mobs), so a check that refused on the
        presence of a conflict rather than of the conflicting CLONE would make
        all four uninstallable.

        Every family folder is searched rather than this manifest's own. Ids are
        unique across the catalog, the two known pairs are same-family, and a
        conflict that reaches across families -- an ale script against the
        module it shadows -- is exactly the one a same-family check would miss.
        """
        if not manifest.conflicts_with:
            return None
        for other, kind in conflicting_installed(manifest, installed_clones(self.server_dir)):
            # An EMPTY directory is not an installed module. `clone_names()`
            # counts every non-hidden directory name -- no `.git`, no claim,
            # no content -- so a leftover from a failed install, or a folder
            # made by hand, blocked the alternative forever with a refusal
            # naming something that is not really there (review, 2026-09-13).
            # CONTENT, not `.git`: a module copied in from a folder has
            # neither and is exactly as present to the linker as a clone.
            seat = self.server_dir / CLONE_DIRS[kind] / other
            try:
                if not any(seat.iterdir()):
                    continue
            except OSError:
                pass  # cannot tell: treat as occupied, a false pass costs an hour
            where = _rel(self.server_dir, seat)
            return (
                f"{manifest.id} and {other} cannot both be installed: they are alternatives "
                f"to each other, and the catalog records the conflict. {other} is already "
                f"here, at {where}. Remove it first, or keep it and leave {manifest.id} "
                f"out. Nothing was changed."
            )
        return None

    def _requires_refusal(self, manifest: Manifest) -> str | None:
        """Why this cannot be installed without something that is not here, or `None`.

        `requires` was `conflicts_with`'s twin in the worst way (T69): in the
        schema, in the JSON Schema, in eleven shipped manifests and read by
        NOTHING. Loot Pet and SitMeansRest declare `mod-ale` and installed
        cleanly without it, which puts a Lua script into a server that has no
        Lua engine -- an install that reports success and then does nothing at
        all, with no line anywhere saying why.

        The UI locks the same row for the same reason, and this is the half that
        is a guarantee: the tab's Install is one route to `install()` and a
        custom-folder or link install is another, so a lock alone would be a
        suggestion. `missing_requirements()` is the single reading both use.

        Asked of the DISK and not of the catalog, so a module the SERVER install
        cloned counts as present; `missing_requirements()` says why that matters.
        """
        if not manifest.requires:
            return None
        for needed in missing_requirements(manifest, installed_clones(self.server_dir)):
            return requirement_refusal(manifest.id, needed) + " Nothing was changed."
        return None

    def _require_own_clone(self, manifest: Manifest, clone: Path, action: When) -> None:
        """Refuse `modules/<id>` unless this app can show the folder is its own.

        Both destructive paths go through here, and neither had anything before
        (three reviewers, 2026-08-31). `install()` handed the path straight to
        the clone seam, which `shutil.rmtree`s a destination that is not a git
        checkout and runs `git fetch` + `git reset --hard FETCH_HEAD` on one
        that is, without ever comparing its origin; `remove()` deleted it
        outright. The path is not obscure: it is `modules/<id>` under the
        server dir, which is exactly where an AzerothCore user installs a
        module by hand, and the id comes from a catalog this app chose. The
        shipped Modules tab binds "Install selected" to `install()` directly,
        so one press over a hand-made `modules/mod-x` was enough.

        The order is the order of the evidence, cheapest and most certain
        first, and nothing is written before all of it is in:

        0. Nothing at the path: the ordinary first install, and the only case
           with nothing to lose. Allowed without asking git anything.
        1. Not a directory — a FILE at the clone path is somebody's,
           and neither `rmtree` nor a clone may decide what it was.
        2. A directory with no `.git`: hand-installed content, or a tarball
           unpacked there. Refused if it holds anything, allowed if it is an
           empty folder somebody made — there is nothing there to lose.
        3. A checkout this app's own claim vouches for: allowed, and this is
           the ONLY way through. `read_clone_claim()` says why the claim is
           enough on its own here, where `catalog.native` needs its record
           corroborated by `origin`.
        4. A checkout of the right repository with no claim, in a server
           directory this app installed, with nothing modified in it and no
           commits of its own: adopted. `_adoption_refusal()` has the four
           facts, why three of them were not enough, and — when one of them
           says no — which one the user is told about.
        5. Everything else is a checkout this app did not make. `origin` is
           asked only to say WHICH refusal — a different repository, this
           repository, or a git that would not answer — because every branch
           of it refuses. That is the divergence from
           `native.refuse_unowned_checkout()`, which can be reached with the
           question already answered by its caller.

        `UNKNOWN` is refused with its own sentence, per `Ownership`: a damaged
        claim means this app knows LESS than an absent one, so it must not act
        more freely.

        **`action` is here because a refusal's REMEDY is not the same for the
        three callers, and one wrong remedy causes the harm it prevents.** The
        first version said "move the folder aside" everywhere. For `install()`
        that is right and cheap. For `remove()` it is advice to strand yourself:
        moving the clone aside makes `clone.exists()` false, and then this run's
        remove-time SQL and `_undeploy()` either fail outright (an `in_clone`
        patch or an SQL file that is no longer there) or quietly leave the
        deployed files behind — so the module stays half-installed in the
        database with no route back through the app. Every `remove()` refusal
        therefore keeps the user pointed at removing, and says why the folder
        must not simply be made to disappear.

        **Does `remove()` accept weaker proof than `install()`? Yes — one
        specific piece of it, and no more.** The asymmetry is in what a refusal
        COSTS. Refusing `install()` costs the user nothing they had: their
        folder is untouched, and the remedy (move it aside, install again) is a
        download. Refusing `remove()` costs them the only in-app route to a
        clean database, and there is no second one — the rows and the deployed
        files stay, forever, and the folder they were told to move aside is not
        the problem. The relocation case makes that a real lockout rather than a
        theoretical one: `install_id()` is a hash of the ABSOLUTE path, so
        moving or renaming a server folder makes EVERY claim in it read
        `UNKNOWN` at once.

        So `remove()` — and only `remove()` — also accepts
        `claim_written_by_this_app()`: a claim that parses, carries this
        version, names THIS item, and differs from `OWNED` in exactly one field,
        the folder it was written in. That is this app's own handwriting; a
        hand-installing user does not produce it, and a stranger's checkout has
        no claim file at all. What it cannot distinguish is a MOVED install from
        a COPIED one — nothing on disk can — and that is the whole of what is
        given up: in a copy, `remove()` deletes a clone that this app made a copy
        of, for an item the user has just asked to remove. `install()` is not
        given the same licence, because there the same input would authorise
        `git reset --hard` inside a copy somebody may be keeping precisely
        because it is not the original.

        **Be exact about what that weaker path proves, because it returns before
        `remote_url()` is ever reached.** It never asks git anything: its whole
        evidence is a `.git` directory plus a file at `CLAIM_FILE`'s name whose
        JSON has this version, this item id, and some other folder's
        `install_id()`. It does NOT establish that the checkout is a checkout of
        the manifest's repository. Writing such a file needs local write access
        to this exact path under a server directory, which is inside the trust
        boundary already, and it is not a new asymmetry — the `OWNED` path is
        origin-blind for the same reason and by the same design (the claim is
        its own corroboration). Recorded so the next reader does not mistake
        "this app's own handwriting" for "this is the right repository".
        """
        if not clone.exists():
            return
        rel = _rel(self.server_dir, clone)
        retry = f"{action} {manifest.id} again"
        if not clone.is_dir():
            raise ApplyError(
                f"{rel} is a file, not this app's clone of {manifest.id}. Nothing was changed. "
                f"Move it aside and {retry}."
            )
        url = manifest.source.url if manifest.source is not None else ""
        if not (clone / ".git").is_dir():
            # A folder with no `.git` used to be somebody else's by definition,
            # and the claim was never even read here. It is not any more: a
            # module installed from a FOLDER is a copy, a copy carries no `.git`
            # (see `FolderCopier`), and the only thing separating this app's own
            # copy from a tarball somebody unpacked at this path is the claim
            # inside it — the same evidence, in the same file, that authorises
            # re-cloning and removing a checkout. Without this the app could
            # install a module from a folder and then never uninstall it.
            #
            # It is the claim and nothing else. `origin` and the adoption facts
            # below all need a repository, and a copy has none: git would either
            # refuse to answer or answer about whatever checkout the folder was
            # copied FROM, which is a repository this install has nothing to do
            # with. So an unrecognised folder still falls through to the
            # leftovers refusal exactly as it always did — and a damaged claim
            # is itself one of the leftovers, which is why UNKNOWN needs no
            # separate sentence here.
            owned = read_clone_claim(clone, item_id=manifest.id)
            if owned is Ownership.OWNED:
                return
            if owned is Ownership.UNKNOWN and self._relocation_licence(
                manifest, clone, rel, action
            ):
                return
            leftovers = sorted(item.name for item in clone.iterdir())
            if leftovers:
                raise ApplyError(
                    f"{rel} already has files in it and was not put there by this app "
                    f"({', '.join(leftovers[:5])}). Nothing was changed. Move that folder "
                    f"aside — or delete it yourself if you no longer want it — and then "
                    f"{retry}.{self._removal_note(action, manifest)}"
                )
            return
        owned = read_clone_claim(clone, item_id=manifest.id)
        if owned is Ownership.OWNED:
            return
        if owned is Ownership.UNKNOWN and self.unmodified(clone, CLAIM_FILE) is True:
            # The one thing that stops an upstream repository which TRACKS a
            # file at this name from locking a module out for good. After
            # `git reset --hard FETCH_HEAD` the repository's own copy is back at
            # that path, it reads `UNKNOWN`, and every later install, configure
            # and remove of that module refuses — permanently, with a message
            # about a file the user cannot delete without dirtying their
            # checkout. Git can tell the two apart with certainty: a claim THIS
            # app wrote is never committed to a module's repository, so a file
            # here that `status` reports as unchanged from HEAD is the
            # repository's content and not a claim at all. Treated as if the
            # name were free, so the folder is judged on its origin and on the
            # adoption evidence below, exactly as an unclaimed one is.
            #
            # `is True` and not truthiness: `None` is "git could not be asked",
            # which must keep refusing.
            logger.warning(
                f"{clone / CLAIM_FILE} is committed content of that repository, not a claim "
                f"this app wrote; judging {rel} on its origin instead"
            )
            owned = Ownership.UNCLAIMED
        if owned is Ownership.UNKNOWN:
            if self._relocation_licence(manifest, clone, rel, action):
                return
            # "Move the folder aside" is offered to the two callers it is a
            # remedy for and withheld from `remove()`, for which it is the
            # opposite of one — see this method's docstring.
            put_back = "Put this server folder back where it was installed"
            if action != "remove":
                put_back += f", or move {rel} aside,"
            raise ApplyError(
                f"{rel} holds a {CLAIM_FILE} this app cannot read as its own, so it cannot tell "
                f"whether that checkout is its own {manifest.id} or your own work. Nothing was "
                f"changed. The likeliest cause is an install folder that was moved, renamed or "
                f"copied since {manifest.id} was installed: that file records the folder's own "
                f"path, so it stops matching. {put_back} and then "
                f"{retry}.{self._removal_note(action, manifest)}"
            )
        remote = self.remote_url(clone)
        if remote is None:
            raise ApplyError(
                f"{rel} is a git checkout, but git would not say what it is a checkout of, so "
                f"nothing was changed. Move that folder aside and then "
                f"{retry}.{self._removal_note(action, manifest)}"
            )
        if url and not same_repo(remote, url):
            raise ApplyError(
                f"{rel} is a checkout of {remote}, not of {url}. Nothing was changed. Move that "
                f"folder aside and then {retry}.{self._removal_note(action, manifest)}"
            )
        branch = manifest.source.branch if manifest.source is not None else None
        # A manifest with no source never clones, so there is no repository to
        # be a checkout OF and nothing to adopt: the folder is somebody else's
        # by definition, which is what `NO_RECORD` says.
        refusal = (
            self._adoption_refusal(clone, remote, url, branch) if url else _NoAdoption.NO_RECORD
        )
        if refusal is None:
            return
        raise ApplyError(
            _no_adoption_message(refusal, rel, retry) + self._removal_note(action, manifest)
        )

    def _relocation_licence(self, manifest: Manifest, clone: Path, rel: str, action: When) -> bool:
        """May a `remove()` proceed over this app's own claim naming another folder?

        The weaker proof `remove()` — and only `remove()` — accepts, argued in
        `_require_own_clone()`'s docstring. One function rather than a paragraph
        repeated in two branches: the checkout case and the copied-folder case
        reach `UNKNOWN` by different routes and the licence is the same one, so
        a rule stated twice would be a rule that can diverge (style-guide §4).

        Never for `install()` or `configure()`, whatever the folder holds.
        """
        if action != "remove" or not claim_written_by_this_app(clone, item_id=manifest.id):
            return False
        logger.warning(
            f"{rel} holds this app's own claim for {manifest.id} naming a different "
            f"folder (this install was moved, renamed or copied); removing anyway, "
            f"because refusing would leave {manifest.id} in the database with no way out"
        )
        return True

    def _removal_note(self, action: When, manifest: Manifest) -> str:
        """The sentence a `remove()` refusal must carry, and the other two must not.

        Every remedy above ends in "and then remove <id> again", and that "then"
        is the whole point: a user who moves the folder aside and stops has not
        removed anything, they have hidden the files and kept the database rows.
        Only this app knows which rows and which deployed files those are.
        """
        if action != "remove":
            return ""
        return (
            f" Removing {manifest.id} also undoes the database changes it made and deletes the "
            f"files it deployed elsewhere in this server, and only this app can do that — so "
            f"moving or deleting this folder is not by itself an uninstall."
        )

    def _adoption_refusal(
        self, clone: Path, remote: str, url: str, branch: str | None
    ) -> _NoAdoption | None:
        """Adopt an existing checkout of the RIGHT repository — on four facts, not one.

        `None` is the adoption; anything else is the fact that stopped it, which
        the caller turns into that fact's own sentence. Which fact, and which of
        its answers, is the whole of what a refused user needs to hear — see
        `_NoAdoption`.

        The gap this narrows: a module installed by a build older than the claim
        file has no claim, so the first Install after that change is refused. A
        migration on `origin` alone would have undone the guard exactly — a
        matching `origin` is what EVERYBODY with this catalog entry has, and
        "the user's own checkout of the right repository" is the case the guard
        was written for.

        **It does not close that gap for every existing user, and the commit
        that introduced it said it did.** A module whose upstream ships no
        `include.sh` gets one written by `install()` itself; that file is
        untracked; fact 3 asks about the whole tree; so exactly those modules
        fail adoption and their users still get the refusal and its remedy. The
        behaviour is right — see the paragraph on untracked files below — but
        the claim was overbroad, and this is where a reader will look for it.

        Four independent facts, and a hand-installed module fails one of them:

        1. `origin` names the repository the manifest does (established by the
           caller, and passed in so this reads as one rule rather than half of
           one).
        2. `server_dir_claim()` is `OWNED`: `.yulon-install.json`, OUTSIDE this
           folder, at the server dir, says this app CREATED this server
           directory. A user who cloned a module into their own AzerothCore tree
           cannot produce that file, and it is not something a module repository
           can carry — it is not in the folder being adopted at all. This is the
           fact that does the work.
        3. The checkout is unmodified. `git reset --hard FETCH_HEAD` destroys
           exactly the tracked changes `status` reports, so an empty answer is a
           proof that adopting costs nothing, and `None` — git could not be
           asked — is not that proof and refuses. It refuses under its own
           name (`TREE_UNSEEN`, not `EDITED`): nobody established that this
           checkout has anything uncommitted in it.
        4. HEAD carries no commit the update would not. **`status` is not this
           question and cannot be made into it.** It compares the working tree
           and the index against HEAD; it says nothing at all about what HEAD
           itself is. A user who cloned this catalog's own repository into a
           directory this app created and then COMMITTED their customisations
           has a perfectly clean tree, passes facts 1-3, and the update that
           adoption authorises — `fetch` + `reset --hard FETCH_HEAD` — moves
           HEAD off those commits and leaves them reachable only through the
           reflog. So the fourth fact asks git the question the third one only
           looks like: `rev-list FETCH_HEAD..HEAD` is empty, after
           `no_local_commits()` has run the update's own fetch to put a truthful
           commit behind `FETCH_HEAD`. `None` — git could not be asked, or the
           fetch could not reach the remote — refuses, per `Ownership`'s three
           outcomes: "nothing to compare against" is not "nothing to lose". It
           refuses as `HISTORY_UNSEEN` rather than `COMMITTED`, because the
           commonest way to reach it is a machine that is offline, and telling
           somebody who has committed nothing that their commits are in the way
           would be the collapse this fact was added to end. `branch` is the
           manifest's, because the manifest's is what the update will fetch.

           **This fact costs a network round trip, and that is why it is asked
           last.** It runs only once facts 2 and 3 have both said yes, moments
           before the install fetches the same refs anyway; an offline machine
           gets a refusal for an install that could not have proceeded. Its
           first version compared against `refs/remotes/origin/<branch>` and
           `refs/remotes/origin/HEAD`, and the second of those is never
           refreshed by the branchless `fetch origin HEAD` this app runs — so
           one legitimate update made every already-updated module look like the
           user's own work and refused exactly the population the fact exists to
           let through. `git.RunnerGit.no_local_commits()` carries the
           measurement.

           This is the third time in this codebase that a question with more
           states than the answer being carried has produced a bug (see
           `native.read_claim()`'s absent-vs-unreadable collapse and
           `read_clone_claim()`'s note about it). "Clean" and "has nothing of
           the user's in it" are different facts, and only one of them is what
           `status` returns.

        The whole tree, `"."`, not one path, for fact 3: there is no file in a
        module clone this app can point at as the one that matters. That also
        means an UNTRACKED file blocks adoption, which is stricter than the harm
        requires — a hard reset does not delete untracked files — and it is the
        `include.sh` case above.

        **Two names `unmodified()` does not count, and the argument this
        paragraph used to make against the second one.** `CLAIM_FILE` is the
        first (T66, `git._status_pathspec()`), and it changes nothing here:
        this method is reached only for `UNCLAIMED`, and the two ways to be
        `UNCLAIMED` are no such file at all and one the REPOSITORY tracks —
        which `status` already reports as unchanged. A claim this app wrote but
        cannot read as its own is `UNKNOWN`, and `_require_own_clone()` raises
        on that before ever getting here.

        The second is an untracked, EMPTY `include.sh` (T47,
        `git._without_the_generated_include()`). This paragraph refused it for
        years on the grounds that "an exact-name allowlist would have to become
        a content check to be safe, and a content check is the first step of
        deciding which of somebody's untracked files are innocent" — and the
        first half was right, which is why it IS a content check and not a
        name. Two conditions, both read from the checkout: `?? ` in the status
        line, so the repository does not track the file, and zero bytes, so
        there is nothing in it to lose. Nothing decides that somebody's file is
        innocent; a file with anything at all in it is counted, and so is one
        the repository tracks, however small. What it buys is the case that
        made the rule wrong: the app touches that file itself, into a folder it
        created, and then refused to update it because of what it had done.
        """
        if self.server_dir_claim(self.server_dir) is not Ownership.OWNED:
            return _NoAdoption.NO_RECORD
        # `is False` / `is not True` throughout, never truthiness: the whole
        # point is that these seams answer three things, and which of the two
        # refusals it is decides what the user is told.
        clean = self.unmodified(clone, ".")
        if clean is not True:
            if clean is False:
                return _NoAdoption.EDITED
            return _NoAdoption.TREE_UNSEEN
        nothing_of_theirs = self.no_local_commits(clone, branch)
        if nothing_of_theirs is not True:
            if nothing_of_theirs is False:
                return _NoAdoption.COMMITTED
            return _NoAdoption.HISTORY_UNSEEN
        logger.info(
            f"adopting the existing checkout at {_rel(self.server_dir, clone)}: it is a clean "
            f"checkout of {remote} (the manifest's {url}) with no commits of its own, inside a "
            f"server directory this app installed"
        )
        return None

    # -- steps -------------------------------------------------------------

    def _deploy(self, manifest: Manifest, clone: Path, log: _Log) -> None:
        for step in manifest.deploy:
            src = clone / step.src
            target = self._deploy_target(step.src, step.dest)
            if src.is_dir():
                shutil.copytree(src, target, dirs_exist_ok=True)
            elif src.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
            else:
                raise ApplyError(f"deploy source missing in clone: {src}")
            log.done.append(f"deploy {step.src} → {_rel(self.server_dir, target)}")
            for old, new in step.rename:
                (target / old).replace(target / new)
                log.done.append(f"rename {old} → {new}")

    def _undeploy(self, step: Deploy, clone: Path, log: _Log) -> None:
        """Delete exactly what `_deploy()` put under `dest` — never the dest dir itself.

        A directory source may land in a SHARED dir (battlepass: `lua_scripts/` →
        `.../lua_scripts/`, next to every other ALE script), so the deployed set is
        re-derived from the clone's `src` listing plus `rename`. Without the clone
        that set is unknowable for a directory: the files stay and the step is
        reported skipped — never guessed at (review finding, 2026-08-21).
        """
        target = self._deploy_target(step.src, step.dest)
        src = clone / step.src
        if src.is_dir():
            # BOTH names of a renamed file (T104 fix wave). A rename added to a
            # manifest after installs exist -- accountwide's Ashen Order script,
            # `.lua` -> `.lua.unused` -- leaves every earlier install holding the
            # OLD name, and deleting only the new one left that live script
            # behind in silence. `_rm()` names what it found and skips what is
            # not there, so the report says which of the two this install had.
            deployed = {entry.name for entry in src.iterdir()}
            for old, new in step.rename:
                old_parts, new_parts = Path(old).parts, Path(new).parts
                if len(old_parts) == 1 and old_parts[0] in deployed:
                    deployed.add(new_parts[0])
            for name in sorted(deployed):
                self._rm(target / name, log)
            return
        if target.is_file():
            self._rm(target, log)
        elif target.is_dir():
            log.skipped.append(
                f"{step.src}: clone missing, so the files deployed into "
                f"{_rel(self.server_dir, target)} are unknown and were left in place"
            )

    def _rm(self, path: Path, log: _Log) -> None:
        # `is_symlink()` FIRST, because `is_dir()` follows the link: a symlink to
        # a directory used to take the tree branch and, since T49 gave that
        # branch a retry that walks, would have been followed into somebody
        # else's files (review, 2026-09-13). A manifest that says "remove this
        # path" means the link, never what it points at.
        if path.is_symlink():
            path.unlink()
            log.done.append(f"rm {_rel(self.server_dir, path)}")
        elif path.is_dir():
            rmtree.remove_tree(path)  # T49: may be a checkout, so read-only packs
            log.done.append(f"rm -r {_rel(self.server_dir, path)}")
        elif path.is_file():
            path.unlink()
            log.done.append(f"rm {_rel(self.server_dir, path)}")

    def _deploy_target(self, src: str, dest: str) -> Path:
        target = self.server_dir / dest
        # "file.lua" → "dir/" means dir/file.lua; "dir/" → "dir2/" means dir2 itself.
        if dest.endswith("/") and not src.endswith("/"):
            return target / Path(src).name
        return target

    def _patches(
        self, manifest: Manifest, clone: Path, vals: Mapping[str, str], when: When, log: _Log
    ) -> None:
        for patch in manifest.patches:
            if patch.when != when:
                continue
            base = clone if patch.in_clone else self.server_dir
            files = sorted(base.glob(patch.file)) if _is_glob(patch.file) else [base / patch.file]
            spelled = _lua_spelling(manifest, vals) if patch.file.endswith(".lua") else vals
            replacement = _render(patch.replace, spelled, f"patch {patch.file}")
            for path in files:
                if not path.is_file():
                    raise ApplyError(f"patch target missing: {path}")
                changed = _apply_patch(path, patch, replacement)
                log.done.append(
                    f"patch {_rel(base, path)} ({'changed' if changed else 'already applied'})"
                )

    def _sql(
        self,
        manifest: Manifest,
        clone: Path,
        vals: Mapping[str, str],
        when: When,
        log: _Log,
        undo: Mapping[str, str] | None = None,
    ) -> None:
        """Run this action's SQL steps; a relative manifest's as one recorded text (T115).

        A `reapplies_on_top()` manifest's install or remove goes through
        `_run_relative()` once the guards below have passed -- the same
        running-world refusal and the same database start as any other press.
        `undo` is its applied record on a re-install: the values to divide out
        first.
        """
        plan = self._plan_sql(manifest, clone, vals, when)
        self._refuse_direct_sql_into_a_running_world(manifest, when)
        # Second, and never first: a press against a live world is refused above
        # having started nothing. Starting containers under a world this guard
        # is about to refuse would undo the guard's own advice on a stack the
        # user had Stopped.
        if self._start_the_database_for_direct_sql(manifest, when, log):
            # THIRD, and the reason the guard is asked twice for one press.
            # `start_database()` waits for the database to report healthy, up to
            # 180 s (`docker._DB_HEALTHY_TIMEOUT_SECONDS = 180.0`, read on the
            # tree 2026-09-09; these comments said 120 s until then). The first
            # reading is
            # that old by the time the first statement would be sent, and the
            # Server tab's Start is a button the same user can press in the
            # meantime — as is a `compose up` in another terminal. A world
            # started inside that window is holding these tables when the writes
            # land, which is the whole of what this guard is for, and the report
            # would have said the world was left stopped.
            #
            # The same sentence as the first refusal, deliberately: it is the
            # same fact and the same remedy, and a second vocabulary for one
            # rule is how a user comes to meet two. Nothing is reported when it
            # raises — `install()` never returns a report — so the database this
            # run started is left up and unmentioned. That is the safe side of
            # the trade: a container that is running when it need not be, rather
            # than rows written under a live world.
            self._refuse_direct_sql_into_a_running_world(manifest, when)
        if (
            when in ("install", "remove")
            and reapplies_on_top(manifest)
            and not reapply_steps_problem(manifest)
        ):
            self._run_relative(manifest, vals, when, undo, log)
            return
        for index, step in enumerate(manifest.sql):
            if step.when != when:
                continue
            if step.applied_by == "db-import":
                log.pending_sql.append(self._pending_sql(step, clone))
                continue
            if self.sql is None:
                log.skipped.append(f"sql → {step.db}: no SQL runner configured")
                continue
            # T63: between the guard above and the write below, the one question
            # neither of them asks — is this step's turn yet? Skipping here and
            # not inside `_run_sql()` keeps that function what it is (it writes),
            # and keeps the skip in the same list the user already reads.
            if not self._precondition_met(step, log):
                continue
            self._run_sql(step, clone, vals, log, plan.get(index))
            self._verify_sql(manifest, step, log)

    def _refuse_direct_sql_into_a_running_world(self, manifest: Manifest, when: When) -> None:
        """Checklist 8.7a's guard: no direct SQL into a live world's databases.

        Owner answer 7 (`phase8-parity-decisions.md:44`) is the rule — *no
        direct writes to `characters`/`world` while running; reads are fine* —
        and until this function existed the applier had **no** running-world
        guard on this route at all. Only the `db-import` route was ever pressed
        live (`8.7a-wotlk-yulon-ubuntu-2026-09-08/11-cycle2-up.log`), and that
        route's guard is not here: it is `docker.apply_module_sql()`
        (`docker.py:2029-2036`), which refuses when `spec.world`/`spec.auth` are
        in `_running().ours`. **That guard could not be reused.** It needs a
        `ContainerSpec`, a compose project name and Docker itself, and this
        module's whole contract is that it never touches Docker (module
        docstring, style-guide §3/§5): the applier is called down into and
        signals up. So this is a second ENFORCEMENT POINT for one rule, not a
        second rule — the fact is asked through a seam so the caller can hand
        both routes the same answer, and the sentence deliberately echoes
        `docker.py`'s ("holds ... in memory and saves them back over whatever it
        finds. Press Stop") so a user meets one rule and not two.

        Four decisions, each of which could have gone the other way:

        * **A pre-pass over the action's steps, not a check inside the loop.**
          `all-stackables` sends three statements to `world` on install; a guard
          consulted per statement can let the first through and refuse the
          second, which is a half-applied mod and a worse bug than no guard.
          Nothing has run when this raises, so *no rows written* is true of the
          whole action and not merely of the step that tripped it.
        * **It raises rather than reporting `skipped`.** `phase8-designs/
          c-operators-risk.md` says both — `:90` "refused", `:345` "skipped with
          the step named" — and `checklist.md:2501`, which is the definition of
          done this box ticks on, says *refused ... with the step named and no
          rows written, and applies once the server is stopped*. A `skipped`
          line inside a press that otherwise succeeds is not a refusal: the
          module would end up marked installed with its SQL never applied, which
          is the very defect 8.7a's third clause is about. `ApplyError` is this
          engine's one refusal vocabulary and every caller of `install()`
          already handles it. It also has to be a raise for `remove()`, whose
          SQL is the module's *undo* and whose next statements delete the clone
          holding it — a skip there loses that file forever, and `remove()`'s
          own comment already puts its refusals before the SQL for this reason.
          What the message must NOT claim is that nothing happened: by the time
          `_sql()` runs, `install()` has cloned, deployed and patched. It says
          what is true — no SQL, no rows — and that a second press repeats the
          steps already taken.
        * **`auth` is never a reason to refuse, but an `auth` step alongside a
          refused one does not run either.** Half a manifest is not an outcome
          anyone asked for, and the `auth` write survives the refusal being
          lifted (a second press re-runs it).
        * **The seam absent means the behaviour every caller had before this
          landed, byte for byte** (`c-operators-risk.md:345`). It shipped that
          way: for one day no caller passed `world_running` at all, so
          `_world_running` was `None` on all four games and this function
          returned at its first line — a capability, not a defence. T2's press
          had to attach the seam itself, and recorded that the Modules tab as it
          then shipped would have written 7 219 rows into a live world without a
          word. Every factory the app builds an `Applier` through passes it now
          (T7), and `test_apply.py` enumerates them so the next one added is
          caught rather than discovered.

        Fails closed on anything short of a clear "no": `None` and a seam that
        raises are both refusals, because *could not ask* is not *not running* —
        the same three-valued discipline `docker._running()` uses for a project
        it cannot read.
        """
        if self._world_running is None:
            return  # no seam: the behaviour every existing caller has today
        at_risk = [
            step
            for step in manifest.sql
            if step.when == when and step.applied_by == "direct" and step.db in WORLD_HELD_DBS
        ]
        # No runner means this run writes nothing whatever and `_sql()` already
        # says so per step. Refusing here would be a refusal about a write that
        # was never going to happen, and it would replace that message.
        if not at_risk or self.sql is None:
            return
        why = ""
        try:
            running: bool | None = self._world_running()
        except Exception as exc:  # noqa: BLE001 - any seam failure is one answer here
            logger.warning(f"could not tell whether the world is running: {exc}")
            running, why = None, f"{type(exc).__name__}: {exc}"
        if running is False:
            return
        # Named as the manifest spells them, and unrendered for `_pending_sql`'s
        # reason: `_render()` raises for a value this run has not got, and a
        # refusal that dies while composing its own sentence names nothing.
        steps = ", ".join(_step_name(step) for step in at_risk)
        dbs = ", ".join(sorted({step.db for step in at_risk}))
        if running is None:
            raise ApplyError(
                f"{manifest.id}: could not tell whether the world server is running "
                f"({why or 'the seam gave no answer'}), and a running one holds {dbs} in memory "
                f"and writes back over whatever it finds there. No SQL was run and no rows were "
                f"written: {steps}. Stop the server, then {when} again."
            )
        raise ApplyError(
            f"{manifest.id}: the world server is running, and it holds {dbs} in memory and writes "
            f"back over whatever it finds there. No SQL was run and no rows were written: "
            f"{steps}. Press Stop, then {when} again — the steps this run already took repeat, "
            f"and the SQL follows them."
        )

    def _start_the_database_for_direct_sql(self, manifest: Manifest, when: When, log: _Log) -> bool:
        """Make *"Press Stop, then install again"* a thing that can be done.

        The guard above tells a user to stop the server. The app's Stop is
        `docker.stop_staged()`, which takes the whole compose project down —
        database included — and every direct SQL step in this engine is a
        `docker exec` into that container. So the instruction ended in
        `Error response from daemon: container 3c922e8e... is not running`,
        measured through the app's own controls (T2, `4-press-world-stopped.log`).
        That is `bug-checklist §46` — *"there is no compliant way to install a
        SQL mod at all"* — whose title scopes it to CMaNGOS and whose mechanism
        is shared, so it was AzerothCore's too.

        Three decisions:

        * **The world is not started, ever.** Only the database, alone, which is
          the "world down, database up" state §46 says the app had no way to
          reach. Starting the world would put back the very thing the refusal
          above exists to keep away from these tables.
        * **Every `direct` step counts, not only the `WORLD_HELD_DBS` ones.**
          The guard's set is about what a running worldserver holds in memory;
          this is about whether there is a database process to talk to at all,
          and an `auth`-only mod fails the same way on a stopped stack. The
          `when` filter is kept, so a configure over a manifest whose SQL is all
          install-time reaches for nothing.
        * **A failure to start is a refusal, carrying the daemon's own
          sentence.** `docker.start_database()` names the container, the timeout
          and where the logs are; nothing here knows better, and a paraphrase
          would send the operator looking in the wrong place. Nothing has run
          when this raises, for the same reason the guard is a pre-pass.

        Returns whether the start seam was CONSULTED — not whether it started
        anything. `_sql()` re-reads the running-world guard on a true answer,
        because consulting it is what opens the window: the call can block for
        up to three minutes waiting on health (`_DB_HEALTHY_TIMEOUT_SECONDS`),
        and the world can come up inside it. `False` here means nothing was
        asked of Docker and no time passed,
        so the first reading is still the current one.
        """
        if self._start_database is None or self.sql is None:
            return False
        direct = [
            step for step in manifest.sql if step.when == when and step.applied_by == "direct"
        ]
        if not direct:
            return False
        try:
            started = self._start_database()
        except Exception as exc:  # noqa: BLE001 - any failure to start is one answer here
            steps = ", ".join(_step_name(step) for step in direct)
            raise ApplyError(
                f"{manifest.id}: the database could not be started, so no SQL was run and no "
                f"rows were written: {steps}. {exc}"
            ) from exc
        if started:
            log.done.append("started the database alone; the world server was left stopped")
        return True

    def _pending_sql(self, step: SqlStep, clone: Path) -> PendingSql:
        """Resolve a deferred step's glob so the count reported is a real one.

        The path is globbed UNRENDERED on purpose. `_action_templates()` leaves
        db-import paths out of `required_prompts()`, so a `{key}` in one was
        never put to the user and `_render()` here would raise on a step this
        run is not performing — turning a report into a failure. A `{key}` gets
        `files=None` instead of a glob that would match nothing and call it
        zero; see `PendingSql`.

        A step with no `path` cannot happen — `SqlStep._exactly_one_body`
        requires exactly one of `path`/`statement`, and an inline `statement`
        is never `applied_by="db-import"` (there is no file for the importer to
        find) — but the assert says so rather than the type checker alone.
        """
        assert step.path is not None
        return PendingSql(db=step.db, path=step.path, files=_sql_files(clone, step.path))

    def _plan_sql(
        self, manifest: Manifest, clone: Path, vals: Mapping[str, str], when: When
    ) -> dict[int, tuple[tuple[str, ...], str]]:
        """Resolve, read and check every named SQL file this press will run, before any runs.

        T100. Without it a press of several SQL steps ran them in order until one
        was missing or unusable, and the ones before it stayed applied --
        `hearthstone-cd`'s reset landed and then "sql file missing in clone" was
        reported, with a custom cooldown already gone; and after round 2, a
        good first step committed before a later `then` file was found to hold
        DDL (Codex, round 3). So every named file of every direct step is looked
        for here, and every `then` step's files are read, decoded and put through
        `transaction_refusal()` -- the only place "nothing was run" is said.

        Returns, per index into `manifest.sql`, the file names and the ONE text
        `_run_transaction()` sends, so nothing is read twice or read differently
        later. A glob is left alone: it names no file that can be missing.
        """
        if self.sql is None:
            return {}
        missing: list[str] = []
        refusals: list[str] = []
        plan: dict[int, tuple[tuple[str, ...], str]] = {}
        for index, step in enumerate(manifest.sql):
            if step.when != when or step.applied_by != "direct" or step.path is None:
                continue
            names = tuple(_render(t, vals, "sql path") for t in (step.path, *step.then))
            absent = [n for n in names if not _is_glob(n) and not (clone / n).is_file()]
            missing += absent
            if not step.then or absent:
                continue
            texts: list[str] = []
            for name in names:
                try:
                    text = (clone / name).read_bytes().decode("utf-8-sig")
                except UnicodeDecodeError as exc:
                    refusals.append(f"{name}: not UTF-8 text ({exc.reason} at byte {exc.start})")
                    continue
                refusal = transaction_refusal(name, text)
                if refusal:
                    refusals.append(refusal)
                texts.append(text if text.endswith("\n") else text + "\n")
            plan[index] = (names, "START TRANSACTION;\n" + "".join(texts) + "COMMIT;\n")
        if missing:
            refusals.insert(
                0,
                f"{', '.join(missing)} is not in the module's files; its upstream may have "
                f"renamed or removed it",
            )
        if refusals:
            raise ApplyError(f"{manifest.id}: nothing was run. " + " ".join(refusals))
        return plan

    def _run_transaction(
        self, step: SqlStep, planned: tuple[tuple[str, ...], str], log: _Log
    ) -> None:
        """`path` and every `then` file as ONE text inside one transaction (T100 review).

        Sent over `run_statement()` -- the runner every inline step already
        uses, which pipes the text into `mysql` on stdin. `mysql` reading a
        script stops at the first error, and the session it ends rolls back the
        transaction still open, so a chosen file that fails takes the reset
        before it with it. That holds only for statements that do not commit on
        their own, which `_plan_sql()` has already made sure of for every file
        (`transaction_refusal()`). `item_template` is InnoDB on AzerothCore --
        read on the live world database, see the T100 gate -- which is what
        makes the rollback real.
        """
        assert self.sql is not None
        names, text = planned
        self.sql.run_statement(step.db, text)
        for name in names:
            log.done.append(f"sql {name} → {step.db}")

    def _run_sql(
        self,
        step: SqlStep,
        clone: Path,
        vals: Mapping[str, str],
        log: _Log,
        planned: tuple[tuple[str, ...], str] | None = None,
    ) -> None:
        assert self.sql is not None
        if step.statement is not None:
            self.sql.run_statement(step.db, _render(step.statement, vals, "sql statement"))
            log.done.append(f"sql inline → {step.db}")
            return
        if step.then:
            assert planned is not None, "a `then` step runs only from `_plan_sql()`'s text"
            self._run_transaction(step, planned, log)
            return
        assert step.path is not None
        pattern = _render(step.path, vals, "sql path")
        files = sorted(clone.glob(pattern)) if _is_glob(pattern) else [clone / pattern]
        for path in files:
            if not path.is_file():
                raise ApplyError(f"sql file missing in clone: {path}")
            self.sql.run_file(step.db, path)
            log.done.append(f"sql {_rel(clone, path)} → {step.db}")

    def _ask_db(self, check: ExistsCheck) -> tuple[bool | None, str]:
        """Did a row come back — `True`, `False`, or `None` for *could not ask*, and why.

        Three-valued for `docker._running()`'s reason, and here the third value
        is not a rare one: the case T63 exists for — `acore_playerbots` before
        mod-playerbots has ever started — is a schema that does not exist, and a
        `mysql` told to connect to it fails rather than returning no rows. So
        *the database is not there yet* and *the table is not there yet* arrive
        by two different routes and must mean the same thing to both callers.

        The query is sent UNRENDERED. Only `Prompt.exists` templates over the
        user's answers; a `SqlStep` check is the catalog's own sentence about
        the module's own tables, `test_manifest.py::test_no_sql_step_check_
        carries_a_template_field` holds every shipped one to that, and rendering
        here would mean a `{` in somebody's SQL could raise inside a check whose
        whole job is to answer a question.
        """
        if not isinstance(self.sql, SqlReader):
            return None, "this install has no database reader"
        try:
            rows = self.sql.query(check.db, check.query)
        except Exception as exc:  # noqa: BLE001 - every seam failure is one answer here
            return None, f"{type(exc).__name__}: {exc}"
        return bool(rows.strip()), ""

    def _precondition_met(self, step: SqlStep, log: _Log) -> bool:
        """Whether this step's turn has come — and if not, why, in the report.

        T63. A direct step can be correct and still be too early: `mod-city-bots`
        writes `playerbots_account_type`, which mod-playerbots creates on the
        world server's FIRST start, and that start is after the rebuild an
        install only *reports*. Before this, the step would have been run
        regardless and `mysql` would have failed on a missing table — an
        `ApplyError` out of `_run_sql()`, after the clone, the deploy and the
        patches, for a module that is in fact installed correctly and merely not
        finished. That is a press reported as broken when the true answer is
        "come back after the rebuild".

        Three decisions:

        * **Skipped, not refused.** Everything this press did is real and worth
          keeping, and the remaining work is the user's (rebuild, start once).
          The sentence goes in `skipped`, which the Modules tab already prints
          under `– skipped:` (`controller_view.py::_format_apply_report`), so
          nothing new has to be reached for the user to be told.
        * **Fails CLOSED.** `None` — no reader, or a query that raised — skips
          too, carrying the seam's own words. *Could not ask* is not *yes*, and
          the write on the other side of this question is a `DROP TABLE`.
        * **Per step, not a pre-pass.** Unlike the running-world guard, a
          precondition is about ONE step's own tables; a manifest's other steps
          are not implicated and are not held back by it.
        """
        if step.precondition is None:
            return True
        found, why = self._ask_db(step.precondition)
        if found:
            return True
        detail = (
            step.precondition.missing if found is False else f"{why}. {step.precondition.missing}"
        )
        log.skipped.append(f"{_step_name(step)}: {detail}")
        return False

    def _verify_sql(self, manifest: Manifest, step: SqlStep, log: _Log) -> None:
        """Prove the step produced the state it claims, or raise saying which half did not.

        T63, and the half that is a port rather than an invention:
        `city_bots_import_roster()` in `guides/wow-wotlk/wow-manage.sh` counts
        400 roster rows AND 400 city-bot account types after its import and
        calls anything else a failure, because `mysql` exiting 0 over a file of
        400 inserts says the statements parsed, not that the cast is there.

        **Each entry is asked separately and says its own sentence**, which is
        the whole reason `verify` is a list and not one query with two clauses:
        a single check that both counts are 400 is green for two different
        reasons and red for two more, and a refusal that cannot say which half
        failed sends the operator to look in the wrong table.

        It raises, like every other refusal in this engine — but the message is
        careful not to claim nothing happened, because something did: the file
        ran. A `skipped` line would be the worse lie of the two (the module
        would be marked installed with its roster half-written), and a repeat is
        safe: the roster file drops and recreates its own table, so pressing
        Install again after fixing the cause is a repair and not a second copy.

        `None` — no reader, or a query that raised — is reported and does NOT
        refuse, and that is the opposite of `_precondition_met()`'s fail-closed
        on purpose: there the unknown gates a write, here the write has already
        happened and an unaskable database is not evidence against it.
        """
        for check in step.verify:
            found, why = self._ask_db(check)
            if found:
                continue
            if found is None:
                log.skipped.append(
                    f"{_step_name(step)}: ran, but NOT checked against the database ({why}), "
                    f"so {check.missing[0].lower() + check.missing[1:]} would not be noticed here"
                )
                continue
            raise ApplyError(
                f"{manifest.id}: {check.missing}. {_step_name(step)} was run — the file's "
                f"statements reached the database — so this is the result being wrong and not "
                f"the step being skipped. Fix the cause and {step.when} again: the import "
                f"replaces its own rows, so a repeat repairs rather than duplicates."
            )

    def _conf(self, manifest: Manifest, clone: Path, vals: Mapping[str, str], log: _Log) -> None:
        for conf in manifest.conf:
            if _is_glob(conf.file) or not conf.file.endswith(_CONF_KEY_WRITE_SUFFIXES):
                continue  # Lua/DB-table "conf" is patched or prompted, not key-written
            target = self.server_dir / conf.file
            if conf.template is not None and not target.exists():
                template = clone / conf.template
                if not template.is_file():
                    log.skipped.append(f"conf {conf.file}: template {conf.template} not in clone")
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(template, target)
                log.done.append(
                    f"activate {conf.file} from {conf.template} — the world reads "
                    f"{conf.file} at its next start"
                )
                log.conf_restart = True
            writes = [(k.key, k.default) for k in conf.keys if k.default is not None]
            # A key the catalog names with no `default` is a step nobody takes.
            # Measured on yulon-ubuntu 2026-09-08 (8.7a, defect 1): the four
            # `mod_npc_beastmaster.conf` keys and `Creatures.CustomIDs` on the
            # core's own `worldserver.conf` were dropped here in silence — absent
            # from `done`, absent from `skipped`, with the file byte-identical
            # afterwards. Reported rather than filled in: which value belongs in
            # a user's core configuration is the catalog's sentence to write, and
            # `Creatures.CustomIDs` in particular is an APPEND to a list this
            # applier has no syntax for.
            valueless = [k.key for k in conf.keys if k.default is None]
            if valueless:
                log.skipped.append(
                    f"conf {conf.file}: no value in the catalog for "
                    f"{', '.join(valueless)} — not written"
                )
            if not writes:
                continue
            if not target.is_file():
                log.skipped.append(f"conf {conf.file}: file missing, keys not written")
                continue
            changed = False
            for key, default in writes:
                mode = _set_conf_key(target, key, _render(default, vals, f"conf {key}"))
                changed = changed or mode != "unchanged"
            if not changed:
                continue  # every key already read this value: nothing to restart for
            log.done.append(
                f"set {len(writes)} key(s) in {conf.file} — the world reads "
                f"{conf.file} at its next start"
            )
            log.conf_restart = True

    def _client(self, manifest: Manifest, clone: Path, log: _Log) -> None:
        """Copy this manifest's `client` steps into the game client's own folders.

        **Never the checkout's bookkeeping** (T65). Two `wow-tortoise` addons
        deploy with `src: "."` -- the addon IS the repository root -- so the
        copy carried `.git` and this app's `CLAIM_FILE` into
        `Interface/AddOns/<name>`. Git writes its pack and idx files 0444, and
        `copytree(dirs_exist_ok=True)` cannot open a 0444 destination for
        writing: the SECOND install of either addon died with a raw
        `shutil.Error` (Errno 13) before it had landed a single new file, so
        the addon could be installed once and never updated or reinstalled. A
        pinned install failed identically, which is why this is not T60's.

        Left out rather than force-overwritten: WoW reads the `.toc` and the
        files it names, so a copy of somebody's git history in the AddOns
        folder was never wanted -- it was 20+ MB of what the user's client has
        to scan, and it is why the manifests' notes claimed the `.git` copy was
        intended. Those notes are corrected with this change. A user who
        removes and reinstalls also gets no stale `.git` back, because the
        clone is where the history lives and it stays there.
        """
        for step in manifest.client:
            if self.client_dir is None:
                log.skipped.append(f"client {step.src}: no client dir configured")
                continue
            src = clone / step.src
            if step.dest == "addons":
                target = self.client_dir / "Interface" / "AddOns" / (step.name or src.name)
            elif step.dest == "interface":
                target = self.client_dir / "Interface"
            else:
                target = self.client_dir / "Data"
            if src.is_dir():
                shutil.copytree(src, target, dirs_exist_ok=True, ignore=_NOT_FOR_THE_CLIENT)
                if step.dest == "data":
                    log.client_copies += self._receipts(step.src, src, target)
            elif src.is_file():
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target / src.name)
                if step.dest == "data":
                    log.client_copies += self._receipts(step.src, src, target)
            else:
                raise ApplyError(f"client source missing in clone: {src}")
            log.done.append(f"client {step.src} → {step.dest}")

    @staticmethod
    def _receipts(step: str, src: Path, target: Path) -> list[ClientCopy]:
        """Hash what a `dest: data` step just put in the client, for `remove()` to check.

        Only `dest: data`. An addon folder is never taken back (the owner's
        decision, T67: the player disables it in the game's AddOns menu), and
        `dest: interface` copies INTO the shared `Interface/` folder, where this
        app cannot tell its own files from the client's — so neither is worth a
        receipt nobody would act on.

        **WHICH files is read off the SOURCE in the clone, never off a listing of
        the destination, and that distinction is a whole `Data/` folder.** Round 1
        review, 2026-09-16: the first version listed `target` — and for a step
        whose `src` is a DIRECTORY, `_client` copies its contents INTO
        `<client>/Data`, so `target` IS the user's `Data/` folder. It recorded a
        receipt for every archive the user's WoW install has ever had, each
        matching its own hash at remove time, and removing the shipped Season of
        Discovery keg (`kegs/sod.json`, `src: .../Client Files/data`, `dest:
        data`) emptied `Data/`. The source names exactly the files this app
        copied; nothing else can be ours by definition.

        The HASH is still taken from the destination copy, which is a different
        question from which files to hash: same bytes when all goes well, and
        when it does not — a short copy, a full disk — the receipt describes the
        file that is really in the user's game, which is the one `remove()` will
        be comparing against.

        A source file with nothing at its destination, or a copy that cannot be
        hashed, yields no receipt — the same state as an older install's: the
        file stays at remove time and is named. Never fatal: the install itself
        succeeded.
        """
        if src.is_dir():
            pairs = [(target / p.relative_to(src)) for p in sorted(src.rglob("*")) if p.is_file()]
        else:
            pairs = [target / src.name]
        out: list[ClientCopy] = []
        for path in pairs:
            try:
                out.append(ClientCopy(step=step, path=str(path), sha256=sha256_of(path)))
            except OSError as exc:
                logger.warning(
                    f"could not hash {path} after copying it, so it is not recorded: {exc}"
                )
        return out

    def _unclient(self, manifest: Manifest, clone: Path, log: _Log) -> None:
        """Take back the client patches this app can PROVE it put there; name the rest (T67).

        The owner's rule, 2026-09-16: a module's client MPQ is deleted from the
        client's `Data/` only if it is still byte-for-byte the file this app
        copied, and an addon folder is never deleted at all.

        Why an MPQ is deleted and an addon folder is not, when both were copied
        by the same step: the 3.3.5a client loads EVERY `Patch-*.MPQ` in `Data/`
        at start and offers what they contain. A left-behind ARAC patch goes on
        offering race/class pairs the server no longer has DBCs for, which is a
        character creation screen that lies and a login that fails. An addon does
        nothing of the kind, the game has a checkbox for turning it off, and the
        player may have keybinds and saved variables attached to it.

        Why a checksum and not "the app installed this item, so this file is
        its": the path is `<the user's game>/Data/<a name a catalog entry
        chose>`. The user may have put their own patch there, or edited ours.
        `Ownership`'s lesson holds — evidence, and a refusal when there is none.

        Must run BEFORE the clone is deleted: the receipts live in the clone's
        claim file, which the `rmtree` at the end of `remove()` takes with it.
        """
        copies = read_client_copies(clone, item_id=manifest.id) if clone.is_dir() else ()
        for step in manifest.client:
            if self.client_dir is None:
                log.client_left_behind.append(
                    f"{step.src} (in whatever game client you installed it into — no game "
                    f"client folder is set here now, so Yu'lon could not reach it)"
                )
                continue
            if step.dest == "addons":
                name = step.name or Path(step.src).name
                log.client_left_behind.append(
                    f"the {name} addon folder in your game client's Interface/AddOns "
                    f"(Yu'lon does not delete addons — disable it in the game's AddOns menu)"
                )
                continue
            if step.dest == "interface":
                log.client_left_behind.append(
                    f"{step.src} (copied into your game client's Interface folder, where "
                    f"Yu'lon cannot tell its own files from the game's)"
                )
                continue
            mine = [c for c in copies if c.step == step.src]
            if not mine:
                log.client_left_behind.append(
                    f"{step.src} (in your game client's Data folder — Yu'lon has no record of "
                    f"copying it, so it left it alone)"
                )
                continue
            for copy in mine:
                self._take_back(copy, log)

    def _take_back(self, copy: ClientCopy, log: _Log) -> None:
        """One recorded file: delete it if it is still ours byte-for-byte, else say why not."""
        path = Path(copy.path)
        if not path.exists():
            log.skipped.append(f"client {path.name}: already gone from {path.parent}")
            return
        try:
            same = sha256_of(path) == copy.sha256
        except OSError as exc:
            log.client_left_behind.append(
                f"{path.name} in your game client's Data folder (Yu'lon could not read it to "
                f"check whether it is still the file it copied: {exc})"
            )
            return
        if not same:
            log.client_left_behind.append(
                f"{path.name} in your game client's Data folder (it has changed since Yu'lon "
                f"copied it, so it left it alone)"
            )
            return
        try:
            path.unlink()
        except OSError as exc:
            log.client_left_behind.append(
                f"{path.name} in your game client's Data folder (Yu'lon could not delete it: "
                f"{exc} — close the game and delete it by hand)"
            )
            return
        log.done.append(f"took back {path.name} from {path.parent}")

    def _dbc(self, manifest: Manifest, clone: Path, log: _Log) -> None:
        for step in manifest.server_dbc:
            if self.dbc is None:
                log.skipped.append(f"server_dbc {step.src}: no DBC copier configured")
                continue
            self.dbc.copy_dbc_dir(clone / step.src)
            log.done.append(f"server_dbc {step.src} → data/dbc/")

    # -- helpers -----------------------------------------------------------

    def _values(self, manifest: Manifest, values: Mapping[str, str] | None) -> dict[str, str]:
        """Defaults, then what this install remembers, then what the caller handed in.

        The middle layer is T104's. It is what makes a Remove of a mob
        multiplier divide by the factor install multiplied by, and what any
        caller that asks nothing (`values=None`) now gets instead of the
        manifest's default. An install with no record -- every one made before
        T104 -- gets exactly the defaults it always did.
        """
        merged = {p.key: p.default for p in manifest.prompts if p.default is not None}
        merged.update(self.remembered_answers(manifest))
        merged.update(values or {})
        return merged

    def remembered_answers(self, manifest: Manifest) -> dict[str, str]:
        """The answers last given for `manifest` on this install that are still usable.

        Only declared questions, and only answers `check_answer()` accepts: a
        saved `choice` the manifest no longer offers is dropped rather than
        pre-filled, and the default takes its place. Never raises; a record it
        cannot read is no record (`module_answers._read_all()`).
        """
        saved = module_answers.read_answers(self.server_dir, manifest)
        return {
            prompt.key: saved[prompt.key]
            for prompt in manifest.prompts
            if prompt.key in saved and check_answer(prompt, saved[prompt.key]) == ""
        }

    def applied_record(self, manifest: Manifest) -> tuple[dict[str, str] | None, str]:
        """What this install's database holds for a relative manifest, and why not (T115).

        `(values, "")` for a usable record: every answer the remove statements
        render, each one `check_answer()` accepts and, for a number, not zero --
        nothing divides by zero, and MySQL would write NULL. `(None, "")` for no
        record at all. `(None, why)` for a record that is there and cannot be
        used, which is a known re-run with unknown values -- including one still
        marked in flight (`module_answers.is_pending()`), where a press stopped
        between marking its statement and recording the result.
        """
        if module_answers.is_pending(self.server_dir, manifest):
            return None, (
                "an earlier press stopped while its SQL was being sent, so Yu'lon cannot tell "
                "whether the database holds the values from before it or after it"
            )
        raw = module_answers.read_applied(self.server_dir, manifest)
        if raw is None:
            return None, ""
        out: dict[str, str] = {}
        for prompt in required_prompts(manifest, "remove"):
            value = raw.get(prompt.key)
            if value is None:
                return None, f"it has no value for {prompt.question!r}"
            problem = check_answer(prompt, value)
            if not problem and prompt.kind in ("int", "float") and float(value) == 0:
                problem = "it is zero, and nothing divides by zero"
            if problem:
                return None, f"{prompt.question!r} was recorded as {value!r}: {problem}"
            out[prompt.key] = value
        return out, ""

    def reapply_refusal(self, manifest: Manifest) -> str | None:
        """Why running this relative manifest's install again is refused, or `None` (T115).

        The ruling: a KNOWN re-run whose applied values cannot be read is refused,
        never guessed. Known means a record that is there but unusable, or a
        clone whose install finished with no record at all (a sourced relative
        manifest; none is shipped). Remove asks for the values in its own dialog,
        so the remedy is Remove, then Install. An install made by an older
        Yu'lon leaves neither, and the database cannot say what it was
        multiplied by, so that one is not detectable here.
        """
        if not reapplies_on_top(manifest):
            return None
        applied, why = self.applied_record(manifest)
        if applied is not None:
            problem = reapply_steps_problem(manifest)
            if not problem:
                return None
            why = f"{problem}, so Yu'lon cannot undo the last values and apply these in one go"
        elif not why:
            finished = clone_install_completed(self.clone_dir(manifest), item_id=manifest.id)
            if finished is not True:
                return None
            why = "there is no record of the values it was applied with"
        return (
            f"{manifest.name} ({manifest.id}) is already applied to this server's database, "
            f"and running it again would multiply the values it already multiplied: {why}. "
            f"Remove it first, then Install. Nothing was changed."
        )

    def _undo_values(self, manifest: Manifest) -> dict[str, str] | None:
        """The applied record a re-install undoes first, `None` for a first install; or refuse."""
        refusal = self.reapply_refusal(manifest)
        if refusal is not None:
            raise ApplyError(refusal)
        if not reapplies_on_top(manifest):
            return None
        return self.applied_record(manifest)[0]

    def _relative_text(
        self,
        manifest: Manifest,
        when: When,
        vals: Mapping[str, str],
        undo: Mapping[str, str] | None,
    ) -> tuple[Db, str]:
        """One transaction: on a re-install, remove's statements with `undo` first, then `when`'s.

        Sent over `run_statement()` as T100's `_run_transaction()` sends its
        files: `mysql` stops at the first error and the session it ends rolls
        the transaction back, so the divide never lands without the multiply.
        `creature_template` is InnoDB on AzerothCore (read on the m910q world
        database, T115 gate), which is what makes that rollback real.
        """
        passes: list[tuple[When, Mapping[str, str]]] = []
        if when == "install" and undo is not None:
            passes.append(("remove", {**vals, **undo}))
        passes.append((when, vals))
        parts: list[str] = []
        db: Db | None = None
        for step_when, values in passes:
            for step in manifest.sql:
                if step.when != step_when:
                    continue
                assert step.statement is not None, "`reapply_steps_problem()` refused this"
                db = step.db
                text = _render(step.statement, values, "sql statement").strip()
                parts.append(text if text.endswith(";") else text + ";")
        assert db is not None, "a relative manifest has install and remove SQL"
        return db, "START TRANSACTION;\n" + "\n".join(parts) + "\nCOMMIT;\n"

    def _run_relative(
        self,
        manifest: Manifest,
        vals: Mapping[str, str],
        when: When,
        undo: Mapping[str, str] | None,
        log: _Log,
    ) -> None:
        """Mark the record pending, send the one text, then record what the database holds.

        **The order is the fix-wave's (cold review, Important).** The first
        version wrote the new record before `_sql()`'s guards and database start,
        which can wait up to 180 s: killed there, the record said x-new over a
        database still at x-old, and Remove divided by the wrong number. Now:

        1. Nothing is written until the statement is the very next thing.
        2. `record_pending()` marks it in flight, fail-closed: if that cannot be
           written, nothing is sent. A kill from here on leaves a mark that
           `applied_record()` reads as unusable -- a re-run is refused and Remove
           asks -- instead of an answer that may be wrong.
        3. The statement. A failure is NOT proof of a rollback (Codex, T115):
           ERROR 2013 or a broken `docker exec` can come after the server ran
           COMMIT. So the mark stays and the failure says Yu'lon cannot tell
           whether the change landed. Only `SqlNotSent` -- no process ever ran --
           drops the mark, leaving the untouched applied record true.
        4. After the commit, `record_applied()` writes the new values (or clears
           them for a Remove) and drops the mark in one write.

        With no SQL runner nothing is sent, so the record is left alone (Minor 3).
        """
        if self.sql is None:
            log.skipped.append(f"sql → {manifest.id}: no SQL runner configured")
            return
        db, text = self._relative_text(manifest, when, vals, undo)
        after = (
            {p.key: vals[p.key] for p in required_prompts(manifest, "remove")}
            if when == "install"
            else None
        )
        was_pending = module_answers.is_pending(self.server_dir, manifest)
        problem = module_answers.record_pending(self.server_dir, manifest, after)
        if problem:
            raise ApplyError(
                f"{manifest.id}: nothing was run. Yu'lon could not note in its record that "
                f"this SQL was about to run ({problem}), and without that note an "
                f"interrupted run could leave the record saying the wrong values."
            )
        try:
            self.sql.run_statement(db, text)
        except SqlNotSent:
            # Proven: no process ran, so the database is as the applied record
            # says -- unless an EARLIER press was interrupted, whose mark stays.
            if not was_pending:
                dropped = module_answers.clear_pending(self.server_dir, manifest)
                if dropped:
                    logger.warning(
                        f"{manifest.id}: nothing reached the database, but the in-flight mark "
                        f"could not be dropped ({dropped}); the next Install will be refused "
                        f"and Remove will ask for the values"
                    )
            raise
        except Exception as exc:
            # NOT proof of a rollback (Codex, T115): ERROR 2013 or a broken
            # `docker exec` can come after the server ran COMMIT. The mark stays,
            # so the record reads as unknown: a re-run is refused and Remove asks.
            raise ApplyError(
                f"{manifest.id}: the SQL failed ({exc}), and Yu'lon cannot tell whether the "
                f"change reached the database before it did. Its record of the values is now "
                f"marked as unknown: Install is refused until you Remove {manifest.id}, and "
                f"Remove will ask which values are in the database."
            ) from exc
        if when == "install" and undo is not None:
            log.done.append(
                f"sql inline → {db}: undid the values applied last time and applied these, "
                f"in one transaction"
            )
        else:
            log.done.append(f"sql inline → {db}")
        problem = module_answers.record_applied(self.server_dir, manifest, after)
        if problem:
            log.skipped.append(
                f"{module_answers.ANSWERS_FILE}: the SQL ran, but the record of what it "
                f"applied could not be updated ({problem}); it stays marked as interrupted, "
                f"so the next Install of {manifest.id} is refused and Remove asks for the values"
            )

    def _remember(self, manifest: Manifest, values: Mapping[str, str] | None, log: _Log) -> None:
        """Keep the answers this press was handed, once every step they fed has run (T104).

        Only what the caller HANDED IN, never the defaults filled in around it:
        `values=None` is a caller that asked nothing, and it must not overwrite
        what the player said last time. Only declared questions.
        """
        declared = {prompt.key for prompt in manifest.prompts}
        answers = {k: str(v) for k, v in (values or {}).items() if k in declared}
        if not answers:
            return
        problem = module_answers.record_answers(self.server_dir, manifest, answers)
        if problem:
            log.skipped.append(
                f"{module_answers.ANSWERS_FILE}: your answers could not be saved ({problem}), "
                f"so the next Update of {manifest.id} will offer the defaults instead"
            )

    def _report(self, action: When, manifest: Manifest, log: _Log) -> ApplyReport:
        report = ApplyReport(
            action=action,
            item_id=manifest.id,
            family=manifest.type,
            done=tuple(log.done),
            skipped=tuple(log.skipped),
            rebuild_required=manifest.build.rebuild and action != "configure",
            # Declared first, then derived. Three of the four derived clauses
            # read the manifest itself — NPCs, direct SQL and server DBCs, all
            # of which reach the database or the data volume. The fourth reads
            # what THIS RUN actually did: `log.conf_restart`, set by `_conf()`
            # the moment it writes a byte to a file the running world reads
            # only at startup (T27; measured live, `.notes/gates/8.6-spec-
            # takes-effect-yulon-ubuntu2-2026-09-10/03-activate.log:38` —
            # activating `mod-playerbots`' conf reported `restart_recommended
            # = False` while the world went on running the OLD config until
            # the next restart). A conf step that found nothing to write —
            # the file already there, no keyed value in the catalog — leaves
            # `conf_restart` False, same as a manifest with no `conf` at all.
            # `build.restart` is the one clause that is a DECLARATION rather
            # than an observation; every clause here can only ADD a yes, never
            # take one away.
            restart_recommended=bool(
                manifest.build.restart
                or manifest.npcs
                or any(s.applied_by == "direct" for s in manifest.sql)
                or manifest.server_dbc
                or log.conf_restart
            ),
            pending_sql=tuple(log.pending_sql),
            left_behind=(
                _left_behind(manifest, tuple(log.client_left_behind)) if action == "remove" else ()
            ),
        )
        logger.info(
            f"{action} {manifest.id}: {len(report.done)} step(s), "
            f"{len(report.skipped)} skipped, {len(report.pending_sql)} left unapplied, "
            f"rebuild={report.rebuild_required}"
        )
        return report


# --------------------------------------------------------------- functions


def _left_behind(manifest: Manifest, client: tuple[str, ...] = ()) -> tuple[str, ...]:
    """The steps `remove()` did not take back (see `ApplyReport.left_behind`).

    The server DBC and SQL halves are read off the MANIFEST, because the rows and
    the files in question were put there by an INSTALL, possibly long ago, and a
    remove run has no record of that install — only of what the manifest says it
    does.

    The client half is the opposite and is passed IN, because since T67 there is
    such a record: `_unclient()` has just been through every `client` step with
    the receipts the install wrote, deleted what it could show was its own, and
    written a sentence for each one it did not. Reading `manifest.client` here as
    well would name the file it had just deleted.
    """
    out = [
        f"the server DBC files from {step.src} (in the server's data volume)"
        for step in manifest.server_dbc
    ]
    out += client
    if not any(step.when == "remove" for step in manifest.sql):
        out += [
            f"what {step.path or 'its SQL'} wrote into the {step.db} database"
            for step in manifest.sql
            if step.when == "install" and step.applied_by == "direct"
        ]
    return tuple(out)


def _sql_files(clone: Path, path: str) -> tuple[str, ...] | None:
    """A step's declared `path` resolved against `clone`. `None` when it cannot be.

    Shared by `Applier._pending_sql()` (which reports what is waiting) and
    `module_sql_plan()` (which reports what each route owns), because the two
    describe the SAME files and a second resolver here would be a second answer
    to "which files does this step name".

    The path is globbed UNRENDERED on purpose. `_action_templates()` leaves
    db-import paths out of `required_prompts()`, so a `{key}` in one was never
    put to the user and `_render()` would raise on a step this run is not
    performing -- turning a report into a failure. A `{key}` gets `None` instead
    of a glob that would match nothing and call it zero; see `PendingSql`.

    `as_posix()`, not `_rel()`: these names are compared against the manifest's
    own `path` (forward slashes, always) and read by a person who may be on
    Windows, and `_rel()` would answer `data\\sql\\db-world\\a.sql` there and
    `data/sql/db-world/a.sql` on the Linux box the same install was measured on.
    One spelling, both.
    """
    if _fields(path):
        return None
    matches = sorted(clone.glob(path)) if _is_glob(path) else [clone / path]
    return tuple(p.relative_to(clone).as_posix() for p in matches if p.is_file())


_TRANSACTION_SAFE = frozenset({"UPDATE", "INSERT", "REPLACE", "DELETE", "SELECT", "SET"})
"""The statements a `then` step may hold: row changes and reads, nothing else (T100 round 3).

An ALLOWLIST, because the list of statements MySQL commits around by itself
(8.4 manual, "Statements That Cause an Implicit Commit") is long, grows with
each release, and a denylist that misses one entry calls a half-applied step
atomic. `SET` is further narrowed in `transaction_refusal()` to user variables.
"""

_USER_VARIABLE_SET = re.compile(r"SET\s+@[A-Za-z0-9_$.]+\s*(:?=)", re.IGNORECASE)


class _SqlSplitError(ValueError):
    """The text cannot be split into statements this app can vouch for."""


def split_sql(text: str) -> list[str]:
    """Statements of a SQL script, comments removed, quoted text kept as written.

    A small scanner, not a parser: it knows exactly what decides where a MySQL
    statement ends -- `'...'`, `"..."` and `` `...` `` quoting (a backslash
    escapes inside the first two, a doubled quote closes and reopens), `-- `,
    `#` and `/* */` comments, and `;` -- so a `;` or a keyword inside a string
    or a comment is not a statement. It refuses (`_SqlSplitError`) what it will
    not guess at: an executable `/*! ... */` comment (MySQL runs its body), a
    backslash outside a quote (a `mysql` client command such as `\\!` or
    `\\g`), and a quote or comment left open at the end of the text.
    """
    statements: list[str] = []
    current: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch in "'\"`":
            j = i + 1
            while True:
                if j >= n:
                    raise _SqlSplitError(f"a {ch} quote is never closed")
                if text[j] == "\\" and ch != "`":
                    j += 2
                    continue
                if text[j] == ch:
                    break
                j += 1
            current.append(text[i : j + 1])
            i = j + 1
        elif ch == "#" or (ch == "-" and nxt == "-" and (i + 2 >= n or text[i + 2] <= " ")):
            j = text.find("\n", i)
            i = n if j < 0 else j
            current.append(" ")
        elif ch == "/" and nxt == "*":
            if i + 2 < n and text[i + 2] == "!":
                raise _SqlSplitError("an executable /*! ... */ comment, which MySQL runs")
            j = text.find("*/", i + 2)
            if j < 0:
                raise _SqlSplitError("a /* comment is never closed")
            current.append(" ")
            i = j + 2
        elif ch == "\\":
            raise _SqlSplitError("a backslash outside a quote, which the mysql client runs")
        elif ch == ";":
            statements.append("".join(current).strip())
            current = []
            i += 1
        else:
            current.append(ch)
            i += 1
    statements.append("".join(current).strip())
    return [statement for statement in statements if statement]


def transaction_refusal(name: str, text: str) -> str:
    """Why `text` cannot go inside one transaction, or `""` when every statement can.

    Checked per statement, after `split_sql()`: a DDL statement second on a
    line used to pass a check that only looked at the start of each line
    (Codex, T100 round 3). Every statement's first word must be one of
    `_TRANSACTION_SAFE`, and a `SET` must assign user variables only (`SET @x =
    ...`) -- `SET autocommit`, `SET TRANSACTION`, `SET PASSWORD` and every other
    system variable are refused, the first two because they end or reshape the
    transaction this is for.
    """
    tail = "so this step cannot run inside one transaction"
    try:
        statements = split_sql(text)
    except _SqlSplitError as exc:
        return f"{name}: {exc}, {tail}"
    for statement in statements:
        word = statement.split(None, 1)[0].upper() if statement.split() else ""
        shown = " ".join(statement.split())[:60]
        if word not in _TRANSACTION_SAFE:
            return f"{name}: `{shown}` is not a row change or a read, {tail}"
        if word == "SET" and not all(
            _USER_VARIABLE_SET.match("SET " + part.strip())
            for part in statement[3:].split(",")
            if part.strip()
        ):
            return f"{name}: `{shown}` sets more than a user variable, {tail}"
    return ""


def _render(template: str, values: Mapping[str, str], what: str) -> str:
    """`{key}` substitution; a missing key is an `ApplyError`, never silent garbage."""
    try:
        return template.format_map(dict(values))
    except KeyError as exc:
        raise ApplyError(f"{what}: no value for {{{exc.args[0]}}}") from exc
    except (IndexError, ValueError) as exc:
        raise ApplyError(f"{what}: bad template {template!r}: {exc}") from exc


def _is_glob(path: str) -> bool:
    return any(ch in path for ch in "*?[")


def _step_name(step: SqlStep) -> str:
    """How a SQL step is named to a human — the manifest's own spelling, unrendered.

    The same shape `_refuse_direct_sql_into_a_running_world()` builds its list
    from, and for the same reason: a sentence about a step that has not run must
    not die composing its own subject. One function, so a user who meets the
    running-world refusal and then a skipped precondition reads one vocabulary.
    """
    return f"sql {step.path or 'inline'} → {step.db}"


def _apply_patch(path: Path, patch: Patch, replacement: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if patch.regex:
        new = re.sub(patch.find, replacement, text, flags=re.MULTILINE)
    else:
        new = text.replace(patch.find, replacement)
    if new == text:
        return False
    path.write_text(new, encoding="utf-8", newline="\n")
    return True


_KeyMode = Literal["replace", "append", "unchanged"]


def _set_conf_key(path: Path, key: str, value: str) -> _KeyMode:
    """Set `key = value` in a worldserver-style conf: replace the line, append it, or —

    T27 round 2 (Codex adversarial review): a re-apply of a keyed conf that already
    reads `key = value` byte-for-byte used to hit the replace branch every time and
    write the file anyway, so `_conf()` could not tell a real change from a no-op —
    it reported `restart_recommended = True` over a conf it had not touched. `new ==
    text` is that no-op, caught before the write rather than after: nothing on disk
    changes, and the caller sees `"unchanged"` rather than `"replace"`.
    """
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=.*$", re.MULTILINE)
    new, count = pattern.subn(f"{key} = {value}", text, count=1)
    if count:
        if new == text:
            return "unchanged"
        path.write_text(new, encoding="utf-8", newline="\n")
        return "replace"
    sep = "" if text.endswith("\n") or not text else "\n"
    path.write_text(f"{text}{sep}{key} = {value}\n", encoding="utf-8", newline="\n")
    return "append"


def _rel(base: Path, path: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


@dataclass(frozen=True)
class ModuleUpdate:
    """One installed module and how far behind its upstream it is (checklist 8.7a)."""

    key: str
    """The folder name under `modules/` — which is the module's id, and the same
    string `docker.allowed_modules()` hands the importer."""

    path: Path
    is_checkout: bool
    """Whether this folder is a git checkout at all. A `modules/` directory also
    holds `CMakeLists.txt`, `ModulesLoader.cpp.in.cmake` and friends beside the
    modules (read off yulon-ubuntu, 2026-09-07), and a user can copy a module in
    by hand with no `.git` in it. Both are still listed — a folder the importer
    will be handed is worth showing — but neither can be asked."""

    behind: int | None
    """Commits the upstream has that this checkout does not. `None` is "could not
    ask": no `.git`, an offline machine, a repository that has gone private.
    Never collapsed into `0` — see `git.BehindReader`."""

    @property
    def line(self) -> str:
        """The row as the Modules tab prints it.

        Here rather than in the view because it is the sentence the figure is
        READ in, and the one thing 8.7a's definition of done is about is that
        this number equals the same range run by hand. A view that formatted it
        itself could round, pluralise or default it without a test noticing.
        """
        if not self.is_checkout:
            return f"{self.key}: not a git checkout — nothing to compare"
        if self.behind is None:
            return f"{self.key}: could not ask (no answer from git)"
        plural = "" if self.behind == 1 else "s"
        return f"{self.key}: {self.behind} commit{plural} behind"


def module_updates(
    server_dir: Path,
    *,
    git: BehindReader,
    branches: Mapping[str, str | None] | None = None,
    kind: ManifestType = "module",
) -> tuple[ModuleUpdate, ...]:
    """How far behind each installed module of `server_dir` is (checklist 8.7a).

    Enumerated from DISK, not from the manifest store, and that is the whole
    point: "installed" means a clone is in `modules/`, so a module a user put
    there by hand is listed and a manifest nobody installed is not. It is the
    same enumeration `docker.allowed_modules()` hands the importer, for the same
    reason — the folder is what the server has, and the catalog is only what it
    could have had.

    `branches` maps a module key to the branch its manifest names, because the
    manifest's branch is what an update would fetch. Every module in the
    `wow-wotlk` catalog omits `source.branch`, so the ordinary answer is `None`
    and the ordinary fetch is `origin HEAD` — which is exactly why a wrong
    branch here would go unnoticed until the first module that names one.

    Costs one `git fetch` per checkout, so it belongs behind a control the user
    pressed. Nothing outside each clone's `.git` is written and no working tree
    is touched.
    """
    root = server_dir / CLONE_DIRS[kind]
    branch_of = branches or {}
    try:
        entries = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)
    except OSError as exc:
        # Not an error and not empty-with-a-shrug: the three CMaNGOS games have
        # no `modules/` at all, and a server dir that cannot be listed is a
        # different problem than one with nothing installed. Logged, either way.
        logger.debug(f"no {CLONE_DIRS[kind]} folder to list under {server_dir}: {exc}")
        return ()
    rows: list[ModuleUpdate] = []
    for path in entries:
        checkout = (path / ".git").is_dir()
        behind = git.commits_behind(path, branch_of.get(path.name)) if checkout else None
        rows.append(ModuleUpdate(key=path.name, path=path, is_checkout=checkout, behind=behind))
    return tuple(rows)


# ---------------------------------------------------------------------------
# What "Apply module SQL" may hand to the core's own updater (T78)


IMPORTER_SQL_ROOT = "data/sql/"
"""The subtree of a module's clone AzerothCore's own updater is pointed at.

`UpdateFetcher.cpp:159-186` joins every allowed module name onto
`<source>/modules/<name>/data/sql/` and skips the ones that are not directories
-- the reading recorded on `docker.ALL_MODULES`, measured live 2026-09-07. It is
the ROOT and not the answer: what the updater opens under it is `IMPORTER_DB_DIRS`
and nothing else.
"""

IMPORTER_DB_DIRS = ("db-auth", "db-characters", "db-world")
"""The directories under `IMPORTER_SQL_ROOT` the core updater actually walks.

**One per database it loads, and that is the whole set.** `DBUpdater` runs once
for each of the three `DatabaseLoader` pools and walks
`<module>/data/sql/<that pool's directory>/` recursively -- `base/` and
`updates/` alike, which is why this is a directory prefix and not a file list.
Evidence in this tree, none of it inferred:

* `catalog/installers/wow-wotlk/native/base.yml.tmpl:163-164` mounts `./modules`
  into the importer *because* "the import applies every module's own
  `data/sql/db-auth` and `db-characters` updates as well as AzerothCore's", and
  `docker.verify_import()` carries the live measurement behind it (yulon-ubuntu
  2026-08-23: a first-ever import of an install carrying mod-city-bots came out
  with 400 accounts and 400 characters, every row from that module's own
  `db-auth`/`db-characters` files). Those two plus `db-world` are the three.
* `data/sql/playerbots/` is NOT one of them, and that is the whole of T78 round
  3. mod-playerbots builds its OWN `DatabaseLoader` for `acore_playerbots` with
  no module list at all, so nothing joins a path under `playerbots/` and no
  press of any button applies a file there -- this app does, at install. The
  2026-09-17 live gate is the measurement: City Bots' `db-auth`, `db-characters`
  and `db-world` files were the updater's to apply, and its one
  `data/sql/playerbots/updates/…citizen_roster.sql` was not.

A direct `.sql` OUTSIDE these directories is therefore never in the updater's
way, and withholding its module -- which is the only granularity
`AC_UPDATES_ALLOWED_MODULES` has -- costs real db-import work for nothing. That
is what it cost live: City Bots' three db-import groups went unapplied because
of its one `playerbots/` file, the world died on `Table 'acore_world.city_bot_poi'
doesn't exist`, and the app's own remedy ("Press Apply module SQL") could not be
followed by pressing the button it named.
"""

IMPORTER_SQL_DIRS = tuple(f"{IMPORTER_SQL_ROOT}{name}/" for name in IMPORTER_DB_DIRS)
"""`IMPORTER_DB_DIRS` as clone-relative prefixes, in manifest spelling (POSIX)."""

_APPLIED_UPDATE = re.compile(r"Applying update\s+[\"']?(\S+\.sql)")
"""The importer's own evidence that it applied a file.

Measured live 2026-09-07 while `acore_world.updates` went 2967 ->
2968: `>> Applying update aoe_loot_module_string.sql`. The quotes are optional in
the pattern because the same image prints its refusals wrapped in ANSI colour
runs (the round-3 gate's importer capture) and this line's exact punctuation is
upstream's to change; the file name is the part that is stable.
"""


@dataclass(frozen=True)
class ModuleSqlFile:
    """One SQL file of one installed module, and which route owns it.

    `route` is the manifest's `applied_by` for the step that names the file, so
    it is a declaration and not a guess: `direct` means THIS app runs the file
    with its own client, `db-import` means the file is left to the core updater.
    A file is evidence of no database row either way -- what a press then did
    with it is the verdict `module_sql_report()` prints.
    """

    module: str
    db: Db
    path: str
    route: Literal["direct", "db-import"]
    when: When = "install"
    installed_on: str = ""
    """The date on this app's claim for the clone, `""` when there is no claim.

    **The date the module was INSTALLED here, and nothing more than that** --
    the name said `applied_on` for one round of review and was wrong twice over.
    `install()` writes the claim at `apply.py:1366` and runs the SQL at
    `apply.py:1386`, so the claim exists for every install that got as far as
    cloning, including one whose `_run_sql()` then raised -- T2 measured exactly
    that, `DockerSql` refusing with *"container ... is not running"*. Nothing in
    this app records that a direct file reached a database; that is the whole of
    T78, and it is why no sentence built from this may say "applied".

    No claim means no date: a clone this app did not make has no install of ours
    to date, and the directory's own mtime is whatever last wrote inside it -- a
    `git pull`, an editor, the compile. It was the fallback for one round of
    review, and printing it as a date this app did something would be inventing
    the record T78 exists because we do not keep.
    """


@dataclass(frozen=True)
class ModuleSqlPlan:
    """Which installed modules the core updater may be given, and why not the rest.

    Built before the importer is started, so the refusal it would otherwise
    produce is a sentence the user reads instead of an exit code.
    """

    allowed: str
    """The value for `AC_UPDATES_ALLOWED_MODULES`.

    `docker.ALL_MODULES` when the folder could not be read or holds no module at
    all -- exactly what `docker.allowed_modules()` answers, so an install this
    plan has nothing to say about behaves as it did before T78. Otherwise the
    comma-joined `handed`, and an empty `handed` with modules on disk is `""`,
    which upstream reads as *"Loading modules: none"*: the third meaning
    recorded on `docker.ALL_MODULES`, and the only one that applies no module
    file at all."""

    handed: tuple[str, ...]
    withheld: tuple[str, ...]
    unmanifested: tuple[str, ...]
    files: tuple[ModuleSqlFile, ...]


def module_sql_plan(
    server_dir: Path,
    manifests: Sequence[Manifest],
    installed: Sequence[str] | None,
) -> ModuleSqlPlan:
    """Split the modules on disk into the ones the updater may have and the ones it may not.

    **The defect (T78, round-3 gate press 9c).** ARAC's
    `data/sql/db-world/arac.sql` is `applied_by="direct"`: this app runs it
    itself, with its own client, and nothing writes a row into the `updates`
    ledger when it does -- upstream's updater has no way to learn that a file it
    can see has already been run. `docker.allowed_modules()` then handed the
    importer every folder under `modules/`, ARAC among them, so the updater
    opened `arac.sql`, ran it against a world database that already held its
    columns, and the press ended `ac-db-import exited 1, so its modules' SQL may
    be part-applied` over a state this app itself had created.

    **What "in the updater's way" means, and it is per FILE (T78 round 3).** The
    first version of this asked only whether a direct file was under
    `data/sql/`, which is where the updater is pointed rather than what it
    walks. City Bots keeps three db-import groups under the three directories
    the updater DOES walk and one direct file under `data/sql/playerbots/`,
    which nothing joins a path for -- and that one file withheld the module
    whole. Measured live 2026-09-17: City Bots installed with "3 left
    unapplied", the rebuild succeeded, the world aborted on `Table
    'acore_world.city_bot_poi' doesn't exist`, the app printed its own correct
    remedy ("Press Apply module SQL on the Modules tab, then Start"), and that
    button withheld `mod-city-bots`; the world restarted 18 times. A direct file
    outside `IMPORTER_DB_DIRS` is now no reason to withhold anything, and a
    direct file inside one still withholds its module -- that is the only lever
    `AC_UPDATES_ALLOWED_MODULES` offers for a file the updater can see, and the
    module's db-import work is the price. `module_sql_report()` then says which
    file cost it.

    **Why this and not a ledger row.** Recording the direct route's files in
    `updates` was the other candidate and is refused on evidence: nothing in this
    repository carries that table's schema -- no fixture, no SQL file, no compose
    file names one of its columns -- the updater re-checks the hash it stored on
    every later run, and its own refusal says in as many words that applying
    repository SQL with your own client and using the auto-update system are not
    to be mixed. A row written from here would be this app guessing at somebody
    else's bookkeeping, and a wrong guess aborts the NEXT legitimate update
    rather than this press.

    `installed` is the folder listing -- `None` for "could not be read", which is
    the one answer that is not a decision: the plan then changes nothing and the
    press behaves exactly as it did before, which is what
    `docker.allowed_modules()` does with the same `None`.

    A module on disk that no manifest describes is handed over: this app has no
    record of applying anything of its own, the updater is its only route, and
    withholding it would silently stop applying SQL that has always been applied.
    """
    if installed is None:
        return ModuleSqlPlan(
            allowed=docker.ALL_MODULES, handed=(), withheld=(), unmanifested=(), files=()
        )
    by_id = {manifest.id: manifest for manifest in manifests if manifest.type == "module"}
    handed: list[str] = []
    withheld: list[str] = []
    unmanifested: list[str] = []
    files: list[ModuleSqlFile] = []
    for name in installed:
        manifest = by_id.get(name)
        if manifest is None:
            unmanifested.append(name)
            handed.append(name)
            continue
        clone = server_dir / CLONE_DIRS["module"] / name
        installed_on = _installed_on(clone)
        conflicting = False
        for step in manifest.sql:
            if step.path is None:
                continue  # an inline statement is in no file the updater could open
            for found in _sql_files(clone, step.path) or (step.path,):
                files.append(
                    ModuleSqlFile(
                        module=name,
                        db=step.db,
                        path=found,
                        route=step.applied_by,
                        when=step.when,
                        installed_on=installed_on if step.applied_by == "direct" else "",
                    )
                )
                # Decided per RESOLVED FILE and then raised to the module,
                # because the module is the only granularity the updater has.
                # `_blocking_files()` re-asks the same question of the same
                # strings when the report has to name them, so the sentence
                # cannot name a file the decision was not made on.
                if step.applied_by == "direct" and _the_updater_would_find_it(found):
                    conflicting = True
        (withheld if conflicting else handed).append(name)
    return ModuleSqlPlan(
        allowed=",".join(handed) if installed else docker.ALL_MODULES,
        handed=tuple(handed),
        withheld=tuple(withheld),
        unmanifested=tuple(unmanifested),
        files=tuple(files),
    )


def _the_updater_would_find_it(path: str) -> bool:
    """Would the core updater open this clone-relative file if it were given the module?

    Asked of ONE FILE, and the caller supplies the other half (`applied_by ==
    "direct"`): a db-import file under the same prefix is the updater's job and
    is no reason to withhold anything.

    `IMPORTER_SQL_DIRS`, not `IMPORTER_SQL_ROOT`, and the difference is T78 round
    3. `data/sql/` is where the updater is POINTED; the three directories under
    it are what it walks. A direct file anywhere else under `data/sql/` --
    City Bots' `data/sql/playerbots/updates/2026_07_15_00_citizen_roster.sql` is
    the shipped one -- is as invisible to it as Battle Pass's `sql/`, so
    withholding a module over it takes that module's REAL db-import work down
    with it and leaves the world unable to start. See `IMPORTER_DB_DIRS`.

    `.sql`, because the updater applies nothing else: `npc-teleporter` keeps two
    `data/sql/db-world/*.dist` files, which sit inside a walked directory and
    are not update files.

    The step's `when` is deliberately not read by the caller. A `remove` step's
    file is one this app runs itself too, and a Down script applied by the
    updater on an install that still wants the module is a worse outcome than a
    skipped update.
    """
    return path.endswith(".sql") and path.startswith(IMPORTER_SQL_DIRS)


def _blocking_files(plan: ModuleSqlPlan, module: str) -> tuple[str, ...]:
    """The files of `module` that are the reason it was withheld, in plan order.

    Derived from the plan rather than carried beside it, and asked with the same
    predicate `module_sql_plan()` decided with, over the same resolved strings.
    A second field would be a second answer to "which file cost this module its
    updates", and the sentence could then name a file the decision was not made
    on -- which is exactly the shape of the bug being fixed, one level up.
    """
    return tuple(
        entry.path
        for entry in plan.files
        if entry.module == module
        and entry.route == "direct"
        and _the_updater_would_find_it(entry.path)
    )


def _and_list(items: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c` -- for a sentence a person reads, not a log grep."""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _installed_on(clone: Path) -> str:
    """The date on this app's own claim for `clone`, or `""` when there is none.

    The claim and nothing else. A directory mtime was the fallback for one round
    of review and is not a date this app did anything: it moves when a `git
    pull`, an editor or a compile writes inside the checkout, and for a clone
    this app never made it would put OUR name on a stranger's timestamp. No
    claim is no record, and the sentence then carries no date at all.
    """
    try:
        return date.fromtimestamp((clone / CLAIM_FILE).stat().st_mtime).isoformat()
    except (OSError, ValueError, OverflowError):
        return ""


def applied_updates(lines: Sequence[str]) -> frozenset[str]:
    """The file names the importer said it applied, as base names.

    Base names, because that is what upstream prints and what `UpdateFetcher`
    keys its ledger on -- not the clone-relative path this app tracks a step by.
    Two modules shipping a file of the same name would therefore both read as
    applied; that is the direction that cannot invent an application which did
    not happen.
    """
    found: set[str] = set()
    for line in lines:
        match = _APPLIED_UPDATE.search(line)
        if match:
            found.add(PurePosixPath(match.group(1)).name)
    return frozenset(found)


def module_sql_report(
    plan: ModuleSqlPlan,
    *,
    service: str,
    applied: frozenset[str],
    refusal: str = "",
) -> tuple[str, ...]:
    """One sentence per SQL file: what this press did with it, and what it did not.

    The report T78 owes. Three verdicts were asked for and there are five, and
    the two that were added are the two that would otherwise have been lies:

    * a db-import file the importer did not name is not "applied" (nothing ran)
      and not "refused" (nothing complained) -- it is a file already in the
      updater's own ledger, which is what every press after the first looks
      like;
    * a db-import file inside a WITHHELD module was not offered to the updater
      at all, so no verdict of this run belongs to it.

    **The withheld module's sentence names the file that cost it.** It used to
    say "this app runs that folder's SQL itself", which is true of ARAC -- whose
    one SQL step IS the direct one -- and false of every module with more than
    one. The 2026-09-17 gate read that sentence over `mod-city-bots`, where the
    app runs one of four SQL groups; saying which file is what makes it true of
    a mixed module, and it is the sentence a user needs to act on.

    **Nothing here says a direct file was applied**, and that is the whole
    discipline of this function rather than a nicety. This app keeps no record
    that a direct file reached a database -- exactly the gap T78 is about -- so
    the only honest report of one is which route owns it, plus the date of this
    app's own claim where there is one. See `ModuleSqlFile.installed_on`.

    `refusal` is the updater's words, empty when it exited 0. It is printed
    against every db-import file the run did not name, because an importer that
    stopped part-way cannot say which of the files it had not reached it would
    have applied -- and naming them all is the only reading that does not
    promise one of them was fine.
    """
    lines: list[str] = []
    for name in plan.withheld:
        blocking = _blocking_files(plan, name)
        what = _and_list(blocking) if blocking else "that folder's SQL"
        lines.append(
            f"{name}: not given to {service} -- this app runs {what} itself, and "
            f"the updater refuses a file it holds no ledger row for."
        )
    for name in plan.unmanifested:
        lines.append(
            f"{name}: given to {service} -- this app has no manifest for it and runs none of "
            f"its SQL, so the updater is its only route."
        )
    for entry in plan.files:
        where = f"sql {entry.path} -> {entry.db}"
        if entry.route == "direct":
            # NEVER "applied". This app keeps no record that a direct file
            # reached a database -- that absence IS T78 -- and the claim it does
            # keep is written before the SQL runs (`install()`, apply.py:1366
            # ahead of :1386), so an install whose `_run_sql()` raised leaves a
            # clone and a claim behind with nothing in the world database. What
            # is known is the route and, if this app made the clone, the date it
            # installed the module; the sentence says exactly those two.
            when = "at install" if entry.when == "install" else f"on {entry.when}"
            on = f" (module installed here on {entry.installed_on})" if entry.installed_on else ""
            lines.append(
                f"{where}: not handed to the updater: this app applies it itself {when}{on}"
            )
        elif entry.module in plan.withheld:
            # The module went nowhere, so neither did this file. Reporting it as
            # already-ledgered would be the T78 defect upside down: a promise
            # that a file is in the database because nothing complained about a
            # run it was never part of.
            lines.append(
                f"{where}: not applied: {entry.module} was not given to {service}, so the "
                f"updater was not offered this file either"
            )
        elif PurePosixPath(entry.path).name in applied:
            lines.append(f"{where}: applied")
        elif refusal:
            lines.append(f"{where}: refused: {refusal}")
        else:
            lines.append(
                f"{where}: not applied now: {service} did not name it, which is what a file "
                f"already in its updates ledger looks like"
            )
    return tuple(lines)
