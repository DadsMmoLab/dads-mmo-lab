"""The install spine: named, resumable stages, game-free (roadmap 6.2 → 7.1).

One typed engine for every server on every platform. `StagedInstaller` owns
what is true of every install — the state file and its hint semantics, the
directory/ownership guard, preflight and Docker provisioning, the
refuse-not-delete clone safety, the compose marker rules, streaming, cancel
copy, keep-awake — and a FAMILY (`families/azerothcore.py`, `families/cmangos.py`)
composes its stages into an ordered tuple. `installer.installer_for()` picks
the family from `catalog.json`'s `install.native.family`. Every family engine
has the same contract (`run(options, *, cancel, ask) -> Iterator[str]`), so the
catalog view, the log panel and the job runner need no changes.

**Staged and resumable.** The stages are recorded by NAME in
`.yulon-install.json`, so reordering them can never re-interpret an existing
install. The rules below each cost the earlier Rust launcher an evening
(`pyplan/rust-prior-art.md` §1), and they are the reason this file is more
careful than its length suggests:

* preflight and the guard are not stages: the spine runs them itself, so a
  family can neither forget nor record them — a guard a resume skips is not a
  guard. A `Stage` with `recorded=False` (`start-db`, `up`, `ready`) is run by
  every resume: an install must end by actually starting and verifying the
  server, and the database must be back up before the import can ask it
  anything.
* **The state file is a hint, and disk evidence answers in both directions.**
  Every stage re-checks disk evidence before skipping: the clone stages ask git
  for the remote, the build asks the daemon for images, compose generation
  reads its own marker. An `is_done` short-circuit once let a state file
  dropped into a directory make the generator rewrite a real server's compose
  file and orphan its character volumes. The converse is just as load-bearing
  and was missing until 7.1: a stage that is recorded AND corroborated must be
  a genuine no-op, because "re-run it to be sure" is destructive for a clone —
  see `StagedInstaller.already_cloned()`.
* `install_id` is a hash of the ABSOLUTE server directory, so a COPIED install
  directory is refused rather than adopted; `family` is recorded too, so a
  catalog edit that moves a game between families is a refusal, never a
  reinterpretation.
* A failure mid-stage records nothing, so the stage re-runs.
* A stage's cancel note is said by the spine, right after `--- <name>`, and
  by nothing else.

**Nothing on this path prompts for its own decisions.** Exactly two questions
pass THROUGH it, both inside Docker provisioning before stage 1 and both via
the forwarded `ask`: the docker-group consent and, on Linux, the sudo password.
A stage that turns out to need an answer is a design failure to fix rather
than a dialog to add.

**Verified where, exactly.** Everything here is unit-tested against seams;
docstrings say which claims are inherited from the Rust launcher's incidents,
which are measured on yulon-ubuntu (Linux), and which are merely written.
"""

from __future__ import annotations

import io
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Collection, Generator, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from secrets import token_hex
from typing import ClassVar, Protocol

from yulon import dbsecret, docker, git, networking, platform, resources, runner
from yulon.catalog import composegen, preflight
from yulon.catalog.catalog import (
    CatalogEntry,
    EmulatorSource,
    NativeInstall,
    ReadyMarkers,
    SqlPhase,
    SqlPlan,
)
from yulon.catalog.installer import (
    DockerUnavailableError,
    InstallerError,
    InstallOptions,
    UnsupportedPlatformError,
    docker_unavailable,
    generated_compose_files,
    provision_lines,
    unsupported_platform_message,
)
from yulon.log import get_logger
from yulon.ownership import Ownership as Ownership

logger = get_logger(__name__)

STATE_FILE = ".yulon-install.json"
STATE_VERSION = 1

OUR_OWN_FILES = (STATE_FILE, networking.INTENT_FILE)
"""Every file this app writes into a server directory as its OWN bookkeeping.

The set `_listing()` is asked to look past when the question is "is this folder
somebody else's?". Anything not in it is a user's file and a reason to refuse.

It was one name until 2026-09-05, and adding the second was not a tidy-up.
`networking.apply()` writes `.yulon-network.json` into the server directory when
a mode is applied from the Networking tab, and with `STATE_FILE` alone in the
list, a folder holding only that file answered "not empty and was not created by
this app" — measured that day as `InstallerError: ... is not empty and was not
created by this app (.yulon-network.json)` from
`test_spine.py::test_a_loopback_the_owner_chose_is_left_alone_and_the_line_says_why`,
which is a folder this app had written every byte of being refused by its own
guard. A tuple with a name, so the next file this app learns to write is added
in one place rather than in the five call sites that ask the question.
"""

OPENING_NOTE = (
    "You can stop this at any time. What an install writes outside the folder below is named "
    "here rather than denied: the images this build produces and the volumes holding the "
    "database and the server data, which live in Docker's own storage — that is why the checks "
    "below look at the space on Docker's disk too; Docker itself, when no daemon answers yet "
    "(on Linux: system packages, a service, and the group you are asked about first); and this "
    "app's own settings and log, in its config folder. Starting the install again continues "
    "from where it stopped: source this app has finished cloning is never fetched, reset or "
    "moved, and the build is not run a second time once Docker confirms its images are there. "
    "Everything cheap runs again on every attempt — the compose files this app writes are put "
    "back the way the catalog says, the server-data download resumes and re-checks itself, and "
    "the database and server are started and waited for."
)
"""What a Stop costs, said before it is pressed, and true of every stage.

The sentence this replaced was about the build (see `BUILD_CANCEL_NOTE`) and
was said as the second line of every install and appended to every
cancellation, so a user who stopped during the clone or the download was told
Docker was finishing a build step (review, 2026-08-23).

**This sentence has been rewritten until every clause names something a stage
is responsible for keeping, because each version claimed more than the engine
does.** It said "only the step that was interrupted runs again" while the live
Ubuntu gate (2026-08-30) printed `Already finished: clone-core, clone-modules,
generate-compose` and ran all three, each doing `git fetch` + `git reset --hard
FETCH_HEAD` over the user's source. It said the source was "left exactly as it
is on disk", which is false of `docker-compose.yml` — a TRACKED file of the
emulator checkout, carrying this engine's marker after the first install, so
`composegen.write_plan()` puts it back whenever it differs. It said only the
last steps run every time, while `start-db` sits mid-list with `recorded=False`
and `client-data` consults no state at all. And it opened with "nothing is
written outside the folder below", three lines above `Docker setup did:
apt-get update; apt-get install -y docker.io; systemctl enable --now docker;
usermod -aG docker pk` — a reassurance printed directly above the actions
contradicting it, which is the exact shape of the sentence removed from the
clone stage (review, 2026-08-31).

So each clause is now a claim something is responsible for keeping:

* *what goes outside this folder* — the base template's two NAMED volumes
  (`db-data`, `client-data`) plus the built images, which is why
  `preflight.evaluate()` has a check about Docker's data root and not only
  about this drive; `platform.ensure_docker()` for the second item, whose own
  `done` steps are printed a few lines below this one; and `config_dir()` for
  the third;
* *finished cloning is never fetched, reset or moved* — `already_cloned()`, and
  `refuse_unowned_checkout()` for the checkout that was never this app's;
* *the build is not run a second time once Docker confirms its images* —
  `stage_build()`, where an unknown answer rebuilds rather than skips;
* *the compose files this app writes are put back* — said, not hidden, because
  `write_plan()` really does overwrite an edit to a file it wrote;
* *the download resumes and re-checks itself* — the generated entrypoint's
  `curl --continue-at -` plus its data-version comparison;
* *the database and server are started and waited for* — the three
  `recorded=False` stages, which is why a resume always ends with a live server.
"""

ROLLBACK_TAG_SUFFIX = "-rollback"
"""What the build a rebuild is about to overwrite is tagged as, while it runs.

Owner answer 2 (2026-09-08): *always keep a rollback, and restore it
automatically if the world does not come up.* `docker compose build` writes the
new image over the tag the running containers were created from, so the old
build is gone the moment the compile finishes unless it has a second name
first. This is that name: `<ref>-rollback`, one per image in
`composegen.built_image_refs()`. It is the recipe that brought m910q's Tortoise
back on the night of 2026-09-08 -- `docker tag` before, retag and recreate after
-- done by hand then and by `StagedInstaller.rebuild()` now.

A suffix on the TAG rather than a second repository name, so `docker images`
lists the pair side by side and a purge that enumerates the install's refs can
find the leftover by the same rule.
"""

FAILED_TAG_SUFFIX = "-failed"
"""What the NEW build is named while the old one is being put back over its tags.

Added on the adversarial review of 2026-09-08. The restore moves one tag at a
time, and a `docker tag` that fails on the second leaves the first ref on the
old build and the rest on the new -- a server nobody has run, reported as if
it were one thing. Giving the new build its own name BEFORE any tag moves is
what makes a failure part-way undoable: the refs already moved are moved back
onto this name, and "the tags still name the new build" is then true of all of
them. Removed once the restore has settled either way.

**Letting it go takes two attempts, and that is measured rather than defensive.**
The first runs the moment the tags are back, while the containers made FROM the
new build are still there, and docker refuses to remove a name whose image a
container references -- so on `yulon-ubuntu2`, 2026-09-09, exactly the two
long-running services came back `conflict: unable to delete ... (must be forced)
- container 31769acad1c4 is using its referenced image` while the two one-shots
(whose containers were not recreated at all) were removed and their images
deleted. Half a broken build kept for ever under a name documented as transient
is not a state anybody chose, so `_restore_rollback` asks again after its own
recreate, which is the thing that frees them.
"""

DOCKERFILE_STAGE = "write-dockerfile"
"""The stage that renders this install's build recipe from the app's own templates.

TWO files, and the name says one: `_write_dockerfile()` renders `Dockerfile`
and `.dockerignore` through a single `dockerfile.write()` that rewrites whichever
differs and refuses either that carries no marker. Both user sentences say
"build recipe" and name the pair, because a `.dockerignore` behind its template
is rewritten by this press exactly as the Dockerfile is, and a dialog promising
"nothing else in the folder" was wrong about the second one (Fable, round 1).

Named here because two things select on it: `rebuild_stages()`, which runs it
again ahead of every compile for the families that have one, and
`rebuild_opening_note()`, which counts what the press is about to do. A family
whose checkout ships its own Dockerfile (AzerothCore) does not have it, and the
`dockerfile_dir` field's own description is where that is written down.
"""


class _UnreadableRecipe:
    """The third thing a build-recipe file can be: there, and not readable by this process."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNREADABLE_RECIPE"


UNREADABLE_RECIPE = _UnreadableRecipe()
"""`_recipe_ground()`'s "could not ask", kept apart from its "was not there".

A singleton and not `None`, because the two answers lead to opposite acts:
absent means the re-render created the file and the restore removes it, while
unreadable means this process never saw the bytes and may not remove anything.
Collapsing them deleted a user's `Dockerfile` and said it had been "put back
exactly as it was" (cold review of T8, round 2). `_keep_rollback()` takes the
same line about `images_built()` answering `None`: destructive work on an
unanswered question fails closed.
"""

RecipeGround = bytes | None | _UnreadableRecipe
"""What `_recipe_ground()` knows about one file: its bytes, absent, or unreadable."""


def rebuild_opening_note(*, renders_dockerfile: bool) -> str:
    """What a rebuild costs and what it leaves alone, said before the first stage.

    `OPENING_NOTE`'s counterpart, and a separate sentence rather than a reuse
    because almost none of that one is true here: a rebuild clones nothing,
    generates no compose files, downloads nothing and runs no import. Its own
    docstring records what it cost to have one sentence claim more than the
    stages keep, so the rule is the same — every clause names something
    `rebuild_stages()` is responsible for, and the list is exactly as long as
    that tuple precisely so it stays checkable.

    It is a function and not a constant because the tuple stopped being one
    length on 2026-09-09: the families that render their own Dockerfile now
    write it again from the current template before compiling (T8), and the
    families whose checkout ships one do not. A single constant would have had
    to claim the re-render for WotLK, where nothing does it, or hide it from
    the three CMaNGOS games, where it is the point of the press — and "this
    does three things and nothing else" is the kind of clause that goes quietly
    false rather than red.

    The downtime clause is the one a user is most likely to be surprised by and
    is the reason it is stated twice: here, and in `rebuild_confirmation()`
    before they agree to it.

    **"stopping ... leaves the server you have now exactly as it is" is a
    promise the re-render nearly broke**, and it is kept by code rather than by
    narrowing the words: the stage rewrites two files in the folder before the
    compile, so `rebuild()` reads them first and `_put_recipe_back()` puts them
    back on every failure and every cancel that lands before the containers are
    replaced. Narrowing the sentence to "the containers stay as they are" was
    the alternative and is worse: a user who stops a press would be left with a
    build recipe they never agreed to, and the next compile — which may be
    somebody else's press, hours later — would silently produce a different
    image. A promise this app can keep is cheaper than a caveat every reader
    has to hold.
    """
    recipe = (
        "it writes this install's build recipe — its Dockerfile and .dockerignore — again "
        "from the templates this app ships, so a fix made to them since you installed is in "
        "what gets compiled, "
        if renders_dockerfile
        else ""
    )
    return (
        "You can stop this at any time; stopping before the containers are replaced leaves the "
        f"server you have now exactly as it is. This does {'four' if recipe else 'three'} things "
        f"and nothing else: {recipe}it "
        "compiles the server again from the source and modules in the folder below, it replaces "
        "the running containers so the new build is what starts, and it waits for the server to "
        "come back up. It does not fetch anything, does not rewrite your settings, and does not "
        "touch your database — your characters, accounts and the module SQL already applied are "
        "not read or written by this. The server is DOWN from the moment the containers are "
        "replaced until it reports ready."
    )


UPDATES_BUTTON_LABEL = "Apply pending database updates…"
"""The updates control's label, here rather than in the view because the ENGINE says it.

`REBUILD_BUTTON_LABEL` lives in `controller_view.py` and nothing below the view
quotes it. This one is quoted by a refusal `update_databases()` raises — *"Press
Stop, then press … again"* — and a refusal that names a button which does not
exist under that name is exactly the defect T7's ticket is titled after: an
instruction the user cannot follow. One string, so a rename moves both.
"""

UPDATES_OPENING_NOTE = (
    "You can stop this at any time. This does two things and nothing else: it starts this "
    "install's database on its own if it is down, and it applies the parts of the install plan "
    "that are meant to be re-applied to a server that already exists. It does not start the "
    "world server, does not compile anything, does not fetch anything, and does not re-run the "
    "rest of the install — your databases keep the completion marker they already have and no "
    "new one is written. If they do not read as a finished import, this stops and says so "
    "rather than importing them."
)
"""What an updates press costs and what it leaves alone, said before the first stage.

`rebuild_opening_note()`'s counterpart, under the same rule: every clause names
something `update_stages()` is responsible for, and the list is exactly as long
as that tuple so it stays checkable. A constant and not a function because that
tuple is one length for every family that has the stages at all.

**The last sentence is what makes the two before it true**, and it was added in
round 2. "Does not re-run the rest of the install" and "no new one is written"
were claims about which arm of `import`'s five-branch table the press takes, and
until `updates_only` existed the press did not choose that arm — `absent`
imported everything and marked it, `partial` dropped every schema the plan names
first. The rule this file keeps for opening notes is that a clause names
something the tuple is responsible for; a clause that names what the tuple
REFUSES belongs here for the same reason, because it is the only thing standing
between these words and a press that contradicts them.
"""


ADOPT_BUTTON_LABEL = "Adopt as imported…"
"""The adopt control's label, here for `UPDATES_BUTTON_LABEL`'s reason.

Quoted by the refusals `adopt_as_imported()` raises — *"press Stop on the
Server tab, then press … again"* — and a refusal naming a button that does not
exist under that name is the defect T7's ticket is titled after. One string, so
a rename moves both.
"""

ADOPT_CONSEQUENCE = (
    "Yu'lon will treat these databases as a finished import from now on. It cannot check that "
    "the import finished; you are saying so. If it did not, the next press of Apply pending "
    "database updates will run the flagged files on an unfinished database."
)
"""What adopting COSTS, in the owner's own words, said in the confirmation.

Verbatim from the ticket's spec and not paraphrased, and it is the sentence the
whole feature turns on: three rounds of an Opus and a Fable hand tried to
DERIVE "this import finished" from the plan — per-schema table counts, the
plan's own `verify` rules, then the table set parsed out of every dump file —
and each round's reviewer found the next layer of inference underneath. The
owner's answer was to stop inferring. So this press claims nothing, and this
sentence is where the claim moves from the app to the person making it.

It names *Apply pending database updates* without `UPDATES_BUTTON_LABEL`'s
trailing ellipsis, which is how the owner wrote it: the ellipsis is a
convention about dialogs, and a sentence quoting it mid-clause reads as a
trailing-off rather than as a name.
"""

ADOPT_OPENING_NOTE = (
    "You can stop this at any time. This does three things and nothing else: it starts this "
    "install's database on its own if it is down, it writes one row saying this install plan "
    "finished, and it puts the database back down again if this press was what started it. It "
    "imports nothing, drops nothing, streams no file, creates no user, and never starts the "
    "world server. If these databases already carry that row, or do not hold the schemas and "
    "tables this plan names, this stops and says so rather than writing it."
)
"""What an adopt press costs and what it leaves alone, said before the first stage.

`UPDATES_OPENING_NOTE`'s counterpart under the same rule: every clause names
something `adopt_stages()` is responsible for, and the last sentence names what
the press REFUSES — which belongs here for the reason that note's does, because
it is the only thing standing between these words and a press that contradicts
them.
"""


@dataclass(frozen=True)
class MarkerRow:
    """The one row an adopt press writes, spelled the way the writer spells it.

    Carried out of the family rather than read inside the confirmation, because
    `native.py` cannot import `families.sqlplan` — that module imports THIS one
    (`IMPORT_CANCEL_NOTE`), and the cycle is real. So the family, which holds
    the plan and the marker table's name, hands the four facts up and the
    dialog's words are written once, here, where they can be asserted without
    Qt and without a database.

    `plan_hash` is `SqlPlan.plan_hash()` — the same string `sqlplan.write_marker()`
    puts in the row — and not a hash this dialog computes. A confirmation naming
    a hash the write does not use would be a promise about a different row.
    """

    schema: str
    """The database the row goes into: the plan's `marker_db`, as the writer spells it."""
    table: str
    """`sqlplan.MARKER_TABLE`, created by the writer if it is not there."""
    plan_hash: str
    """This plan's hash, exactly as the import would have written it."""
    databases: tuple[str, ...]
    """Every schema this plan names, for the dialog to list."""


@dataclass(frozen=True)
class AdoptRoute:
    """The three parts of an adopt control, wired together so they cannot arrive apart.

    `UpdateRoute`'s shape with one more field, and the extra field is the whole
    difference: this control is offered on a READING of the databases, not on a
    fact about the catalog. `state` is that reading, and it is a callable the
    tab asks at a moment of its own choosing rather than a value computed when
    the tab is built — the probe is `docker exec … mariadb` several times over
    and a tab that took it at build time would pay for it on every install the
    app opens, most of which will never press this.

    A tab holding a `confirmation` with no `press` describes a press it cannot
    make; one holding a `press` with no `state` offers to write a marker row
    without having asked the databases anything. `None` for the whole thing is
    the only other legal state and it greys the control.
    """

    state: Callable[[], docker.ImportState]
    """What the databases read as, for the enabling rule. Never raises: everything
    that could not be asked comes back as `unreadable`, which greys the button."""
    confirmation: Callable[[], str]
    """The dialog's text for this install. Pure — no database is asked to compose it."""
    press: Callable[[threading.Event | None], Iterator[str]]
    """Cancel in, lines out: `RebuildSource`'s shape, for the same panel."""


@dataclass(frozen=True)
class UpdateRoute:
    """The two halves of an updates control, wired together so they cannot arrive apart.

    One field on `ControllerServices` rather than two optional callables, and
    the pairing is the reason: a tab holding a `confirmation` with no `apply`
    describes a press it cannot make, and one holding an `apply` with no
    `confirmation` writes DDL into somebody's character database behind a dialog
    nobody wrote. `None` for the whole thing is the only other legal state, and
    it greys the control.

    Both are built lazily over an engine constructed on the call, never at tab
    build time — `install_wiring.rebuild_for_app()` holds that argument, and it
    is the same one: four seams and an import gate for every tab the app opens,
    for a control most of them will never press.
    """

    confirmation: Callable[[], str]
    """The dialog's text for this install. Raises `InstallerError` if the plan
    cannot be expanded against the folder — a clone that predates the directory
    the phases name — in which case there is no press to offer."""
    press: Callable[[threading.Event | None], Iterator[str]]
    """Cancel in, lines out: `RebuildSource`'s shape, for the same panel.

    Not spelled `apply`: `test_every_seam_for_wotlk_builds_says_which_daemon_it_means`
    collects every name in the package that takes a `wsl_distro` keyword and
    reports any call to one from this view without it — and `sqlplan.apply()`
    is such a name, so `route.apply(cancel)` read as a seam addressing the wrong
    daemon. The audit is right to be spelling-based (a renamed helper stays
    covered), so the field is what moved.
    """


def import_reads_as_finished(state: docker.ImportState) -> bool:
    """Do these databases read as an import that COMPLETED? The one predicate.

    The two answers `cmangos._import` treats as finished: a marker row of any
    hash (`imported`), and `populated` with every schema carrying tables — the
    second because an install made by the shell scripts has no marker row at all
    and would otherwise be as exposed as before T11.

    Written once because it is now asked from two places that must not disagree.
    `_import` asks it to decide whether the ordinary import runs, and the
    updates press asks it as a PRECONDITION — and if the precondition were even
    slightly wider than the branch, a press that consented to a handful of files
    would fall through into `stage_import()`'s table, whose `partial` arm drops
    every schema the plan names and whose `absent` arm runs the whole import and
    writes a completion marker (cold review of T14, round 1).
    """
    return state.state == "imported" or (state.state == "populated" and state.complete)


def rerunnable_phases(plan: SqlPlan) -> tuple[SqlPhase, ...]:
    """The phases of one plan that an install already read as finished still applies.

    The ONE filter. `CmangosInstaller._rerun_on_marked()` decides what a press
    runs and `update_phases()` decides whether the control is offered at all,
    and written twice those two could disagree in the direction that costs: a
    button offered for a phase the route then skips reports success and applies
    nothing. `SqlPhase.rerun_on_marked`'s own description is where the rule is
    argued; this is where it is spelled.
    """
    return tuple(phase for phase in plan.phases if phase.rerun_on_marked)


def update_phases(entry: CatalogEntry) -> tuple[SqlPhase, ...]:
    """What a database-updates press would apply to an install of `entry`, in plan order.

    Empty means the control is not offered — read off the catalog and never off
    an id. Today exactly one entry answers non-empty (`wow-tortoise`'s
    `character updates`), and a test enumerates the whole catalog so that stays
    a fact about the data rather than a name in an `if`.

    Empty for AzerothCore for a reason that is not "no phase is flagged there":
    that family imports through a compose one-shot and carries no phase list in
    the catalog at all, so there is nothing for a phase flag to sit on. The
    `cmangos` block is therefore read directly rather than through a family
    engine — the same shape `CatalogEntry._every_patch_names_a_source_this_entry_clones`
    uses, and it keeps this readable without constructing an engine for every
    tab the app opens (`install_wiring.rebuild_for_app()` holds that argument).
    """
    native_block = entry.install.native
    block = native_block.cmangos if native_block is not None else None
    if block is None:
        return ()
    return rerunnable_phases(block.sql)


def updates_confirmation(
    entry: CatalogEntry,
    server_dir: Path,
    phases: Sequence[str],
    files: Sequence[str],
) -> str:
    """What the user agrees to before an updates press: the phases, the files, the two fears.

    Authored here and not in the view, for `rebuild_confirmation()`'s reason:
    it has assertions on it that run without Qt.

    **The file list is expanded from the folder, never from the catalog's
    glob.** A dialog reading `sql/character_updates/*.sql` while the press
    streams three named files is a promise about a pattern; the caller hands
    this the same `expand()` output the run will stream, so the two cannot
    disagree about which files exist or about the order they go in.

    Two facts a user cannot see and would be right to fear, and both are stated
    rather than fixed — this control puts a button on the route T11 built and
    changes nothing about what that route applies:

    * **A file can be refused, and the press stops on it.** Every flagged phase
      ships `on_error: fail`, and the case that is known to be reachable is
      `MODIFY money INT(10) UNSIGNED` against a `guild_bank_money.money` that
      has gone negative: MySQL's strict mode will not narrow it, and the client's
      own last line names the file (T11's reviewer, note 2).
    * **The marker does not move.** A marker row says the whole PLAN finished,
      and one written after two of its phases would tell every later press
      something it can never take back. So no marker is written here and no
      `verify` rule is re-asked; the row already there goes on reading
      `imported`.

    A clone that predates the directory these phases name does not reach this
    function at all: `expand()` refuses first, in `sqlplan._matches()`'s own
    words, which name the pattern and the folder and say the sources may not
    have cloned completely (note 4). That sentence is not wrapped in a second
    one here — it is already the sentence a user reads.
    """
    named = ", ".join(phases)
    listing = "\n".join(f"    {name}" for name in files)
    return (
        f"Apply the database updates {entry.name}'s install plan carries for a server that "
        f"already exists?\n\n"
        f"Folder: {server_dir}\n\n"
        f"This applies {len(files)} SQL step(s), the whole of {named}, into this install's "
        f"databases:\n\n"
        f"{listing}\n\n"
        f"Nothing else in the install plan is re-run. Your databases keep the completion marker "
        f"they already have — this press writes no new one — and your characters, accounts and "
        f"world are not otherwise written, and read only to learn which state they are in. If "
        f"they do not read as a finished import this "
        f"press stops and says so: it will not import them, and it will not clear anything.\n\n"
        f"The server must be STOPPED first — press Stop on the Server tab, and leave it down "
        f"until this has finished. A running world server holds these tables in memory "
        f"and writes back over whatever it finds in them, so this press refuses while it is up. "
        f"The database alone is started if it is down; the world server is never started by "
        f"this.\n\n"
        f"If a file cannot be applied the press stops on it and says which file and why — "
        f"nothing after it runs. The one refusal known to be reachable is a guild bank balance "
        f"that has gone negative, which the column type these files set cannot hold."
    )


def adopt_confirmation(entry: CatalogEntry, server_dir: Path, row: MarkerRow) -> str:
    """What the user agrees to before an adopt press: the folder, the databases, the row, the cost.

    Authored here and not in the view, for `updates_confirmation()`'s reason:
    it has assertions on it that run without Qt.

    **The row is named exactly as it will be written**, down to the schema, the
    table and the plan hash, because that is the whole of what this press does
    and a dialog that said "records the import as finished" would be describing
    an effect rather than an act. `MarkerRow` comes out of the family that owns
    the writer, so the two cannot disagree about which row this is.

    **`ADOPT_CONSEQUENCE` is the last thing said before the buttons**, and it is
    quoted rather than reworded. It is the only sentence in this dialog that is
    about the person rather than about the app: the app cannot check that the
    import finished, and pressing Yes is the person saying it did. Every other
    control in this package refuses on a reading it took itself; this one is the
    one place a reading is replaced by a consent, and the sentence says so in
    those words.

    The stopped-world clause is here as well as in the refusal for
    `updates_confirmation()`'s reason: a user who meets the refusal has already
    paid for the dialog.
    """
    listing = ", ".join(row.databases)
    return (
        f"Adopt {entry.name}'s databases as a finished import?\n\n"
        f"Folder: {server_dir}\n\n"
        f"These databases: {listing}\n\n"
        f"This writes ONE row and nothing else. Into `{row.schema}`.`{row.table}` — the table "
        f"this app's own import creates and writes at the end of a successful one, created here "
        f"if it is not already there — goes a row recording that this install plan "
        f"({row.plan_hash}) finished. Nothing is imported, nothing is dropped, no user is "
        f"created, no SQL file is streamed, and your characters, accounts and world are read "
        f"only to learn which state they are in.\n\n"
        f"{ADOPT_CONSEQUENCE}\n\n"
        f"The server must be STOPPED first — press Stop on the Server tab, and leave it down "
        f"until this has finished. A running world server holds these tables in memory and "
        f"writes back over whatever it finds in them, so this press refuses while it is up. The "
        f"database alone is started if it is down, and stopped again afterwards if this press "
        f"was what started it; the world server is never started by this."
    )


REBUILD_CLOSING_NOTE = (
    "The server is running the build that was just made, with every module in this folder "
    "compiled into it. This rebuild ran no SQL: if a module you added still does not seem to "
    "be there in-game, check the worldserver log for its own startup line and for database "
    "updates it applied — that part is the server's own doing, not this button's."
)
"""The last thing said before the closing line, and the honest half of it.

The reported behaviour is that AzerothCore's updater applies a module's SQL for
the modules in `AC_MODULES_LIST`, so a module compiled in by this rebuild is
covered from this build onward. NOTHING in this repository measures that, and
this sentence therefore does not claim it: it says what the rebuild did (the
binary), says what it did not do (any SQL), and points at the one place that
can answer the rest. A closing line that promised the module was "fully
installed" would be repeating, one layer up, the mistake this whole feature
exists to fix — telling a user something took effect when nobody checked.
"""

BUILD_CANCEL_NOTE = (
    "Stopping now leaves Docker finishing the build step it is already on, in the background. "
    "That is deliberate: the work it has done is kept, and starting this install again picks up "
    "from there instead of compiling it all a second time."
)
"""What a Stop does DURING THE BUILD, said just before the build starts.

Abandoning the compose client does not abandon the daemon's work, and the
layer cache is precisely what makes a resume cheap — a user told "cancelled"
without this sentence reaches for `docker builder prune` and throws away the
thing that would have saved them three hours.
"""

DOWNLOAD_CANCEL_NOTE = (
    "The part of the download that finished is kept: the fetch resumes from where it stopped."
)
"""True of this stage only, and it is the generated entrypoint that makes it true.

`curl --continue-at -` against a version-keyed file, plus an `unzip -t` before
anything is extracted (see the base template). Upstream's own downloader
truncates and restarts at byte zero, which is why the fetch is replaced.
"""

IMPORT_CANCEL_NOTE = (
    "Databases left half-written are detected and cleared before the import is run again, so "
    "nothing here has to be undone by hand."
)
"""Why a stopped import is recoverable — measured, not assumed.

Re-running the importer over a schema that already exists reports success in 28
seconds and leaves `acore_world` permanently unimportable (yulon-ubuntu,
2026-08-23). The `partial` branch of `stage_import()` is what makes this sentence
true; without it the honest copy would be the opposite.
"""

IMPORT_STAGE_CANCEL_NOTE = (
    "If these databases are half-written from an earlier stop, they are detected and cleared "
    "before the import is run again, so nothing has to be undone by hand; if they already read "
    "as a finished import, a stop leaves the statements that already ran in place and clears "
    "nothing, and the flagged phase is applied whole again next time."
)
"""What the spine says before the ordinary `import` stage runs -- true on BOTH routes.

The spine says a stage's note before the body runs (A4), and `_import` only learns
which route it is on from its one probe, inside the body: a fresh or partial import
(`stage_import()`, whose `partial` arm clears -- `IMPORT_CANCEL_NOTE`'s promise) or a
finished install's re-run of the flagged phases (`_rerun_on_marked()`, which clears
nothing -- `RERUN_CANCEL_NOTE`). Said up front, either single-route sentence is false on
the other route (Codex on T19, round 2); this one names both arms and lets the stop
itself say which happened.
"""

RERUN_CANCEL_NOTE = (
    "A stop here leaves the statements that already ran in place and clears nothing; the "
    "flagged phase is applied whole again the next time this is pressed."
)
"""The honest cousin of `IMPORT_CANCEL_NOTE`, for the one call `_rerun_on_marked()` makes.

`gate.reset()` — `DROP DATABASE IF EXISTS` over every schema the plan names — is
`stage_import()`'s own `partial` arm, reached only while a fresh import is still
running. `_rerun_on_marked()` runs after the gate already reads a FINISHED import,
through either caller: the ordinary spine's own resume, or the updates button's
`_only_the_rerunnable_phases()`, which never calls `stage_import()` at all (T19,
finding 2). A stop mid-way through its statements changes neither the marker nor
the gate's answer, so `IMPORT_CANCEL_NOTE`'s clearing promise is false for this
call specifically — the round-1 rework found it still reached the updates route
through `sqlplan.apply()`'s between-run check after the stage's own note had
been cleared, which removed the only advance warning of the real cost.
"""

REALM_HOST_TOKEN = "{{REALM_HOST}}"
"""The catalog token `ready.auth` names the realm's advertised address with."""

REALM_ADDRESS_PATTERN = r"\S+"
"""What `{{REALM_HOST}}` becomes in a ready marker: any address, not a literal one.

**Measured on `yulon-ubuntu2`, 2026-09-09, on the first live press of the
Rebuild control.** `ready.auth` is `{{REALM_HOST}}:{{WORLD_PORT}}`, and filling
the token with `INSTALL_REALM_HOST` made the marker `127\\.0\\.0\\.1:8085` while
the auth server's own line said
`Added realm "Yulon ubuntu2" at 100.99.204.5:8085.` — because
`_advertise_realm()` is the install's LAST act and had replaced that row hours
earlier. The compile finished, the containers were replaced, the new
worldserver came up with the module compiled in and answered a command over its
own channel, and the press sat in "Waiting for the world server" with nothing
left that could ever match. Unattended it spends `READY_CEILING_SECONDS` and
then puts a GOOD build back — the report this button exists to answer, with six
hours added to it.

**What is asserted, and what is not.** Readiness needs the auth server to have
loaded a realm and to be advertising it on THIS install's world port: the port
half stays exact, which is what
`test_the_ready_wait_still_refuses_a_realm_line_on_another_port` holds. WHICH
address it advertises is a different question with an owner —
`_advertise_realm()`, which reads the row itself, decides whether it is
reachable and says so — so pinning it here bought nothing and cost the above.

`\\S+` and not `.+`: the address is one whitespace-free field in that line, and
a greedy `.+` would let a marker match across a line that names no realm at all.
"""

INSTALL_REALM_HOST = "127.0.0.1"
"""The realm address every fresh install advertises, and what the `ready` stage expects.

A fresh install's realmlist row is the loopback address and the world port —
AzerothCore's default row, and the row the CMaNGOS plan writes — so the auth
log's own line is what the catalog's `ready.auth` marker names through
`{{REALM_HOST}}:{{WORLD_PORT}}`. Public: `families/cmangos.py` fills its
templates and SQL from the same constant (A3).

**It is what the row says UNTIL the install's last act, and no longer.**
`StagedInstaller._advertise_realm()` replaces it with this machine's LAN
address once every stage has finished, which is the fix for bug-checklist §35
— a realm left on this value tells every remote client that the world server
lives on the CLIENT's own machine. That step runs AFTER `ready` and can never
run before it, because `wow-wotlk`'s `ready.auth` marker is literally this
string plus the world port: an install that advertised the LAN IP first would
wait 1800 seconds for a line the auth server was never going to print. The
Networking tab still changes it afterwards, and is what the install names when
it could not.
"""

REALM_ADDRESS_UNKNOWN = (
    "This machine's address on the local network could not be worked out, so the address the "
    "realm advertises was left exactly as it is — which on a new install is "
    f"{INSTALL_REALM_HOST}, and means only this computer can reach the server. Nothing is "
    "broken and nothing needs reinstalling: connect this machine to your network, then open "
    "this server's Networking tab, press Show plan and then Apply, and it will set the address "
    "for you."
)
"""Said when there is no address to advertise — never a refusal, and never a guess.

The install has already succeeded by the time this can be said, so the only
two honest options are to say what is owed or to say nothing. A guess is not
among them: writing a plausible-looking address into the realm row produces a
server that fails in exactly the way §35 describes, with the app's own
fingerprints on it instead of the default's.

It names the Networking tab and its two buttons because "configure networking"
is not an instruction anybody can follow, and because that action already
exists and already does this job (`networking.plan()` + `apply()`).
"""


def loopback_chosen_on_purpose(intent: networking.NetworkIntent) -> str:
    """Why the realm row was left on the loopback: because somebody asked for it.

    bug-checklist §41's gate asks for "a log line saying why it was left alone",
    and each clause here is one of the things a reader needs to act on it: the
    address, so the sentence is searchable in a log next to the §35 ones; the
    date the choice was made, because a file holding one word and no date
    cannot be told from a leftover; the file, so the choice can be found and
    deleted without this app; the consequence, because a person reading an
    install log months later may not remember what they picked; and the way
    back, spelled as the two buttons that do it.

    Past tense on purpose (`was left`, `was chosen`): it records what this run
    did and what a person did before it, both of which stay true. The sentence
    it replaced in an earlier draft said the row "is" the loopback, which stops
    being true the moment anyone presses Apply with another mode.

    "no other machine can reach this server" was read against the machine on
    2026-09-06 and kept. It is loose: the mode changes the address the row hands
    out, not what is bound. On yulon-ubuntu at 06:27:43 +02:00, on the install
    the 04:43 loopback Apply had run against, `docker ps --format
    '{{.Names}}\t{{.Ports}}'` printed `ac-authserver 0.0.0.0:3724->3724/tcp` and
    `ac-worldserver … 0.0.0.0:8085->8085/tcp`, and `ss -ltn` printed LISTEN on
    `0.0.0.0:3724` and `0.0.0.0:8085` — so another machine still connects and
    logs in, and is then told the world server is at 127.0.0.1, i.e. on itself,
    so it cannot play. Kept rather than reworded because this exact string is
    what the closing-step gate driver asserts
    (`pyplan/gates/bug41-loopback-2026-09-05/closing_step_driver_b41.py:121`)
    and what two committed press records of 2026-09-06 hold verbatim
    (that folder's `closing-step-output.txt:13` and
    `yulon-ubuntu-press/yulon-log-press-chosen.txt:12`); rewording it would make
    the closed §41 entry quote a sentence the tree no longer prints, and no code
    reads this string to decide anything.
    """
    # A record with no timestamp is one nothing this app wrote: `record_network_intent()`
    # always stamps it. Printing the epoch for it would put "on 1970-01-01" in
    # an install log, which reads as a bug in the app rather than as a file
    # somebody edited by hand.
    when = (
        f"on {time.strftime('%Y-%m-%d', time.localtime(intent.recorded_unix))}"
        if intent.recorded_unix > 0
        else "at a time the record does not give"
    )
    return (
        f"The address this realm advertises was left exactly as it is, because this server was "
        f"set to only this computer ({networking.LOOPBACK_ADDRESS}) {when} from its "
        f"Networking tab, and that choice is recorded in {networking.INTENT_FILE} in the server "
        "folder. Nothing here overwrote it, which means no other machine can reach this server. "
        "To undo it, open this server's Networking tab, pick LAN (same Wi-Fi) or Internet play, "
        "press Show plan and then Apply."
    )


@dataclass(frozen=True)
class InstallState:
    """`.yulon-install.json`: what a previous run of THIS install got through.

    A hint and a claim of ownership, never an authority on what is on disk.
    `install_id` is what makes it a claim: it is derived from the absolute path
    of the directory holding it, so a state file copied into another directory
    describes an install that is not there.

    `family` (7.1) is the second claim: a folder installed as one emulator
    family is never reinterpreted as another by a catalog edit — the guard
    refuses instead. Empty in files written before 7.1, which the guard reads
    as the entry's current family; `version` stays 1 because the key is
    additive.
    """

    game_id: str
    install_id: str
    family: str = ""
    completed: tuple[str, ...] = ()
    last_error: str = ""
    updated_unix: int = 0
    version: int = STATE_VERSION
    unknown: tuple[str, ...] = ()
    """Stage names the file carried that THIS binary does not recognise.

    Kept so a downgrade is not destructive. `read_state()` used to drop them
    silently and `write_state()` then persisted the filtered tuple, so running an
    older build against a newer install PERMANENTLY stripped the newer names off
    disk — and the user-facing "Already finished: ..." line printed the filtered
    list, so nothing said it had happened. A resume on the newer build afterwards
    would redo whatever those stages were, which for this family includes a
    multi-hour compile.

    They are deliberately NOT merged into `completed`: this binary must not act on
    a stage it cannot interpret. Behaviour reads `completed`; persistence writes
    both. Added 2026-09-02 with bug-checklist section 23.
    """

    def with_stage(self, stage: str, order: Sequence[str]) -> InstallState:
        """This state plus `stage`, in `order`, with nothing recorded twice.

        `order` is the ENTRY's stage tuple rather than a module constant
        because 7.1 has two families with two tuples. A name outside it is
        dropped — the same rule `read_state()` applies on the way in — so the
        file can never carry a name nobody can interpret.
        """
        if stage in self.completed:
            return self
        done = set(self.completed) | {stage}
        # `unknown` rides along untouched: `replace()` keeps it, and it is not in
        # `order`, so the comprehension below could not carry it even by accident.
        return replace(self, completed=tuple(s for s in order if s in done), last_error="")

    def has(self, stage: str) -> bool:
        """Did a previous run finish `stage`? Never a reason to skip on its own."""
        return stage in self.completed


@dataclass(frozen=True)
class Claim:
    """`read_claim()`'s answer: the ownership, plus the state when there is one.

    `state` is non-None exactly when `ownership is Ownership.OWNED`. It is the
    parsed record; `UNKNOWN` carries none, which is the point of it.
    """

    ownership: Ownership
    state: InstallState | None = None
    reason: str = ""
    """Why this is `UNKNOWN`, when the generic sentence would be wrong or harmful.

    Empty for every other answer, and for the ordinary damaged file. It is
    here for the one `UNKNOWN` that is not damage: a record written by a
    NEWER build, which is intact, is somebody's working install, and must
    not be met with the generic refusal's advice to delete the file.
    """


def read_claim(server_dir: Path, *, valid: Sequence[str]) -> Claim:
    """The ONE place a folder is turned into an ownership answer.

    This function exists because two of them disagreed, on exactly one input,
    in the direction that loses work. `read_state()` answered `None` for a
    state file that would not parse and `claimed_this_folder()` answered
    `(server_dir / STATE_FILE).is_file()` — presence — so a corrupt file made
    the guard treat the folder as FRESH while the clone stage treated it as
    OURS, and `git fetch` + `git reset --hard` ran over the user's own
    checkout. Repro (adversarial review, 2026-08-31): clone a repo to
    `~/mywork`, edit a file, truncate `.yulon-install.json` to zero bytes,
    install into `~/mywork`; the edit is gone. Commit `60d53374` hid this on
    enforcing SELinux boxes — the container could not read `.git`, so an
    earlier guard raised first — and `5c6c655c`, which made those reads work
    again, uncovered it.

    So: presence is never ownership, and a corrupt file is never allowed to
    make this engine MORE confident than a missing one. Everything that asks
    "is this folder mine?" asks here.

    The file is never deleted or rewritten here — one that cannot be read may
    be somebody else's, and the caller is what decides whether the directory
    can be used at all.

    `valid` is the entry's stage tuple: a name outside it is dropped rather
    than kept, so a stage that no longer exists can never become a skip.
    """
    if not (server_dir / STATE_FILE).is_file():
        return Claim(Ownership.UNCLAIMED)
    parsed = _parse_state(server_dir, valid=valid)
    if parsed is None:
        return Claim(Ownership.UNKNOWN)
    if parsed.version > STATE_VERSION:
        # `>` and not `>=`: a file AT this version is every install anyone
        # has. Measured 2026-09-02 on a v2 file resumed by this v1 build:
        # it parsed as OWNED, the install resumed, and `write_state()`
        # rebuilt the payload from the keys this build knows -- losing
        # `client_dir` and `secrets_rotated_unix` while writing `version: 2`
        # back unchanged, so the file went on claiming to be a v2 record
        # after it had stopped being one and the newer build could not tell.
        # Additive keys are what `STATE_VERSION`'s docstring names as the
        # evolution path, and they were exactly what was destroyed. Refusing
        # here means the file is never opened for writing at all.
        logger.warning(
            f"{server_dir / STATE_FILE} says version {parsed.version}; this build "
            f"understands {STATE_VERSION}. Refusing rather than rewriting it."
        )
        return Claim(
            Ownership.UNKNOWN,
            reason=(
                f"{server_dir} holds an install record written by a newer version of "
                f"Yu'lon (the record says version {parsed.version}; this one "
                f"understands {STATE_VERSION}). It was left exactly as it is and "
                "nothing was written, because rewriting it here would throw away "
                "whatever the newer version put in it. Update Yu'lon, or install "
                "into another folder."
            ),
        )
    return Claim(Ownership.OWNED, parsed)


def read_state(server_dir: Path, *, valid: Sequence[str]) -> InstallState | None:
    """The state file in `server_dir`, or None if there is none this engine can read.

    The HINT, and only the hint: an unreadable or malformed file answers None
    because a hint that cannot be parsed is simply no hint. That is the right
    answer for "which stages may I skip?" and the wrong one for "is this folder
    mine?", which is `read_claim()`'s question and never this one's. The two
    used to share this single answer, and see `read_claim()` for what that cost.
    """
    return read_claim(server_dir, valid=valid).state


def _parse_state(server_dir: Path, *, valid: Sequence[str]) -> InstallState | None:
    """The state file's contents, or None if it will not open, parse, or say whose it is."""
    path = server_dir / STATE_FILE
    try:
        with path.open(encoding="utf-8") as fh:
            parsed = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.debug(f"no usable install state in {server_dir}: {exc}")
        return None
    if not isinstance(parsed, dict):
        return None
    game_id = parsed.get("game_id")
    install_id = parsed.get("install_id")
    if not isinstance(game_id, str) or not isinstance(install_id, str):
        logger.warning(f"{path} does not say which install it belongs to; ignoring it")
        return None
    completed = parsed.get("completed")
    named = tuple(s for s in completed if isinstance(s, str)) if isinstance(completed, list) else ()
    stages = tuple(s for s in named if s in valid)
    unknown = tuple(s for s in named if s not in valid)
    # `valid` empty means the CALLER IS NOT ASKING ABOUT STAGES, and the two
    # callers that pass `()` both say so in as many words: `apply.
    # server_dir_claim()` wants the identity and `purge._default_reason()` wants
    # the version. Measuring `completed` against an empty tuple made every
    # recorded stage "unknown", so every purge plan of every ordinary install
    # logged advice about a version mismatch that was not happening — read on
    # m910q during 8.9b's live gate, 2026-09-08, on an install this build had
    # written itself. `stages` is still filtered and `unknown` is still carried,
    # so nothing about what the file yields changes; only the sentence goes.
    if unknown and valid:
        # Loud, because the silent version cost a downgrade its progress. This is
        # the ordinary shape of running an older build against a newer install;
        # it is not an error, and the names are kept and written back.
        logger.warning(
            f"{path} records stages this build does not know: {', '.join(unknown)}. "
            "They are kept in the file and ignored for this run — this is usually an "
            "older Yu'lon opening an install a newer one created."
        )
    # A missing or non-string `family` reads as "" — the one value that means
    # "this file does not say". It is never a family name, so it can never
    # match the wrong one; the guard treats it as the entry's own family, which
    # is safe because every state file written before 7.1 was written by the
    # only family that existed then.
    family = parsed.get("family")
    error = parsed.get("last_error")
    updated = parsed.get("updated_unix")
    version = parsed.get("version")
    return InstallState(
        game_id=game_id,
        install_id=install_id,
        family=family if isinstance(family, str) else "",
        completed=stages,
        last_error=error if isinstance(error, str) else "",
        updated_unix=updated if isinstance(updated, int) else 0,
        version=version if isinstance(version, int) else STATE_VERSION,
        unknown=unknown,
    )


def write_state(server_dir: Path, state: InstallState) -> None:
    """Write the state file atomically, never leaving a half-written one behind.

    A truncated state file is worse than none: `read_state()` would answer None
    and the resume would redo a two-hour build it had already finished.

    The directory is created if it is not there. It always was under the 6.2
    stage order, where `clone-core` made it before anything was recorded; a
    family whose first recorded stage writes nothing to disk (7.3's
    `db-password`) would otherwise have its record silently dropped and redo
    that stage on every resume.
    """
    path = server_dir / STATE_FILE
    payload = {
        "version": state.version,
        "game_id": state.game_id,
        "family": state.family,
        "install_id": state.install_id,
        # Both halves. `completed` alone is what used to make a downgrade lossy:
        # the unknown names were read, dropped, and then written out of existence.
        # Appended after the known ones rather than interleaved, because their
        # position in a future stage order is exactly what this build cannot know.
        "completed": [*state.completed, *state.unknown],
        "last_error": state.last_error,
        "updated_unix": int(time.time()),
    }
    tmp = path.with_name(path.name + ".new")
    try:
        server_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)  # atomic on POSIX and on Windows
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        logger.warning(f"could not record install progress in {path}: {exc}")


@dataclass(frozen=True)
class Secrets:
    """What a stage may need that must never be printed: the database password.

    A holder rather than a bare `str` so a `StageContext` can be logged or
    land in a traceback without the value going with it — `repr()` masks it
    on purpose, and there is no `__str__` that gives it back.
    """

    db_password: str

    def __repr__(self) -> str:
        return "Secrets(db_password=***)"


def secret_token_name(field_name: str) -> str:
    """The `{{TOKEN}}` a `Secrets` field stands for: `db_password` -> `DB_PASSWORD`.

    One line, and it lives here so that it is spelled ONCE. Two modules derive
    from `Secrets` and they must agree exactly or a protection stops covering
    what it thinks it covers: `families/dockerfile.py` derives the NAMES it
    refuses and drops (`SECRET_TOKENS`), and `families/cmangos.py` derives the
    name->VALUE mapping the conf tables and the SQL spend (`secret_token_map`).
    Both used to write `field.name.upper()` themselves, in different files,
    with nothing holding them equal — and a divergence there is silent in the
    dangerous direction: the mapping still carries the value under the new
    spelling while the refusal still looks for the old one.

    Removing the duplication rather than testing for it was the choice, because
    a cross-module equality test is one more thing to keep and it can only
    report a drift that this function makes impossible.

    "Spelled ONCE" is true of the DEFINITION and not of the bindings, and the
    difference bites exactly one kind of test. Both users import the function
    by name (`from yulon.catalog.native import secret_token_name`), so the
    object is reachable through three module namespaces — this one, and each of
    `families/dockerfile.py` and `families/cmangos.py` — and rebinding any one
    of them leaves the other two pointing at the original. Checked 2026-09-02:
    nothing monkeypatches it, and the only mentions under `tests/` are two
    direct calls in `test_families_cmangos.py`. A future test that set
    `native.secret_token_name` and then exercised either derivation would be a
    silent no-op rather than a failure; patch the module that spends it, or
    pass the spelling in.

    It takes the field NAME rather than a `Field` so a caller with only a
    string can spend it, and it is deliberately not `str.upper` under a new
    name: the grammar the catalog's templates use is what this states, and if
    that grammar ever needs an exception it needs exactly one place to put it.
    """
    return field_name.upper()


@dataclass(frozen=True)
class StageContext:
    """Everything a stage body is handed. Frozen: a stage reads, the spine decides.

    `secrets` is resolved by the spine BEFORE the first stage (see
    `StagedInstaller.resolve_secrets()`), so a frozen context can carry it and
    no stage has to know where the password came from.
    """

    server_dir: Path
    client_dir: Path | None
    state: InstallState
    cancel: threading.Event | None
    secrets: Secrets
    force_build: bool = False
    """The press was a REBUILD, so the build stage's skip rule does not apply.

    On the context rather than on the installer because it is a fact about
    THIS press, not about this install: the same engine object serves Install
    and Rebuild, and a flag on the object would outlive the press that set it.

    It is read in exactly two places — `build_would_be_skipped()` and
    `stage_build()` — and those two are one rule asked from two sides, which is
    why the six-state test in `test_spine.py` drives them together and
    `test_rebuild.py` drives all six again with this set. Anything else that
    learns to read it has to join that pairing or the prediction and the
    outcome can disagree, which is a refusal firing on a press that is about to
    compile.
    """
    updates_only: bool = False
    """The press was an UPDATES press, so the import stage may apply the plan's
    re-runnable phases and NOTHING else.

    On the context for `force_build`'s reason — it is a fact about this press,
    not about this install, and one engine object serves every press — and it
    carries a much harder promise than that one does. `import` is the family's
    own stage, and its body branches on a probe: the flagged-phase route is one
    of five arms, and two of the others are a full multi-hour import that writes
    a completion marker (`absent`) and a `DROP DATABASE` over every schema the
    plan names (`partial`, through `gate.reset()` — reached even with
    `service=None`, because `stage_import()` returns AFTER that block).

    So a flag read late enough to be a precondition check is not enough: the
    family reads this BEFORE it calls `stage_import()` at all, and the ordinary
    import is unreachable on this route rather than merely guarded. Found by
    T14's cold reviewer, whose reading of the round-1 tuple was that a press
    consenting to three files could run the whole install's import against a
    Tortoise install that had failed at that very stage, mint the app user's
    password in memory and persist it nowhere — `db-password` is not in the
    updates tuple.
    """


@dataclass(frozen=True)
class Stage:
    """One named, resumable step: data plus a bound callable, not a name in an if-chain.

    `name` is what `.yulon-install.json` records, so renaming one reinterprets
    every state file in the wild — the AzerothCore names are pinned by a test
    for that reason. `recorded=False` is the old `NEVER_RECORDED`: a resume
    must always run it again. `cancel_note` is what a Stop costs HERE and only
    here; the SPINE says it right after `--- <name>`, and no stage body yields
    its own, so no family can say the build's sentence over the download.
    """

    name: str
    run: Callable[[StageContext], Iterator[str]]
    recorded: bool = True
    cancel_note: str = ""


class ImportGate(Protocol):
    """What the import stage asks of a database: its state, and a way to clear a half-written one.

    Family-neutral: AzerothCore answers through the injected `acore_*` probe
    pair (`CallableGate`), CMaNGOS through its own marker table (7.3). The
    stage's five-branch table is the same for both.
    """

    def probe(self) -> docker.ImportState: ...

    def reset(self) -> tuple[str, ...]: ...

    def adoption_gaps(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class CallableGate:
    """An `ImportGate` from the two callables the app already injects for AzerothCore.

    `reset_fn` may be None — an engine built without a reset seam — and then
    `reset()` is the refusal the old `_import()` made inline: a half-written
    database with no way to clear it is a stop, not a guess.
    """

    probe_fn: docker.ImportProbe
    reset_fn: docker.ResetUnfinished | None
    gaps_fn: Callable[[], tuple[str, ...]] | None = None
    """What `adoption_gaps()` answers, or None: this gate cannot look table by table.

    Defaulted to None and NOT to a callable answering `()`, because the two are
    opposite answers: `()` means "the plan's schemas and tables are all there",
    which is what an adopt press writes a completion marker on the strength of.
    A gate built out of the AzerothCore probe pair has no plan to read tables
    off, and it must say so rather than say nothing is missing.
    """

    def probe(self) -> docker.ImportState:
        return self.probe_fn()

    def adoption_gaps(self) -> tuple[str, ...]:
        """`gaps_fn`'s answer, or the one gap a gate that cannot look must report.

        Fails CLOSED. The caller's rule is "no gaps, so the row may be written",
        and a gate with nothing behind this question answering `()` would put a
        completion marker on a database it never opened.
        """
        if self.gaps_fn is None:
            return ("these databases cannot be checked table by table by this install's probe",)
        return self.gaps_fn()

    def reset(self) -> tuple[str, ...]:
        if self.reset_fn is None:
            raise InstallerError(
                "This install's databases were left half-written and this installer has no way "
                "to clear them, so nothing was run."
            )
        return self.reset_fn()


def _git_file_unmodified(dest: Path, relative_path: str) -> bool | None:
    """`git status --porcelain -- <path>` inside a container; see `ContainerGit`.

    Defined above `Seams` rather than beside `_git_remote_url()` because it is a
    default value, evaluated when the dataclass is created.
    """
    return git.ContainerGit().is_unmodified(dest, relative_path)


READY_CEILING_SECONDS = 6 * 60 * 60
"""The outer bound on the ready wait, whatever the server is saying.

NOT a load budget, and the refusal that names it says so. `wait_for_ready()`
grants a server that is still printing another window every time it prints, so
without an outer bound an install could wait for ever with no way to cancel it
(`wait_ready()` takes no cancel) — this is where that stops.

Six hours is 5.8 times the slowest first boot this project has measured —
Tortoise's 3702 s ready stage on yulon-win11-gate 2026-09-05, with the server
directory on Docker Desktop's 9p share reading at about 1.4 MB/s. (This line
read "about eight times" and cited TBC's 46.0 minutes until 2026-09-05, when
the Tortoise run measured a slower boot and nothing recomputed the multiple;
21600 / 2763 is 7.8 and 21600 / 3702 is 5.8. The evidence moved, the sentence
did not.) It is a wall-clock number and therefore exactly the kind this design
is here to get rid of, which is why it sits several times clear of the evidence
rather than beside it: a server still emitting fresh boot output six hours in
has a problem no timeout should hide.

This is the INSTALL ceiling. A management wait gets its own — see
`MANAGEMENT_CEILING_WINDOWS`.
"""

MEASURED_9P_FIRST_BOOTS_SECONDS = (1479, 2763, 3702)
"""Every first boot this project has timed on Docker Desktop's 9p share, in seconds.

All three are HEALTHY: each printed the whole way and ended `RestartCount=0`.
They are slow servers, not broken ones, and they are the entire evidence base
under both numbers below.

    Vanilla   1479 s  yulon-win11-gate 2026-09-04, `docker logs -t`,
                      06:12:43Z `mangosd` start -> 06:37:22Z first `Avg Diff:`
    TBC       2763 s  yulon-win11-gate 2026-09-04, `docker logs -t`,
                      18:59:55Z -> 19:45:58Z
    Tortoise  3702 s  yulon-win11-gate 2026-09-05, ready-stage wall,
                      `pyplan/gates/7.7-win11-tortoise/README.md`,
                      23:41:37 `up` -> 00:43:19 `finished` box-local (the
                      banner's own "59 minutes 18 seconds" is 3558 s of that;
                      the stage wall is what a wait sits through, so the stage
                      wall is the number)

Each figure is the difference between the two stamps beside it and nothing
else. Until 2026-09-05 this docstring printed 1476 and 2760 for the first two,
which are 24.6 * 60 and 46.0 * 60 — the write-up's ROUNDED MINUTES multiplied
back out, three seconds adrift of the stamps on the same line. The seconds are
no longer typed anywhere they can drift:
`test_the_boots_this_file_bounds_waits_with_are_the_difference_between_their_own_stamps`
computes all three, from the stamps for the first two and from the gate's
README for the third.

The Tortoise number is a STAGE wall and the other two are container-to-banner,
so they are not the same measurement. Mixing them anyway is the conservative
direction and the only one available: a ready wait pays the stage's clock, and
nobody has timed a CMaNGOS stage wall on 9p.
"""

SLOWEST_MEASURED_FIRST_BOOT_SECONDS = max(MEASURED_9P_FIRST_BOOTS_SECONDS)
"""The longest of those, 3702 s. The measurement, not the bound.

A management wait that stops before this has stopped on a server that was about
to succeed, which is the 2026-09-04 verdict this whole lane exists to remove:
until 2026-09-05 the two callers that use `docker.azerothcore_ready()`'s 480 s
default — `controller.Controller.wait_ready()` and
`controller_wow_wotlk.docker_ctl.wait_server_ready()` — were bounded at four
windows, 1920 s, which is shorter than all three of the boots above.

What bounds a wait is `MANAGEMENT_FLOOR_SECONDS`, which stands clear of this
rather than on it. The two are separate names on purpose: this one may only
change when somebody measures a boot, and the review that split them measured
the reason — with the floor sitting exactly here, a 3702 s boot was accepted at
the 480 s callers and a 3703 s one was refused, at elapsed 3702 s (m910q
2026-09-05, driven through `wait_ready_quietly()`).
"""

MANAGEMENT_FLOOR_MARGIN = max(
    slower / faster
    for faster, slower in zip(
        sorted(MEASURED_9P_FIRST_BOOTS_SECONDS),
        sorted(MEASURED_9P_FIRST_BOOTS_SECONDS)[1:],
        strict=False,
    )
)
"""How much slower than the slowest boot measured a healthy one is still allowed to be.

1.868 today: 2763 / 1479, the widest gap between two adjacent boots in
`MEASURED_9P_FIRST_BOOTS_SECONDS`. **OWNER DECISION, made 2026-09-05 and open**
— `pyplan/checklist.md`, under 7.7 — because it is a policy argued from
evidence rather than a measurement, and the owner may want it wider, narrower,
or replaced by a fourth run.

THE ARGUMENT. Round 4 put the floor exactly on the slowest sample, and a review
answered it in one line: 3703 s is refused. A bound with zero margin over a
single sample of a noisy quantity is a bound that will refuse the first healthy
boot slightly slower than the one boot anybody happened to time. The three
measurements are the only thing available to size the margin with, and what
they show is the spread ACROSS the servers this project ships on one box and one
share: 1479 -> 2763 -> 3702, a factor of 2.50 end to end and 1.87 between the
widest-separated neighbours. So the margin is the widest gap the evidence
actually exhibits, applied once above the slowest thing in it — i.e. the next
server, or the next box, is assumed to sit no further above Tortoise than TBC
sits above Vanilla.

WHAT IT DOES NOT MEASURE, said plainly: nothing here is run-to-run variance.
These are three DIFFERENT servers, timed once each; nobody has booted the same
server twice on 9p, so the project has no number for how much one server varies
between runs, and this margin is a stand-in for a quantity that has never been
measured. That is the weakness the owner is being asked to rule on, and the way
to close it is a second run of one of these three rather than an argument.

WHY NOT THE INSTALL CEILING'S RULE. `READY_CEILING_SECONDS` sits 5.8 times
clear of the same slowest boot, and copying that here would bound the 480 s
AzerothCore callers at six hours — the exact collapse of the two ceilings that
`MANAGEMENT_CEILING_WINDOWS` exists to undo. The two bounds answer different
failures: the install ceiling stops a server that talks for ever, and this
floor stops a management wait refusing one that was going to finish.
"""

MANAGEMENT_FLOOR_SECONDS = math.ceil(SLOWEST_MEASURED_FIRST_BOOT_SECONDS * MANAGEMENT_FLOOR_MARGIN)
"""The shortest wall clock any management wait may be bounded by. 6916 s (1.9 h).

`ceil(3702 * 1.868…)`. Derived rather than typed, so the two things a reader
would otherwise have to trust — the measurement and the margin — are each owned
somewhere: the boots by the stamps and the gate README they are read from, the
margin by the docstring above and the checklist bullet it points at.

6916 is not a multiple of any budget in use (480, 1800, 10800), which is why
the last window of a management wait is a short one — see
`test_a_management_wait_is_bounded_by_the_ceiling_and_shortens_its_last_window`.
That is a consequence, not a reason.
"""

MANAGEMENT_CEILING_WINDOWS = 2
"""How many quiet budgets a MANAGEMENT wait may spend, when that is the larger bound.

The install gets `READY_CEILING_SECONDS`. The two waits are bounded by different
things and collapsing them was a regression: an INSTALL is a long operation the
user started knowing it was long and which streams progress the whole way, and
six hours is there only so a server printing rubbish for ever cannot hang it.
Between 2026-09-04 and 2026-09-05 the management waits took the install's
ceiling, so a call that used to be bounded at 480 s, 1800 s or 10800 s could
block for six hours whatever its caller asked for, and nothing tested the change.

TWO, because two is the whole claim this constant makes: a budget spent once is
not a budget, and that single-shot reading is the bug this lane exists for. It
is not the number that decides any shipped ceiling today, and that is asserted
rather than hoped — `test_the_management_ceiling_is_the_floor_or_the_cap_for_every_budget_shipped`
walks `catalog.json` and fails the day an entry lands in the band
(3458 s < `timeout_s` < 10800 s) where `timeout * WINDOWS` is what answers. At
that point somebody has to own the multiple with a measurement.

HOW FAR THAT GOES, measured rather than claimed (m910q 2026-09-05, this file's
whole test module at each value): 1 is red (two tests, one of them a real-path
wait that goes back to spending the budget once), 2 and 3 are both green, and 4,
5 and 11 are red (the floor-or-cap audit plus the last-window shortening). So
the constant is owned to a band of exactly two values and a docstring arguing
for one of them over the other would be a confident reason with nothing behind
it. The band was a single value, 2, until 2026-09-05: raising the floor to
`MANAGEMENT_FLOOR_SECONDS` moved 1800 * 3 = 5400 s from above the floor to
below it, and a bound nothing reaches is a bound nothing can measure. That is a
real cost of the margin, recorded here rather than left for the next reader to
find with a mutation.

WHO ACTUALLY CALLS ONE. Not the Server tab's Stop/Start buttons: an earlier
version of this docstring rested the size on them and they do not wait —
`yulon/ui/controller_view.py:998` and `:1006` call `controller.start` and
`.stop`, neither of which asks whether the server came up. Measured 2026-09-05
by grepping the tree: outside `native.py` and `docker.py` every `wait_ready` /
`wait_server_ready` is a definition or prose, and the only code that RUNS one is
`pyplan/gates/gate-79-controller-surface.py`, three times per run (lines 219,
414, 487). Its worst case — a server that keeps printing and never says ready —
is therefore three ceilings:

    game          budget    ceiling   3 x ceiling   was, single-shot
    wow-wotlk       480 s    6916 s        5.8 h          24 min
    wow-tbc        1800 s    6916 s        5.8 h           1.5 h
    wow-vanilla    1800 s    6916 s        5.8 h           1.5 h
    wow-tortoise  10800 s   21600 s         18 h             9 h

Those are the numbers this change costs, written down because the round that
introduced a management ceiling quoted only the flattering end of its own
arithmetic. The first three rows read 3702 s and 3.1 h for one day: that was
`MANAGEMENT_FLOOR_SECONDS` sitting exactly on the slowest measured boot, with
no margin, which a review refuted by refusing a 3703 s boot. The margin is
1.87x and the price of it is the difference between those two columns.
A quiet server still ends its wait after ONE window (`_read_world()`
answers `quiet` the moment two readings match), so none of this is paid by a
server that is merely down — only by one that talks for ever.

Tortoise is the row worth reading twice. Its `timeout_s` was widened to 10800
on 2026-09-05 (`eb5f3b3f`, the 7.7 gate), and at that size ANY multiple of two
or more lands on `READY_CEILING_SECONDS`, so its management ceiling and the
install's are the same six hours and cannot be separated without giving it a
single window. That is a property of the catalogue number, not of this constant:
10800 s was measured as a stage TOTAL and is read here as how long the server may
say NOTHING, and nothing has ever measured three hours of Tortoise silence.
Narrowing it belongs to whoever owns `catalog.json`'s `ready` block; recorded
here so the next reader does not have to re-derive it.
"""


def management_ceiling(timeout: float) -> float:
    """The wall clock a management wait may spend, given the quiet budget it was handed.

    Three bounds, in this order, and each one is a different failure:

    * `timeout * MANAGEMENT_CEILING_WINDOWS` — the budget must be spendable more
      than once or it is the single-shot total this lane replaced.
    * `MANAGEMENT_FLOOR_SECONDS` as a floor — a caller may not ask for a bound
      shorter than the slowest healthy boot anyone here has measured, plus a
      margin, because that bound refuses a server that was going to succeed.
      This is why `timeout=` no longer bounds the call all the way down, and it
      is deliberate: at 480 s the old four-window bound stopped 1782 s short of
      the boot the same box measured on the same share. The margin is there
      because the floor spent a day sitting exactly ON that boot, where 3703 s
      was refused.
    * `READY_CEILING_SECONDS` as a cap — a catalogue entry asking for a six-hour
      quiet budget does not get a twelve-hour poll behind a button.
    """
    return min(
        max(timeout * MANAGEMENT_CEILING_WINDOWS, float(MANAGEMENT_FLOOR_SECONDS)),
        float(READY_CEILING_SECONDS),
    )


def _line_around(text: str, found: re.Match[str]) -> str:
    """The whole log line a match landed in, stripped.

    `docker.wait_ready()` logs `match.group(0)` and calls it "the LINE, not the
    pattern". It is neither: a catalogue `fatal` is an alternation, so the group
    is the BRANCH that matched — `Correct *.map files not found`, with the
    server's own `in data directory` cut off. The branch is no more the server's
    sentence than the alternation is, so a refusal a user reads widens it back
    to the line it came from.
    """
    start = text.rfind("\n", 0, found.start()) + 1
    end = text.find("\n", found.end())
    return (text[start:] if end < 0 else text[start:end]).strip()


def _spell_seconds(seconds: float) -> str:
    """`30 -> "30 seconds"`, `1800 -> "30 minutes"`, `21600 -> "6 hours"`.

    For refusals and progress notes a user reads, so the sub-minute branch is
    not decoration: it answered `"0 minutes"` until 2026-09-05, which is true of
    nothing that ever happened. Unreachable while every duration printed was
    ASSUMED from the window count (the smallest of those was a whole window);
    reachable the moment they are measured, because `docker.wait_ready()` gives
    up after `_CLI_MISSING_GRACE_SECONDS` — thirty seconds — when the docker CLI
    has gone.

    `0 -> "less than a second"` for the same reason `"0 minutes"` was wrong, and
    it answered `"0 seconds"` until 2026-09-05: a duration this reports is one
    the wait MEASURED, so it happened, so no true sentence about it is "0". A
    window can end in effectively no time — `docker.wait_ready()` returns False
    on its first poll when the log already holds a `fatal` line, and the two
    `monotonic` reads either side of it can land in the same clock tick.
    """
    minutes = int(round(seconds / 60))
    if minutes == 0:
        count = int(round(seconds))
        if count == 0:
            return "less than a second"
        return f"{count} second" if count == 1 else f"{count} seconds"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"


@dataclass(frozen=True)
class WorldOutput:
    """One look at the world container: what it has said on THIS run, and whether it is alive.

    The three fields are what `wait_for_ready()` needs to tell a slow server
    from a dead one, taken together so they describe a single moment rather
    than three moments the engine then reasons across.

    `restarts` is `None` for "could not ask", never 0: 0 is a container that has
    never died, and reading a failed `docker inspect` as that would let a crash
    loop run out the whole ceiling. `status` is `""` for the same reason —
    `docker.ContainerState()` answers `""` when its read failed, and `""` must
    never be read as "not running".
    """

    text: str
    restarts: int | None
    status: str


def _world_output(spec: docker.ContainerSpec, *, wsl_distro: str | None = None) -> WorldOutput:
    """`WorldOutput` for the world container, in one state read plus one log read.

    `wsl_distro` names the daemon to ask, and BOTH reads take it or the verdict
    is formed from a machine the wait was never watching. On Windows a WSL
    install's containers exist only inside the distro: `docker inspect` on the
    host answers "No such object", `container_state()` turns that into a
    default `ContainerState()`, and this comes back `WorldOutput("", None, "")`
    — "docker would not talk" — for a container that is up and printing. Two of
    those in a row end the wait. Until 2026-09-05 `wait_ready_quietly()`
    forwarded the distro to the WAIT and called this without it, so the wait
    watched one daemon while the verdict came from another. RED on m910q
    2026-09-05, from the docker guard added to `tests/conftest.py` that day:
    `test_wait_helpers_forward_the_distro_a_wsl_install_lives_in` — a test whose
    whole subject is that the distro is forwarded — was passing while it ran
    `['docker', 'inspect', 't-world', ...]` against the box's own daemon.

    `container_state()` first, and its `started_at` is handed to the log read:
    that is what scopes the log to the CURRENT run, without which a restarted
    server's previous run — its old ready banner included — is still sitting
    there (`docker._logs`, measured 2026-08-22). It also means the restart count
    and the text come from the same look at the machine.

    `docker._logs()` rather than a public function because there is none: that
    module's only public log reader is `follow_logs()`, which streams
    `docker logs -f` and does not return. The same reach as
    `ABANDONED_WORKER_SECONDS`' into `runner`, and for the same reason — naming
    the real thing beats copying it.

    An EMPTY `status` is how this recognises "could not ask", and that is read
    off the value rather than caught as an exception. Until 2026-09-05 the
    unknown case was an `except docker.DockerCommandError` around both calls,
    and NEITHER of them raises it: `container_state()` logs "could not read the
    state of ..." and returns a default `ContainerState()` on a non-zero
    inspect, `_logs()` logs and returns `""`, and `_docker()` turns even a
    missing CLI into a non-zero `CompletedProcess` rather than an exception (its
    docstring says so, and gives the reason). So the branch that produced
    `restarts=None` was unreachable, and every failed inspect arrived as the
    fabricated `restarts=0` that `WorldOutput`'s own docstring forbids — a crash
    loop underneath an unaskable daemon would have run out the whole ceiling.
    RED on m910q 2026-09-05: `assert 0 is None`, from a `container_state` double
    that answers the way the real one does.

    `text` is the one field with no unknown value: `_logs()` answers `""` both
    for "the log could not be read" and for "the container has printed nothing
    yet", and docker offers nothing to tell those apart. A state that reads and
    a log that does not therefore looks like a silent container, which
    `wait_for_ready()` refuses after one quiet budget. That is the conservative
    direction (it never calls a dead server ready) and it is the reason the
    state is read FIRST: the failure that takes both down at once — no daemon —
    is caught by the status, not by the log.
    """
    state = docker.container_state(spec.world, wsl_distro=wsl_distro)
    if not state.status:
        return WorldOutput(text="", restarts=None, status="")
    text = docker._logs(
        spec.world, this_run_only=True, since=state.started_at, wsl_distro=wsl_distro
    )
    return WorldOutput(text=text, restarts=state.restart_count, status=state.status)


_ALIVE_STATUSES = ("", "running", "restarting")
"""Container statuses that are NOT "it is gone for good".

`""` is a read that failed (`docker.ContainerState()`), and `restarting` is the
one that cost a wrong sentence: every compose service this app writes carries
`restart: unless-stopped`, so a crash-looping world server spends most of its
life in restart backoff reporting `restarting` — `ContainerState.settled` is
built on exactly that fact — and `docker.wait_ready()` answers False in the
seconds right after a restart, which is precisely when a window ends. Reading
`restarting` as "not running any more" told a user their container had stopped
while docker was busy starting it again.

Docker's full set is `created`, `running`, `restarting`, `exited`, `paused`,
`dead` and `removing`, and the four not listed above all mean nothing is going
to print — which is the sentence they get. Every one of the seven has a test
(`test_ready_budget.py`, the two parametrized status tests), because until
2026-09-05 not one of them did.

The list survived its own mutations, which is how that was found. Measured on
m910q 2026-09-05, against the file as it then stood: dropping `restarting` left
it GREEN — including the test that carries the word in its name, which drove a
container thirty restarts past the threshold, and `_read_world()` asks the
crash-loop question first, so it never reached the status at all. Adding
`paused` left it green too. Against the file as it now stands both go red, and
an earlier version of this docstring claimed a measurement the question order
makes impossible to reproduce. A status list nobody tests is a list that says
whatever it was last typed as.
"""


def _read_world(
    before: WorldOutput,
    now: WorldOutput,
    first_restarts: int | None,
    restart_loop: int,
    fatal: str | None,
) -> tuple[str, object]:
    """What a window that ended without the banner means. `("alive", None)` to wait on.

    ONE function because there are two loops that must agree — the install
    spine's `wait_for_ready()`, which turns this into five different sentences,
    and `wait_ready_quietly()`, which turns it into a bool. Two copies of an
    ordering is two orderings the day one of them is edited.

    The order is the whole content, and it is not the order the questions were
    asked in until 2026-09-05:

    * **crash loop** first. A looping container reports `restarting`, so any
      "is it still there" test that runs before this one answers for it, and a
      loop gets named a stop. `restarts` grown by `restart_loop` since the FIRST
      readable count; `None` on either side is "could not ask" and counts
      towards nothing.
    * **gone** second: a status outside `_ALIVE_STATUSES`. It is still ahead of
      the log-based questions, because an exited container is silent too and
      "it exited" is the better sentence.
    * **fatal** third: `markers.fatal` matched HERE as well as inside
      `docker.wait_ready()`, so the refusal can quote the line the server
      printed rather than the alternation from the catalog.
    * **quiet** last: this reading identical to the last. Any change at all
      counts as life, a shrinking log included — a log that shrank means the
      container restarted or `docker logs` failed, and both are better answered
      by the questions above than by declaring silence.

    "quiet" splits in two on the STATUS, and that split is a sentence rather
    than a decision: both end the wait. `WorldOutput.status` is `""` only when
    `container_state()` could not read the container at all, and two identical
    unreadable readings are `unreadable` — docker stopped answering — not
    `quiet`. Until 2026-09-05 a daemon that died mid-wait was reported as "it
    stopped printing anything at all ... so this one is stuck rather than slow",
    which contradicts the announcement printed moments earlier (this wait is
    watching the log; it had not read the log) and sends the user to
    `docker compose logs`, the one command that cannot work either. Nothing was
    learned about the server, and the refusal said it had been.
    """
    grew = None if first_restarts is None or now.restarts is None else now.restarts - first_restarts
    if grew is not None and grew >= restart_loop:
        return "loop", grew
    if now.status not in _ALIVE_STATUSES:
        return "gone", now.status
    found = re.search(fatal, now.text) if fatal is not None else None
    if found is not None:
        return "fatal", _line_around(now.text, found)
    if now == before:
        return ("quiet" if now.status else "unreadable"), None
    return "alive", None


def _restart_baseline(first_restarts: int | None, now: WorldOutput) -> int | None:
    """The crash-loop baseline: the first reading that HAS one, not the first reading.

    `_world_output()` answers `restarts=None` for a docker that would not talk,
    and a container is at its least inspectable in the seconds after `up`.
    Taking `None` as the baseline would switch the crash-loop check off for the
    REST of the wait because of one unlucky first look: `_read_world()` computes
    `grew` as `None` whenever either side is `None`, so a looping container then
    runs the whole ceiling out and is refused as quiet — a wrong sentence about a
    container docker was telling us the truth about. Same shape as
    `docker.wait_ready()`'s own `if first_restarts is None and world.status`.

    ONE function because both loops need it and only one of the two copies had a
    test: deleting the two lines from `wait_ready_quietly()` left the whole suite
    green on m910q 2026-09-05, while the identical two lines in
    `StagedInstaller.wait_for_ready()` were owned by
    `test_a_first_reading_the_daemon_refused_does_not_switch_off_the_crash_check`.
    A rule with two copies is a rule with one owner.
    """
    return now.restarts if first_restarts is None else first_restarts


def wait_ready_quietly(
    spec: docker.ContainerSpec,
    ready: docker.ReadySpec,
    *,
    wait: Callable[..., bool] | None = None,
    output: Callable[..., WorldOutput] | None = None,
    monotonic: Callable[[], float] | None = None,
    wsl_distro: str | None = None,
) -> bool:
    """`docker.wait_ready_for()` with `ready.timeout` spent as a QUIET budget, not a total.

    The bool half of `StagedInstaller.wait_for_ready()`, and the reason it
    exists is that one catalogue number was being read two ways. The install
    spine spends `install.native.ready.timeout_s` as a window that restarts
    every time the world server prints; six other waits — the base
    `controller.Controller.wait_ready()`, `TortoiseController.wait_ready()` and
    the four `docker_ctl.wait_server_ready()` — built a `ReadySpec` from the
    same field and waited it out ONCE, as a fixed total wall clock. That is the
    reading the incident of 2026-09-04 disproved: TBC's world server took 46.0
    minutes to its first `Avg Diff:` against a `timeout_s` of 1800 while
    printing the whole way, and every one of those six sites would have called
    it a failure at 30 minutes exactly as the installer did.

    So the number has one meaning in this app now, and it is this one. The
    alternative was to rename the field, which needs `catalog.py` and
    `catalog.json` — not this lane's to edit — and would have left six sites
    spending a budget under a name that said they should not.

    **The call is bounded**, by `management_ceiling(ready.timeout)`: at least
    `MANAGEMENT_CEILING_WINDOWS` of the caller's windows, never shorter than
    `MANAGEMENT_FLOOR_SECONDS`, never longer than
    `READY_CEILING_SECONDS`. So `timeout=` shortens the call only down to that
    measured floor, and deliberately: the two callers on
    `docker.azerothcore_ready()`'s 480 s default were bounded at 1920 s between
    2026-09-04 and 2026-09-05, which is shorter than every 9p first boot this
    project has measured, and refusing a server that was about to succeed is the
    defect this file is here to remove rather than to relocate.

    `wait`, `output` and `monotonic` are resolved at CALL time, never bound as
    defaults: `docker.wait_ready_for` bound at import is a function a test's
    `monkeypatch` can no longer replace, and most of those sites are tested
    that way.

    `wsl_distro` reaches the WAIT and both READS. It reached only the wait until
    2026-09-05; `_world_output()`'s docstring has what that cost.

    Returns True the moment the world server reports ready. Returns False for
    every one of `_read_world()`'s verdicts and at the ceiling, because a
    caller polling a running server wants a bool — the five sentences are the
    install spine's job, and it keeps its own loop to build them.

    A docker that will not answer at all needs no special case here, and one was
    written and then deleted: `WorldOutput("", None, "")` twice running is the
    `unreadable` verdict, so an unreachable daemon already ends this after one
    window — exactly what the single-shot wait it replaced did. The guard that
    checked for it explicitly survived its own mutation on m910q 2026-09-05
    (`if False:`, whole file still green) and differed from having no guard in
    only one case: a daemon that was unreachable for the first look and
    answered for the second, where it gave up on a container it could by then
    see. A branch whose only distinct behaviour is the wrong one.
    """
    wait = wait or docker.wait_ready_for
    look = output or _world_output
    clock = monotonic or time.monotonic
    ceiling = management_ceiling(ready.timeout)

    started = clock()
    before = look(spec, wsl_distro=wsl_distro)
    first_restarts = before.restarts
    while True:
        window = min(ready.timeout, ceiling - (clock() - started))
        if window <= 0:
            return False
        if wait(spec, replace(ready, timeout=window), wsl_distro=wsl_distro):
            return True
        now = look(spec, wsl_distro=wsl_distro)
        first_restarts = _restart_baseline(first_restarts, now)
        verdict, _ = _read_world(before, now, first_restarts, ready.restart_loop, ready.fatal)
        if verdict != "alive":
            return False
        before = now


@dataclass
class Seams:
    """Everything the engine reaches outside itself through. Real by default.

    Grouped rather than spread over twenty constructor keywords, because the
    list is long for a reason: an engine whose every external effect is a seam
    is one whose control flow can be tested without a daemon, a network or a
    four-hour build — which is the only kind of test anyone on this project can
    run for this file.
    """

    platform_id: Callable[[], str] = platform.detect
    docker_ready: Callable[[], bool] = platform.docker_ready
    ensure_docker: Callable[..., platform.ProvisionReport] = platform.ensure_docker
    # Read twice on purpose. `_preflight_lines()` asks it before anything is
    # provisioned, and it is ALSO handed to `gather()` so the report's folder
    # check and the early refusal cannot answer from two different functions —
    # the same reason `platform_id` is threaded rather than left to default.
    dir_problem: Callable[[Path], str | None] = platform.server_dir_problem
    gather: Callable[..., preflight.Facts] = preflight.gather
    clone: Callable[[git.CloneSpec], None] = field(default_factory=lambda: git.ContainerGit().clone)
    remote_url: Callable[[Path], str | None] | None = None
    file_unmodified: Callable[[Path, str], bool | None] = _git_file_unmodified
    images_built: Callable[[Sequence[str]], bool | None] = docker.images_built
    build: Callable[..., docker.AttachedRun] = docker.build_staged
    one_shot: Callable[..., docker.AttachedRun] = docker.run_one_shot
    verify_import: Callable[..., docker.ImportState] = docker.verify_import
    container_exists: Callable[[str], bool] = docker.container_exists
    container_project: Callable[[str], str | None] = docker.container_project
    container_working_dir: Callable[[str], str | None] = docker.container_working_dir
    """Which folder the compose project owning a container was brought up from.

    Threaded beside `container_project` for the same reason: a refusal that
    names a project id and not a folder tells a reporter nothing they can act
    on (T32). Read only after `container_project` has already answered with a
    real, non-`UNREADABLE` owner — see `_refuse_foreign_containers()`.
    """
    # `object` rather than `None`: `start_database()` has said since T7 whether
    # it HAD to start the container, for `apply.Applier`'s report line. This
    # stage ignores that -- it wants the database up, and it is up either way --
    # and the annotation says "whatever it answers" rather than pinning a
    # return this seam's own fakes do not have to produce.
    start_db: Callable[[docker.ContainerSpec, Path], object] = docker.start_database
    start: Callable[[docker.ContainerSpec, Path], bool] = docker.start_staged
    recreate: Callable[[docker.ContainerSpec, Path], bool] = docker.recreate_staged
    """`start` with `--force-recreate`, and the rebuild's only reason to exist as a seam.

    A separate field rather than a keyword on `start`, because the two are
    different requests and a test that could not tell them apart could not see
    the bug: a rebuild that recreated nothing would leave the pre-rebuild
    binary running behind an hour of perfectly correct compiler output.
    `docker.staged_up_argv()` holds why the force is asked for rather than left
    to compose.
    """
    tag_image: Callable[[str, str], str] = docker.tag_image
    remove_image: Callable[[str], str] = docker.remove_image
    """The rebuild's rollback: kept as a second tag before the compile, let go as one after.

    Two seams and not one `docker` handle, for the reason `recreate` is its
    own: a test that could not see the tag happen BEFORE the build, or the
    restore happen AFTER the ready wait failed, could not see the two bugs
    that would make the rollback decorative -- a rollback tagged after the
    compile is a copy of the new build, and one restored without a recreate
    leaves the failed build running.
    """
    wait_db_healthy: Callable[[docker.ContainerSpec], bool] = docker.wait_db_healthy_for
    wait_ready: Callable[[docker.ContainerSpec, docker.ReadySpec], bool] = docker.wait_ready_for
    world_output: Callable[[docker.ContainerSpec], WorldOutput] = _world_output
    """What the world server has printed, asked BETWEEN waits rather than during one.

    `wait_ready()` reads the same log every two seconds and tells its caller
    only True or False, so the caller cannot tell a server that is still
    loading from one that has stopped dead. This seam is the second reading
    that makes that difference visible; `wait_for_ready()` is the only caller,
    and its docstring holds the argument.
    """
    # `selinux_enforcing` answers True, False or None, and `None` is "could not
    # ask" — never "no". The type says so here so that a caller collapsing the
    # two is a type error, and not a Fedora install that renders no `:z`,
    # relabels nothing, and looks exactly like a working one until the
    # worldserver cannot read the config it was just handed.
    relabel: Callable[[Path], bool] = platform.relabel_for_containers
    selinux_enforcing: Callable[[], bool | None] | None = None
    fs_type: Callable[[Path], str | None] | None = None
    """The two platform questions this class is NOT the only answerer of.

    `None` and a late lookup, rather than `= platform.selinux_enforcing` bound
    at import, and the difference was measured rather than argued. Bound at
    import, ONE `monkeypatch` of `platform.selinux_enforcing` and
    `platform.filesystem_type` got two different answers out of a single
    install (m910q, 2026-09-05): `preflight.gather(...)` returned the fake's
    `True` and `btrfs` while `Seams().selinux_enforcing()` and
    `Seams().fs_type()` returned the host's `False` and `ext2/ext3`, and the
    fake counted one caller where two had asked. That is bug-checklist §27,
    and this was its last live site: `docker.bind_mount_ok()`,
    `preflight.gather()` and `git.ContainerGit` were moved to this same shape
    on 2026-09-04/05, which is `extract.run_plan()`'s shape and always was.

    Read through `ask_selinux()` / `ask_fs()` below, never directly: a caller
    that reads the field gets `None` and a crash, which is the loud version of
    the quiet bug this replaced.

    Every other seam here stays import-bound on purpose. `relabel` is the
    closest call and is deliberately left alone: nothing else in the app asks
    the host whether a folder was relabelled, so there is no second answerer
    and no split to fix -- only the same latent trap, recorded in §27 rather
    than changed for no measured defect.
    """
    monotonic: Callable[[], float] = time.monotonic
    """The clock `wait_for_ready()` reports its own durations from.

    A seam because every duration that wait PRINTS used to be ASSUMED from the
    window count — `(spent + 1) * timeout_s` — and `docker.wait_ready()` returns
    False EARLY on three of its four paths (a `fatal` line, the crash-loop latch,
    and a missing docker CLI after `_CLI_MISSING_GRACE_SECONDS`). A window that
    ended in thirty seconds was therefore reported to the user as thirty
    minutes, and no test could see it because the fake always consumed its whole
    window (review, m910q 2026-09-05). Measuring needs a clock; a clock a test
    can hand over needs a seam.
    """
    keep_awake: Callable[[], AbstractContextManager[None]] = platform.keep_awake
    lan_ip: Callable[[], str | None] = platform.detect_lan_ip
    """This machine's LAN address, for the realm row the install ends by setting.

    A seam for this module's usual reason and for one that is specific to it:
    the real function's answer depends on what network the machine happens to
    be on, so an assertion about the closing realmlist step that did NOT go
    through a seam would be a statement about a test box's DHCP lease. `None`
    is a first-class answer here (no network, or a route that could not be
    read) and `_advertise_realm()` treats it as such; see `REALM_ADDRESS_UNKNOWN`.
    """
    # The 7.3 primitives. Four of the five real functions take a `wsl_distro`
    # keyword that these types do not carry — exactly as `container_exists`,
    # `start_db` and `start` above have not carried it since 7.1. **That
    # erasure is a boundary held by convention, and nothing here enforces it.**
    # Every `wsl_distro` value in this app originates from
    # `controller.wsl_distro`, which is the MANAGEMENT path for a server that
    # is already installed; `Seams` is constructed in exactly one place
    # (`native.Seams(platform_id=platform_id)`) and no install-side caller
    # holds a distro. `catalog_view.adopt_from_wsl()` is the only route to a
    # WSL-resident server and it adopts an already-BUILT one: it never runs the
    # installer and never reaches a stage. So Yu'lon installs against the local
    # daemon and only manages a WSL-resident server, and the types say so.
    #
    # What nothing says is that it has to stay that way. A "re-run extraction
    # on an adopted server" — or any repair that reached a stage on an adopted
    # install — would hand these seams a container living on another daemon,
    # and the erasure would then send all of them to the wrong one silently:
    # `volume_exists` would answer "no such volume" for a database sitting
    # right there, which is the destructive branch its own docstring exists
    # for. No test would notice. Whether the boundary is meant to hold: the
    # 7.3 contract states these field types and gives no reason, while
    # `sqlplan.ExecStdin`/`SqlQuery` declare the keyword for the opposite
    # reason. Nobody has reconciled the two — undecided.
    run_container: Callable[..., docker.AttachedRun] = docker.run_container
    copy_from_image: Callable[[str, str, Path], None] = docker.copy_from_image
    exec_stdin: Callable[..., subprocess.CompletedProcess[str]] = docker.exec_stdin
    sql_query: Callable[[str, str, str, str | None, str], str] = docker.sql_query
    volume_exists: Callable[[str], bool] = docker.volume_exists
    world_running: Callable[[str], bool | None] | None = None
    """Is this install's world server up? Three-valued, and `None` is not "no".

    `None` here means "nobody gave one", and `ask_world_running()` then goes to
    `docker.world_running` — the same function T7 wired into every `Applier` the
    app builds, so the two enforcement points of owner answer 7 answer from one
    mapping rather than two.

    A LATE lookup, like `selinux_enforcing` and `fs_type` above and unlike every
    other seam in this class, and the reason is the test that matters most: the
    button's own. `Seams`' other defaults are bound when this class is defined,
    so a `monkeypatch` of the `docker` function they name never reaches an
    engine — and the engine an updates press runs is built inside
    `install_wiring.installer_for_app()`, where no test can hand it a fake.
    Resolved on the call, `monkeypatch.setattr(docker, "world_running", ...)`
    reaches the shipped path end to end, which is how T7's Modules-tab refusal
    is proved and is the shape this copies.
    """

    db_running: Callable[[str], bool | None] | None = None
    """Is this install's DATABASE container up? Three-valued, `world_running`'s shape.

    A second field and not the same one pointed at another container, because
    the two are asked for opposite reasons and a test that could not tell them
    apart could not see either answer: the world is read to REFUSE, and the
    database is read to decide whether an adopt press has to put the container
    back down when it is finished. One seam answering both would make a test
    that models "the world is down and the database is up" — the state every
    successful press runs in — impossible to write.

    A LATE lookup for `world_running`'s reason, and it defaults to the same
    function: `docker.world_running()` is a three-valued reading of ANY
    container of this install (it is named for the caller T7 wrote it for, not
    for the container it can be asked about), and its mapping is the one this
    press needs — a container it could not read is `None`, which is not "down",
    so a press that could not tell leaves the database exactly as it found it.
    """

    stop_db: Callable[[list[str]], None] = docker.stop_containers
    """Stop these containers. Used by ONE press, to put back what it started.

    `docker.stop_containers()` and not `stop_staged()`: this is a single
    container by name, never the install's whole stack — an adopt press that
    started the database alone must put back exactly that and must not touch a
    world server it never started (it refuses while one is up, so there is none
    to touch, and a stop that reached for the stack anyway would be a second
    promise this press has no business making).
    """

    def ask_world_running(self, container: str) -> bool | None:
        """The world's state, through the seam if one was given, else `docker`'s own."""
        ask = self.world_running
        return (ask if ask is not None else docker.world_running)(container)

    def ask_db_running(self, container: str) -> bool | None:
        """The database container's state, through the seam if one was given, else `docker`'s."""
        ask = self.db_running
        return (ask if ask is not None else docker.world_running)(container)

    def ask_selinux(self) -> bool | None:
        """Is SELinux enforcing — through the seam if one was given, else the host.

        The resolution happens HERE, on the call, which is what makes a
        `monkeypatch` of `platform.selinux_enforcing` reach this class as well
        as `preflight.gather()`. Reading `self.selinux_enforcing` directly gets
        `None` and a TypeError; see the field's docstring for the measurement
        that made that deliberate.
        """
        ask = self.selinux_enforcing
        return (ask if ask is not None else platform.selinux_enforcing)()

    def ask_fs(self, path: Path) -> str | None:
        """The filesystem under `path`, through the seam if one was given, else the host."""
        ask = self.fs_type
        return (ask if ask is not None else platform.filesystem_type)(path)


class StagedInstaller:
    """Abstract spine: everything an install needs that is not about one emulator.

    Subclasses implement `stages()` and nothing else is required of them. The
    spine runs preflight, the guard and keep-awake itself, then each stage in
    the family's order, recording the ones the family says to record.

    Constructed by `installer.installer_for()`, which is the only thing that
    decides between families. `import_probe`/`reset_unfinished` are supplied
    by the CALLER (the app wires `controller_wow_wotlk.repair`), because they
    are per-game facts and `catalog/` must not import a controller package —
    the same shape `controller_view.py` already uses.
    """

    family: ClassVar[str]
    """The `install.native.family` this class installs; asserted against the entry in preflight."""

    def __init__(
        self,
        entry: CatalogEntry,
        *,
        installers_root: Path | None = None,
        import_probe: docker.ImportProbe | None = None,
        reset_unfinished: docker.ResetUnfinished | None = None,
        seams: Seams | None = None,
    ) -> None:
        self.entry = entry
        self.installers_root = (
            installers_root if installers_root is not None else resources.installers_dir()
        )
        self._probe = import_probe
        self._reset = reset_unfinished
        self._seams = seams if seams is not None else Seams()
        self._check_stage_tuple()

    # -- the family's contract -------------------------------------------

    def stages(self) -> tuple[Stage, ...]:
        """The ordered stages, each bound to a body. Names unique; never `preflight`/`guard`."""
        raise NotImplementedError(f"{type(self).__name__} must define its stages")

    def stage_names(self) -> tuple[str, ...]:
        """The names `stages()` records, in order — what `read_state()` validates against."""
        return tuple(stage.name for stage in self.stages())

    def _check_stage_tuple(self) -> None:
        """Refuse a broken family at construction, not after a two-hour build.

        A `ValueError`, not an `InstallerError`: this is a bug in a family
        class, never something a user did (A8). A repeated name would let one
        stage's record skip another; `preflight` and `guard` are the spine's
        own and must never be recordable.
        """
        names = self.stage_names()
        if len(set(names)) != len(names):
            raise ValueError(f"{type(self).__name__} lists a stage twice: {names}")
        reserved = [name for name in ("preflight", "guard") if name in names]
        if reserved:
            raise ValueError(
                f"{type(self).__name__} may not name a stage {reserved[0]!r}: the spine owns it"
            )

    # -- the contract ----------------------------------------------------

    def server_dir(self, options: InstallOptions) -> Path:
        """Where this install goes: what the user picked, or the entry's default under $HOME."""
        if options.server_dir is not None:
            return options.server_dir
        return Path.home() / self.entry.install.default_server_dir

    def preflight(
        self,
        options: InstallOptions,
        cancel: threading.Event | None = None,
        *,
        ask: runner.Prompter | None = None,
    ) -> None:
        """Everything that must be true before anything is written. Raises, or returns.

        Same signature on every family engine, which is what lets the view
        drive one without knowing which it got. Docker provisioning is
        attempted exactly once before the machine facts are gathered, because
        every number below it is fabricated without a daemon.

        `ask` is forwarded to Docker provisioning — the docker-group consent
        and the Linux sudo password — and to nothing else; see the module
        docstring.
        """
        for _ in self._preflight_lines(options, cancel, ask):
            pass

    def run(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
        ask: runner.Prompter | None = None,
    ) -> Iterator[str]:
        """Run the install, yielding output live. Resumes whatever a previous run finished.

        `ask` reaches provisioning only; no stage may prompt.

        Raises:
            InstallerError: any refusal, any stage that failed, or a cancel.
                The message is the sentence a user reads in the failure dialog.
        """
        opts = options or InstallOptions()
        server_dir = self.server_dir(opts)
        yield f"Installing {self.entry.name} into {server_dir}"
        yield OPENING_NOTE
        yield from self._preflight_lines(opts, cancel, ask)
        self._check_cancel(cancel)

        state = self._guard(server_dir)
        # Whether this folder was OURS TO FILL when the guard accepted it, which
        # is the only fact that distinguishes the two ways a failed first stage
        # can leave a non-empty directory. See `_claim_before_writing()`.
        # `is_dir()` first: on a fresh install the folder the user chose may not
        # exist at all yet, and `_listing()` turns that `FileNotFoundError` into
        # an `InstallerError` — a refusal, from a line that is only gathering a
        # fact. A folder that is not there is as ours-to-fill as an empty one.
        started_empty = not (server_dir / STATE_FILE).is_file() and (
            not server_dir.is_dir() or not _listing(server_dir, ignoring=OUR_OWN_FILES)
        )
        self._claim_before_writing(server_dir, state, started_empty)
        yield f"Using {server_dir} ({'resuming' if state.completed else 'a fresh install'})"
        if state.completed:
            yield f"Already finished: {', '.join(state.completed)}"
        ctx = StageContext(
            server_dir=server_dir,
            client_dir=opts.client_dir,
            state=state,
            cancel=cancel,
            secrets=self.resolve_secrets(server_dir),
        )

        # The failure record for anything in here is written by `_staged()`,
        # which is the only frame holding the state each finished stage
        # produced; see its docstring for what reading a stale copy cost.
        state = yield from self._staged(self.stages(), ctx)
        # OUTSIDE the staged loop, and after the last stage, on purpose. Outside,
        # because everything in there is a reason to fail the install and this
        # is not one — a realm row that could not be written is a sentence, not
        # a failed install (`_advertise_realm()` raises nothing at all), and it
        # is deliberately not a `Stage` for that reason. After,
        # because `ready` waits for an auth log line that is `INSTALL_REALM_HOST`
        # plus the world port, so advertising the LAN address any earlier would
        # make a working server time out. Before the closing line, because that
        # line is asserted to be LAST.
        yield from self._advertise_realm(replace(ctx, state=state))
        # The bash path logs `install of <id> finished` (installer.py); this path
        # logged nothing at the end, so the only sign a run had ended was the
        # compose-project pin - which is how a tester on yulon-win11 (2026-08-28)
        # read a seven-minute readiness wait as "the install was not remembered".
        logger.info(f"install of {self.entry.id} finished")
        self._clear_error(server_dir, state)
        yield f"{self.entry.name} is installed and running in {server_dir}"

    def _staged(
        self, stages: Sequence[Stage], ctx: StageContext
    ) -> Generator[str, None, InstallState]:
        """Run `stages` in order, saying where the user is. The ONE progress reporter.

        Extracted from `run()` on 2026-09-08 so the rebuild reports through it
        rather than beside it. That is not tidiness: `^--- <name>` is what gate
        scripts, log captures and the interrupted-import watchers match on, and
        a second loop printing its own nearly-identical marker is a second place
        for that format to drift. One run WAS missed on 2026-09-03 by a watcher
        that could not see the stage it was armed for.

        The percentage is of STAGES BEHIND YOU, not of work done: the twelve are
        wildly unequal -- `conf` is seconds and `build` is an hour -- so this
        says "9 of 12 started", which is true, rather than implying three
        quarters of the time is gone, which it is not. A resumed install counts
        the same way, because the stages it skips are done. A rebuild's three
        count the same way again, and the denominator is ITS length: "Step 1 of
        3" over a rebuild is honest, where "Step 4 of 9" would describe an
        install that is not happening.

        `ctx.state` threads through the loop and is RETURNED, because
        `_run_one()` writes the state file and the caller needs what was written
        -- `run()` clears the error record against it and advertises the realm
        with it.

        **The failure record is written HERE, and that is not a tidy-up.** It
        used to be a `try` in `run()` around this loop, reading the `state`
        variable the loop assigned on each pass. Moving the loop into a function
        moved that variable with it, and the caller's copy stayed at the state
        the run STARTED with -- so a stage-three failure wrote a state file
        whose `completed` was empty, throwing away the record of the two stages
        that had finished and turning the next press into a re-clone. Caught by
        `test_a_moved_upstream_refuses_by_file_and_line_before_anything_is_built`
        during the extraction, 2026-09-08. The record belongs to whoever holds
        the current state, and after this change that is exactly one function.
        """
        state = ctx.state
        try:
            with self._held_awake() as note:
                if note:
                    yield note
                for number, stage in enumerate(stages, start=1):
                    self._check_cancel(ctx.cancel)
                    # WHERE THE USER IS, on its own line and never folded into
                    # the `--- <name>` marker. A format everything greps is not
                    # a place to add fields.
                    yield (
                        f"Step {number} of {len(stages)} "
                        f"({number * 100 // len(stages)}%): {stage.name}"
                    )
                    yield f"--- {stage.name}"
                    if stage.cancel_note:
                        # The spine says it, once, here (A4); no body yields its own.
                        yield stage.cancel_note
                    state = yield from self._run_one(stage, replace(ctx, state=state))
        except InstallerError as exc:
            # Recorded only into a state file that already exists — which, since
            # the claim moved ahead of stage one, is every install that started
            # from a folder of ours. The one shape with no file to write into is
            # the user's own checkout, and nothing should be written there.
            #
            # Not quite every: measured m910q 2026-09-05, a `clone-core` failure
            # has no file to write into either. That stage clones INTO the
            # server dir, and `git.py`'s two seams empty a destination with no
            # `.git` before cloning, so the claim `run()` wrote before stage one
            # is gone by the time this runs and the failure sentence is dropped
            # on the floor. Pinned in
            # `test_the_clone_that_fills_the_server_dir_takes_the_ownership_record_with_it`;
            # closing it means changing the clone, not this line.
            self._record_error(ctx.server_dir, state, str(exc))
            raise
        return state

    def stage_named(self, name: str) -> Stage:
        """This family's stage called `name`, or a loud failure.

        `rebuild_stages()` selects by name out of the family's own tuple rather
        than naming methods, so a family that renames or drops `build` gets an
        error here instead of a rebuild that quietly runs two stages and reports
        success. `Stage.name` is already load-bearing -- it is what the state
        file records -- so selecting on it adds no new coupling.
        """
        for stage in self.stages():
            if stage.name == name:
                return stage
        raise InstallerError(
            f"{self.entry.name} has no `{name}` stage, so this app cannot rebuild it. "
            f"That is a bug in this build, not something you did. Nothing was started."
        )

    def rebuild_stages(self) -> tuple[Stage, ...]:
        """What a rebuild runs: the build recipe, the compile, the containers, the wait.

        Deliberately NOT the install's tuple with the finished stages skipped.
        A rebuild is a different act with a different failure surface, and three
        of the install's stages would be actively wrong to re-enter here:
        `clone-*` fetches, `generate-compose` rewrites files a running server is
        using, and `import` reaches for a database that has a player's
        characters in it. None of the three has anything to do with "the
        worldserver does not contain the module I just installed".

        **`write-dockerfile` IS re-entered, and until 2026-09-09 it was not.**
        The exclusion above was written about files a running server reads, and
        the Dockerfile is not one: nothing but `docker build` ever opens it, and
        the stage is idempotent by text (`dockerfile.write()` leaves matching
        bytes alone, so the mtime does not move and the layer cache survives)
        and refuses a file this app did not write rather than replacing it. What
        the omission cost was measured on m910q the night of 2026-09-09
        (`pyplan/gates/tortoise-upgrade-m910q-2026-09-09/`): that install's
        Dockerfile had been rendered 2026-09-07 and the template was fixed
        2026-09-08 (`3a1ed6ee` -- both `FROM` lines to ubuntu:24.04 and the
        `INSERT IGNORE` rewrite one migration needs to apply at all), so the
        Rebuild button would have compiled the 22.04 recipe again and produced
        exactly the image the upgrade existed to replace. The upgrade only
        worked because the hand ran this stage first. A fix shipped in a
        template reaches an existing install through this line or through
        nothing.

        Selected by PRESENCE, not prepended: `dockerfile_dir` is None for
        AzerothCore because that checkout ships its own Dockerfile, so its
        family has no such stage and `stage_named()` would refuse every WotLK
        rebuild by name. `rebuild_confirmation()` reads the same fact off the
        entry, and `test_the_confirmation_promises_the_re_render_for_exactly_the_games_that_get_it`
        binds the two derivations across every shipped entry.

        `recreate` is this tuple's own stage rather than the install's `up`,
        and `docker.staged_up_argv()` holds the argument: `up -d` was measured
        to replace a container whose CONFIGURATION changed, and a rebuild
        changes neither the compose files nor the image tag. Never recorded --
        `up` is not either, and a rebuild must not be able to leave a state file
        claiming a stage the install's own resume would then skip. The re-render
        keeps the family's own `recorded=True` for the opposite reason: it is
        the install's stage, run with the install's body, and it really did
        happen.
        """
        recipe = (
            (self.stage_named(DOCKERFILE_STAGE),) if DOCKERFILE_STAGE in self.stage_names() else ()
        )
        return (
            *recipe,
            self.stage_named("build"),
            Stage("recreate", self.stage_recreate, recorded=False),
            self.stage_named("ready"),
        )

    def update_stages(self) -> tuple[Stage, ...]:
        """What an updates press runs: the database on its own, then the import stage.

        Two stages, selected by name out of the family's own tuple for
        `rebuild_stages()`'s reason, and the interesting half of this method is
        the four that are NOT here. `up` is the one that matters: this press is
        for a server somebody stopped in order to run it, and ending by starting
        the world would put back the very thing the press refuses to run
        alongside. `build`, `generate-compose` and `write-dockerfile` have
        nothing to do with three SQL files.

        `import` is exactly `_import`, which on an install the probe reads as
        finished runs the marker rule's own table, applies the phases the plan
        declares `rerun_on_marked` and returns (T11). This tuple does not
        re-implement that route; it is the second way in to it, the first being
        `engine.run()` through the CLI harness.

        **Recorded off**, and it is the family's own stage with the record
        taken away rather than a copy of the body. The install's `import` is
        recorded and rightly — it imported. This press reaches the same body and
        the body does not import, so a record written here would claim a stage
        that did not happen, and an install whose state file has no `import` in
        it (one made by the shell scripts, or one killed mid-install) would have
        its next resume skip the import on the strength of this press. The same
        rule `rebuild_stages()` applies to `recreate`, arrived at from the other
        side: there the stage is not the install's, here the outcome is not.

        **`cancel_note` swapped, not cleared**, and for the same reason
        `recorded` is turned off: the install's `import` carries
        `IMPORT_CANCEL_NOTE` because its `partial` arm calls `gate.reset()`
        before it re-imports, and that promise is false on this route —
        `_only_the_rerunnable_phases` never calls `stage_import()`, so
        `gate.reset()` is unreachable (T19, finding 2). An empty string looked
        like the fix and was rejected round 1: the spine still says a stage's
        note once, up front, and a Stop can still land mid-run inside
        `sqlplan.apply()`'s own between-run check — silence there is not the
        same as a true sentence, it is just no advance warning at all.
        `RERUN_CANCEL_NOTE` is the true one, and `_rerun_on_marked()` passes it
        into `sqlplan.apply()` by name so the mid-run raise says the same
        thing this heading does.
        """
        return (
            self.stage_named("start-db"),
            replace(self.stage_named("import"), recorded=False, cancel_note=RERUN_CANCEL_NOTE),
        )

    def update_files(self, ctx: StageContext) -> tuple[str, ...]:
        """What the re-runnable phases would stream into this install, in stream order.

        The confirmation's list, named the way the run's own log names each run
        (`PhaseRun.rel`: the path relative to the server dir, never an absolute
        one and never the SQL text). Empty here on the spine: a family whose
        import is a compose one-shot has no phase list to expand, and the button
        is not offered for it anyway — `update_phases()` reads the same absence
        off the catalog. The CMaNGOS family overrides it through the same
        `expand()` call its re-run route makes.
        """
        return ()

    def _update_context(self, server_dir: Path, cancel: threading.Event | None) -> StageContext:
        """The context both halves of an updates press run under.

        No state file is required and none is written. `rebuild()` refuses a
        folder with no `.yulon-install.json` because a rebuild is this app's
        claim on a folder it built; this press is the opposite case by design —
        the install it exists for may well be one the shell scripts made, which
        carries no state file and no marker row and is exactly the shape T11's
        route recognises as `populated` and complete. Neither stage is recorded,
        so nothing goes to disk; `_record_error()` writes only into a state file
        that already exists.
        """
        state = read_state(server_dir, valid=self.stage_names()) or InstallState(
            game_id=self.entry.id,
            install_id=self._install_id(server_dir),
            family=self.family,
        )
        return StageContext(
            server_dir=server_dir,
            client_dir=None,
            state=state,
            cancel=cancel,
            secrets=self.resolve_secrets(server_dir),
            updates_only=True,
        )

    def update_confirmation(self, options: InstallOptions | None = None) -> str:
        """The dialog's text for this install, with the file list read off the folder.

        Raises:
            InstallerError: the plan could not be expanded against this folder —
                most plausibly a clone that predates the directory the phases
                name, which `sqlplan._matches()` refuses by pattern and folder
                (T11's reviewer, note 4). Raised rather than swallowed into an
                empty list: an empty list under this dialog's words would be a
                confirmation for a press that applies nothing.
        """
        server_dir = self.server_dir(options or InstallOptions())
        phases = update_phases(self.entry)
        return updates_confirmation(
            self.entry,
            server_dir,
            [phase.name for phase in phases],
            self.update_files(self._update_context(server_dir, None)),
        )

    def update_databases(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
    ) -> Iterator[str]:
        """Apply the plan's re-runnable phases to an install that already exists. Yields live.

        The button T11's reviewer said was owed (note 1): the route that puts a
        file added to an install plan onto a server made before it existed was
        reachable only from `engine.run()`, which for a GUI user means never —
        the catalog tile greys to "Installed" once the app knows the folder, and
        `rebuild_stages()` excludes `import` on purpose.

        **The world is read before anything and again after the database is
        up**, and both readings refuse on anything but an explicit `False`.
        Owner answer 7 is the rule — no direct writes to `characters`/`world`
        while the server is running — and T11's reviewer recorded (note 3) that
        the route as it then stood wrote DDL into `tw_char` under a running
        world, because `stage_start_db` returns as soon as the database is up
        and nothing asked about the world. The second reading is T7's finding
        from the applier's side: `start_database()` waits for the container to
        report healthy, and the Server tab's Start is a button the same user can
        press inside that window.

        **The databases must already read as a finished import**, and that is
        the precondition the round-1 tuple was missing. It is enforced by
        `updates_only` on the context rather than by a check here, because a
        check here would be a SECOND probe: `import`'s body probes and branches,
        and between two probes the answer can differ — so the arm that drops
        every schema the plan names would still be reachable on the second
        answer. The family reads the flag before it calls `stage_import()` at
        all. What this method refuses is the other half, which no probe can see:
        an entry whose plan declares no re-runnable phase has nothing this route
        could apply, so it is refused rather than handed to a family that might
        not read the flag.

        Raises:
            InstallerError: the entry has no re-runnable phase, the world is up
                or unreadable, the databases do not read as a finished import,
                the plan could not be expanded, a stage failed, or the press was
                cancelled. The message is the sentence a user reads.
        """
        opts = options or InstallOptions()
        server_dir = self.server_dir(opts)
        if not update_phases(self.entry):
            raise InstallerError(
                f"{self.entry.name}'s install plan carries no phase meant to be re-applied to a "
                f"server that already exists, so there is nothing for this to apply. Nothing was "
                f"started. That is a fact about this game's plan, not about your install."
            )
        yield f"Applying pending database updates for {self.entry.name} in {server_dir}"
        yield UPDATES_OPENING_NOTE
        # FIRST, before the database is started and before a secret is resolved:
        # a press against a live world must leave the stack exactly as it found
        # it, and starting containers under a world this guard is about to
        # refuse would undo the guard's own advice on a stack the user stopped.
        self._refuse_writes_into_a_running_world(UPDATES_BUTTON_LABEL)
        self._check_cancel(cancel)
        planned = self.update_stages()
        # BY NAME, never positionally: T8 recorded what a positional wrapper
        # cost when a stage was later prepended to the rebuild's tuple, and this
        # tuple is one stage away from the same trap.
        guarded = [stage for stage in planned if stage.name == "import"]
        if not guarded:
            raise InstallerError(
                f"{self.entry.name} cannot be updated safely: its update tuple has no `import` "
                f"stage to guard, so the second reading of the world would never happen. That "
                f"is a bug in this build, not something you did. Nothing was started."
            )
        stages = tuple(
            (
                replace(stage, run=self._guard_then(stage, UPDATES_BUTTON_LABEL))
                if stage.name == "import"
                else stage
            )
            for stage in planned
        )
        ctx = self._update_context(server_dir, cancel)
        yield from self._staged(stages, ctx)

    def _guard_then(self, stage: Stage, button: str) -> Callable[[StageContext], Iterator[str]]:
        """`stage`, with the world read once more immediately before its body runs.

        `button` is the label the refusal tells the user to press again; see
        `_refuse_writes_into_a_running_world()`. Both presses that wrap a stage
        in this wrap the one that writes, so the reading is as young as it can
        be made without asking inside the family's own body.
        """

        def run(ctx: StageContext) -> Iterator[str]:
            self._refuse_writes_into_a_running_world(button)
            yield from stage.run(ctx)

        return run

    def _refuse_writes_into_a_running_world(self, button: str) -> None:
        """Owner answer 7 at this engine's own enforcement point. Fails closed.

        A second enforcement point for one rule, not a second rule, and the
        wording deliberately echoes `apply.Applier`'s and `docker.py`'s — *holds
        them in memory and writes back over whatever it finds. Press Stop* — so
        a user meets one rule and not three.

        `None` and a seam that raises are both refusals, because *could not ask*
        is not *not running*: `docker.container_state()` answers an empty state
        for a missing container and for a daemon that will not reply, and the
        one answer that would let DDL into a live world's tables is `False`.

        **`button` is the label of the press being refused**, and it is a
        parameter rather than a constant because two presses now come through
        here — T14's updates press and T19's adopt press — and each refusal
        ends by telling the user to press that press again. Named `updates`
        until the second caller arrived; a refusal from the adopt button
        reading *press "Apply pending database updates…" again* would be an
        instruction that does the wrong thing when followed, which is the
        defect T7's ticket is titled after rather than a cosmetic one.
        """
        container = self.entry.container_spec().world
        why = ""
        try:
            running: bool | None = self._seams.ask_world_running(container)
        except Exception as exc:  # noqa: BLE001 - any seam failure is one answer here
            logger.warning(f"could not tell whether {container} is running: {exc}")
            running, why = None, f"{type(exc).__name__}: {exc}"
        if running is False:
            return
        if running is None:
            # The remedy names Docker FIRST, because this branch's most likely
            # cause is not a running server: `container_state()` answers an
            # empty state both for a container that is not there and for a
            # daemon that will not reply, so "Stop the server" is advice that
            # cannot be followed on a machine where Docker is down — the same
            # unfollowable-instruction shape T7's ticket is titled after
            # (cold review of T14, round 1).
            raise InstallerError(
                f"Yu'lon could not tell whether {self.entry.name}'s world server is running "
                f"({why or 'the daemon gave no answer'}), and a running one holds these "
                f"databases in memory and writes back over whatever it finds in them. Nothing "
                f"was applied. Docker itself may be the thing that is not answering — it reads "
                f"a stopped container and a daemon that is down the same way — so check that "
                f"Docker is running, then press Stop on the Server tab if the server is up, and "
                f'press "{button}" again.'
            )
        raise InstallerError(
            f"{self.entry.name}'s world server is running, and it holds these databases in "
            f"memory and writes back over whatever it finds in them. Nothing was applied. "
            f"Press Stop on the Server tab, then press "
            f'"{button}" again — the database is started '
            f"on its own for it, and the world server stays down."
        )

    # -- adopting an install this app did not make (T19) ----------------------

    def adopt_gate(self, ctx: StageContext) -> ImportGate | None:
        """The gate an adopt press probes, checks and writes its marker through.

        `None` on the spine, which is a family saying it has no marker to
        adopt: AzerothCore imports through a compose one-shot and records
        nothing this app wrote, so there is no row for a person to consent to.
        The CMaNGOS family overrides it with the SAME gate its import stage
        uses — one question, one implementation, so the reading that offers the
        button and the reading the press takes cannot come from two probes that
        disagree.
        """
        return None

    def marker_row(self) -> MarkerRow | None:
        """The row an adopt press would write for this install, or None: no marker to write.

        The four facts the confirmation names, carried up out of the family
        because `native.py` cannot import `families.sqlplan` — see `MarkerRow`.
        `None` here and `None` from `adopt_gate()` are one fact said twice and
        are asserted to agree, because a family that could probe but not name
        the row would offer a dialog with a hole in it.
        """
        return None

    def write_import_marker(self, ctx: StageContext) -> None:
        """Write the completion marker for this install, through the family's own writer.

        The spine cannot: the row is `sqlplan.write_marker()`'s, and this module
        may not import that one. The refusal here is what a family that never
        learned to adopt says, and it is a bug in this build rather than a state
        of the machine.

        Raises:
            InstallerError: this family has no marker writer, or the write failed.
        """
        raise InstallerError(
            f"{self.entry.name} keeps no completion marker this app can write, so there is "
            f"nothing to adopt. That is a fact about this game's install plan, not about your "
            f"install. Nothing was written."
        )

    def adopt_stages(self) -> tuple[Stage, ...]:
        """What an adopt press runs: the database on its own, then the one row.

        Two stages, and the interesting half is again what is NOT here.
        `update_stages()` selects the family's `import` stage; this one does
        not, and must not: `import` is the five-branch table, and this press
        consented to a single INSERT. So the second stage is this method's own,
        with a body no install ever runs.

        `start-db` IS the family's own, selected by name for `rebuild_stages()`'s
        reason — the probe reaches the databases through `docker exec`, so with
        nothing running it answers `unreadable` and this press would refuse on a
        machine with nothing wrong with it.

        **Neither is recorded.** `start-db` never is. `adopt` must not be,
        because a state file naming a stage no install tuple contains is a
        record the next resume would have to interpret, and this press does not
        import: the row it writes says the PLAN finished, which is exactly the
        claim the state file's `import` entry would make on the strength of
        something that never happened.

        No `cancel_note`: a stop lands either before the one statement or after
        it, and `sqlplan.write_marker()` sends both of its lines in one script
        to one client. There is no half-written state to warn about, and a note
        promising one would be a sentence about a route this tuple does not
        have.
        """
        return (
            self.stage_named("start-db"),
            Stage("adopt", self.stage_adopt, recorded=False),
        )

    def stage_adopt(self, ctx: StageContext) -> Iterator[str]:
        """Probe, refuse, write the one row, probe again. The whole of the press's body.

        The branch table, in the order a wrong answer costs:

        * **`imported`** — these databases already carry the row. Refused, not
          skipped silently: a press that reported success having written
          nothing would teach the user that the button is decorative.
        * **anything but `populated`** — `absent`, `partial` and `unreadable`
          are databases with nobody's data in them, or none this app could
          read. Adopting is a claim about an import somebody already made; over
          these it would be a claim about nothing.
        * **a gap** — `populated` says a `player_data` table has rows in it, and
          it short-circuits on the first one. It does NOT say the plan's other
          schemas exist or that its other tables are there, and this press
          writes a row saying the whole plan finished. So the gate is asked once
          more, for presence only (`MarkerGate.adoption_gaps()`), and a gap is a
          refusal naming it.

        **Presence and never completeness**, and that is the ticket's own
        conclusion rather than a shortcut. Three rounds tried to derive "this
        dump finished" from the plan and each found the next layer of inference
        beneath the last; the owner's answer was to stop inferring and let the
        person say so. `ADOPT_CONSEQUENCE` is where the claim changes hands, and
        these checks are only the floor under it — they keep the row off a
        database that is plainly not the thing being claimed.

        **The probe runs again after the write**, and it is the only proof this
        press has that anything happened: the writer answers by not raising,
        which is the client's exit status and not a reading of the row. A
        re-probe that does not say `imported` is a write that did not land where
        the gate looks, and it is raised rather than reported — the alternative
        is a green press and a button that goes on offering itself.
        """
        gate = self.adopt_gate(ctx)
        row = self.marker_row()
        if gate is None or row is None:
            raise InstallerError(
                f"{self.entry.name} keeps no completion marker this app can write, so there is "
                f"nothing to adopt. That is a fact about this game's install plan, not about "
                f"your install. Nothing was written."
            )
        seen = gate.probe()
        yield f"The databases read as {seen.state}: {seen.detail}"
        if seen.state == "imported":
            raise InstallerError(
                f"{self.entry.name}'s databases already carry Yu'lon's marker ({seen.detail}), "
                f"so there is nothing to adopt — they already read as a finished import. "
                f"Nothing was written. If you meant to apply the files this install plan has "
                f'gained since, press "{UPDATES_BUTTON_LABEL}" instead.'
            )
        if seen.state != "populated":
            raise InstallerError(
                f"{self.entry.name}'s databases do not hold an import to adopt ({seen.state}: "
                f"{seen.detail}). This button is for databases somebody has already imported "
                f"and played on, and says so on their behalf; it does not make them. Nothing "
                f"was written."
                + (
                    " The state above is unreadable, which reads the same whether the database "
                    "is down or Docker is not answering, so check that Docker is running "
                    "before anything else."
                    if seen.state == "unreadable"
                    else " Install this server, or finish the install of this folder, instead."
                )
            )
        try:
            gaps = gate.adoption_gaps()
        except docker.DockerCommandError as exc:
            raise InstallerError(
                f"{self.entry.name}'s databases could not be asked which of this plan's tables "
                f"they hold ({exc}), so nothing was written. Adopting says these databases are "
                f"a finished import, and that is not a thing to say about a database that would "
                f"not answer."
            ) from exc
        if gaps:
            raise InstallerError(
                f"{self.entry.name}'s install plan names things these databases do not have: "
                f"{'; '.join(gaps)}. A marker row here would say this plan finished over them, "
                f"which it plainly did not. Nothing was written."
            )
        yield (
            f"Writing one row into `{row.schema}`.`{row.table}`: this install plan "
            f"({row.plan_hash}) is recorded as finished. Nothing else is run."
        )
        # The reading that counts is the one immediately before the write: the
        # two the wrapper took are older than the probe, the gap queries and the
        # yield above, and a consumer paused at that yield leaves the world free
        # to start in between (Codex on the adopt press). T25's boundary rule,
        # said again: the check sits at the destructive statement, not at the
        # stage's entry.
        self._refuse_writes_into_a_running_world(ADOPT_BUTTON_LABEL)
        self.write_import_marker(ctx)
        after = gate.probe()
        if after.state != "imported":
            raise InstallerError(
                f"The marker row was written, but {self.entry.name}'s databases still read as "
                f"{after.state} ({after.detail}). Something wrote the row somewhere this app "
                f"does not look for it, so nothing can be established either way — do not treat "
                f"this install as adopted."
            )
        yield f"These databases now read as {after.state}: {after.detail}"
        yield (
            f'"{UPDATES_BUTTON_LABEL}" can now apply the files this install plan has gained '
            f"since this server was made. No import ran and nothing was cleared."
        )

    def adopt_state(self, options: InstallOptions | None = None) -> docker.ImportState:
        """What these databases read as, for the adopt button's enabling rule. NEVER raises.

        The tab asks this to decide whether to offer the control at all, which
        is why every way of not knowing has to come back as `unreadable`: a
        password file that cannot be read, a catalog the gate refuses to be
        built from, a database that will not answer. `unreadable` greys the
        button, and the one outcome that must never follow from a question
        nobody answered is a control that writes a marker row appearing.

        The same discipline `controller_wow_tortoise.repair.import_state()`
        takes for the Repair button, and for the same reason: this is called
        from a status path, which has nowhere to put an exception.

        A family with no marker answers `unreadable` too. It is the honest
        answer — nothing here knows what state such an install is in — and the
        view greys the control on the route being `None` before it ever asks.
        """
        server_dir = self.server_dir(options or InstallOptions())
        try:
            gate = self.adopt_gate(self._update_context(server_dir, None))
        except Exception as exc:  # noqa: BLE001 - a status path has nowhere to put one
            logger.warning(f"could not build the import gate for {server_dir}: {exc}")
            return docker.ImportState(
                "unreadable",
                f"this install's databases could not be asked what state they are in "
                f"({type(exc).__name__}: {exc})",
            )
        if gate is None:
            return docker.ImportState(
                "unreadable", f"{self.entry.name} keeps no completion marker this app can read"
            )
        try:
            return gate.probe()
        except Exception as exc:  # noqa: BLE001 - `probe()` promises not to; this is the boundary
            logger.warning(f"the import probe raised for {server_dir}: {exc}")
            return docker.ImportState(
                "unreadable",
                f"the databases could not be asked what state they are in "
                f"({type(exc).__name__}: {exc})",
            )

    def adopt_confirmation(self, options: InstallOptions | None = None) -> str:
        """The dialog's text for this install. Asks the databases nothing.

        Unlike `update_confirmation()`, which expands the plan against the
        folder and can refuse there, this one is pure: the row is read off the
        plan and the folder off the options. What could refuse — the marker
        already present, the databases not populated, a table missing — is read
        by the PRESS, after the person has agreed, because every one of those
        readings costs a `docker exec` and the answer can change between the
        dialog and the press anyway.

        Raises:
            InstallerError: this family keeps no marker, so there is no row to
                describe. The view never offers the control there.
        """
        row = self.marker_row()
        if row is None:
            raise InstallerError(
                f"{self.entry.name} keeps no completion marker this app can write, so there is "
                f"nothing to adopt. That is a fact about this game's install plan, not about "
                f"your install."
            )
        return adopt_confirmation(self.entry, self.server_dir(options or InstallOptions()), row)

    def adopt_as_imported(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
    ) -> Iterator[str]:
        """Record that these databases are a finished import, on the person's word. Yields live.

        The press the owner chose after three rounds of the alternative. T14's
        button refuses an install with no marker row, and the install it was
        built for — the owner's Tortoise server, made by the shell scripts —
        is exactly that: `populated`, complete in every way a person can see,
        and unreachable by the one button that would put the files it is
        missing onto it. The probe cannot prove that import finished, so this
        press does not try; it writes the row a person consented to.

        **The world is read before anything and again after the database is
        up**, `update_databases()`'s two readings for `update_databases()`'s
        reasons: a running worldserver holds these tables in memory and writes
        back over whatever it finds in them, and the window between the two is
        the health wait, inside which the Server tab's Start is one click away.
        Both refuse on anything but an explicit `False`.

        **The database is put back down if this press was what started it**, and
        the reading that decides is taken BEFORE the first stage. This is a
        press a user makes on a server they stopped, and leaving its database up
        afterwards would be this app changing something it was not asked to
        change. `None` from that reading — could not tell — leaves the container
        alone, which is the same fail-closed direction the world reading takes
        pointed at a smaller question.

        Raises:
            InstallerError: the entry has no re-runnable phase, the world is up
                or unreadable, the databases already carry the marker, do not
                read as `populated`, or are missing something the plan names,
                the writer failed, or the press was cancelled. The message is
                the sentence a user reads.
        """
        opts = options or InstallOptions()
        server_dir = self.server_dir(opts)
        if not update_phases(self.entry):
            raise InstallerError(
                f"{self.entry.name}'s install plan carries no phase meant to be re-applied to a "
                f"server that already exists, so adopting these databases would buy nothing: "
                f"there is no press that would then do anything it cannot do now. Nothing was "
                f"started. That is a fact about this game's plan, not about your install."
            )
        yield f"Adopting {self.entry.name}'s databases in {server_dir} as a finished import"
        yield ADOPT_OPENING_NOTE
        # FIRST, before the database is started and before a secret is resolved,
        # for the reason `update_databases()` reads it first: a press against a
        # live world must leave the stack exactly as it found it, and starting
        # containers under a world this guard is about to refuse would undo the
        # guard's own advice on a stack the user stopped.
        self._refuse_writes_into_a_running_world(ADOPT_BUTTON_LABEL)
        self._check_cancel(cancel)
        # BEFORE the first stage, so what is put back is what was found. Read
        # here rather than off `start_database()`'s own return — which does say
        # whether it had to start the container — because `stage_start_db()` is
        # the family's stage and discards it, and a copy of that stage taken to
        # keep the answer would be a second spelling of the one primitive T7
        # wired.
        container = self.entry.container_spec().db
        try:
            was_up: bool | None = self._seams.ask_db_running(container)
        except Exception as exc:  # noqa: BLE001 - any seam failure is one answer here
            logger.warning(f"could not tell whether {container} is running: {exc}")
            was_up = None
        planned = self.adopt_stages()
        # BY NAME, never positionally: T8 recorded what a positional wrapper
        # cost when a stage was later prepended to the rebuild's tuple.
        if not [stage for stage in planned if stage.name == "adopt"]:
            raise InstallerError(
                f"{self.entry.name} cannot be adopted safely: its adopt tuple has no `adopt` "
                f"stage to guard, so the second reading of the world would never happen. That "
                f"is a bug in this build, not something you did. Nothing was started."
            )
        stages = tuple(
            (
                replace(stage, run=self._guard_then(stage, ADOPT_BUTTON_LABEL))
                if stage.name == "adopt"
                else stage
            )
            for stage in planned
        )
        ctx = self._update_context(server_dir, cancel)
        try:
            yield from self._staged(stages, ctx)
        except BaseException:
            # Logged and not yielded: a generator whose consumer has abandoned
            # it may not yield again, and a press that failed has already said
            # why. The container still goes back down — putting back what this
            # press started is not conditional on the press succeeding.
            if was_up is False:
                logger.info(self._stop_the_database_again(container))
            raise
        if was_up is False:
            yield self._stop_the_database_again(container)

    def _stop_the_database_again(self, container: str) -> str:
        """Put the database back down, and say so. Never raises.

        One spelling for both exits of `adopt_as_imported()` — the successful
        one, which yields this sentence into the panel, and the failed one,
        which logs it — because the two must not be able to stop different
        things or say different words about it.

        A failure to stop is a sentence and not a refusal. The row is already
        written by then on the successful path, and raising here would report a
        press that did its work as having failed; on the failed path there is
        already a refusal in flight and this must not replace it.
        """
        try:
            self._seams.stop_db([container])
        except Exception as exc:  # noqa: BLE001 - a tidy-up may not become the failure
            logger.warning(f"could not stop {container} again: {exc}")
            return (
                f"The database could not be stopped again ({type(exc).__name__}: {exc}), so it "
                f"is still running. Nothing else was left behind; Stop on the Server tab takes "
                f"it down."
            )
        return (
            "The database is stopped again: it was down when this began, and this press was "
            "what started it."
        )

    def stage_recreate(
        self, ctx: StageContext, *, before_replace: Callable[[], None] | None = None
    ) -> Iterator[str]:
        """Replace the long-running containers so the binary just built is the one running.

        The stage the whole feature turns on. Everything above it can be
        perfect -- an hour of compiler output, four fresh images -- and if the
        containers created before the rebuild keep running, the user logs back
        in to exactly what they had, which is the report this control exists to
        answer.

        **The preflight is split off in front of the destructive command (T25 round 2).**
        `docker_ready()` is the one question this method can answer with certainty before
        doing anything: not reachable means the daemon was never asked to replace anything,
        so refusing HERE, before the first yield, is the one place left where "nothing was
        touched" is still true rather than assumed. Past it, `self._seams.recreate()` is
        ONE `compose up --force-recreate` for every service this install has, and a
        `DockerCommandError` from it does not say which of them it got to before failing --
        compose can recreate two services and fail the third, or recreate all three and
        then fail the running-container check. `before_replace` is the boundary the
        wrapper in `rebuild()` needs: it is called synchronously, after every argument is
        prepared and immediately before the compose command is issued, so a failure
        anywhere in front of it -- the readiness probe, the progress yield the caller is
        suspended at, `container_spec()` -- is still "nothing was touched", and only a
        failure from the command itself is not. (Round 2 read the first yield as that
        boundary; the round-2 review pointed out the daemon can go away between the
        probe and the call, and a consumer can stop at the yield, so the flag moved to
        the call.)
        """
        if not self._seams.docker_ready():
            raise InstallerError(
                "Docker is not answering, so the containers were not replaced -- the server "
                "you have is still the one that was running before this rebuild. Nothing was "
                "touched. Check the docker daemon is up, then press Rebuild again."
            )
        yield "Replacing the running containers so the new build is what starts."
        spec = self.entry.container_spec()
        if before_replace is not None:
            before_replace()
        try:
            self._seams.recreate(spec, ctx.server_dir)
        except docker.DockerCommandError as exc:
            raise InstallerError(
                f"The server was rebuilt, but its containers could not be replaced, so the "
                f"old build is still what is running: {exc}"
            ) from exc
        yield "The containers were replaced."

    def rebuild(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
    ) -> Iterator[str]:
        """Recompile this install and restart it on what was compiled. Yields output live.

        The action `apply.ApplyReport.rebuild_required` has named since it was
        written -- "worldserver REBUILD required before this takes effect",
        printed by the Modules tab after 20 of the 41 shipped manifests -- and
        which nothing in `yulon/ui/` offered until 2026-09-08.

        **Why re-pressing Install was never it.** `stage_build()` skips the
        compile when the state file records a build and the daemon holds every
        image, and `composegen.image_tag()` is derived from the FOLDER, so
        adding a module changes no tag and the images all still exist. Measured
        through this app's own predicates on yulon-ubuntu: `state.has("build")`
        True, `built_images()` True, `build_would_be_skipped()` True. A user who
        pressed Install again got a few seconds of output and no change, which
        is very probably what "i used the this thing to rebuild the server but
        nothing changes when i log back in" (2026-09-07) is a report of.

        **What it deliberately does not do: SQL.** One press does the compile
        and the restart, and touches no database. Three reasons, in the order
        they decided it:

        1. a database write behind a confirmation whose entire subject is a
           compile is a write nobody agreed to, and it would leave no way to ask
           for the compile alone -- which is what somebody re-testing a build
           wants;
        2. the databases here hold a player's characters. Every other path in
           this app that writes to them (`repair_import`, `restore`) is a
           separate, separately-confirmed action, and one of them arms on two
           presses;
        3. the reported reason it would be unnecessary is CARRIED, not
           verified here: AzerothCore applies a module's SQL through its own
           updater for the modules in `AC_MODULES_LIST`, which CMake bakes in
           at configure time, so a module compiled in by this rebuild is in
           that list from this build onward. Nothing in this repository
           measures that, and nothing in this function depends on it -- the
           closing line tells the user to look rather than promising it
           happened.

        Raises:
            InstallerError: any refusal (see `_refuse_unless_rebuildable`), any
                stage that failed, or a cancel. The message is the sentence a
                user reads.
        """
        opts = options or InstallOptions()
        server_dir = self.server_dir(opts)
        state = self._refuse_unless_rebuildable(server_dir)
        planned = self.rebuild_stages()
        renders = any(stage.name == DOCKERFILE_STAGE for stage in planned)
        # Read BEFORE the first stage and only for the families that have one,
        # because the promise in the opening note is about this: a press stopped
        # before the containers are replaced leaves the server exactly as it is,
        # and a rewritten recipe left on disk is not "exactly as it is" -- the
        # next build would compile something the user never confirmed.
        ground = self._recipe_ground(server_dir) if renders else {}
        yield f"Rebuilding {self.entry.name} in {server_dir}"
        yield rebuild_opening_note(renders_dockerfile=renders)
        self._check_cancel(cancel)
        ctx = StageContext(
            server_dir=server_dir,
            client_dir=opts.client_dir,
            state=state,
            cancel=cancel,
            secrets=self.resolve_secrets(server_dir),
            force_build=True,
        )
        # Two facts the failure path needs and a stage cannot return: whether
        # the compile FINISHED (the live tags name the new image from then on)
        # and whether the containers were TOUCHED (from then on the old build
        # is not what is running). Read off the stages as they pass rather
        # than guessed from the exception's wording.
        #
        # `touched` was set on ENTRY to `recreate()` until T25 round 1 found what
        # that costs: `stage_recreate()` can raise before `_seams.recreate()` ever
        # runs (the daemon unreachable) or before it returns, and with `touched`
        # already True the `except` below in `rebuild()` skipped
        # `_put_recipe_back()` -- the recipe just re-rendered stayed on disk
        # though no container had moved -- and `_restore_rollback` took its
        # second `stage_recreate()` call for a server nothing had touched the
        # first time. Round 1 moved the assignment to AFTER `stage_recreate()`
        # returns, beside `built` -- which round 2's review then found the other
        # side of: `self._seams.recreate()` is one `compose up --force-recreate`
        # for every service, and a `DockerCommandError` from it does not say
        # whether it recreated two of three containers before failing the
        # third. Waiting for a clean RETURN to believe anything moved reads a
        # partial replacement as untouched, exactly backwards.
        #
        # The boundary that survives both is `stage_recreate()`'s own: its
        # `docker_ready()` preflight is the one question answerable BEFORE the
        # destructive call, so `touched` is set once its generator reaches the
        # first thing IT yields past that check -- not on entry to the wrapper,
        # and not on a clean return, but at the point past which `stage_recreate`
        # can no longer fail having changed nothing. `next()` rather than `yield
        # from` because splitting the first yield from the rest is the only way
        # to observe that point from here.
        built = False
        touched = False

        def build(stage_ctx: StageContext) -> Iterator[str]:
            nonlocal built
            yield from self.stage_build(stage_ctx)
            built = True

        def recreate(stage_ctx: StageContext) -> Iterator[str]:
            def mark_touched() -> None:
                nonlocal touched
                touched = True

            # `touched` flips inside `stage_recreate()`, synchronously, immediately
            # before the compose command is issued -- not at the stage's first yield
            # (round 2), which left a window between the readiness probe and the
            # command where a failure read as a partial replacement.
            yield from self.stage_recreate(stage_ctx, before_replace=mark_touched)

        # BY NAME, and it was positional (`first, second, *rest`) until
        # 2026-09-09. That was true of a tuple beginning with `build`, and T8
        # put the re-render in front of it: both wrappers would have slid one
        # stage early, so `built` would be set by a Dockerfile being written and
        # `touched` before the compiler had started -- and a compile that then
        # failed would "restore" tags it never moved and recreate the containers
        # of a server that is still running the build it had. A tuple's shape is
        # not a place to keep a fact two closures depend on.
        #
        # BEFORE the rollback is kept, so the refusal below can say nothing was
        # started and mean it: `_keep_rollback()` tags four images.
        wrappers: dict[str, Callable[[StageContext], Iterator[str]]] = {
            "build": build,
            "recreate": recreate,
        }
        stages = tuple(
            replace(stage, run=wrappers.pop(stage.name)) if stage.name in wrappers else stage
            for stage in planned
        )
        if wrappers:
            # `stage_named()`'s refusal covers a family with no `build` at all;
            # this covers the other half -- a tuple carrying one under another
            # name, or a `recreate` this method stopped adding -- because what
            # that produces otherwise is not a missing stage but a rollback that
            # silently never fires.
            raise InstallerError(
                f"{self.entry.name} cannot be rebuilt safely: its rebuild has no "
                f"`{sorted(wrappers)[0]}` stage to watch, so a failure could not be rolled "
                f"back. That is a bug in this build, not something you did. Nothing was "
                f"started."
            )
        refs = self.built_image_refs(ctx)
        kept = yield from self._keep_rollback(ctx, refs)
        try:
            state = yield from self._staged(stages, ctx)
        except InstallerError as exc:
            # FIRST, and outside every rollback branch below, because it is not
            # about the images at all: `touched` False is the whole window the
            # opening note makes its promise about, and in it the recipe on disk
            # has to be the one the running build was made from. After the
            # containers are replaced the promise has already been spent and
            # `_restore_rollback` owns what happens next.
            if not touched:
                yield from self._put_recipe_back(ctx, ground)
            if not kept:
                raise
            if not built:
                # A compile that failed or was stopped leaves the live tags on
                # the build that is running: the second name is a duplicate.
                self._let_go(kept)
                raise
            message = yield from self._restore_rollback(ctx, refs, kept, touched, str(exc))
            self._record_error(server_dir, ctx.state, message)
            raise InstallerError(message) from exc
        self._let_go(kept)
        logger.info(f"rebuild of {self.entry.id} finished")
        self._clear_error(server_dir, state)
        yield REBUILD_CLOSING_NOTE
        yield f"{self.entry.name} was rebuilt and is running in {server_dir}"

    def _keep_rollback(
        self, ctx: StageContext, refs: Sequence[str]
    ) -> Generator[str, None, tuple[str, ...]]:
        """Give every image the compile will overwrite its `-rollback` name, or say why not.

        One answer and three refusals, all before the compile (owner answer 2:
        ALWAYS keep a rollback):

        * the images are all there and all tagged -- the rollback is kept;
        * the images are there and docker will not tag one -- refused, with
          the tags already made taken back, because building anyway would
          overwrite the only copy of the running build with the rollback the
          owner asked for unkept. Docker's words are in the sentence:
          "read-only layer store" is a different evening from "no such image";
        * the images are not all there -- refused, and the sentence names the
          button that repairs that: Install's resume rebuilds missing images,
          this one only replaces present ones;
        * docker will not say whether they are there -- refused. `None` is
          "could not ask", and destructive work on an unanswered question
          fails closed.

        Until the adversarial review of 2026-09-08 the last two went ahead
        with a sentence saying no rollback was kept, which contradicted the
        confirmation the user had just agreed to and made the one press with
        no safety net look exactly like the others.

        Returns the rollback names kept -- never empty on a return.
        """
        present = self._seams.images_built(refs)
        if present is None:
            raise InstallerError(
                "Docker would not say whether this install's images exist, so the build you "
                "have now could not be kept as a rollback and nothing was compiled over it. "
                "Nothing was started. Check the docker daemon is up, then press Rebuild again."
            )
        if not present:
            raise InstallerError(
                "This install's images are not all on the daemon under their tags, so there "
                "is no build to keep as a rollback, and a rebuild does not run without one. "
                "Nothing was started. Press Install on this folder instead: its resume "
                "rebuilds the missing images, and Rebuild works from then on."
            )
        kept: list[str] = []
        for ref in refs:
            back = ref + ROLLBACK_TAG_SUFFIX
            problem = self._seams.tag_image(ref, back)
            if problem:
                self._let_go(kept)
                raise InstallerError(
                    f"The build you have now could not be kept as a rollback ({problem}), so "
                    f"nothing was compiled over it. Nothing was started; the server you have "
                    f"is running exactly as it was."
                )
            kept.append(back)
        yield (
            f"Kept the build you have now as a rollback ({len(kept)} images tagged "
            f"{ROLLBACK_TAG_SUFFIX}). If the new build does not come up it is put back "
            f"automatically."
        )
        return tuple(kept)

    def _restore_rollback(
        self,
        ctx: StageContext,
        refs: Sequence[str],
        kept: Sequence[str],
        touched: bool,
        failure: str,
    ) -> Generator[str, None, str]:
        """Put the old build back after a compile that finished and a server that did not.

        Returns the sentence the user reads -- the original failure first,
        then what was done about it and how that went -- because the panel
        shows one message and a rollback that hides the failure it answered
        would be reporting a success that nobody asked for.

        **The world's last words are read BEFORE the containers are replaced**,
        because the recreate that brings the old build back removes the
        failed container and its log with it; after that, "show what the log
        said" (owner answer 2) could only point at a log that is gone.

        `touched` False is the compile-finished, containers-not-yet-replaced
        window: the running server IS the old build, only the tags name the
        new one. Then the tags go back and nothing is restarted, which is what
        `REBUILD_OPENING_NOTE` promised a stop before the replacement costs.
        """
        spec = self.entry.container_spec()
        last_words = ""
        if touched:
            printed = self._seams.world_output(spec).text.strip().splitlines()
            last_words = "\n".join(printed[-5:])
        yield "Putting the build from before this rebuild back."
        # The new build gets its own name FIRST, so a retag that fails part-way
        # can be undone onto it (`FAILED_TAG_SUFFIX`). If even that fails,
        # nothing has moved yet and the sentence below is already true.
        failed = [ref + FAILED_TAG_SUFFIX for ref in refs]
        named: list[str] = []
        for ref, name in zip(refs, failed, strict=True):
            problem = self._seams.tag_image(ref, name)
            if problem:
                self._let_go(named)
                return (
                    f"{failure} Putting the build from before this rebuild back was not "
                    f"attempted, because the new build could not be given a name to undo "
                    f"onto ({problem}); the tags still name the new build, all of them. The "
                    f"old images are on the daemon under their {ROLLBACK_TAG_SUFFIX} tags."
                )
            named.append(name)
        moved: list[str] = []
        for ref, back in zip(refs, kept, strict=True):
            problem = self._seams.tag_image(back, ref)
            if problem:
                undone = [r for r in moved if not self._seams.tag_image(r + FAILED_TAG_SUFFIX, r)]
                mixed = [r for r in moved if r not in undone]
                self._let_go(named)
                if mixed:
                    return (
                        f"{failure} Putting the build from before this rebuild back failed "
                        f"part-way ({problem}) and undoing it failed too, so the tags are "
                        f"MIXED: {', '.join(mixed)} name the old build and the rest name the "
                        f"new one. Do not start this server until they agree; the old images "
                        f"are under their {ROLLBACK_TAG_SUFFIX} tags."
                    )
                return (
                    f"{failure} Putting the build from before this rebuild back failed "
                    f"({problem}), and the {len(undone)} tag(s) already moved were moved back, "
                    f"so the tags still name the new build, all of them. The old images are "
                    f"on the daemon under their {ROLLBACK_TAG_SUFFIX} tags."
                )
            moved.append(ref)
        self._let_go(named)
        if not touched:
            self._let_go(kept)
            return (
                f"{failure} The tags were put back to the build that is running, and no "
                f"container was replaced."
            )
        said = (
            f" Before it was replaced, {spec.world} had printed:\n{last_words}"
            if last_words
            else ""
        )
        # What an image rollback does NOT put back, said in the same sentence
        # as the restore: the incident this answers (Tortoise, 2026-09-08) was
        # the new binary's own updater migrating the world database at
        # startup. The owner chose the image rollback knowing that; the user
        # is told it here rather than left to find out (adversarial review).
        database = (
            "\nWhat the new build wrote into the database on its first start, if anything, "
            "is NOT put back by this -- the lines above say whether its updater ran -- so "
            "the old build is running on the database as the new one left it."
        )
        try:
            yield from self.stage_recreate(ctx)
            # The `-failed` names again, and the second attempt is the one that
            # can work. The first ran while the containers made FROM the new
            # build were still there, and docker refuses to remove a name whose
            # image a container references -- measured on yulon-ubuntu2 on the
            # first live restore (2026-09-09): two of the four removals came
            # back `conflict: unable to delete ... container 31769acad1c4 is
            # using its referenced image`, and nothing asked again, so a broken
            # build stayed on the daemon for ever under a name this module
            # documents as transient. The recreate above is exactly what frees
            # them. Both calls are kept because the failure paths ABOVE this
            # one never reach a recreate, and a name docker already let go is a
            # no-op here (`remove_image` treats "no such image" as done).
            self._let_go(named)
            yield from self.wait_for_ready(ctx, self._native().ready)
        except InstallerError as second:
            return (
                f"{failure} The build from before this rebuild was put back, but it did not "
                f"report ready either: {second}{said}{database}"
            )
        self._let_go(kept)
        return (
            f"{failure} The build from before this rebuild was put back and is running "
            f"again.{said}{database}"
        )

    def _let_go(self, kept: Sequence[str]) -> None:
        """Remove the rollback names. A refusal is logged by the seam and changes nothing here."""
        for back in kept:
            self._seams.remove_image(back)

    def _recipe_ground(self, server_dir: Path) -> dict[str, RecipeGround]:
        """The build-recipe files as found. Bytes, `None` for absent, `UNREADABLE` for neither.

        `_let_go`'s counterpart for the disk: `_keep_rollback` keeps the images
        the compile is about to overwrite, and this keeps the two files the
        re-render is about to overwrite. Both are taken before anything runs and
        both exist so that a press stopped early leaves the machine as it was.

        Bytes, not text: what has to go back is what was there, and a read/write
        round trip through `str` would rewrite a file's line endings on Windows
        and call it a restore.

        **THREE answers, and it had two.** Until round 2 of T8's review every
        `OSError` became `None`, and `None` means "there was no file, so remove
        the one the re-render made". Traced by the cold reviewer: a `Dockerfile`
        this process cannot READ (a permission, a directory in its place)
        answered `None`, `write-dockerfile` then refused it as `UNREADABLE`
        saying "Nothing was touched", and the restore deleted the user's file
        and reported "put back exactly as it was". On Windows the `unlink`
        raised `PermissionError` instead -- not an `InstallerError`, so it flew
        past `_let_go` and left the rollback tags on the daemon. "Could not ask"
        is not "was not there", which is the same rule `_keep_rollback` applies
        to `images_built()` returning `None` one method above.

        The import is local because `families/dockerfile.py` imports `Secrets`
        from this module; `installer.py` breaks the same cycle the same way. The
        names are taken from it rather than restated here, so a rename is one
        edit and not a silent drift into restoring a file nobody writes.
        """
        from yulon.catalog.families import dockerfile

        ground: dict[str, RecipeGround] = {}
        for name in (dockerfile.DOCKERFILE, dockerfile.DOCKERIGNORE):
            try:
                ground[name] = (server_dir / name).read_bytes()
            except FileNotFoundError:
                ground[name] = None
            except OSError as exc:
                logger.warning(f"could not read {server_dir / name} before the rebuild: {exc}")
                ground[name] = UNREADABLE_RECIPE
        return ground

    def _put_recipe_back(
        self, ctx: StageContext, ground: Mapping[str, RecipeGround]
    ) -> Iterator[str]:
        """Undo the re-render: the files as they were, and the record that claimed them.

        Called on every failure and every cancel that happens before the
        containers are replaced, which is exactly the window
        `rebuild_opening_note()` promises about. Silent when nothing moved --
        the common case, since the stage writes only on a difference -- so an
        install that was already current gets no line about a restore that
        restored nothing.

        The state record goes back with the files, because the two are one
        claim: `write-dockerfile` is a recorded stage, and leaving its name in
        `completed` after putting its output back would tell the install's own
        resume that a stage ran whose effect is no longer on disk. For every
        install made by a version that had the stage this is already a no-op --
        the name is in `completed` from the install -- and the case it is for is
        the folder made before it, which is every install on a disk today.

        `last_error` is deliberately NOT restored: `_staged` has just recorded
        why this press stopped, and that record is true.

        **Nothing in here may raise.** It runs first in `rebuild()`'s `except`,
        ahead of `_let_go` and `_restore_rollback`, so an `OSError` escaping
        this body would take the rollback with it and leave the `-rollback` tags
        on the daemon for ever -- the failure this method exists to prevent,
        arriving through the method itself. Every filesystem call is caught and
        turned into a sentence naming the file, and the press then goes on to
        put its images back.
        """
        moved = False
        left: list[str] = []
        for name, was in ground.items():
            path = ctx.server_dir / name
            if isinstance(was, _UnreadableRecipe):
                # `isinstance` and not `is UNREADABLE_RECIPE`, which is the same
                # test at runtime and does not NARROW: mypy left `bytes |
                # _UnreadableRecipe` on the write below, which is the type error
                # that says this branch is load-bearing.
                #
                # Never unlinked and never written: this method does not know
                # what was in it, and a file it could not read is not a file
                # this press is entitled to remove.
                left.append(f"{path} could not be read when this rebuild started")
                continue
            try:
                if was is None:
                    if path.exists():
                        path.unlink()
                        moved = True
                    continue
                try:
                    if path.read_bytes() == was:
                        continue
                except OSError:
                    pass
                path.write_bytes(was)
                moved = True
            except OSError as exc:
                left.append(f"{path} could not be put back ({exc})")
        if moved:
            try:
                now = read_state(ctx.server_dir, valid=self.stage_names())
                if now is not None and now.completed != ctx.state.completed:
                    write_state(ctx.server_dir, replace(now, completed=ctx.state.completed))
            except OSError as exc:
                logger.warning(f"could not put the stage record back after a rebuild: {exc}")
        if left:
            yield (
                "Part of the build recipe was left as it is now: "
                + "; ".join(left)
                + ". Nothing was written to it and nothing was removed from it, so check that "
                "file before building again."
            )
        elif moved:
            yield (
                "The build recipe was put back exactly as it was before this rebuild, so the "
                "server you have now is unchanged and the next build compiles what it compiled "
                "before."
            )

    def _refuse_unless_rebuildable(self, server_dir: Path) -> InstallState:
        """The two folders this button cannot help, refused by name before anything runs.

        **No record.** A rebuild needs a state file for the same reason the
        install's resume does -- it is this app's claim on the folder -- and its
        absence is the honest signal for "somebody else built this". Adopting a
        server through "Use existing…" never writes one (`attach_existing()`
        checks for a compose file and stops there), and neither does any other
        tool.

        **A compose file this app did not write.** The `build:` blocks live in
        `docker-compose.build.yml` and compose never auto-loads it, so without
        that file a `docker compose build` in this folder builds NOTHING and
        exits 0 -- and this app cannot put one there, because
        `composegen.write_plan()` refuses to overwrite a compose file it did not
        write rather than orphan somebody's character volumes. So the honest
        answer to "can this button rebuild an adopted DML-built install?" is no,
        and it says so with the reason rather than failing later with a build
        that succeeded and changed nothing.

        Both refusals are made BEFORE the confirmation's cost is spent and
        before any container is touched, and both name the folder: a user with
        two installs needs to know which one was refused.

        Returns:
            The install's recorded state, for the context the stages run under.
        """
        state = read_state(server_dir, valid=self.stage_names())
        if state is None:
            raise InstallerError(
                f"{server_dir} has no {STATE_FILE}, so Yu'lon has no record of building a "
                f"server there and cannot rebuild one. Nothing was started. A server this "
                f'app installed carries that file; one adopted through "Use existing…", or '
                f"built by another launcher, does not — rebuild that one the way it was "
                f"built."
            )
        ours = generated_compose_files(server_dir)
        missing = [name for name in composegen.COMPOSE_FILES if name not in ours]
        if missing:
            raise InstallerError(
                f"{server_dir} is missing the compose files Yu'lon builds with, or they were "
                f"not written by Yu'lon: {', '.join(missing)}. Nothing was started. "
                f"{composegen.BUILD_FILE} is the only file that carries the build "
                f"instructions — compose never loads it on its own, so without it "
                f"`docker compose build` here builds nothing and reports success — and this "
                f"app will not overwrite a compose file it did not write, because doing that "
                f"can orphan the volumes your characters are in. A server built by another "
                f"launcher has to be rebuilt by that launcher."
            )
        return state

    def _claim_before_writing(
        self, server_dir: Path, state: InstallState, started_empty: bool
    ) -> None:
        """Record the install BEFORE the first mutating stage, when the folder is ours to fill.

        `_run_one()` writes the state file only after a stage FINISHES, so
        anything that ended the process during stage one left `src/` and no
        record, and `_guard()` refused the retry with "is not empty and was not
        created by this app" -- a sentence that was false, this app having
        written every byte, and whose only remedy was deleting a part-finished
        multi-gigabyte clone by hand. Driven, not reasoned: the TBC-on-Windows
        gate was killed mid-clone on `yulon-win11` (2026-09-03) and refused its
        own 162 MB checkout on the next attempt.

        **And that one failure is the one this still does not fix.** Measured
        on m910q 2026-09-05: `_clone_core()` clones into the SERVER DIR, and
        both seams in `git.py` (`RunnerGit.clone`, `ContainerGit.clone`) begin
        by emptying a destination that has no `.git` -- so the record written
        below is removed at the start of stage one on every fresh install, kill
        or no kill. Three tests in `test_families_azerothcore.py` asserted
        otherwise and passed only because their clone doubles skipped that
        line; with the doubles made faithful (`as_the_clone_seam_does()`) all
        three went red, and they now say what happens. What this method DOES
        buy is every stage after the first: their destinations are under
        `modules/`, the record survives them, and the retry resumes. Closing
        the rest means changing where `clone-core` clones -- `git clone <url>
        <dir>` refuses a directory that is not empty, which is why the seam
        empties it -- and that is a `git.py` change with a live gate behind it,
        not a patch here.

        **`5eef8d9f` recorded it on the `except InstallerError` path instead,
        and an adversarial review the same day was right that this misses the
        failure that produced it.** SIGKILL, a power cut and an unhandled
        exception never reach an `except` block, so the harshest endings -- the
        ones a person actually meets -- still left an unclaimed folder. A claim
        that only survives a cooperative failure is a claim about the easy case.

        **Why the early write is safe, which is what §38 thought was the
        obstacle.** The bug list recorded that three tests forbid writing before
        stage one, two of them because an install into the USER'S OWN git
        checkout must leave that checkout untouched, and concluded that the
        "whose checkout is this" question had to be moved ahead of the claim
        first. Re-read on 2026-09-03: it is already ahead. `_guard()` refuses
        every non-empty folder outright, with ONE deliberate exception -- a
        directory holding `.git`, deferred so the clone stage can say whose fork
        it is instead of "this folder is not empty". So the only folder that
        reaches stage one non-empty and unclaimed is somebody's checkout, and
        `started_empty` is exactly the predicate that excludes it. All three
        tests drive a `.git` directory; none of them constrains an empty folder.

        `started_empty` is also no longer a fact carried across the whole
        install to be used at the end. It is read and acted on in consecutive
        statements, which is as narrow as the window gets without a lock: the
        review's point that a second install, a dropped-in file or a remount
        could invalidate it stands, and is now a race of microseconds rather
        than of hours.

        **A failure to claim is a refusal, not a shrug.** The late version had
        to be silent -- it ran with an `InstallerError` already in flight, so
        anything it raised replaced the sentence the user was about to read.
        Nothing is in flight here. A folder this app cannot write a 200-byte
        JSON file into is a folder the install cannot succeed in, and saying so
        now costs one attempt instead of one clone.

        Nothing is written when the folder is not ours to fill, and nothing when
        a record already exists: a resume must not overwrite the progress it is
        resuming from.
        """
        if not started_empty or (server_dir / STATE_FILE).is_file():
            return
        try:
            server_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise InstallerError(
                f"{server_dir} could not be created ({exc}). Nothing was written. Pick a "
                "folder this app is allowed to write to."
            ) from exc
        write_state(server_dir, state)
        # `write_state()` logs its own `OSError` and returns, which is right for
        # the callers that record PROGRESS -- a lost progress note costs a redone
        # stage. It is wrong here: this note is what makes an interrupted install
        # recognisable as ours, so losing it silently rebuilds the exact bug this
        # method exists for. The file is therefore read back rather than assumed,
        # and its absence refused.
        if not (server_dir / STATE_FILE).is_file():
            raise InstallerError(
                f"{server_dir} would not accept {STATE_FILE}, the small file this install "
                "writes first so it can recognise its own work if it is interrupted. Nothing "
                "else was written. Pick a folder this app is allowed to write to."
            )

    def _clear_error(self, server_dir: Path, state: InstallState) -> None:
        """Drop a previous run's failure sentence once this run has finished.

        `InstallState.with_stage()` clears `last_error`, and that was the ONLY
        thing that did -- which meant it cleared nothing on the run where it
        matters most. It returns `self` untouched when the stage is already in
        `completed`, and an unrecorded stage never reaches it at all.
        `recorded=False`, not a stage's POSITION, is the property that decides
        the second half: CMaNGOS's four unrecorded stages are `db-password`
        (2nd of 12), `start-db`, `up` and `ready`, while its last four are
        `start-db`, `import`, `up` and `ready` -- and `import` is recorded. So a
        resume that finishes every remaining stage records nothing new, clears
        nothing, and leaves the old sentence sitting in a state file it has just
        rewritten.

        Seen on m910q 2026-09-02: WoW TBC finished -- three containers up, "WoW
        TBC is installed and running" printed -- with
        `"last_error": "The server started but never reported ready..."` still
        in `.yulon-install.json`, `updated_unix` freshly bumped. A working
        install describing itself as a failed one, on exactly the path -- retry
        after a failure -- where a reader is most likely to believe it.

        Best-effort: the install HAS succeeded and the user has been told so, so
        a state file that cannot be written now must not turn that into a
        failure. The stale sentence is a wrong label on a working server; an
        exception here would be a wrong outcome.
        """
        if not state.last_error:
            return
        if not (server_dir / STATE_FILE).is_file():
            # The same guard `_record_error` carries, for the same reason: a
            # state file removed while this ran must not be RE-CREATED here.
            # `write_state` does `mkdir(parents=True, exist_ok=True)`, so
            # without this a folder the user emptied mid-install gets a state
            # file back, and `_guard()` then refuses the retry on the strength
            # of the record it just wrote (review, 2026-09-02).
            return
        # No try/except: `write_state` catches its own `OSError` and logs
        # "could not record install progress in ...". A handler here was dead
        # code that made this function look more careful than it is.
        write_state(server_dir, replace(state, last_error=""))

    def _run_one(self, stage: Stage, ctx: StageContext) -> Generator[str, None, InstallState]:
        """Run one stage and, if the family says so, write it down."""
        yield from stage.run(ctx)
        if not stage.recorded:
            return ctx.state
        recorded = ctx.state.with_stage(stage.name, self.stage_names())
        write_state(ctx.server_dir, recorded)
        return recorded

    def _install_id(self, server_dir: Path) -> str:
        """This install's id, always through the engine's own `platform_id` seam.

        One method rather than the same two-line call at each site: the seam is
        the whole point of it. `install_id()` lowercases the path on Windows and
        does not elsewhere, so a caller that let `platform.detect` default would
        compute a different id from the one the rest of the install uses — and
        an id is what both the compose project and the kept database password
        are filed under.
        """
        return composegen.install_id(server_dir, platform_id=self._seams.platform_id)

    def resolve_secrets(self, server_dir: Path) -> Secrets:
        """The database password this install uses, decided before stage 1.

        `fixed` is the catalog value (WotLK's `password` — a contract with
        backup, console and every guide). `generated` reads the file a previous
        run persisted (written with a trailing newline, read with `.strip()`),
        else mints `<prefix><16 hex>`; persisting it is the family's
        `db-password` stage (7.3), not this method's job, so a preflight
        refusal after this point leaves no secret on disk.

        A file that is already there is taken AS WRITTEN. `prefix` decorates a
        value this app mints; it is not a shape an existing password has to
        have, and a shipped bash installer minted the same passwords without
        the dash `catalog.json` now carries.

        **No file, but a copy Yu'lon kept** is the third case and the reason
        this is not two branches: an uninstall with "keep my characters" ticked
        deletes the folder the file was in and keeps the database volume, so
        minting here would hand every later stage a password that database has
        never heard of. `dbsecret.recall()` is keyed by the same
        `<game>-<install id>` a reinstall to this folder recomputes, and
        answers `None` for every install that never went through such an
        uninstall — so the mint below is still what an ordinary first install
        gets. Whether the recalled value may be WRITTEN back beside a volume
        that exists is the `db-password` stage's question, not this one's.
        """
        plan = self.entry.install.password
        if plan.mode == "fixed":
            if plan.value is None:
                raise InstallerError(
                    f"{self.entry.name}'s catalog entry says its database password is fixed "
                    "but gives no value. That is a bug in the app."
                )
            return Secrets(plan.value)
        if plan.file is None:
            raise InstallerError(
                f"{self.entry.name}'s catalog entry says its database password is generated "
                "but names no file to keep it in. That is a bug in the app."
            )
        path = server_dir / plan.file
        try:
            return Secrets(path.read_text(encoding="utf-8").strip())
        except FileNotFoundError:
            kept = dbsecret.recall(self.entry.id, self._install_id(server_dir))
            if kept is not None:
                logger.info(
                    f"{path} is gone; using the password Yu'lon kept for {kept.volume} "
                    "when this install was removed"
                )
                return Secrets(kept.password)
            return Secrets(f"{plan.prefix}{token_hex(8)}")
        except OSError as exc:
            raise InstallerError(
                f"{path} exists but could not be read ({exc}), so this install cannot know its "
                "own database password. Nothing was started."
            ) from exc

    def _native(self) -> NativeInstall:
        """The entry's `install.native` block; preflight has already refused its absence."""
        native = self.entry.install.native
        if native is None:
            raise InstallerError(
                f"{self.entry.name} has no `install.native` section, so nothing was started."
            )
        return native

    # -- preflight -------------------------------------------------------

    def _preflight_lines(
        self,
        options: InstallOptions,
        cancel: threading.Event | None,
        ask: runner.Prompter | None = None,
    ) -> Iterator[str]:
        here = self._seams.platform_id()
        if not self.entry.install.supports(here):
            raise UnsupportedPlatformError(unsupported_platform_message(self.entry, here))
        if self.entry.install.native is None:
            raise InstallerError(
                f"{self.entry.name} is not set up for a native install on {here} — its "
                "catalog entry has no `install.native` section. Nothing was started."
            )
        if self.entry.install.native.family != self.family:
            raise InstallerError(
                f"{self.entry.name} is catalogued as a `{self.entry.install.native.family}` "
                f"install but was handed to the `{self.family}` engine. That is a bug in the "
                "app, not something to fix on this machine."
            )
        if self.entry.containers.db_import and self._probe is None:
            # An installer that cannot ask what state the databases are in
            # cannot know whether its own import finished, and an installer
            # with no reset seam would strand exactly the interrupted install a
            # resumable engine exists for (see `docker.repair_import()`'s
            # `partial` branch, measured on yulon-ubuntu 2026-08-23).
            raise InstallerError(
                f"{self.entry.name} names a database import service but this installer was "
                "built without a way to check it. That is a bug in the app, not something to "
                "fix on this machine."
            )
        server_dir = self.server_dir(options)
        # Before provisioning, not after it. The same rule is in the report
        # `preflight.evaluate()` builds, but that report is built from
        # `gather()`, which runs after `ensure_docker()` — so picking $HOME on a
        # clean Linux box bought the docker-group consent dialog, a sudo
        # password typed into Yu'lon's own dialog and a package install, and
        # only then "that folder will not work". Measured on Fedora 44
        # (2026-08-25) against the shell installer, whose own `case
        # "$SERVER_DIR" in /|"$HOME"|...` refused in that same order; the native
        # engine inherited the order in 7.1 and kept it until this call site
        # existed. The words are `preflight._folder_check()`'s, so a user reads
        # the same refusal whichever half of preflight reaches it.
        folder_problem = self._seams.dir_problem(server_dir)
        if folder_problem is not None:
            raise InstallerError(
                f"{folder_problem} Pick a different folder and try again. Nothing was written."
            )
        # ...and the rest of the folder's own rules with it, for the same reason
        # and one more: each says some version of `Nothing was written`, and after
        # provisioning that sentence is false of the machine. The state file, the
        # ownership checks and the emptiness check are filesystem reads that no
        # daemon can help with, so a user whose folder was never usable is now
        # told so before being asked for a root password. The answer is thrown
        # away here: `_guard()` asks again for the state it returns.
        #
        # `_refuse_foreign_containers()` deliberately does NOT move up. It asks
        # which compose project owns a container wearing this entry's names, and
        # there is no answer to that without a daemon.
        self._claim_folder(server_dir)
        yield "Checking Docker."
        if not self._seams.docker_ready():
            # Provisioning prints nothing of its own and can be a Docker
            # Desktop download followed by a three-minute readiness poll. The
            # first macOS tester watched an empty panel through all of it and
            # reported the install as silently dead (macOS gate, 2026-08-25).
            yield (
                "Docker is not answering yet. Setting it up - this can mean downloading "
                "Docker Desktop and waiting for its engine, up to a few minutes with no "
                "output. You can stop at any time."
            )
            # `ask` reaches provisioning and nothing else: the docker-group
            # consent and the Linux sudo password are asked there, before any
            # privileged step, and declined when there is nobody to ask.
            report = self._seams.ensure_docker(cancel=cancel, ask=ask)
            # Said whatever happens next, including when nothing goes wrong.
            # Provisioning's own report was read only inside the refusals below,
            # so a run that installed Docker and joined the docker group told
            # the user neither — and the log-out-and-back-in step it produces is
            # the one thing standing between them and a server. See
            # `installer.provision_lines()`.
            yield from provision_lines(report)
            if report.reboot_required:
                raise DockerUnavailableError(
                    "Docker's prerequisites were installed but a reboot is needed first. "
                    + " ".join(report.manual_steps)
                )
            if not report.docker_ready and not self._seams.docker_ready():
                raise docker_unavailable(report)
        try:
            # The engine's own seams are handed down rather than letting
            # preflight fall back to its real defaults: without this an engine
            # built with `platform_id=lambda: "macos"` dispatches as macOS and
            # then gathers facts about whatever host it is really on, and the
            # two can disagree with nothing noticing.
            facts = self._seams.gather(
                self.entry,
                server_dir,
                client_dir=options.client_dir,
                platform_id=self._seams.platform_id,
                docker_ready=self._seams.docker_ready,
                dir_problem=self._seams.dir_problem,
            )
        except docker.DockerCommandError as exc:
            # `gather()`'s port scan goes through `docker._run()`, which RAISES.
            # Every other outward call in this engine is wrapped, and an
            # unwrapped one escapes `run()` as a traceback instead of the
            # sentence this method's contract promises.
            raise InstallerError(
                f"Docker answered once and then would not answer again, so nothing was started: "
                f"{exc}"
            ) from exc
        report_checks = preflight.evaluate(self.entry, server_dir, facts)
        yield from preflight.lines(report_checks)
        if not report_checks.ok():
            raise InstallerError(
                "This machine cannot install the server yet:\n" + report_checks.message()
            )

    # -- the guard -------------------------------------------------------

    def _guard(self, server_dir: Path) -> InstallState:
        """Claim the directory, or refuse it. Never recorded, so a resume re-runs it.

        TWO halves, and the split is about WHEN each may be answered rather
        than about what each asks. `_claim_folder()` is pure filesystem and is
        called from `_preflight_lines()` before anything is provisioned;
        `_refuse_foreign_containers()` needs a daemon to answer which compose
        project owns a container, so it can only run here, after one exists.

        Both are still called from here, and that is not belt and braces: this
        is the call `run()` takes its `InstallState` from, and re-reading a
        state file that may have changed between preflight and the run is the
        honest answer rather than a cached one. Every rule below is a read.
        """
        state = self._claim_folder(server_dir)
        self._refuse_foreign_containers(server_dir, state.install_id)
        return state

    def _claim_folder(self, server_dir: Path) -> InstallState:
        """The half of the guard that is pure filesystem, and so needs no daemon.

        Called from `_preflight_lines()` BEFORE provisioning, and again from
        `_guard()`. Until 2026-09-02 every rule here ran only in the second
        place, which is after `ensure_docker()` -- a sudo password, the
        docker-group consent and a package install -- and after `gather()`'s
        docker ps, port scan and image-pulling bind-mount probe. Recorded seam
        order on a real run:

            dir_problem -> None | 'Checking Docker.' | docker_ready -> False
            ensure_docker
            preflight.gather
            REFUSED: ...server is not empty and was not created by this app.
                     Nothing was written.

        `Nothing was written` was untrue of the machine by the time it was
        said. `7cb3bf17` hoisted the one sibling rule that lives in
        `platform.server_dir_problem()` and left these below it.

        Nothing here writes, deletes or asks anything outside this directory,
        which is what makes running it twice free and running it early safe.

        Distinct from preflight, which is about the machine. This is about this
        one folder and this one install: it must be empty, or ours by
        `install_id`. It must also be ours by `family`: a catalog edit that
        moves a game between families is a refusal here, never a
        reinterpretation. The container rule this paragraph used to name as
        well is `_guard()`'s other half, and it is the reason there are two.

        A state file that will not parse is refused rather than ignored, and
        that refusal is the whole of `Ownership.UNKNOWN`. Treating it as absent
        made this the FRESH-install path while `claimed_this_folder()` was
        simultaneously calling the folder ours — the two ownership answers
        disagreeing on exactly the input where the engine knows least, with
        `git reset --hard` at the end of it (`read_claim()`). Refusing costs a
        user with a damaged file one sentence and one deletion; the other
        direction cost them their work.
        """
        install_id = self._install_id(server_dir)
        claim = (
            read_claim(server_dir, valid=self.stage_names())
            if server_dir.is_dir()
            else Claim(Ownership.UNCLAIMED)
        )
        if claim.ownership is Ownership.UNKNOWN:
            # The generic sentence is for DAMAGE, and its advice is to delete
            # the file. `Claim.reason` is how the one `UNKNOWN` that is not
            # damage -- an intact record from a newer build -- says something
            # else, because deleting that one destroys a working install's
            # progress.
            raise InstallerError(
                claim.reason
                or (
                    f"{server_dir} holds a {STATE_FILE} this app cannot read, so it "
                    "cannot tell whether this folder is one of its own installs or "
                    "somebody else's work. Nothing was written. If that file is left "
                    "over from an install that was interrupted, delete it and try "
                    "again; if you did not expect it to be there, install into another "
                    "folder instead."
                )
            )
        existing = claim.state
        if existing is not None and existing.install_id != install_id:
            raise InstallerError(
                f"{server_dir} holds an install record made for a different folder, so this "
                "looks like a copy of another install. Copying a server folder does not copy "
                "its containers or its database. Install into an empty folder instead."
            )
        if existing is not None and existing.game_id != self.entry.id:
            raise InstallerError(
                f"{server_dir} already holds an install of {existing.game_id}. Pick another "
                "folder."
            )
        if existing is not None and existing.family and existing.family != self.family:
            raise InstallerError(
                f"{server_dir} was installed as `{existing.family}`, but the catalog now says "
                f"{self.entry.name} is `{self.family}`. A folder is never reinterpreted: pick "
                "another folder, or remove that install first."
            )
        if existing is not None and not existing.family:
            # Written before 7.1: ours, and the next write says which family.
            existing = replace(existing, family=self.family)
        if existing is None and server_dir.is_dir() and not (server_dir / ".git").is_dir():
            # A directory that is a git checkout is deliberately NOT judged
            # here: the family's clone stage can ask whose it is, and "this is
            # a checkout of somebody else's fork" is a far better sentence than
            # "this folder is not empty". Everything else is refused before a
            # byte is written.
            leftovers = _listing(server_dir, ignoring=OUR_OWN_FILES)
            if leftovers:
                raise InstallerError(
                    f"{server_dir} is not empty and was not created by this app "
                    f"({', '.join(sorted(leftovers)[:5])}). Nothing was written. Pick an empty "
                    "folder, or remove that one yourself if you no longer want it."
                )
        return existing or InstallState(
            game_id=self.entry.id, install_id=install_id, family=self.family
        )

    def _refuse_foreign_containers(self, server_dir: Path, install_id: str) -> None:
        """Refuse when a container wearing our names belongs to somebody else's project.

        Container names are global per engine and every AzerothCore install in
        the wild uses the same three, so this is not exotic: a second install
        while the first exists is the normal way to meet it. The remedy named
        is Remove, which the app has.

        **Existence is asked FIRST, and that is the whole of the fresh-install
        case.** `container_project()` answers `UNREADABLE` for any non-zero
        `docker inspect`, and `docker inspect <missing>` exits 1 — so asking it
        about a container that is not there refused every install on every
        machine that had never run this server, naming a container the user
        could then not find (review, 2026-08-23). The pre-existing caller in
        `docker._running()` guards the same way, `if name not in running:
        continue`; this is that check, spelled for containers that exist but are
        stopped as well as running ones.

        **The sentence names the folder, not only the project id.** T32: a
        reporter told a container belonged to `yulon-wow-wotlk-db937032` has no
        way to act on that — the id names nothing they can see in their own
        file manager. `container_working_dir()` reads the working-dir sibling
        of the same compose label, so the refusal can say WHERE to go instead
        of a hash; `None` or `UNREADABLE` from that seam means the label could
        not be read, and the sentence says so rather than printing either
        value. A project this app made (`composegen.PROJECT_PREFIX`) gets the
        friendlier wording, because "another install this app made" is true of
        it and is not true of a stranger's Docker Compose project, whose only
        honest remedy is to go and stop it from its own tooling.

        **The folder is where compose brought the project up FROM, not
        necessarily where it lives now.** `container_working_dir()`'s own
        docstring says the label is baked in at container creation, so a
        moved install reports its old path. The sentence says "brought up
        from … when it was created" rather than "is at", and does not offer
        "install into that folder instead" — the folder named may no longer
        be the right one to open, though the install's own tab still knows
        wherever it is now (review, Codex, 2026-09-11).
        """
        ours = composegen.project_name(
            self.entry.id, server_dir, platform_id=self._seams.platform_id
        )
        spec = self.entry.container_spec()
        for name in (spec.db, spec.auth, spec.world):
            try:
                if not self._seams.container_exists(name):
                    continue
            except docker.DockerCommandError as exc:
                raise InstallerError(
                    "Docker would not say what containers already exist on this machine "
                    f"({exc}), so this install cannot prove it is safe to create its own. "
                    "Nothing was written."
                ) from exc
            owner = self._seams.container_project(name)
            if owner is None or owner == ours:
                # `None` is a container that exists and carries no compose label
                # — something started outside compose, wearing our name. Not a
                # project conflict, and the daemon's own duplicate-name error is
                # the honest report if it is still there at `up`.
                continue
            if owner == docker.UNREADABLE:
                raise InstallerError(
                    f"Docker would not say which install the existing {name} container belongs "
                    "to, so this install cannot prove it is safe to create one. Nothing was "
                    "written."
                )
            working_dir = self._seams.container_working_dir(name)
            readable = working_dir not in (None, docker.UNREADABLE)
            if owner.startswith(composegen.PROJECT_PREFIX):
                if readable:
                    raise InstallerError(
                        f"A container called {name} already exists and belongs to another "
                        f"install this app made, brought up from {working_dir} when it was "
                        "created (if that folder has moved since, its own tab still knows "
                        "it). Two servers cannot share that name. Open that install's tab "
                        "and stop and remove its containers, then try again."
                    )
                raise InstallerError(
                    f"A container called {name} already exists and belongs to another "
                    f"install this app made ({owner}), but this app could not read which "
                    "folder that install used. Two servers cannot share that name. Open "
                    "that install's tab and stop and remove its containers, then try again."
                )
            if readable:
                raise InstallerError(
                    f"A container called {name} already exists and belongs to another "
                    f"Docker Compose project ({owner}), brought up from {working_dir} when "
                    "it was created (if that folder has moved since, its own tab still "
                    "knows it). Two servers cannot share that name. Remove the other "
                    "install's containers from its own tab first, then try again."
                )
            raise InstallerError(
                f"A container called {name} already exists and belongs to another Docker "
                f"Compose project ({owner}), but this app could not read which folder it "
                "was brought up from. Two servers cannot share that name. Remove the other "
                "install's containers from its own tab first, then try again."
            )

    # -- stage bodies a family binds into `Stage.run` --------------------

    def claimed_this_folder(self, ctx: StageContext) -> Ownership:
        """Has a previous run of THIS install already written its record here?

        Answered by `read_claim()`, which is the same function `_guard()` asks,
        because the previous two answers to this question disagreed. This one
        used to be `(ctx.server_dir / STATE_FILE).is_file()` — PRESENCE — on the
        premise that `_guard()` had already proved that any state file still
        here belongs to this install. The premise held only for a file
        `_guard()` could parse: every check there is written `if existing is not
        None and …`, and `read_state()` answered `None` for a file it could not
        read. So a corrupt state file passed `_guard()` as "fresh folder" and
        passed this as "ours", and `refuse_unowned_checkout()` stood down over a
        user's own checkout. `read_claim()`'s docstring has the repro.

        Three answers, and only `OWNED` is ownership. `UNKNOWN` is a state file
        that is there and unreadable, and it must never be worth more than
        `UNCLAIMED`, which is no file at all: it is the input the engine knows
        least about. `_guard()` refuses `UNKNOWN` before a stage runs, so
        reaching it here means the file was damaged or replaced DURING the
        install — which is exactly why this re-reads the folder rather than
        trusting a decision taken minutes ago.

        `ctx.state` cannot answer it either: `_guard()` hands every run a state
        object whether or not a file was read, so a fresh install and a resumed
        one are indistinguishable from the object alone. What `ctx.state` is
        good for is the identity `_guard()` already validated, which is what the
        re-read is checked against here.

        (`_record_error()` still asks `is_file()`, and correctly: its question is
        "is there a file to update?", never "is this folder mine?".)
        """
        claim = read_claim(ctx.server_dir, valid=self.stage_names())
        found = claim.state
        if found is None:
            return claim.ownership
        if found.install_id != ctx.state.install_id or found.game_id != ctx.state.game_id:
            return Ownership.UNKNOWN
        if found.family and found.family != ctx.state.family:
            return Ownership.UNKNOWN
        return Ownership.OWNED

    def refuse_unowned_checkout(
        self, ctx: StageContext, dest: Path, url: str, remote: str | None
    ) -> None:
        """Refuse a checkout of the RIGHT repository that this install never made.

        The one path in this engine that could still destroy a user's work, and
        it was reached by a first press rather than a resume. `_guard()`
        deliberately exempts a directory that is a git checkout from the
        not-empty refusal, so that a clone stage can say whose repository it is
        instead of "this folder is not empty" — but the clone stages only ever
        refused a checkout of a DIFFERENT repository. Point a first install at
        your own checkout of the same repo and every guard passed: no record, so
        `already_cloned()` is False, so the seam's update path ran, which is
        `git fetch` + `git reset --hard FETCH_HEAD`. Driven end to end (review,
        2026-08-31): `// my patch` became `// upstream`.

        Ownership is `claimed_this_folder()`, and only `Ownership.OWNED` is it.
        INSIDE a folder this install owns, an unrecorded checkout is this
        install's own unfinished work and fetch+reset is the right repair — that
        is how a `modules/` clone that died half way is healed. Outside one, it
        is somebody's repository and this app did not put it there, so it is
        named and left alone. `UNKNOWN` — a state file that is there and will
        not parse — is not ownership and gets its own sentence, because "there
        is no record here" would be a lie about a folder that has one.

        **`remote is None` is handled HERE, not at three call sites.** It used
        to `return` — safe only because
        `if has_git and existing is None: raise` was copy-pasted into
        `families/azerothcore.py` twice and `stage_clone_sources()` once, so
        the one method whose job is this could not do it alone (review,
        2026-08-31). Every one of those copies had the same `dest` this method
        is handed. `None` there does NOT mean "no checkout": it also means no
        docker CLI, a daemon that would not answer, a failed image pull, and —
        until `5c6c655c` — an SELinux denial on every enforcing box. A `.git`
        on disk is the host's own evidence that the container's `None` is a
        misdiagnosis, and stopping is the only safe reading of it.

        The cost is stated in the message rather than worked around: a FIRST
        clone that died before its stage was recorded also lands here, and the
        remedy is to delete the half-finished folder. Losing a download is not
        the same order of harm as losing work, and no evidence available at this
        point tells the two apart — a partly-cloned tree and a checkout a user
        made both answer `git remote get-url origin` with this exact URL.
        """
        if remote is None:
            if (dest / ".git").is_dir():
                raise InstallerError(
                    f"{dest} contains a git checkout, but git would not say what it is a "
                    "checkout of, so nothing was changed. Move that folder aside and try again."
                )
            return
        claim = self.claimed_this_folder(ctx)
        if claim is Ownership.OWNED:
            return
        if claim is Ownership.UNKNOWN:
            raise InstallerError(
                f"{dest} is a git checkout of {url}, and the {STATE_FILE} beside it cannot be "
                "read, so this app cannot tell whether the checkout is its own unfinished work "
                "or yours. Continuing would run `git fetch` and `git reset --hard` over it, so "
                "nothing was touched. Delete that file if it is left over from an interrupted "
                "install, or install into an empty folder instead."
            )
        raise InstallerError(
            f"{dest} is already a git checkout of {url}, and there is no record here of an "
            "install this app made. Continuing would run `git fetch` and `git reset --hard` "
            "over it, which throws away anything you have changed, so nothing was touched. "
            "Install into an empty folder instead — and if this folder is a half-finished "
            "download from an earlier attempt, delete it first."
        )

    def already_cloned(self, ctx: StageContext, stage: str, remote: str | None) -> bool:
        """Is this clone BOTH written down and corroborated by a checkout on disk?

        The two halves are the whole rule, and neither is sufficient alone.

        `InstallState.has()` is documented as "never a reason to skip on its
        own" because a state file can lie — a fresh empty folder once
        skip-compiled against another install's images — so the record is only
        ever a hint, and `remote` (what `git remote get-url origin` says at the
        destination, already checked against the source's URL by the caller) is
        the disk evidence that can contradict it.

        The other direction is what this method was missing until 7.1, and it
        cost the live Ubuntu gate (2026-08-30) its source tree: a clone that is
        recorded AND present was being handed to the seam anyway, where an
        existing `.git` means `git fetch` + `git reset --hard FETCH_HEAD`. That
        is destructive twice over. It discards any edit a user made to the
        server source, unrecoverably and with no warning; and it moves the
        checkout to whatever upstream has published since — so a user who stops
        a compile and starts it again does not resume the build they
        interrupted, they start a different one. A resume must not be able to
        change what is being built.

        `remote is None` with a record is the repair case and still clones: the
        checkout was deleted, or was never a checkout. A checkout with NO record
        is the other repair case — a run interrupted part-way through a clone —
        and fetch+reset is exactly right there, because a half-materialised
        working tree is not something to build against.
        """
        return ctx.state.has(stage) and remote is not None

    def stage_clone_sources(
        self, ctx: StageContext, sources: Sequence[EmulatorSource], *, recorded_as: str
    ) -> Iterator[str]:
        """Clone every source at its `dest`, refusing what is not ours and touching what is done.

        `recorded_as` is the name the FAMILY binds this body to in its `Stage`
        tuple; the body cannot know it, and without it the record could not be
        consulted at all — which is how the AzerothCore stages came to fetch and
        reset on every resume. See `already_cloned()`.

        Disk evidence beats the state file in both directions: a `.git` whose
        `origin` is this source and a record of this stage is a finished clone
        and is left exactly as it is; the same `.git` with no record gets
        fetch+reset through the seam's own update path; a `.git` pointing
        somewhere else is refused BY NAME and never deleted, because a directory
        holding somebody's fork is not this installer's to remove. A directory
        with files and no `.git` is refused too: the clone seam `shutil.rmtree`s
        a destination it does not recognise, and a tree a user unpacked by hand
        must not fall through (review, 2026-08-23). `dest == "."` is the server
        dir itself, where the state file is the one leftover that does not count.
        """
        if not sources:
            yield "This server has nothing to clone."
            return
        for source in sources:
            dest = ctx.server_dir / source.dest
            has_git = (dest / ".git").is_dir()
            existing = self._remote_of(dest)
            if existing is not None and not git.same_repo(existing, source.url):
                raise InstallerError(
                    f"{dest} is a checkout of {existing}, not of {source.url}. Nothing was "
                    "changed."
                )
            if not has_git and dest.is_dir():
                leftovers = _listing(dest, ignoring=OUR_OWN_FILES)
                if leftovers:
                    raise InstallerError(
                        f"{dest} has files in it but is not a checkout of {source.url}, so it "
                        "was left alone. Move that folder aside and try again."
                    )
            if self.already_cloned(ctx, recorded_as, existing):
                yield f"{source.repo} is already in {source.dest}; leaving it exactly as it is."
                continue
            self.refuse_unowned_checkout(ctx, dest, source.url, existing)
            yield f"Cloning {source.repo} into {source.dest}"
            if existing is not None:
                yield "A previous run of this install left it part-way through; finishing it off."
            self._clone(
                git.CloneSpec(
                    url=source.url,
                    dest=dest,
                    branch=source.branch,
                    sparse_path=source.sparse_path,
                    depth=source.depth,
                    rev=source.rev,
                )
            )
        yield "Sources are in place."

    def stage_generate_compose(self, ctx: StageContext) -> Iterator[str]:
        """Write the three compose files, and refuse to overwrite ones we did not write.

        With one recognised exception, which every install needs. The server dir
        IS the emulator checkout and that repository ships its own
        `docker-compose.yml` at the root — the Linux installer's whole mechanism
        depends on it being there — so the clone stage lays an unmarked base
        file down before this ever runs. Refusing it refused every install, with
        "point the install at an empty folder" said to a user who had (review,
        2026-08-23).

        The exception is narrow and mechanical: git is asked whether that path
        is tracked and unmodified in this checkout. Empty output from `git
        status --porcelain -- docker-compose.yml` proves the file is byte for
        byte what the clone wrote and that `git checkout -- docker-compose.yml`
        restores it, so replacing it destroys nothing. A file a user has edited
        answers ` M`, an untracked one answers `??`, and a git that cannot be
        asked answers `None` — all three keep the refusal. The override and
        build files get no exception at all: upstream ships neither, so an
        unmarked one there is somebody's own settings.

        Under enforcing SELinux the bind lines get `:z` through
        `{{BIND_LABEL}}` and the folder is relabelled once (`relabel` seam);
        the 2026-08-25 Fedora gate proved the bash, gate 7.1 on Fedora proves
        this port.
        """
        # `:z` on every host bind line when SELinux enforces and the drive can
        # carry labels; empty otherwise, so the rendered files are byte-identical
        # off SELinux (the committed compose-config fixture is the proof). A
        # uniform `:z`, not `:Z`: `./modules` is shared by the import and the
        # worldserver, and `:Z` locks the other service out.
        #
        # THREE ANSWERS GO IN, not two. `selinux_enforcing()` returns None for
        # "could not ask" and it is passed through as None — `bind_label()` is
        # the one place allowed to decide what an unknown means (it renders
        # nothing, and preflight says "unchecked" so the user sees that the
        # question went unanswered). Collapsing it here to a bool would make
        # the two indistinguishable everywhere downstream.
        label = platform.bind_label(
            enforcing=self._seams.ask_selinux(), fs_type=self._seams.ask_fs(ctx.server_dir)
        )
        # `render()` INSIDE a `try`, not beside one. It was called bare until
        # 2026-09-02, and `ComposeGenError` is not an `InstallerError` - both
        # subclass `RuntimeError` independently, so neither `except` clause can
        # see the other's refusal and `run()`'s caught nothing here. Measured
        # through the real `install_wiring.main()` on `wow-wotlk` and on
        # `wow-tbc`: an unfilled placeholder in a compose template printed a
        # traceback where the harness's own docstring promises the sentence
        # written for a person, and `_record_error` never ran, so the state file
        # kept `"last_error": ""` where every other stage failure records its
        # own. Every refusal reachable from `render()` escaped that way; the one
        # exception was `write_plan()`'s, below, which was already translated.
        # This body is bound by EVERY family (`azerothcore.py`'s stage tuple and
        # `cmangos.py`'s both name this method), so it was never one game's bug.
        try:
            plan = composegen.render(
                self.entry,
                ctx.server_dir,
                templates_root=self.installers_root,
                db_password=ctx.secrets.db_password,
                bind_label=label,
                platform_id=self._seams.platform_id,
            )
        except composegen.ComposeGenError as exc:
            raise InstallerError(str(exc)) from exc
        replaceable = self._replaceable_compose(ctx.server_dir)
        if replaceable:
            yield (
                f"Replacing the {composegen.BASE_FILE} that came with the repository; it is "
                "unchanged from what git has, so `git checkout` brings it back."
            )
        try:
            written = composegen.write_plan(plan, ctx.server_dir, replaceable=replaceable)
        except composegen.ComposeGenError as exc:
            raise InstallerError(str(exc)) from exc
        except OSError as exc:
            raise InstallerError(f"the compose files could not be written: {exc}") from exc
        if not written:
            yield "The compose files are already exactly what this install needs."
        for path in written:
            yield f"Wrote {path.name}"
        if label and not self._seams.relabel(ctx.server_dir):
            # A Python port of the Fedora script's `selinux_label_for_containers`
            # (`chcon -Rt container_file_t`, no sudo). Failure is said, not
            # fatal: the `:z` mount option relabels at `up` on most setups.
            #
            # Run here, and only here, on purpose. Both clone stages are done by
            # now, so everything the host has written exists; everything created
            # after this — the build's output, the client data — is written by a
            # container into a volume or into a bind that `:z` relabels when it
            # is mounted. A later Fedora permission error is therefore evidence
            # for a SECOND relabel somewhere, not for a longer timeout on this
            # one.
            yield (
                f"{ctx.server_dir} could not be relabelled for containers (chcon); if the "
                "server refuses to start under SELinux, run `chcon -Rt container_file_t` on it."
            )

    def built_image_refs(self, ctx: StageContext) -> tuple[str, ...]:
        """The image references this install's build produces, fully qualified.

        Split out of `built_images()` on 2026-09-05 so that a refusal telling a
        user to REMOVE those images names the same strings the skip decision was
        made of. Two callers each spelling `composegen.built_image_refs(...)`
        would be two chances to name an image this install does not have, and a
        `docker image rm` on the wrong tag either does nothing or removes
        somebody else's build -- neither of which says which happened.
        """
        return composegen.built_image_refs(
            self.entry, ctx.server_dir, platform_id=self._seams.platform_id
        )

    def built_images(self, ctx: StageContext) -> bool | None:
        """Does the daemon hold every image this install's build produces?

        `None` is "the daemon would not say", which is not "no".
        """
        return self._seams.images_built(self.built_image_refs(ctx))

    def build_would_be_skipped(self, ctx: StageContext) -> bool:
        """Will `stage_build` skip the compile on this press?

        The record AND the images, which is `stage_build`'s own rule, kept in
        one place so an earlier stage can ask the same question and cannot
        drift from the answer the build itself will give. `None` -- a daemon
        that would not say -- is False here for the same reason it is a rebuild
        there: not knowing is not a reason to believe.

        Asked one stage earlier by `CmangosInstaller._patch_sources()`, which
        refuses to edit a source tree whose compiled form this press is not
        going to rebuild.

        `force_build` short-circuits ahead of the daemon question on purpose:
        a forced press compiles whatever the daemon says, so asking would be a
        `docker image inspect` per image whose answer changes nothing — and, on
        a daemon that will not answer, a warning about an unknown that is not
        this press's problem.
        """
        if ctx.force_build:
            return False
        return ctx.state.has("build") and self.built_images(ctx) is True

    def stage_build(self, ctx: StageContext) -> Iterator[str]:
        """Compile the server. Hours, and the one stage whose output is worth watching.

        The state file alone never skips this: the daemon is asked whether this
        install's image references exist, and only "they all do" plus a
        recorded build counts. A daemon that will not answer is not "no images"
        — it is unknown, and an unknown re-runs the build, which is slow and
        safe rather than fast and wrong.

        It used to ask `compose images -q`, which cannot answer the question:
        compose enumerates the images of a project's CREATED CONTAINERS, so in
        the built-but-not-yet-up window this runs in it returned nothing and
        every resume re-ran the compile (measured 2026-08-24; see
        `docker.images_built()`).

        `ctx.force_build` is the rebuild press, and it takes the whole skip
        decision out of play rather than adding a fourth case to it: the daemon
        is not asked (its answer changes nothing), the state file is not
        consulted, and the reason is SAID — the panel is about to show an hour
        of compiler output for a server that is installed and running, and
        output like that with nothing above it explaining itself reads as a bug
        rather than as the thing the user confirmed thirty seconds ago.

        The `BUILD_CANCEL_NOTE` this stage used to yield is gone from the body:
        the spine says a stage's cancel note right after `--- <name>` (A4).
        """
        if ctx.force_build:
            yield (
                "This server is already built; a rebuild was asked for, so the compile runs "
                "anyway. That is the whole point of the button — a module added after the "
                "last build is only in the worldserver once it has been compiled in."
            )
        else:
            built = self.built_images(ctx)
            if ctx.state.has("build") and built:
                yield "The server is already built; skipping the compile."
                return
            if ctx.state.has("build") and built is None:
                yield (
                    "Docker would not say whether this install is built, so it is being rebuilt."
                )
        # Two sentences for one action, because "on a first install" is the
        # wrong half of the truth for the press that is deliberately rebuilding
        # a finished one, and this feature is about not telling a user something
        # that does not match what they just did.
        yield (
            "Building the server. The output below is live. "
            + (
                "This is the same compile an install does, and it takes as long."
                if ctx.force_build
                else "This takes hours on a first install."
            )
        )
        run = yield from self._pump(
            lambda sink: self._seams.build(
                ctx.server_dir, composegen.COMPOSE_FILES, sink=sink, cancel=ctx.cancel
            ),
            cancel=ctx.cancel,
        )
        self._check_run(run, "the build", ctx.cancel, BUILD_CANCEL_NOTE)
        yield "The build finished."

    def stage_start_db(self, ctx: StageContext) -> Iterator[str]:
        """Bring the database up alone, before the import stage asks it anything.

        Not an optimisation and not tidiness — without it the install cannot
        finish. `stage_import()` probes first, and the real probe is a `docker
        exec <db container> mysql …`; with no such container it raises and the
        probe answers `unreadable`, which `stage_import()` turns into a hard
        refusal. So the fresh install died at the import stage AFTER the
        multi-hour build, every time, and every resume died in the same place
        (review, 2026-08-23). Running the one-shot anyway would not have
        helped: `run_one_shot()` passes `--no-deps`, which prunes exactly the
        `depends_on: <db>: condition: service_healthy` edge the generated base
        file declares on the import service.

        `pyplan/phase6-decisions.md` had already settled this for the repair
        button — "The action starts the database, and that is a deliberate
        widening of 'runs only the one-shot service'… Without it the action is
        unreachable" — and `docker.start_database()` is that same code, shared
        so the two paths cannot drift.

        Never recorded: it is a precondition, not progress, and it returns
        immediately when the container is already up.

        Unconditional (A7): every family's import needs the database up, and
        CMaNGOS has no `db_import` service at all. The AzerothCore family keeps
        its no-service short-circuit in its own wrapper.
        """
        yield "Starting the database, which the import writes into."
        try:
            self._seams.start_db(self.entry.container_spec(), ctx.server_dir)
        except docker.DockerCommandError as exc:
            raise InstallerError(
                f"The database could not be started, so nothing was imported: {exc}"
            ) from exc
        yield "The database is up."

    def stage_import(
        self, ctx: StageContext, gate: ImportGate, service: str | None
    ) -> Iterator[str]:
        """Populate the databases, using the same probe/reset machinery as the repair button.

        Five answers, four different things to do — `docker.DatabaseImport` is a
        five-member `Literal`, and `populated` splits on `complete`, joining
        `imported` on one side and `unreadable` on the other. That is why this
        count and the "five-branch table" below are different numbers. This
        opened "Four answers" until 2026-09-02, which read as a contradiction of
        its own closing paragraph and of `cmangos._import`. The branch table is
        `docker.repair_import()`'s, because an installer and a repair ask the
        same question of the same databases:

        * `absent` — run the one-shot;
        * `partial` — reset the half-written schemas FIRST, then run. Re-running
          the importer over a schema that already exists reported success in 28
          seconds and left `acore_world` permanently unimportable (measured on
          yulon-ubuntu, 2026-08-23);
        * `imported`, or `populated` with every schema complete — skip. A resume
          must not touch a finished import, and rows alone are not failure:
          modules seed them;
        * `unreadable`, or `populated` but incomplete — refuse. An unanswerable
          database is not an empty one, and a database with rows in it is
          somebody's.

        `unreadable` means what it says here only because `start-db` ran first:
        the probe reaches the databases through `docker exec` on the database
        container, so without one running it answers `unreadable` for a machine
        with nothing wrong with it. See `stage_start_db()`.

        The five-branch table ALWAYS runs first (A7). With `service` given the
        compose one-shot and `verify_import` follow, as before; with `service`
        None this returns after the table and the family applies the SQL
        itself, re-probes and writes its own marker (7.3).
        """
        before = gate.probe()
        yield f"The databases read as {before.state}: {before.detail}"
        if before.state == "imported" or (before.state == "populated" and before.complete):
            yield "They are already imported; leaving them alone."
            return
        if before.state == "unreadable":
            raise InstallerError(
                f"The databases could not be asked what state they are in ({before.detail}), so "
                "nothing was imported. Nothing can be established about them either way."
            )
        if before.state == "populated":
            raise InstallerError(
                f"These databases already hold data ({before.detail}) but are not finished. "
                "Importing over them would overwrite it, so nothing was run. Use an empty "
                "folder for a new install."
            )
        if before.state == "partial":
            yield f"Clearing the half-written databases first ({before.detail})."
            # `reset()` INSIDE a `try`. It was called bare until 2026-09-02, and
            # the seam behind it on the AzerothCore path,
            # `controller_wow_wotlk.repair.reset_unfinished()`, names three
            # things it raises: `MaintenanceError` (the schemas could not be
            # listed, or one survived its `DROP`), `ApplyError` (the server
            # refused a `DROP DATABASE`) and a bare `RuntimeError` (there is
            # player data). None of the three is an `InstallerError` -- they
            # subclass `RuntimeError` independently -- so `run()`'s `except
            # InstallerError` could not see them. Reproduced through the real
            # `install_wiring.main()` on `wow-wotlk` for each: a traceback where
            # the harness promises a sentence, and `"last_error": ""` where
            # every other stage failure records its own. Same shape, and the
            # same fix, as the `composegen.render()` blocker (`b22ab381`).
            #
            # `Exception`, not a named tuple of types: `gate` is an `ImportGate`
            # PROTOCOL, and the spine is family-neutral by construction -- it
            # cannot import `controller_wow_wotlk` to name those two classes,
            # and 7.3's CMaNGOS gate answers through entirely different
            # machinery. What a seam raises is the seam's business; that
            # everything crossing this boundary is an `InstallerError` is this
            # engine's.
            #
            # `InstallerError` is re-raised untouched and FIRST, because
            # `CallableGate.reset()` already raises one -- the refusal for an
            # engine built with no reset seam at all -- and re-wrapping it would
            # bury that sentence inside this one.
            try:
                dropped = gate.reset()
            except InstallerError:
                raise
            except Exception as exc:
                raise InstallerError(
                    "The half-written databases could not be cleared, so the import was not "
                    f"run: {exc}"
                ) from exc
            if not dropped:
                raise InstallerError(
                    "The databases read as unfinished, but nothing was found to clear, so the "
                    "import was not run. Nothing was changed."
                )
            yield f"Cleared {', '.join(dropped)}."
        if service is None:
            return
        yield f"Importing the databases ({service}). This takes several minutes."
        run = yield from self._pump(
            lambda sink: self._seams.one_shot(
                service, ctx.server_dir, sink=sink, cancel=ctx.cancel
            ),
            cancel=ctx.cancel,
        )
        if run.returncode == docker.CANCELLED_RETURNCODE:
            raise InstallerError(_cancelled_message("the database import", IMPORT_CANCEL_NOTE))
        try:
            after = self._seams.verify_import(gate.probe, service, ctx.server_dir, run)
        except docker.DockerCommandError as exc:
            raise InstallerError(str(exc)) from exc
        yield f"The databases now read as {after.state}."

    def stage_up(self, ctx: StageContext) -> Iterator[str]:
        """Start the three long-running services, and only those.

        Never recorded: a resume has to end with the server actually running,
        and this is cheap. `start_staged()` names the services explicitly so
        `up` can never select the one-shot import — the thing `dml-start.sh`
        warns about in as many words.
        """
        yield "Starting the server."
        try:
            self._seams.start(self.entry.container_spec(), ctx.server_dir)
        except docker.DockerCommandError as exc:
            raise InstallerError(f"The server would not start: {exc}") from exc

    def stage_ready(self, ctx: StageContext) -> Iterator[str]:
        """Wait until the database is healthy and both servers have said they are up.

        `wait_db_healthy_for()` polls the container's health status and reads no
        logs at all. The world half is `wait_for_ready()` below, which is where
        the interesting decision lives. What it waits FOR is catalog data
        (`install.native.ready`), filled through the same `fill()` as the
        compose templates so a typo is an error and not a silent 600-second
        timeout.
        """
        spec = self.entry.container_spec()
        yield "Waiting for the database."
        if not self._seams.wait_db_healthy(spec):
            raise InstallerError(
                f"The database never reported healthy. `docker compose logs "
                f"{spec.service_for(spec.db)}` in {ctx.server_dir} will say why."
            )
        yield from self.wait_for_ready(ctx, self._native().ready)

    def wait_for_ready(self, ctx: StageContext, markers: ReadyMarkers) -> Iterator[str]:
        """Wait for the world server, giving a server that is still TALKING more time.

        **`ready.timeout_s` is a quiet budget, not a total one.** It is how long
        the world server may print NOTHING NEW before this calls it stuck; every
        time it prints, the budget starts again. That is the whole fix, and the
        incident that forced it was measured on yulon-win11-gate 2026-09-04 from
        the world server's own timestamps (`docker logs -t`), with the server
        directory on Docker Desktop's 9p share reading at about 1.4 MB/s:

            Vanilla  06:12:43Z mangosd start -> 06:37:22Z first `Avg Diff:` = 24.6 min
            TBC      18:59:55Z mangosd start -> 19:45:58Z first `Avg Diff:` = 46.0 min

        Both entries carried `timeout_s: 1800`, so the first fitted and the
        second did not. TBC's install ended `The server started but never
        reported ready`, exit 1, while `tbc-mangosd` was up, had `restarts=0`,
        had loaded its world and went on printing its diff loop for hours. The
        install was complete and correct; only the verdict was wrong. The two
        games differ in how much world is being read over that mount and in
        nothing else, so no fixed wall-clock number can be right on both a
        native Linux disk and a 9p share — while "has it said anything in the
        last half hour" means the same thing on both.

        Structure: `wait_ready()` is called for one `timeout_s` window at a
        time, and between windows `world_output` is asked what the container
        has said. A window that ends without the banner is read against
        `_read_world()`'s questions, which holds the order and the argument for
        it — and which `wait_ready_quietly()` shares, so the six management
        waits cannot drift into a different one. This loop's own job is the
        three things a bool cannot carry: the announcement, the progress notes,
        and a separate sentence per verdict — including the one that says
        NOTHING about the server, because docker stopped answering and this
        wait's only view of the world is through it.

        Every duration in those sentences is MEASURED, off the `monotonic`
        seam, and none is assumed from the window count. `docker.wait_ready()`
        returns False early on three of its four paths, so a window handed
        thirty minutes can end in two — and until 2026-09-05 the note the user
        read said thirty minutes anyway (`(spent + 1) * quiet`), and the ceiling
        was twelve of those windows however short they were. RED on m910q that
        day: a fake whose windows ended at 120 s printed `Still loading after 30
        minutes` and gave up at 24 minutes of wall clock saying six hours.

        The ceiling (`READY_CEILING_SECONDS`) is the outer bound on a server
        that prints for ever, and it is now wall clock rather than a count of
        windows. The last window is shortened to whatever is left of it, so the
        wait spends at most the ceiling and never more — including for a
        `timeout_s` that does not divide it (2500 bought nine whole windows and
        overshot by fifteen minutes) and for one LARGER than it, which now buys
        one window of six hours instead of one of its own full length. The
        catalogue asking for longer than the ceiling does not raise the ceiling.

        Two gaps inherited from `docker.wait_ready()`, recorded in 7.1 and still
        true: its crash-loop latch and its `fatal` search both look at the WORLD
        container only, so an auth container that loops or prints a fatal line
        is not seen. Both are conservative — slower to give up, never quicker to
        call a dead server ready.
        """
        spec = self.entry.container_spec()
        ready = self._ready_spec(markers)
        service, container = spec.service_for(spec.world), spec.world
        logs = f"`docker compose logs {service}` in {ctx.server_dir}"
        quiet = markers.timeout_s
        never_ready = "The server started but never reported ready"

        # The stage's own announcement, and it is not decoration: `stage_ready()`
        # says "Waiting for the database." and then this half can legitimately
        # take three quarters of an hour. Between 2026-09-04 and 2026-09-05 there
        # was no line here at all — `grep -rn "Waiting for the world server"`
        # returned nothing — so a user watching a 46-minute first boot saw the
        # database line and then nothing until the first window ended.

        yield (
            f"Waiting for the world server. A first boot loads the whole world and can take "
            f"many minutes; this waits as long as the server keeps printing, and calls it "
            f"stuck after {_spell_seconds(quiet)} with nothing new."
        )

        started = self._seams.monotonic()
        before = self._seams.world_output(spec)
        first_restarts = before.restarts
        while True:
            window = min(float(quiet), READY_CEILING_SECONDS - (self._seams.monotonic() - started))
            if window <= 0:
                break
            window_started = self._seams.monotonic()
            if self._seams.wait_ready(spec, replace(ready, timeout=window)):
                yield "The server is up."
                return
            now = self._seams.world_output(spec)
            first_restarts = _restart_baseline(first_restarts, now)
            verdict, detail = _read_world(
                before, now, first_restarts, markers.restart_loop, ready.fatal
            )
            spent = self._seams.monotonic() - started
            if verdict == "loop":
                raise InstallerError(
                    f"{never_ready}: {container} restarted {detail} times while this waited, "
                    f"which is a crash loop and not a slow start. {logs} has what it printed "
                    f"before each one."
                )
            if verdict == "gone":
                raise InstallerError(
                    f"{never_ready}: {container} is not running any more (docker says "
                    f"{detail!r}), so nothing is going to print it. {logs} has its "
                    f"last words."
                )
            if verdict == "fatal":
                raise InstallerError(
                    f"{never_ready}. It printed a line that means it never will: "
                    f"{detail!r}. {logs} has the rest."
                )
            if verdict == "quiet":
                silent_for = self._seams.monotonic() - window_started
                raise InstallerError(
                    f"{never_ready}, and it stopped printing anything at all for the last "
                    f"{_spell_seconds(silent_for)} — a server that is still loading says so as "
                    f"it goes, so this one is stuck rather than slow. {logs} has its last words."
                )
            if verdict == "unreadable":
                blind_for = self._seams.monotonic() - window_started
                raise InstallerError(
                    f"The wait for {container} stopped because docker stopped answering: "
                    f"for the last {_spell_seconds(blind_for)} neither its state nor its log "
                    f"could be read, so nothing here knows whether the server is still "
                    f"loading, finished, or gone. The install itself got as far as starting "
                    f"the containers. Check the docker daemon is up, then {logs}."
                )
            before = now
            yield (f"Still loading after {_spell_seconds(spent)}, and still printing — waiting on.")
        raise InstallerError(
            f"{never_ready}. It was still printing after "
            f"{_spell_seconds(self._seams.monotonic() - started)}, so it is doing something "
            f"without finishing it. This wait gives a server that keeps talking another "
            f"{_spell_seconds(quiet)} every time it prints, up to a ceiling of "
            f"{_spell_seconds(READY_CEILING_SECONDS)}, which is many times the slowest first "
            f"boot this has been measured against. {logs} has what it is doing."
        )

    def _ready_spec(self, markers: ReadyMarkers) -> docker.ReadySpec:
        """`ReadyMarkers` with `{{REALM_HOST}}`/`{{WORLD_PORT}}` filled, then made a regex.

        `wait_ready()` searches the log with `re.search`, so a literal marker
        (`regex: false`, the default) is `re.escape`d after filling — otherwise
        the `.` in `127.0.0.1` is a wildcard, the very thing
        `docker.azerothcore_ready()` escapes (A5). Tortoise's alternations set
        `regex: true` and are handed over as written.

        Every pattern is COMPILED here, where `catalog.json` can still be named
        as the thing to fix. `wait_ready()` calls `re.search` inside its poll
        loop, so a `regex: true` marker with an unbalanced group would raise
        `re.error` in the middle of the last stage of an install — after the
        clone, the build and the import — and read as a crash rather than as a
        typo in a data file (A.2 review finding).
        """
        tokens = {"REALM_HOST": INSTALL_REALM_HOST, "WORLD_PORT": str(self.entry.ports.world)}

        def marker(text: str) -> str:
            if markers.regex:
                pattern = composegen.fill(text, tokens)
            else:
                # `{{REALM_HOST}}` becomes a wildcard rather than a literal, and
                # the port beside it stays exact. See `REALM_ADDRESS_PATTERN`.
                halves = text.split(REALM_HOST_TOKEN)
                pattern = REALM_ADDRESS_PATTERN.join(
                    re.escape(composegen.fill(half, tokens)) for half in halves
                )
            try:
                re.compile(pattern)
            except re.error as exc:
                raise InstallerError(
                    f"{self.entry.name}'s ready marker {text!r} is not a usable pattern "
                    f"({exc}). Fix `install.native.ready` in catalog.json; nothing was started."
                ) from exc
            return pattern

        try:
            world = marker(markers.world)
            auth = marker(markers.auth) if markers.auth is not None else None
            fatal = marker(markers.fatal) if markers.fatal is not None else None
        except composegen.ComposeGenError as exc:
            raise InstallerError(f"{self.entry.name}'s ready markers are broken: {exc}") from exc
        # The catalogue's `timeout_s` wins over `docker.ReadySpec`'s own 480s
        # default, which covers only a spec written in Python. Data beats a
        # constant wherever there is data, and this is the only place the two
        # numbers meet.
        return docker.ReadySpec(
            world=world,
            auth=auth,
            fatal=fatal,
            timeout=float(markers.timeout_s),
            restart_loop=markers.restart_loop,
        )

    # -- what the realm advertises, once everything else has finished ----

    def _advertise_realm(self, ctx: StageContext) -> Iterator[str]:
        """Set the realm's advertised address to one other machines can reach.

        The fix for bug-checklist §35, driven on real hardware 2026-09-02: a
        WoW TBC server this app installed on m910q was joined from another PC
        over Tailscale, auth succeeded, the realm list arrived, and the world
        connect could not — `realmd.realmlist.address` was still
        `127.0.0.1`, so the client had been told the world server was on its
        OWN machine and hung at "Connecting" saying nothing. One
        `UPDATE realmd.realmlist SET address='100.78.24.50' WHERE id=1` later
        the same client reached a character screen. Every piece of this existed
        (`networking.realmlist_sql()`, `CatalogEntry.realmlist`,
        `platform.detect_lan_ip()`, the Networking tab) and nothing on the
        install path ever ran any of it, so EVERY server this app installed was
        unreachable from every other machine until somebody found that tab.

        Here rather than in a family, because all four shipped games have a
        `realmlist` block and all four had the bug. NOT a stage: `STAGE_NAMES`
        is pinned by equality tests in two families, a new name would be
        written into every state file in the wild, and a step that must run on
        every resume would have to be `recorded=False` anyway — which is to say
        it would be this, with a name.

        **Nothing here can fail the install, and that is the whole design.**
        By the time this runs the server is built, imported, started and has
        reported ready; the user has been promised a working server and has
        one. Five outcomes, one line each — and the first of them is a file.

        bug-checklist §41: the last four all end in the row being rewritten or
        in a reason it was not, and none of them could tell a row that says
        `127.0.0.1` because nobody set it from one that says `127.0.0.1`
        because the owner asked for it. The row cannot answer that — it is the
        same eight characters either way — so the answer is recorded intent,
        written by `networking.apply()` when the Networking tab's `loopback`
        mode was applied, and read here BEFORE anything else this method does.
        Before, and not after: on 2026-09-06 at `30671d6e` two mutations of this
        order were run on m910q from a fresh `git clone --shared` with
        `__pycache__` purged on both sides
        (`pyplan/gates/bug41-loopback-2026-09-05/mutations-round3.txt`). Reading
        the intent after `self._detected_lan_ip()` still left the row alone, and
        reading it after the `address is None` return printed
        `REALM_ADDRESS_UNKNOWN` instead of the §41 sentence on a machine with no
        LAN address — the machine this mode exists for. Both kept
        `_statements()` and `sql_calls` empty, so the two empty lists in
        `test_spine.py::test_a_loopback_the_owner_chose_is_left_alone_and_the_line_says_why`
        hold nothing here; what holds it is that test's call-counting `lan_ip`
        seam plus
        `test_spine.py::test_the_machine_this_mode_is_for_has_no_lan_address_at_all`.

        * the owner CHOSE the loopback — nothing is detected, nothing is asked,
          nothing is sent, and the line says which file says so and how to undo
          it. A server whose loopback was never chosen has no such file and
          falls through to the four below, which is §41's other half;
        * no address — `REALM_ADDRESS_UNKNOWN`, and no SQL is attempted at all.
          A guess would be worse than the default, and a refusal would be a lie
          about what happened;
        * the row is ALREADY REACHABLE — nothing is sent. Not "already equal
          to the LAN address": equality is the wrong question, and asking it
          was a real defect. `networking.apply()` exists so a user can advertise
          a PUBLIC address for internet play, and every ordinary resume of the
          installer runs this method again. Comparing against the LAN address
          overwrote that public address with a LAN one and printed "players on
          other machines can reach this server", which was the opposite of what
          had just happened (review, 2026-09-03). The question that matters is
          whether the row can be reached from another machine at all, which is
          exactly what `networking.advertisable()` already answers;
        * the UPDATE failed — said, with what the database answered, plus where
          to fix it by hand. The install stays successful;
        * it worked — said, naming the address, because the address is what the
          user has to type into their client next.
        """
        chosen = networking.read_network_intent(ctx.server_dir)
        if chosen is not None and chosen.mode == "loopback":
            yield loopback_chosen_on_purpose(chosen)
            return
        address = self._detected_lan_ip()
        if address is None:
            yield REALM_ADDRESS_UNKNOWN
            return
        stored = self._stored_realm_row(ctx)
        # EVERY column the UPDATE would write must already be reachable. One
        # loopback among them still leaves a client that is sent to its own
        # machine, so `all()` and not `any()`.
        if stored is not None and all(networking.advertisable(value) for value in stored):
            shown = ", ".join(dict.fromkeys(stored))
            yield (
                f"The realm already advertises {shown}, which other machines can reach, so its "
                "row was left exactly as it is."
            )
            return
        failed = self._run_auth_statement(
            networking.realmlist_sql(self.entry, address, address), ctx
        )
        if failed:
            yield (
                f"The install is finished and the server is running, but the address the realm "
                f"advertises could not be set to {address} ({failed}), so it is unchanged and "
                "players on other machines may still be sent to their own computer. Nothing "
                "needs reinstalling: open this server's Networking tab, press Show plan and "
                "then Apply."
            )
            return
        yield (
            f"The realm now advertises {address}, so players on other machines can reach this "
            f"server: {address} is the address they set in their client's realmlist. The server "
            "was already running when this was set, so if a client is still sent to the old "
            "address, stop and start it again on the Server tab."
        )

    def _detected_lan_ip(self) -> str | None:
        """The seam's answer, or None — including when the seam itself blew up.

        `platform.detect_lan_ip()` catches `OSError` on its local branch, and
        its WSL branch does not: it shells out to `powershell.exe` through
        `runner.run()`, which on a machine without one raises `FileNotFoundError`.
        That is an `OSError` escaping into the last three lines of a successful
        install, and it must be a missing address rather than a traceback.
        """
        try:
            found = self._seams.lan_ip()
        except (OSError, RuntimeError) as exc:
            logger.debug(f"this machine's LAN address could not be detected: {exc}")
            return None
        return networking.advertisable(found)

    def _stored_realm_row(self, ctx: StageContext) -> tuple[str, ...] | None:
        """What the realm row's address columns say now, or None if it would not say.

        **None means UNKNOWN and never "it is already fine".** The caller writes
        on None, deliberately: the UPDATE is idempotent and cheap, while the
        other reading of an unanswerable database — skip, say nothing — is
        exactly how a server ends up advertising the loopback with a green
        install log above it. That is the failure this method is part of fixing,
        so it may not be reintroduced by its own error handling.

        A row count other than one is unknown for the same reason: no rows is a
        realm id this core does not have, more than one is not one realm's row,
        and neither is something to compare an address against.
        """
        try:
            answer = self._seams.sql_query(
                self.entry.container_spec().db,
                self._native().db.client,
                ctx.secrets.db_password,
                None,
                networking.realmlist_address_query(self.entry),
            )
        except docker.DockerCommandError as exc:
            logger.debug(f"the realmlist row could not be read: {exc}")
            return None
        rows = answer.splitlines()
        if len(rows) != 1:
            return None
        return tuple(field.strip() for field in rows[0].split("\t"))

    def _run_auth_statement(self, statement: str, ctx: StageContext) -> str:
        """Send one statement to this server's database; `""` if it worked, else why not.

        The same transport `sqlplan._run_sql()` uses and for the same reasons —
        stdin rather than `-e <sql>`, `MYSQL_PWD` in the exec environment rather
        than in an argv anyone can read — but it answers instead of raising,
        because its one caller must not turn a finished install into a failed
        one. The statement is fully qualified with the auth schema by
        `networking.realmlist_sql()`, so no schema argument is needed.

        Whatever the client said is passed back for the log line, with the
        password taken out of it (`_without()`). That is not theoretical
        caution: a client quotes the line it could not parse back at you, and an
        install log is what a user pastes into a bug report.
        """
        password = ctx.secrets.db_password
        try:
            proc = self._seams.exec_stdin(
                self.entry.container_spec().db,
                [self._native().db.client, "-u", "root"],
                io.BytesIO(statement.encode("utf-8")),
                env={"MYSQL_PWD": password},
            )
        except docker.DockerCommandError as exc:
            return _without(str(exc), password)
        if proc.returncode == 0:
            return ""
        said = (proc.stderr or "").strip().splitlines()
        return _without(
            said[-1] if said else f"the database client exited {proc.returncode}", password
        )

    # -- plumbing --------------------------------------------------------

    def _replaceable_compose(self, server_dir: Path) -> tuple[str, ...]:
        """`docker-compose.yml`, when git proves it is the one the clone wrote. See above."""
        path = server_dir / composegen.BASE_FILE
        if not path.exists() or composegen.is_ours(path):
            return ()
        if self._seams.file_unmodified(server_dir, composegen.BASE_FILE):
            return (composegen.BASE_FILE,)
        return ()

    def _clone(self, spec: git.CloneSpec) -> None:
        try:
            self._seams.clone(spec)
        except git.GitError as exc:
            raise InstallerError(f"Cloning {spec.url} failed: {exc}") from exc

    def _remote_of(self, dest: Path) -> str | None:
        """What `origin` points at in an existing checkout at `dest`; None if there is none."""
        if not (dest / ".git").is_dir():
            return None
        ask = self._seams.remote_url if self._seams.remote_url is not None else _git_remote_url
        return ask(dest)

    def _pump(
        self,
        call: Callable[[docker.OutputSink], docker.AttachedRun],
        *,
        cancel: threading.Event | None,
    ) -> Generator[str, None, docker.AttachedRun]:
        """Turn a push-style docker call into yielded lines, without buffering the run.

        `docker.run_attached()` pushes lines into a sink (the shape the repair
        button's UI needs) and this engine pulls them (the shape `run()`'s
        contract needs). A queue between a worker thread and this generator is
        the whole bridge: nothing is collected into a list first, so a
        four-hour build appears line by line rather than at the end.

        `cancel` is the SAME event `call` closed over, handed in a second time
        so that a consumer who abandons this generator can be honoured — see
        `stop_abandoned_worker()`. Required rather than defaulted, for the
        reason `_check_run()` gives about its own note: a default here is the
        shape of the mistake, because the call site that forgets it is exactly
        the one whose worker is left running.
        """
        lines: queue.Queue[str | None] = queue.Queue()
        outcome: list[docker.AttachedRun] = []
        failure: list[BaseException] = []

        def work() -> None:
            try:
                outcome.append(call(lines.put))
            except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread below
                failure.append(exc)
            finally:
                lines.put(None)

        worker = threading.Thread(target=work, daemon=True, name="yulon-install-output")
        worker.start()
        try:
            while True:
                item = lines.get()
                if item is None:
                    break
                yield item
        except BaseException:
            # Abandonment, or an exception thrown INTO this frame — never the
            # normal path, which leaves the loop by `break` once the worker has
            # put its sentinel; setting the cancel event there would mark a
            # stage that succeeded as stopped. `BaseException` and not
            # `GeneratorExit`, measured on m910q 2026-09-04: the CLI harness
            # spends an install blocked in `lines.get()` above, so its Ctrl+C
            # is raised right there as a `KeyboardInterrupt`, and the narrower
            # clause let it past with the worker still running — §21's state,
            # one exception type to the side of the test that closed it.
            stop_abandoned_worker(worker, cancel, what="the install output")
            raise
        worker.join()
        if failure:
            raise InstallerError(f"the command could not be run: {failure[0]}") from failure[0]
        return outcome[0]

    def _check_run(
        self,
        run: docker.AttachedRun,
        what: str,
        cancel: threading.Event | None,
        note: str,
    ) -> None:
        """`note` is what a Stop costs FOR THIS STAGE, and only this stage.

        Required rather than defaulted, because there is no sentence about
        keeping work that is true of the build, the download and the import
        alike — and the copy that was true of one was being said for all three.
        A default here is the shape that mistake had.
        """
        if run.returncode == docker.CANCELLED_RETURNCODE:
            raise InstallerError(_cancelled_message(what, note))
        if run.returncode != 0:
            raise InstallerError(
                f"{what} failed (exit {run.returncode}). Its last words were: "
                f"{docker.last_words(run.tail)}"
            )
        self._check_cancel(cancel)

    def _check_cancel(self, cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise InstallerError(_cancelled_message("the install"))

    @contextmanager
    def _held_awake(self) -> Iterator[str]:
        """Hold the machine awake for the stages that take hours, and say if we cannot.

        Yields the sentence to put in the log, empty when the assertion was
        actually taken.

        A failure to assert it is not a reason to refuse an install: on Windows
        the assertion is per-thread, and `platform.keep_awake()` refuses the
        declared GUI thread, because a claim made there on a worker's behalf
        holds nothing. `run()` is a generator, so this block is entered by
        whichever thread resumes it — the app's `QThread`, or the headless
        harness's own main thread — and both of those ARE the thread doing the
        install. Until 2026-09-05 the refusal went by `threading.main_thread()`
        identity instead, and the harness's run on `yulon-win11-gate` took this
        `except` for that reason (bug-checklist §43). The engine says so in its
        output instead of promising something it did not get.

        Written with an `ExitStack` so the `except` covers ONLY entering the
        context. Wrapping the `yield` too would have swallowed every
        `InstallerError` a stage raises — `InstallerError` is a `RuntimeError`
        — and turned a failed build into a sleep warning.
        """
        with ExitStack() as stack:
            note = ""
            try:
                stack.enter_context(self._seams.keep_awake())
            except RuntimeError as exc:
                logger.warning(f"not holding this machine awake: {exc}")
                note = (
                    "This machine may go to sleep during the build; leave it awake, and leave "
                    "the lid open on a laptop."
                )
            yield note

    def _record_error(self, server_dir: Path, state: InstallState, message: str) -> None:
        if not (server_dir / STATE_FILE).is_file():
            return
        write_state(server_dir, replace(state, last_error=message))


def _without(said: str, secret: str) -> str:
    """`said` with the database password taken back out of it, every occurrence.

    `sqlplan._redact()`'s rule, spelled again here rather than imported:
    `yulon.catalog.families` imports this module, so importing back out of it
    would close a cycle at import time. The guard on an empty secret is not
    decoration — `"".replace("", "***")` puts the marker between every
    character, so a `Secrets` that somehow held nothing would produce a log
    line nobody could read.
    """
    return said.replace(secret, "***") if secret else said


ABANDONED_WORKER_SECONDS = runner._SHUTDOWN_TIMEOUT_SECONDS
"""How long an abandoning consumer may be blocked while its worker stops.

A cap on the ABANDONER, not a promise about teardown. `join()` with no timeout
is what bug-checklist §21 rejects in as many words: the worker is a container
run, so an unbounded join blocks whoever dropped the generator for the hours
the extraction has left. The number is read from `runner` rather than typed
here a second time: `_SHUTDOWN_TIMEOUT_SECONDS` answers the same question one
layer down, and `test_spine.py` pins that the two agree.
"""


def stop_abandoned_worker(
    worker: threading.Thread, cancel: threading.Event | None, *, what: str
) -> None:
    """Stop a `_pump()`/`_stream()` worker whose consumer walked away (bug-checklist §21).

    Both bridges start a daemon thread and join it only after the queue drains.
    `GeneratorExit` at the `yield` skips that join, so the worker went on
    running and went on pushing into a queue nobody would ever read — for the
    extract stage, a live multi-hour extraction with no owner.

    Setting the cancel event is what actually stops it: every worker here is a
    `run_container(cancel=…)` underneath, and that is the seam it already
    polls. The join that follows is only so the abandoner does not race ahead
    of a container still being torn down, and it is bounded for the reason
    `ABANDONED_WORKER_SECONDS` gives.

    **Setting the event cannot change what the user is told.** The one thing
    that says "cancelled" is `LogPanel._stop_requested`, set by the Stop button
    and by nothing else — its own docstring says why: a cancelled source does
    not raise, so the panel is the only thing that knows. `_check_cancel()`
    does read the event, but it is downstream of the `yield` that was
    abandoned and can never run again for this install. Checked rather than
    assumed, because "set the caller's cancel event" is exactly the shape that
    quietly turns a crash into "you stopped it".

    `cancel=None` (a test that passes no event; the CLI harness did too, until
    2026-09-04) has no seam to pull, so it is logged rather than silently
    tolerated. It is NOT joined: nothing would end the worker, so the join
    could only spend the full timeout before saying the same thing.
    """
    if sys.is_finalizing():
        # A daemon thread that asks for the GIL after finalisation has begun is
        # exited on the spot, so it can never reach the cancel check and the
        # join below could only ever time out. The process is going away, which
        # is the one moment this leak costs nothing.
        return
    if cancel is None:
        logger.warning(f"{what} was abandoned with no cancel event; its worker was left running")
        return
    cancel.set()
    worker.join(timeout=ABANDONED_WORKER_SECONDS)
    if worker.is_alive():
        logger.warning(
            f"{what} was abandoned and did not stop within {ABANDONED_WORKER_SECONDS}s; "
            f"thread {worker.name} was left running"
        )


def _cancelled_message(what: str, note: str = "") -> str:
    """ "<what> was stopped", plus whatever is TRUE of the stage that was stopped.

    No note is the honest default. A cancel between stages has nothing to add
    beyond `OPENING_NOTE`, which the user was already told.
    """
    return f"{what} was stopped. {note}".rstrip()


def _listing(folder: Path, *, ignoring: Collection[str] = ()) -> list[str]:
    """What is in `folder`, minus `ignoring` — or a refusal, never a bare `OSError`.

    THE ONLY PLACE THIS ENGINE DECIDES WHETHER A FOLDER IS ITS TO WRITE INTO.
    That is narrower than what this line said until 2026-09-05 — "THE ONLY
    PLACE THIS ENGINE LISTS A DIRECTORY" — which was true of no commit that
    ever carried it. `families/clientdir.py` walks a client's `Data/` in
    `_to_depth()` and `locale_dirs()`, `families/extract.py` counts what a tool
    produced in `file_count()`, and `families/sqlplan.py` lists `Updates/` in a
    second private function of this very name. Those three READ a folder
    somebody else filled and deliberately let the `OSError` out to a caller
    with a better sentence for it than this one has — `mpq_files()` says so in
    as many words, because `rglob()` answering short would reach the user as
    "too few archives" about a folder nobody could open. Rewording was the fix
    rather than routing them through here: they need `Path`s and the raw error,
    and this function exists to hand back names and a refusal.

    The narrow claim is pinned by enumeration rather than by assertion:
    `test_every_folder_listing_in_the_package_is_accounted_for` lists every
    directory listing under `yulon/` in eight spellings — `iterdir`, `scandir`,
    `listdir`, `glob`, `iglob`, `rglob`, `walk`, `fwalk`, at module level and
    inside `async def` too — with the reason each is not a write decision, so a
    new one anywhere in the app fails that audit rather than quietly making this
    paragraph false again. It read two modules until 2026-09-05, which is how a
    sentence about the whole engine went unchecked over five sixths of it; then
    three spellings under `yulon/catalog/`, which a `glob` respelling of the
    very regression it was widened for walked straight past; then six, which
    `glob.iglob` walked past the same way. Eight is a set that can be checked,
    and `test_the_listing_audit_sees_every_spelling_it_names` checks it — not a
    claim to read every spelling Python has, which is what the set's own
    docstring said both times it was wrong.

    Not every listed site is exonerated: `apply.py`'s `_require_own_clone()`
    makes THIS decision — may the module applier write into this clone dir —
    with a bare `iterdir()` and no `except` at all, so an unreadable clone dir
    reaches the user as a `PermissionError` traceback (measured, m910q
    2026-09-05). It is recorded in that map as a defect rather than a design,
    and filed; the sentence at the top of this docstring is about the INSTALL
    engine and stays true.

    The caller that made the point was `installer.cancelled_install_message()`,
    which decided with a bare `iterdir()` whether the folder the user just
    stopped an install in has leftovers — `_claim_folder()`'s question, asked
    by the copy that tells the user what `_claim_folder()` will do. Two answers
    to one question, and they could differ on exactly the folder that matters,
    the one neither can read. It comes through here now, and both answers are
    driven on one unreadable folder in
    `test_a_folder_the_copy_cannot_list_is_refused_rather_than_called_empty`.

    Four sites asked `folder.iterdir()` bare until 2026-09-02 — `_claim_folder()`, which every
    shipped game reaches through preflight and `_guard()`;
    `stage_clone_sources()`, which the three CMaNGOS games bind; and
    AzerothCore's `_clone_core()` and `_clone_modules()` — and reproduced
    through the real `install_wiring.main()` at all four, an unreadable folder
    printed a `PermissionError` traceback where the harness's own docstring
    promises the sentence written for a person. At the three stage sites
    `run()`'s `except InstallerError` could not see it either, so
    `_record_error` never ran and the state file kept `"last_error": ""`. That
    is the same shape as the `composegen.render()` blocker (`b22ab381`), and
    the same fix: translate where the call is.

    REFUSE, never assume empty. A listing that fails says nothing about what is
    in there, and the caller's next move on "empty" is a clone whose seam
    `shutil.rmtree`s a destination it does not recognise. Treating an
    unreadable folder as empty would delete a user's files on exactly the input
    where the engine knows least — the same trade `_claim_folder()` makes for a
    state file it cannot parse.

    Not exotic, either: `iterdir()` raises for a permission change, an
    unreadable mount, a drive that went away, and a stale UNC path into a WSL
    distro — the app reaches folders that way often enough that
    `Identification.UNVERIFIED` exists in the UI for it.

    `ignoring` is a single name that does not count as content, which is
    `STATE_FILE` at three of the four sites; `_clone_modules()` passes nothing,
    because a module directory holding this app's state file would be a
    leftover worth refusing over.

    Raises:
        InstallerError: the folder could not be listed. The sentence names the
            folder, carries what the OS said, and is distinct from every
            "this folder has files in it" refusal above it.
    """
    if isinstance(ignoring, str):
        # A `str` IS a `Collection[str]`, so mypy accepts one here and the
        # membership test below then filters by CHARACTER: `ignoring=STATE_FILE`
        # would drop every one-character name in the folder and keep
        # `.yulon-install.json` itself. The five callers all pass
        # `OUR_OWN_FILES` now; this is what makes a sixth that passes a bare
        # name fail loudly instead of quietly answering about the wrong folder.
        raise TypeError(f"_listing(ignoring=) takes a collection of names, not {ignoring!r}")
    try:
        return [item.name for item in folder.iterdir() if item.name not in ignoring]
    except OSError as exc:
        raise InstallerError(
            f"{folder} could not be listed ({exc}), so this app cannot tell whether it is empty "
            "or holds somebody else's files, and it will not write into a folder it cannot "
            "read. Nothing was written. If it is on a network drive, an external disk or "
            "another machine, check that it is still reachable and try again."
        ) from exc


def _git_remote_url(dest: Path) -> str | None:
    """`git remote get-url origin`, run inside a container so no host git is needed.

    The default for the `remote_url` seam. It is a `git` question, and this
    engine's whole point on macOS and Windows is that the machine may not have
    one — so it goes through the same containerized git the clones use.
    """
    return git.ContainerGit().remote_url(dest)
